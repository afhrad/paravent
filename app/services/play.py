"""Playing a character: everything the sheet's buttons do.

Each action reads the derived :class:`~app.services.sheet.Sheet` (for maximum HP, slot
totals, bonuses) and changes only **play state** in the character file — never a choice.
All changes go through :meth:`CharacterStore.mutate`, so they are validated, written
atomically and announced to every open screen.

Rules that are applied, because the sheet would be wrong without them:

* Damage takes temporary HP first; temporary HP never stacks, the higher value wins.
* Damage while concentrating rolls the Constitution save (DC 10 or half the damage)
  and ends concentration on a failure. The roll is logged either way.
* Damage at 0 HP is a failed death save; healing from 0 resets the death saves.
* A short rest restores short-rest resources and pact slots; a long rest restores HP,
  slots, every resource, half the hit dice (at least one) and one exhaustion level.
* Casting a spell spends a slot of the chosen level, falling back to a pact slot, and
  casting a concentration spell replaces any concentration already held.

Every roll and every change of hit points or conditions is also collected for the log
(documentation/PROTOCOL.md) and appended once the character is stored — with the dice,
the advantage and why, and each bonus by its source.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from app.models.pc import ChosenOption, LogKind, PlayerCharacter, Recharge
from app.models.protocol import EntryKind, ProtocolEntry, RollRecord, Scope, Visibility
from app.services.condition_rolls import bend
from app.services.dice import Advantage, Dice, DiceError, Roll, is_expression
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService, outcome_of, roll_record
from app.services.sheet import CONDITIONS, Sheet, SpellView, Value, damage_at
from app.store.characters import CharacterStore

# The actions whose log entries the sheet's "private" switch keeps between the roller
# and the gamemaster (PROTOCOL.md §4): rolls, never the hit points or conditions.
PRIVATE_ACTIONS = frozenset({"roll", "death_save", "hit_die"})


class PlayError(Translatable):
    """An action the rules do not allow right now: no slot left, no hit die to spend."""


@dataclass(frozen=True)
class Actor:
    """Who pressed the button, as the log names them.

    ``via_combat`` is set when the tracker acts on the character: the tracker logs the
    damage, healing and conditions it applies itself, so the sheet does not again;
    ``combat`` and ``round`` put what the sheet does log (a concentration save the
    damage called for, a player's own save) in that fight.
    """

    name: str
    username: str = ""
    via_combat: bool = False
    combat: str | None = None
    round: int | None = None
    # The fight's world: its log is where what happens in the fight is kept.
    world: str | None = None


def _int(params: Mapping[str, str], key: str, default: int | None = None) -> int:
    raw = params.get(key)
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        if default is None:
            raise PlayError("play.error.bad_input") from None
        return default


class PlayService:
    """Applies one named action to a character."""

    def __init__(
        self,
        store: CharacterStore,
        dice: Dice | None = None,
        protocol: ProtocolService | None = None,
        fight_of: Callable[[str], tuple[str, int | None, str | None] | None] | None = None,
    ) -> None:
        """Bind to the character store, a dice source (injectable for tests), the log,
        and ``fight_of(character_id)``: the running fight a character is in (id, round,
        world)."""
        self._store = store
        self._dice = dice or Dice()
        self._protocol = protocol
        self._fight_of = fight_of
        # The log entries of the action this thread is applying (PROTOCOL.md §9 step 1).
        self._staged = threading.local()
        self._actions: dict[
            str, Callable[[PlayerCharacter, Sheet, Mapping[str, str], Actor], None]
        ] = {
            "hp": self._hp_action,
            "slot": self._slot,
            "cast": self._cast,
            "concentration": self._concentration,
            "condition": self._condition_action,
            "exhaustion": self._exhaustion,
            "resource": self._resource,
            "inspiration": self._inspiration,
            "rest": self._rest,
            "hit_die": self._hit_die,
            "death_save": self._death_save,
            "roll": self._roll,
            "equip": self._equip,
            "clear_log": self._clear_log,
            "saving_throw": self._saving_throw,
            "resistance": self._resistance,
            "drink": self._drink,
            "rage": self._rage,
            "mastery": self._mastery,
        }

    @property
    def actions(self) -> frozenset[str]:
        """Every action name the sheet may post."""
        return frozenset(self._actions)

    def act(
        self, character_id: str, action: str, params: Mapping[str, str], sheet: Sheet, actor: Actor
    ) -> PlayerCharacter:
        """Apply ``action``; raise :class:`PlayError` when the rules refuse it."""
        handler = self._actions.get(action)
        if handler is None:
            raise PlayError("play.error.unknown_action", action=action)

        staged: list[ProtocolEntry] = []

        def apply(pc: PlayerCharacter) -> None:
            staged.clear()  # a retried mutation starts its entries afresh
            handler(pc, sheet, params, actor)
            self._record_hit_points(pc, sheet)

        # mutate() hands the handler a copy and writes only if it returns normally, so
        # a refused action changes nothing — and logs nothing.
        outer = getattr(self._staged, "items", None)
        self._staged.items = staged
        try:
            changed = self._store.mutate(character_id, apply)
        finally:
            self._staged.items = outer
        if self._protocol is not None and staged:
            if action in PRIVATE_ACTIONS and params.get("private") == "1":
                # Rolled privately: only the roller and the gamemaster read it (§4).
                staged = [e.model_copy(update={"visibility": Visibility.PRIVATE}) for e in staged]
            self._protocol.append(staged)
        return changed

    @staticmethod
    def _record_hit_points(pc: PlayerCharacter, sheet: Sheet) -> None:
        """Write the maximum, and "full" as a number, so the file reads "41 of 62".

        The maximum is derived (``hit_points``, CON, overrides); recording it here is
        for whoever opens the file, not an input the sheet relies on.
        """
        top = sheet.hp_max.value
        if top is None:
            return
        pc.state.hp_max = top
        if pc.state.hp is None:
            pc.state.hp = top

    # -- logging -----------------------------------------------------------------

    def _log(
        self,
        pc: PlayerCharacter,
        actor: Actor,
        kind: LogKind,
        subject: str = "",
        roll: Roll | None = None,
        *,
        outcome: str = "",
        record: RollRecord | None = None,
        what: str = "",
        against: int | None = None,
        amount: int | None = None,
    ) -> None:
        """Collect what happened for the log: the kind, what exactly, the dice."""
        self._stage(
            pc,
            actor,
            EntryKind(kind.value),
            what=what or (f"{kind.value}:{subject}" if subject else kind.value),
            roll=record or (roll_record(roll, rest="modifier") if roll else None),
            outcome=outcome or outcome_of(roll),
            against=against,
            amount=amount,
        )

    def _stage(self, pc: PlayerCharacter, actor: Actor, kind: EntryKind, **facts: Any) -> None:
        """Collect a log entry for the action being applied; appended once it is stored.
        A roll made while the character fights belongs to that fight and its round."""
        staged = getattr(self._staged, "items", None)
        if staged is None:
            return
        combat, round_, world = actor.combat, actor.round, actor.world
        if combat is None and self._fight_of is not None:
            combat, round_, world = self._fight_of(pc.id) or (None, None, None)
        staged.append(
            ProtocolEntry(
                actor=actor.username,
                actor_name=actor.name,
                subject=f"character:{pc.id}",
                subject_name=pc.name,
                kind=kind,
                # In a fight, in the fight's world (its log, slice and recap hold it).
                scope=Scope(
                    world=world if combat is not None else pc.world_id,
                    combat=combat,
                    round=round_,
                ),
                origin="sheet",
                **facts,
            )
        )

    # -- hit points --------------------------------------------------------------

    def _hp_action(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """The sheet's damage, healing and temporary HP, logged as what was asked — before
        the concentration save the damage may call for, so the log reads in order. A
        refused change logs nothing, whatever was collected."""
        amount = abs(_int(params, "amount", 0))
        kind = {
            "damage": EntryKind.DAMAGE,
            "heal": EntryKind.HEAL,
            "temp": EntryKind.TEMP_HP,
        }.get(params.get("mode", ""))
        if amount and kind is not None and not actor.via_combat:
            self._stage(pc, actor, kind, amount=amount)
        self._hp(pc, sheet, params, actor)

    def _hp(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        mode = params.get("mode", "")
        amount = abs(_int(params, "amount"))
        if not amount:
            return
        state = pc.state
        top = sheet.hp_max.value
        current = sheet.hp_current
        if mode == "temp":
            state.temp_hp = max(state.temp_hp, amount)
            return
        if mode == "heal":
            if current <= 0:
                state.death_saves.successes = state.death_saves.failures = 0
            healed = current + amount if top is None else min(top, current + amount)
            state.hp = None if top is not None and healed >= top else healed
            return
        if mode != "damage":
            raise PlayError("play.error.bad_input")
        absorbed = min(state.temp_hp, amount)
        state.temp_hp -= absorbed
        rest = amount - absorbed
        if current <= 0 and rest > 0:
            state.death_saves.failures = min(3, state.death_saves.failures + 1)
        state.hp = max(0, current - rest)
        if rest > 0 and state.concentration is not None:
            self._concentration_check(pc, sheet, rest, actor)

    def _concentration_check(
        self, pc: PlayerCharacter, sheet: Sheet, damage: int, actor: Actor
    ) -> None:
        dc = max(10, damage // 2)
        bonus = sheet.ability("con").save.value or 0
        roll = self._dice.d20(bonus)
        held = pc.state.concentration
        kept = roll.total >= dc
        self._log(
            pc,
            actor,
            LogKind.CONCENTRATION,
            held.name if held else "",
            roll,
            outcome="success" if kept else "failure",
            record=roll_record(roll, sources=sheet.ability("con").save.sources),
            against=dc,
        )
        if not kept:
            pc.state.concentration = None

    # -- spell slots and casting -------------------------------------------------

    def _slot(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        op = params.get("op", "spend")
        if params.get("level") == "pact":
            if sheet.pact is None:
                raise PlayError("play.error.no_slot")
            used = pc.state.pact_slots_used
            pc.state.pact_slots_used = (
                min(sheet.pact.total, used + 1) if op == "spend" else max(0, used - 1)
            )
            return
        level = _int(params, "level")
        slot = sheet.slot(level)
        if slot is None:
            raise PlayError("play.error.no_slot")
        used = pc.state.slots_used.get(str(level), 0)
        if op == "spend":
            if used >= slot.total:
                raise PlayError("play.error.no_slot")
            used += 1
        else:
            used = max(0, used - 1)
        pc.state.slots_used = {**pc.state.slots_used, str(level): used}

    @staticmethod
    def _cast_free(pc: PlayerCharacter, sheet: Sheet, spell: SpellView) -> None:
        """Cast a granted spell without a slot: at will, or spending its use per rest
        ("Bless (Magic Initiate)")."""
        if spell.free == "will":
            return
        resource = next((r for r in sheet.resources if spell.free and r.name == spell.free), None)
        if resource is None:
            raise PlayError("play.error.bad_input")
        used = pc.state.resources_used.get(resource.name, 0)
        if used >= resource.max:
            raise PlayError("play.error.resource_spent", name=resource.name)
        pc.state.resources_used = {**pc.state.resources_used, resource.name: used + 1}

    def _cast(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        spell = sheet.spell(params.get("spell", ""))
        if spell is None:
            raise PlayError("play.error.unknown_spell")
        if params.get("level") == "free":
            self._cast_free(pc, sheet, spell)
            level = spell.level
        elif spell.level > 0:
            level = max(_int(params, "level", spell.level), spell.level)
            slot = sheet.slot(level)
            used = pc.state.slots_used.get(str(level), 0)
            if slot is not None and used < slot.total:
                pc.state.slots_used = {**pc.state.slots_used, str(level): used + 1}
            elif (
                sheet.pact is not None
                and sheet.pact.level >= spell.level
                and pc.state.pact_slots_used < sheet.pact.total
            ):
                pc.state.pact_slots_used += 1
                level = sheet.pact.level
            else:
                raise PlayError("play.error.no_slot_for", level=level)
        else:
            level = 0
        if spell.concentration:
            pc.state.concentration = ChosenOption.model_validate(spell.reference)
        self._log(
            pc,
            actor,
            LogKind.CAST,
            spell.name,
            what=f"cast:{spell.name}:{level}" if spell.level else f"cast:{spell.name}",
        )

    def _concentration(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        pc.state.concentration = None

    # -- conditions and resources ------------------------------------------------

    def _condition_action(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """A condition switched on the sheet, logged as on or off."""
        key = params.get("key", "").lower()
        was = key in pc.state.conditions
        self._condition(pc, sheet, params, actor)
        if not actor.via_combat:
            self._stage(pc, actor, EntryKind.CONDITION, what=f"{'off' if was else 'on'}:{key}")

    def _condition(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        key = params.get("key", "").lower()
        if key not in CONDITIONS:
            raise PlayError("play.error.bad_input")
        active = list(pc.state.conditions)
        pc.state.conditions = [c for c in active if c != key] if key in active else [*active, key]

    def _exhaustion(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        pc.state.exhaustion = max(0, min(6, pc.state.exhaustion + _int(params, "delta")))

    def _resource(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        name = params.get("name", "")
        resource = next((r for r in sheet.resources if r.name == name), None)
        if resource is None:
            raise PlayError("play.error.bad_input")
        used = pc.state.resources_used.get(name, 0)
        if params.get("op", "use") == "use":
            if used >= resource.max:
                raise PlayError("play.error.resource_spent", name=name)
            used += 1
        else:
            used = max(0, used - 1)
        pc.state.resources_used = {**pc.state.resources_used, name: used}

    def _inspiration(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        pc.state.inspiration = not pc.state.inspiration

    # -- rests, hit dice, death saves ---------------------------------------------

    def _rest(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        kind = params.get("kind", "short")
        state = pc.state
        if kind not in ("short", "long"):
            raise PlayError("play.error.bad_input")
        restored = {Recharge.SHORT} if kind == "short" else {Recharge.SHORT, Recharge.LONG}
        recharge = {r.name: r.recharge for r in sheet.resources}
        state.resources_used = {
            name: used
            for name, used in state.resources_used.items()
            if used and recharge.get(name, Recharge.LONG) not in restored
        }
        state.pact_slots_used = 0
        state.raging = False  # a rest ends a Rage
        if kind == "long":
            state.hp = None
            state.temp_hp = 0
            state.slots_used = {}
            state.death_saves.successes = state.death_saves.failures = 0
            state.exhaustion = max(0, state.exhaustion - 1)
            state.mastery_set = False  # Weapon Master may choose its kind again
            total = sum(pool.total for pool in sheet.hit_dice)
            regain = max(1, total // 2)
            used = dict(state.hit_dice_used)
            for pool in sheet.hit_dice:  # largest dice back first
                key = f"d{pool.faces}"
                back = min(regain, used.get(key, 0))
                used[key] = used.get(key, 0) - back
                regain -= back
            state.hit_dice_used = {k: v for k, v in used.items() if v > 0}
        self._log(pc, actor, LogKind.REST, kind)

    def _hit_die(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        faces = _int(params, "faces")
        pool = next((p for p in sheet.hit_dice if p.faces == faces), None)
        if pool is None or pool.available <= 0:
            raise PlayError("play.error.no_hit_die")
        con = sheet.ability("con").modifier
        roll = self._dice.roll(f"1d{faces}{con:+d}" if con else f"1d{faces}")
        healed = max(0, roll.total)
        key = f"d{faces}"
        pc.state.hit_dice_used = {
            **pc.state.hit_dice_used,
            key: pc.state.hit_dice_used.get(key, 0) + 1,
        }
        top = sheet.hp_max.value
        new = sheet.hp_current + healed
        pc.state.hp = None if top is not None and new >= top else new
        self._log(pc, actor, LogKind.HIT_DIE, key, roll, amount=healed)

    def _death_save(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        if sheet.hp_current > 0:
            raise PlayError("play.error.not_dying")
        roll = self._dice.d20(0)
        saves = pc.state.death_saves
        natural = roll.natural or 0
        if natural == 20:
            saves.successes = saves.failures = 0
            pc.state.hp = 1
            outcome = "crit"
        elif natural == 1:
            saves.failures = min(3, saves.failures + 2)
            outcome = "fumble"
        elif natural >= 10:
            saves.successes = min(3, saves.successes + 1)
            outcome = "success"
        else:
            saves.failures = min(3, saves.failures + 1)
            outcome = "failure"
        self._log(
            pc,
            actor,
            LogKind.DEATH_SAVE,
            "",
            roll,
            outcome=outcome,
            record=roll_record(roll),
        )

    # -- rolls ---------------------------------------------------------------------

    def _roll(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        kind = params.get("kind", "")
        subject = params.get("subject", "")
        chosen = Advantage.parse(params.get("adv"))
        # The character's own conditions bend the roll (Poisoned: disadvantage on attacks
        # and checks); the reasons go into the log line as "[poisoned]".
        roll_kind = {"check": "check", "skill": "check", "initiative": "check", "attack": "attack"}
        conditions = list(pc.state.conditions)
        if sheet.load is not None and sheet.load.burden == 2 and _physical(sheet, kind, subject):
            conditions.append("encumbered")  # variant encumbrance, heavily
        bent = bend(
            chosen,
            conditions,
            f"save:{subject}" if kind == "save" else roll_kind.get(kind, ""),
            pc.state.exhaustion,
            sheet.edition,
        )
        marked = f" [{', '.join(bent.reasons)}]" if bent.reasons else ""

        made: list[RollRecord] = []

        def d20(bonus: int, sources: tuple[tuple[str, int], ...] = ()) -> Roll:
            rolled = self._dice.d20(bonus + bent.penalty, bent.advantage)
            made.append(
                roll_record(
                    rolled,
                    sources=(*sources, ("exhaustion", bent.penalty)),
                    advantage=bent.advantage,
                    chosen=chosen,
                    reasons=bent.reasons,
                )
            )
            return replace(rolled, detail=rolled.detail + marked)

        def test(value: Value) -> Roll:
            return d20(value.value or 0, value.sources)

        if kind in ("check", "save"):
            ability = next((a for a in sheet.abilities if a.key == subject), None)
            if ability is None:
                raise PlayError("play.error.bad_input")
            if kind == "check":
                rolled = d20(ability.modifier, ((f"ability:{subject}", ability.modifier),))
            else:
                rolled = test(ability.save)
            self._log(pc, actor, LogKind(kind), subject, rolled, record=made[-1])
        elif kind == "skill":
            skill = next((s for s in sheet.skills if s.key == subject), None)
            if skill is None:
                raise PlayError("play.error.bad_input")
            rolled = test(skill.value)
            self._log(
                pc,
                actor,
                LogKind.SKILL,
                skill.name,
                rolled,
                record=made[-1],
                what=f"skill:{skill.key}",
            )
        elif kind == "initiative":
            rolled = test(sheet.initiative)
            self._log(pc, actor, LogKind.INITIATIVE, "", rolled, record=made[-1])
        elif kind == "attack":
            rolled = test(self._attack_value(sheet, subject))
            self._log(
                pc,
                actor,
                LogKind.ATTACK,
                self._label(sheet, subject),
                rolled,
                record=made[-1],
            )
        elif kind == "damage":
            expression = self._damage_expression(
                sheet, subject, params.get("versatile") == "1", _int(params, "level", 0)
            )
            self._log(
                pc, actor, LogKind.DAMAGE, self._label(sheet, subject), self._dice.roll(expression)
            )
        elif kind == "custom":
            expression = params.get("expr", "").replace(" ", "")
            if not is_expression(expression):
                raise PlayError("play.error.bad_dice", expression=expression)
            try:
                self._log(pc, actor, LogKind.CUSTOM, expression, self._dice.roll(expression))
            except DiceError as exc:
                raise PlayError("play.error.bad_dice", expression=expression) from exc
        else:
            raise PlayError("play.error.bad_input")

    @staticmethod
    def _label(sheet: Sheet, subject: str) -> str:
        """What the log names: a spell by its name ("Fire Bolt"), not its reference."""
        spell = sheet.spell(subject)
        return spell.name if spell is not None else subject

    @staticmethod
    def _attack_value(sheet: Sheet, subject: str) -> Value:
        """A weapon's attack bonus, or the spell attack for an attack spell."""
        attack = next((a for a in sheet.attacks if a.name == subject), None)
        if attack is not None:
            return attack.bonus
        spell = sheet.spell(subject)
        bonus = sheet.attack_for(spell) if spell is not None and spell.attack else None
        if bonus is not None:
            return bonus
        raise PlayError("play.error.bad_input")

    @staticmethod
    def _damage_expression(sheet: Sheet, subject: str, versatile: bool, level: int = 0) -> str:
        attack = next((a for a in sheet.attacks if a.name == subject), None)
        found: str | None
        if attack is not None:
            found = attack.versatile if versatile and attack.versatile else attack.damage
        else:
            spell = sheet.spell(subject)
            # Cast with a higher slot, a spell's damage grows (Fireball: +1d6 a level).
            found = damage_at(spell, level) if spell is not None else None
        expression = (found or "").replace(" ", "")
        if not is_expression(expression):
            raise PlayError("play.error.bad_input")
        return expression

    # -- inventory and log -------------------------------------------------------

    def _equip(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        index = _int(params, "index")
        if not 0 <= index < len(pc.inventory):
            raise PlayError("play.error.bad_input")
        pc.inventory[index].equipped = not pc.inventory[index].equipped

    def _saving_throw(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Record the save a Resilient feat made proficient, when the file lacks it."""
        ability = params.get("ability", "")
        known = next((a for a in sheet.abilities if a.key == ability), None)
        if sheet.open_saves < 1 or known is None or known.save_proficient:
            raise PlayError("play.error.bad_input")
        pc.saving_throws = [*pc.saving_throws, ability]

    def _drink(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Drink a healing potion: its dice heal the character, one fewer is carried."""
        index = _int(params, "index", -1)
        item = next((i for i in sheet.inventory if i.index == index), None)
        if item is None or not item.heal or not 0 <= index < len(pc.inventory):
            raise PlayError("play.error.bad_input")
        rolled = self._dice.roll(item.heal)
        self._hp(pc, sheet, {"mode": "heal", "amount": str(rolled.total)}, actor)
        carried = pc.inventory[index]
        if carried.quantity > 1:
            carried.quantity -= 1
        else:
            pc.inventory = [i for n, i in enumerate(pc.inventory) if n != index]
        self._log(pc, actor, LogKind.HEAL, item.name, rolled, amount=rolled.total)

    def roll(self, expression: str) -> Roll:
        """A dice roll with this service's dice (a healing spell cast from another file)."""
        return self._dice.roll(expression)

    def _resistance(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Record a resistance the species or a feat lets the character choose."""
        damage = params.get("damage", "").lower()
        if not any(damage in options for _origin, options in sheet.open_resistances):
            raise PlayError("play.error.bad_input")
        pc.resistances = [*pc.resistances, damage]

    def _mastery(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Weapon Master (2024): the kind of weapon whose mastery to use, until a long
        rest lets the character choose again."""
        wanted = params.get("kind", "")
        kind = next((k for k in sheet.mastery_choices if k.lower() == wanted.lower()), None)
        if not sheet.weapon_master or kind is None or not sheet.mastery_open:
            raise PlayError("play.error.bad_input")
        pc.state.mastery_kind = kind
        pc.state.mastery_set = True

    def _rage(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Start or end a Rage; only a character with the feature starts one."""
        if not pc.state.raging and not sheet.has_feature("Rage"):
            raise PlayError("play.error.bad_input")
        pc.state.raging = not pc.state.raging

    def _clear_log(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Start the character's log afresh: views of it begin after a ``clear`` entry;
        nothing is deleted from the log itself (PROTOCOL.md §3)."""
        self._stage(pc, actor, EntryKind.CLEAR)


PHYSICAL = frozenset({"str", "dex", "con"})


def _physical(sheet: Sheet, kind: str, subject: str) -> bool:
    """True for a roll of Strength, Dexterity or Constitution: those saves and checks,
    skills of them, initiative, and weapon attacks (which use STR or DEX)."""
    if kind in ("save", "check"):
        return subject in PHYSICAL
    if kind == "skill":
        return any(s.key == subject and s.ability in PHYSICAL for s in sheet.skills)
    if kind == "attack":
        return any(a.name == subject for a in sheet.attacks)
    return kind == "initiative"


__all__ = ["Actor", "PlayError", "PlayService"]
