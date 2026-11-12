"""What can be translated, found by what its page names it (TRANSLATION.md §7).

Five kinds of text, one shape: a reference entry (a spell, a creature, a rule…), a
rulebook chapter, a section of a published adventure, a chapter of a reference
screen, and one of the creature builder's ready-made texts. Each becomes a
:class:`Target` — the English record, the category and key its translation is stored
under, and which fields hold its text — so the editor, the machine drafts and the
coverage counts treat them alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.services.references import ReferenceService
from app.services.translations import (
    CHAPTER_FIELDS,
    COMPOSITE,
    ENTRY_FIELDS,
    record_key,
    translation_key,
)
from app.store.creature_tables import CreatureTables
from app.store.errors import StoreError
from app.store.published import PublishedAdventures, PublishedBooks, ReferenceScreens

TargetKind = Literal["ref", "feature", "book", "adventure", "fluff", "screen", "builder"]
# The creature builder's texts are stored as one "book" of this id; its parts are a
# ready-made trait or action (by its full name, "Bite (Cloaker)") or a monster feature.
BUILDER_BOOK = "creatures"
BUILDER_KINDS = ("feature", "trait", "action")
BUILDER_FIELDS = ("name", "entries")
# Base items share the items' fluff.
FLUFF_OF = {"baseitem": "item"}


def fluff_category(category: str) -> str:
    """Where what the books say about an entry (its fluff) is stored when translated:
    beside the entry's own translation, under a category of its own."""
    return f"{FLUFF_OF.get(category, category)}Fluff"


class TargetNotFound(LookupError):
    """Nothing to translate under that name."""


@dataclass(frozen=True)
class Target:
    """One record to translate."""

    kind: TargetKind
    category: str
    key: str
    data: dict[str, Any]
    fields: tuple[str, ...]
    # The English name the page shows, and where the record lives (``ref``: its
    # category/source/name; ``book``: book id and chapter index; ``adventure``: the
    # adventure's id and the section's; ``screen``: ``dm`` or ``quick`` and the index;
    # ``fluff``: as ``ref``, a subclass's with its class and that class's source;
    # ``builder``: ``feature``, ``trait`` or ``action`` and the name).
    title: str
    where: tuple[str, ...]


class TranslationTargets:
    """Finds the English record behind each translatable page."""

    def __init__(
        self,
        references: ReferenceService,
        rulebooks: PublishedBooks,
        adventures: PublishedAdventures,
        compendium: Any = None,
        fluff: Any = None,
        screens: ReferenceScreens | None = None,
        creature_tables: CreatureTables | None = None,
    ) -> None:
        """Bind to the reference index, the published books and adventures, the
        compendium (for features reached by their composite key), the fluff store, the
        reference screens and the creature builder's tables."""
        self._creature_tables = creature_tables
        self._screens = screens
        self._compendium = compendium
        self._fluff = fluff
        self._references = references
        self._rulebooks = rulebooks
        self._adventures = adventures

    def entry(self, category: str, source: str, name: str) -> Target:
        """A reference entry (raises the reference index's not-found error)."""
        found = self._references.get(category, source, name, None)
        return Target(
            "ref",
            found.category,
            translation_key(found.category, found.data),
            found.data,
            ENTRY_FIELDS,
            found.name,
            (found.category, found.source, found.name),
        )

    def fluff(
        self, category: str, source: str, name: str, class_name: str = "", class_source: str = ""
    ) -> Target:
        """The books' description of an entry (its fluff), as a reference page and the
        players' Details show it; a subclass's needs its class, as a name does not say
        whose subclass it is. Raises :class:`TargetNotFound` when there is none."""
        kind = FLUFF_OF.get(category, category)
        data: dict[str, Any] = {"name": name, "source": source}
        where: tuple[str, ...] = (category, source, name)
        key = record_key(name, source)
        title = name
        if category == "subclass":
            data |= {"className": class_name, "classSource": class_source}
            where = (*where, class_name, class_source)
            key = translation_key("subclass", data)
            title = f"{name} · {class_name}"
        found = None
        if self._fluff:
            extra = {k: v for k, v in data.items() if k in ("className", "classSource")}
            found = self._fluff.get(kind, name, source, **extra)
        if found is None or not found.entries:
            raise TargetNotFound("/".join(where))
        return Target(
            "fluff",
            fluff_category(category),
            key,
            {**data, "entries": list(found.entries)},
            ("entries",),
            title,
            where,
        )

    def feature(self, category: str, key: str) -> Target:
        """A class feature, subclass feature or subclass by the corpus's composite key —
        their names repeat across classes and levels, so a name cannot find them."""
        if category not in COMPOSITE:
            raise TargetNotFound(f"{category}/{key}")
        try:
            model = self._compendium.get(category, key)
        except StoreError as exc:
            raise TargetNotFound(f"{category}/{key}") from exc
        data: dict[str, Any] = model.model_dump(by_alias=True, exclude_none=True)
        owner = data.get("subclassShortName") or data.get("className") or ""
        level = data.get("level")
        title = f"{data.get('name', '')} · {owner}" + (f" {level}" if level else "")
        return Target(
            "feature",
            category,
            translation_key(category, data),
            data,
            ENTRY_FIELDS,
            title,
            (category, key),
        )

    def chapter(self, book_id: str, index: int) -> Target:
        """A rulebook chapter, by position as the reading page has it."""
        body = self._rulebooks.body(book_id)
        chapter = body.section(index) if body is not None else None
        if body is None or chapter is None:
            raise TargetNotFound(f"{book_id}/{index}")
        return Target(
            "book",
            "book",
            f"{body.book.id}|{index}",
            chapter.entry,
            CHAPTER_FIELDS,
            f"{chapter.name} · {body.book.name}",
            (body.book.id, str(index)),
        )

    def screen(self, screen: str, index: int) -> Target:
        """A chapter of the DM screen or the quick reference, by position."""
        body = self._screens.body(screen) if self._screens is not None else None
        chapter = body.section(index) if body is not None else None
        if body is None or chapter is None:
            raise TargetNotFound(f"screen/{screen}/{index}")
        return Target(
            "screen",
            "screen",
            f"{screen}|{index}",
            chapter.entry,
            CHAPTER_FIELDS,
            f"{chapter.name} · {body.book.name}",
            (screen, str(index)),
        )

    def builder(self, kind: str, name: str) -> Target:
        """One of the creature builder's texts: a ready-made trait or action by its full
        name, or a monster feature, its text as the builder writes it."""
        from app.services.creature_builder import feature_block  # it imports this module

        tables = self._creature_tables
        data: dict[str, Any] | None = None
        if tables is not None and kind == "feature":
            feature = tables.feature(name)
            if feature is not None:
                block = feature_block(feature)
                name = feature.name
                data = {"name": block.name, "entries": [block.text]}
        elif tables is not None and kind in ("trait", "action"):
            template = tables.template(kind, name)
            if template is not None:
                name = template.name
                data = {"name": template.title, "entries": list(template.entries)}
        if data is None:
            raise TargetNotFound(f"builder/{kind}/{name}")
        return Target(
            "builder",
            "builder",
            builder_key(kind, name),
            data,
            BUILDER_FIELDS,
            name,
            (kind, name),
        )

    def builder_all(self) -> list[Target]:
        """Every text of the creature builder: its monster features, traits and actions."""
        tables = self._creature_tables
        if tables is None:
            return []
        names = {
            "feature": [f.name for f in tables.features()],
            "trait": [b.name for b in tables.traits()],
            "action": [b.name for b in tables.actions()],
        }
        out: list[Target] = []
        for kind in BUILDER_KINDS:
            for name in names[kind]:
                try:
                    out.append(self.builder(kind, name))
                except TargetNotFound:
                    continue
        return out

    def section(self, adventure_id: str, section_id: str) -> Target:
        """A section of a published adventure, by the corpus's id."""
        body = self._adventures.body(adventure_id)
        part = body.section(section_id) if body is not None else None
        if body is None or part is None:
            raise TargetNotFound(f"{adventure_id}/{section_id}")
        return Target(
            "adventure",
            "adventure",
            f"{body.adventure.id}|{part.id}",
            part.entry,
            CHAPTER_FIELDS,
            f"{part.name} · {body.adventure.name}",
            (body.adventure.id, part.id),
        )


def builder_key(kind: str, name: str) -> str:
    """Where a creature builder's text is stored: ``creatures|trait|Bite (Cloaker)``."""
    return f"{BUILDER_BOOK}|{kind}|{name}"


__all__ = [
    "BUILDER_BOOK",
    "BUILDER_FIELDS",
    "BUILDER_KINDS",
    "builder_key",
    "FLUFF_OF",
    "fluff_category",
    "Target",
    "TargetKind",
    "TargetNotFound",
    "TranslationTargets",
]
