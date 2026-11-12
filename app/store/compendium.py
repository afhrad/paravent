"""The in-memory compendium: load JSON once, search and filter in RAM.

The whole corpus is ~114 MB of JSON across ~500 files, read-mostly. documentation/TECHSTACK.md rules
out a database daemon, so the store loads everything at startup, resolves ``_copy``
inheritance, validates against the Pydantic models, and keeps the result in dicts. At
this data volume a linear scan is a millisecond-scale operation and no index is needed.

Homebrew is loaded after the compendium and uses the same models and the same
``(name, source)`` keyspace, so a homebrew entry can override an imported one and can
itself be the target of a ``_copy``.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import ValidationError

from app.models.base import FiveToolsModel
from app.models.compendium import (
    Action,
    Background,
    Boon,
    Card,
    CharOption,
    ClassEntry,
    ClassFeature,
    Condition,
    CrochetPattern,
    Cult,
    Deck,
    Deity,
    Disease,
    Facility,
    Feat,
    Hazard,
    Item,
    ItemMastery,
    ItemProperty,
    ItemType,
    ItemTypeEntries,
    Language,
    LegendaryGroup,
    LegendaryGroupTemplate,
    Monster,
    Object,
    OptionalFeature,
    Psionic,
    Race,
    Recipe,
    Reward,
    Sense,
    Skill,
    Spell,
    Status,
    Subclass,
    SubclassFeature,
    Table,
    TableGroup,
    Trap,
    VariantRule,
    Vehicle,
    VehicleUpgrade,
)
from app.store.copyresolver import CopyResolver, composite_key, entry_key
from app.store.errors import CopyResolutionError, EntryNotFound
from app.store.item_entries import ItemEntries, read_templates
from app.store.jsonfile import iter_json_files, read_json
from app.store.magicvariants import generic_entry, specific_variants
from app.store.projections import Projections
from app.store.query import ItemQuery, MonsterQuery, SpellQuery
from app.store.reference import ReferenceIndex, ReferenceResolver

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=FiveToolsModel)


@dataclass(frozen=True)
class Category:
    """Describes one compendium category and where its entries come from.

    ``json_key`` is the array key inside the source file; ``glob`` selects the files.
    ``supports_copy`` marks categories whose entries may carry ``_copy``.
    """

    name: str
    model: type[FiveToolsModel]
    json_key: str
    globs: tuple[str, ...]
    supports_copy: bool = False
    # Extra keys in the same files that hold ``_copy`` targets but are not themselves
    # published entries (bestiary templates, for example).
    aux_keys: tuple[str, ...] = ()
    # Files read for ``aux_keys`` only: they hold no entries of the category.
    aux_globs: tuple[str, ...] = ()
    # The field ``_copy`` and the key name an entry by: ``abbreviation`` for item types.
    key_field: str = "name"

    def copy_view(self, item: dict[str, Any]) -> dict[str, Any]:
        """``item`` as the copy index and its directives see it: named by its key field."""
        if self.key_field == "name" or not isinstance(item.get(self.key_field), str):
            return item
        return {**item, "name": item[self.key_field]}

    def resolve_copy(self, resolver: CopyResolver, item: dict[str, Any]) -> dict[str, Any]:
        """Apply ``item``'s ``_copy`` through ``resolver``, an index of :meth:`copy_view`."""
        if self.key_field == "name":
            return resolver.resolve(item)
        directive = self.copy_view(dict(item["_copy"]))
        resolved = resolver.resolve({**item, "_copy": directive})
        if "name" in item:
            resolved["name"] = item["name"]
        return resolved


# The category table. Adding a category here is the only step needed to make it
# loadable, searchable, and CRUD-able.
CATEGORIES: tuple[Category, ...] = (
    Category(
        "monster",
        Monster,
        "monster",
        ("bestiary/bestiary-*.json",),
        supports_copy=True,
        aux_keys=("monsterTemplate",),
        # "Skeleton", "Reduced Threat": 208 monsters name one in ``_copy._templates``.
        aux_globs=("bestiary/template.json",),
    ),
    Category("spell", Spell, "spell", ("spells/spells-*.json",)),
    Category("item", Item, "item", ("items.json",), supports_copy=True),
    Category("baseitem", Item, "baseitem", ("items-base.json",), supports_copy=True),
    # Generic magic variants ("+1 Weapon"); the specific items they make are added to
    # ``item`` after the load (app/store/magicvariants.py).
    Category("magicvariant", Item, "magicvariant", ("magicvariants.json",)),
    # "Artisan's Tools", "Ioun Stone": a choice among items, named by its own tag
    # ({@item Artisan's Tools|XPHB}); its ``items`` are ``name|source`` references.
    Category("itemGroup", Item, "itemGroup", ("items.json",)),
    Category("condition", Condition, "condition", ("conditionsdiseases.json",)),
    Category("disease", Disease, "disease", ("conditionsdiseases.json",)),
    Category("status", Status, "status", ("conditionsdiseases.json",)),
    Category("background", Background, "background", ("backgrounds.json",)),
    Category("feat", Feat, "feat", ("feats.json",)),
    Category("race", Race, "race", ("races.json",), supports_copy=True),
    Category("subrace", Race, "subrace", ("races.json",), supports_copy=True),
    Category("action", Action, "action", ("actions.json",)),
    Category("skill", Skill, "skill", ("skills.json",)),
    Category("sense", Sense, "sense", ("senses.json",)),
    Category("deity", Deity, "deity", ("deities.json",)),
    # And the rules 5eTools cuts out of book sections ("Weapon Mastery Properties").
    Category(
        "variantrule",
        VariantRule,
        "variantrule",
        ("variantrules.json", "generated/gendata-variantrules.json"),
    ),
    Category("language", Language, "language", ("languages.json",)),
    Category("optionalfeature", OptionalFeature, "optionalfeature", ("optionalfeatures.json",)),
    Category("trap", Trap, "trap", ("trapshazards.json",)),
    Category("hazard", Hazard, "hazard", ("trapshazards.json",)),
    Category("reward", Reward, "reward", ("rewards.json",)),
    Category("object", Object, "object", ("objects.json",)),
    # The 13 standalone tables, and the 2,234 printed inside the books and adventures
    # that 5eTools gathers for {@table …} (each with its book chapter).
    Category("table", Table, "table", ("tables.json", "generated/gendata-tables.json")),
    # Tables printed together under one name, which a {@table …} tag names as one.
    Category("tableGroup", TableGroup, "tableGroup", ("generated/gendata-tables.json",)),
    # Classes: one file per class, each holding all four keys. Subclasses and subclass
    # features carry ``_copy`` (124 and 75 records respectively), so resolution must run
    # before validation — an unresolved shell lacks the ``source`` the model requires.
    Category("class", ClassEntry, "class", ("class/class-*.json",)),
    Category("subclass", Subclass, "subclass", ("class/class-*.json",), supports_copy=True),
    Category("classFeature", ClassFeature, "classFeature", ("class/class-*.json",)),
    Category(
        "subclassFeature",
        SubclassFeature,
        "subclassFeature",
        ("class/class-*.json",),
        supports_copy=True,
    ),
    # Lair and regional actions, pointed at by a legendary monster's ``legendaryGroup``.
    Category(
        "legendaryGroup",
        LegendaryGroup,
        "legendaryGroup",
        ("bestiary/legendarygroups.json",),
    ),
    # What a creature template adds to the group a creature already has (the legendary
    # shadow dragon's), read by the reference page, never searched.
    Category(
        "legendaryGroupTemplate",
        LegendaryGroupTemplate,
        "legendaryGroupTemplate",
        ("bestiary/template.json",),
    ),
    # The long tail of TECHSTACK.md §6b phase 2: whole categories with one file each.
    Category("psionic", Psionic, "psionic", ("psionics.json",)),
    Category("vehicle", Vehicle, "vehicle", ("vehicles.json",)),
    Category("vehicleUpgrade", VehicleUpgrade, "vehicleUpgrade", ("vehicles.json",)),
    Category("facility", Facility, "facility", ("bastions.json",)),
    Category("charoption", CharOption, "charoption", ("charcreationoptions.json",)),
    Category("cult", Cult, "cult", ("cultsboons.json",)),
    Category("boon", Boon, "boon", ("cultsboons.json",)),
    # The long tail of phase 3: the cookbooks' recipes and the crochet book's patterns.
    Category("recipe", Recipe, "recipe", ("recipes.json",)),
    Category("crochetPattern", CrochetPattern, "crochetPattern", ("homecrafts.json",)),
    # The 2024 weapon mastery properties, linked from a weapon's ``mastery``.
    Category("itemMastery", ItemMastery, "itemMastery", ("items-base.json",)),
    # Weapon properties and item types, addressed by abbreviation ("V|XPHB", "M|XPHB").
    Category(
        "itemProperty",
        ItemProperty,
        "itemProperty",
        ("items-base.json",),
        key_field="abbreviation",
    ),
    Category(
        "itemType",
        ItemType,
        "itemType",
        ("items-base.json",),
        supports_copy=True,
        key_field="abbreviation",
    ),
    # More text about a type from a later book, shown with the type and its items.
    Category(
        "itemTypeAdditionalEntries",
        ItemTypeEntries,
        "itemTypeAdditionalEntries",
        ("items-base.json",),
    ),
    Category("deck", Deck, "deck", ("decks.json",), supports_copy=True),
    # Keyed by (name, source, deck) and reached through the deck, like class features
    # through their class: 32 (name, source) pairs repeat.
    Category("card", Card, "card", ("decks.json",)),
)

CATEGORIES_BY_NAME: dict[str, Category] = {c.name: c for c in CATEGORIES}

# Categories whose entries may hold ``{#itemEntry}`` (app/store/item_entries.py).
ITEM_CATEGORIES = frozenset({"item", "baseitem", "magicvariant", "itemGroup"})


@dataclass
class LoadReport:
    """What a load pass produced, including everything it could not use.

    Failures are collected rather than raised so that one malformed hand-edit does not
    take the other ten thousand entries down with it. Callers decide what to do — the
    dev server surfaces them in the UI, tests assert the list is empty.
    """

    loaded: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    unresolved_copies: list[str] = field(default_factory=list)
    unknown_copy_modes: set[str] = field(default_factory=set)
    # ``_templates`` references that named no template: the entry loads without it.
    missing_templates: set[str] = field(default_factory=set)
    # ``{#itemEntry}`` references to no template, and gaps an item had no value for.
    missing_item_entries: set[str] = field(default_factory=set)
    unfilled_item_entries: set[str] = field(default_factory=set)
    files_read: int = 0
    # Specific magic items made from generic variants ("+1 Longsword").
    variants_made: int = 0

    @property
    def total(self) -> int:
        """Total number of entries successfully loaded."""
        return sum(self.loaded.values())

    @property
    def ok(self) -> bool:
        """True when nothing was rejected."""
        return not self.errors and not self.unresolved_copies

    def summary(self) -> str:
        """A one-line summary for logs."""
        return (
            f"{self.total} entries from {self.files_read} files "
            f"({len(self.errors)} errors, {len(self.unresolved_copies)} unresolved copies)"
        )


class CompendiumStore:
    """Holds every compendium category in memory and answers queries against it."""

    def __init__(self, *, strict: bool = False) -> None:
        """``strict`` re-raises validation failures instead of collecting them."""
        self._strict = strict
        self._entries: dict[str, dict[str, FiveToolsModel]] = {c.name: {} for c in CATEGORIES}
        self._sources: dict[str, set[str]] = defaultdict(set)
        self._index = ReferenceIndex()
        self._projections: Projections | None = None
        self._dumps: dict[str, list[dict[str, Any]]] = {}
        self.report = LoadReport()

    # -- loading -----------------------------------------------------------------

    def load(self, *roots: Path) -> LoadReport:
        """Load every category from ``roots``, in order.

        Later roots override earlier ones on key collision, which is how ``homebrew/``
        shadows ``compendium/``. Replaces the current contents wholesale, so a reload
        after a file change is just another call.
        """
        report = LoadReport()
        entries: dict[str, dict[str, FiveToolsModel]] = {c.name: {} for c in CATEGORIES}
        seen_files: set[Path] = set()
        shared = ItemEntries(read_templates(roots))

        for category in CATEGORIES:
            raw, aux, files = self._read_category(category, roots)
            seen_files |= files

            if category.supports_copy:
                raw = self._resolve_copies(category, raw, aux, report)
            if category.name == "magicvariant":
                # Book, rarity and text sit inside ``inherits``: lift them, for a key.
                raw = [generic_entry(item) for item in raw]
            if category.name in ITEM_CATEGORIES:
                raw = [shared.expand(item) for item in raw]

            for item in raw:
                model = self._validate(category, item, report)
                if model is not None:
                    key = key_of(model)
                    entries[category.name][key] = model
                    if source := getattr(model, "source", None):
                        self._sources[category.name].add(source)

            report.loaded[category.name] = len(entries[category.name])

        report.variants_made = self._make_variants(entries, report, shared)
        report.missing_item_entries = shared.missing
        report.unfilled_item_entries = shared.unfilled
        report.files_read = len(seen_files)
        self._entries = entries
        self._index.build(entries)
        self._projections = None  # rebuilt on first use, from the entries just loaded
        self._dumps = {}
        self.report = report
        logger.info("Compendium loaded: %s", report.summary())
        return report

    def _make_variants(
        self,
        entries: dict[str, dict[str, FiveToolsModel]],
        report: LoadReport,
        shared: ItemEntries,
    ) -> int:
        """Add the specific items the generic variants make; an item the books print
        under the same name and source is kept as printed."""
        variants = [
            m.model_dump(by_alias=True, exclude_none=True) for m in entries["magicvariant"].values()
        ]
        bases = [
            m.model_dump(by_alias=True, exclude_none=True) for m in entries["baseitem"].values()
        ]
        items = entries["item"]
        made = 0
        for raw in specific_variants(variants, bases):
            key = f"{raw['name']}|{raw.get('source', '')}".lower()
            if key in items:
                continue
            model = self._validate(CATEGORIES_BY_NAME["item"], shared.expand(raw), report)
            if model is not None:
                items[key_of(model)] = model
                made += 1
        report.loaded["item"] = len(items)
        return made

    def _read_category(
        self, category: Category, roots: Iterable[Path]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[Path]]:
        """Collect the raw dicts for one category across all roots."""
        raw: list[dict[str, Any]] = []
        aux: list[dict[str, Any]] = []
        files: set[Path] = set()

        for root in roots:
            for pattern in category.globs:
                for path in expand_glob(root, pattern):
                    data = _read_or_none(path)
                    if data is None:
                        continue
                    files.add(path)
                    raw.extend(_as_dicts(data.get(category.json_key)))
                    for aux_key in category.aux_keys:
                        aux.extend(_as_dicts(data.get(aux_key)))
            for pattern in category.aux_globs:
                for path in expand_glob(root, pattern):
                    data = _read_or_none(path)
                    if data is None:
                        continue
                    files.add(path)
                    for aux_key in category.aux_keys:
                        aux.extend(_as_dicts(data.get(aux_key)))
        return raw, aux, files

    def _resolve_copies(
        self,
        category: Category,
        raw: list[dict[str, Any]],
        aux: list[dict[str, Any]],
        report: LoadReport,
    ) -> list[dict[str, Any]]:
        """Apply every ``_copy`` directive in a category.

        The index spans the category's own entries plus its auxiliary records, because
        ``_templates`` reference definitions that are not themselves entries.
        """
        # Indexed by composite key so that categories whose ``(name, source)`` repeats
        # — subclasses across classes, features across levels — resolve against the
        # right record rather than themselves.
        index: dict[str, dict[str, Any]] = {}
        for item in [*raw, *aux]:
            view = category.copy_view(item)
            if view.get("name") and view.get("source"):
                index[composite_key(view)] = view

        resolver = CopyResolver(index)
        resolved: list[dict[str, Any]] = []
        for item in raw:
            if not item.get("_copy"):
                resolved.append(item)
                continue
            try:
                resolved.append(category.resolve_copy(resolver, item))
            except (CopyResolutionError, ValueError) as exc:
                if self._strict:
                    raise
                report.unresolved_copies.append(f"{category.name}: {exc}")

        report.unknown_copy_modes |= resolver.unknown_modes
        report.missing_templates |= resolver.missing_templates
        if resolver.missing_templates:
            logger.warning(
                "Templates not found in %s: %s",
                category.name,
                sorted(resolver.missing_templates),
            )
        if resolver.unknown_modes:
            logger.warning(
                "Unknown _copy modes in %s: %s", category.name, sorted(resolver.unknown_modes)
            )
        return resolved

    def _validate(
        self, category: Category, item: dict[str, Any], report: LoadReport
    ) -> FiveToolsModel | None:
        """Validate one raw dict, collecting the failure unless strict."""
        try:
            return category.model.model_validate(item)
        except ValidationError as exc:
            if self._strict:
                raise
            name = item.get("name", "<unnamed>")
            source = item.get("source", "?")
            report.errors.append(f"{category.name} {name!r} ({source}): {exc.error_count()} errors")
            return None

    # -- queries -----------------------------------------------------------------

    def categories(self) -> list[str]:
        """The names of every loaded category."""
        return [c.name for c in CATEGORIES]

    def count(self, category: str) -> int:
        """Number of entries in a category."""
        return len(self._require(category))

    def all(self, category: str) -> list[FiveToolsModel]:
        """Every entry in a category, in load order."""
        return list(self._require(category).values())

    def get(self, category: str, key: str) -> FiveToolsModel:
        """Fetch one entry by its ``name|source`` key, raising when absent."""
        entries = self._require(category)
        model = entries.get(key.lower())
        if model is None:
            raise EntryNotFound(category, key)
        return model

    def find(self, category: str, name: str, source: str | None = None) -> FiveToolsModel | None:
        """Look an entry up by name, optionally pinned to a source.

        Without a source the first match wins. That is what ``{@creature goblin}`` means
        in the corpus — a source-less tag reference resolves to whichever edition is
        loaded, and pinning it is the author's job, not ours.
        """
        entries = self._require(category)
        if source:
            return entries.get(entry_key(name, source))
        needle = name.lower()
        for key, model in entries.items():
            if key.split("|", 1)[0] == needle:
                return model
        return None

    def search(
        self,
        category: str,
        query: str = "",
        *,
        predicate: Callable[[Any], bool] | None = None,
        limit: int | None = None,
    ) -> list[FiveToolsModel]:
        """Substring-match names in a category, optionally filtered further.

        Results are ordered by match quality — exact, then prefix, then substring — so
        that typing "fire" surfaces *Fire* before *Wall of Fire* in the search popup.
        """
        needle = query.strip().lower()
        exact: list[FiveToolsModel] = []
        prefix: list[FiveToolsModel] = []
        contains: list[FiveToolsModel] = []

        for model in self._require(category).values():
            if predicate is not None and not predicate(model):
                continue
            name = str(getattr(model, "name", "")).lower()
            if not needle:
                contains.append(model)
            elif name == needle:
                exact.append(model)
            elif name.startswith(needle):
                prefix.append(model)
            elif needle in name:
                contains.append(model)

        results = exact + prefix + contains
        return results[:limit] if limit else results

    def search_all(self, query: str, *, limit_per_category: int = 5) -> dict[str, list[Any]]:
        """Search every category at once, for the global command palette."""
        out: dict[str, list[Any]] = {}
        for category in self.categories():
            hits = self.search(category, query, limit=limit_per_category)
            if hits:
                out[category] = hits
        return out

    def dumps(self, category: str) -> list[dict[str, Any]]:
        """Every entry of ``category`` as plain 5eTools data (upstream keys, no None),
        made on first use and kept until the next load. Shared: read them, never change
        them. A character sheet looks through every class feature; dumping them for each
        sheet was most of its cost."""
        found = self._dumps.get(category)
        if found is None:
            found = [
                entry.model_dump(by_alias=True, exclude_none=True)
                for entry in self._require(category).values()
            ]
            self._dumps[category] = found
        return found

    @property
    def projections(self) -> Projections:
        """Typed projections over monsters, spells and items, built once per load."""
        if self._projections is None:
            self._projections = Projections.build(
                self._entries["monster"].values(),
                self._entries["spell"].values(),
                [
                    (category, entry)
                    for category in ("item", "baseitem")
                    for entry in self._entries[category].values()
                ],
            )
        return self._projections

    def monsters(self) -> MonsterQuery:
        """Every creature, ready to filter: ``monsters().cr_between(3, 7).of_type("dragon")``."""
        return MonsterQuery(self.projections.monsters)

    def spells(self) -> SpellQuery:
        """Every spell, ready to filter: ``spells().level(3).school("V")``."""
        return SpellQuery(self.projections.spells)

    def items(self) -> ItemQuery:
        """Every item and base item, ready to filter: ``items().magic().rarity("rare")``."""
        return ItemQuery(self.projections.items)

    @property
    def index(self) -> ReferenceIndex:
        """Candidate buckets by bare name, rebuilt on every load."""
        return self._index

    def resolver(self) -> ReferenceResolver:
        """A resolver over the currently loaded entries.

        Cheap to construct — it holds references, not copies — so callers make one per
        request rather than caching one across a reload.
        """
        return ReferenceResolver(self._index, self._entries)

    def sources(self, category: str) -> list[str]:
        """The distinct source books present in a category."""
        return sorted(self._sources.get(category, set()))

    def __iter__(self) -> Iterator[tuple[str, FiveToolsModel]]:
        """Iterate every entry as ``(category, model)``."""
        for name, entries in self._entries.items():
            for model in entries.values():
                yield name, model

    def _require(self, category: str) -> dict[str, FiveToolsModel]:
        """Return a category's entry map, or raise for an unknown category name."""
        entries = self._entries.get(category)
        if entries is None:
            raise EntryNotFound("category", category)
        return entries


# -- helpers ---------------------------------------------------------------------


def key_of(model: FiveToolsModel) -> str:
    """The index key for a model.

    A model may define its own ``key`` property, and several must: ``(name, source)`` is
    **not** universally unique. Subclass names repeat across classes (124 of 315
    records), and class features repeat per level — *Additional Maneuvers* exists at
    levels 7, 10 and 15. Those models key on a composite that includes the class and the
    level; everything else falls back to ``name|source``.
    """
    key = getattr(model, "key", None)
    if isinstance(key, str) and key:
        return key
    name = str(getattr(model, "name", "")).lower()
    source = getattr(model, "source", None)
    return f"{name}|{str(source).lower()}" if source else name


def _as_dicts(value: Any) -> list[dict[str, Any]]:
    """Coerce a JSON array into a list of dicts, dropping anything else."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _read_or_none(path: Path) -> dict[str, Any] | None:
    """Read a JSON object, returning None (and logging) on a malformed file."""
    try:
        data = read_json(path)
    except json.JSONDecodeError as exc:
        logger.warning("Skipping malformed JSON %s: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def is_vtt_payload(path: Path) -> bool:
    """True for Foundry VTT integration files, which are not content.

    ``foundry-items.json`` and ``spells/foundry.json`` carry the same ``item``/``spell``
    keys as the real data but hold VTT automation rather than game content. Indexing
    them manufactures phantom duplicate entries — every ``item`` and ``spell`` key
    collision in the corpus traces to these files and to nothing else (676 of them).

    Filtered here rather than per-glob so that every category, including ones added
    later, is covered by one rule.
    """
    return path.name == "foundry.json" or path.name.startswith("foundry-")


def expand_glob(root: Path, pattern: str) -> list[Path]:
    """Expand a category glob under a root, tolerating a missing root."""
    if not root.exists():
        return []
    if any(ch in pattern for ch in "*?["):
        return [p for p in sorted(root.glob(pattern)) if not is_vtt_payload(p)]
    candidate = root / pattern
    if is_vtt_payload(candidate):
        return []
    return [candidate] if candidate.exists() else []


__all__ = [
    "CATEGORIES",
    "CATEGORIES_BY_NAME",
    "Category",
    "CompendiumStore",
    "LoadReport",
    "expand_glob",
    "is_vtt_payload",
    "iter_json_files",
    "key_of",
]
