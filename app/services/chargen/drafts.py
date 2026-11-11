"""Drafts: starting a character, saving each step's answers (CHARGEN-WIZARD.md §6).

A draft is an ordinary character file with ``status: draft`` from the first step on,
so a closed tab loses nothing and the character has a name in the lists. Every save
stores the step's answers in ``choices``, writes the character's lists from them
(:meth:`Planner.apply`) and remembers the step to resume at.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from app.models.base import ABILITIES
from app.models.gamestate import GivenName, Role
from app.models.pc import (
    Abilities,
    AbilityMethod,
    DraftStatus,
    HitPointMethod,
    HitPoints,
    PlayerCharacter,
)
from app.models.protocol import EntryKind
from app.services.auth import Identity
from app.services.characters import CharacterNotFound, CharacterService
from app.services.chargen.backstory import Backstory, Physique, Translate, roll_physique
from app.services.chargen.blocks import Pick
from app.services.chargen.catalogue import Catalogue
from app.services.chargen.plan import GOLD, GOLD_ROLL, Plan, Planner
from app.services.chargen.rules import point_budget, point_cost, starting_gold
from app.services.dice import Dice
from app.services.i18n import Translatable
from app.services.naming import Naming, NamingError
from app.services.naming_workshop import NamingWorkshop
from app.services.npc_gen import edition_for
from app.services.protocol import for_character
from app.store.fluff import FluffStore
from app.store.gamestate import GameStateStore
from app.store.lookups import LookupTables
from app.store.npc_tables import NpcTables

# Plain fields a step may set besides its questions.
TEXT_FIELDS = ("name", "pronouns", "appearance", "backstory")
# A point-buy step: ``"str:+1"`` or ``"str:-1"``.
ADJUST = "adjust"
# The equipment step's shop: ``"+Name|SOURCE"`` or ``"-Name|SOURCE"``.
TRADE = "trade"
NAME_LIMIT = 80
BACKSTORY_LIMIT = 4000


class DraftError(Translatable):
    """A draft that cannot be started or changed: no such world, already finished."""


class Drafts:
    """The wizard's drafts for logged-in users, on top of the character store."""

    def __init__(
        self,
        store: GameStateStore,
        compendium: Any,
        lookups: LookupTables,
        characters: CharacterService,
        dice: Dice | None = None,
        life: NpcTables | None = None,
        fluff: FluffStore | None = None,
        images: Path | None = None,
        naming: Naming | None = None,
        workshop: NamingWorkshop | None = None,
    ) -> None:
        """Bind to the library, the loaded corpus and its lookups; ``life`` holds the
        backstory tables (``life.json``), offered when given; ``fluff`` and ``images``
        what the books say about each option; ``naming`` makes names to suggest,
        ``workshop`` knows whose names a new one can descend from."""
        self._naming = naming
        self._workshop = workshop
        self._fluff = fluff
        self._images = images
        self._store = store
        self._compendium = compendium
        self._lookups = lookups
        self._characters = characters
        self._dice = dice or Dice()
        self._life = life

    @property
    def characters(self) -> CharacterService:
        """The character service the drafts sit on (access rules, sources)."""
        return self._characters

    # -- reading ---------------------------------------------------------------------

    def get(self, identity: Identity, character_id: str) -> PlayerCharacter:
        """A draft ``identity`` may work on; a finished character is not one."""
        pc = self._characters.get_for(identity, character_id)
        if pc.status is not DraftStatus.DRAFT:
            raise CharacterNotFound(character_id)
        return pc

    def _relatives(self, pc: PlayerCharacter) -> list[tuple[str, str]]:
        if self._workshop is None or not pc.world_id:
            return []
        return [r for r in self._workshop.relatives(pc.world_id) if r[0] != f"pc:{pc.id}"]

    def planner(self, pc: PlayerCharacter) -> Planner:
        """The planner over the draft's world's books."""
        books = self._characters.source_set(pc)
        catalogue = Catalogue(self._compendium, books, self._lookups, self._fluff, self._images)
        backstory = Backstory(self._life, books, self._dice, catalogue) if self._life else None
        naming = self._naming_of(pc)
        styles = naming.entries if naming else tuple
        return Planner(catalogue, backstory, self._relatives(pc), styles)

    def _naming_of(self, pc: PlayerCharacter) -> Naming | None:
        """The styles of the draft's world (NAMES.md §5b); every style without one."""
        world = self._store.worlds.find(pc.world_id) if pc.world_id else None
        if self._naming is None or world is None:
            return self._naming
        return self._naming.within(world.naming_styles)

    def plan(self, pc: PlayerCharacter) -> Plan:
        """The wizard for ``pc`` as it stands."""
        return self.planner(pc).plan(pc)

    # -- starting --------------------------------------------------------------------

    def start(
        self, identity: Identity, world_id: str | None, player: str | None = None
    ) -> PlayerCharacter:
        """A new draft in ``world_id``.

        A player's draft is their own; the gamemaster names the player it is for, or
        keeps it unassigned. Without any world declared, ``None`` means every book.
        """
        if world_id is not None and self._store.worlds.find(world_id) is None:
            raise DraftError("chargen.error.world")
        if world_id is None and self._store.worlds.list():
            raise DraftError("chargen.error.world")
        owner = player if identity.role is Role.GAMEMASTER else identity.user.username
        created = self._characters.create("", owner)
        draft = self._store.characters.update(created.id, world_id=world_id)
        # A wizard_step marks the file as the wizard's: lists link it back here.
        first = self.plan(draft).steps[0].id
        return self._store.characters.update(created.id, wizard_step=first)

    # -- saving ----------------------------------------------------------------------

    def save(
        self,
        identity: Identity,
        character_id: str,
        step_id: str,
        answers: Mapping[str, Sequence[str]],
        fields: Mapping[str, str] | None = None,
    ) -> tuple[PlayerCharacter, Plan]:
        """Store a step's answers and fields; return the draft and its new plan.

        Only the step's own questions are taken from ``answers``. A question of this
        step that the new answers make disappear (the subrace of a species no longer
        chosen) loses its answer at once — the player is looking at it. Answers to
        other steps are kept even when they no longer fit: their step then reads
        "review" (CHARGEN-WIZARD.md §6).
        """
        pc = self.get(identity, character_id)
        planner = self.planner(pc)
        before = planner.plan(pc).step(step_id)
        if before is None:
            raise DraftError("chargen.error.step")

        def change(record: PlayerCharacter) -> None:
            asked = {p.id for p in before.picks}
            self._take(planner, record, before.picks, answers)
            # An answer can bring the question it belongs with — a background and its
            # +2/+1 posted together: take those too, until no new question appears.
            for _ in range(3):
                now = planner.plan(record).step(step_id)
                fresh = [p for p in (now.picks if now else ()) if p.id not in asked]
                if not fresh:
                    break
                asked |= {p.id for p in fresh}
                self._take(planner, record, [p for p in fresh if p.id in answers], answers)
            scores = record.abilities.as_dict()
            for key, value in (fields or {}).items():
                if key in TEXT_FIELDS:
                    limit = NAME_LIMIT if key == "name" else BACKSTORY_LIMIT
                    setattr(record, key, value.strip()[:limit])
                elif key.startswith("score.") and key[6:] in ABILITIES:
                    raw = value.strip()
                    number = int(raw) if raw.lstrip("-").isdigit() else None
                    scores[key[6:]] = number if number is not None and 1 <= number <= 30 else None
            method = (record.choices.get("abilities.method") or [""])[0]
            if method == AbilityMethod.POINTBUY.value:
                scores = _point_buy(scores, (fields or {}).get("adjust", ""))
            record.abilities = Abilities.model_validate(scores)
            if (fields or {}).get(TRADE):
                planner.trade(record, (fields or {})[TRADE])
            # Questions of this step that its new answers removed. Packages left for
            # gold keep theirs: a change of mind finds them as they were.
            after = planner.plan(record).step(step_id)
            remaining = {p.id for p in after.picks} if after is not None else set()
            if (record.choices.get("equipment") or [""])[0] != GOLD or step_id != "equipment":
                for gone in asked - remaining:
                    planner.answer(record, gone, [])
            record.wizard_step = step_id
            planner.apply(record)

        saved = self._store.characters.mutate(character_id, change)
        return saved, planner.plan(saved)

    @staticmethod
    def _take(
        planner: Planner,
        record: PlayerCharacter,
        picks: Sequence[Pick],
        answers: Mapping[str, Sequence[str]],
    ) -> None:
        for pick in picks:
            values = [str(v).strip() for v in answers.get(pick.id, ())]
            feats = [v for v in values if v.startswith("feat:")]
            if pick.kind == "asi" and feats:
                # A feat instead of the increase: the two ability selects are ignored.
                values = feats[:1]
            if pick.weights and any(values):
                # A distribution keeps its slots: "+2 to —, +1 to INT" is not +2 INT.
                record.choices = {**record.choices, pick.id: values}
            else:
                planner.answer(record, pick.id, values)

    def roll_backstory(
        self,
        identity: Identity,
        character_id: str,
        key: str,
        labels: Mapping[str, str] | None = None,
        translate: Translate | None = None,
    ) -> PlayerCharacter:
        """Roll on a backstory table (or on each, for ``"all"``) and add the lines to the
        draft's backstory, which stays the player's to edit."""
        pc = self.get(identity, character_id)
        backstory = self.planner(pc).backstory
        lines = backstory.roll(pc, key, labels, translate) if backstory is not None else []
        if not lines:
            raise DraftError("chargen.error.backstory")

        def change(record: PlayerCharacter) -> None:
            text = "\n\n".join(x for x in (record.backstory.strip(), *lines) if x)
            record.backstory = text[:BACKSTORY_LIMIT]

        return self._store.characters.mutate(character_id, change)

    def roll_physique(
        self, identity: Identity, character_id: str, line: Callable[[Physique], str]
    ) -> PlayerCharacter:
        """Roll height and weight on the species' table and add ``line`` of them to the
        draft's appearance, which stays the player's to edit."""
        pc = self.get(identity, character_id)
        table = self.planner(pc).physique(pc)
        if table is None:
            raise DraftError("chargen.error.physique")
        rolled = line(roll_physique(table, self._dice))

        def change(record: PlayerCharacter) -> None:
            text = "\n".join(x for x in (record.appearance.strip(), rolled) if x)
            record.appearance = text[:BACKSTORY_LIMIT]

        return self._store.characters.mutate(character_id, change)

    def suggest_name(
        self,
        identity: Identity,
        character_id: str,
        gender: str = "any",
        style: str = "",
        parent: str = "",
    ) -> PlayerCharacter:
        """Name the draft in ``style``, or by default for its species and subspecies,
        avoiding the world's other characters (NAMES.md §5)."""
        pc = self.get(identity, character_id)
        naming = self._naming_of(pc)
        if naming is None or self._life is None:
            raise DraftError("chargen.error.name_style")
        world = self._store.worlds.find(pc.world_id) if pc.world_id else None
        language = identity.user.language
        key = (
            style
            or naming.find(
                pc.species.name if pc.species else "",
                pc.subspecies.name if pc.subspecies else "",
                edition_for(world.source_set if world else None, self._life),
                language,
            ).key
        )
        used = [c.name for c in self._store.characters.list() if c.world_id == pc.world_id]
        kin = self._workshop.kin_parts(parent) if self._workshop else {}
        try:
            made = naming.made(key, gender, 1, used, self._dice, kin=kin, language=language)
        except NamingError:
            made = []
        if not made:
            raise DraftError("chargen.error.name_style")

        def change(record: PlayerCharacter) -> None:
            record.name = made[0].text
            record.given_name = GivenName(text=made[0].text, parts=made[0].parts)

        return self._store.characters.mutate(character_id, change)

    def roll_abilities(self, identity: Identity, character_id: str) -> PlayerCharacter:
        """Roll six scores, 4d6 dropping the lowest die, and log them.

        A roll is a commitment (CHARGEN-WIZARD.md §4): rolling again is refused unless
        the world allows re-rolls. The scores are then assigned like the standard array.
        """
        pc = self.get(identity, character_id)
        world = self._store.worlds.find(pc.world_id) if pc.world_id else None
        if pc.ability_rolls and not (world is not None and world.allow_rerolls):
            raise DraftError("chargen.error.rolled")
        rolls = []
        details = []
        for _ in range(6):
            dice = sorted((self._dice.die(6) for _ in range(4)), reverse=True)
            rolls.append(sum(dice[:3]))
            details.append("+".join(str(d) for d in dice[:3]) + f" ({dice[3]})")
        planner = self.planner(pc)
        by = identity.user.display_name or identity.user.username

        def change(record: PlayerCharacter) -> None:
            planner.answer(record, "abilities.method", [AbilityMethod.ROLL.value])
            record.ability_rolls = rolls
            record.abilities = Abilities()
            record.wizard_step = "abilities"
            planner.apply(record)

        saved = self._store.characters.mutate(character_id, change)
        self.characters.record(
            for_character(
                saved,
                EntryKind.ABILITY_SCORES,
                actor=identity.user.username,
                actor_name=by,
                what="4d6: " + " · ".join(str(r) for r in rolls),
                note=", ".join(details),
            )
        )
        return saved

    def roll_hit_points(self, identity: Identity, character_id: str) -> PlayerCharacter:
        """Roll the hit die for every level after the first; the first takes the full die.

        Logged, and final in the same way as rolled ability scores. CON is not part of
        the stored results: the sheet adds it per level, so a later CON change counts.
        """
        pc = self.get(identity, character_id)
        planner = self.planner(pc)
        plan = planner.plan(pc)
        if plan.cls is None:
            raise DraftError("chargen.error.no_class")
        world = self._store.worlds.find(pc.world_id) if pc.world_id else None
        if pc.hit_points.per_level and not (world is not None and world.allow_rerolls):
            raise DraftError("chargen.error.rolled")
        hd = plan.cls.data.get("hd")
        faces = int(hd.get("faces", 8)) if isinstance(hd, Mapping) else 8
        level = planner.level_of(pc.choices)
        results = [faces, *(self._dice.die(faces) for _ in range(level - 1))]
        by = identity.user.display_name or identity.user.username

        def change(record: PlayerCharacter) -> None:
            planner.answer(record, "hp", [HitPointMethod.ROLLED.value])
            record.hit_points = HitPoints(method=HitPointMethod.ROLLED, per_level=results)
            record.wizard_step = "features"
            planner.apply(record)

        saved = self._store.characters.mutate(character_id, change)
        self.characters.record(
            for_character(
                saved,
                EntryKind.HIT_POINTS,
                actor=identity.user.username,
                actor_name=by,
                what=f"d{faces}: " + " · ".join(str(r) for r in results[1:]),
            )
        )
        return saved

    def roll_gold(self, identity: Identity, character_id: str) -> PlayerCharacter:
        """Roll the class's starting gold; logged, and final like rolled ability scores."""
        pc = self.get(identity, character_id)
        planner = self.planner(pc)
        cls = planner.plan(pc).cls
        roll = starting_gold(cls.data) if cls is not None else None
        if cls is None or roll is None:
            raise DraftError("chargen.error.no_gold")
        world = self._store.worlds.find(pc.world_id) if pc.world_id else None
        if planner.gold(pc, cls) is not None and not (world is not None and world.allow_rerolls):
            raise DraftError("chargen.error.gold_rolled")
        dice = [self._dice.die(roll.faces) for _ in range(roll.count)]
        gold = sum(dice) * roll.times
        by = identity.user.display_name or identity.user.username

        def change(record: PlayerCharacter) -> None:
            planner.answer(record, "equipment", [GOLD])
            planner.answer(record, GOLD_ROLL, [str(gold * 100), cls.reference])
            record.wizard_step = "equipment"
            planner.apply(record)

        saved = self._store.characters.mutate(character_id, change)
        times = f" × {roll.times}" if roll.times > 1 else ""
        self.characters.record(
            for_character(
                saved,
                EntryKind.STARTING_GOLD,
                actor=identity.user.username,
                actor_name=by,
                what=f"{roll.count}d{roll.faces}{times}: {gold}",
                note="+".join(str(d) for d in dice),
            )
        )
        return saved

    def finish(self, identity: Identity, character_id: str) -> PlayerCharacter:
        """Mark a complete draft ready: from now on it is a character with a sheet.

        Refused while any step is open or needs review — nothing is validated only at
        the end, so this is the same rule the footer shows (CHARGEN-WIZARD.md §5). The
        answers stay in ``choices``; hit points start full.
        """
        pc = self.get(identity, character_id)
        planner = self.planner(pc)
        if not planner.plan(pc).complete:
            raise DraftError("chargen.error.incomplete")

        def change(record: PlayerCharacter) -> None:
            planner.apply(record)
            record.status = DraftStatus.READY
            record.state = record.state.model_copy(update={"hp": None, "temp_hp": 0})

        return self._store.characters.mutate(character_id, change)

    def resume_at(self, pc: PlayerCharacter, plan: Plan) -> str:
        """Where to reopen the wizard: the stored step if it still exists, else the first."""
        if pc.wizard_step and plan.step(pc.wizard_step) is not None:
            return pc.wizard_step
        return plan.steps[0].id


def _point_buy(scores: dict[str, int | None], adjust: str) -> dict[str, int | None]:
    """Point-buy scores: missing ones start at 8; ``"str:+1"`` raises one if affordable.

    Going over the budget is impossible rather than flagged (CHARGEN-WIZARD.md §4): an
    adjustment that would cost more than is left is ignored, and so is one past 8–15.
    """
    out = {a: (v if v is not None and 8 <= v <= 15 else 8) for a, v in scores.items()}
    ability, _, step = adjust.partition(":")
    if ability in out and step in ("+1", "-1"):
        trial = dict(out)
        trial[ability] = out[ability] + int(step)
        cost = point_cost({a: v for a, v in trial.items() if v is not None})
        if 8 <= trial[ability] <= 15 and cost is not None and cost <= point_budget():
            out = trial
    return dict(out)


__all__ = ["DraftError", "Drafts"]
