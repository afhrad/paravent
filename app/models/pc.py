"""The player character record — and the character file format.

This is **library** data: each character is one JSON file in ``library/characters/``,
written by the server and equally editable by hand (documentation/CHARACTER-FORMAT.md).
The format is the serialisation of :class:`PlayerCharacter`, so this module *is* the
specification; ``documentation/character.schema.json`` is generated from it.

The central decision is unchanged from the wizard design: the record stores **chosen
options and play state, not resolved values** (documentation/CHARGEN.md §4). A choice is
kept as the reference the player picked — ``"Wizard|PHB"`` — and AC, maximum HP, save
bonuses and the rest are derived on demand in :mod:`app.services.sheet`. That means:

* A corpus update or a change to the world's declared books **re-derives** the character
  rather than leaving a stale number behind.
* Back-navigation in the wizard is free: the choice stack *is* the record.
* No top-level field can hold a wrong derived number.

What the rules engine cannot derive — a Ring of Protection, a homebrew class, a character
imported from paper — is stated explicitly in ``overrides``, each with a note saying
why. An override is visible on the sheet as such, never silently mixed in.

Hand-editing is a first-class use, so references accept a compact string form
(``"Fireball|PHB"``, or just ``"Arcana"`` where no book is needed) and are written back in
that form.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import (
    ConfigDict,
    Field,
    GetJsonSchemaHandler,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue, SkipJsonSchema
from pydantic_core import CoreSchema

from app.models.base import ABILITIES
from app.models.gamestate import GameStateModel, GivenName, Record, _now

FORMAT_VERSION = 1

# How many dice-log entries a character file keeps. Enough for a combat's worth of
# rolls; few enough that the file stays readable by hand.
LOG_LIMIT = 50


class DraftStatus(StrEnum):
    """How far along a character is.

    A partly built character is a real, saved thing — it has a name in the list and it
    resumes (documentation/CHARGEN-WIZARD.md §1). ``DRAFT`` is therefore an ordinary
    state, not an error.
    """

    DRAFT = "draft"
    READY = "ready"
    RETIRED = "retired"


class AbilityMethod(StrEnum):
    """How the player generated their ability scores.

    Kept on the record because it changes what the wizard may still edit: a rolled score
    is a commitment, a point-buy score can be respent while the budget allows.
    """

    STANDARD = "standard"
    POINTBUY = "pointbuy"
    ROLL = "roll"
    MANUAL = "manual"


class Abilities(GameStateModel):
    """The six base scores, before any species, background or improvement increase.

    Serialises under the **upstream** keys (``str``, ``int``), not the aliased field
    names, so a hand-edited file reads the way the corpus does.

    Base only, deliberately. Increases are themselves stored choices, so the total is
    derived and the player can always be shown base, bonus and total separately.
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        validate_assignment=True,
        serialize_by_alias=True,
    )

    str_: int | None = Field(default=None, alias="str")
    dex: int | None = None
    con: int | None = None
    int_: int | None = Field(default=None, alias="int")
    wis: int | None = None
    cha: int | None = None

    @field_validator("str_", "dex", "con", "int_", "wis", "cha")
    @classmethod
    def _within_range(cls, value: int | None) -> int | None:
        """Reject scores outside the 1–30 rule ceiling."""
        if value is not None and not 1 <= value <= 30:
            raise ValueError(f"ability score {value} outside 1–30")
        return value

    def get(self, ability: str) -> int | None:
        """One score by its 5eTools key (``"str"``, ``"int"``, …)."""
        return getattr(self, _field_for(ability), None)

    def as_dict(self) -> dict[str, int | None]:
        """All six, keyed as the corpus keys them."""
        return {ability: self.get(ability) for ability in ABILITIES}

    @property
    def is_complete(self) -> bool:
        """True once every score has a value."""
        return all(self.get(ability) is not None for ability in ABILITIES)


def _split_reference(value: str) -> tuple[str, str | None]:
    name, _, source = value.partition("|")
    return name.strip(), (source.strip() or None)


class ChosenOption(GameStateModel):
    """A reference to a corpus entry: ``"Fireball|PHB"``, or a bare ``"Arcana"``.

    ``source`` is kept because a bare name does not identify an entry — two thirds of
    spell names exist in more than one book (documentation/TECHSTACK.md §6c). A name with
    no source is still valid: it resolves against the world's declared books, and for
    things like skills and languages no book is needed at all.

    In the file it is a single string, which is what a person editing by hand writes.
    """

    name: str
    source: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_reference(cls, data: Any) -> Any:
        if isinstance(data, str):
            name, source = _split_reference(data)
            return {"name": name, "source": source}
        return data

    @model_serializer(mode="plain")
    def _to_reference(self) -> str:
        return self.reference

    @classmethod
    def __get_pydantic_json_schema__(
        cls, _core: CoreSchema, _handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {
            "type": "string",
            "description": "A reference: 'Name|SOURCE', or 'Name' alone.",
            "examples": ["Fireball|PHB", "Arcana"],
        }

    @property
    def reference(self) -> str:
        """The ``name|SOURCE`` form the resolver takes."""
        return f"{self.name}|{self.source}" if self.source else self.name


# ``choices["prepared.<Class>"]``: a class's prepared spells, when more than one class
# prepares and one of them from a spellbook.
PREPARED_PREFIX = "prepared."


class GrantedSpells(GameStateModel):
    """Spells a feat, species or background grants, as chosen (``additionalSpells``).

    ``source`` names the grant: ``"feat:Magic Initiate|XPHB"``, ``"species"``,
    ``"subspecies"`` or ``"background"``. ``list`` (``from_list``) is the block chosen
    where the record offers several ("Cleric Spells"), ``ability`` the spellcasting
    ability where the player picks it, ``spells`` the spells picked. Spells the grant
    gives outright (a tiefling's Thaumaturgy) are not listed: the sheet adds them, like
    a domain's spells.
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
        serialize_by_alias=True,
    )

    source: str
    # ``list`` in the file; named apart here so it does not shadow the builtin.
    from_list: str | None = Field(default=None, alias="list")
    ability: str | None = None
    spells: list[ChosenOption] = Field(default_factory=list)

    @field_validator("ability")
    @classmethod
    def _ability(cls, value: str | None) -> str | None:
        if value is not None and value not in ABILITIES:
            raise ValueError(f"not an ability: {value} (use str, dex, con, int, wis or cha)")
        return value


class SpellSelection(GameStateModel):
    """A character's spells, split the way the rules split them.

    Cantrips are always available. For a spellbook caster (a wizard), ``known`` is the
    book and ``prepared`` the subset usable today — 24 in the book, 13 prepared.
    A known-spells caster (a sorcerer) lists ``known`` only; a caster who prepares from
    the whole class list (a cleric) lists ``prepared`` only.
    """

    cantrips: list[ChosenOption] = Field(default_factory=list)
    known: list[ChosenOption] = Field(default_factory=list)
    prepared: list[ChosenOption] = Field(default_factory=list)
    # Spells from feats, species and backgrounds, each with its own ability. Left out of
    # the file while empty, so files without any read as they always did.
    granted: list[GrantedSpells] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def _without_empty_granted(self, handler: SerializerFunctionWrapHandler) -> Any:
        data = handler(self)
        if isinstance(data, dict) and not data.get("granted"):
            data.pop("granted", None)
        return data

    @property
    def usable(self) -> list[ChosenOption]:
        """What can be cast today: cantrips plus prepared, else cantrips plus known."""
        return [*self.cantrips, *(self.prepared or self.known)]


class ClassLevel(GameStateModel):
    """One class a character has levels in, with its subclass once chosen.

    A list of these, rather than a single class plus a level, because multiclassing is
    the same engine and not a special case. In the file:
    ``{"class": "Wizard|PHB", "level": 10, "subclass": "Evocation|PHB"}``.

    ``options`` holds the class's and subclass's choices between features, by the
    feature that offers them — ``{"Hunter's Prey": "Colossus Slayer"}``,
    ``{"Totem Spirit": "Bear", "Aspect of the Beast": "Eagle"}`` — and, under
    ``"spells"``, the subclass spell list where it offers several (a Circle of the Land's
    "Coast").
    """

    name: str
    source: str | None = None
    level: int = Field(default=1, ge=1, le=20)
    subclass: ChosenOption | None = None
    options: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _from_compact(cls, data: Any) -> Any:
        if isinstance(data, dict) and "class" in data:
            data = dict(data)
            name, source = _split_reference(str(data.pop("class")))
            data["name"], data["source"] = name, source
        return data

    @model_serializer(mode="plain")
    def _to_compact(self) -> dict[str, Any]:
        out: dict[str, Any] = {"class": self.reference, "level": self.level}
        if self.subclass is not None:
            out["subclass"] = self.subclass.reference
        if self.options:
            out["options"] = dict(self.options)
        return out

    @classmethod
    def __get_pydantic_json_schema__(
        cls, _core: CoreSchema, _handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        return {
            "type": "object",
            "required": ["class"],
            "additionalProperties": False,
            "properties": {
                "class": {"type": "string", "examples": ["Wizard|PHB"]},
                "level": {"type": "integer", "minimum": 1, "maximum": 20, "default": 1},
                "subclass": {"type": "string", "examples": ["Evocation|PHB"]},
                "options": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "examples": [{"Hunter's Prey": "Colossus Slayer", "spells": "Coast"}],
                },
            },
        }

    @property
    def reference(self) -> str:
        """The ``name|SOURCE`` form the resolver takes."""
        return f"{self.name}|{self.source}" if self.source else self.name


class InventoryItem(GameStateModel):
    """Something the character carries. ``item`` may be a corpus reference or free text.

    ``equipped`` matters to the rules: equipped armour and shields set AC, and equipped
    weapons appear as attacks.
    """

    item: ChosenOption
    quantity: int = Field(default=1, ge=0)
    equipped: bool = False
    attuned: bool = False
    notes: str = ""


class Currency(GameStateModel):
    """Coins, by denomination, as a character sheet lists them."""

    cp: int = Field(default=0, ge=0)
    sp: int = Field(default=0, ge=0)
    ep: int = Field(default=0, ge=0)
    gp: int = Field(default=0, ge=0)
    pp: int = Field(default=0, ge=0)


class Recharge(StrEnum):
    """When a limited-use resource comes back."""

    SHORT = "short"
    LONG = "long"
    NONE = "none"


class Resource(GameStateModel):
    """A limited-use feature: Arcane Recovery, Channel Divinity, Rage, Ki points…

    Declared on the character rather than derived, because the corpus states most of
    these maxima only in prose. Usage is play state and lives in ``state``.
    """

    name: str
    max: int = Field(ge=0)
    recharge: Recharge = Recharge.LONG


class HitPointMethod(StrEnum):
    """How a character gains hit points per level after the first."""

    AVERAGE = "average"
    ROLLED = "rolled"


class HitPoints(GameStateModel):
    """How the character's hit points were gained — a choice, like the ability method.

    ``average``: the first level takes the full hit die, every later level its average
    (``die / 2 + 1``) — what most tables use, and computable from the class alone.

    ``rolled``: ``per_level`` holds the hit-die result for each character level in the
    order taken, the first normally the full die. CON is **not** included: it is added
    for every level when the maximum is computed, so a CON change applies retroactively,
    as the rules require. A level without an entry yet counts as the average.

    The maximum itself is derived (and written to ``state.hp_max`` for readability).
    """

    method: HitPointMethod = HitPointMethod.AVERAGE
    per_level: list[int] = Field(default_factory=list, max_length=20)

    @field_validator("per_level")
    @classmethod
    def _die_results(cls, value: list[int]) -> list[int]:
        bad = [v for v in value if not 1 <= v <= 12]
        if bad:
            raise ValueError(f"a hit die result must be 1–12, not {bad[0]}")
        return value


class DeathSaves(GameStateModel):
    """Successes and failures while at 0 HP."""

    successes: int = Field(default=0, ge=0, le=3)
    failures: int = Field(default=0, ge=0, le=3)


_HIT_DIE = re.compile(r"^d(4|6|8|10|12|20)$")
_SLOT_LEVEL = re.compile(r"^[1-9]$")


class PlayState(GameStateModel):
    """What changes at the table. Everything here is reset or spent by play.

    ``hp`` absent means "at full health", so a new or hand-written character needs no
    knowledge of its own maximum; the server writes the number on the next change.
    """

    hp: int | None = Field(default=None, ge=0)
    # Written by the server on every change: the maximum as the sheet computed it, so the
    # file shows "41 of 62" without anyone doing the arithmetic. Not an input — edit
    # ``hit_points`` or ``overrides.hp_max`` instead. Used only as a last resort when the
    # maximum cannot be computed at all.
    hp_max: int | None = Field(default=None, ge=0)
    temp_hp: int = Field(default=0, ge=0)
    hit_dice_used: dict[str, int] = Field(default_factory=dict)
    slots_used: dict[str, int] = Field(default_factory=dict)
    pact_slots_used: int = Field(default=0, ge=0)
    resources_used: dict[str, int] = Field(default_factory=dict)
    conditions: list[str] = Field(default_factory=list)
    exhaustion: int = Field(default=0, ge=0, le=6)
    concentration: ChosenOption | None = None
    inspiration: bool = False
    # A barbarian's Rage: resistance to bludgeoning, piercing and slashing while it lasts.
    raging: bool = False
    # Weapon Master (2024): the kind of weapon whose mastery the character uses
    # ("Longsword"), "" until chosen; once chosen it stays until a long rest opens it.
    mastery_kind: str = ""
    mastery_set: bool = False
    death_saves: DeathSaves = Field(default_factory=DeathSaves)

    @field_validator("hit_dice_used")
    @classmethod
    def _hit_die_keys(cls, value: dict[str, int]) -> dict[str, int]:
        bad = [k for k in value if not _HIT_DIE.match(k)]
        if bad:
            raise ValueError(f"not a hit die: {', '.join(bad)} (use 'd6', 'd8', …)")
        return {k: max(0, v) for k, v in value.items()}

    @field_validator("slots_used")
    @classmethod
    def _slot_keys(cls, value: dict[str, int]) -> dict[str, int]:
        bad = [k for k in value if not _SLOT_LEVEL.match(k)]
        if bad:
            raise ValueError(f"not a spell level: {', '.join(bad)} (use '1'–'9')")
        return {k: max(0, v) for k, v in value.items()}

    @field_validator("conditions")
    @classmethod
    def _conditions_lowercase(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for name in value:
            key = name.strip().lower()
            if key and key not in seen:
                seen.append(key)
        return seen


# Values a person may state by hand when the rules engine cannot derive them.
OVERRIDE_KEYS = re.compile(
    r"^(ac|hp_max|speed|initiative|spell_save_dc|spell_attack|passive_perception"
    r"|proficiency_bonus|pact_slots|pact_slot_level"
    r"|(ability|save)\.(str|dex|con|int|wis|cha)|skill\.[a-z][a-z ']*|slots\.[1-9])$"
)


class Override(GameStateModel):
    """A hand-stated value, with the reason. ``15`` alone is accepted as shorthand."""

    value: int
    note: str = ""

    @model_validator(mode="before")
    @classmethod
    def _from_number(cls, data: Any) -> Any:
        if isinstance(data, int) and not isinstance(data, bool):
            return {"value": data}
        return data

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        full = handler.resolve_ref_schema(handler(core))
        return {
            "anyOf": [{"type": "integer"}, full],
            "examples": [15, {"value": 15, "note": "Ring of Protection"}],
        }


class LogKind(StrEnum):
    """What a log entry records. The screen phrases it in the viewer's language."""

    CHECK = "check"
    SAVE = "save"
    SKILL = "skill"
    INITIATIVE = "initiative"
    ATTACK = "attack"
    DAMAGE = "damage"
    CAST = "cast"
    CONCENTRATION = "concentration"
    DEATH_SAVE = "death_save"
    HIT_DIE = "hit_die"
    REST = "rest"
    HEAL = "heal"
    CUSTOM = "custom"


class LogEntry(GameStateModel):
    """One roll or notable action, kept so every screen showing the character sees it.

    The log is shared between viewers who may read different languages, so an entry
    stores *what* happened (``kind`` + ``subject``: ``"save"`` + ``"dex"``) rather than an
    English sentence; each screen phrases it itself.
    """

    at: datetime = Field(default_factory=_now)
    kind: LogKind = LogKind.CUSTOM
    subject: str = ""
    detail: str = ""
    total: int | None = None
    outcome: Literal["", "crit", "fumble", "success", "failure"] = ""
    by: str = ""


class PlayerCharacter(Record):
    """A player character, stored as the choices that produced it plus its play state."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_assignment=True,
        serialize_by_alias=True,
    )

    # Optional pointer to the JSON Schema, for editors that validate as you type.
    schema_ref: str | None = Field(default=None, alias="$schema")
    format: Literal[1] = 1  # FORMAT_VERSION; bumped only with a migration

    # -- ownership and lifecycle --------------------------------------------------
    name: str = ""
    # The parts of a name Paravent made, which a child's name can read (NAMES.md §3).
    given_name: GivenName | None = None
    # The player this character belongs to, by **username** — readable and typeable in a
    # hand-edited file. A name with no account yet simply waits: the character appears
    # for that player once the gamemaster creates the account. Renaming a player renames
    # it here too (app/services/players.py).
    player: str | None = None
    world_id: str | None = None
    status: DraftStatus = DraftStatus.DRAFT
    # Levels the gamemaster has granted and the player has not taken yet: while above
    # 0 the sheet offers "Level up" (documentation/CHARGEN-WIZARD.md §9).
    pending_levels: int = Field(default=0, ge=0, le=19)
    # Experience points, once the table tracks them (an award after a fight starts it at
    # the current level's threshold). None: levels come from the gamemaster alone.
    experience: int | None = Field(default=None, ge=0)

    # Which wizard step to resume at. Stored rather than inferred because "the first
    # incomplete step" is not the same as "where the player left off".
    wizard_step: str | None = None
    # The wizard's answers, one list per question it asked (``"class.skills"``,
    # ``"asi.4"``, ``"opt.Fighting Style"``). The lists below — skills, feats,
    # improvements — are derived from these while the wizard runs; keeping the answers
    # says which question each came from, so an earlier change can show what it made
    # invalid instead of silently dropping it (documentation/CHARGEN-WIZARD.md §6).
    # Empty for a character written by hand.
    choices: dict[str, list[str]] = Field(default_factory=dict)

    # -- the choices --------------------------------------------------------------
    species: ChosenOption | None = None
    subspecies: ChosenOption | None = None
    background: ChosenOption | None = None
    classes: list[ClassLevel] = Field(default_factory=list)

    hit_points: HitPoints = Field(default_factory=HitPoints)

    ability_method: AbilityMethod | None = None
    abilities: Abilities = Field(default_factory=Abilities)
    # Rolled results are kept verbatim so a roll stays a commitment.
    ability_rolls: list[int] = Field(default_factory=list)

    # Every chosen increase, one entry per occasion: Ability Score Improvements, a 2024
    # background's +2/+1, a species' "+1 to two of your choice". Fixed species bonuses
    # (a 2014 elf's +2 DEX) are read from the corpus and are not listed here.
    ability_improvements: list[dict[str, int]] = Field(default_factory=list)

    skills: list[ChosenOption] = Field(default_factory=list)
    # Saving throw proficiencies beyond the first class's: the ability a Resilient feat
    # chose (``["con"]``). Abilities by their three-letter key.
    saving_throws: list[str] = Field(default_factory=list)
    # Damage resistances picked where the species or a feat offers a choice (a
    # dragonborn's ancestry, Boon of Energy Resistance): ``["fire"]``.
    resistances: list[str] = Field(default_factory=list)
    expertise: list[ChosenOption] = Field(default_factory=list)
    tools: list[ChosenOption] = Field(default_factory=list)
    languages: list[ChosenOption] = Field(default_factory=list)
    # Weapons a feat let the player choose proficiency with (Weapon Master's four):
    # ``["Longsword|PHB"]``. Training a class, species or feat grants outright is derived.
    weapons: list[ChosenOption] = Field(default_factory=list)
    feats: list[ChosenOption] = Field(default_factory=list)
    optional_features: list[ChosenOption] = Field(default_factory=list)

    spells: SpellSelection = Field(default_factory=SpellSelection)

    inventory: list[InventoryItem] = Field(default_factory=list)
    currency: Currency = Field(default_factory=Currency)
    resources: list[Resource] = Field(default_factory=list)

    # Prerequisites that are free prose and cannot be machine-checked, confirmed by
    # hand (documentation/CHARGEN.md G4).
    confirmed_prerequisites: list[str] = Field(default_factory=list)

    # -- identity -----------------------------------------------------------------
    pronouns: str = ""
    appearance: str = ""
    backstory: str = ""
    deity: ChosenOption | None = None
    notes: str = ""

    # -- play ---------------------------------------------------------------------
    state: PlayState = Field(default_factory=PlayState)
    overrides: dict[str, Override] = Field(
        default_factory=dict,
        json_schema_extra={"propertyNames": {"pattern": OVERRIDE_KEYS.pattern}},
    )
    # The character's rolls before the log (documentation/PROTOCOL.md §8): read from an
    # older file, copied into the log on start, never written again.
    log: SkipJsonSchema[list[LogEntry]] = Field(default_factory=list, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _legacy_player_id(cls, data: Any) -> Any:
        """Read files written before ownership was a username.

        Format 1 first stored ``player_id`` (an account id). It is carried over as-is;
        the registry rewrites an id to its username on load, where the accounts are
        known (:meth:`app.services.characters.CharacterService.adopt_legacy_owners`).
        """
        if isinstance(data, dict) and "player_id" in data:
            data = dict(data)
            legacy = data.pop("player_id")
            data.setdefault("player", legacy)
        return data

    @model_validator(mode="after")
    def _prepared_must_be_known(self) -> PlayerCharacter:
        """When a spellbook exists, prepared spells must come from it.

        With ``known`` empty the character prepares from the class list, which is not
        stored, so there is nothing to check against. A character who prepares from a
        book *and* a class list (a wizard who is also a cleric) records each class's
        spells under ``choices["prepared.<Class>"]``; spells recorded there may come from
        outside the book (CHARGEN-WIZARD.md §10).
        """
        spells = self.spells
        if not spells.known:
            return self
        allowed = {option.name.lower() for option in spells.known}
        for key, values in self.choices.items():
            if key.startswith(PREPARED_PREFIX):
                allowed |= {ChosenOption.model_validate(v).name.lower() for v in values}
        unknown = [o.name for o in spells.prepared if o.name.lower() not in allowed]
        if unknown:
            raise ValueError(f"prepared but not known: {', '.join(sorted(unknown))}")
        return self

    def belongs_to(self, username: str) -> bool:
        """True when ``username`` is this character's player (case-insensitive)."""
        return self.player is not None and self.player.casefold() == username.casefold()

    @field_validator("ability_improvements")
    @classmethod
    def _improvements_name_real_abilities(cls, value: list[dict[str, int]]) -> list[dict[str, int]]:
        """Reject an improvement naming something that is not an ability."""
        for entry in value:
            unknown = set(entry) - set(ABILITIES)
            if unknown:
                raise ValueError(f"not an ability: {', '.join(sorted(unknown))}")
        return value

    @field_validator("overrides")
    @classmethod
    def _override_keys(cls, value: dict[str, Override]) -> dict[str, Override]:
        """Only values the sheet knows how to show may be overridden."""
        normalized = {key.strip().lower(): v for key, v in value.items()}
        bad = [key for key in normalized if not OVERRIDE_KEYS.match(key)]
        if bad:
            raise ValueError(f"cannot override: {', '.join(sorted(bad))}")
        return normalized

    @field_validator("log")
    @classmethod
    def _log_capped(cls, value: list[LogEntry]) -> list[LogEntry]:
        """Newest first, at most :data:`LOG_LIMIT` entries."""
        return value[:LOG_LIMIT]

    @property
    def total_level(self) -> int:
        """Character level: the sum across every class."""
        return sum(entry.level for entry in self.classes)

    @property
    def primary_class(self) -> ClassLevel | None:
        """The first class taken, which the sheet leads with."""
        return self.classes[0] if self.classes else None

    @property
    def is_multiclassed(self) -> bool:
        """True once a second class is taken."""
        return len(self.classes) > 1

    def class_level(self, class_name: str) -> int:
        """Levels in one class, 0 when it is not taken."""
        lowered = class_name.lower()
        return sum(c.level for c in self.classes if c.name.lower() == lowered)

    def improvement_total(self, ability: str) -> int:
        """Points added to one ability across every improvement taken."""
        return sum(entry.get(ability, 0) for entry in self.ability_improvements)

    def override(self, key: str) -> Override | None:
        """The hand-stated value for ``key``, if any."""
        return self.overrides.get(key)

    @property
    def is_spellcaster(self) -> bool:
        """True when any spell has been chosen.

        Deliberately a fact about the record, not a rules question: whether a class
        *can* cast is read from the class data, which lives in the compendium.
        """
        return bool(self.spells.cantrips or self.spells.known or self.spells.prepared)


def character_json_schema() -> dict[str, Any]:
    """The JSON Schema of a character file, for editors that validate as you type.

    Written to ``documentation/character.schema.json`` by ``python -m app.cli schema``;
    a test keeps the committed copy in step with this model.
    """
    schema = PlayerCharacter.model_json_schema(mode="validation", by_alias=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Paravent character"
    schema["description"] = (
        "One player character. Choices and play state are stored; AC, HP maximum, "
        "bonuses and spell slots are derived unless stated in 'overrides'. "
        "See documentation/CHARACTER-FORMAT.md."
    )
    return schema


def _field_for(ability: str) -> str:
    """Map a 5eTools ability key to its model field (``str``/``int`` are builtins)."""
    return f"{ability}_" if ability in ("str", "int") else ability


__all__ = [
    "PREPARED_PREFIX",
    "FORMAT_VERSION",
    "HitPointMethod",
    "HitPoints",
    "LOG_LIMIT",
    "OVERRIDE_KEYS",
    "Abilities",
    "AbilityMethod",
    "character_json_schema",
    "ChosenOption",
    "ClassLevel",
    "Currency",
    "DeathSaves",
    "DraftStatus",
    "InventoryItem",
    "LogEntry",
    "LogKind",
    "Override",
    "PlayState",
    "PlayerCharacter",
    "Recharge",
    "Resource",
    "SpellSelection",
]
