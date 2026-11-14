"""The character sheet: everything a screen shows, derived from the file plus the corpus.

The character file holds choices and play state (:mod:`app.models.pc`); this module turns
that into a :class:`Sheet` — every number with its explanation, every spell with its
real data, every slot, attack and feature. Rendering reads only the ``Sheet``.

Two rules run through all of it:

* **Derive, then let an override win, visibly.** Each number is computed from the rules
  and the data; if the file states an override for it, that value is shown instead,
  flagged as such with its note (documentation/CHARACTER-FORMAT.md).
* **Missing data degrades, never fails.** A character whose class is not in the corpus
  still renders — the values that need the class are blank unless overridden, and a
  spell that cannot be found is listed by name, marked as not in the books.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from app.models.base import ABILITIES
from app.models.gamestate import SourceSet
from app.models.pc import (
    ChosenOption,
    ClassLevel,
    HitPointMethod,
    PlayerCharacter,
    Recharge,
)
from app.models.protocol import ProtocolEntry
from app.services import feature_options, granted_spells, wild_shape
from app.services.character import (
    HEAVY_ARMOR,
    LIGHT_ARMOR,
    MEDIUM_ARMOR,
    SHIELD,
    UNARMORED_DEFENSE,
    Derivation,
    EquippedArmor,
    ability_modifier,
    armor_class,
    check_bonus,
    prepared_spells,
    proficiency_bonus,
    spell_attack_bonus,
    spell_save_dc,
)
from app.services.class_refs import (
    FeatureRef,
    feature_refs,
    files_under,
    is_variant,
    subclass_owner,
)
from app.services.text import first_dice, paragraphs, strip_tags
from app.services.translations import translation_key
from app.services.wild_shape import Form, WildShapeRules

ABILITY_NAMES = {
    "str": "Strength",
    "dex": "Dexterity",
    "con": "Constitution",
    "int": "Intelligence",
    "wis": "Wisdom",
    "cha": "Charisma",
}

SCHOOLS = {
    "A": "Abjuration",
    "C": "Conjuration",
    "D": "Divination",
    "E": "Enchantment",
    "V": "Evocation",
    "I": "Illusion",
    "N": "Necromancy",
    "T": "Transmutation",
}

# The fourteen conditions a character can be under; exhaustion is a level, kept apart.
CONDITIONS = (
    "blinded",
    "charmed",
    "deafened",
    "frightened",
    "grappled",
    "incapacitated",
    "invisible",
    "paralyzed",
    "petrified",
    "poisoned",
    "prone",
    "restrained",
    "stunned",
    "unconscious",
)

# Used only when no corpus is configured, so a hand-made character still has skills.
# With a corpus, the skill list and each skill's ability come from skills.json.
FALLBACK_SKILLS = {
    "acrobatics": "dex",
    "animal handling": "wis",
    "arcana": "int",
    "athletics": "str",
    "deception": "cha",
    "history": "int",
    "insight": "wis",
    "intimidation": "cha",
    "investigation": "int",
    "medicine": "wis",
    "nature": "int",
    "perception": "wis",
    "performance": "cha",
    "persuasion": "cha",
    "religion": "int",
    "sleight of hand": "dex",
    "stealth": "dex",
    "survival": "wis",
}

# The multiclass spellcaster table (PHB ch. 6) — prose-only in the corpus
# (documentation/CHARGEN.md G3). It equals the full caster progression, so it also
# serves any caster level computed by the multiclass rule.
CASTER_SLOTS: dict[int, tuple[int, ...]] = {
    1: (2,),
    2: (3,),
    3: (4, 2),
    4: (4, 3),
    5: (4, 3, 2),
    6: (4, 3, 3),
    7: (4, 3, 3, 1),
    8: (4, 3, 3, 2),
    9: (4, 3, 3, 3, 1),
    10: (4, 3, 3, 3, 2),
    11: (4, 3, 3, 3, 2, 1),
    12: (4, 3, 3, 3, 2, 1),
    13: (4, 3, 3, 3, 2, 1, 1),
    14: (4, 3, 3, 3, 2, 1, 1),
    15: (4, 3, 3, 3, 2, 1, 1, 1),
    16: (4, 3, 3, 3, 2, 1, 1, 1),
    17: (4, 3, 3, 3, 2, 1, 1, 1, 1),
    18: (4, 3, 3, 3, 3, 1, 1, 1, 1),
    19: (4, 3, 3, 3, 3, 2, 1, 1, 1),
    20: (4, 3, 3, 3, 3, 2, 2, 1, 1),
}

DAMAGE_TYPES = {
    "A": "acid",
    "B": "bludgeoning",
    "C": "cold",
    "F": "fire",
    "O": "force",
    "L": "lightning",
    "N": "necrotic",
    "P": "piercing",
    "I": "poison",
    "Y": "psychic",
    "R": "radiant",
    "S": "slashing",
    "T": "thunder",
}

WEAPON_PROPERTIES = {
    "A": "ammunition",
    "AF": "ammunition",
    "BF": "burst fire",
    "F": "finesse",
    "H": "heavy",
    "L": "light",
    "LD": "loading",
    "R": "reach",
    "RLD": "reload",
    "S": "special",
    "T": "thrown",
    "2H": "two-handed",
    "V": "versatile",
}

# Feature names that are choices recorded elsewhere, not abilities worth listing.
_SKIP_FEATURES = frozenset({"ability score improvement"})
_SKIP_SPECIES_TRAITS = frozenset({"age", "alignment", "size", "speed", "languages"})


# -- view types ------------------------------------------------------------------------


@dataclass(frozen=True)
class Value:
    """A number on the sheet, how it was reached, and whether a person overrode it."""

    value: int | None
    explanation: str = ""
    overridden: bool = False
    note: str = ""
    # The bonus by source, for the log: ``(("ability:dex", 3), ("proficiency", 2))``.
    sources: tuple[tuple[str, int], ...] = ()

    @property
    def signed(self) -> str:
        """``+3`` / ``-1`` / ``—``."""
        return "—" if self.value is None else f"{self.value:+d}"

    @property
    def text(self) -> str:
        """``13`` / ``—``."""
        return "—" if self.value is None else str(self.value)


@dataclass(frozen=True)
class AbilityView:
    key: str
    name: str
    score: Value
    modifier: int
    save: Value
    save_proficient: bool


@dataclass(frozen=True)
class Defense:
    """A resistance, immunity or vulnerability to one damage type, and its source."""

    kind: str  # "resist", "immune" or "vulnerable"
    damage: str
    origin: str


DEFENSE_KINDS = ("resist", "immune", "vulnerable")


@dataclass(frozen=True)
class SkillView:
    key: str
    name: str
    ability: str
    value: Value
    proficient: bool
    expertise: bool


@dataclass(frozen=True)
class SpellView:
    reference: str
    name: str
    found: bool
    source: str | None = None
    level: int = 0
    school: str = ""
    time: str = ""
    range: str = ""
    components: str = ""
    duration: str = ""
    concentration: bool = False
    ritual: bool = False
    save: str | None = None
    attack: bool = False
    damage: str | None = None
    damage_types: tuple[str, ...] = ()
    # What each slot above the spell's level adds ({@scaledamage 8d6|3-9|1d6}: "1d6").
    upcast: str = ""
    # A healing spell (tagged HL) that heals one creature by dice: "1d8", what a higher
    # slot adds ({@scaledice 1d8|1-9|1d8}), and whether the caster's modifier is added.
    heal: str = ""
    heal_upcast: str = ""
    heal_mod: bool = False
    # How many creatures one casting heals (Mass Cure Wounds: up to six).
    heal_targets: int = 1
    material: str | None = None
    text: tuple[str, ...] = ()
    # The 5eTools entries themselves, for the tag renderer (links, roll chips).
    entries: tuple[Any, ...] = ()
    # Where ``entries`` turns from the spell's text to its "At Higher Levels" text, so
    # a translation (stored by the record's own paths) can be laid over each part.
    higher_from: int = 0
    # The corpus's own facts behind ``school``, ``time``, ``range`` and ``duration``, so a
    # page can phrase them in its reader's language (app/ui/rules_text.py); the strings
    # above stay the English rendering.
    school_code: str = ""
    time_data: tuple[Any, ...] = ()
    range_data: dict[str, Any] = field(default_factory=dict)
    duration_data: tuple[Any, ...] = ()
    # Always prepared by the subclass (a domain's or an oath's spells): not chosen, and
    # not counted against the number the character may prepare.
    always: bool = False
    # Granted by a feat, species or background ("Magic Initiate"), cast with its own
    # ability; ``free`` names the resource that counts its castings without a slot
    # ("Bless (Magic Initiate)"), or is "will" for at will.
    origin: str = ""
    ability: str = ""
    free: str = ""


@dataclass(frozen=True)
class Preparing:
    """A class that prepares its spells after a long rest, and how many it may.

    From its whole class list (a cleric), or from the spellbook (a wizard). The 2024
    bard, sorcerer and warlock are not here: they change their list on level up.
    """

    class_name: str
    level: int
    limit: int
    from_book: bool


@dataclass(frozen=True)
class SlotLevel:
    level: int
    total: int
    used: int

    @property
    def available(self) -> int:
        return max(0, self.total - self.used)


@dataclass(frozen=True)
class AttackView:
    name: str
    bonus: Value
    damage: str
    damage_type: str
    properties: tuple[str, ...] = ()
    range: str = ""
    versatile: str | None = None
    # The weapon's 2024 mastery ("Sap|XPHB"); shown when the character has Weapon Mastery.
    mastery: str = ""
    # A mastery's save DC: 8 + the attack's ability modifier + proficiency (Topple).
    mastery_dc: int = 0
    # Each of ``properties`` as the corpus addresses it, ``("V", "XPHB")``, to link to.
    property_refs: tuple[tuple[str, str], ...] = ()
    # The kind of weapon it is ("Longsword" for a +1 Longsword), for Weapon Master (2024).
    kind: str = ""


@dataclass(frozen=True)
class FeatureView:
    name: str
    origin: str
    level: int | None = None
    text: tuple[str, ...] = ()
    # Which book it came from and what granted it, so a feature whose rules differ
    # between editions (Remarkable Athlete, Draconic Resilience) can be told apart.
    source: str = ""
    kind: str = ""  # "class", "subclass", "species", "background", "feat" or "option"
    owner: str = ""  # the class, for class and subclass features
    # What its prose asked the player to choose (Elemental Adept's damage type).
    chosen: tuple[str, ...] = ()
    # The 5eTools entries themselves, for the tag renderer.
    entries: tuple[Any, ...] = ()
    # Where its text lives, for a translation (TRANSLATION.md §5): the record's
    # category and translation key, and the path inside that record — "" for a record
    # of its own (a feat, a class feature), "entries/3" for a trait inside a species.
    text_at: tuple[str, str, str] | None = None


@dataclass(frozen=True)
class ResourceView:
    name: str
    max: int
    used: int
    recharge: Recharge
    # Uses a short rest gives back where it does not restore them all (2024 Wild Shape).
    short_regain: int = 0

    @property
    def available(self) -> int:
        return max(0, self.max - self.used)


@dataclass(frozen=True)
class ItemView:
    index: int
    name: str
    quantity: int
    equipped: bool
    attuned: bool
    notes: str
    found: bool
    kind: str  # "armor", "shield", "weapon" or "item"
    summary: str = ""
    weight: float | None = None
    # A healing potion's dice ("2d4+2"): drinking it heals the character.
    heal: str = ""


@dataclass(frozen=True)
class Load:
    """What a character carries against what it can (the rules' carrying capacity:
    Strength × 15 lb., doubled by Powerful Build; push, drag or lift twice that)."""

    items: float  # lb., the inventory's known weights times their quantity
    coins: float  # lb., 50 coins to the pound
    capacity: int
    push: int
    powerful_build: bool = False
    unweighed: int = 0  # entries whose weight the books do not give
    # The PHB's variant encumbrance, where the world plays it: past ``encumbered`` lb.
    # speed −10 ft., past ``heavy`` −20 ft. and disadvantage on STR, DEX and CON rolls.
    variant: bool = False
    encumbered: int = 0
    heavy: int = 0

    @property
    def carried(self) -> float:
        """Everything carried, in pounds."""
        return self.items + self.coins

    @property
    def burden(self) -> int:
        """Under the variant: 0 unencumbered, 1 encumbered, 2 heavily encumbered."""
        if not self.variant:
            return 0
        return 2 if self.carried > self.heavy else 1 if self.carried > self.encumbered else 0

    @property
    def over(self) -> bool:
        """More than it can carry: it can only push, drag or lift it, at 5 ft. a turn."""
        return self.carried > self.capacity


@dataclass(frozen=True)
class ConditionView:
    key: str
    name: str
    active: bool
    text: tuple[str, ...] = ()


@dataclass(frozen=True)
class HitDicePool:
    faces: int
    total: int
    used: int

    @property
    def available(self) -> int:
        return max(0, self.total - self.used)


@dataclass(frozen=True)
class WildShapeView:
    """A druid's Wild Shape on the sheet: the rules at its level, the forms it may take
    (a 2024 druid's known ones apart), and the form it is in."""

    rules: WildShapeRules
    forms: tuple[Form, ...]
    known: tuple[Form, ...] = ()
    form: Form | None = None
    # The 2014 form's hit points now; its maximum is the beast's.
    form_hp: int | None = None

    @property
    def choices(self) -> tuple[Form, ...]:
        """The forms the druid may take now: its known ones (2024), else every one."""
        return self.known if self.rules.known is not None else self.forms


@dataclass
class Sheet:
    """Everything one character's screen shows."""

    character: PlayerCharacter
    revision: str | None = None
    file_error: str | None = None
    level: int = 0
    summary: str = ""
    species_name: str = ""
    class_line: str = ""
    background_name: str = ""
    proficiency: Value = field(default_factory=lambda: Value(None))
    abilities: list[AbilityView] = field(default_factory=list)
    armor_class: Value = field(default_factory=lambda: Value(None))
    initiative: Value = field(default_factory=lambda: Value(None))
    speed: Value = field(default_factory=lambda: Value(None))
    hp_max: Value = field(default_factory=lambda: Value(None))
    hp_current: int = 0
    temp_hp: int = 0
    hit_dice: list[HitDicePool] = field(default_factory=list)
    passive_perception: Value = field(default_factory=lambda: Value(None))
    spellcasting_ability: str | None = None
    spell_save_dc: Value | None = None
    spell_attack: Value | None = None
    # A granted spell's ability → its save DC and attack bonus (Magic Initiate's WIS).
    granted_casting: dict[str, tuple[Value, Value]] = field(default_factory=dict)
    skills: list[SkillView] = field(default_factory=list)
    spells: list[SpellView] = field(default_factory=list)
    preparing: list[Preparing] = field(default_factory=list)
    # How many are prepared now: always-prepared spells listed in the file not counted.
    prepared_count: int = 0
    slots: list[SlotLevel] = field(default_factory=list)
    pact: SlotLevel | None = None
    attacks: list[AttackView] = field(default_factory=list)
    features: list[FeatureView] = field(default_factory=list)
    resources: list[ResourceView] = field(default_factory=list)
    inventory: list[ItemView] = field(default_factory=list)
    load: Load | None = None
    conditions: list[ConditionView] = field(default_factory=list)
    concentration: SpellView | None = None
    languages: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    armor_proficiencies: list[str] = field(default_factory=list)
    weapon_proficiencies: list[str] = field(default_factory=list)
    # A named weapon's training ("daggers") → the item it names ("dagger"), so a screen
    # can show the item's name in another language.
    training_items: dict[str, str] = field(default_factory=dict)
    # Weapon Master (2024): the kind chosen ("Longsword"), whether the feat is taken, the
    # kinds it may be (weapons with a mastery the character is proficient with), and
    # whether a long rest has opened the choice again.
    mastery_kind: str = ""
    weapon_master: bool = False
    mastery_choices: list[str] = field(default_factory=list)
    mastery_open: bool = True
    # Worn magic items' bonuses by corpus field ("bonusSavingThrow"), each with its item.
    item_bonuses: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    # Resilient feats whose saving throw the file does not record (taken before the
    # wizard kept it): the sheet asks for it.
    open_saves: int = 0
    # Damage the character resists, ignores or suffers double — from the species, feats
    # and worn items — each with where it comes from.
    defenses: list[Defense] = field(default_factory=list)
    # Resistance choices the file has not made yet: (who offers it, the types to pick from).
    open_resistances: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    # Set by the page: the character is in a running fight, so spells that call for saves
    # can be sent to it.
    in_fight: bool = False
    # The rules the character plays by, from its first class's book: "2014" or "2024".
    edition: str = "2014"
    # Set by the page: the characters a healing spell may target (id, name) — the
    # caster's world, the caster first.
    allies: list[tuple[str, str]] = field(default_factory=list)
    wild_shape: WildShapeView | None = None

    @property
    def casting_modifier(self) -> int:
        """The spellcasting ability modifier, from the spell save DC (8 + prof + mod)."""
        dc = self.spell_save_dc.value if self.spell_save_dc is not None else None
        return 0 if dc is None else dc - 8 - (self.proficiency.value or 0)

    def save_dc_for(self, spell: SpellView) -> Value | None:
        """The save DC ``spell`` is cast with: its grant's ability's, else the class's."""
        found = self.granted_casting.get(spell.ability)
        return found[0] if found else self.spell_save_dc

    def attack_for(self, spell: SpellView) -> Value | None:
        """The spell attack bonus ``spell`` is cast with."""
        found = self.granted_casting.get(spell.ability)
        return found[1] if found else self.spell_attack

    def modifier_for(self, spell: SpellView) -> int:
        """The casting modifier ``spell`` adds (Cure Wounds): its ability's."""
        dc = self.save_dc_for(spell)
        if dc is None or dc.value is None:
            return 0
        return dc.value - 8 - (self.proficiency.value or 0)

    def defended(self, kind: str) -> list[str]:
        """The damage types of one kind ("resist", "immune", "vulnerable"), once each."""
        return list(dict.fromkeys(d.damage for d in self.defenses if d.kind == kind))

    # The character's newest roll in the log, for the party card (set by the service
    # that builds the sheet, which knows the log; documentation/PROTOCOL.md §8).
    last_roll: ProtocolEntry | None = None
    # What is worn, for features that depend on it (Defense, Unarmored Movement).
    armor_type: str | None = None
    shield_bonus: int = 0

    def masters(self, attack: AttackView) -> bool:
        """Uses this weapon's mastery: every weapon's with Weapon Mastery (the class
        feature), or the one kind Weapon Master (the 2024 feat) chose."""
        if not attack.mastery:
            return False
        if self.has_feature("Weapon Mastery") is not None:
            return True
        return bool(self.mastery_kind) and attack.kind.lower() == self.mastery_kind.lower()

    def has_feature(self, name: str, *, kind: str | None = None) -> FeatureView | None:
        """The first feature called ``name`` (ignoring case), optionally of one kind."""
        wanted = name.lower()
        return next(
            (
                f
                for f in self.features
                if f.name.lower() == wanted and (kind is None or f.kind == kind)
            ),
            None,
        )

    @property
    def prepare_limit(self) -> int:
        """How many spells the character may prepare, across the classes that prepare."""
        return sum(p.limit for p in self.preparing)

    @property
    def is_spellcaster(self) -> bool:
        return bool(self.spells or self.slots or self.pact)

    @property
    def is_dying(self) -> bool:
        return self.hp_max.value is not None and self.hp_current <= 0

    @property
    def hp_state(self) -> str:
        """``down``, ``crit``, ``hurt`` or ``""`` — and ``""`` when the maximum is unknown,
        so a character with nothing filled in yet is not drawn as dying."""
        if self.hp_max.value is None:
            return ""
        if self.is_dying:
            return "down"
        ratio = self.hp_ratio
        return "crit" if ratio <= 0.25 else "hurt" if ratio <= 0.5 else ""

    @property
    def hp_text(self) -> str:
        """Current HP for display; "—" while the maximum is unknown, rather than a false 0."""
        return "—" if self.hp_max.value is None else str(self.hp_current)

    @property
    def hp_ratio(self) -> float:
        top = self.hp_max.value or 0
        return 0.0 if top <= 0 else max(0.0, min(1.0, self.hp_current / top))

    def ability(self, key: str) -> AbilityView:
        return next(a for a in self.abilities if a.key == key)

    def spell(self, reference: str) -> SpellView | None:
        lowered = reference.lower()
        return next(
            (s for s in self.spells if s.reference.lower() == lowered or s.name.lower() == lowered),
            None,
        )

    def slot(self, level: int) -> SlotLevel | None:
        return next((s for s in self.slots if s.level == level), None)

    def castable(self, spell: SpellView) -> list[int]:
        """The slot levels a leveled spell can be cast with now: a slot left, at or above
        its level — pact slots included."""
        if spell.level == 0:
            return []
        levels = {s.level for s in self.slots if s.level >= spell.level and s.available}
        pact = self.pact
        if pact is not None and pact.level >= spell.level and pact.available:
            levels.add(pact.level)
        return sorted(levels)


# -- corpus access ---------------------------------------------------------------------


class _Corpus:
    """Lookups the sheet needs, scoped to one source set, tolerant of no corpus at all."""

    def __init__(self, compendium: Any | None, source_set: SourceSet | None) -> None:
        self._compendium = compendium
        self._source_set = source_set or SourceSet()
        self._resolver = compendium.resolver() if compendium is not None else None
        self._dumps: dict[int, dict[str, Any]] = {}

    def data(self, entry: Any) -> dict[str, Any]:
        """An entry as plain 5eTools-shaped data (upstream keys), cached per build."""
        key = id(entry)
        if key not in self._dumps:
            self._dumps[key] = entry.model_dump(by_alias=True, exclude_none=True)
        return self._dumps[key]

    def resolve(self, category: str, option: ChosenOption | None) -> dict[str, Any] | None:
        if option is None or self._resolver is None:
            return None
        found = self._resolver.resolve_parts(category, option.name, option.source, self._source_set)
        return self.data(found.entry) if found.entry is not None else None

    def item(self, option: ChosenOption) -> dict[str, Any] | None:
        return self.resolve("baseitem", option) or self.resolve("item", option)

    def all(self, category: str) -> list[Any]:
        if self._compendium is None:
            return []
        try:
            return list(self._compendium.all(category))
        except Exception:  # noqa: BLE001 - an unloaded category is simply empty here
            return []

    def all_data(self, category: str) -> list[dict[str, Any]]:
        """Every entry of a category as plain data, made once per corpus load and shared:
        read only."""
        dumps = getattr(self._compendium, "dumps", None)
        if dumps is None:
            return [self.data(entry) for entry in self.all(category)]
        try:
            found: list[dict[str, Any]] = dumps(category)
        except Exception:  # noqa: BLE001 - an unloaded category is simply empty here
            return []
        return found

    def wild_forms(self, rules: WildShapeRules) -> list[Form]:
        """The beasts in this source set a druid's Wild Shape allows."""
        return wild_shape.eligible(self._compendium, self._source_set, rules)

    def subclass(self, cls: ClassLevel) -> dict[str, Any] | None:
        """A subclass is found through its class — names repeat across classes."""
        wanted = cls.subclass
        if wanted is None:
            return None
        name = wanted.name.lower()
        # Its own class's, or one of a class it takes subclasses from.
        owners = [(cls.name.lower(), (cls.source or "").lower())]
        record = self.resolve("class", ChosenOption(name=cls.name, source=cls.source)) or {}
        for ref in record.get("includeSubclassesFrom") or []:
            other, _, source = str(ref).partition("|")
            owners.append((other.lower(), (source or "phb").lower()))
        for data in self.all_data("subclass"):
            class_name = str(data.get("className", "")).lower()
            class_source = str(data.get("classSource", "")).lower()
            if not any(
                class_name == o_name and (not o_source or class_source == o_source)
                for o_name, o_source in owners
            ):
                continue
            if name not in (
                str(data.get("name", "")).lower(),
                str(data.get("shortName", "")).lower(),
            ):
                continue
            if wanted.source and str(data.get("source", "")).lower() != wanted.source.lower():
                continue
            return data
        return None


# -- the build -------------------------------------------------------------------------


def build_sheet(
    character: PlayerCharacter,
    compendium: Any | None = None,
    *,
    source_set: SourceSet | None = None,
    revision: str | None = None,
    file_error: str | None = None,
    variant_encumbrance: bool = False,
) -> Sheet:
    """Derive the whole sheet for ``character``; ``variant_encumbrance`` where its world
    plays the PHB's optional rule."""
    corpus = _Corpus(compendium, source_set)
    sheet = Sheet(character=character, revision=revision, file_error=file_error)
    level = character.total_level
    sheet.level = level

    classes = [
        (cls, corpus.resolve("class", ChosenOption(name=cls.name, source=cls.source)))
        for cls in character.classes
    ]
    primary = classes[0][1] if classes else None
    species = corpus.resolve("race", character.species)
    subspecies = _subspecies(corpus, character)
    background = corpus.resolve("background", character.background)

    _identity(sheet, character, species, background)

    prof = _value(
        character,
        "proficiency_bonus",
        Derivation(proficiency_bonus(level), (f"level {level}",)) if level else None,
    )
    sheet.proficiency = prof
    prof_bonus = prof.value or 0

    _abilities(sheet, character, species, subspecies, primary, prof_bonus)
    mods = {a.key: a.modifier for a in sheet.abilities}

    _skills(sheet, character, corpus, mods, prof_bonus)
    training = _training(character, corpus, (species, subspecies))
    _armor_and_attacks(sheet, character, corpus, classes, mods, prof_bonus, training[1])

    sheet.initiative = _value(
        character,
        "initiative",
        Derivation(
            mods["dex"],
            (f"DEX {mods['dex']:+d}",),
            (("ability:dex", mods["dex"]),) if mods["dex"] else (),
        ),
    )
    sheet.speed = _value(character, "speed", _speed(species, subspecies))
    sheet.load = _load(sheet, character, species, subspecies, variant_encumbrance)
    _hit_points(sheet, character, classes, mods["con"])
    _spellcasting(sheet, character, corpus, classes, mods, prof_bonus)
    _features(sheet, character, corpus, classes, species, subspecies, background)
    # Features that change numbers (Aura of Protection, Defense, Dwarven Toughness…).
    # Imported here: traits reads the sheet's types, so it cannot be imported at the top.
    from app.services.traits import apply_traits

    apply_traits(sheet)
    sheet.edition = "2024" if (primary or {}).get("edition") == "one" else "2014"
    _exhaustion(sheet, character.state.exhaustion)
    _burden(sheet)
    _play_state(sheet, character, corpus)
    _wild_shape(sheet, character, corpus, classes)
    _proficiencies(sheet, character, classes, training)
    _weapon_master(sheet, character, corpus, classes, training[1])
    _defenses(sheet, character, corpus, species, subspecies)
    resilient = sum(1 for f in character.feats if f.name.lower() == RESILIENT)
    sheet.open_saves = max(0, resilient - len(character.saving_throws))
    return sheet


WEAPON_MASTER = ("weapon master", "xphb")


def _weapon_master(
    sheet: Sheet,
    character: PlayerCharacter,
    corpus: _Corpus,
    classes: list[tuple[ClassLevel, dict[str, Any] | None]],
    trained: Sequence[str],
) -> None:
    """Weapon Master (2024): one kind of simple or martial weapon whose mastery the
    character uses, chosen on the sheet and changed after a long rest."""
    sheet.weapon_master = any(
        (f.name.lower(), str((corpus.resolve("feat", f) or {}).get("source", f.source)).lower())
        == WEAPON_MASTER
        for f in character.feats
    )
    if not sheet.weapon_master:
        return
    sheet.mastery_kind = character.state.mastery_kind
    sheet.mastery_open = not character.state.mastery_set
    kinds: dict[str, str] = {}
    for item in corpus.all_data("baseitem"):
        if (
            item.get("mastery")
            and str(item.get("weaponCategory", "")).lower() in ("simple", "martial")
            and not item.get("age")
            and _weapon_proficient(item, classes, trained)
        ):
            kinds.setdefault(str(item["name"]).lower(), str(item["name"]))
    sheet.mastery_choices = sorted(kinds.values())


def _feat_chosen(reference: str, choices: dict[str, list[str]]) -> tuple[str, ...]:
    """What a feat's prose asked the player to choose, as the wizard kept it
    (``choices["feat.<name|source>.choice.<id>"]``, chargen/feat_choices.py)."""
    # Imported here: the chargen package reads the sheet.
    from app.services.chargen.rules import feat_choices

    return tuple(
        value
        for row in feat_choices(reference)
        for value in choices.get(f"feat.{reference.lower()}.choice.{row.id}") or []
    )


def _exhaustion(sheet: Sheet, level: int) -> None:
    """Exhaustion's lasting effects by edition. 2014: speed halved from level 2 and 0 from
    level 5, maximum HP halved from level 4. 2024: speed −5 ft. a level. (Its effect on
    rolls is the roll's: app/services/condition_rolls.py.)"""
    if not level:
        return

    def changed(value: Value, new: int, note: str) -> Value:
        if value.overridden or value.value is None:
            return value
        return Value(new, f"{value.explanation} · {note}" if value.explanation else note)

    speed = sheet.speed.value
    if speed is not None:
        if sheet.edition == "2024":
            sheet.speed = changed(sheet.speed, max(0, speed - 5 * level), f"exhaustion {level}")
        elif level >= 5:
            sheet.speed = changed(sheet.speed, 0, f"exhaustion {level}")
        elif level >= 2:
            sheet.speed = changed(sheet.speed, speed // 2, f"exhaustion {level}")
    top = sheet.hp_max.value
    if sheet.edition == "2014" and level >= 4 and top is not None:
        sheet.hp_max = changed(sheet.hp_max, max(1, top // 2), f"exhaustion {level}")


def _value(character: PlayerCharacter, key: str, derived: Derivation | None) -> Value:
    """The derived value, unless the file overrides it."""
    override = character.override(key)
    if override is not None:
        return Value(
            override.value,
            override.note,
            overridden=True,
            note=override.note,
            sources=((f"override:{key}", override.value),) if override.value else (),
        )
    if derived is None:
        return Value(None)
    return Value(derived.value, derived.explanation, sources=derived.sources)


def _identity(
    sheet: Sheet,
    character: PlayerCharacter,
    species: dict[str, Any] | None,
    background: dict[str, Any] | None,
) -> None:
    species_name = ""
    if character.subspecies is not None and character.species is not None:
        species_name = f"{character.subspecies.name} {character.species.name}"
    elif character.species is not None:
        species_name = character.species.name
    sheet.species_name = species_name
    parts = []
    for cls in character.classes:
        text = f"{cls.name} {cls.level}"
        if cls.subclass is not None:
            text += f" · {cls.subclass.name}"
        parts.append(text)
    sheet.class_line = " / ".join(parts)
    sheet.background_name = character.background.name if character.background else ""
    sheet.summary = " · ".join(
        p for p in (species_name, sheet.class_line, sheet.background_name) if p
    )


def _subspecies(corpus: _Corpus, character: PlayerCharacter) -> dict[str, Any] | None:
    if character.subspecies is None:
        return None
    return corpus.resolve("subrace", character.subspecies)


def _fixed_bonuses(record: dict[str, Any] | None) -> dict[str, int]:
    """Fixed ability bonuses of a species record; choices are the character's to state."""
    if not record:
        return {}
    options = record.get("ability") or []
    first = options[0] if options and isinstance(options[0], dict) else {}
    return {k: v for k, v in first.items() if k in ABILITIES and isinstance(v, int)}


def _abilities(
    sheet: Sheet,
    character: PlayerCharacter,
    species: dict[str, Any] | None,
    subspecies: dict[str, Any] | None,
    primary: dict[str, Any] | None,
    prof: int,
) -> None:
    species_bonus = _fixed_bonuses(species)
    for k, v in _fixed_bonuses(subspecies).items():
        species_bonus[k] = species_bonus.get(k, 0) + v
    save_profs = set((primary or {}).get("proficiency") or []) | {
        s.lower() for s in character.saving_throws
    }
    for key in ABILITIES:
        base = character.abilities.get(key)
        bonus = species_bonus.get(key, 0)
        improvement = character.improvement_total(key)
        derived: Derivation | None = None
        if base is not None:
            parts = [f"base {base}"]
            if bonus:
                parts.append(f"species {bonus:+d}")
            if improvement:
                parts.append(f"improvements {improvement:+d}")
            derived = Derivation(min(30, base + bonus + improvement), tuple(parts))
        score = _value(character, f"ability.{key}", derived)
        modifier = ability_modifier(score.value)
        proficient = key in save_profs
        save = _value(
            character,
            f"save.{key}",
            check_bonus(modifier, prof, proficient=proficient, label=key.upper(), ability=key),
        )
        sheet.abilities.append(
            AbilityView(key, ABILITY_NAMES[key], score, modifier, save, proficient)
        )


def _skills(
    sheet: Sheet, character: PlayerCharacter, corpus: _Corpus, mods: dict[str, int], prof: int
) -> None:
    table: dict[str, str] = {}
    for data in corpus.all_data("skill"):
        ability = data.get("ability")
        if isinstance(ability, str) and ability in ABILITIES:
            table[str(data.get("name", "")).lower()] = ability
    table = table or dict(FALLBACK_SKILLS)
    proficient = {o.name.lower() for o in character.skills}
    expert = {o.name.lower() for o in character.expertise}
    for key in sorted(table):
        ability = table[key]
        value = _value(
            character,
            f"skill.{key}",
            check_bonus(
                mods[ability],
                prof,
                proficient=key in proficient or key in expert,
                expertise=key in expert,
                label=ability.upper(),
                ability=ability,
            ),
        )
        sheet.skills.append(
            SkillView(
                key, key.title(), ability, value, key in proficient or key in expert, key in expert
            )
        )
    perception = next((s for s in sheet.skills if s.key == "perception"), None)
    base = (
        perception.value.value if perception and perception.value.value is not None else mods["wis"]
    )
    sheet.passive_perception = _value(
        character, "passive_perception", Derivation(10 + base, ("10", f"Perception {base:+d}"))
    )


def property_codes(item: dict[str, Any]) -> list[tuple[str, str]]:
    """An item's properties as ``(abbreviation, source)``: ``"V|XPHB"`` → ``("V",
    "XPHB")``; a bare code is the PHB's, and ``{"uid": "2H|XPHB", "note": …}`` (the
    Lance's "unless mounted") reads its ``uid``."""
    out: list[tuple[str, str]] = []
    for raw in item.get("property") or []:
        uid = raw.get("uid", "") if isinstance(raw, dict) else raw
        code, _, source = str(uid).partition("|")
        if code.strip():
            out.append((code.strip(), source.strip() or "PHB"))
    return out


def _first_word(value: Any) -> str:
    return str(value).split("|")[0].strip()


def _weapon_proficient(
    item: dict[str, Any],
    classes: list[tuple[ClassLevel, dict[str, Any] | None]],
    extra: Sequence[str] = (),
) -> bool:
    """Trained with this weapon: by a class, or by ``extra`` — the species', the feats'
    and the weapons a feat let the player choose (:func:`_training`)."""
    category = str(item.get("weaponCategory", "")).lower()
    name = str(item.get("name", "")).lower()
    for entry in [*_class_proficiencies(classes, "weapons"), *extra]:
        text = strip_tags(
            entry if isinstance(entry, str) else str(entry.get("proficiency", ""))
        ).lower()
        if category and category in text:
            return True
        if name and (text == name or text.rstrip("s") == name or name.rstrip("s") in text):
            return True
    return False


# The feat whose saving throw is a choice the file must record.
RESILIENT = "resilient"

# Item fields that add to numbers other than AC and a weapon's own attacks.
ITEM_BONUSES = ("bonusSavingThrow", "bonusSpellAttack", "bonusSpellSaveDc", "bonusAbilityCheck")


def _bonus(item: dict[str, Any], key: str) -> int:
    raw = item.get(key)
    try:
        return int(str(raw).replace("+", "")) if raw not in (None, "") else 0
    except ValueError:
        return 0


# Fixed training a species or feat grants, by the corpus's keys: armor kinds and the
# weapon categories (a named weapon is keyed "longsword|phb").
_ARMOR_KEYS = ("light", "medium", "heavy", "shield")
_WEAPON_CATEGORIES = ("simple", "martial")


def _training(
    character: PlayerCharacter,
    corpus: _Corpus,
    records: Sequence[dict[str, Any] | None],
) -> tuple[list[str], list[str], dict[str, str]]:
    """Armor and weapon training beyond the classes': the species' and subspecies'
    (an elf's longsword), the feats' (Heavily Armored, Martial Weapon Training) and the
    weapons a feat let the player choose (Weapon Master). Returns the armor words, the
    weapon words and, for a named weapon, its item reference."""
    armor: list[str] = []
    weapons: list[str] = []
    items: dict[str, str] = {}
    sources = [*records, *(corpus.resolve("feat", f) for f in character.feats)]
    for record in sources:
        for block in (record or {}).get("armorProficiencies") or []:
            if isinstance(block, dict):
                armor.extend(k for k in _ARMOR_KEYS if block.get(k) is True)
        for block in (record or {}).get("weaponProficiencies") or []:
            if not isinstance(block, dict):
                continue
            for key, value in block.items():
                if value is not True:
                    continue
                if key in _WEAPON_CATEGORIES:
                    weapons.append(key)
                elif "|" in key:
                    name = key.split("|")[0]
                    weapons.append(name)
                    items[name] = key
    for chosen in character.weapons:
        weapons.append(chosen.name.lower())
        items[chosen.name.lower()] = chosen.reference
    return list(dict.fromkeys(armor)), list(dict.fromkeys(weapons)), items


def _armor_and_attacks(
    sheet: Sheet,
    character: PlayerCharacter,
    corpus: _Corpus,
    classes: list[tuple[ClassLevel, dict[str, Any] | None]],
    mods: dict[str, int],
    prof: int,
    trained: Sequence[str] = (),
) -> None:
    armor = EquippedArmor()
    magic_ac = 0
    for index, entry in enumerate(character.inventory):
        data = corpus.item(entry.item)
        kind, summary, weight = "item", "", None
        if data is not None:
            item_type = _first_word(data.get("type", ""))
            weight = data.get("weight")
            if item_type in (LIGHT_ARMOR, MEDIUM_ARMOR, HEAVY_ARMOR):
                kind = "armor"
                summary = f"AC {data.get('ac', '?')}"
                if entry.equipped:
                    armor = EquippedArmor(
                        name=str(data.get("name")),
                        armor_type=item_type,
                        base_ac=int(data.get("ac", 10)) + _bonus(data, "bonusAc"),
                        shield_ac=armor.shield_ac,
                        stealth_disadvantage=bool(data.get("stealth")),
                    )
            elif item_type == SHIELD:
                kind = "shield"
                summary = f"AC +{data.get('ac', 2)}"
                if entry.equipped:
                    armor = EquippedArmor(
                        name=armor.name,
                        armor_type=armor.armor_type,
                        base_ac=armor.base_ac,
                        shield_ac=int(data.get("ac", 2)) + _bonus(data, "bonusAc"),
                        stealth_disadvantage=armor.stealth_disadvantage,
                    )
            elif data.get("dmg1"):
                kind = "weapon"
                summary = f"{data['dmg1']} {DAMAGE_TYPES.get(str(data.get('dmgType')), '')}".strip()
                if entry.equipped:
                    sheet.attacks.append(
                        _attack(entry.item.name, data, classes, mods, prof, trained)
                    )
            elif (
                entry.equipped
                and data.get("bonusAc")
                and (entry.attuned or not data.get("reqAttune"))
            ):
                # A ring or cloak of protection: worn and attuned, it adds to AC.
                magic_ac += _bonus(data, "bonusAc")
            if entry.equipped and (entry.attuned or not data.get("reqAttune")):
                # Worn — and attuned, if it asks for that — an item adds to saves, spell
                # attacks and DCs, checks (traits.apply_items).
                for key in ITEM_BONUSES:
                    if _bonus(data, key):
                        sheet.item_bonuses.setdefault(key, []).append(
                            (str(data.get("name")), _bonus(data, key))
                        )
        heal = ""
        potion = data is not None and str(data.get("type", "")).startswith("P")
        if potion and data is not None and "healing" in str(data.get("name", "")).lower():
            found = _DICE_TAG.search(str(data.get("entries")))
            heal = found.group(1).replace(" ", "") if found else ""
        sheet.inventory.append(
            ItemView(
                index,
                entry.item.name,
                entry.quantity,
                entry.equipped,
                entry.attuned,
                entry.notes,
                data is not None,
                kind,
                summary,
                float(weight) if isinstance(weight, int | float) else None,
                heal,
            )
        )

    unarmored = None
    primary = character.primary_class
    if primary is not None and primary.name.lower() in UNARMORED_DEFENSE and not armor.is_armored:
        unarmored = primary.name.lower()
    second = mods.get(UNARMORED_DEFENSE[unarmored][0], 0) if unarmored else 0
    derived = armor_class(armor, mods["dex"], unarmored_class=unarmored, second_modifier=second)
    if magic_ac:
        derived = Derivation(derived.value + magic_ac, (*derived.parts, f"items +{magic_ac}"))
    sheet.armor_class = _value(character, "ac", derived)
    sheet.armor_type = armor.armor_type if armor.is_armored else None
    sheet.shield_bonus = armor.shield_ac

    # Everyone can punch: 1 + STR, proficient (PHB "Unarmed Strike").
    sheet.attacks.append(
        AttackView(
            "Unarmed Strike",
            Value(
                mods["str"] + prof,
                f"STR {mods['str']:+d} + prof {prof:+d}",
                sources=tuple(
                    s for s in (("ability:str", mods["str"]), ("proficiency", prof)) if s[1]
                ),
            ),
            str(max(0, 1 + mods["str"])),
            "bludgeoning",
        )
    )


def _attack(
    name: str,
    item: dict[str, Any],
    classes: list[tuple[ClassLevel, dict[str, Any] | None]],
    mods: dict[str, int],
    prof: int,
    trained: Sequence[str] = (),
) -> AttackView:
    codes = property_codes(item)
    props = [code for code, _source in codes]
    ranged = _first_word(item.get("type", "")) == "R"
    if "F" in props:
        ability = "dex" if mods["dex"] >= mods["str"] else "str"
    else:
        ability = "dex" if ranged else "str"
    magic = _bonus(item, "bonusWeapon")
    proficient = _weapon_proficient(item, classes, trained)
    to_hit = mods[ability] + (prof if proficient else 0) + magic
    parts = [f"{ability.upper()} {mods[ability]:+d}"]
    if proficient:
        parts.append(f"prof {prof:+d}")
    if magic:
        parts.append(f"magic {magic:+d}")
    damage_bonus = mods[ability] + magic
    damage = f"{item['dmg1']}{damage_bonus:+d}" if damage_bonus else str(item["dmg1"])
    versatile = None
    if item.get("dmg2") and "V" in props:
        versatile = f"{item['dmg2']}{damage_bonus:+d}" if damage_bonus else str(item["dmg2"])
    sources = (
        (f"ability:{ability}", mods[ability]),
        ("proficiency", prof if proficient else 0),
        (f"item:{name}", magic),
    )
    return AttackView(
        name,
        Value(to_hit, " + ".join(parts), sources=tuple(s for s in sources if s[1])),
        damage,
        DAMAGE_TYPES.get(str(item.get("dmgType")), ""),
        tuple(WEAPON_PROPERTIES[p] for p in props if p in WEAPON_PROPERTIES),
        str(item.get("range") or ""),
        versatile,
        str((item.get("mastery") or [""])[0]),
        8 + mods[ability] + prof,
        tuple(c for c in codes if c[0] in WEAPON_PROPERTIES),
        kind=_kind_of(item),
    )


def _kind_of(item: dict[str, Any]) -> str:
    """A weapon's kind: its base item's name for a magic one ("longsword|dmg" → Longsword)."""
    base = str(item.get("baseItem") or "").split("|")[0]
    return base.title() if base else str(item.get("name", ""))


COINS_PER_POUND = 50


def _load(
    sheet: Sheet,
    character: PlayerCharacter,
    species: dict[str, Any] | None,
    subspecies: dict[str, Any] | None,
    variant: bool = False,
) -> Load | None:
    """Carried weight and carrying capacity. Every species the books print is Small or
    Medium, which carry alike; Powerful Build counts as one size larger (twice as
    much). None without a Strength score."""
    strength = next((a.score.value for a in sheet.abilities if a.key == "str"), None)
    if strength is None:
        return None
    powerful = any(_has_entry(record, "Powerful Build") for record in (species, subspecies))
    capacity = strength * 15 * (2 if powerful else 1)
    items = sum(i.weight * i.quantity for i in sheet.inventory if i.weight is not None)
    unweighed = sum(1 for i in sheet.inventory if i.weight is None)
    coins = sum(character.currency.model_dump().values()) / COINS_PER_POUND
    size = 2 if powerful else 1
    return Load(
        round(items, 2),
        round(coins, 2),
        capacity,
        capacity * 2,
        powerful,
        unweighed,
        variant,
        strength * 5 * size,
        strength * 10 * size,
    )


def _burden(sheet: Sheet) -> None:
    """Variant encumbrance's speed: −10 ft. encumbered, −20 ft. heavily (its disadvantage
    is the roll's, ``condition_rolls``)."""
    load, speed = sheet.load, sheet.speed
    if load is None or not load.burden or speed.value is None or speed.overridden:
        return
    less = 10 * load.burden
    note = "heavily encumbered" if load.burden == 2 else "encumbered"
    sheet.speed = Value(
        max(0, speed.value - less),
        f"{speed.explanation} · {note} −{less} ft" if speed.explanation else note,
    )


def _has_entry(record: dict[str, Any] | None, name: str) -> bool:
    """True when ``record``'s entries hold one named ``name`` (a species trait)."""
    entries = (record or {}).get("entries") or []
    return any(isinstance(e, dict) and e.get("name") == name for e in entries)


def _speed(species: dict[str, Any] | None, subspecies: dict[str, Any] | None) -> Derivation | None:
    for record in (subspecies, species):
        if not record or "speed" not in record:
            continue
        speed = record["speed"]
        if isinstance(speed, int):
            return Derivation(speed, (f"{speed} ft",))
        if isinstance(speed, dict) and isinstance(speed.get("walk"), int):
            return Derivation(speed["walk"], (f"{speed['walk']} ft",))
    return None


def _hit_points(
    sheet: Sheet,
    character: PlayerCharacter,
    classes: list[tuple[ClassLevel, dict[str, Any] | None]],
    con: int,
) -> None:
    """Maximum HP from the ``hit_points`` choice, then the override, then the last saved.

    Each character level contributes one hit-die value — the full die at first level and
    the average after (``average``), or what was rolled (``rolled``) — and CON is added
    per level on top, so a CON change applies to every level at once.
    """
    choice = character.hit_points
    rolled = choice.method is HitPointMethod.ROLLED
    pools: dict[int, int] = {}
    # One hit die per character level, in the order the classes are listed.
    dice: list[int | None] = []
    for cls, record in classes:
        faces = ((record or {}).get("hd") or {}).get("faces")
        if isinstance(faces, int):
            pools[faces] = pools.get(faces, 0) + cls.level
        dice.extend([faces if isinstance(faces, int) else None] * cls.level)

    total = 0
    complete = bool(dice)
    averaged = 0
    for index, faces in enumerate(dice):
        if rolled and index < len(choice.per_level):
            total += choice.per_level[index]
        elif faces is None:
            complete = False
        elif index == 0:
            total += faces
        else:
            total += faces // 2 + 1
            averaged += 1 if rolled else 0
    derived = None
    if complete and sheet.level:
        if rolled:
            stated = min(len(choice.per_level), len(dice))
            parts = [f"rolled {sum(choice.per_level[:stated])} ({stated} levels)"]
            if averaged:
                parts.append(f"{averaged} levels at average")
        else:
            first = dice[0] or 0
            parts = [f"d{first} max {first}"]
            if len(dice) > 1:
                parts.append(f"{len(dice) - 1} levels at average")
        parts.append(f"CON {con:+d} × {sheet.level}")
        derived = Derivation(max(1, total + con * sheet.level), tuple(parts))
    elif character.state.hp_max is not None:
        # Nothing to compute from — a homebrew class with no rolls stated. The last
        # maximum the file recorded is better than none.
        derived = Derivation(character.state.hp_max, ("last saved maximum",))

    sheet.hp_max = _value(character, "hp_max", derived)
    used = character.state.hit_dice_used
    sheet.hit_dice = [
        HitDicePool(f, n, min(n, used.get(f"d{f}", 0)))
        for f, n in sorted(pools.items(), reverse=True)
    ]
    top = sheet.hp_max.value or 0
    current = character.state.hp
    sheet.hp_current = top if current is None else min(current, top) if top else current
    sheet.temp_hp = character.state.temp_hp


def _slot_progression(
    record: dict[str, Any] | None, groups_key: str, level: int
) -> tuple[int, ...] | None:
    for group in (record or {}).get(groups_key) or []:
        rows = group.get("rowsSpellProgression")
        if rows and 1 <= level <= len(rows):
            return tuple(int(x) for x in rows[level - 1])
    return None


def _pact(record: dict[str, Any] | None, level: int) -> tuple[int, int] | None:
    """Warlock pact magic: (slots, slot level) from the class table's columns."""
    for group in (record or {}).get("classTableGroups") or []:
        labels = [strip_tags(str(label)).lower() for label in group.get("colLabels") or []]
        if "spell slots" in labels and "slot level" in labels:
            rows = group.get("rows") or []
            if not 1 <= level <= len(rows):
                return None
            row = rows[level - 1]
            count = row[labels.index("spell slots")]
            slot_level = re.search(r"\d", strip_tags(str(row[labels.index("slot level")])))
            if isinstance(count, int) and slot_level:
                return count, int(slot_level.group())
    return None


def _caster_level(progression: str | None, level: int) -> int:
    if progression == "full":
        return level
    if progression == "1/2":
        return level // 2
    if progression == "1/3":
        return level // 3
    if progression == "artificer":
        return math.ceil(level / 2)
    return 0


def _spellcasting(
    sheet: Sheet,
    character: PlayerCharacter,
    corpus: _Corpus,
    classes: list[tuple[ClassLevel, dict[str, Any] | None]],
    mods: dict[str, int],
    prof: int,
) -> None:
    casters: list[tuple[str | None, tuple[int, ...] | None, int]] = []
    ability: str | None = None
    pact: tuple[int, int] | None = None
    for cls, record in classes:
        sub = corpus.subclass(cls)
        progression = (record or {}).get("casterProgression")
        if progression == "pact":
            pact = _pact(record, cls.level)
            ability = ability or (record or {}).get("spellcastingAbility")
            continue
        own = None
        if progression:
            own = _slot_progression(record, "classTableGroups", cls.level)
            ability = ability or (record or {}).get("spellcastingAbility")
        elif sub and sub.get("casterProgression"):
            progression = sub["casterProgression"]
            own = _slot_progression(sub, "subclassTableGroups", cls.level)
            ability = ability or sub.get("spellcastingAbility")
        if progression:
            casters.append((progression, own, _caster_level(progression, cls.level)))
        preparing = _preparing(cls, record, mods)
        if preparing is not None:
            sheet.preparing.append(preparing)

    totals: tuple[int, ...] = ()
    if len(casters) == 1 and casters[0][1] is not None:
        totals = casters[0][1]
    elif casters:
        totals = CASTER_SLOTS.get(sum(c[2] for c in casters), ())

    used = character.state.slots_used
    for index in range(9):
        level = index + 1
        total = totals[index] if index < len(totals) else 0
        override = character.override(f"slots.{level}")
        if override is not None:
            total = override.value
        if total > 0:
            sheet.slots.append(SlotLevel(level, total, min(total, used.get(str(level), 0))))

    pact_total = character.override("pact_slots")
    pact_level = character.override("pact_slot_level")
    if pact is not None or pact_total is not None:
        count = pact_total.value if pact_total else pact[0] if pact else 0
        slot_level = pact_level.value if pact_level else pact[1] if pact else 1
        if count > 0:
            sheet.pact = SlotLevel(slot_level, count, min(count, character.state.pact_slots_used))

    if ability in ABILITIES:
        sheet.spellcasting_ability = ability
        sheet.spell_save_dc = _value(character, "spell_save_dc", spell_save_dc(prof, mods[ability]))
        sheet.spell_attack = _value(
            character, "spell_attack", spell_attack_bonus(prof, mods[ability], ability)
        )
    elif character.override("spell_save_dc") or character.override("spell_attack"):
        sheet.spell_save_dc = _value(character, "spell_save_dc", None)
        sheet.spell_attack = _value(character, "spell_attack", None)

    always = _always_prepared(corpus, classes)
    always_names = {o.name.lower() for o in always}
    free = _class_spell_uses(sheet, character, corpus, classes)
    sheet.prepared_count = sum(
        1 for o in character.spells.prepared if o.name.lower() not in always_names
    )
    seen: set[str] = set()
    named: set[str] = set()
    for option in character.spells.usable:
        key = option.reference.lower()
        if key in seen:
            continue
        seen.add(key)
        named.add(option.name.lower())
        view = _spell_view(option, corpus.resolve("spell", option), sheet.level)
        if option.name.lower() in always_names:
            view = replace(view, always=True, free=free.get(option.name.lower(), ""))
        sheet.spells.append(view)
    # The class's and subclass's own spells, unless the file already lists them (by any
    # printing).
    for option in always:
        if option.name.lower() in named:
            continue
        named.add(option.name.lower())
        view = _spell_view(option, corpus.resolve("spell", option), sheet.level)
        sheet.spells.append(replace(view, always=True, free=free.get(option.name.lower(), "")))
    for option in _subclass_cantrips(corpus, classes):
        if option.name.lower() in named:
            continue
        view = _spell_view(option, corpus.resolve("spell", option), sheet.level)
        sheet.spells.append(replace(view, always=True))
    _granted(sheet, character, corpus, mods, prof)
    sheet.spells.sort(key=lambda s: (s.level, s.name.lower()))


def _granted(
    sheet: Sheet,
    character: PlayerCharacter,
    corpus: _Corpus,
    mods: dict[str, int],
    prof: int,
) -> None:
    """Spells a species, background or feat grants (app/services/granted_spells.py): the
    ones given outright at the character's level, and those chosen (``spells.granted``),
    each cast with its grant's ability; a spell castable without a slot once per rest
    counts its castings as a resource ("Bless (Magic Initiate)")."""
    entries = {g.source.lower(): g for g in character.spells.granted}
    species = corpus.resolve("race", character.species)
    subspecies = corpus.resolve("subrace", character.subspecies)
    background = corpus.resolve("background", character.background)
    # A feat a species or background grants as a version: "magic initiate; cleric|xphb".
    versions: dict[str, str] = {}
    for record in (species, subspecies, background):
        for block in (record or {}).get("feats") or []:
            for key in block if isinstance(block, dict) else ():
                name = str(key).partition("|")[0]
                base, _, version = name.partition(";")
                if version.strip():
                    versions[base.strip().lower()] = version.strip()
    lineage = next(iter(character.choices.get("lineage") or ()), None)
    sources: list[tuple[str, str, dict[str, Any] | None, str | None]] = [
        ("species", character.species.name if character.species else "", species, lineage),
        (
            "subspecies",
            character.subspecies.name if character.subspecies else "",
            subspecies,
            None,
        ),
        (
            "background",
            character.background.name if character.background else "",
            background,
            None,
        ),
    ]
    for feat in character.feats:
        data = corpus.resolve("feat", feat)
        if data is not None:
            reference = f"{data.get('name', feat.name)}|{data.get('source', feat.source or '')}"
            sources.append((f"feat:{reference}", feat.name, data, versions.get(feat.name.lower())))
    present = {s.reference.lower() for s in sheet.spells}
    for key, origin, data, variant in sources:
        found = granted_spells.blocks(data)
        entry = entries.get(key.lower())
        block = granted_spells.chosen_block(found, (entry.from_list if entry else None) or variant)
        if block is None:
            continue
        ability = (entry.ability if entry else None) or block.ability
        ability = ability or sheet.spellcasting_ability or ""
        if ability in ABILITIES and ability not in sheet.granted_casting:
            sheet.granted_casting[ability] = (
                _value(character, "-", spell_save_dc(prof, mods[ability])),
                _value(character, "-", spell_attack_bonus(prof, mods[ability], ability)),
            )
        chosen_lines = [line for _i, line in block.choices(sheet.level)]
        given = [(ChosenOption.model_validate(x.spell), x) for x in block.fixed(sheet.level)]
        picked = [(o, None) for o in (entry.spells if entry else [])]
        for option, line in [*given, *picked]:
            if option.reference.lower() in present:
                continue
            present.add(option.reference.lower())
            view = _spell_view(option, corpus.resolve("spell", option), sheet.level)
            if line is None:  # a chosen spell: the choice it answers, by its level
                line = next(
                    (
                        x
                        for x in chosen_lines
                        if x.choice and x.choice.cantrips_only == (view.level == 0)
                    ),
                    chosen_lines[0] if chosen_lines else None,
                )
            free = ""
            if line is not None and view.level > 0:
                if line.use == "will":
                    free = "will"
                elif line.use in ("daily", "rest"):
                    free = f"{view.name} ({origin})"
                    times = prof if line.times < 0 else max(1, line.times)
                    used = min(times, character.state.resources_used.get(free, 0))
                    recharge = Recharge.SHORT if line.use == "rest" else Recharge.LONG
                    sheet.resources.append(ResourceView(free, times, used, recharge))
            sheet.spells.append(replace(view, origin=origin, ability=ability, free=free))


def _preparing(
    cls: ClassLevel, record: dict[str, Any] | None, mods: dict[str, int]
) -> Preparing | None:
    """The class's preparation after a long rest, if it prepares that way.

    ``preparedSpellsChange: "restLong"`` says so; a 2014 wizard states no change but
    has a spellbook (``spellsKnownProgressionFixed``), and prepares from it daily. The
    count is the class's ``preparedSpellsProgression`` (2024) or its ``preparedSpells``
    formula (2014) at the class's own level.
    """
    data = record or {}
    if not data.get("casterProgression"):
        return None
    book = bool(data.get("spellsKnownProgressionFixed"))
    change = data.get("preparedSpellsChange")
    if not (change == "restLong" or (book and change is None)):
        return None
    limit: int | None = None
    progression = data.get("preparedSpellsProgression")
    if isinstance(progression, list) and 0 < cls.level <= len(progression):
        limit = int(progression[cls.level - 1])
    elif isinstance(data.get("preparedSpells"), str):
        limit = prepared_spells(data["preparedSpells"], cls.level, mods)
    if not limit:
        return None
    return Preparing(cls.name, cls.level, limit, book)


def _always_prepared(
    corpus: _Corpus, classes: list[tuple[ClassLevel, dict[str, Any] | None]]
) -> list[ChosenOption]:
    """The spells each class and its subclass always have prepared, up to the class's
    level (``granted_spells.always_prepared``)."""
    out: list[ChosenOption] = []
    for cls, record in classes:
        for spell in granted_spells.always_prepared(
            record, corpus.subclass(cls), cls.level, cls.options.get("spells")
        ):
            name, _sep, source = spell.partition("|")
            out.append(ChosenOption(name=name, source=source.upper() or None))
    return out


def _class_spell_uses(
    sheet: Sheet,
    character: PlayerCharacter,
    corpus: _Corpus,
    classes: list[tuple[ClassLevel, dict[str, Any] | None]],
) -> dict[str, str]:
    """A class spell castable without a slot so many times a day (a death knight's
    Rune Strike once per long rest), by lowercased name: its resource, added to the
    sheet, as ``SpellView.free``; ``"will"`` for one cast at will."""
    out: dict[str, str] = {}
    for cls, record in classes:
        for line in granted_spells.class_grants(record, cls.level):
            option = ChosenOption.model_validate(line.spell)
            name = str((corpus.resolve("spell", option) or {}).get("name") or option.name)
            if line.use == "will":
                out[name.lower()] = "will"
            elif line.use in ("daily", "rest") and not line.cantrip:
                label = f"{name} ({cls.name})"
                times = (sheet.proficiency.value or 2) if line.times < 0 else max(1, line.times)
                used = min(times, character.state.resources_used.get(label, 0))
                recharge = Recharge.SHORT if line.use == "rest" else Recharge.LONG
                sheet.resources.append(ResourceView(label, times, used, recharge))
                out[name.lower()] = label
    return out


def _subclass_cantrips(
    corpus: _Corpus, classes: list[tuple[ClassLevel, dict[str, Any] | None]]
) -> list[ChosenOption]:
    """The cantrip taken where a subclass offers one of several (a Path of the Giant's)."""
    out = []
    for cls, _record in classes:
        wanted = cls.options.get("cantrip", "").lower()
        for spell in granted_spells.cantrip_choice(corpus.subclass(cls)):
            if wanted and spell.split("|")[0].split("#")[0].lower() == wanted:
                out.append(ChosenOption.model_validate(spell))
    return out


_TIME_UNITS = {
    "action": "Action",
    "bonus": "Bonus action",
    "reaction": "Reaction",
    "minute": "min",
    "hour": "h",
}


_DICE_TAG = re.compile(r"\{@dice ([^}|]+)")
_SCALE_DICE_FULL = re.compile(r"\{@scaledice ([^|}]+)\|[^|}]+\|([^|}]+)\}")
_FIXED_HEAL = re.compile(r"regains? (\d+) hit points", re.IGNORECASE)
# "up to six creatures of your choice": how many one casting heals.
_UP_TO = re.compile(r"\bup to (\w+) creatures", re.IGNORECASE)
_NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
_SCALE_DAMAGE = re.compile(r"\{@scaledamage [^|}]+\|[^|}]+\|([^|}]+)\}")
_DICE_PART = re.compile(r"(\d*)d(\d+)(.*)")


def _scaled(base: str, step: str, extra: int) -> str:
    """``base`` with ``extra`` more of ``step``: 8d6 + 2×1d6 is 10d6; 70 + 2×10 is 90."""
    if not step or extra <= 0:
        return base
    if base.isdigit() and step.isdigit():
        return str(int(base) + extra * int(step))
    here, more = _DICE_PART.fullmatch(base), _DICE_PART.fullmatch(step)
    if here and more and here.group(2) == more.group(2) and not more.group(3):
        count = int(here.group(1) or 1) + extra * int(more.group(1) or 1)
        return f"{count}d{here.group(2)}{here.group(3)}"
    return "+".join([base, *[step] * extra])


def damage_at(spell: SpellView, slot: int) -> str | None:
    """A spell's damage cast with a slot of ``slot``: Fireball at 5th is 10d6."""
    base = (spell.damage or "").replace(" ", "")
    if not base or not spell.upcast or slot <= spell.level:
        return spell.damage
    return _scaled(base, spell.upcast, slot - spell.level)


def heal_at(spell: SpellView, slot: int, modifier: int) -> str | None:
    """A healing spell's roll at ``slot``: Cure Wounds at 2nd is 2d8, plus the caster's
    spellcasting modifier where the spell adds it."""
    if not spell.heal:
        return None
    dice = _scaled(spell.heal, spell.heal_upcast, slot - spell.level if slot else 0)
    return f"{dice}{modifier:+d}" if spell.heal_mod and modifier else dice


def spell_view(data: dict[str, Any], level: int = 1) -> SpellView:
    """A spell's facts — level, school, time, range, components, duration — from its data.

    ``level`` is the caster's character level, which picks a cantrip's damage step.
    """
    name, source = str(data.get("name", "")), str(data.get("source", ""))
    return _spell_view(ChosenOption(name=name, source=source or None), data, level)


def _spell_view(option: ChosenOption, data: dict[str, Any] | None, level: int) -> SpellView:
    if data is None:
        return SpellView(option.reference, option.name, found=False, source=option.source)
    time = ""
    for t in data.get("time") or []:
        unit, number = t.get("unit", ""), t.get("number", 1)
        time = (
            _TIME_UNITS.get(unit, unit)
            if unit in ("action", "bonus", "reaction")
            else f"{number} {_TIME_UNITS.get(unit, unit)}"
        )
        break
    rng = data.get("range") or {}
    distance = rng.get("distance") or {}
    dtype = distance.get("type", "")
    if dtype in ("feet", "miles"):
        range_text = f"{distance.get('amount')} {'ft' if dtype == 'feet' else 'mi'}"
    else:
        range_text = {
            "self": "Self",
            "touch": "Touch",
            "sight": "Sight",
            "unlimited": "Unlimited",
        }.get(dtype, dtype.title())
    if rng.get("type") in ("cone", "line", "radius", "sphere", "cube", "emanation") and range_text:
        range_text = f"Self ({range_text} {rng['type']})"
    comps = data.get("components") or {}
    components = "".join(k.upper() for k in ("v", "s", "m") if comps.get(k))
    material = comps.get("m")
    material_text = (
        material.get("text")
        if isinstance(material, dict)
        else material
        if isinstance(material, str)
        else None
    )
    concentration = False
    duration = ""
    for d in data.get("duration") or []:
        concentration = bool(d.get("concentration"))
        kind = d.get("type")
        if kind == "instant":
            duration = "Instant"
        elif kind == "timed":
            amount = d.get("duration") or {}
            duration = f"{amount.get('amount', '')} {amount.get('type', '')}".strip()
        elif kind == "permanent":
            duration = "Until dispelled"
        else:
            duration = str(kind or "").title()
        break
    damage = None
    scaling = data.get("scalingLevelDice")
    if isinstance(scaling, list):
        scaling = scaling[0] if scaling else None
    if isinstance(scaling, dict):
        steps = scaling.get("scaling") or {}
        for threshold in sorted(steps, key=int):
            if level >= int(threshold):
                damage = steps[threshold]
    if damage is None:
        damage = first_dice(data.get("entries"))
    save = (data.get("savingThrow") or [None])[0]
    text = paragraphs(data.get("entries")) + paragraphs(data.get("entriesHigherLevel"))
    scale = _SCALE_DAMAGE.search(str(data.get("entriesHigherLevel") or ""))
    heal = heal_upcast = ""
    heal_targets = 1
    words = str(data.get("entries"))
    if "HL" in (data.get("miscTags") or []) and "divide" not in words.lower():
        # Dice ({@dice 1d8}), or a fixed amount (Heal's 70: in its text, or as the base
        # of its scaling tag in 2024's wording).
        first = _DICE_TAG.search(words)
        step = _SCALE_DICE_FULL.search(str(data.get("entriesHigherLevel") or ""))
        fixed = _FIXED_HEAL.search(words)
        if first:
            heal = first.group(1).replace(" ", "")
        elif fixed:
            heal = fixed.group(1)
        elif step and step.group(1).strip().isdigit():
            heal = step.group(1).strip()
        heal_upcast = step.group(2).replace(" ", "") if step else ""
        group = _UP_TO.search(words)
        heal_targets = _NUMBERS.get(group.group(1).lower(), 1) if group else 1
    return SpellView(
        reference=option.reference,
        name=str(data.get("name", option.name)),
        found=True,
        source=str(data.get("source", option.source or "")),
        level=int(data.get("level", 0)),
        school=SCHOOLS.get(str(data.get("school")), ""),
        time=time,
        range=range_text,
        components=components,
        duration=duration,
        concentration=concentration,
        ritual=bool((data.get("meta") or {}).get("ritual")),
        save=str(save)[:3].upper() if save else None,
        attack=bool(data.get("spellAttack")),
        damage=damage,
        upcast=scale.group(1).replace(" ", "") if scale else "",
        heal=heal,
        heal_upcast=heal_upcast,
        heal_mod="spellcasting ability modifier" in str(data.get("entries")).lower(),
        heal_targets=heal_targets,
        damage_types=tuple(data.get("damageInflict") or ()),
        material=material_text,
        text=tuple(text),
        entries=(*(data.get("entries") or []), *(data.get("entriesHigherLevel") or [])),
        higher_from=len(data.get("entries") or []),
        school_code=str(data.get("school") or ""),
        time_data=tuple(data.get("time") or ()),
        range_data=dict(data.get("range") or {}),
        duration_data=tuple(data.get("duration") or ()),
    )


def _named_entries(
    record: dict[str, Any] | None,
    origin: str,
    *,
    skip: frozenset[str] = frozenset(),
    only_prefix: str | None = None,
    kind: str = "",
    category: str = "",
) -> list[FeatureView]:
    out: list[FeatureView] = []
    source = str((record or {}).get("source", ""))
    key = translation_key(category, record) if record and category else ""
    for index, entry in enumerate((record or {}).get("entries") or []):
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        name = strip_tags(str(entry["name"]))
        if name.lower() in skip:
            continue
        if only_prefix and not name.lower().startswith(only_prefix):
            continue
        out.append(
            FeatureView(
                name,
                origin,
                None,
                tuple(paragraphs(entry.get("entries"))),
                source,
                kind,
                entries=tuple(entry.get("entries") or ()),
                text_at=(category, key, f"entries/{index}") if key else None,
            )
        )
    return out


def _features(
    sheet: Sheet,
    character: PlayerCharacter,
    corpus: _Corpus,
    classes: list[tuple[ClassLevel, dict[str, Any] | None]],
    species: dict[str, Any] | None,
    subspecies: dict[str, Any] | None,
    background: dict[str, Any] | None,
) -> None:
    class_features = corpus.all_data("classFeature")
    subclass_features = corpus.all_data("subclassFeature")
    for cls, record in classes:
        if record is None:
            continue
        source = str(record.get("source", cls.source or "")).lower()
        wanted: list[tuple[str, FeatureRef]] = []
        first_subclass_pointer = True
        opted_in = {o.name.lower() for o in character.optional_features}
        for fref in feature_refs(record):
            name, lvl, pointer = fref.name, fref.level, fref.gains_subclass
            if lvl > cls.level or name.lower() in _SKIP_FEATURES:
                continue
            # A feature from another book than the class (Tasha's "Cantrip Formulas" on
            # the PHB wizard) is an optional variant: shown only when the character
            # took it, listed in optional_features.
            if is_variant(fref, record) and name.lower() not in opted_in:
                continue
            if pointer and not first_subclass_pointer:
                continue  # "Arcane Tradition feature" — the subclass's own features follow
            first_subclass_pointer = first_subclass_pointer and not pointer
            wanted.append((name, fref))
        for name, fref in wanted:
            lvl = fref.level
            # Filed under the class the reference names: a class on another's frame
            # borrows its features (class_refs).
            match = next((f for f in class_features if fref.matches(f)), None)
            sheet.features.append(
                FeatureView(
                    name,
                    f"{cls.name} {lvl}",
                    lvl,
                    tuple(paragraphs((match or {}).get("entries"))),
                    str((match or record).get("source", "")),
                    "class",
                    cls.name,
                    entries=tuple((match or {}).get("entries") or ()),
                    text_at=("classFeature", translation_key("classFeature", match), "")
                    if match
                    else None,
                )
            )
            # A choice the feature offers (a 2024 cleric's Divine Order): the option taken.
            chosen = cls.options.get(name)
            for block in feature_options.option_blocks((match or {}).get("entries")):
                ref = next((r for r in block.options if r.name == chosen), None)
                taken = next(
                    (
                        f
                        for f in class_features
                        if ref is not None
                        and str(f.get("name")) == ref.name
                        and f.get("level") == ref.level
                        and str(f.get("className", "")).lower() == fref.class_name
                    ),
                    None,
                )
                if taken is not None and ref is not None:
                    sheet.features.append(
                        FeatureView(
                            ref.name,
                            f"{cls.name} {lvl} · {name}",
                            lvl,
                            tuple(paragraphs(taken.get("entries"))),
                            str(taken.get("source", "")),
                            "class",
                            cls.name,
                            entries=tuple(taken.get("entries") or ()),
                            text_at=("classFeature", translation_key("classFeature", taken), ""),
                        )
                    )
        sub = corpus.subclass(cls)
        if sub is not None:
            short = str(sub.get("shortName", sub.get("name", ""))).lower()
            owner = subclass_owner(sub, cls.name, source)
            picked = sorted(
                (
                    f
                    for f in subclass_features
                    if files_under(f, owner) and int(f.get("level", 99)) <= cls.level
                ),
                key=lambda f: int(f.get("level", 0)),
            )
            # Of a choice (Hunter's Prey), only the option taken; none until one is.
            passed_over = {
                (ref.name.lower(), ref.level)
                for f in picked
                for block in feature_options.option_blocks(f.get("entries"))
                for ref in block.options
                if ref.name != cls.options.get(str(f.get("name")))
            }
            picked = [
                f
                for f in picked
                if (str(f.get("name", "")).lower(), f.get("level")) not in passed_over
            ]
            for f in picked:
                sheet.features.append(
                    FeatureView(
                        str(f.get("name")),
                        f"{sub.get('name', short)} {f.get('level')}",
                        int(f.get("level", 0)),
                        tuple(paragraphs(f.get("entries"))),
                        str(f.get("source", "")),
                        "subclass",
                        cls.name,
                        entries=tuple(f.get("entries") or ()),
                        text_at=("subclassFeature", translation_key("subclassFeature", f), ""),
                    )
                )
    species_label = sheet.species_name or (character.species.name if character.species else "")
    sheet.features.extend(
        _named_entries(
            species, species_label, skip=_SKIP_SPECIES_TRAITS, kind="species", category="race"
        )
    )
    sheet.features.extend(
        _named_entries(
            subspecies,
            species_label,
            skip=_SKIP_SPECIES_TRAITS,
            kind="species",
            category="subrace",
        )
    )
    if background is not None:
        sheet.features.extend(
            _named_entries(
                background,
                sheet.background_name,
                only_prefix="feature",
                kind="background",
                category="background",
            )
        )
    nth_taking: dict[str, int] = {}
    times: dict[str, int] = {}
    for feat in character.feats:
        times[feat.name.lower()] = times.get(feat.name.lower(), 0) + 1
    for feat in character.feats:
        data = corpus.resolve("feat", feat)
        feat_chosen = _feat_chosen(
            f"{data.get('name')}|{data.get('source')}" if data else feat.reference,
            character.choices,
        )
        if times[feat.name.lower()] > 1:
            # A feat taken twice (Elemental Adept): each taking shows its own choice.
            nth = nth_taking.get(feat.name.lower(), 0)
            nth_taking[feat.name.lower()] = nth + 1
            feat_chosen = feat_chosen[nth : nth + 1]
        sheet.features.append(
            FeatureView(
                feat.name,
                "Feat",
                None,
                tuple(paragraphs((data or {}).get("entries"))),
                str((data or {}).get("source", feat.source or "")),
                "feat",
                entries=tuple((data or {}).get("entries") or ()),
                text_at=("feat", translation_key("feat", data), "") if data else None,
                chosen=feat_chosen,
            )
        )
    # Chosen options — a fighting style, an invocation, a maneuver. Class features from
    # another book (Tasha's) are listed above when opted into; anything else chosen
    # here is an option record of its own.
    class_feature_names = {f.name.lower() for f in sheet.features}
    for option in character.optional_features:
        if option.name.lower() in class_feature_names:
            continue
        data = corpus.resolve("optionalfeature", option)
        sheet.features.append(
            FeatureView(
                option.name,
                "Option",
                None,
                tuple(paragraphs((data or {}).get("entries"))),
                str((data or {}).get("source", option.source or "")),
                "option",
                entries=tuple((data or {}).get("entries") or ()),
                text_at=("optionalfeature", translation_key("optionalfeature", data), "")
                if data
                else None,
            )
        )


def _play_state(sheet: Sheet, character: PlayerCharacter, corpus: _Corpus) -> None:
    state = character.state
    active = set(state.conditions)
    for key in CONDITIONS:
        data = corpus.resolve("condition", ChosenOption(name=key))
        sheet.conditions.append(
            ConditionView(
                key, key.title(), key in active, tuple(paragraphs((data or {}).get("entries")))
            )
        )
    for resource in character.resources:
        used = min(resource.max, state.resources_used.get(resource.name, 0))
        sheet.resources.append(ResourceView(resource.name, resource.max, used, resource.recharge))
    if state.concentration is not None:
        sheet.concentration = sheet.spell(state.concentration.reference) or _spell_view(
            state.concentration, corpus.resolve("spell", state.concentration), sheet.level
        )


def _wild_shape(
    sheet: Sheet,
    character: PlayerCharacter,
    corpus: _Corpus,
    classes: list[tuple[ClassLevel, dict[str, Any] | None]],
) -> None:
    """Wild Shape: its uses as a resource, the forms, and the form the druid is in —
    whose AC and speed the sheet then shows (the 2024 Circle of the Moon's 13 + WIS where
    that is higher)."""
    rules = next(
        (
            found
            for cls, record in classes
            if (found := wild_shape.rules_for(cls, record, corpus.subclass(cls))) is not None
        ),
        None,
    )
    if rules is None:
        return
    state = character.state
    used = min(rules.uses, state.resources_used.get(wild_shape.RESOURCE, 0))
    sheet.resources.append(
        ResourceView(
            wild_shape.RESOURCE,
            rules.uses,
            used,
            Recharge.LONG if rules.edition == "2024" else Recharge.SHORT,
            short_regain=1 if rules.edition == "2024" else 0,
        )
    )
    forms = tuple(corpus.wild_forms(rules))
    known = tuple(
        f for option in character.wild_shapes if (f := wild_shape.find(forms, option)) is not None
    )
    form = wild_shape.find(forms, state.wild_shape) or (
        _outside_form(corpus, state.wild_shape) if state.wild_shape is not None else None
    )
    sheet.wild_shape = WildShapeView(
        rules,
        forms,
        known,
        form,
        min(state.wild_shape_hp, form.hp)
        if form is not None and state.wild_shape_hp is not None
        else None,
    )
    if form is None:
        return
    label = f"{form.name} form"
    ac = form.ac
    if rules.moon and rules.edition == "2024":
        moon_ac = 13 + sheet.ability("wis").modifier
        if ac is None or moon_ac > ac:
            ac, label = moon_ac, f"{form.name} form, Circle Forms 13 + WIS"
    if ac is not None:
        sheet.armor_class = Value(ac, label)
    speed = form.data.get("speed")
    walk = speed.get("walk") if isinstance(speed, dict) else None
    walk = walk.get("number") if isinstance(walk, dict) else walk
    if isinstance(walk, int):
        sheet.speed = Value(walk, f"{form.name} form")


def _outside_form(corpus: _Corpus, option: ChosenOption) -> wild_shape.Form | None:
    """A form the druid is in that the rules no longer list (a book removed, a level
    lost): still shown, so it can be left."""
    data = corpus.resolve("monster", option)
    if data is None:
        return None
    cr = data.get("cr")
    cr_text = cr.get("cr") if isinstance(cr, dict) else cr
    return wild_shape.Form(str(data.get("name")), str(data.get("source")), str(cr_text or ""), data)


def _defenses(
    sheet: Sheet,
    character: PlayerCharacter,
    corpus: _Corpus,
    species: dict[str, Any] | None,
    subspecies: dict[str, Any] | None,
) -> None:
    """Resistances, immunities, vulnerabilities the records type as plain damage types.

    A choice the file does not record (a dragonborn's ancestry, Boon of Energy
    Resistance) and a feature's prose (Rage) are left out: guessing would be worse.
    """
    sources: list[tuple[dict[str, Any] | None, str]] = [
        (species, str((species or {}).get("name", ""))),
        (subspecies, str((subspecies or {}).get("name", ""))),
    ]
    sources += [(corpus.resolve("feat", feat), feat.name) for feat in character.feats]
    for entry in character.inventory:
        data = corpus.item(entry.item)
        if data is not None and entry.equipped and (entry.attuned or not data.get("reqAttune")):
            sources.append((data, str(data.get("name", entry.item.name))))
    chosen = [r.lower() for r in character.resistances]
    for data, origin in sources:
        for kind in DEFENSE_KINDS:
            for value in (data or {}).get(kind) or []:
                if isinstance(value, str):
                    sheet.defenses.append(Defense(kind, value.lower(), origin))
                elif kind == "resist" and isinstance(value, dict):
                    _chosen_resistance(sheet, value.get("choose"), origin, chosen)
    if character.state.raging and sheet.has_feature("Rage"):
        for damage in RAGE_RESISTS:
            sheet.defenses.append(Defense("resist", damage, "Rage"))


# What raging resists (2014 and 2024 alike).
RAGE_RESISTS = ("bludgeoning", "piercing", "slashing")


def _chosen_resistance(sheet: Sheet, choose: Any, origin: str, chosen: list[str]) -> None:
    """A "choose from" resistance: the file's picks, in order, fill it; what is left is
    asked on the sheet."""
    if not isinstance(choose, dict):
        return
    options = tuple(str(o).lower() for o in choose.get("from") or [])
    wanted = int(choose.get("count", 1))
    for damage in list(chosen):
        if wanted and damage in options:
            sheet.defenses.append(Defense("resist", damage, origin))
            chosen.remove(damage)
            wanted -= 1
    sheet.open_resistances.extend((origin, options) for _ in range(wanted))


_ITEM_TAG = re.compile(r"\{@item ([^|}]+)")


def _proficiencies(
    sheet: Sheet,
    character: PlayerCharacter,
    classes: list[tuple[ClassLevel, dict[str, Any] | None]],
    training: tuple[list[str], list[str], dict[str, str]] = ([], [], {}),
) -> None:
    sheet.languages = [o.name for o in character.languages]
    sheet.tools = [o.name for o in character.tools]
    for attr, key in (("armor_proficiencies", "armor"), ("weapon_proficiencies", "weapons")):
        names: dict[str, str] = {}
        for entry in _class_proficiencies(classes, key):
            raw = entry if isinstance(entry, str) else str(entry.get("proficiency", ""))
            name = strip_tags(raw)
            names.setdefault(name.lower(), name)
            item = _ITEM_TAG.match(raw)
            if item:
                sheet.training_items[name.lower()] = item.group(1)
        # Beyond the classes': the species', the feats' and the weapons chosen.
        for name in training[0] if key == "armor" else training[1]:
            names.setdefault(name.lower(), name)
        setattr(sheet, attr, list(names.values()))
    for name, reference in training[2].items():
        sheet.training_items.setdefault(name, reference.split("|")[0])


def _class_proficiencies(
    classes: list[tuple[ClassLevel, dict[str, Any] | None]], key: str
) -> list[Any]:
    """Armor or weapon proficiencies: the first class's in full, a later class's reduced.

    A class taken by multiclassing grants only its ``multiclassing.proficienciesGained``
    (a Fighter adds light and medium armor, shields and martial weapons).
    """
    out: list[Any] = []
    for index, (_cls, record) in enumerate(classes):
        if index == 0:
            block = (record or {}).get("startingProficiencies") or {}
        else:
            block = ((record or {}).get("multiclassing") or {}).get("proficienciesGained") or {}
        out.extend(block.get(key) or [])
    return out


__all__ = [
    "ABILITY_NAMES",
    "CASTER_SLOTS",
    "CONDITIONS",
    "AbilityView",
    "AttackView",
    "ConditionView",
    "FeatureView",
    "HitDicePool",
    "ItemView",
    "Preparing",
    "ResourceView",
    "Sheet",
    "SkillView",
    "SlotLevel",
    "SpellView",
    "Value",
    "build_sheet",
    "spell_view",
]
