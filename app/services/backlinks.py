"""What references this? — the reverse of every link (documentation/PROJECT.md).

**The corpus** is indexed once per load: every entity tag (``{@spell fireball}``) in
every entry, filed under the entry that contains it — the creature whose spellcasting
names the spell, the item that holds it — by the target's tag, name and, when the tag
names one, book. About 0.3 s for the whole corpus.

**Resolution happens when a page asks**, because it depends on the world's books
(TECHSTACK.md §6c): a bare ``{@spell fireball}`` counts for the printing the world
resolves it to, never for every Fireball. A tag naming its book counts for that book's
entry only. Entries from books the world does not declare are left out, as search does.
Without a world (no books declared) a bare name counts for every printing of it.

**The user's own content** — chapters, NPCs, treasure, encounters — is read live: it is
small and changes all the time. Its tags resolve the same way; its structured references
(an NPC's statblock, a treasure's items, an encounter's monsters) name their book.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.models.gamestate import SourceSet
from app.store.gamestate import GameStateStore
from app.store.published import PublishedBooks
from app.store.reference import parse_reference

# Feature categories carry their class: "Potent Spellcasting" alone says little.
_OWNER_CONTEXT = {"classFeature": "className", "subclassFeature": "className"}


@dataclass(frozen=True)
class Owner:
    """An entry that links to the one asked about."""

    category: str
    name: str
    source: str
    context: str = ""
    # A book's chapter is not a compendium entry: it carries its own link.
    url: str = ""


@dataclass(frozen=True)
class Mention:
    """One tag in one owner: whom it names, and the book if the tag says."""

    owner: Owner
    source: str | None


@dataclass(frozen=True)
class LibraryLink:
    """Something of the user's that mentions the entry."""

    kind: str  # "chapter", "npc", "place", "treasure" or "encounter"
    record_id: str
    name: str
    context: str = ""


@dataclass(frozen=True)
class Backlinks:
    """Who links to an entry, by the linking entry's category, and in the campaign."""

    groups: tuple[tuple[str, tuple[Owner, ...]], ...]
    library: tuple[LibraryLink, ...] = ()

    @property
    def total(self) -> int:
        """How many entries and records link here."""
        return sum(len(owners) for _c, owners in self.groups) + len(self.library)


class BacklinkIndex:
    """The reverse link index over the compendium, plus the library read live."""

    def __init__(
        self,
        compendium: Any,
        store: GameStateStore,
        tags: Mapping[str, tuple[str, ...]],
        books: PublishedBooks | None = None,
    ) -> None:
        """Bind to the compendium, the library and the renderer's tag → category table.

        With ``books``, every rulebook chapter is indexed too, as a referrer of its own.
        """
        self._compendium = compendium
        self._books = books
        self._store = store
        self._tags = tags
        self._pattern = re.compile(r"\{@(" + "|".join(map(re.escape, tags)) + r") ([^{}]*)\}")
        self._for: Any = None
        # tag → lower-case name → mentions
        self._index: dict[str, dict[str, list[Mention]]] = {}

    # -- building --------------------------------------------------------------------

    def _ensure(self) -> dict[str, dict[str, list[Mention]]]:
        loaded = getattr(self._compendium, "projections", None)
        if self._for is loaded and self._index:
            return self._index
        index: dict[str, dict[str, list[Mention]]] = defaultdict(lambda: defaultdict(list))
        for category in self._compendium.categories():
            context_field = _OWNER_CONTEXT.get(category)
            for entry in self._compendium.all(category):
                owner = Owner(
                    category,
                    str(getattr(entry, "name", "")),
                    str(getattr(entry, "source", "")),
                    str(getattr(entry, context_field, "") or "") if context_field else "",
                )
                if category == "card":
                    # A card has no page of its own: what it mentions, its deck does.
                    owner = Owner("deck", str(getattr(entry, "set", "")), owner.source)
                text = entry.model_dump_json(by_alias=True, exclude_none=True)
                seen: set[tuple[str, str, str | None]] = set()
                for tag, payload in self._pattern.findall(text):
                    name, source, _display = parse_reference(payload, tag)
                    key = (tag, name.lower(), source.lower() if source else None)
                    if key in seen:
                        continue
                    seen.add(key)
                    index[tag][name.lower()].append(Mention(owner, source))
        for owner, text in self._book_texts():
            seen = set()
            for tag, payload in self._pattern.findall(text):
                name, source, _display = parse_reference(payload, tag)
                key = (tag, name.lower(), source.lower() if source else None)
                if key in seen:
                    continue
                seen.add(key)
                index[tag][name.lower()].append(Mention(owner, source))
        self._index = {tag: dict(names) for tag, names in index.items()}
        self._for = loaded
        return self._index

    def _book_texts(self) -> Iterable[tuple[Owner, str]]:
        """Each rulebook chapter as a referrer, with its text."""
        if self._books is None:
            return
        for book in self._books.all():
            body = self._books.body(book.id)
            if body is None:
                continue
            for index, chapter in enumerate(body.sections):
                owner = Owner(
                    "book", chapter.name, book.source, book.name, f"/books/{book.id}/{index}"
                )
                yield owner, json.dumps(chapter.entry)

    def _tags_for(self, category: str) -> list[str]:
        return [tag for tag, categories in self._tags.items() if category in categories]

    # -- asking ----------------------------------------------------------------------

    def _is_entry(
        self,
        category: str,
        name: str,
        source: str,
        tag: str,
        mentioned: str,
        mention_source: str | None,
        source_set: SourceSet | None,
        resolved: dict[tuple[str, str], tuple[str, str] | None],
    ) -> bool:
        """Does this tag mean the entry? Its book if it names one; else resolution.

        With no books declared, no world decides which printing a bare name means, so it
        counts for every printing of that name (the names already match).
        """
        if mention_source is not None:
            return mention_source.lower() == source.lower()
        if source_set is None or source_set.is_unrestricted:
            return True
        key = (tag, mentioned.lower())
        if key not in resolved:
            found = None
            resolver = self._compendium.resolver()
            for target in self._tags[tag]:
                result = resolver.resolve_parts(target, mentioned, None, source_set)
                if result.entry is not None:
                    entry = result.entry
                    found = (target, f"{getattr(entry, 'name', '')}|{getattr(entry, 'source', '')}")
                    break
            resolved[key] = found
        hit = resolved[key]
        return (
            hit is not None and hit[0] == category and hit[1].lower() == f"{name}|{source}".lower()
        )

    def links_to(
        self,
        category: str,
        name: str,
        source: str,
        *,
        source_set: SourceSet | None = None,
        world_id: str | None = None,
    ) -> Backlinks:
        """Every entry and library record that links to ``category``/``name``/``source``."""
        index = self._ensure()
        resolved: dict[tuple[str, str], tuple[str, str] | None] = {}
        restricted = source_set is not None and not source_set.is_unrestricted
        owners: dict[str, dict[tuple[str, str, str], Owner]] = defaultdict(dict)
        for tag in self._tags_for(category):
            for mention in index.get(tag, {}).get(name.lower(), ()):
                owner = mention.owner
                if (owner.category, owner.name.lower(), owner.source.lower()) == (
                    category,
                    name.lower(),
                    source.lower(),
                ):
                    continue  # an entry naming itself
                if restricted and source_set is not None and not source_set.allows(owner.source):
                    continue
                if not self._is_entry(
                    category, name, source, tag, name, mention.source, source_set, resolved
                ):
                    continue
                owners[owner.category][(owner.name.lower(), owner.source, owner.context)] = owner
        groups = tuple(
            (
                cat,
                tuple(sorted(found.values(), key=lambda o: (o.name.lower(), o.source))),
            )
            for cat, found in sorted(owners.items())
        )
        library = tuple(self._library(category, name, source, source_set, world_id, resolved))
        return Backlinks(groups, library)

    # -- the user's own content ------------------------------------------------------

    def _mentions(
        self,
        text: str,
        category: str,
        name: str,
        source: str,
        source_set: SourceSet | None,
        resolved: dict[tuple[str, str], tuple[str, str] | None],
    ) -> bool:
        wanted = set(self._tags_for(category))
        for tag, payload in self._pattern.findall(text or ""):
            if tag not in wanted:
                continue
            mentioned, mention_source, _display = parse_reference(payload, tag)
            if mentioned.lower() != name.lower():
                continue
            if self._is_entry(
                category, name, source, tag, mentioned, mention_source, source_set, resolved
            ):
                return True
        return False

    def _library(
        self,
        category: str,
        name: str,
        source: str,
        source_set: SourceSet | None,
        world_id: str | None,
        resolved: dict[tuple[str, str], tuple[str, str] | None],
    ) -> Iterable[LibraryLink]:
        store = self._store
        ref = f"{name}|{source}".lower()

        def in_world(record_world: str | None) -> bool:
            return world_id is None or record_world == world_id

        def says(text: str) -> bool:
            return self._mentions(text, category, name, source, source_set, resolved)

        adventures = {a.id: a.name for a in store.adventures.list()}
        for chapter in store.chapters.list(lambda c: in_world(c.world_id)):
            if says(chapter.body):
                yield LibraryLink(
                    "chapter", chapter.id, chapter.title, adventures.get(chapter.adventure_id, "")
                )
        for npc in store.npcs.list(lambda n: in_world(n.scope.world_id)):
            by_statblock = category == "monster" and (npc.statblock or "").lower() == ref
            if by_statblock or says(npc.notes):
                yield LibraryLink("npc", npc.id, npc.name)
        for location in store.locations.list(lambda x: in_world(x.scope.world_id)):
            if says(location.notes):
                yield LibraryLink("place", location.id, location.name)
        for treasure in store.treasures.list(lambda t: in_world(t.scope.world_id)):
            holds = category in ("item", "baseitem") and any(
                i.ref.lower() == ref for i in treasure.items
            )
            if holds or says(treasure.notes):
                yield LibraryLink("treasure", treasure.id, treasure.name)
        if category == "monster":
            for encounter in store.encounters.list(lambda e: in_world(e.world_id)):
                if any(f"{m.name}|{m.source}".lower() == ref for m in encounter.monsters):
                    yield LibraryLink("encounter", encounter.id, encounter.name)


__all__ = ["BacklinkIndex", "Backlinks", "LibraryLink", "Mention", "Owner"]
