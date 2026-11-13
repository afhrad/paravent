"""Levelling up: what one more level brings, asked and applied (CHARGEN-WIZARD.md §9).

The gamemaster grants a level (``pending_levels``); the player answers only what the
new level brings, found by comparing the class's level plan at N with N + 1 — the same
plan the creation wizard reads, so no class is special-cased:

* the subclass, when this is the level it is chosen at;
* an Ability Score Improvement (or feat) at an ASI level, and a chosen feat's own
  increase;
* the optional features and feat progressions whose count grows (one more invocation);
* new cantrips and spells — the growth in spells known, two for a wizard's book —
  from the spell levels the new level reaches;
* hit points: the average, or one roll of the hit die for a character whose hit points
  are rolled.

**Multiclassing** is the same engine: the first question is which class gains the
level — one the character has, or a new one of the same rule edition whose minimum
scores are met (and, by the 2014 rule, those of the classes already held). A new class
starts at its level 1 with the reduced proficiencies of ``multiclassing``; its spells
are counted as if it were the only class, as the rules count them.

It works on any character, made in the wizard or by hand: the answers change the
character's own lists directly, and are kept under ``levels.<N>.<question>`` in
``choices`` as the record of what each level gave. While in progress they live under
``levelup.<question>``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from app.models.base import ABILITIES
from app.models.gamestate import Role
from app.models.pc import (
    ChosenOption,
    ClassLevel,
    DraftStatus,
    HitPointMethod,
    PlayerCharacter,
)
from app.models.protocol import EntryKind
from app.services.auth import Identity
from app.services.chargen.blocks import (
    ABILITY_LIMIT,
    Pick,
    ability_block,
    ability_increases,
    proficiencies,
)
from app.services.chargen.catalogue import Offer
from app.services.chargen.drafts import Drafts
from app.services.chargen.feat_choices import feat_choice_picks, repeatable
from app.services.chargen.plan import (
    ASI_FEATURE,
    LevelPlan,
    Planner,
    Problem,
    level_plan,
    limit_problems,
)
from app.services.chargen.prereq import Context, Verdict, check
from app.services.chargen.rules import Requirement, meets, multiclass_requirement
from app.services.class_refs import files_under, subclass_owner
from app.services.dice import Dice
from app.services.i18n import Translatable
from app.services.protocol import for_character
from app.services.sheet import Sheet, build_sheet
from app.store.gamestate import GameStateStore

PREFIX = "levelup."
ProficiencyKind = Literal["skill", "tool", "language"]
_PROFICIENCY_KEYS: tuple[tuple[ProficiencyKind, str, str], ...] = (
    ("skill", "skills", "skillProficiencies"),
    ("tool", "tools", "toolProficiencies"),
    ("language", "languages", "languageProficiencies"),
)
# Questions that may be left unanswered: replace one spell, one option or one style.
SWAP = "swap."
# Option kinds a level that adds one may also trade one of: invocations, Battle Master
# maneuvers, Metamagic.
SWAPPABLE_OPTIONS = ("EI", "MV:B", "MM")


def _caster_record(cls: Offer, subclass: Offer | None) -> Mapping[str, Any]:
    """The record that holds the spell progressions: the class, or a casting subclass."""
    if (
        not cls.data.get("casterProgression")
        and subclass is not None
        and subclass.data.get("casterProgression")
    ):
        return subclass.data
    return cls.data


@dataclass(frozen=True)
class LevelUp:
    """One level's worth of growth for one character, as it stands."""

    cls: Offer
    subclass: Offer | None
    from_level: int
    to_level: int
    gains: tuple[tuple[str, int], ...]
    picks: tuple[Pick, ...]
    missing: tuple[str, ...]
    problems: tuple[Problem, ...]
    hit_die: int
    hp_method: HitPointMethod
    hp_roll: int | None
    before: Sheet
    after: Sheet
    verdicts: Mapping[str, Verdict] = field(default_factory=dict)
    prepared: int = 0
    # Where the level goes: the index in ``classes``, or None for a new class.
    index: int | None = 0
    character_level: int = 0
    # Classes the scores keep out, with what they need; and, by the 2014 rule, the
    # character's own classes whose minimums keep any new class out.
    blocked: tuple[tuple[Offer, Requirement], ...] = ()
    held: tuple[tuple[Offer, Requirement], ...] = ()

    @property
    def complete(self) -> bool:
        """True when every question is answered and fits: the level may be taken."""
        return not self.missing and not self.problems

    @property
    def new_class(self) -> bool:
        """True when this level starts a new class."""
        return self.index is None

    @property
    def hp_gain(self) -> int:
        """How much the maximum rises."""
        return (self.after.hp_max.value or 0) - (self.before.hp_max.value or 0)


class LevelUpError(Translatable):
    """A level that cannot be taken: none granted, no class to level, not finished."""


def _answers(pc: PlayerCharacter) -> dict[str, list[str]]:
    return {k.removeprefix(PREFIX): v for k, v in pc.choices.items() if k.startswith(PREFIX)}


class LevelUpPlanner:
    """Level-up plans and their application, over one world's books."""

    def __init__(self, planner: Planner) -> None:
        """Reuse the creation planner: its catalogue and its answer checks."""
        self.planner = planner
        self.catalogue = planner.catalogue

    # -- reading ---------------------------------------------------------------------

    def _current(self, pc: PlayerCharacter) -> list[tuple[int, ClassLevel, Offer]]:
        """The character's classes the books know, with their index in ``classes``."""
        out = []
        for index, entry in enumerate(pc.classes):
            cls = self.catalogue.find("class", ChosenOption(name=entry.name, source=entry.source))
            if cls is not None:
                out.append((index, entry, cls))
        if not out or out[0][0] != 0:
            raise LevelUpError("levelup.error.no_class")
        return out

    def _subclass(self, cls: Offer, entry: ClassLevel) -> Offer | None:
        if entry.subclass is None:
            return None
        offers = self.catalogue.subclasses(cls)
        found = next((o for o in offers if o.matches(entry.subclass)), None)
        if found is None:  # a short name ("Evocation"), as hand-written files have
            wanted = entry.subclass.name.lower()
            found = next(
                (o for o in offers if str(o.data.get("shortName", "")).lower() == wanted), None
            )
        return found

    def _multiclass(
        self, current: list[tuple[int, ClassLevel, Offer]], scores: Mapping[str, int]
    ) -> tuple[list[Offer], list[tuple[Offer, Requirement]], list[tuple[Offer, Requirement]]]:
        """New classes open to the character, those the scores keep out, and own holds."""
        primary = current[0][2]
        held = []
        if primary.edition == "classic":  # 2014: leaving a class needs its minimums too
            for _i, _entry, cls in current:
                need = multiclass_requirement(cls.data)
                if not meets(need, scores):
                    held.append((cls, need))
        have = {cls.name.lower() for _i, _entry, cls in current}
        open_: list[Offer] = []
        blocked: list[tuple[Offer, Requirement]] = []
        for cls in self.catalogue.classes():
            if cls.edition != primary.edition or cls.name.lower() in have:
                continue
            need = multiclass_requirement(cls.data)
            if held:
                continue
            if meets(need, scores):
                open_.append(cls)
            else:
                blocked.append((cls, need))
        return open_, blocked, held

    def _sheet(self, pc: PlayerCharacter) -> Sheet:
        return build_sheet(pc, self.catalogue.compendium, source_set=self.catalogue.source_set)

    def plan(self, pc: PlayerCharacter) -> LevelUp:
        """What the next level brings for ``pc``, with the answers given so far."""
        if pc.pending_levels < 1:
            raise LevelUpError("levelup.error.none_granted")
        current = self._current(pc)
        answers = _answers(pc)
        before = self._sheet(pc)
        scores = {a.key: a.score.value or 0 for a in before.abilities}
        open_, blocked, held = self._multiclass(current, scores)

        # Which class gains the level: the answer, if it is still open; else the first.
        targets: list[tuple[int | None, Offer]] = [(i, cls) for i, _e, cls in current]
        targets += [(None, cls) for cls in open_]
        wanted = (answers.get("class") or [""])[0].lower()
        index, cls = next(((i, c) for i, c in targets if c.reference.lower() == wanted), targets[0])
        entry = pc.classes[index] if index is not None else None
        subclass = self._subclass(cls, entry) if entry is not None else None
        n = entry.level if entry is not None else 0

        after_pc = pc.model_copy(deep=True)
        self._write(after_pc, answers, cls, index)
        after = self._sheet(after_pc)
        chosen_sub = subclass
        if chosen_sub is None and answers.get("subclass"):
            chosen_sub = self.catalogue.find("subclass", answers["subclass"][0])
        # A class's spells are counted as if it were the only one (PHB ch. 6): its own
        # level decides how many and of which spell level, not the shared slots.
        before_own, after_own = before, after
        if len(after_pc.classes) > 1:
            at = index if index is not None else len(after_pc.classes) - 1
            after_own = self._sheet(after_pc.model_copy(update={"classes": [after_pc.classes[at]]}))
            if entry is not None:
                before_own = self._sheet(pc.model_copy(update={"classes": [entry]}))
        old = level_plan(cls, subclass, n, before_own)
        new = level_plan(cls, chosen_sub, n + 1, after_own)
        ctx = self._context(after_pc, after)

        picks: list[Pick] = []
        verdicts: dict[str, Verdict] = {}
        if len(targets) > 1:
            picks.append(
                Pick("class", "class", "class", options=tuple(c.reference for _i, c in targets))
            )
        due = new.subclass_level is not None and new.subclass_level <= n + 1
        if due and (entry is None or entry.subclass is None):
            picks.append(
                Pick(
                    "subclass",
                    "subclass",
                    cls.name,
                    options=tuple(o.reference for o in self.catalogue.subclasses(cls)),
                    label=str(cls.data.get("subclassTitle") or ""),
                )
            )
        if n + 1 in new.asi_levels:
            # A feat already taken is offered again only if it may be (Elemental Adept).
            held_feats = {f.name.lower() for f in pc.feats}
            general = [
                f
                for f in self._general_feats(cls)
                if f.name.lower() not in held_feats or repeatable(f)
            ]
            picks.append(
                Pick(
                    "asi",
                    "asi",
                    f"level {n + 1}",
                    count=2,
                    options=(*ABILITIES, *(f"feat:{f.reference}" for f in general)),
                    label=str(n + 1),
                    limit=ABILITY_LIMIT,
                )
            )
            for offer in general:
                verdicts[offer.reference] = check(offer.data.get("prerequisite"), ctx)
            feat = next((v for v in answers.get("asi", []) if v.startswith("feat:")), None)
            taken = self.catalogue.find("feat", feat.removeprefix("feat:")) if feat else None
            if taken is not None:
                _fixed, extra = ability_block(
                    taken.data.get("ability"), taken.name, "asi.feat", answers, ABILITY_LIMIT
                )
                picks.extend(extra)
                picks.extend(
                    self.planner.granted_questions(
                        f"feat:{taken.reference}", taken, None, pc.total_level + 1, answers
                    )
                )
        new_feats = self._new_feats(pc, after_pc)
        picks.extend(
            self._proficiency_picks(pc, self._proficiency_sources(cls, index, new_feats), answers)
        )
        # What else a new feat asks: maneuvers, weapons, a damage type (feat_choices.py).
        picks.extend(
            feat_choice_picks(
                new_feats,
                self.catalogue,
                ctx,
                verdicts,
                held_options=(o.name for o in pc.optional_features),
                held_weapons=(w.name for w in pc.weapons),
                held_choices=pc.choices,
            )
        )
        picks.extend(self._growth(pc, old, new, ctx, verdicts))
        # Choices between features the new level brings (a Hunter's Defensive Tactics), and
        # a subclass spell list not chosen yet.
        made = entry.options if entry is not None else {}
        picks.extend(
            p
            for p in self.planner.option_picks(cls, chosen_sub, [n + 1])
            if p.id.removeprefix("featopt.") not in made
        )
        picks.extend(self._spells(pc, cls, chosen_sub, old, new))
        if index is not None:
            picks.extend(self._swaps(pc, cls, chosen_sub, new, ctx, verdicts))
        picks.extend(self._expertise(pc, after_pc, cls, n + 1, new_feats, answers))

        faces = self._faces(cls)
        method = pc.hit_points.method
        roll = None
        if method is HitPointMethod.ROLLED:
            given = answers.get("hp") or []
            roll = int(given[0]) if given and given[0].isdigit() else None

        missing: list[str] = []
        problems: list[Problem] = []
        for pick in picks:
            if pick.id == "class":  # unanswered means the first class: never missing
                continue
            values = answers.get(pick.id) or []
            if pick.id.startswith(SWAP):
                # A swap may be left alone; once half of it is given, the other half is owed.
                other = (
                    pick.id[: -len("out")] + "in"
                    if pick.id.endswith("out")
                    else (pick.id[: -len("in")] + "out")
                )
                if not values and not answers.get(other):
                    continue
            if not values or self.planner.partial(pick, values):
                missing.append(pick.id)
                continue
            problem = self.planner.check_answer(pick, values, verdicts)
            if problem is not None:
                problems.append(problem)
        problems.extend(
            limit_problems(picks, answers, {a.key: a.score.value or 0 for a in after.abilities})
        )
        if method is HitPointMethod.ROLLED and roll is None:
            missing.append("hp")
        gains = tuple(
            (name, lvl) for name, lvl in new.features if lvl == n + 1
        ) + self._subclass_gains(cls, chosen_sub, n + 1)
        return LevelUp(
            cls,
            chosen_sub,
            n,
            n + 1,
            gains,
            tuple(picks),
            tuple(missing),
            tuple(problems),
            faces,
            method,
            roll,
            before,
            after,
            verdicts,
            new.spells.prepared,
            index,
            pc.total_level + 1,
            tuple(blocked),
            tuple(held),
        )

    def _new_feats(self, pc: PlayerCharacter, after: PlayerCharacter) -> list[Offer]:
        """The feats this level adds (an increase taken as a feat, a fighting style)."""
        return [
            offer
            for f in after.feats[len(pc.feats) :]
            if (offer := self.catalogue.find("feat", f)) is not None
        ]

    @staticmethod
    def _proficiency_sources(
        cls: Offer, index: int | None, feats: Sequence[Offer]
    ) -> list[tuple[ProficiencyKind, str, Any, str, str]]:
        """Proficiency blocks this level brings: kind, attribute, block, origin, pick id.

        A new class's reduced ``multiclassing.proficienciesGained`` (a Rogue's one
        skill), and a new feat's own skills, tools and languages (Skill Expert's skill).
        """
        out: list[tuple[ProficiencyKind, str, Any, str, str]] = []
        if index is None:
            gained = (cls.data.get("multiclassing") or {}).get("proficienciesGained") or {}
            out.append(("skill", "skills", gained.get("skills"), cls.name, "mc.skill"))
            out.append(("tool", "tools", gained.get("toolProficiencies"), cls.name, "mc.tool"))
        for feat in feats:
            prefix = f"feat.{feat.reference.lower()}"
            for kind, attr, key in _PROFICIENCY_KEYS:
                block = feat.data.get(key)
                out.append((kind, attr, block, feat.name, f"{prefix}.{kind}s"))
        return out

    def _proficiency_picks(
        self,
        pc: PlayerCharacter,
        sources: Sequence[tuple[ProficiencyKind, str, Any, str, str]],
        answers: Mapping[str, list[str]],
    ) -> list[Pick]:
        """The choices of those blocks, never offering a proficiency already held."""
        picks: list[Pick] = []
        for kind, attr, block, origin, pick_id in sources:
            if not block:
                continue
            _grants, found = proficiencies(block, kind, origin, pick_id, answers)
            had = {o.name.lower() for o in getattr(pc, attr)}
            for pick in found:
                if pick.options:
                    pick = replace(
                        pick, options=tuple(o for o in pick.options if o.lower() not in had)
                    )
                picks.append(pick)
        return picks

    def _general_feats(self, cls: Offer) -> list[Offer]:
        if cls.edition == "one":
            return [f for f in self.catalogue.feats(("G",)) if f.name.lower() != ASI_FEATURE]
        return [f for f in self.catalogue.feats() if f.data.get("category") in (None, "G")]

    def _growth(
        self,
        pc: PlayerCharacter,
        old: LevelPlan,
        new: LevelPlan,
        ctx: Context,
        verdicts: dict[str, Verdict],
    ) -> list[Pick]:
        """One more invocation, a third metamagic, another fighting style feat."""
        picks = []
        had_options = {o.name.lower() for o in pc.optional_features}
        before = {name: count for name, _types, count in old.optional}
        for name, types, count in new.optional:
            more = count - before.get(name, 0)
            if more <= 0:
                continue
            offers = [
                o
                for o in self.catalogue.optional_features(types)
                if o.name.lower() not in had_options
            ]
            for offer in offers:
                verdicts[offer.reference] = check(offer.data.get("prerequisite"), ctx)
            picks.append(
                Pick(
                    f"opt.{name}",
                    "optional",
                    name,
                    count=more,
                    options=tuple(o.reference for o in offers),
                    label=name,
                )
            )
        had_feats = {f.name.lower() for f in pc.feats}
        before_feats = {name: count for name, _cats, count in old.feat_progressions}
        for name, categories, count in new.feat_progressions:
            more = count - before_feats.get(name, 0)
            if more <= 0:
                continue
            offers = [
                f for f in self.catalogue.feats(categories) if f.name.lower() not in had_feats
            ]
            for offer in offers:
                verdicts[offer.reference] = check(offer.data.get("prerequisite"), ctx)
            picks.append(
                Pick(
                    f"featprog.{name}",
                    "feat",
                    name,
                    count=more,
                    options=tuple(f.reference for f in offers),
                    label=name,
                )
            )
        return picks

    def _spells(
        self,
        pc: PlayerCharacter,
        cls: Offer,
        subclass: Offer | None,
        old: LevelPlan,
        new: LevelPlan,
    ) -> list[Pick]:
        """New cantrips and spells: the growth in what is known, from what is reachable.

        A 2024 bard, sorcerer or warlock keeps its spells as a prepared list that changes
        on level up (``preparedSpellsChange: "level"``): the growth of that list is asked
        here. A cleric's or a wizard's prepared spells change after a long rest instead,
        on the sheet.
        """
        picks = []
        had = {s.name.lower() for s in (*pc.spells.cantrips, *pc.spells.known, *pc.spells.prepared)}
        more_cantrips = new.spells.cantrips - old.spells.cantrips
        if more_cantrips > 0:
            offers = [
                o
                for o in self.catalogue.class_spells(cls, 0, subclass)
                if o.name.lower() not in had
            ]
            picks.append(
                Pick(
                    "spells.cantrips",
                    "cantrip",
                    cls.name,
                    count=more_cantrips,
                    options=tuple(o.reference for o in offers),
                )
            )
        more_known = new.spells.known - old.spells.known
        if more_known > 0:
            offers = [
                o
                for o in self.catalogue.class_spells(cls, new.spells.max_level, subclass)
                if o.data.get("level", 0) >= 1 and o.name.lower() not in had
            ]
            picks.append(
                Pick(
                    "spells.known",
                    "spell",
                    cls.name,
                    count=more_known,
                    options=tuple(o.reference for o in offers),
                )
            )
        more_prepared = new.spells.prepared - old.spells.prepared
        if (
            more_prepared > 0
            and _caster_record(cls, subclass).get("preparedSpellsChange") == "level"
        ):
            offers = [
                o
                for o in self.catalogue.class_spells(cls, new.spells.max_level, subclass)
                if o.data.get("level", 0) >= 1 and o.name.lower() not in had
            ]
            picks.append(
                Pick(
                    "spells.prepared",
                    "spell",
                    cls.name,
                    count=more_prepared,
                    options=tuple(o.reference for o in offers),
                )
            )
        return picks

    def _expertise(
        self,
        pc: PlayerCharacter,
        after: PlayerCharacter,
        cls: Offer,
        level: int,
        new_feats: Sequence[Offer],
        answers: Mapping[str, list[str]],
    ) -> list[Pick]:
        """Expertise the class level or this level's new feats ask for (Rogue 6: two).

        The options are the proficiencies the character has once the level is taken —
        a new class's skill included — less the Expertise already held.
        """
        picks, _fixed = self.planner.expertise_picks(
            cls,
            range(level, level + 1),
            new_feats,
            {o.name.lower() for o in after.skills},
            {o.name.lower() for o in after.tools},
            answers,
            had=[o.name for o in pc.expertise],
        )
        return picks

    def _swaps(
        self,
        pc: PlayerCharacter,
        cls: Offer,
        subclass: Offer | None,
        new: LevelPlan,
        ctx: Context,
        verdicts: dict[str, Verdict],
    ) -> list[Pick]:
        """One spell, one cantrip (2024) and one invocation the level may replace — each
        optional.

        A known-spells caster (``spellsKnownProgression``: the 2014 bard, ranger,
        sorcerer and warlock, an Eldritch Knight) and a 2024 caster whose list changes on
        level up may replace one spell with another of a level it can cast. A warlock may
        replace one invocation (``featureType`` ``EI``) with one it could learn now.
        """
        picks: list[Pick] = []
        record = _caster_record(cls, subclass)
        if record.get("spellsKnownProgression") or record.get("preparedSpellsChange") == "level":
            pool = pc.spells.known if record.get("spellsKnownProgression") else pc.spells.prepared
            current = []
            for option in pool:
                offer = self.catalogue.find("spell", option)
                if offer is not None and offer.data.get("level", 0) >= 1:
                    current.append(offer.reference)
            had = {
                s.name.lower() for s in (*pc.spells.cantrips, *pc.spells.known, *pc.spells.prepared)
            }
            offers = [
                o
                for o in self.catalogue.class_spells(cls, new.spells.max_level, subclass)
                if o.data.get("level", 0) >= 1 and o.name.lower() not in had
            ]
            if current and offers:
                picks.append(Pick(f"{SWAP}spell.out", "spell", cls.name, options=tuple(current)))
                picks.append(
                    Pick(
                        f"{SWAP}spell.in",
                        "spell",
                        cls.name,
                        options=tuple(o.reference for o in offers),
                    )
                )
        if cls.edition == "one" and record.get("cantripProgression"):
            # 2024: on gaining a level in the class, one cantrip may be replaced.
            mine = [
                offer.reference
                for option in pc.spells.cantrips
                if (offer := self.catalogue.find("spell", option)) is not None
            ]
            had_cantrips = {o.name.lower() for o in pc.spells.cantrips}
            others = [
                o
                for o in self.catalogue.class_spells(cls, 0, subclass)
                if o.data.get("level", 0) == 0 and o.name.lower() not in had_cantrips
            ]
            if mine and others:
                picks.append(Pick(f"{SWAP}cantrip.out", "cantrip", cls.name, options=tuple(mine)))
                picks.append(
                    Pick(
                        f"{SWAP}cantrip.in",
                        "cantrip",
                        cls.name,
                        options=tuple(o.reference for o in others),
                    )
                )
        # Options the level adds of a kind that may be traded: an invocation (warlock),
        # a maneuver (Battle Master: "each time you learn new maneuvers"), a Metamagic
        # option (sorcerer). One of them, the first the level grows.
        swappable = next(
            (
                kind
                for kind in SWAPPABLE_OPTIONS
                if any(kind in types for _name, types, _count in new.optional)
            ),
            None,
        )
        if swappable is not None:
            held = []
            for option in pc.optional_features:
                offer = self.catalogue.find("optionalfeature", option)
                if offer is not None and swappable in (offer.data.get("featureType") or []):
                    held.append(offer.reference)
            names = {o.name.lower() for o in pc.optional_features}
            offers = [
                o
                for o in self.catalogue.optional_features((swappable,))
                if o.name.lower() not in names
            ]
            for offer in offers:
                verdicts[offer.reference] = check(offer.data.get("prerequisite"), ctx)
            if held and offers:
                picks.append(Pick(f"{SWAP}opt.out", "optional", cls.name, options=tuple(held)))
                picks.append(
                    Pick(
                        f"{SWAP}opt.in",
                        "optional",
                        cls.name,
                        options=tuple(o.reference for o in offers),
                    )
                )
        if cls.edition == "one" and cls.name.lower() == "fighter":
            # 2024 Fighter: "Whenever you gain a Fighter level, you can replace the feat
            # you chose with a different Fighting Style feat."
            styles = self.catalogue.feats(("FS",))
            style_names = {f.name.lower() for f in styles}
            mine = [
                offer.reference
                for f in pc.feats
                if f.name.lower() in style_names
                and (offer := self.catalogue.find("feat", f)) is not None
            ]
            had = {f.name.lower() for f in pc.feats}
            others = [f for f in styles if f.name.lower() not in had]
            for offer in others:
                verdicts[offer.reference] = check(offer.data.get("prerequisite"), ctx)
            if mine and others:
                picks.append(Pick(f"{SWAP}style.out", "feat", cls.name, options=tuple(mine)))
                picks.append(
                    Pick(
                        f"{SWAP}style.in",
                        "feat",
                        cls.name,
                        options=tuple(o.reference for o in others),
                    )
                )
        return picks

    def _subclass_gains(
        self, cls: Offer, subclass: Offer | None, level: int
    ) -> tuple[tuple[str, int], ...]:
        if subclass is None:
            return ()
        owner = subclass_owner(subclass.data, cls.name, cls.source)
        out = []
        for entry in self.catalogue.rows("subclassFeature"):
            data = entry.data
            if files_under(data, owner) and data.get("level") == level:
                out.append((entry.name, level))
        return tuple(out)

    def _context(self, pc: PlayerCharacter, sheet: Sheet) -> Context:
        return Context(
            level=pc.total_level,
            class_levels={c.name.lower(): c.level for c in pc.classes},
            abilities={a.key: a.score.value or 0 for a in sheet.abilities},
            species=pc.species.name if pc.species else "",
            background=pc.background.name if pc.background else "",
            feats=frozenset(f.name.lower() for f in pc.feats),
            features=frozenset(f.name.lower() for f in sheet.features),
            armor=frozenset(a.lower() for a in sheet.armor_proficiencies),
            weapons=frozenset(w.lower() for w in sheet.weapon_proficiencies),
            caster=sheet.is_spellcaster,
            spells=frozenset(s.name.lower() for s in sheet.spells),
        )

    # -- answering and applying --------------------------------------------------------

    @staticmethod
    def answer(pc: PlayerCharacter, pick_id: str, values: Sequence[str]) -> None:
        """Store an in-progress answer; the ASI's feat replaces its two increases."""
        cleaned = [v.strip() for v in values if v and v.strip()]
        feats = [v for v in cleaned if v.startswith("feat:")]
        if pick_id == "asi" and feats:
            cleaned = feats[:1]
        choices = dict(pc.choices)
        if cleaned:
            choices[PREFIX + pick_id] = cleaned
        else:
            choices.pop(PREFIX + pick_id, None)
        pc.choices = choices

    def take(self, pc: PlayerCharacter) -> tuple[str, int, int]:
        """Apply a complete level: the class goes up, the answers become the record.
        Returns the class, the level it reached and the hit points gained, for the log."""
        plan = self.plan(pc)
        if not plan.complete:
            raise LevelUpError("levelup.error.incomplete")
        gained_hp = plan.hp_gain
        answers = _answers(pc)
        self._write(pc, answers, plan.cls, plan.index)
        choices = {k: v for k, v in pc.choices.items() if not k.startswith(PREFIX)}
        for key, values in answers.items():
            choices[f"levels.{plan.character_level}.{key}"] = values
        choices[f"levels.{plan.character_level}.class"] = [plan.cls.reference]
        pc.choices = choices
        pc.pending_levels -= 1
        if pc.state.hp is not None:
            # The new hit points arrive with the level: current HP rises by as much.
            pc.state = pc.state.model_copy(update={"hp": pc.state.hp + max(0, gained_hp)})
        return plan.cls.name, plan.to_level, gained_hp

    @staticmethod
    def _faces(cls: Offer) -> int:
        hd = cls.data.get("hd")
        return int(hd.get("faces", 8)) if isinstance(hd, Mapping) else 8

    def _write(
        self,
        pc: PlayerCharacter,
        answers: Mapping[str, list[str]],
        cls: Offer,
        index: int | None,
    ) -> None:
        """The next level's answers on the character (or on a copy, for the preview).

        ``index`` is the class in ``classes`` that gains the level; None starts ``cls``.
        """
        if not pc.classes:
            return
        taken = pc.total_level
        dice = self._dice(pc)
        chosen_sub = (
            ChosenOption.model_validate(answers["subclass"][0]) if answers.get("subclass") else None
        )
        if index is None:
            pc.classes = [
                *pc.classes,
                ClassLevel(name=cls.name, source=cls.source, level=1, subclass=chosen_sub),
            ]
        else:
            current = pc.classes[index]
            classes = list(pc.classes)
            classes[index] = current.model_copy(
                update={"level": current.level + 1, "subclass": current.subclass or chosen_sub}
            )
            pc.classes = classes
        chosen_options = {
            key.removeprefix("featopt."): values[0]
            for key, values in answers.items()
            if key.startswith("featopt.") and values
        }
        if chosen_options:
            at = index if index is not None else len(pc.classes) - 1
            classes = list(pc.classes)
            classes[at] = classes[at].model_copy(
                update={"options": {**classes[at].options, **chosen_options}}
            )
            pc.classes = classes
        asi = answers.get("asi") or []
        improvements = list(pc.ability_improvements)
        feats = list(pc.feats)
        feat = next((v for v in asi if v.startswith("feat:")), None)
        if feat is not None:
            offer = self.catalogue.find("feat", feat.removeprefix("feat:"))
            if offer is not None:
                feats.append(ChosenOption(name=offer.name, source=offer.source))
                fixed, extra = ability_block(
                    offer.data.get("ability"), offer.name, "asi.feat", answers
                )
                gained = dict(fixed)
                for pick in extra:
                    for ability, n in ability_increases(pick, answers.get(pick.id) or []).items():
                        gained[ability] = gained.get(ability, 0) + n
                if offer.data.get("savingThrowProficiencies"):
                    # Resilient: the save of the ability it raised.
                    pc.saving_throws = list(dict.fromkeys([*pc.saving_throws, *gained]))
                if gained:
                    improvements.append(gained)
        elif asi:
            raised: dict[str, int] = {}
            for ability in asi:
                if ability in ABILITIES:
                    raised[ability] = raised.get(ability, 0) + 1
            if raised:
                improvements.append(raised)
        for key, values in answers.items():
            if key.startswith("featprog."):
                for value in values:
                    offer = self.catalogue.find("feat", value)
                    if offer is not None:
                        feats.append(ChosenOption(name=offer.name, source=offer.source))
        pc.ability_improvements = improvements
        added = feats[len(pc.feats) :]
        added_offers = [o for f in added if (o := self.catalogue.find("feat", f)) is not None]
        pc.feats = feats
        # A new feat's own spells (Magic Initiate): its list, ability and picks.
        level = pc.total_level
        granted = list(pc.spells.granted)
        for offer in added_offers:
            answer = self.planner.granted_answer(
                f"feat:{offer.reference}", offer, None, level, answers
            )
            if answer is not None:
                granted = [g for g in granted if g.source.lower() != answer.source.lower()]
                granted.append(answer)
        pc.spells = pc.spells.model_copy(update={"granted": granted})
        expertise = list(pc.expertise)
        named = {o.name.lower() for o in expertise}
        experts = [
            str(key)
            for f in added
            if (record := self.catalogue.find("feat", f)) is not None
            for block in (record.data.get("expertise") or [])[:1]
            if isinstance(block, Mapping)
            for key, value in block.items()
            if value is True
        ]
        for key, values in answers.items():
            if key.startswith("expertise."):
                experts.extend(values)
        for value in experts:
            if value.lower() not in named:
                named.add(value.lower())
                kind = "tool" if "tool" in value.lower() else "skill"
                expertise.append(ChosenOption(name=self.planner.display(kind, value)))
        pc.expertise = expertise
        options = list(pc.optional_features)
        for key, values in answers.items():
            if key.startswith("opt."):
                options.extend(ChosenOption.model_validate(v) for v in values)
        options = _swapped(options, answers, "opt")
        pc.optional_features = options
        # A new feat's weapons (Weapon Master) and its prose choices (Elemental Adept's
        # damage type), the latter kept in ``choices`` where the sheet reads them.
        weapons = list(pc.weapons)
        for key, values in answers.items():
            if key.startswith("feat.") and key.endswith(".weapons"):
                weapons.extend(ChosenOption.model_validate(v) for v in values)
            elif key.startswith("feat.") and ".choice." in key and values:
                # Added to an earlier taking's (a second Elemental Adept's damage type).
                pc.choices[key] = list(dict.fromkeys([*pc.choices.get(key, []), *values]))
        pc.weapons = list({w.name.lower(): w for w in weapons}.values())
        pc.feats = _swapped(pc.feats, answers, "style")
        spells = pc.spells
        cantrips = _swapped(
            [
                *spells.cantrips,
                *(ChosenOption.model_validate(v) for v in answers.get("spells.cantrips") or []),
            ],
            answers,
            "cantrip",
        )
        known = [
            *spells.known,
            *(ChosenOption.model_validate(v) for v in answers.get("spells.known") or []),
        ]
        prepared = [
            *spells.prepared,
            *(ChosenOption.model_validate(v) for v in answers.get("spells.prepared") or []),
        ]
        out = (answers.get(f"{SWAP}spell.out") or [""])[0]
        if out and _named(out) in {o.name.lower() for o in known}:
            known = _swapped(known, answers, "spell")
        elif out:
            prepared = _swapped(prepared, answers, "spell")
        pc.spells = spells.model_copy(
            update={"cantrips": cantrips, "known": known, "prepared": prepared}
        )
        self._write_proficiencies(pc, self._proficiency_sources(cls, index, added_offers), answers)
        hp = answers.get("hp") or []
        if pc.hit_points.method is HitPointMethod.ROLLED and hp and hp[0].isdigit():
            per_level = list(pc.hit_points.per_level)
            # A level without a result counts as the average (the first as the full
            # die); write those out, so the new roll lands on the new level.
            while len(per_level) < taken:
                faces = dice[len(per_level)] if len(per_level) < len(dice) else 8
                per_level.append(faces if not per_level else faces // 2 + 1)
            pc.hit_points = pc.hit_points.model_copy(
                update={"per_level": [*per_level[:taken], int(hp[0])]}
            )

    def _dice(self, pc: PlayerCharacter) -> list[int]:
        """The hit die of every level taken, in the order the sheet counts them."""
        dice: list[int] = []
        for entry in pc.classes:
            cls = self.catalogue.find("class", ChosenOption(name=entry.name, source=entry.source))
            dice.extend([self._faces(cls) if cls is not None else 8] * entry.level)
        return dice

    def _write_proficiencies(
        self,
        pc: PlayerCharacter,
        sources: Sequence[tuple[ProficiencyKind, str, Any, str, str]],
        answers: Mapping[str, list[str]],
    ) -> None:
        """Granted and chosen proficiencies of those blocks, each once."""
        for kind, attr, block, origin, pick_id in sources:
            if not block:
                continue
            grants, picks = proficiencies(block, kind, origin, pick_id, answers)
            values = [g.value for g in grants]
            for pick in picks:
                values.extend(answers.get(pick.id) or [])
            have = list(getattr(pc, attr))
            names = {o.name.lower() for o in have}
            for value in values:
                if value.lower() not in names:
                    names.add(value.lower())
                    have.append(ChosenOption(name=self.planner.display(kind, value)))
            setattr(pc, attr, have)


def _named(reference: str) -> str:
    return ChosenOption.model_validate(reference).name.lower()


def _swapped(
    options: list[ChosenOption], answers: Mapping[str, list[str]], kind: str
) -> list[ChosenOption]:
    """``options`` with the swap's ``out`` replaced by its ``in``, when both are given."""
    out = (answers.get(f"{SWAP}{kind}.out") or [""])[0]
    into = (answers.get(f"{SWAP}{kind}.in") or [""])[0]
    if not out or not into:
        return options
    replaced = [o for o in options if o.name.lower() != _named(out)]
    if len(replaced) == len(options):
        return options
    return [*replaced, ChosenOption.model_validate(into)]


class LevelUps:
    """Levels granted by the gamemaster and taken by the player, on stored characters."""

    def __init__(self, drafts: Drafts, store: GameStateStore, dice: Dice | None = None) -> None:
        """Bind to the drafts service (for planners and access) and the library."""
        self._drafts = drafts
        self._store = store
        self._dice = dice or Dice()

    def _ready(self, identity: Identity, character_id: str) -> PlayerCharacter:
        pc = self._drafts.characters.get_for(identity, character_id)
        if pc.status is not DraftStatus.READY:
            raise LevelUpError("levelup.error.not_ready")
        return pc

    def planner(self, pc: PlayerCharacter) -> LevelUpPlanner:
        """The level-up planner over the character's world's books."""
        return LevelUpPlanner(self._drafts.planner(pc))

    # -- the gamemaster ------------------------------------------------------------

    def grant(self, identity: Identity, character_id: str, levels: int = 1) -> PlayerCharacter:
        """Let a character go up ``levels`` more (or, negative, take a grant back)."""
        if identity.role is not Role.GAMEMASTER:
            raise LevelUpError("levelup.error.gamemaster")
        pc = self._ready(identity, character_id)
        target = max(0, min(20 - pc.total_level, pc.pending_levels + levels))

        def change(record: PlayerCharacter) -> None:
            record.pending_levels = target
            if target == 0:
                record.choices = {
                    k: v for k, v in record.choices.items() if not k.startswith(PREFIX)
                }

        return self._store.characters.mutate(character_id, change)

    def grant_world(self, identity: Identity, world_id: str | None) -> int:
        """A level for every ready character in a world (or all); returns how many."""
        if identity.role is not Role.GAMEMASTER:
            raise LevelUpError("levelup.error.gamemaster")
        count = 0
        for pc in self._store.characters.list(
            lambda c: c.status is DraftStatus.READY and (world_id is None or c.world_id == world_id)
        ):
            if pc.total_level < 20:
                self.grant(identity, pc.id)
                count += 1
        return count

    # -- the player ----------------------------------------------------------------

    def get(self, identity: Identity, character_id: str) -> tuple[PlayerCharacter, LevelUp]:
        """The character and the level waiting for it."""
        pc = self._ready(identity, character_id)
        return pc, self.planner(pc).plan(pc)

    def save(
        self, identity: Identity, character_id: str, answers: Mapping[str, Sequence[str]]
    ) -> tuple[PlayerCharacter, LevelUp]:
        """Store the answers to this level's questions; return the new plan."""
        pc, before = self.get(identity, character_id)
        planner = self.planner(pc)
        asked = [p.id for p in before.picks]
        world = self._store.worlds.find(pc.world_id) if pc.world_id else None
        fixed: set[str] = set()
        if before.hp_roll is not None and not (world is not None and world.allow_rerolls):
            # The hit die is rolled for this class; another class would roll again.
            fixed.add("class")
            asked = [a for a in asked if a not in fixed]

        def change(record: PlayerCharacter) -> None:
            for pick_id in asked:
                planner.answer(record, pick_id, list(answers.get(pick_id, ())))
            # Questions a new answer brings (a feat's own increase, a new class's
            # skill) are taken too.
            for pick in planner.plan(record).picks:
                if pick.id not in asked and pick.id not in fixed and pick.id in answers:
                    planner.answer(record, pick.id, list(answers[pick.id]))
            # Answers to questions no longer asked — another class's spells — go.
            now = planner.plan(record)
            if now.cls.reference != before.cls.reference:
                planner.answer(record, "hp", [])
                now = planner.plan(record)
            kept = {p.id for p in now.picks} | {"hp"}
            record.choices = {
                k: v
                for k, v in record.choices.items()
                if not k.startswith(PREFIX) or k.removeprefix(PREFIX) in kept
            }

        saved = self._store.characters.mutate(character_id, change)
        return saved, planner.plan(saved)

    def roll_hit_points(self, identity: Identity, character_id: str) -> PlayerCharacter:
        """Roll the new level's hit die: once, unless the world allows rolling again."""
        pc, plan = self.get(identity, character_id)
        world = self._store.worlds.find(pc.world_id) if pc.world_id else None
        if plan.hp_roll is not None and not (world is not None and world.allow_rerolls):
            raise LevelUpError("chargen.error.rolled")
        result = self._dice.die(plan.hit_die)
        planner = self.planner(pc)

        def change(record: PlayerCharacter) -> None:
            planner.answer(record, "hp", [str(result)])

        return self._store.characters.mutate(character_id, change)

    def take(self, identity: Identity, character_id: str) -> PlayerCharacter:
        """Take the level: refused until every question of it is answered."""
        pc = self._ready(identity, character_id)
        planner = self.planner(pc)
        taken: list[tuple[str, int, int]] = []

        def change(record: PlayerCharacter) -> None:
            taken[:] = [planner.take(record)]

        saved = self._store.characters.mutate(character_id, change)
        name, level, gained = taken[0]
        user = identity.user
        self._drafts.characters.record(
            for_character(
                saved,
                EntryKind.LEVEL_UP,
                actor=user.username,
                actor_name=user.display_name or user.username,
                what=f"level:{name}:{level}",
                amount=gained,
            )
        )
        return saved


__all__ = ["PREFIX", "LevelUp", "LevelUpError", "LevelUpPlanner", "LevelUps"]
