"""What the wizard may offer: the corpus, filtered by the world's books.

Every list the player picks from comes through here (documentation/CHARGEN-WIZARD.md §4).
Unlike a reference in prose, a choice is deliberate: where a table declares both the 2014
and the 2024 books, **both printings are offered** — supersession, the right default for
resolving a bare tag, is the wrong one for a choice the player is making (CHARGEN.md §4,
trap #1). So the filter here is the declared books alone.

Species and backgrounds are offered in the **chosen class's edition** when the books hold
both: a 2014 species carries ability increases and a 2024 background does too, and the
rules never meant a character to take both. A world declaring only one edition is
unaffected.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, Literal

from app.models.compendium import Fluff
from app.models.gamestate import SourceSet
from app.models.pc import ChosenOption
from app.services.creature_sight import fluff_image
from app.services.translation_targets import fluff_category
from app.services.translations import record_key
from app.store.fluff import FluffStore
from app.store.lookups import LookupTables

Edition = Literal["classic", "one"]

# Tool kinds as the corpus types them, and what an "any … tool" grant draws from.
TOOL_TYPES = {
    "artisan": ("AT",),
    "instrument": ("INS",),
    "gaming": ("GS",),
    "any": ("AT", "INS", "GS", "T"),
}

# What a shop for starting gear leaves out: vehicles and bulk trade goods.
_NOT_SOLD = frozenset({"AIR", "SHP", "SPC", "VEH", "TG"})
# The shop's shelves by item kind; anything else is adventuring gear.
_SHELVES = {
    **dict.fromkeys(("LA", "MA", "HA", "S"), "armor"),
    **dict.fromkeys(("M", "R", "A", "AF"), "weapons"),
    **dict.fromkeys(("AT", "GS", "INS", "T"), "tools"),
    **dict.fromkeys(("MNT", "TAH"), "mounts"),
    "FD": "food",
}
SHELVES = ("armor", "weapons", "gear", "tools", "mounts", "food")


def equipment_kinds(entry: Mapping[str, Any]) -> tuple[str, ...]:
    """A deferred picker's kinds: ``equipmentType``, or ``equipmentTypes`` for "one of
    these" (the 2024 Monk's artisan's tool or instrument)."""
    if entry.get("equipmentType"):
        return (str(entry["equipmentType"]),)
    return tuple(str(kind) for kind in entry.get("equipmentTypes") or ())


# A class's deferred equipment pickers ({"equipmentType": "weaponMartial"}).
_WEAPON_TYPES = {
    "weaponSimple": ("simple", None),
    "weaponMartial": ("martial", None),
    "weaponSimpleMelee": ("simple", "M"),
    "weaponMartialMelee": ("martial", "M"),
    "weaponSimpleRanged": ("simple", "R"),
    "weaponMartialRanged": ("martial", "R"),
}
_FOCUS_TYPES = {
    "focusSpellcastingArcane": "arcane",
    "focusSpellcastingHoly": "holy",
    "focusSpellcastingDruidic": "druid",
}


def shelf(offer: Offer) -> str:
    """The shop shelf ``offer`` stands on, one of :data:`SHELVES`."""
    return _SHELVES.get(_type_code(offer.data), "gear")


@dataclass(frozen=True)
class Offer:
    """One corpus entry the wizard can offer, as the upstream data and its reference."""

    name: str
    source: str
    data: Mapping[str, Any]

    @property
    def reference(self) -> str:
        """``Name|SOURCE``, the form the character file stores."""
        return f"{self.name}|{self.source}"

    @property
    def edition(self) -> Edition:
        """``one`` for the 2024 books; anything not marked is 2014-era."""
        return "one" if self.data.get("edition") == "one" else "classic"

    def matches(self, option: ChosenOption | str | None) -> bool:
        """True when ``option`` names this entry (a missing source matches any)."""
        if option is None:
            return False
        if isinstance(option, str):
            option = ChosenOption.model_validate(option)
        if option.name.lower() != self.name.lower():
            return False
        return option.source is None or option.source.lower() == self.source.lower()


@dataclass(frozen=True)
class About:
    """What the books say about an offer: the text in English, for the page to overlay
    under ``category`` and ``key``, the picture's path, and where its translation editor
    is (``/gm/translate/fluff/<where>``)."""

    category: str
    key: str
    entries: list[Any]
    image: str | None
    where: tuple[str, ...] = ()


def edition_of(data: Mapping[str, Any] | None) -> Edition:
    """The edition of a corpus record."""
    return "one" if data is not None and data.get("edition") == "one" else "classic"


def _type_code(data: Mapping[str, Any]) -> str:
    return str(data.get("type", "")).split("|")[0]


class Catalogue:
    """The options of one world: its books applied to every category the wizard reads."""

    def __init__(
        self,
        compendium: Any,
        source_set: SourceSet | None = None,
        lookups: LookupTables | None = None,
        fluff: FluffStore | None = None,
        images: Path | None = None,
    ) -> None:
        """``compendium`` is the loaded store; ``lookups`` supplies the spell lists;
        ``fluff`` and ``images`` what the books say about an offer and its picture."""
        self.compendium = compendium
        self.fluff = fluff
        self.images = images
        self.source_set = source_set or SourceSet()
        self.lookups = lookups or LookupTables()
        self._cache: dict[str, list[Offer]] = {}
        self._indexes: dict[str, dict[str, Offer]] = {}

    # -- generic -----------------------------------------------------------------------

    def _rows(self, category: str) -> list[Offer]:
        try:
            entries = self.compendium.all(category)
        except KeyError:
            return []
        out = []
        for entry in entries:
            if not self.source_set.allows(entry.source):
                continue
            data = entry.model_dump(by_alias=True, exclude_none=True)
            out.append(Offer(entry.name, entry.source, data))
        return sorted(out, key=lambda o: (o.name.lower(), o.source))

    def rows(self, category: str) -> list[Offer]:
        """Every entry of ``category`` the declared books allow, by name."""
        if category not in self._cache:
            self._cache[category] = self._rows(category)
        return self._cache[category]

    def find(self, category: str, option: ChosenOption | str | None) -> Offer | None:
        """The offer ``option`` names, or None when the books no longer allow it."""
        if option is None:
            return None
        if isinstance(option, str) and "|" in option:
            exact = self.index(category).get(option.lower())
            if exact is not None:
                return exact
        return next((o for o in self.rows(category) if o.matches(option)), None)

    def index(self, category: str) -> dict[str, Offer]:
        """``name|source`` (lowercased) → offer, for looking many up at once."""
        key = f"index:{category}"
        if key not in self._indexes:
            self._indexes[key] = {o.reference.lower(): o for o in self.rows(category)}
        return self._indexes[key]

    @property
    def editions(self) -> set[Edition]:
        """The editions the world's classes come in."""
        return {o.edition for o in self.classes()} or {"classic"}

    def about(self, category: str, offer: Offer) -> About | None:
        """What the books say about ``offer``, or None when they say nothing."""
        name, found = offer.name, None
        if self.fluff is None:
            return None
        if category == "subclass":
            found = self.fluff.get(
                category,
                offer.name,
                offer.source,
                className=offer.data.get("className"),
                classSource=offer.data.get("classSource"),
            )
            owner = (str(offer.data.get("className")), str(offer.data.get("classSource")))
            return self._about(category, name, offer.source, found, owner)
        elif category == "subrace":
            # A subrace's fluff is filed as a race, under the name 5eTools composes.
            category = "race"
            name = f"{offer.data.get('raceName')} ({offer.name})"
            found = self.fluff.get(category, name, offer.source)
        else:
            found = self.fluff.get(category, name, offer.source)
        return self._about(category, name, offer.source, found)

    def _about(
        self,
        category: str,
        name: str,
        source: str,
        fluff: Fluff | None,
        owner: tuple[str, ...] = (),
    ) -> About | None:
        if fluff is None:
            return None
        image = fluff_image(fluff, self.images)
        if not fluff.entries and image is None:
            return None
        key = "|".join((name, source, *owner)) if owner else record_key(name, source)
        where = (category, source, name, *owner)
        return About(fluff_category(category), key, list(fluff.entries), image, where)

    # -- classes ---------------------------------------------------------------------

    def classes(self) -> list[Offer]:
        """Playable classes: those with a hit die (sidekicks have none)."""
        return [o for o in self.rows("class") if o.data.get("hd")]

    def subclasses(self, cls: Offer) -> list[Offer]:
        """The subclasses of ``cls`` the books allow, from whichever book prints them."""
        return [
            o
            for o in self.rows("subclass")
            if str(o.data.get("className", "")).lower() == cls.name.lower()
            and str(o.data.get("classSource", "")).lower() == cls.source.lower()
        ]

    # -- species and backgrounds -----------------------------------------------------

    def _in_edition(self, rows: list[Offer], edition: Edition | None) -> list[Offer]:
        if edition is None:
            return rows
        same = [o for o in rows if o.edition == edition]
        return same or rows

    def species(self, edition: Edition | None = None) -> list[Offer]:
        """Species (races), in ``edition`` when the books hold both."""
        return self._in_edition(self.rows("race"), edition)

    def subspecies(self, species: Offer) -> list[Offer]:
        """The subraces of ``species``; empty when it has none."""
        return [
            o
            for o in self.rows("subrace")
            if str(o.data.get("raceName", "")).lower() == species.name.lower()
            and str(o.data.get("raceSource", "")).lower() == species.source.lower()
        ]

    @staticmethod
    def lineages(species: Offer) -> list[str]:
        """A 2024 species' lineages (Drow, High Elf, Wood Elf), named in its spells."""
        named = [
            str(block["name"])
            for block in species.data.get("additionalSpells") or []
            if isinstance(block, dict) and block.get("name")
        ]
        return named if len(named) > 1 else []

    def backgrounds(self, edition: Edition | None = None) -> list[Offer]:
        """Backgrounds, in ``edition`` when the books hold both."""
        return self._in_edition(self.rows("background"), edition)

    # -- feats and options -----------------------------------------------------------

    def feats(self, categories: Iterable[str] | None = None) -> list[Offer]:
        """Feats, optionally of some categories (``O`` origin, ``G`` general, ``FS``…).

        A category is matched by its prefix too, so ``FS`` includes ``FS:P``.
        """
        rows = self.rows("feat")
        if categories is None:
            return rows
        wanted = tuple(categories)
        return [
            o for o in rows if any(str(o.data.get("category") or "").startswith(c) for c in wanted)
        ]

    def optional_features(self, types: Iterable[str]) -> list[Offer]:
        """Fighting styles, invocations, metamagic…: options of the given feature types."""
        wanted = set(types)
        return [
            o for o in self.rows("optionalfeature") if wanted & set(o.data.get("featureType") or [])
        ]

    # -- proficiencies ---------------------------------------------------------------

    @cached_property
    def skills(self) -> list[str]:
        """The skills, lowercased, each once (both editions name the same eighteen)."""
        return sorted({o.name.lower() for o in self.rows("skill")})

    def skill_ability(self, skill: str) -> str | None:
        """The ability a skill uses."""
        row = next((o for o in self.rows("skill") if o.name.lower() == skill.lower()), None)
        return str(row.data["ability"]) if row and row.data.get("ability") else None

    def languages(self, kind: Literal["standard", "exotic", "any"] = "any") -> list[str]:
        """Language names, lowercased: standard ones, the rest, or all.

        "Exotic" here is everything not standard — the 2014 exotic and the 2024 rare
        languages — but never the secret ones (Druidic, Thieves' Cant).
        """
        out = set()
        for o in self.rows("language"):
            kind_of = o.data.get("type")
            if kind_of == "secret":
                continue
            if kind == "standard" and kind_of != "standard":
                continue
            if kind == "exotic" and kind_of == "standard":
                continue
            out.add(o.name.lower())
        return sorted(out)

    def tools(self, kind: str = "any") -> list[str]:
        """Tool names, lowercased: artisan's tools, instruments, gaming sets, or all."""
        codes = TOOL_TYPES.get(kind, TOOL_TYPES["any"])
        out = {
            o.name.lower()
            for category in ("baseitem", "item")
            for o in self.rows(category)
            if _type_code(o.data) in codes and o.data.get("rarity", "none") == "none"
        }
        return sorted(out)

    # -- equipment -------------------------------------------------------------------

    def equipment(self, equipment_type: str) -> list[Offer]:
        """What a deferred picker (``{"equipmentType": "weaponMartial"}``) expands to."""
        # Renaissance, modern and futuristic firearms (the DMG's) are not starting gear.
        base = [o for o in self.rows("baseitem") if not o.data.get("age")]
        if equipment_type in _WEAPON_TYPES:
            category, reach = _WEAPON_TYPES[equipment_type]
            return [
                o
                for o in base
                if o.data.get("weaponCategory") == category
                and (reach is None or _type_code(o.data) == reach)
            ]
        if equipment_type in _FOCUS_TYPES:
            focus = _FOCUS_TYPES[equipment_type]
            return [
                o
                for category in ("baseitem", "item")
                for o in self.rows(category)
                if _type_code(o.data) == "SCF"
                and o.data.get("scfType") == focus
                and o.data.get("rarity", "none") == "none"
            ]
        kinds = {"instrumentMusical": "INS", "toolArtisan": "AT", "setGaming": "GS"}
        if equipment_type in kinds:
            # Tools, instruments and gaming sets live in both item files.
            return [
                o
                for category in ("baseitem", "item")
                for o in self.rows(category)
                if _type_code(o.data) == kinds[equipment_type]
                and o.data.get("rarity", "none") == "none"
                and not o.data.get("age")
            ]
        return []

    def shop(self, edition: Edition) -> list[Offer]:
        """What can be bought with starting gold: priced, mundane, one printing per name.

        A world with both editions prints most gear twice; the character's edition wins.
        """
        key = f"shop:{edition}"
        if key not in self._cache:
            best: dict[str, Offer] = {}
            for category in ("baseitem", "item"):
                for o in self.rows(category):
                    code = _type_code(o.data)
                    if (
                        not o.data.get("value")
                        or o.data.get("rarity", "none") != "none"
                        or o.data.get("age")
                        or code.startswith("$")
                        or code in _NOT_SOLD
                    ):
                        continue
                    held = best.get(o.name.lower())
                    if held is None or (held.edition != edition and o.edition == edition):
                        best[o.name.lower()] = o
            self._cache[key] = sorted(best.values(), key=lambda o: o.name.lower())
        return self._cache[key]

    def item(self, reference: str) -> Offer | None:
        """A base item or item by ``name|source`` (case-insensitive, as classes write it)."""
        return self.find("baseitem", reference) or self.find("item", reference)

    # -- spells ----------------------------------------------------------------------

    def class_spells(
        self, cls: Offer, max_level: int, subclass: Offer | None = None
    ) -> list[Offer]:
        """The spells a class (and subclass) chooses from, up to ``max_level``.

        Membership comes from the generated spell-source lookup, never from a field on
        the spell (CHARGEN.md trap #2), and is keyed by the class's own book: the 2024
        wizard's list is not the 2014 one. A subclass adds its own entries — the
        Eldritch Knight's whole list, the Hexblade's expanded spells.
        """
        out = []
        for o in self.rows("spell"):
            level = o.data.get("level")
            if not isinstance(level, int) or level > max_level:
                continue
            lists = self.lookups.spell_sources.classes_for(o.name, o.source)
            names = {n.lower() for n in lists.get(cls.source, set())}
            if cls.name.lower() in names or self._on_subclass_list(o, cls, subclass):
                out.append(o)
        return out

    def _on_subclass_list(self, spell: Offer, cls: Offer, subclass: Offer | None) -> bool:
        if subclass is None:
            return False
        entry = self.lookups.spell_sources.entry(spell.name, spell.source) or {}
        by_book = (entry.get("subclass") or {}).get(cls.source) or {}
        by_class: dict[str, Any] = next(
            (v for k, v in by_book.items() if k.lower() == cls.name.lower()), {}
        )
        lists = by_class.get(subclass.source) or {}
        short = str(subclass.data.get("shortName") or subclass.name).lower()
        return any(str(name).lower() == short for name in lists)


__all__ = ["Catalogue", "Edition", "Offer", "edition_of"]
