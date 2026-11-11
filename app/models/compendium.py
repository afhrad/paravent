"""Compendium entity models: the categories Paravent renders and cross-references.

Field selection is driven by measured fill rates in the real corpus rather than by the
upstream schema in full. Anything not declared here still survives on the model via
``extra`` (see :class:`app.models.base.FiveToolsModel`).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from app.models.base import Copyable, Entry, Named
from app.models.values import (
    ArmorClass,
    ChallengeRating,
    CreatureAction,
    CreatureType,
    HitPoints,
    Speed,
    Spellcasting,
    format_alignment,
    format_size,
)


class Monster(Named, Copyable):
    """A bestiary entry.

    4,458 entries across 202 files; 25% of them are ``_copy`` shells that must be
    resolved before use.
    """

    # Identity
    size: list[str] = Field(default_factory=list)
    type: str | CreatureType | None = None
    alignment: list[Any] = Field(default_factory=list)
    alignmentPrefix: str | None = None

    # Defences
    ac: list[int | ArmorClass] = Field(default_factory=list)
    hp: HitPoints | None = None
    speed: Speed | dict[str, Any] | None = None

    # Ability scores. Absent on ~25% of entries (templates and copy shells).
    # D&D names two abilities exactly like Python builtins, so the fields are
    # suffixed and the upstream JSON keys kept as aliases. Without this, a bare
    # ``str`` field shadows the builtin and breaks every later ``str | None``
    # annotation in the class body.
    str_: int | None = Field(default=None, alias="str")
    dex: int | None = None
    con: int | None = None
    int_: int | None = Field(default=None, alias="int")
    wis: int | None = None
    cha: int | None = None

    # Proficiencies and senses
    save: dict[str, Any] = Field(default_factory=dict)
    skill: dict[str, Any] = Field(default_factory=dict)
    senses: list[Any] = Field(default_factory=list)
    passive: int | str | None = None
    languages: list[Any] = Field(default_factory=list)

    # Damage and condition modifiers. These lists mix bare strings with objects.
    immune: list[Any] = Field(default_factory=list)
    resist: list[Any] = Field(default_factory=list)
    vulnerable: list[Any] = Field(default_factory=list)
    conditionImmune: list[Any] = Field(default_factory=list)

    # Challenge
    cr: str | ChallengeRating | None = None
    pbNote: str | None = None
    # Bare int on a handful of 2024-era entries; dict elsewhere.
    initiative: int | dict[str, Any] | None = None

    # Statblock body
    trait: list[CreatureAction] = Field(default_factory=list)
    action: list[CreatureAction] = Field(default_factory=list)
    bonus: list[CreatureAction] = Field(default_factory=list)
    reaction: list[CreatureAction] = Field(default_factory=list)
    legendary: list[CreatureAction] = Field(default_factory=list)
    mythic: list[CreatureAction] = Field(default_factory=list)
    legendaryHeader: list[Any] = Field(default_factory=list)
    legendaryActions: int | None = None
    legendaryGroup: dict[str, Any] | None = None
    mythicHeader: list[Any] = Field(default_factory=list)
    spellcasting: list[Spellcasting] = Field(default_factory=list)

    # Classification tags, used by the encounter builder and search facets
    environment: list[str] = Field(default_factory=list)
    traitTags: list[str] = Field(default_factory=list)
    actionTags: list[str] = Field(default_factory=list)
    senseTags: list[str] = Field(default_factory=list)
    languageTags: list[str] = Field(default_factory=list)
    damageTags: list[str] = Field(default_factory=list)
    miscTags: list[str] = Field(default_factory=list)
    conditionInflict: list[str] = Field(default_factory=list)

    # Provenance
    group: list[str] | str | None = None
    isNpc: bool | None = None
    isNamedCreature: bool | None = None
    hasToken: bool | None = None
    hasFluff: bool | None = None
    hasFluffImages: bool | None = None
    srd: bool | str | None = None
    otherSources: list[Any] = Field(default_factory=list)
    reprintedAs: list[Any] = Field(default_factory=list)

    @property
    def abilities(self) -> dict[str, int | None]:
        """The six ability scores keyed by their canonical 5e abbreviations."""
        return {
            "str": self.str_,
            "dex": self.dex,
            "con": self.con,
            "int": self.int_,
            "wis": self.wis,
            "cha": self.cha,
        }

    @property
    def cr_value(self) -> str | None:
        """The challenge rating as a plain string, whichever shape it arrived in."""
        if isinstance(self.cr, ChallengeRating):
            return self.cr.cr
        return self.cr

    @property
    def type_name(self) -> str:
        """The creature type as a bare string, discarding subtype tags."""
        if isinstance(self.type, CreatureType):
            inner = self.type.type
            return inner if isinstance(inner, str) else str(inner)
        return self.type or ""

    @property
    def size_label(self) -> str:
        """Human-readable size, e.g. ``Large``."""
        return format_size(self.size)

    @property
    def alignment_label(self) -> str:
        """Human-readable alignment, e.g. ``chaotic evil``."""
        return format_alignment(self.alignment)


class Spell(Named):
    """A spell. 936 entries; the schema is dense and stable — 10 fields at 100% fill."""

    level: int
    school: str
    time: list[Any] = Field(default_factory=list)
    range: dict[str, Any] = Field(default_factory=dict)
    components: dict[str, Any] = Field(default_factory=dict)
    duration: list[Any] = Field(default_factory=list)
    entries: list[Any] = Field(default_factory=list)
    entriesHigherLevel: list[Any] = Field(default_factory=list)

    # Facets used by search and by the encounter/statblock cross-referencer
    damageInflict: list[str] = Field(default_factory=list)
    damageResist: list[str] = Field(default_factory=list)
    damageImmune: list[str] = Field(default_factory=list)
    conditionInflict: list[str] = Field(default_factory=list)
    savingThrow: list[str] = Field(default_factory=list)
    spellAttack: list[str] = Field(default_factory=list)
    abilityCheck: list[str] = Field(default_factory=list)
    areaTags: list[str] = Field(default_factory=list)
    miscTags: list[str] = Field(default_factory=list)
    affectsCreatureType: list[str] = Field(default_factory=list)
    scalingLevelDice: dict[str, Any] | list[Any] | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    srd: bool | str | None = None
    otherSources: list[Any] = Field(default_factory=list)

    @property
    def is_cantrip(self) -> bool:
        """Cantrips are level 0 and never consume a slot."""
        return self.level == 0

    @property
    def is_ritual(self) -> bool:
        """Rituals can be cast without a slot at +10 minutes casting time."""
        return bool(self.meta.get("ritual"))

    @property
    def is_concentration(self) -> bool:
        """Concentration spells end when the caster loses concentration."""
        return any(isinstance(d, dict) and d.get("concentration") for d in self.duration)


class Item(Named, Copyable):
    """An item. 2,417 magic items plus 230 base items; the widest schema at 104 fields."""

    type: str | None = None
    rarity: str | None = None
    tier: str | None = None
    weight: float | None = None
    value: float | None = None
    entries: list[Any] = Field(default_factory=list)
    additionalEntries: list[Any] = Field(default_factory=list)

    # Attunement is ``True`` for plain attunement, or prose for a restriction
    # (``"by a spellcaster"``) — hence the union.
    reqAttune: bool | str | None = None
    reqAttuneTags: list[Any] = Field(default_factory=list)

    wondrous: bool | None = None
    baseItem: str | None = None
    weaponCategory: str | None = None
    # ``property`` is a Python builtin used as a decorator further down this class
    # body, so the field is suffixed and the JSON key kept as an alias.
    properties: list[Any] = Field(default_factory=list, alias="property")
    dmg1: str | None = None
    dmg2: str | None = None
    dmgType: str | None = None
    range: str | None = None
    ac: int | None = None

    bonusWeapon: str | None = None
    bonusWeaponAttack: str | None = None
    bonusWeaponDamage: str | None = None
    bonusAc: str | None = None
    bonusSpellAttack: str | None = None
    bonusSpellSaveDc: str | None = None
    bonusSavingThrow: str | None = None

    charges: int | str | None = None
    recharge: str | None = None
    rechargeAmount: int | str | None = None
    attachedSpells: list[Any] | dict[str, Any] = Field(default_factory=list)

    lootTables: list[str] = Field(default_factory=list)
    miscTags: list[str] = Field(default_factory=list)
    srd: bool | str | None = None
    otherSources: list[Any] = Field(default_factory=list)

    @property
    def requires_attunement(self) -> bool:
        """True when the item needs attunement, conditional or not."""
        return self.reqAttune is not None and self.reqAttune is not False


class Condition(Named):
    """A condition such as *prone* or *grappled*. 30 entries."""

    entries: list[Any] = Field(default_factory=list)
    srd: bool | str | None = None


class Disease(Named):
    """A disease. 29 entries; shares the conditionsdiseases.json file."""

    entries: list[Any] = Field(default_factory=list)


class Status(Named):
    """A status such as *concentrating*. 5 entries."""

    entries: list[Any] = Field(default_factory=list)


class Background(Named):
    """A character background. 157 entries."""

    entries: list[Any] = Field(default_factory=list)
    skillProficiencies: list[Any] = Field(default_factory=list)
    languageProficiencies: list[Any] = Field(default_factory=list)
    toolProficiencies: list[Any] = Field(default_factory=list)
    startingEquipment: list[Any] = Field(default_factory=list)
    feats: list[Any] = Field(default_factory=list)
    ability: list[Any] = Field(default_factory=list)
    srd: bool | str | None = None


class Feat(Named):
    """A feat. 265 entries."""

    entries: list[Any] = Field(default_factory=list)
    prerequisite: list[Any] = Field(default_factory=list)
    ability: list[Any] = Field(default_factory=list)
    category: str | None = None
    skillProficiencies: list[Any] = Field(default_factory=list)
    srd: bool | str | None = None


class Race(Named, Copyable):
    """A player race. 157 races plus 98 subraces."""

    # Present on subraces, which point back at their parent race.
    raceName: str | None = None
    raceSource: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _name_base_variant(cls, data: Any) -> Any:
        """Give the unnamed base variant of a race a name.

        Five subraces in the corpus carry no ``name`` at all — they are the *base*
        variant of their parent (the plain Dragonborn alongside Chromatic and
        Metallic). Upstream renders them as the parent race's own name, so we
        materialise that rather than making ``name`` optional on every entry and
        pushing the None into every consumer.
        """
        if isinstance(data, dict) and not data.get("name") and data.get("raceName"):
            data = {**data, "name": data["raceName"], "isBaseVariant": True}
        return data

    @property
    def is_base_variant(self) -> bool:
        """True when this subrace is its parent race's unnamed default variant."""
        return bool(self.extra_fields().get("isBaseVariant"))

    size: list[str] = Field(default_factory=list)
    speed: int | Speed | dict[str, Any] | None = None
    ability: list[Any] = Field(default_factory=list)
    entries: list[Any] = Field(default_factory=list)
    traitTags: list[str] = Field(default_factory=list)
    languageProficiencies: list[Any] = Field(default_factory=list)
    darkvision: int | None = None
    srd: bool | str | None = None


class Action(Named):
    """A combat action such as *Dash* or *Grapple*. 48 entries."""

    entries: list[Any] = Field(default_factory=list)
    time: list[Any] = Field(default_factory=list)
    seeAlsoAction: list[str] = Field(default_factory=list)
    srd: bool | str | None = None


class Skill(Named):
    """A skill such as *Stealth*. 36 entries."""

    entries: list[Any] = Field(default_factory=list)
    ability: str | None = None


class Sense(Named):
    """A sense such as *darkvision*. 8 entries."""

    entries: list[Any] = Field(default_factory=list)


class Deity(Named):
    """A deity. 563 entries."""

    entries: list[Any] = Field(default_factory=list)
    pantheon: str | None = None
    alignment: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    symbol: str | None = None
    title: str | None = None
    category: str | None = None


class VariantRule(Named):
    """An optional or variant rule. 230 entries."""

    entries: list[Any] = Field(default_factory=list)
    ruleType: str | None = None
    type: str | None = None


class Language(Named):
    """A language. 186 entries."""

    entries: list[Any] = Field(default_factory=list)
    type: str | None = None
    script: str | None = None
    typicalSpeakers: list[str] = Field(default_factory=list)


class OptionalFeature(Named):
    """An invocation, maneuver, metamagic, or similar. 213 entries."""

    entries: list[Any] = Field(default_factory=list)
    featureType: list[str] = Field(default_factory=list)
    prerequisite: list[Any] = Field(default_factory=list)


class Trap(Named):
    """A trap. 37 entries."""

    entries: list[Any] = Field(default_factory=list)
    trapHazType: str | None = None
    tier: str | None = None
    threat: str | None = None


class Hazard(Named):
    """A hazard. 73 entries."""

    entries: list[Any] = Field(default_factory=list)
    trapHazType: str | None = None


class Reward(Named):
    """A blessing, charm, or boon. 272 entries."""

    entries: list[Any] = Field(default_factory=list)
    type: str | None = None
    rarity: str | None = None


class Object(Named):
    """An inanimate object with a statblock. 37 entries."""

    entries: list[Any] = Field(default_factory=list)
    size: list[str] = Field(default_factory=list)
    ac: int | list[Any] | dict[str, Any] | None = None
    hp: int | HitPoints | dict[str, Any] | None = None
    objectType: str | None = None


class Table(Named):
    """A rollable table. 13 entries in tables.json, many more inline in books."""

    caption: str | None = None
    colLabels: list[str] = Field(default_factory=list)
    colStyles: list[str] = Field(default_factory=list)
    rows: list[Any] = Field(default_factory=list)


class Fluff(Named, Copyable):
    """Flavour text and images attached to an entry by ``(name, source)``.

    Fluff is a **parallel keyspace, not a field**: it lives in sibling ``fluff-*.json``
    files keyed exactly like the entries it describes, and is joined at load time
    (documentation/TECHSTACK.md §6b). It is not searchable in its own right — nobody looks up fluff,
    they look up the monster and expect its description to be there.

    1,310 of 5,984 records are ``_copy`` shells, so this is :class:`Copyable`. The copy
    targets resolve within the fluff keyspace, which is why fluff with no matching entry
    is still loaded rather than discarded: it may be a parent.
    """

    entries: list[Any] = Field(default_factory=list)
    images: list[Any] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _tolerate_explicit_nulls(cls, data: Any) -> Any:
        """Drop ``"images": null``, which one record in the corpus carries.

        An explicit null is not the same as an absent key in 5eTools — elsewhere it
        means "delete the inherited value" — but on a list field it would fail
        validation, and the intent here is plainly "no images".
        """
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not (k == "images" and v is None)}
        return data

    @property
    def has_content(self) -> bool:
        """True when this record carries anything worth rendering."""
        return bool(self.entries or self.images)


# --------------------------------------------------------------------------------------
# Classes. One file per class under ``class/``, each holding all four keys below.
#
# Character generation depends on these entirely (documentation/CHARGEN.md §2 steps 3–4, 8), and so
# does any ``{@class …}`` or ``{@classFeature …}`` tag. Field sets are measured against
# the real corpus: 30 classes, 315 subclasses, 677 class features, 1,397 subclass
# features, with no polymorphic fields among them.
# --------------------------------------------------------------------------------------


class ClassEntry(Named):
    """A character class.

    The spellcasting progression fields are what make a caster work and are deliberately
    typed rather than left to ``extra``: ``preparedSpells`` is an *evaluable formula*
    (``"<$level$> + <$int_mod$>"``), and ``classTableGroups`` carries the per-level spell
    slot table (documentation/CHARGEN.md §2 step 8).

    Named ``ClassEntry`` because ``class`` is a Python keyword; the category is still
    ``"class"`` and the JSON key is untouched.
    """

    edition: str | None = None

    # Core mechanics. Absent on sidekick and partial-variant records, hence optional.
    hd: dict[str, Any] | None = None
    proficiency: list[str] = Field(default_factory=list)
    startingProficiencies: dict[str, Any] | None = None
    startingEquipment: dict[str, Any] | None = None
    multiclassing: dict[str, Any] | None = None
    primaryAbility: list[Any] = Field(default_factory=list)

    # Progression tables.
    classFeatures: list[Any] = Field(default_factory=list)
    classTableGroups: list[Any] = Field(default_factory=list)
    optionalfeatureProgression: list[Any] = Field(default_factory=list)
    featProgression: list[Any] = Field(default_factory=list)
    subclassTitle: str | None = None

    # Spellcasting.
    casterProgression: str | None = None
    spellcastingAbility: str | None = None
    cantripProgression: list[int] = Field(default_factory=list)
    preparedSpells: str | None = None
    preparedSpellsProgression: list[int] = Field(default_factory=list)
    preparedSpellsChange: str | None = None
    spellsKnownProgression: list[int] = Field(default_factory=list)
    spellsKnownProgressionFixed: list[int] = Field(default_factory=list)
    spellsKnownProgressionFixedByLevel: dict[str, Any] | None = None
    additionalSpells: list[Any] = Field(default_factory=list)

    @property
    def is_caster(self) -> bool:
        """True when the class has a spell slot progression of its own."""
        return self.casterProgression is not None

    @property
    def subclass_level(self) -> int | None:
        """The level at which this class chooses a subclass, or None.

        There is no ``subclassLevel`` scalar in the corpus — the level is encoded as a
        ``gainSubclassFeature`` flag inside ``classFeatures`` (documentation/CHARGEN.md G5). Cleric,
        Sorcerer and Warlock choose at 1, Druid and Wizard at 2, most others at 3, so
        this cannot be hardcoded.
        """
        for feature in self.classFeatures:
            if not isinstance(feature, dict) or not feature.get("gainSubclassFeature"):
                continue
            ref = feature.get("classFeature")
            if isinstance(ref, str):
                level = _level_from_reference(ref)
                if level is not None:
                    return level
        return None


class Subclass(Named):
    """A subclass, stored alongside its parent class.

    ``page`` and ``source`` are inherited from the parent on 124 of 315 records via
    ``_copy``, so :class:`Named`'s required ``source`` only holds after resolution —
    which is why resolution runs before validation.
    """

    shortName: str
    className: str
    classSource: str
    edition: str | None = None

    subclassFeatures: list[Any] = Field(default_factory=list)
    subclassTableGroups: list[Any] = Field(default_factory=list)
    optionalfeatureProgression: list[Any] = Field(default_factory=list)

    # A handful of subclasses grant casting the parent class lacks (Eldritch Knight,
    # Arcane Trickster), so the progression fields repeat here.
    casterProgression: str | None = None
    spellcastingAbility: str | None = None
    cantripProgression: list[int] = Field(default_factory=list)
    spellsKnownProgression: list[int] = Field(default_factory=list)
    preparedSpellsProgression: list[int] = Field(default_factory=list)
    preparedSpellsChange: str | None = None
    additionalSpells: list[Any] = Field(default_factory=list)

    @property
    def key(self) -> str:
        """Composite key: ``(name, source, className, classSource)``.

        ``(name, source)`` alone collides on 124 of 315 records — subclass names repeat
        across classes. Verified against the corpus: this key is unique.
        """
        return (
            f"{self.name.lower()}|{self.source.lower()}"
            f"|{self.className.lower()}|{self.classSource.lower()}"
        )


class ClassFeature(Named):
    """A class feature, granted at a specific level."""

    className: str
    classSource: str
    level: int
    entries: list[Any] = Field(default_factory=list)
    header: int | None = None
    consumes: dict[str, Any] | None = None
    isClassFeatureVariant: bool = False

    @property
    def key(self) -> str:
        """Composite key: ``(name, source, className, level)``.

        The level is load-bearing, not decorative: *Channel Divinity* and friends repeat
        at several levels, and 55 of 677 records collide on ``(name, source)`` alone.
        """
        return f"{self.name.lower()}|{self.source.lower()}|{self.className.lower()}|{self.level}"


class SubclassFeature(Named):
    """A subclass feature, granted at a specific level."""

    className: str
    classSource: str
    subclassShortName: str
    subclassSource: str
    level: int
    entries: list[Any] = Field(default_factory=list)
    header: int | None = None
    consumes: dict[str, Any] | None = None
    isClassFeatureVariant: bool = False

    @property
    def key(self) -> str:
        """Composite key: ``(name, source, className, subclassShortName, level)``.

        *Additional Maneuvers* (Battle Master, PHB) exists at levels 7, 10 and 15 —
        117 of 1,397 records collide without the level and subclass.
        """
        return (
            f"{self.name.lower()}|{self.source.lower()}|{self.className.lower()}"
            f"|{self.subclassShortName.lower()}|{self.level}"
        )


class LegendaryGroup(Named):
    """Lair and regional actions shared by a family of legendary creatures.

    A legendary monster's statblock is incomplete without its group: the monster record
    carries a ``legendaryGroup`` pointer rather than repeating the text.
    """

    lairActions: list[Any] = Field(default_factory=list)
    regionalEffects: list[Any] = Field(default_factory=list)
    mythicEncounter: list[Any] = Field(default_factory=list)


class Psionic(Named):
    """A psionic discipline or talent (UA Mystic). 52 entries; disciplines carry modes."""

    type: str | None = None
    order: str | None = None
    entries: list[Any] = Field(default_factory=list)
    focus: str | None = None
    modes: list[Any] = Field(default_factory=list)


class Vehicle(Named):
    """A ship, spelljammer, airship or infernal war machine. 39 entries.

    Most carry no ``entries``: a vehicle is its parts — hull, helm, weapons, stations —
    each a named block with its own AC, HP and text.
    """

    vehicleType: str | None = None
    # A size code, or a list of them for the four vehicles printed as objects.
    size: str | list[str] | None = None
    terrain: list[str] = Field(default_factory=list)
    entries: list[Any] = Field(default_factory=list)


class VehicleUpgrade(Named):
    """An upgrade for an infernal war machine or a ship. 31 entries."""

    upgradeType: list[str] = Field(default_factory=list)
    entries: list[Any] = Field(default_factory=list)


class Facility(Named):
    """A bastion facility (2024 DMG). 53 entries."""

    facilityType: str | None = None
    level: int | None = None
    entries: list[Any] = Field(default_factory=list)


class CharOption(Named):
    """A character-creation option such as a supernatural gift. 44 entries."""

    optionType: list[str] = Field(default_factory=list)
    entries: list[Any] = Field(default_factory=list)


class Cult(Named):
    """A cult, with its typical members and signature spells. 30 entries."""

    type: str | None = None
    entries: list[Any] = Field(default_factory=list)


class Boon(Named):
    """A demonic boon granted to a cult's members. 12 entries."""

    type: str | None = None
    entries: list[Any] = Field(default_factory=list)


class ItemMastery(Named):
    """A 2024 weapon mastery property — Topple, Vex, Sap… 8 entries, in items-base.json."""

    entries: list[Any] = Field(default_factory=list)


class Abbreviated(Named, Copyable):
    """An entry the corpus addresses by its ``abbreviation``, not its name: items list
    ``"property": ["V|XPHB"]`` and ``"type": "M|XPHB"``, tags read
    ``{@itemProperty V|XPHB|Versatile}``, and reprints and ``_copy`` name the code too.

    The abbreviation is the identity (key, tags, reprints); ``name`` is what a page shows.
    """

    abbreviation: str
    entries: list[Any] = Field(default_factory=list)

    @property
    def key(self) -> str:
        """``abbreviation|source``: ``v|xphb``."""
        return f"{self.abbreviation.lower()}|{self.source.lower()}"

    @property
    def ref_name(self) -> str:
        """Tags and items name the code."""
        return self.abbreviation


class ItemProperty(Abbreviated):
    """A weapon property — Versatile, Finesse, Ammunition. 26 entries, in items-base.json.

    Upstream it has no ``name`` (but "special"): it is the heading of its first entry,
    lifted here. ``template`` is how an item prints it: ``"{{prop_name}} ({{item.dmg2}})"``.
    """

    template: str = ""

    @model_validator(mode="before")
    @classmethod
    def _name_from_entries(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("name"):
            first = (data.get("entries") or [{}])[0]
            heading = first.get("name") if isinstance(first, dict) else None
            data = {**data, "name": heading or data.get("abbreviation", "")}
        return data


class ItemType(Abbreviated):
    """An item type — Melee Weapon, Artisan's Tools, Potion. 67 entries, 17 with rules
    text, in items-base.json."""


class Deck(Named, Copyable):
    """A card deck — the Deck of Many Things, the Tarokka. 34 entries, 2 ``_copy`` shells.

    ``cards`` lists its cards as ``"Name|Deck"`` or ``"Name|Deck|SOURCE"`` strings, or as
    dicts carrying that string under ``uid`` plus a ``count``.
    """

    cards: list[Any] = Field(default_factory=list)
    entries: list[Any] = Field(default_factory=list)


class Card(Named):
    """One card of a deck. 711 entries.

    ``(name, source)`` is **not** unique: *Knight* is in two decks of the 2024 DMG, and 32
    pairs repeat. The deck (``set``) completes the key, and a card is reached through
    its deck — ``{@card Sun|Deck of Many Things}`` names the deck in slot 2.
    """

    set: str
    face: Any = None
    entries: list[Any] = Field(default_factory=list)

    @property
    def key(self) -> str:
        """Composite key: ``(name, source, set)``."""
        return f"{self.name.lower()}|{self.source.lower()}|{self.set.lower()}"


def _level_from_reference(ref: str) -> int | None:
    """Pull the level out of a feature reference string.

    References are pipe-delimited: ``"Martial Archetype|Fighter||3"`` is
    ``name|className|classSource|level``, with an empty segment where the source is
    implied. Returns None when the string does not carry a numeric level.
    """
    parts = ref.split("|")
    for part in reversed(parts):
        if part.isdigit():
            return int(part)
    return None


__all__ = [
    "Action",
    "Background",
    "Boon",
    "ItemMastery",
    "Card",
    "CharOption",
    "ClassEntry",
    "ClassFeature",
    "Condition",
    "Cult",
    "Deck",
    "Deity",
    "Disease",
    "Entry",
    "Facility",
    "Feat",
    "Fluff",
    "Hazard",
    "Item",
    "Language",
    "LegendaryGroup",
    "Monster",
    "Object",
    "OptionalFeature",
    "Psionic",
    "Race",
    "Reward",
    "Sense",
    "Skill",
    "Spell",
    "Status",
    "Subclass",
    "SubclassFeature",
    "Table",
    "Trap",
    "VariantRule",
    "Vehicle",
    "VehicleUpgrade",
]
