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
* Wild Shape spends a use; a 2024 form gives temporary HP, a 2014 form has hit points of
  its own that take damage first, the rest carrying over when it drops to 0. A druid at
  0 HP, or after a long rest, is back in their own shape.
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

from app.models.gamestate import SpellForm
from app.models.pc import (
    ChosenOption,
    InventoryItem,
    LogKind,
    PlayerCharacter,
    PlayState,
    Recharge,
)
from app.models.protocol import EntryKind, ProtocolEntry, RollRecord, Scope, Visibility
from app.services import coins, polymorph, wild_shape
from app.services.clock import OIL, OIL_LIGHTS, light_minutes
from app.services.condition_rolls import bend
from app.services.dice import Advantage, Dice, DiceError, Roll, is_expression
from app.services.i18n import Translatable
from app.services.lifestyle import LIFESTYLES
from app.services.protocol import ProtocolService, outcome_of, roll_record
from app.services.sheet import CONDITIONS, Sheet, SpellView, Value, damage_at
from app.store.characters import CharacterStore

# The actions whose log entries the sheet's "private" switch keeps between the roller
# and the gamemaster (PROTOCOL.md §4): rolls, never the hit points or conditions.
PRIVATE_ACTIONS = frozenset({"roll", "death_save", "hit_die"})
# Actions the server takes for a character and the sheet may never post: their prices
# and goods are worked out by the shop, downtime and treasure, not by the form.
INTERNAL_ACTIONS = frozenset(
    {"receive", "buy", "sell", "spend", "learn", "eat", "inscribe", "scroll"}
)
# A day's food on the road, by item name.
RATIONS = frozenset({"rations", "rations (1 day)"})
# The conditions under which a creature cannot keep concentrating (each one is, or
# includes, Incapacitated); dropping to 0 HP leaves it Unconscious too.
# How long a character's own notes may grow.
NOTES_LIMIT = 20_000
INCAPACITATING = frozenset({"incapacitated", "paralyzed", "petrified", "stunned", "unconscious"})


class PlayError(Translatable):
    """An action the rules do not allow right now: no slot left, no hit die to spend."""


def _fit_privation(state: PlayState) -> None:
    """Hunger's and thirst's levels never more than the exhaustion there is."""
    state.hunger_levels = min(state.hunger_levels, state.exhaustion)
    state.thirst_levels = min(state.thirst_levels, state.exhaustion - state.hunger_levels)


def _take_one(pc: PlayerCharacter, index: int) -> None:
    """One fewer of the inventory's ``index``; the line goes at none."""
    carried = pc.inventory[index]
    if carried.quantity > 1:
        carried.quantity -= 1
    else:
        pc.inventory = [i for n, i in enumerate(pc.inventory) if n != index]


def _castable(sheet: Sheet, spell: SpellView) -> None:
    """Refuse a spell the character cannot cast now: a druid in Wild Shape casts none,
    but for a circle's spells and Beast Spells (``WildShapeView.can_cast``)."""
    if not sheet.can_cast(spell):
        raise PlayError("play.error.wild_shape_no_spells", name=spell.name)


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
        # Told when a character's concentration ends (caster, spell, actor), so what it
        # held ends with it: Polymorph's shapes, a summoned creature (CharacterService).
        self.on_released: Callable[[str, str, Actor], None] | None = None
        # Told when a shape ends by itself (0 HP, its temporary HP gone): the spell is
        # over, so its caster's concentration ends (caster, spell, actor).
        self.on_shape_ended: Callable[[str, str, Actor], None] | None = None
        self._ends = threading.local()
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
            "wild_shape": self._wild_shape,
            "wild_forms": self._wild_forms,
            "polymorph": self._polymorph,
            "receive": self._receive,
            "inscribe": self._inscribe,
            "scroll": self._scroll,
            "buy": self._buy,
            "sell": self._sell,
            "spend": self._spend,
            "learn": self._learn,
            "eat": self._eat,
            "fed": self._fed,
            "lifestyle": self._lifestyle,
            "use": self._use,
            "light": self._light,
            "notes": self._notes,
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
        released: list[str] = []
        ended: list[tuple[str, str]] = []

        def apply(pc: PlayerCharacter) -> None:
            staged.clear()  # a retried mutation starts its entries afresh
            released.clear()
            ended.clear()
            held = pc.state.concentration
            handler(pc, sheet, params, actor)
            self._record_hit_points(pc, sheet)
            self._lose_concentration(pc, actor)
            now = pc.state.concentration
            if held is not None and now != held:
                released.append(held.reference)

        # mutate() hands the handler a copy and writes only if it returns normally, so
        # a refused action changes nothing — and logs nothing.
        outer = getattr(self._staged, "items", None)
        outer_ends = getattr(self._ends, "items", None)
        self._staged.items = staged
        self._ends.items = ended
        try:
            changed = self._store.mutate(character_id, apply)
        finally:
            self._staged.items = outer
            self._ends.items = outer_ends
        if self._protocol is not None and staged:
            if action in PRIVATE_ACTIONS and params.get("private") == "1":
                # Rolled privately: only the roller and the gamemaster read it (§4).
                staged = [e.model_copy(update={"visibility": Visibility.PRIVATE}) for e in staged]
            self._protocol.append(staged)
        if released and self.on_released is not None:
            self.on_released(f"character:{character_id}", released[0], actor)
        for caster, spell in dict.fromkeys(ended):
            if self.on_shape_ended is not None:
                self.on_shape_ended(caster, spell, actor)
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
        shape = state.polymorph
        if mode == "heal" and shape is not None and shape.hp is not None:
            top_shape = sheet.spell_form.hp_max if sheet.spell_form is not None else shape.hp
            shape.hp = min(top_shape, shape.hp + amount)
            return
        if mode == "heal" and state.wild_shape_hp is not None and sheet.wild_shape:
            form = sheet.wild_shape.form
            top_form = form.hp if form is not None else state.wild_shape_hp
            state.wild_shape_hp = min(top_form, state.wild_shape_hp + amount)
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
        if shape is not None and shape.hp is None and absorbed and not state.temp_hp:
            self._end_polymorph(
                pc, actor, natural=True
            )  # 2024: the shape lasts while its temporary HP do
        elif shape is not None and shape.hp is not None and rest > 0:
            # A 2014 shape takes the damage; what it cannot take carries over.
            taken = min(shape.hp, rest)
            shape.hp -= taken
            rest -= taken
            if shape.hp > 0:
                if state.concentration is not None:
                    self._concentration_check(pc, sheet, taken, actor)
                return
            self._end_polymorph(pc, actor, natural=True)
        if state.wild_shape_hp is not None and rest > 0:
            # A 2014 form takes the damage; what it cannot take carries over (PHB).
            taken = min(state.wild_shape_hp, rest)
            state.wild_shape_hp -= taken
            rest -= taken
            if state.wild_shape_hp > 0:
                if state.concentration is not None:
                    self._concentration_check(pc, sheet, taken, actor)
                return
            self._leave_form(pc, actor)
        if current <= 0 and rest > 0:
            state.death_saves.failures = min(3, state.death_saves.failures + 1)
        state.hp = max(0, current - rest)
        if state.hp == 0 and state.wild_shape is not None:
            self._leave_form(pc, actor)
        if state.hp == 0 and state.polymorph is not None:
            self._end_polymorph(pc, actor, natural=True)
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
        _castable(sheet, spell)
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

    def _lose_concentration(self, pc: PlayerCharacter, actor: Actor) -> None:
        """At 0 HP (unconscious) or incapacitated, a character cannot keep concentrating:
        the spell ends, logged, whatever action put it there."""
        held = pc.state.concentration
        if held is None:
            return
        if pc.state.hp == 0 or INCAPACITATING & set(pc.state.conditions):
            pc.state.concentration = None
            self._stage(pc, actor, EntryKind.CONCENTRATION, what=f"lost:{held.reference}")

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
        state = pc.state
        delta = _int(params, "delta")
        state.exhaustion = max(0, min(6, state.exhaustion + delta))
        cause = params.get("cause", "")
        if delta > 0 and cause == "hunger":
            state.hunger_levels += delta
        elif delta > 0 and cause == "thirst":
            state.thirst_levels += delta
        _fit_privation(state)

    def _lifestyle(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """How the character lives from now on (``level``; "" for none kept)."""
        level = params.get("level", "")
        if level and level not in LIFESTYLES:
            raise PlayError("play.error.bad_input")
        pc.lifestyle = level  # type: ignore[assignment]

    def _fed(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """The character has eaten (``what=hunger``), drunk its fill (``thirst``) or
        both: the exhaustion they brought may now go with rests."""
        what = params.get("what", "both")
        if what not in ("hunger", "thirst", "both"):
            raise PlayError("play.error.bad_input")
        if what in ("hunger", "both"):
            pc.state.hunger_levels = 0
        if what in ("thirst", "both"):
            pc.state.thirst_levels = 0

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
        regain = {r.name: r.short_regain for r in sheet.resources} if kind == "short" else {}
        state.resources_used = {
            name: used - regain.get(name, 0)
            for name, used in state.resources_used.items()
            if used - regain.get(name, 0) > 0 and recharge.get(name, Recharge.LONG) not in restored
        }
        state.pact_slots_used = 0
        state.raging = False  # a rest ends a Rage
        if kind == "long":
            state.hp = None
            state.temp_hp = 0
            state.slots_used = {}
            state.death_saves.successes = state.death_saves.failures = 0
            # Exhaustion from hunger or thirst stays until the character is fed;
            # so does what a disease holds (app/services/afflictions.py).
            held = state.hunger_levels + state.thirst_levels + state.held_by_afflictions
            state.exhaustion = max(min(held, state.exhaustion), state.exhaustion - 1)
            state.mastery_set = False  # Weapon Master may choose its kind again
            if state.wild_shape is not None:  # no form lasts through a long rest
                self._leave_form(pc, actor)
            total = sum(pool.total for pool in sheet.hit_dice)
            dice_back = max(1, total // 2)
            used = dict(state.hit_dice_used)
            for pool in sheet.hit_dice:  # largest dice back first
                key = f"d{pool.faces}"
                back = min(dice_back, used.get(key, 0))
                used[key] = used.get(key, 0) - back
                dice_back -= back
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
        roll_kind = {
            "check": "check",
            "skill": "check",
            "tool": "check",
            "initiative": "check",
            "attack": "attack",
        }
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
        elif kind == "tool":
            # An ability check with a tool ("dex|Thieves' Tools"): the proficiency bonus
            # is part of the roll when the character has the tool's proficiency.
            key, _, tool = subject.partition("|")
            ability = next((a for a in sheet.abilities if a.key == key), None)
            if ability is None or not tool:
                raise PlayError("play.error.bad_input")
            known = any(t.lower() == tool.lower() for t in sheet.tools)
            bonus = (sheet.proficiency.value or 0) if known else 0
            sources = ((f"ability:{key}", ability.modifier),) + (
                ((f"tool:{tool}", bonus),) if bonus else ()
            )
            rolled = d20(ability.modifier + bonus, sources)
            self._log(
                pc,
                actor,
                LogKind.CHECK,
                key,
                rolled,
                record=made[-1],
                what=f"tool:{key}:{tool.replace(':', ' ')}",
            )
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
            self._spend_ammo(pc, sheet, subject)
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
        if spell is not None:
            _castable(sheet, spell)
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
            if spell is not None:
                _castable(sheet, spell)
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

    @staticmethod
    def _spend_ammo(pc: PlayerCharacter, sheet: Sheet, weapon: str) -> None:
        """A shot uses one round of the weapon's ammunition: a loose one, else one out of
        a bundle, which is opened (``Arrows (20)`` becomes 19 arrows). Nothing to shoot
        does not stop the roll; the table may count differently."""
        attack = next((a for a in sheet.attacks if a.name == weapon), None)
        if attack is None or not attack.ammo:
            return
        loose = next(
            (n for n, i in enumerate(pc.inventory) if i.item.name.lower() == attack.ammo), None
        )
        if loose is not None:
            _take_one(pc, loose)
            return
        bundle = next((i for i in sheet.inventory if i.pack and i.pack[0] == attack.ammo), None)
        if bundle is None or not 0 <= bundle.index < len(pc.inventory) or bundle.pack is None:
            return
        source = pc.inventory[bundle.index].item.source
        _take_one(pc, bundle.index)
        if bundle.pack[1] > 1:
            loose_item = ChosenOption(name=attack.ammo.title(), source=source)
            pc.inventory = [
                *pc.inventory,
                InventoryItem(item=loose_item, quantity=bundle.pack[1] - 1),
            ]

    @staticmethod
    def _notes(pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor) -> None:
        """The player's own notes on the character (the file's ``notes``); not logged."""
        pc.notes = params.get("text", "")[:NOTES_LIMIT]

    def _use(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Use up one of a consumable (a potion, a scroll, antitoxin), logged."""
        index = _int(params, "index", -1)
        item = next((i for i in sheet.inventory if i.index == index), None)
        if item is None or not item.consumable or not 0 <= index < len(pc.inventory):
            raise PlayError("play.error.bad_input")
        reference = pc.inventory[index].item.reference
        _take_one(pc, index)
        self._stage(pc, actor, EntryKind.CUSTOM, what=f"use:{reference}")

    def _light(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Light a torch or candle (one of them burns away) or a lamp or lantern (a
        flask of oil does), logged; the world's clock times it (app/services/clock.py)."""
        index = _int(params, "index", -1)
        if not 0 <= index < len(pc.inventory) or not light_minutes(pc.inventory[index].item.name):
            raise PlayError("play.error.bad_input")
        reference = pc.inventory[index].item.reference
        if pc.inventory[index].item.name.lower() in OIL_LIGHTS:
            oil = next((n for n, i in enumerate(pc.inventory) if i.item.name.lower() in OIL), None)
            if oil is None:
                raise PlayError("play.error.no_oil")
            _take_one(pc, oil)
        else:
            _take_one(pc, index)
        self._stage(pc, actor, EntryKind.CUSTOM, what=f"light:{reference}")

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

    def _wild_shape(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Take a Wild Shape form, spending a use, or leave the one the druid is in."""
        view = sheet.wild_shape
        if params.get("op") == "revert":
            if pc.state.wild_shape is None:
                raise PlayError("play.error.bad_input")
            self._leave_form(pc, actor)
            return
        wanted = ChosenOption.model_validate(params.get("form", "")) if params.get("form") else None
        form = wild_shape.find(view.choices, wanted) if view is not None else None
        if view is None or form is None:
            raise PlayError("play.error.bad_input")
        resource = next(r for r in sheet.resources if r.name == wild_shape.RESOURCE)
        if resource.available <= 0:
            raise PlayError("play.error.resource_spent", name=resource.name)
        state = pc.state
        state.resources_used = {**state.resources_used, resource.name: resource.used + 1}
        state.wild_shape = ChosenOption(name=form.name, source=form.source)
        if view.rules.edition == "2014":
            state.wild_shape_hp = form.hp
        else:
            state.wild_shape_hp = None
            state.temp_hp = max(state.temp_hp, view.rules.temp_hp)
        self._stage(pc, actor, EntryKind.CUSTOM, what=f"wild_shape:on:{form.reference}")

    def _leave_form(self, pc: PlayerCharacter, actor: Actor) -> None:
        form = pc.state.wild_shape
        pc.state.wild_shape = None
        pc.state.wild_shape_hp = None
        if form is not None:
            self._stage(pc, actor, EntryKind.CUSTOM, what=f"wild_shape:off:{form.reference}")

    def _polymorph(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Take the shape a spell holds the character in (``op=assume``: the shape, the
        spell, its caster and the shape's hit points, the spell's rules checked by the
        caller), or leave it (``op=end``)."""
        state = pc.state
        if params.get("op") == "end":
            if state.polymorph is None:
                raise PlayError("play.error.bad_input")
            self._end_polymorph(pc, actor)
            return
        form, spell = params.get("form", ""), params.get("spell", "")
        if "|" not in form or not polymorph.is_polymorph(spell):
            raise PlayError("play.error.bad_input")
        hp = max(1, _int(params, "hp", 1))
        if state.wild_shape is not None:  # a druid's own form gives way to the spell's
            self._leave_form(pc, actor)
        if state.polymorph is not None:
            self._end_polymorph(pc, actor)
        old_rules = polymorph.edition(spell) == "2014"
        state.polymorph = SpellForm(
            form=form, spell=spell, caster=params.get("caster", ""), hp=hp if old_rules else None
        )
        if not old_rules:
            state.temp_hp = max(state.temp_hp, hp)
        self._stage(pc, actor, EntryKind.CUSTOM, what=f"polymorph:on:{form}")

    def _receive(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Items and coins handed to the character (a treasure shared out): ``items`` is
        ``Name|SOURCE*count`` joined by ``;``, coins by denomination, and ``spell`` the
        spell a scroll among them holds. Items join a stack of the same thing already
        carried."""
        got: list[str] = []
        for part in filter(None, params.get("items", "").split(";")):
            ref, _, count_text = part.rpartition("*")
            count = int(count_text) if count_text.isdigit() else 0
            if not ref or count < 1:
                raise PlayError("play.error.bad_input")
            option = ChosenOption.model_validate(ref)
            self._add_items(pc, option, count, params.get("spell", ""))
            got.append(f"{option.reference}*{count}")
        coins = {d: max(0, _int(params, d, 0)) for d in ("cp", "sp", "ep", "gp", "pp")}
        for denomination, amount in coins.items():
            setattr(pc.currency, denomination, getattr(pc.currency, denomination) + amount)
        purse = ",".join(f"{amount}{d}" for d, amount in coins.items() if amount)
        if got or purse:
            self._stage(pc, actor, EntryKind.CUSTOM, what=f"receive:{';'.join(got)}:{purse}")

    def _buy(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Buy ``count`` of ``item`` at ``price`` copper each (the shop's price), paid out
        of the purse; refused when it holds too little."""
        option = ChosenOption.model_validate(params.get("item", ""))
        count = max(1, _int(params, "count", 1))
        cost = max(0, _int(params, "price", 0)) * count
        purse = coins.pay(pc.currency, cost)
        if purse is None:
            raise PlayError("play.error.no_money")
        pc.currency = purse
        self._add_items(pc, option, count)
        self._stage(
            pc, actor, EntryKind.CUSTOM, what=f"buy:{option.reference}*{count}:{coins.text(cost)}"
        )

    def _sell(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Sell ``count`` of the carried item at ``index`` for ``price`` copper each;
        ``ref``, when given, is the item the shop priced, refused if another lies there."""
        index = _int(params, "index")
        count = max(1, _int(params, "count", 1))
        if not 0 <= index < len(pc.inventory) or pc.inventory[index].quantity < count:
            raise PlayError("play.error.bad_input")
        item = pc.inventory[index]
        ref = params.get("ref")
        if item.equipped or (ref is not None and item.item.reference != ref):
            raise PlayError("play.error.bad_input")
        earned = max(0, _int(params, "price", 0)) * count
        item.quantity -= count
        if item.quantity == 0:
            pc.inventory = [i for n, i in enumerate(pc.inventory) if n != index]
        pc.currency = coins.earn(pc.currency, earned)
        self._stage(
            pc,
            actor,
            EntryKind.CUSTOM,
            what=f"sell:{item.item.reference}*{count}:{coins.text(earned)}",
        )

    def _spend(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Pay ``amount`` copper for something (``for``: what, for the log; ``quiet``:
        the caller logs it in its own words)."""
        cost = max(0, _int(params, "amount", 0))
        purse = coins.pay(pc.currency, cost)
        if purse is None:
            raise PlayError("play.error.no_money")
        pc.currency = purse
        if cost and not params.get("quiet"):
            reason = params.get("for", "").replace(":", " ")
            self._stage(pc, actor, EntryKind.CUSTOM, what=f"spend:{coins.text(cost)}:{reason}")

    def _learn(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Learn a language or gain proficiency with a tool (``kind``, ``ref``), as
        training in downtime gives, or write a spell into the spellbook."""
        kind = params.get("kind", "")
        option = ChosenOption.model_validate(params.get("ref", ""))
        if kind not in ("language", "tool", "spell"):
            raise PlayError("play.error.bad_input")
        held = (
            pc.languages
            if kind == "language"
            else pc.tools
            if kind == "tool"
            else pc.spells.known  # a spell copied into the spellbook
        )
        if all(o.name.lower() != option.name.lower() for o in held):
            held.append(option)
        self._stage(pc, actor, EntryKind.CUSTOM, what=f"learn:{kind}:{option.reference}")

    def _inscribe(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Write which spell a spell scroll holds (``index``, ``spell``), as the server
        found it in the world's books; one of a stack is split off."""
        index = _int(params, "index", -1)
        spell = params.get("spell", "")
        if not 0 <= index < len(pc.inventory) or not spell:
            raise PlayError("play.error.bad_input")
        item = pc.inventory[index]
        if item.quantity > 1:
            item.quantity -= 1
            pc.inventory = [*pc.inventory, item.model_copy(update={"quantity": 1, "spell": spell})]
        else:
            item.spell = spell

    def _scroll(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """A spell scroll used up (``index``): cast from (``cast``) or copied into the
        spellbook (``copy``), with the check the server rolled, logged
        ``scroll:<cast|copy>:<spell>:<kept>[:<total>:<dc>]``."""
        index = _int(params, "index", -1)
        if not 0 <= index < len(pc.inventory):
            raise PlayError("play.error.bad_input")
        spell = pc.inventory[index].spell
        _take_one(pc, index)
        what = f"scroll:{params.get('what', 'cast')}:{spell}:{params.get('ok', '1')}"
        if params.get("total"):
            what += f":{params.get('total')}:{params.get('dc', '')}"
        self._stage(pc, actor, EntryKind.CUSTOM, what=what)

    def _eat(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """A day's rations eaten on the road; refused when the pack has none."""
        found = next(
            (
                n
                for n, i in enumerate(pc.inventory)
                if i.item.name.lower() in RATIONS and i.quantity
            ),
            None,
        )
        if found is None:
            raise PlayError("play.error.no_rations")
        _take_one(pc, found)
        pc.state.hunger_levels = 0

    @staticmethod
    def _add_items(pc: PlayerCharacter, option: ChosenOption, count: int, spell: str = "") -> None:
        """Put ``count`` of an item in the pack, on a stack of the same thing if any (a
        spell scroll only on one of the same spell)."""
        stack = next(
            (
                i
                for i in pc.inventory
                if i.item.name.lower() == option.name.lower()
                and (i.item.source or "").lower() == (option.source or "").lower()
                and i.spell.lower() == spell.lower()
            ),
            None,
        )
        if stack is not None:
            stack.quantity += count
        else:
            pc.inventory = [*pc.inventory, InventoryItem(item=option, quantity=count, spell=spell)]

    def _end_polymorph(self, pc: PlayerCharacter, actor: Actor, natural: bool = False) -> None:
        """Leave a spell's shape; ``natural`` when it ended by itself, which ends the spell
        and so the caster's concentration."""
        shape = pc.state.polymorph
        pc.state.polymorph = None
        if shape is None:
            return
        ends = getattr(self._ends, "items", None)
        if natural and shape.caster and ends is not None:
            ends.append((shape.caster, shape.spell))
        if shape.hp is None:  # 2024: the shape's temporary hit points go with it
            pc.state.temp_hp = 0
        self._stage(pc, actor, EntryKind.CUSTOM, what=f"polymorph:off:{shape.form}")

    def _wild_forms(
        self, pc: PlayerCharacter, sheet: Sheet, params: Mapping[str, str], actor: Actor
    ) -> None:
        """Learn or forget a 2024 druid's known form; learning one of the forms its level
        allows, while it knows fewer than its count."""
        view = sheet.wild_shape
        if view is None or view.rules.known is None or not params.get("form"):
            raise PlayError("play.error.bad_input")
        wanted = ChosenOption.model_validate(params["form"])
        if params.get("op") == "forget":
            pc.wild_shapes = [
                f
                for f in pc.wild_shapes
                if (f.name.lower(), (f.source or "").lower())
                != (wanted.name.lower(), (wanted.source or "").lower())
            ]
            return
        form = wild_shape.find(view.forms, wanted)
        if form is None or len(view.known) >= view.rules.known or form in view.known:
            raise PlayError("play.error.bad_input")
        pc.wild_shapes = [*pc.wild_shapes, ChosenOption(name=form.name, source=form.source)]

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


__all__ = ["INTERNAL_ACTIONS", "RATIONS", "Actor", "PlayError", "PlayService"]
