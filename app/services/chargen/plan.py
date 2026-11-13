"""The wizard's plan for one character: its steps, their questions, and what is done.

A draft is a :class:`~app.models.pc.PlayerCharacter` whose ``choices`` hold the
answers, keyed by question id (documentation/CHARGEN-WIZARD.md §6). :func:`Planner.plan`
reads the draft and the world's books and says, for every step, which questions it asks
(:class:`~app.services.chargen.blocks.Pick`), what is given outright
(:class:`~app.services.chargen.blocks.Grant`), what is still unanswered, and what an
answer no longer fits — the "needs review" state an earlier change leaves behind.
:func:`Planner.apply` then writes the character's own lists (skills, feats, increases,
spells…) from the answers, so the sheet derives from them as from a hand-made file.

The questions are read from the data, never written per class (rule 4): the species,
class, background and feat blocks go through one walker, and a class's level plan comes
from its ``classFeatures``, ``optionalfeatureProgression``, ``featProgression`` and
spell progressions.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from math import ceil
from typing import Any, Literal

from app.models.base import ABILITIES
from app.models.pc import (
    AbilityMethod,
    ChosenOption,
    ClassLevel,
    Currency,
    GrantedSpells,
    HitPointMethod,
    InventoryItem,
    PlayerCharacter,
    SpellSelection,
)
from app.services import feature_options, granted_spells
from app.services.character import prepared_spells
from app.services.chargen.backstory import Backstory
from app.services.chargen.blocks import (
    ABILITY_LIMIT,
    Grant,
    Pick,
    ability_block,
    ability_increases,
    feat_ability_id,
    feat_block,
    feat_reference,
    feat_saves,
    over_limit,
    proficiencies,
)
from app.services.chargen.catalogue import (
    Catalogue,
    Edition,
    Offer,
    edition_of,
    equipment_kinds,
)
from app.services.chargen.feat_choices import feat_choice_picks, feat_weapons, repeatable
from app.services.chargen.prereq import Context, Verdict, check
from app.services.chargen.rules import ability_problem, expertise_grants, starting_gold
from app.services.class_refs import (
    feature_owners,
    feature_refs,
    files_under,
    is_variant,
    subclass_owner,
)
from app.services.i18n import Translatable
from app.services.naming import StyleEntry
from app.services.sheet import Sheet, build_sheet
from app.services.text import paragraphs, strip_tags

StepId = Literal[
    "species",
    "class",
    "abilities",
    "background",
    "features",
    "skills",
    "equipment",
    "spells",
    "details",
]
ORDERS: dict[Edition, tuple[StepId, ...]] = {
    "classic": (
        "species",
        "class",
        "abilities",
        "background",
        "features",
        "skills",
        "equipment",
        "spells",
        "details",
    ),
    "one": (
        "class",
        "species",
        "background",
        "abilities",
        "features",
        "skills",
        "equipment",
        "spells",
        "details",
    ),
}
# Steps a character may not need at all: no class choices, no spells.
SKIPPABLE: frozenset[StepId] = frozenset({"features", "spells"})
# Steps that ask nothing but are never skipped (equipment may simply be empty).
_ALWAYS: frozenset[StepId] = frozenset({"species", "class", "abilities", "background", "details"})

ASI_FEATURE = "ability score improvement"
# Where a rule stated only in the 2024 Player's Handbook's prose comes from.
RULES_2024 = "XPHB"
# The kinds whose values are references to corpus entries.
_REFERENCE_KINDS = frozenset(
    {"species", "subspecies", "class", "subclass", "background", "feat", "optional", "item"}
)


class Problem(Translatable):
    """Why an answer no longer fits, in the reader's language. ``pick`` names it."""

    def __init__(self, pick: str, key: str, /, **params: Any) -> None:
        """A problem with question ``pick``."""
        super().__init__(key, **params)
        self.pick = pick


def limit_problems(
    picks: Sequence[Pick], answers: Mapping[str, Sequence[str]], scores: Mapping[str, int]
) -> list[Problem]:
    """An increase that takes a score past its limit (``blocks.over_limit``)."""
    return [
        Problem(pick.id, "chargen.problem.ability_cap", cap=pick.limit)
        for pick in over_limit(picks, answers, scores)
    ]


@dataclass(frozen=True)
class Step:
    """One step of the wizard as it stands for this draft."""

    id: StepId
    picks: tuple[Pick, ...] = ()
    grants: tuple[Grant, ...] = ()
    missing: tuple[str, ...] = ()
    problems: tuple[Problem, ...] = ()

    @property
    def state(self) -> Literal["done", "open", "review"]:
        """``review`` when an answer no longer fits; ``open`` while one is owed."""
        if self.problems:
            return "review"
        return "open" if self.missing else "done"

    def pick(self, pick_id: str) -> Pick | None:
        """One of the step's questions."""
        return next((p for p in self.picks if p.id == pick_id), None)


@dataclass(frozen=True)
class SpellPlan:
    """How many spells of each sort a caster chooses, and from up to which level."""

    cantrips: int = 0
    known: int = 0  # a known-spells caster's list, or a spellbook
    prepared: int = 0
    max_level: int = 0
    spellbook: bool = False
    prepares_from_list: bool = False


@dataclass(frozen=True)
class LevelPlan:
    """What a class at a level brings: features, choices owed, spellcasting."""

    level: int
    subclass_level: int | None
    asi_levels: tuple[int, ...]
    features: tuple[tuple[str, int], ...]
    optional: tuple[tuple[str, tuple[str, ...], int], ...]
    feat_progressions: tuple[tuple[str, tuple[str, ...], int], ...]
    spells: SpellPlan


@dataclass(frozen=True)
class Plan:
    """The wizard for one draft: its edition, steps in order, and the level plan."""

    edition: Edition
    steps: tuple[Step, ...]
    level: LevelPlan | None
    species: Offer | None = None
    cls: Offer | None = None
    subclass: Offer | None = None
    background: Offer | None = None
    sheet: Sheet | None = None
    verdicts: Mapping[str, Verdict] = field(default_factory=dict)

    def step(self, step_id: str) -> Step | None:
        """A step by id; None when the draft skips it."""
        return next((s for s in self.steps if s.id == step_id), None)

    @property
    def ids(self) -> tuple[StepId, ...]:
        """The step ids in order."""
        return tuple(s.id for s in self.steps)

    @property
    def first_open(self) -> Step | None:
        """The first step needing review, else the first still open."""
        return next((s for s in self.steps if s.state == "review"), None) or next(
            (s for s in self.steps if s.state == "open"), None
        )

    @property
    def complete(self) -> bool:
        """True when every step is done: the character may be finished."""
        return all(s.state == "done" for s in self.steps)

    def picks(self) -> Iterable[Pick]:
        """Every question across the steps."""
        for step in self.steps:
            yield from step.picks

    def pick(self, pick_id: str) -> Pick | None:
        """One question, wherever it is asked."""
        return next((p for p in self.picks() if p.id == pick_id), None)

    def after(self, step_id: str) -> StepId | None:
        """The step that follows ``step_id``."""
        ids = self.ids
        if step_id not in ids:
            return None
        index = ids.index(step_id)
        return ids[index + 1] if index + 1 < len(ids) else None

    def before(self, step_id: str) -> StepId | None:
        """The step that comes before ``step_id``."""
        ids = self.ids
        if step_id not in ids:
            return None
        index = ids.index(step_id)
        return ids[index - 1] if index > 0 else None


# -- the level plan --------------------------------------------------------------------


def _progression_at(progression: Any, level: int) -> int:
    """A count at ``level`` from a progression: a per-level list or ``{level: total}``."""
    if isinstance(progression, list):
        return int(progression[level - 1]) if 0 < level <= len(progression) else 0
    if isinstance(progression, Mapping):
        reached = [int(k) for k in progression if str(k).isdigit() and int(k) <= level]
        return int(progression[str(max(reached))]) if reached else 0
    return 0


def _features(cls: Offer, level: int) -> list[tuple[str, int]]:
    # A feature from another book than the class is an optional variant.
    return [
        (r.name, r.level)
        for r in feature_refs(cls.data)
        if r.level <= level and not is_variant(r, cls.data)
    ]


def level_plan(cls: Offer, subclass: Offer | None, level: int, sheet: Sheet | None) -> LevelPlan:
    """What ``cls`` (and its ``subclass``) bring at ``level``."""
    features = _features(cls, level)
    asi = tuple(lvl for name, lvl in features if name.lower() == ASI_FEATURE)
    subclass_level = None
    for ref in cls.data.get("classFeatures") or []:
        if isinstance(ref, Mapping) and ref.get("gainSubclassFeature"):
            parts = str(ref.get("classFeature")).split("|")
            if len(parts) >= 4 and parts[3].isdigit():
                subclass_level = int(parts[3])
                break
    optional = []
    for entry in [
        *(cls.data.get("optionalfeatureProgression") or []),
        *((subclass.data.get("optionalfeatureProgression") or []) if subclass else []),
    ]:
        count = _progression_at(entry.get("progression"), level)
        if count:
            optional.append((str(entry.get("name")), tuple(entry.get("featureType") or ()), count))
    feat_progressions = []
    for entry in cls.data.get("featProgression") or []:
        count = _progression_at(entry.get("progression"), level)
        if count:
            feat_progressions.append(
                (str(entry.get("name")), tuple(entry.get("category") or ()), count)
            )
    return LevelPlan(
        level,
        subclass_level,
        asi,
        tuple(features),
        tuple(optional),
        tuple(feat_progressions),
        _spell_plan(cls, subclass, level, sheet),
    )


def evaluate_prepared(formula: str, level: int, modifiers: Mapping[str, int]) -> int | None:
    """A ``preparedSpells`` formula (``"<$level$> + <$int_mod$>"``), at least 1.

    See :func:`app.services.character.prepared_spells`, which the sheet shares.
    """
    return prepared_spells(formula, level, modifiers)


def _spell_plan(cls: Offer, subclass: Offer | None, level: int, sheet: Sheet | None) -> SpellPlan:
    record: Mapping[str, Any] = cls.data
    if not record.get("casterProgression") and subclass and subclass.data.get("casterProgression"):
        record = subclass.data
    if not record.get("casterProgression"):
        return SpellPlan()
    cantrips = _progression_at(record.get("cantripProgression"), level)
    max_level = 0
    if sheet is not None:
        levels = [s.level for s in sheet.slots]
        if sheet.pact is not None:
            levels.append(sheet.pact.level)
        max_level = max(levels, default=0)
    known = _progression_at(record.get("spellsKnownProgression"), level)
    fixed = record.get("spellsKnownProgressionFixed") or []
    spellbook = bool(fixed)
    if spellbook:
        known = sum(int(x) for x in fixed[:level])
    prepared = _progression_at(record.get("preparedSpellsProgression"), level)
    formula = record.get("preparedSpells")
    if not prepared and isinstance(formula, str) and sheet is not None:
        modifiers = {a.key: a.modifier for a in sheet.abilities}
        prepared = evaluate_prepared(formula, level, modifiers) or 0
    return SpellPlan(
        cantrips=cantrips,
        known=known,
        prepared=prepared,
        max_level=max_level,
        spellbook=spellbook,
        prepares_from_list=bool(prepared) and not known,
    )


# -- the planner -----------------------------------------------------------------------

# The chosen entries by role (class, subclass, species, subspecies, background).
Chosen = Mapping[str, Offer | None]
# What a step builder returns: questions, grants, other missing answers, problems.
Built = tuple[list[Pick], list[Grant], list[str], list[Problem]]


# A feature offering a choice, the choice, and its options' records by lower-case name.
FeatureChoice = tuple[Mapping[str, Any], feature_options.OptionBlock, dict[str, Mapping[str, Any]]]


def _summary(record: Mapping[str, Any] | None) -> str:
    """The first sentences of a feature's text, tags stripped, to choose it by."""
    if record is None:
        return ""
    text = strip_tags(" ".join(paragraphs(record.get("entries"), limit=1)))
    return text if len(text) <= 180 else text[:177].rsplit(" ", 1)[0] + "…"


def _spell_list_summary(subclass: Mapping[str, Any], name: str) -> str:
    """A subclass spell list's spells, for choosing it ("Arctic: hold person, spike…")."""
    for block in subclass.get("additionalSpells") or []:
        if isinstance(block, Mapping) and block.get("name") == name:
            spells = [
                str(s).split("|")[0].split("#")[0]
                for kind in ("prepared", "known", "expanded")
                for listed in (block.get(kind) or {}).values()
                for s in (listed if isinstance(listed, list) else [])
                if isinstance(s, str)
            ]
            return ", ".join(spells)
    return ""


def _first(choices: Mapping[str, Sequence[str]], pick_id: str) -> str | None:
    values = choices.get(pick_id) or []
    return values[0] if values else None


class Planner:
    """Plans and applies drafts against one world's books."""

    def __init__(
        self,
        catalogue: Catalogue,
        backstory: Backstory | None = None,
        relatives: Sequence[tuple[str, str]] = (),
        styles: Callable[[], Sequence[StyleEntry]] = tuple,
    ) -> None:
        """Bind to the catalogue of the draft's world, the backstory tables, who a name
        can descend from (``(ref, name)``) and the naming styles (NAMES.md §5)."""
        self.styles = styles
        self.catalogue = catalogue
        self.backstory = backstory
        self.relatives = relatives

    # -- the edition and the chosen entries ----------------------------------------------

    def edition(self, cls: Offer | None) -> Edition:
        """The class's edition; before a class is chosen, the books' (2024 if both)."""
        if cls is not None:
            return cls.edition
        editions = self.catalogue.editions
        return "classic" if editions == {"classic"} else "one"

    def _chosen(self, choices: Mapping[str, Sequence[str]]) -> dict[str, Offer | None]:
        cat = self.catalogue
        cls = cat.find("class", _first(choices, "class"))
        edition = self.edition(cls)
        species = next(
            (o for o in cat.species(edition) if o.matches(_first(choices, "species"))), None
        )
        subclass = None
        if cls is not None:
            subclass = next(
                (o for o in cat.subclasses(cls) if o.matches(_first(choices, "subclass"))), None
            )
        subspecies = None
        if species is not None:
            subspecies = next(
                (o for o in cat.subspecies(species) if o.matches(_first(choices, "subspecies"))),
                None,
            )
        background = next(
            (o for o in cat.backgrounds(edition) if o.matches(_first(choices, "background"))),
            None,
        )
        return {
            "class": cls,
            "subclass": subclass,
            "species": species,
            "subspecies": subspecies,
            "background": background,
        }

    @staticmethod
    def level_of(choices: Mapping[str, Sequence[str]]) -> int:
        """The starting level asked with the class (1 until answered)."""
        raw = _first(choices, "level") or "1"
        return int(raw) if raw.isdigit() and 1 <= int(raw) <= 20 else 1

    # -- planning --------------------------------------------------------------------

    def plan(self, pc: PlayerCharacter) -> Plan:
        """The wizard for ``pc`` as it stands."""
        choices = pc.choices
        chosen = self._chosen(choices)
        cls, subclass = chosen["class"], chosen["subclass"]
        species, background = chosen["species"], chosen["background"]
        edition = self.edition(cls)
        level = self.level_of(choices)

        # The derived sheet of the draft as answered so far: prerequisites, spell
        # counts and the highest spell level read it.
        draft = pc.model_copy(deep=True)
        self.apply(draft, chosen=chosen)
        sheet = build_sheet(draft, self.catalogue.compendium, source_set=self.catalogue.source_set)
        levels = level_plan(cls, subclass, level, sheet) if cls is not None else None
        ctx = self._context(draft, sheet, species, background)

        builders = {
            "species": self._species_step,
            "class": self._class_step,
            "abilities": self._abilities_step,
            "background": self._background_step,
            "features": self._features_step,
            "skills": self._skills_step,
            "equipment": self._equipment_step,
            "spells": self._spells_step,
            "details": self._details_step,
        }
        verdicts: dict[str, Verdict] = {}
        steps = []
        for step_id in ORDERS[edition]:
            picks, grants, extra_missing, problems = builders[step_id](
                pc, chosen, levels, ctx, verdicts
            )
            if step_id in SKIPPABLE and not picks and not extra_missing:
                continue
            problems = [*problems, *limit_problems(picks, choices, ctx.abilities)]
            step = self._judge(step_id, pc, picks, grants, extra_missing, problems, verdicts)
            steps.append(step)
        return Plan(
            edition,
            tuple(steps),
            levels,
            species=species,
            cls=cls,
            subclass=subclass,
            background=background,
            sheet=sheet,
            verdicts=verdicts,
        )

    def _context(
        self,
        pc: PlayerCharacter,
        sheet: Sheet,
        species: Offer | None,
        background: Offer | None,
    ) -> Context:
        feats = {feat_reference(f.reference)[0].lower() for f in pc.feats}
        categories = set()
        for feat in pc.feats:
            found = self.catalogue.find("feat", feat)
            if found and found.data.get("category"):
                categories.add(str(found.data["category"]))
        return Context(
            level=pc.total_level,
            class_levels={c.name.lower(): c.level for c in pc.classes},
            abilities={a.key: a.score.value or 0 for a in sheet.abilities},
            species=species.name if species else "",
            background=background.name if background else "",
            feats=frozenset(feats),
            features=frozenset(f.name.lower() for f in sheet.features),
            armor=frozenset(a.lower() for a in sheet.armor_proficiencies),
            weapons=frozenset(w.lower() for w in sheet.weapon_proficiencies),
            caster=sheet.is_spellcaster,
            spells=frozenset(s.name.lower() for s in sheet.spells),
            feat_categories=frozenset(categories),
        )

    def _species_step(
        self,
        pc: PlayerCharacter,
        chosen: Chosen,
        levels: LevelPlan | None,
        ctx: Context,
        verdicts: dict[str, Verdict],
    ) -> Built:
        cat = self.catalogue
        edition = self.edition(chosen["class"])
        picks = [
            Pick(
                "species",
                "species",
                "species",
                options=tuple(o.reference for o in cat.species(edition)),
            )
        ]
        species = chosen["species"]
        grants: list[Grant] = []
        if species is not None:
            subs = cat.subspecies(species)
            if subs:
                picks.append(
                    Pick(
                        "subspecies",
                        "subspecies",
                        species.name,
                        options=tuple(o.reference for o in subs),
                    )
                )
            lineages = cat.lineages(species)
            if lineages:
                picks.append(Pick("lineage", "lineage", species.name, options=tuple(lineages)))
            for record, prefix in self._species_records(chosen):
                g, p = feat_block(
                    record.data.get("feats"), record.name, f"{prefix}.feat", pc.choices
                )
                grants.extend(g)
                picks.extend(p)
        return picks, grants, [], []

    def physique(self, pc: PlayerCharacter) -> Mapping[str, Any] | None:
        """The ``heightAndWeight`` of the draft's subspecies, else of its species; 2024
        species print none."""
        records = self._species_records(self._chosen(pc.choices))
        found = [r.data.get("heightAndWeight") for r, _ in reversed(records)]
        return next((f for f in found if isinstance(f, Mapping)), None)

    def _species_records(self, chosen: Mapping[str, Offer | None]) -> list[tuple[Offer, str]]:
        out = []
        if chosen["species"] is not None:
            out.append((chosen["species"], "species"))
        if chosen["subspecies"] is not None:
            out.append((chosen["subspecies"], "subspecies"))
        return out

    def _class_step(
        self,
        pc: PlayerCharacter,
        chosen: Chosen,
        levels: LevelPlan | None,
        ctx: Context,
        verdicts: dict[str, Verdict],
    ) -> Built:
        cat = self.catalogue
        picks = [
            Pick("class", "class", "class", options=tuple(o.reference for o in cat.classes())),
            Pick("level", "level", "class", options=tuple(str(n) for n in range(1, 21))),
        ]
        cls = chosen["class"]
        due = levels is not None and levels.subclass_level is not None
        if (
            cls is not None
            and levels is not None
            and due
            and levels.level >= (levels.subclass_level or 0)
        ):
            picks.append(
                Pick(
                    "subclass",
                    "subclass",
                    cls.name,
                    options=tuple(o.reference for o in cat.subclasses(cls)),
                    label=str(cls.data.get("subclassTitle") or ""),
                )
            )
        return picks, [], [], []

    def _abilities_step(
        self,
        pc: PlayerCharacter,
        chosen: Chosen,
        levels: LevelPlan | None,
        ctx: Context,
        verdicts: dict[str, Verdict],
    ) -> Built:
        picks = [
            Pick(
                "abilities.method",
                "ability",
                "abilities",
                options=tuple(m.value for m in AbilityMethod),
                label="method",
            )
        ]
        for record, prefix in self._species_records(chosen):
            _fixed, p = ability_block(
                record.data.get("ability"), record.name, f"{prefix}.ability", pc.choices
            )
            picks.extend(p)
        missing: list[str] = []
        problems: list[Problem] = []
        if not pc.abilities.is_complete:
            missing.append("abilities.scores")
        else:
            answered = _first(pc.choices, "abilities.method")
            method = AbilityMethod(answered) if answered in set(AbilityMethod) else None
            reason = ability_problem(pc, method)
            if reason is not None:
                problems.append(Problem("abilities.scores", reason))
        return picks, [], missing, problems

    def _background_step(
        self,
        pc: PlayerCharacter,
        chosen: Chosen,
        levels: LevelPlan | None,
        ctx: Context,
        verdicts: dict[str, Verdict],
    ) -> Built:
        cat = self.catalogue
        edition = self.edition(chosen["class"])
        picks = [
            Pick(
                "background",
                "background",
                "background",
                options=tuple(o.reference for o in cat.backgrounds(edition)),
            )
        ]
        grants: list[Grant] = []
        background = chosen["background"]
        if background is not None:
            _fixed, p = ability_block(
                background.data.get("ability"), background.name, "background.ability", pc.choices
            )
            picks.extend(p)
            g, p = feat_block(
                background.data.get("feats"), background.name, "background.feat", pc.choices
            )
            grants.extend(g)
            picks.extend(p)
        return picks, grants, [], []

    def _feat_records(
        self, pc: PlayerCharacter, plan_picks: Iterable[Pick], *, repeats: bool = False
    ) -> list[tuple[Offer, str]]:
        """The feats taken so far, each with the id prefix its own questions use; with
        ``repeats``, a repeatable feat taken twice is listed twice."""
        out = []
        seen = set()
        for pick in plan_picks:
            if pick.kind not in ("feat", "asi"):
                continue
            for value in pc.choices.get(pick.id) or []:
                ref = value.removeprefix("feat:")
                found = self.catalogue.find("feat", ref)
                if found and (found.reference not in seen or (repeats and repeatable(found))):
                    seen.add(found.reference)
                    out.append((found, f"feat.{found.reference}"))
        return out

    def _granted_feats(
        self, chosen: Mapping[str, Offer | None], pc: PlayerCharacter
    ) -> list[Offer]:
        out = []
        records = [r for r, _ in self._species_records(chosen)]
        if chosen["background"] is not None:
            records.append(chosen["background"])
        for record in records:
            prefix = (
                "background"
                if record is chosen["background"]
                else ("subspecies" if record is chosen["subspecies"] else "species")
            )
            grants, _ = feat_block(
                record.data.get("feats"), record.name, f"{prefix}.feat", pc.choices
            )
            for grant in grants:
                name, source, _variant = feat_reference(grant.value)
                found = self.catalogue.find("feat", f"{name}|{source}" if source else name)
                if found:
                    out.append(found)
        return out

    def option_picks(self, cls: Offer, subclass: Offer | None, levels: Iterable[int]) -> list[Pick]:
        """Choices between features the class and subclass offer at ``levels``: a Hunter's
        Prey, a Totem Spirit, a Divine Order — and a subclass's spell list where it offers
        several (a Circle of the Land's terrain). The answer is the option's name; each
        option carries its first sentences to choose by."""
        wanted = set(levels)
        picks: list[Pick] = []
        for feature, block, records in self._feature_choices(cls, subclass):
            if int(feature.get("level", 0)) not in wanted:
                continue
            name = str(feature.get("name", ""))
            options = tuple(r.name for r in block.options)
            picks.append(
                Pick(
                    f"featopt.{name}",
                    "featureoption",
                    str(
                        subclass.name if feature.get("subclassShortName") and subclass else cls.name
                    ),
                    count=min(block.count, len(options)),
                    options=options,
                    label=name,
                    details=tuple(_summary(records.get(o.lower())) for o in options),
                )
            )
        lists = [b for b in granted_spells.blocks(subclass.data) if b.name] if subclass else []
        level = (
            next(
                (
                    int(k)
                    for b in (subclass.data.get("additionalSpells") or [])
                    for kind in ("prepared", "known", "expanded", "innate")
                    for k in (b.get(kind) or {})
                    if str(k).isdigit()
                ),
                1,
            )
            if subclass
            else 1
        )
        if subclass is not None and any(lvl >= level for lvl in wanted):
            offers = [
                self.catalogue.find("spell", spell)
                for spell in granted_spells.cantrip_choice(subclass.data)
            ]
            cantrips = [o for o in offers if o is not None]
            if cantrips:
                picks.append(
                    Pick(
                        "featopt.cantrip",
                        "featureoption",
                        subclass.name,
                        options=tuple(o.name for o in cantrips),
                        label=subclass.name,
                        details=tuple(_summary(o.data) for o in cantrips),
                    )
                )
        if subclass is not None and len(lists) > 1 and any(lvl >= level for lvl in wanted):
            picks.append(
                Pick(
                    "featopt.spells",
                    "featureoption",
                    subclass.name,
                    options=tuple(b.name for b in lists),
                    label=subclass.name,
                    details=tuple(_spell_list_summary(subclass.data, b.name) for b in lists),
                )
            )
        return picks

    def _feature_choices(self, cls: Offer, subclass: Offer | None) -> list[FeatureChoice]:
        """Every counted choice in the class's and subclass's features, with the option
        records by lower-case name."""
        cat = self.catalogue
        # Filed under the classes the references name: a class on another's frame
        # borrows its features (class_refs). Choices come from the features it lists.
        owners = feature_owners(cls.data)
        refs = feature_refs(cls.data)
        own = [
            o.data
            for o in cat.rows("classFeature")
            if (
                str(o.data.get("className", "")).lower(),
                str(o.data.get("classSource", "")).lower(),
            )
            in owners
        ]
        listed = [f for f in own if any(r.matches(f) for r in refs)]
        if subclass is not None:
            owner = subclass_owner(subclass.data, cls.name, cls.source)
            sub_own = [o.data for o in cat.rows("subclassFeature") if files_under(o.data, owner)]
            own += sub_own
            listed += sub_own
        out = []
        for feature in listed:
            for block in feature_options.option_blocks(feature.get("entries")):
                records = {
                    str(r.get("name", "")).lower(): r
                    for r in own
                    for ref in block.options
                    if str(r.get("name", "")).lower() == ref.name.lower()
                    and r.get("level") == ref.level
                }
                out.append((feature, block, records))
        return sorted(out, key=lambda x: int(x[0].get("level", 0)))

    def _spell_sources(
        self, pc: PlayerCharacter, chosen: Chosen, feat_picks: Iterable[Pick]
    ) -> list[tuple[str, Offer, str | None]]:
        """Every record whose ``additionalSpells`` the character gets, as ``(key, record,
        variant)``: the species and its lineage, the background, each feat — a feat a
        background grants as a version (``magic initiate; cleric|xphb``) with that
        version, which fixes its list."""
        out: list[tuple[str, Offer, str | None]] = []
        records = self._species_records(chosen)
        for record, prefix in records:
            lineage = _first(pc.choices, "lineage") if prefix == "species" else None
            out.append((prefix, record, lineage or None))
        if chosen["background"] is not None:
            records = [*records, (chosen["background"], "background")]
            out.append(("background", chosen["background"], None))
        for record, prefix in records:
            grants, _ = feat_block(
                record.data.get("feats"), record.name, f"{prefix}.feat", pc.choices
            )
            for grant in grants:
                name, source, variant = feat_reference(grant.value)
                found = self.catalogue.find("feat", f"{name}|{source}" if source else name)
                if found is not None:
                    out.append((f"feat:{found.reference}", found, variant))
        out.extend((f"feat:{f.reference}", f, None) for f, _ in self._feat_records(pc, feat_picks))
        seen: set[str] = set()
        unique = []
        for key, record, variant in out:
            if key not in seen and granted_spells.blocks(record.data):
                seen.add(key)
                unique.append((key, record, variant))
        return unique

    def _granted_picks(
        self, pc: PlayerCharacter, chosen: Chosen, level: int, feat_picks: Iterable[Pick]
    ) -> list[Pick]:
        """The questions spells granted by a species, background or feat ask."""
        return [
            pick
            for key, record, variant in self._spell_sources(pc, chosen, feat_picks)
            for pick in self.granted_questions(key, record, variant, level, pc.choices)
        ]

    def granted_questions(
        self,
        key: str,
        record: Offer,
        variant: str | None,
        level: int,
        answers: Mapping[str, Sequence[str]],
    ) -> list[Pick]:
        """What one record's granted spells ask by character ``level``: which list
        (where it offers several), which ability (where the player picks), which spells.
        Ids start ``granted.<key>.``; a spell question names its list, so choosing another
        list starts afresh."""
        found = granted_spells.blocks(record.data)
        if not found:  # a feat or species that grants no spells asks nothing
            return []
        picks: list[Pick] = []
        block = self._granted_block(key, record, variant, answers)
        if granted_spells.chosen_block(found, variant) is None:
            picks.append(
                Pick(
                    f"granted.{key}.list",
                    "variant",
                    record.name,
                    options=tuple(str(i) for i in range(len(found))),
                    details=tuple(b.name for b in found),
                )
            )
        if block is None:
            return picks
        if block.ability_options:
            picks.append(
                Pick(
                    f"granted.{key}.ability", "casting", record.name, options=block.ability_options
                )
            )
        for index, line in block.choices(level):
            assert line.choice is not None
            options = self._granted_options(line.choice, record)
            picks.append(
                Pick(
                    f"granted.{key}.{block.name}.{index}",
                    "cantrip" if line.choice.cantrips_only else "spell",
                    record.name,
                    count=min(line.count, len(options)) or line.count,
                    options=options,
                    label=line.use,
                )
            )
        return picks

    @staticmethod
    def _granted_block(
        key: str, record: Offer, variant: str | None, answers: Mapping[str, Sequence[str]]
    ) -> granted_spells.Block | None:
        """The block a record's grant uses: fixed, or the list answered."""
        found = granted_spells.blocks(record.data)
        block = granted_spells.chosen_block(found, variant)
        if block is not None:
            return block
        answer = (answers.get(f"granted.{key}.list") or [""])[0]
        index = int(answer) if answer.isdigit() else -1
        return found[index] if 0 <= index < len(found) else None

    def granted_answer(
        self,
        key: str,
        record: Offer,
        variant: str | None,
        level: int,
        answers: Mapping[str, Sequence[str]],
    ) -> GrantedSpells | None:
        """The answers to :meth:`granted_questions` as the character file keeps them."""
        block = self._granted_block(key, record, variant, answers)
        if block is None:
            return None
        found = granted_spells.blocks(record.data)
        spells = [
            ChosenOption.model_validate(v)
            for index, _line in block.choices(level)
            for v in answers.get(f"granted.{key}.{block.name}.{index}") or []
        ]
        cast_with = (answers.get(f"granted.{key}.ability") or [""])[0]
        if not (spells or cast_with or len(found) > 1):
            return None
        return GrantedSpells(
            source=key,
            from_list=block.name if len(found) > 1 else None,
            ability=cast_with if cast_with in block.ability_options else None,
            spells=spells,
        )

    def _granted_options(
        self, choice: granted_spells.SpellFilter, record: Offer
    ) -> tuple[str, ...]:
        """The spells a choice offers in the world's books: on the named class's list in
        the record's own rules (a 2024 feat, the 2024 lists), else on any."""
        lookup = self.catalogue.lookups.spell_sources
        book = "XPHB" if edition_of(record.data) == "one" else "PHB"

        def admitted(source: str | None) -> tuple[str, ...]:
            out = []
            for spell in self.catalogue.rows("spell"):
                lists = lookup.classes_for(spell.name, spell.source)
                names = {
                    n.lower()
                    for src, members in lists.items()
                    if source is None or src == source
                    for n in members
                }
                if choice.admits(spell.data, lambda c, names=names: c.lower() in names):
                    out.append(spell.reference)
            return tuple(out)

        return admitted(book) or admitted(None) if choice.classes else admitted(None)

    def _features_step(
        self,
        pc: PlayerCharacter,
        chosen: Chosen,
        levels: LevelPlan | None,
        ctx: Context,
        verdicts: dict[str, Verdict],
    ) -> Built:
        cat = self.catalogue
        picks: list[Pick] = []
        missing: list[str] = []
        edition = self.edition(chosen["class"])
        if levels is None:
            return picks, [], missing, []
        general = (
            [f for f in cat.feats(("G",)) if f.name.lower() != ASI_FEATURE]
            if edition == "one"
            else [f for f in cat.feats() if f.data.get("category") in (None, "G")]
        )
        for lvl in levels.asi_levels:
            picks.append(
                Pick(
                    f"asi.{lvl}",
                    "asi",
                    f"level {lvl}",
                    count=2,
                    options=(*ABILITIES, *(f"feat:{f.reference}" for f in general)),
                    label=str(lvl),
                    limit=ABILITY_LIMIT,
                )
            )
        for name, categories, count in levels.feat_progressions:
            feats = cat.feats(categories)
            picks.append(
                Pick(
                    f"featprog.{name}",
                    "feat",
                    name,
                    count=count,
                    options=tuple(f.reference for f in feats),
                    label=name,
                )
            )
        for name, types, count in levels.optional:
            options = cat.optional_features(types)
            picks.append(
                Pick(
                    f"opt.{name}",
                    "optional",
                    name,
                    count=count,
                    options=tuple(o.reference for o in options),
                    label=name,
                )
            )
            for option in options:
                verdicts[option.reference] = check(option.data.get("prerequisite"), ctx)
        # Questions of the feats taken: the species' and background's, and those above.
        feat_sources = [
            *(self._granted_feats(chosen, pc)),
            *(f for f, _ in self._feat_records(pc, picks)),
        ]
        background_picks, *_ = self._background_step(pc, chosen, levels, ctx, verdicts)
        species_picks, *_ = self._species_step(pc, chosen, levels, ctx, verdicts)
        feat_sources.extend(
            f for f, _ in self._feat_records(pc, [*background_picks, *species_picks])
        )
        # A repeatable feat taken twice asks its questions twice (Elemental Adept).
        once: set[str] = set()
        again = []
        for feat, _prefix in self._feat_records(pc, picks, repeats=True):
            if feat.reference in once:
                again.append(feat)
            once.add(feat.reference)
        firsts = list({f.reference: f for f in feat_sources}.values())
        takings: Counter[str] = Counter()
        for feat in [*firsts, *again]:  # each feat once, then each later taking
            takings[feat.reference] += 1
            taking = takings[feat.reference]
            _fixed, p = ability_block(
                feat.data.get("ability"),
                feat.name if taking == 1 else f"{feat.name} ({taking})",
                feat_ability_id(feat.reference, taking),
                pc.choices,
                ABILITY_LIMIT,
            )
            picks.extend(p)
        # What else those feats ask: maneuvers, weapons, a damage type (feat_choices.py).
        picks.extend(feat_choice_picks([*feat_sources, *again], cat, ctx, verdicts))
        for offer in [*general, *cat.feats(("O", "FS", "EB"))]:
            verdicts.setdefault(offer.reference, check(offer.data.get("prerequisite"), ctx))
        if chosen["class"] is not None:
            picks.extend(
                self.option_picks(chosen["class"], chosen["subclass"], range(1, levels.level + 1))
            )
        if levels.level > 1:
            picks.append(Pick("hp", "hp", "class", options=tuple(m.value for m in HitPointMethod)))
            rolled = _first(pc.choices, "hp") == HitPointMethod.ROLLED.value
            if rolled and len(pc.hit_points.per_level) < levels.level:
                missing.append("hp.rolls")
        return picks, [], missing, []

    def _skills_step(
        self,
        pc: PlayerCharacter,
        chosen: Chosen,
        levels: LevelPlan | None,
        ctx: Context,
        verdicts: dict[str, Verdict],
    ) -> Built:
        picks: list[Pick] = []
        grants: list[Grant] = []
        sources: list[tuple[Mapping[str, Any], str, str]] = []
        cls = chosen["class"]
        if self.edition(cls) == "one":
            # 2024: every character knows Common and two standard languages. The rule
            # is prose in the Player's Handbook, on no species or background record.
            grants.append(Grant("language", "common", RULES_2024))
            picks.append(Pick("rules.languages", "language", RULES_2024, count=2, pool="standard"))
        if cls is not None:
            starting = cls.data.get("startingProficiencies") or {}
            sources.append(
                (
                    {
                        "skillProficiencies": starting.get("skills"),
                        "toolProficiencies": starting.get("toolProficiencies"),
                    },
                    cls.name,
                    "class",
                )
            )
        for record, prefix in self._species_records(chosen):
            sources.append((record.data, record.name, prefix))
        if chosen["background"] is not None:
            sources.append((chosen["background"].data, chosen["background"].name, "background"))
        feat_picks: list[Pick] = []
        for builder in (self._species_step, self._background_step, self._features_step):
            built, *_ = builder(pc, chosen, levels, ctx, {})
            feat_picks.extend(built)
        feats = [
            *self._granted_feats(chosen, pc),
            *(f for f, _ in self._feat_records(pc, feat_picks)),
        ]
        for feat in {f.reference: f for f in feats}.values():
            sources.append((feat.data, feat.name, f"feat.{feat.reference}"))
        for data, origin, prefix in sources:
            for key, kind in (
                ("skillProficiencies", "skill"),
                ("toolProficiencies", "tool"),
                ("languageProficiencies", "language"),
            ):
                block = data.get(key)
                if not block:
                    continue
                g, p = proficiencies(block, kind, origin, f"{prefix}.{kind}s", pc.choices)  # type: ignore[arg-type]
                grants.extend(g)
                picks.extend(p)

        # Expertise, from the proficiencies as answered so far (CHARGEN-WIZARD.md §11).
        held: dict[str, set[str]] = {"skill": set(), "tool": set()}
        for grant in grants:
            if grant.kind in held:
                held[grant.kind].add(grant.value.lower())
        for pick in picks:
            if pick.kind in held:
                held[pick.kind].update(v.lower() for v in pc.choices.get(pick.id) or [])
        level = levels.level if levels is not None else 0
        expert, fixed = self.expertise_picks(
            cls,
            range(1, level + 1),
            list({f.reference: f for f in feats}.values()),
            held["skill"],
            held["tool"],
            pc.choices,
        )
        picks.extend(expert)
        grants.extend(fixed)
        return picks, grants, [], []

    def expertise_picks(
        self,
        cls: Offer | None,
        levels: range,
        feats: Sequence[Offer],
        skills: Collection[str],
        tools: Collection[str],
        answers: Mapping[str, Sequence[str]],
        had: Collection[str] = (),
    ) -> tuple[list[Pick], list[Grant]]:
        """The Expertise the class's features at ``levels`` and ``feats`` ask for.

        A class feature's grant is the rules file's (``expertise.json``: the corpus has it
        only as prose); a feat's is its own ``expertise`` field — ``anyProficientSkill``
        to choose, or a named skill given outright. The options are the proficiencies
        held (``skills``, ``tools``, lower case), less any Expertise already held or
        chosen by an earlier question here.
        """
        picks: list[Pick] = []
        fixed: list[Grant] = []
        taken = {h.lower() for h in had}

        def options(only: Sequence[str] = (), tool_options: Sequence[str] = ()) -> tuple[str, ...]:
            listed = sorted(s for s in skills if (not only or s in only) and s not in taken)
            listed += [t for t in tool_options if t in tools and t not in taken]
            return tuple(listed)

        def add(pick: Pick) -> None:
            # Never more than can be chosen: a question that cannot be finished blocks.
            if not pick.options:
                return
            picks.append(replace(pick, count=min(pick.count, len(pick.options))))
            taken.update(v.lower() for v in answers.get(pick.id) or [])

        if cls is not None:
            for row in expertise_grants(cls.name, cls.source):
                if row.level in levels:
                    add(
                        Pick(
                            f"expertise.{cls.name.lower()}.{row.level}",
                            "expertise",
                            cls.name,
                            count=row.count,
                            options=options(row.skills, row.tools),
                            label=f"{cls.name} {row.level}",
                        )
                    )
        for feat in feats:
            blocks = feat.data.get("expertise") or []
            block = blocks[0] if blocks and isinstance(blocks[0], Mapping) else {}
            for key, value in block.items():
                if key == "anyProficientSkill" and isinstance(value, int):
                    add(
                        Pick(
                            f"expertise.feat.{feat.reference.lower()}",
                            "expertise",
                            feat.name,
                            count=value,
                            options=options(),
                            label=feat.name,
                        )
                    )
                elif value is True:
                    fixed.append(Grant("expertise", key.lower(), feat.name))
                    taken.add(key.lower())
        return picks, fixed

    def _equipment_step(
        self,
        pc: PlayerCharacter,
        chosen: Chosen,
        levels: LevelPlan | None,
        ctx: Context,
        verdicts: dict[str, Verdict],
    ) -> Built:
        cls = chosen["class"]
        gold = starting_gold(cls.data) if cls is not None else None
        picks: list[Pick] = [
            Pick(
                "equipment",
                "equipment",
                "equipment",
                options=("packages", GOLD) if gold is not None else ("packages",),
            )
        ]
        grants: list[Grant] = []
        missing: list[str] = []
        problems: list[Problem] = []
        way = _first(pc.choices, "equipment")
        by_gold = way == GOLD and gold is not None
        if by_gold and self.gold(pc, cls) is None:
            missing.append(GOLD_ROLL)
        if way is not None:
            start, spent = self.purse(pc, chosen)
            if spent > start:
                problems.append(Problem("equipment", "chargen.problem.overspent"))
        for record, prefix in (
            (chosen["class"], "equipment.class"),
            (chosen["background"], "equipment.background"),
        ):
            if record is None or by_gold:
                continue
            raw = record.data.get("startingEquipment")
            groups = raw.get("defaultData") if isinstance(raw, Mapping) else raw
            for index, group in enumerate(groups or []):
                if not isinstance(group, Mapping):
                    continue
                keys = [k for k in group if k != "_"]
                pick_id = f"{prefix}.{index}"
                if len(keys) > 1:
                    picks.append(
                        Pick(
                            pick_id,
                            "equipment",
                            record.name,
                            options=tuple(k.lower() for k in keys),
                        )
                    )
                taken = ["_"] if "_" in group else []
                answer = _first(pc.choices, pick_id)
                if len(keys) == 1:
                    taken.append(keys[0])
                elif answer is not None:
                    taken.extend(k for k in keys if k.lower() == answer)
                for key in taken:
                    for j, entry in enumerate(group[key]):
                        kinds = equipment_kinds(entry) if isinstance(entry, Mapping) else ()
                        if kinds:
                            options = [o for k in kinds for o in self.catalogue.equipment(k)]
                            if not options:
                                # An item kind this catalogue cannot list must not
                                # leave a question nobody can answer.
                                continue
                            picks.append(
                                Pick(
                                    f"{pick_id}.{key.lower()}.{j}",
                                    "item",
                                    record.name,
                                    count=int(entry.get("quantity", 1)),
                                    options=tuple(o.reference for o in options),
                                    label=" ".join(kinds),
                                )
                            )
        return picks, grants, missing, problems

    def _spells_step(
        self,
        pc: PlayerCharacter,
        chosen: Chosen,
        levels: LevelPlan | None,
        ctx: Context,
        verdicts: dict[str, Verdict],
    ) -> Built:
        picks: list[Pick] = []
        cls = chosen["class"]
        if cls is None or levels is None:
            return picks, [], [], []
        feat_picks: list[Pick] = []
        for builder in (self._species_step, self._background_step, self._features_step):
            built, *_ = builder(pc, chosen, levels, ctx, {})
            feat_picks.extend(built)
        granted = self._granted_picks(pc, chosen, levels.level, feat_picks)
        plan = levels.spells
        if plan.cantrips:
            cantrips = self.catalogue.class_spells(cls, 0, chosen["subclass"])
            picks.append(
                Pick(
                    "spells.cantrips",
                    "cantrip",
                    cls.name,
                    count=plan.cantrips,
                    options=tuple(o.reference for o in cantrips),
                )
            )
        listed = tuple(
            o.reference
            for o in self.catalogue.class_spells(cls, plan.max_level, chosen["subclass"])
            if o.data.get("level", 0) >= 1
        )
        if plan.known:
            picks.append(Pick("spells.known", "spell", cls.name, count=plan.known, options=listed))
        if plan.prepared:
            book = tuple(pc.choices.get("spells.known") or ()) if plan.known else listed
            count = min(plan.prepared, len(book)) if plan.known and book else plan.prepared
            picks.append(Pick("spells.prepared", "prepared", cls.name, count=count, options=book))
        return [*picks, *granted], [], [], []

    def _details_step(
        self,
        pc: PlayerCharacter,
        chosen: Chosen,
        levels: LevelPlan | None,
        ctx: Context,
        verdicts: dict[str, Verdict],
    ) -> Built:
        return [], [], ([] if pc.name.strip() else ["name"]), []

    # -- judging answers -----------------------------------------------------------------

    def _judge(
        self,
        step_id: StepId,
        pc: PlayerCharacter,
        picks: list[Pick],
        grants: list[Grant],
        extra_missing: list[str],
        extra_problems: list[Problem],
        verdicts: Mapping[str, Verdict],
    ) -> Step:
        missing = list(extra_missing)
        problems = list(extra_problems)
        taken: dict[tuple[str, str], str] = {}
        for grant in grants:
            if grant.kind in ("skill", "tool", "language"):
                taken.setdefault((grant.kind, grant.value.lower()), grant.origin)
        for pick in picks:
            values = list(pc.choices.get(pick.id) or [])
            if not values:
                missing.append(pick.id)
                continue
            if self.partial(pick, values):
                missing.append(pick.id)
                continue
            problem = self.check_answer(pick, values, verdicts)
            if problem is not None:
                problems.append(problem)
                continue
            if pick.kind in ("skill", "tool", "language"):
                for value in values:
                    key = (pick.kind, value.lower())
                    if key in taken:
                        problems.append(
                            Problem(
                                pick.id, "chargen.problem.duplicate", value=value, origin=taken[key]
                            )
                        )
                        break
                    taken[key] = pick.origin
        return Step(step_id, tuple(picks), tuple(grants), tuple(missing), tuple(problems))

    @staticmethod
    def partial(pick: Pick, values: Sequence[str]) -> bool:
        """An answer still being given: fewer values than asked, or an empty slot.

        Unfinished is open, not wrong — the step must not flag a problem while the
        player is halfway through choosing two skills.
        """
        if pick.kind == "asi":
            return len(values) == 1 and not values[0].startswith("feat:")
        if pick.weights:
            return "" in values or len(values) < pick.count
        return len(values) < pick.count

    def check_answer(
        self, pick: Pick, values: Sequence[str], verdicts: Mapping[str, Verdict]
    ) -> Problem | None:
        """Why a complete answer does not fit its question, or None."""
        if pick.kind == "asi":
            return self._check_asi(pick, values, verdicts)
        if len(values) != pick.count:
            return Problem(pick.id, "chargen.problem.count", n=pick.count)
        if pick.kind not in ("item", "ability") and len({v.lower() for v in values}) != len(values):
            return Problem(pick.id, "chargen.problem.twice")
        if pick.weights and len(set(values)) != len(values):
            return Problem(pick.id, "chargen.problem.twice")
        allowed = self._allowed(pick)
        for value in values:
            if not self._fits(pick, value, allowed):
                return Problem(pick.id, "chargen.problem.invalid", value=value)
            verdict = verdicts.get(value)
            if verdict is not None and not verdict.allowed:
                return Problem(pick.id, "chargen.problem.prerequisite", value=value)
        return None

    def _check_asi(
        self, pick: Pick, values: Sequence[str], verdicts: Mapping[str, Verdict]
    ) -> Problem | None:
        feats = [v for v in values if v.startswith("feat:")]
        if feats:
            if len(values) != 1:
                return Problem(pick.id, "chargen.problem.asi")
            if feats[0] not in pick.options:
                return Problem(
                    pick.id, "chargen.problem.invalid", value=feats[0].removeprefix("feat:")
                )
            verdict = verdicts.get(feats[0].removeprefix("feat:"))
            if verdict is not None and not verdict.allowed:
                return Problem(pick.id, "chargen.problem.prerequisite", value=feats[0])
            return None
        if len(values) != 2 or any(v not in ABILITIES for v in values):
            return Problem(pick.id, "chargen.problem.asi")
        return None

    def _allowed(self, pick: Pick) -> frozenset[str]:
        """Every value a pick accepts, lowercased."""
        if pick.options:
            return frozenset(o.lower() for o in pick.options)
        cat = self.catalogue
        if pick.kind == "skill":
            return frozenset(cat.skills)
        if pick.kind == "language":
            pool = pick.pool if pick.pool in ("standard", "exotic") else "any"
            return frozenset(cat.languages(pool))  # type: ignore[arg-type]
        if pick.kind == "tool":
            return frozenset(cat.tools(pick.pool or "any"))
        if pick.kind == "feat":
            categories = [c for c in pick.pool.split(",") if c] or None
            return frozenset(f.reference.lower() for f in cat.feats(categories))
        return frozenset()

    @staticmethod
    def _fits(pick: Pick, value: str, allowed: frozenset[str]) -> bool:
        lowered = value.lower()
        if lowered in allowed:
            return True
        if pick.kind in _REFERENCE_KINDS and "|" not in lowered:
            # A bare name matches a single offer of that name.
            matches = [a for a in allowed if a.split("|")[0] == lowered]
            return len(matches) == 1
        return False

    # -- answering -------------------------------------------------------------------

    def options(self, pick: Pick) -> list[str]:
        """What a pick may be answered with (resolving "any" pools to lists)."""
        if pick.options:
            return list(pick.options)
        return sorted(self._allowed(pick))

    @staticmethod
    def answer(pc: PlayerCharacter, pick_id: str, values: Sequence[str]) -> None:
        """Store an answer (an empty list clears it)."""
        cleaned = [v.strip() for v in values if v and v.strip()]
        choices = dict(pc.choices)
        if cleaned:
            choices[pick_id] = cleaned
        else:
            choices.pop(pick_id, None)
        pc.choices = choices

    # -- applying --------------------------------------------------------------------

    def apply(
        self, pc: PlayerCharacter, *, chosen: Mapping[str, Offer | None] | None = None
    ) -> None:
        """Write the character's lists from its answers; untouched where unanswered."""
        choices = pc.choices
        chosen = chosen if chosen is not None else self._chosen(choices)
        cls, subclass = chosen["class"], chosen["subclass"]
        species, subspecies = chosen["species"], chosen["subspecies"]
        background = chosen["background"]
        level = self.level_of(choices)

        pc.species = _option(species)
        pc.subspecies = _option(subspecies)
        pc.background = _option(background)
        options = {
            key.removeprefix("featopt."): values[0]
            for key, values in choices.items()
            if key.startswith("featopt.") and values
        }
        pc.classes = (
            [
                ClassLevel(
                    name=cls.name,
                    source=cls.source,
                    level=level,
                    subclass=_option(subclass),
                    options=options,
                )
            ]
            if cls is not None
            else []
        )
        method = _first(choices, "abilities.method")
        if method in {m.value for m in AbilityMethod}:
            pc.ability_method = AbilityMethod(method)
        hp = _first(choices, "hp")
        if hp in {m.value for m in HitPointMethod}:
            pc.hit_points = pc.hit_points.model_copy(update={"method": HitPointMethod(hp)})

        # Structure only, without sheets: the questions apply needs do not depend on
        # derived numbers.
        empty: dict[str, Verdict] = {}
        ctx = Context()
        levels = level_plan(cls, subclass, level, None) if cls is not None else None
        species_picks, _, _, _ = self._species_step(pc, chosen, levels, ctx, empty)
        background_picks, _, _, _ = self._background_step(pc, chosen, levels, ctx, empty)
        ability_picks, _, _, _ = self._abilities_step(pc, chosen, levels, ctx, empty)
        feature_picks, _, _, _ = self._features_step(pc, chosen, levels, ctx, empty)
        skill_picks, skill_grants, _, _ = self._skills_step(pc, chosen, levels, ctx, empty)

        # Proficiencies: granted plus chosen, each once, in the corpus's spelling.
        for kind, attr in (("skill", "skills"), ("tool", "tools"), ("language", "languages")):
            values: dict[str, str] = {}
            for grant in skill_grants:
                if grant.kind == kind:
                    values.setdefault(grant.value.lower(), grant.value)
            for pick in skill_picks:
                if pick.kind == kind:
                    for value in choices.get(pick.id) or []:
                        values.setdefault(value.lower(), value)
            setattr(pc, attr, [ChosenOption(name=self.display(kind, v)) for v in values.values()])
        expertise: dict[str, str] = {}
        for grant in skill_grants:
            if grant.kind == "expertise":
                expertise.setdefault(grant.value.lower(), grant.value)
        for pick in skill_picks:
            if pick.kind == "expertise":
                for value in choices.get(pick.id) or []:
                    expertise.setdefault(value.lower(), value)
        pc.expertise = [
            ChosenOption(name=self.display("tool" if "tool" in v else "skill", v))
            for v in expertise.values()
        ]

        # Feats: granted by species and background, chosen anywhere.
        feats: dict[str, ChosenOption] = {}
        for offer in self._granted_feats(chosen, pc):
            feats.setdefault(
                offer.reference.lower(), ChosenOption(name=offer.name, source=offer.source)
            )
        again: list[ChosenOption] = []  # a repeatable feat taken a second time
        for pick in [*species_picks, *background_picks, *feature_picks]:
            if pick.kind not in ("feat", "asi"):
                continue
            for value in choices.get(pick.id) or []:
                if pick.kind == "asi" and not value.startswith("feat:"):
                    continue
                found = self.catalogue.find("feat", value.removeprefix("feat:"))
                if found is None:
                    continue
                option = ChosenOption(name=found.name, source=found.source)
                if found.reference.lower() in feats and repeatable(found):
                    again.append(option)
                feats.setdefault(found.reference.lower(), option)
        pc.feats = [*feats.values(), *again]

        held = {
            ChosenOption.model_validate(v).name.lower(): ChosenOption.model_validate(v)
            for pick in feature_picks
            if pick.kind == "optional"
            for v in choices.get(pick.id) or []
        }
        pc.optional_features = list(held.values())
        pc.weapons = [ChosenOption.model_validate(v) for v in feat_weapons(feature_picks, choices)]

        # Ability increases, one entry per occasion, in plan order.
        improvements: list[dict[str, int]] = []
        for pick in [*ability_picks, *background_picks, *feature_picks]:
            answered = choices.get(pick.id) or []
            if pick.kind == "ability" and pick.id != "abilities.method" and answered:
                gained = ability_increases(pick, answered)
                if gained:
                    improvements.append(gained)
            elif (
                pick.kind == "asi" and answered and not any(v.startswith("feat:") for v in answered)
            ):
                raised: dict[str, int] = {}
                for ability in answered:
                    if ability in ABILITIES:
                        raised[ability] = raised.get(ability, 0) + 1
                if raised:
                    improvements.append(raised)
        takings: Counter[str] = Counter()
        for feat in pc.feats:
            record = self.catalogue.find("feat", feat)
            if record is None:
                continue
            takings[record.reference] += 1
            fixed, _ = ability_block(
                record.data.get("ability"),
                record.name,
                feat_ability_id(record.reference, takings[record.reference]),
                choices,
            )
            if fixed:
                improvements.append(dict(fixed))
        pc.ability_improvements = improvements
        pc.saving_throws = feat_saves(pc.feats, self.catalogue, feature_picks, choices)

        # Spells, as chosen.
        cantrips = [ChosenOption.model_validate(v) for v in choices.get("spells.cantrips") or []]
        known = [ChosenOption.model_validate(v) for v in choices.get("spells.known") or []]
        prepared = [ChosenOption.model_validate(v) for v in choices.get("spells.prepared") or []]
        if known:
            names = {k.name.lower() for k in known}
            prepared = [p for p in prepared if p.name.lower() in names]
        level_now = levels.level if levels is not None else 1
        feat_picks = [*species_picks, *background_picks, *feature_picks]
        granted = [
            answer
            for key, record, variant in self._spell_sources(pc, chosen, feat_picks)
            for answer in [self.granted_answer(key, record, variant, level_now, choices)]
            if answer is not None
        ]
        pc.spells = SpellSelection(
            cantrips=cantrips, known=known, prepared=prepared, granted=granted
        )

        # Equipment, once the player has chosen packages or gold, and what the shop sold.
        way = _first(choices, "equipment")
        if way in ("packages", GOLD):
            items, copper = (
                self._equipment(pc, chosen) if way == "packages" else ([], self.gold(pc, cls) or 0)
            )
            bought = [self._item(reference, 1) for reference in choices.get(SHOP) or []]
            pc.inventory = self._worn_once(_merge([*items, *bought]))
            pc.currency = _coins(max(copper - self._spent(pc), 0))

    def display(self, kind: str, value: str) -> str:
        """A proficiency in the corpus's own capitalisation."""
        category = {"skill": "skill", "language": "language"}.get(kind)
        if category:
            row = next(
                (o for o in self.catalogue.rows(category) if o.name.lower() == value.lower()), None
            )
            if row is not None:
                return row.name
        return value[:1].upper() + value[1:]

    def gold(self, pc: PlayerCharacter, cls: Offer | None) -> int | None:
        """The starting gold rolled, in copper; a roll for another class does not count."""
        held = pc.choices.get(GOLD_ROLL) or []
        if len(held) == 2 and cls is not None and held[1] == cls.reference and held[0].isdigit():
            return int(held[0])
        return None

    def purse(
        self, pc: PlayerCharacter, chosen: Mapping[str, Offer | None] | None = None
    ) -> tuple[int, int]:
        """The copper the character starts with, and what the shop has cost of it."""
        chosen = chosen if chosen is not None else self._chosen(pc.choices)
        way = _first(pc.choices, "equipment")
        if way == "packages":
            start = self._equipment(pc, chosen)[1]
        else:
            start = (self.gold(pc, chosen["class"]) or 0) if way == GOLD else 0
        return start, self._spent(pc)

    def shop_offers(self, pc: PlayerCharacter) -> list[Offer]:
        """What the draft can buy, in the edition of its class."""
        cls = self._chosen(pc.choices)["class"]
        return self.catalogue.shop(cls.edition if cls is not None else "classic")

    def trade(self, pc: PlayerCharacter, action: str) -> None:
        """``+Name|SOURCE`` buys one, ``-Name|SOURCE`` puts one back.

        Spending more than is left is impossible rather than flagged, like point buy.
        """
        reference = action[1:]
        bought = list(pc.choices.get(SHOP) or [])
        if action.startswith("-") and reference in bought:
            bought.remove(reference)
        elif action.startswith("+"):
            offer = next((o for o in self.shop_offers(pc) if o.reference == reference), None)
            start, spent = self.purse(pc)
            if offer is not None and spent + price(offer) <= start:
                bought.append(reference)
        self.answer(pc, SHOP, bought)

    def _spent(self, pc: PlayerCharacter) -> int:
        found = (self.catalogue.item(r) for r in pc.choices.get(SHOP) or [])
        return sum(price(o) for o in found if o is not None)

    def _worn_once(self, items: list[InventoryItem]) -> list[InventoryItem]:
        """One suit of armour and one shield on; the shop may have sold a spare."""
        out: list[InventoryItem] = []
        worn: set[str] = set()
        for item in items:
            found = self.catalogue.item(item.item.reference)
            kind = str(found.data.get("type", "")).split("|")[0] if found else ""
            slot = "shield" if kind == "S" else "armor" if kind in ("LA", "MA", "HA") else ""
            if slot and item.equipped:
                if slot in worn:
                    item = item.model_copy(update={"equipped": False})
                worn.add(slot)
            out.append(item)
        return out

    def _equipment(
        self, pc: PlayerCharacter, chosen: Mapping[str, Offer | None]
    ) -> tuple[list[InventoryItem], int]:
        items: list[InventoryItem] = []
        copper = 0
        for record, prefix in (
            (chosen["class"], "equipment.class"),
            (chosen["background"], "equipment.background"),
        ):
            if record is None:
                continue
            raw = record.data.get("startingEquipment")
            groups = raw.get("defaultData") if isinstance(raw, Mapping) else raw
            for index, group in enumerate(groups or []):
                if not isinstance(group, Mapping):
                    continue
                pick_id = f"{prefix}.{index}"
                keys = [k for k in group if k != "_"]
                taken = ["_"] if "_" in group else []
                answer = _first(pc.choices, pick_id)
                if len(keys) == 1:
                    taken.append(keys[0])
                elif answer is not None:
                    taken.extend(k for k in keys if k.lower() == answer)
                for key in taken:
                    for j, entry in enumerate(group[key]):
                        got, coins = self._equipment_entry(
                            pc, f"{pick_id}.{key.lower()}.{j}", entry
                        )
                        items.extend(got)
                        copper += coins
        return _merge(items), copper

    def _equipment_entry(
        self, pc: PlayerCharacter, pick_id: str, entry: Any
    ) -> tuple[list[InventoryItem], int]:
        if isinstance(entry, str):
            return [self._item(entry, 1)], 0
        if not isinstance(entry, Mapping):
            return [], 0
        quantity = int(entry.get("quantity", 1))
        if "value" in entry:
            return [], int(entry["value"])
        if equipment_kinds(entry):
            return [self._item(v, 1) for v in pc.choices.get(pick_id) or []], 0
        if entry.get("item"):
            got = [self._item(str(entry["item"]), quantity, str(entry.get("displayName") or ""))]
            return got, int(entry.get("containsValue", 0))
        if entry.get("special"):
            name = str(entry["special"])
            return [InventoryItem(item=ChosenOption(name=name), quantity=quantity)], 0
        return [], 0

    def _item(self, reference: str, quantity: int, note: str = "") -> InventoryItem:
        found = self.catalogue.item(reference)
        option = (
            ChosenOption(name=found.name, source=found.source)
            if found
            else ChosenOption.model_validate(reference.split("|")[0].title())
        )
        kind = str(found.data.get("type", "")).split("|")[0] if found else ""
        # Armour, a shield and weapons are worn from the start, so AC and the attacks
        # show what the package gives while the player is still choosing.
        return InventoryItem(
            item=option, quantity=quantity, notes=note, equipped=kind in _EQUIPPED_KINDS
        )


# The way to start equipped that buys everything, and its answers in ``choices``: the
# roll (copper, and the class it was rolled for) and what was bought, once per item.
GOLD = "gold"
GOLD_ROLL = "equipment.gold"
SHOP = "equipment.shop"


def price(offer: Offer) -> int:
    """What ``offer`` costs in copper; a fraction of a copper rounds up."""
    return ceil(float(offer.data.get("value") or 0))


# Item kinds a starting package puts on rather than in the pack.
_EQUIPPED_KINDS = frozenset({"LA", "MA", "HA", "S", "M", "R"})


def _option(offer: Offer | None) -> ChosenOption | None:
    return ChosenOption(name=offer.name, source=offer.source) if offer is not None else None


def _merge(items: list[InventoryItem]) -> list[InventoryItem]:
    """The same item twice becomes one line with both quantities."""
    out: dict[tuple[str, str], InventoryItem] = {}
    for item in items:
        key = (item.item.reference.lower(), item.notes)
        if key in out:
            out[key] = replace_quantity(out[key], out[key].quantity + item.quantity)
        else:
            out[key] = item
    return list(out.values())


def replace_quantity(item: InventoryItem, quantity: int) -> InventoryItem:
    """``item`` with another quantity."""
    return item.model_copy(update={"quantity": quantity})


def _coins(copper: int) -> Currency:
    """Copper as gold, silver and copper, as the corpus's ``value`` amounts are."""
    gp, rest = divmod(copper, 100)
    sp, cp = divmod(rest, 10)
    return Currency(gp=gp, sp=sp, cp=cp)


__all__ = [
    "GOLD",
    "GOLD_ROLL",
    "ORDERS",
    "SHOP",
    "LevelPlan",
    "Plan",
    "price",
    "Planner",
    "Problem",
    "SpellPlan",
    "Step",
    "StepId",
    "evaluate_prepared",
    "level_plan",
]
