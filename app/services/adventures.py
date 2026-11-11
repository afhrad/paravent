"""Adventures and chapters inside a world (documentation/ADVENTURES.md).

The records are Markdown files (:mod:`app.store.prose`); this service is what the pages
do with them: list in order, create, edit, reorder, delete to the trash, attach images,
and work out what a chapter's text links to.

Order is a number in each file's front matter. Moving a chapter renumbers its siblings
1, 2, 3… so the numbers stay small and readable when the files are opened by hand.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.models.gamestate import Adventure, Chapter, LevelRange, Scope, SourceSet, Status, World
from app.services import image_effects, media
from app.services.i18n import Translatable
from app.store.gamestate import GameStateStore
from app.store.prose import MEDIA_DIR
from app.store.published import AdventureBody, PublishedAdventure, PublishedAdventures, Section

NAME_LIMIT = 120
STATUSES: tuple[Status, ...] = ("draft", "planned", "running", "finished")


class AdventureError(Translatable):
    """A change an adventure or chapter cannot take."""


class EditConflict(AdventureError):
    """The text changed elsewhere — a hand edit, another tab — since the editor opened it.
    ``current`` is the text saved now."""

    def __init__(self, current: str) -> None:
        super().__init__("error.edit_conflict")
        self.current = current


def text_version(text: str) -> str:
    """A fingerprint of a text as an editor opened it, so a save can tell whether the text
    on disk changed meanwhile. Only the text: a new order or title elsewhere is no clash."""
    return hashlib.sha256(text.strip().encode()).hexdigest()[:16]


def check_base(current: str, mine: str, base: str | None) -> None:
    """Refuse a save made from an older text, unless it says the same as the text now."""
    if base and text_version(current) not in (base, text_version(mine)):
        raise EditConflict(current)


class AdventureNotFound(LookupError):
    """No adventure or chapter with that id."""


@dataclass(frozen=True)
class AdventureView:
    """An adventure with its world and its chapters, in order."""

    adventure: Adventure
    world: World
    chapters: tuple[Chapter, ...]
    book: PublishedAdventure | None = None

    @property
    def source_set(self) -> SourceSet:
        """The books its text resolves against, its published adventure's included."""
        return self.adventure.effective_source_set(
            self.world, self.book.source if self.book else None
        )


@dataclass(frozen=True)
class ChapterView:
    """A chapter with everything around it: its adventure, world and neighbours."""

    chapter: Chapter
    adventure: Adventure
    world: World
    previous: Chapter | None
    next: Chapter | None
    book: PublishedAdventure | None = None

    @property
    def source_set(self) -> SourceSet:
        """The books its text resolves against, its published adventure's included."""
        return self.adventure.effective_source_set(
            self.world, self.book.source if self.book else None
        )


def _ordered(records: Sequence[Adventure | Chapter]) -> list[Adventure | Chapter]:
    def name(r: Adventure | Chapter) -> str:
        return (r.name if isinstance(r, Adventure) else r.title).casefold()

    return sorted(records, key=lambda r: (r.order, name(r)))


def _within(where: Scope, here: Scope) -> bool:
    """``where`` lies inside ``here`` — an adventure's objects include its chapters'."""
    if where.world_id != here.world_id:
        return False
    if here.chapter_id is not None:
        return where.chapter_id == here.chapter_id
    return here.adventure_id is None or where.adventure_id == here.adventure_id


def clean_name(name: str) -> str:
    """One line, trimmed; an empty name is refused."""
    cleaned = " ".join(name.split())[:NAME_LIMIT]
    if not cleaned:
        raise AdventureError("adventures.error.name")
    return cleaned


class AdventureService:
    """Adventures and chapters, over the library's Markdown files."""

    def __init__(self, store: GameStateStore, published: PublishedAdventures | None = None) -> None:
        """Bind to the library, and to the corpus's published adventures if there are any."""
        self._store = store
        self.published = published or PublishedAdventures(None)

    # -- reading ---------------------------------------------------------------------

    def world(self, world_id: str) -> World:
        """The world, or :class:`AdventureNotFound`."""
        world = self._store.worlds.find(world_id)
        if world is None:
            raise AdventureNotFound(world_id)
        return world

    def adventures(self, world_id: str) -> list[Adventure]:
        """A world's adventures, in order."""
        found = self._store.adventures.list(lambda a: a.world_id == world_id)
        return [a for a in _ordered(found) if isinstance(a, Adventure)]

    def get(self, adventure_id: str) -> Adventure:
        """One adventure, or :class:`AdventureNotFound`."""
        adventure = self._store.adventures.find(adventure_id)
        if adventure is None:
            raise AdventureNotFound(adventure_id)
        return adventure

    def chapters(self, adventure_id: str) -> list[Chapter]:
        """An adventure's chapters, in order."""
        found = self._store.chapters.list(lambda c: c.adventure_id == adventure_id)
        return [c for c in _ordered(found) if isinstance(c, Chapter)]

    def get_chapter(self, chapter_id: str) -> Chapter:
        """One chapter, or :class:`AdventureNotFound`."""
        chapter = self._store.chapters.find(chapter_id)
        if chapter is None:
            raise AdventureNotFound(chapter_id)
        return chapter

    def view(self, adventure_id: str) -> AdventureView:
        """An adventure with its world and chapters."""
        adventure = self.get(adventure_id)
        return AdventureView(
            adventure,
            self.world(adventure.world_id),
            tuple(self.chapters(adventure_id)),
            self.published.get(adventure.based_on),
        )

    def chapter_view(self, chapter_id: str) -> ChapterView:
        """A chapter with its adventure, world and neighbours."""
        chapter = self.get_chapter(chapter_id)
        adventure = self.get(chapter.adventure_id)
        siblings = self.chapters(adventure.id)
        index = next(i for i, c in enumerate(siblings) if c.id == chapter.id)
        return ChapterView(
            chapter,
            adventure,
            self.world(adventure.world_id),
            siblings[index - 1] if index > 0 else None,
            siblings[index + 1] if index + 1 < len(siblings) else None,
            self.published.get(adventure.based_on),
        )

    def book_section(self, view: ChapterView) -> tuple[AdventureBody, Section] | None:
        """The published text a chapter shows, or None for a chapter of its own."""
        if view.book is None or not view.chapter.section:
            return None
        body = self.published.body(view.book.id)
        section = body.section(view.chapter.section) if body else None
        return (body, section) if body is not None and section is not None else None

    def chapter_count(self, adventure_id: str) -> int:
        """How many chapters an adventure has."""
        return len(self._store.chapters.list(lambda c: c.adventure_id == adventure_id))

    def find_chapter(
        self, world_id: str, title: str, *, prefer: str | None = None
    ) -> Chapter | None:
        """A chapter of this world by title, the ``prefer`` adventure's own first."""
        wanted = title.strip().casefold()
        adventures = self.adventures(world_id)
        adventures.sort(key=lambda a: a.id != prefer)
        for adventure in adventures:
            for chapter in self.chapters(adventure.id):
                if chapter.title.casefold() == wanted:
                    return chapter
        return None

    # -- adventures ------------------------------------------------------------------

    def create(self, world_id: str, name: str) -> Adventure:
        """A new adventure at the end of the world's list."""
        self.world(world_id)
        order = max((a.order for a in self.adventures(world_id)), default=0) + 1
        return self._store.adventures.create(
            Adventure(world_id=world_id, name=clean_name(name), order=order)
        )

    def create_from_book(self, world_id: str, book_id: str) -> Adventure:
        """An adventure on top of a published one: one chapter per section of the book.

        Nothing of the book is copied. Each chapter names its section and starts with an
        empty body for the gamemaster's own notes; the text is read from the corpus.
        """
        self.world(world_id)
        book = self.published.get(book_id)
        body = self.published.body(book_id) if book else None
        if book is None or body is None:
            raise AdventureError("adventures.error.book", book=book_id)
        order = max((a.order for a in self.adventures(world_id)), default=0) + 1
        level = None
        if book.level_start:
            end = book.level_end or book.level_start
            level = LevelRange(start=book.level_start, end=max(end, book.level_start))
        adventure = self._store.adventures.create(
            Adventure(
                world_id=world_id,
                name=clean_name(book.name),
                order=order,
                level=level,
                based_on=book.id,
            )
        )
        for position, section in enumerate(body.sections, start=1):
            self._store.chapters.create(
                Chapter(
                    world_id=world_id,
                    adventure_id=adventure.id,
                    title=clean_name(section.name or f"{position}"),
                    order=position,
                    section=section.id,
                )
            )
        return adventure

    def update(
        self,
        adventure_id: str,
        *,
        name: str,
        summary: str,
        status: str,
        level_start: int | None,
        level_end: int | None,
        body: str,
        base: str | None = None,
    ) -> Adventure:
        """Save an adventure's fields and its text. ``base`` is the :func:`text_version`
        the editor opened; a text changed since raises :class:`EditConflict`."""
        check_base(self.get(adventure_id).body, body, base)
        level = None
        if level_start or level_end:
            low = max(1, min(20, level_start or 1))
            high = max(low, min(20, level_end or low))
            level = LevelRange(start=low, end=high)
        return self._store.adventures.update(
            adventure_id,
            name=clean_name(name),
            summary=" ".join(summary.split()),
            status=status if status in STATUSES else "planned",
            level=level,
            body=body,
        )

    def set_party(self, adventure_id: str, character_ids: Sequence[str]) -> Adventure:
        """The characters an adventure is written for; none means the world's
        (documentation/MCP.md §6). Ids of characters not in its world are dropped."""
        adventure = self.get(adventure_id)
        present = {
            pc.id for pc in self._store.characters.list() if pc.world_id == adventure.world_id
        }
        chosen = [i for i in dict.fromkeys(character_ids) if i in present]
        return self._store.adventures.update(adventure_id, party=chosen or None)

    def delete(self, adventure_id: str, *, contents: bool = False) -> None:
        """Move an adventure's folder, chapters and images included, to the trash.

        NPCs, places, treasure and encounters placed in it move up to the world, so
        deleting the text never deletes the things the text placed — unless
        ``contents``, which deletes them with it (cleaning up a generated adventure).
        Fights already run stay as history; session notes lose the adventure's name.
        """
        self.get(adventure_id)
        self._store.adventures.delete(adventure_id)
        if contents:
            self._remove(adventure_id=adventure_id)
        else:
            self._promote(adventure_id=adventure_id)
        for note in self._store.session_notes.list(lambda n: n.adventure_id == adventure_id):
            self._store.session_notes.update(note.id, adventure_id=None)

    def contents(self, here: Scope) -> dict[str, int]:
        """How many NPCs, places, encounters and treasures lie in ``here`` (an
        adventure or a chapter), for the delete page."""
        store = self._store
        return {
            "npcs": len(store.npcs.list(lambda x: _within(x.scope, here))),
            "places": len(store.locations.list(lambda x: _within(x.scope, here))),
            "encounters": len(
                store.encounters.list(lambda x: x.scope is not None and _within(x.scope, here))
            ),
            "treasure": len(store.treasures.list(lambda x: _within(x.scope, here))),
        }

    def _remove(self, *, adventure_id: str | None = None, chapter_id: str | None = None) -> None:
        """Delete what is placed in an adventure or a chapter."""

        def inside(where: Scope | None) -> bool:
            if where is None:
                return False
            if chapter_id is not None:
                return where.chapter_id == chapter_id
            return adventure_id is not None and where.adventure_id == adventure_id

        for collection in (self._store.npcs, self._store.locations, self._store.treasures):
            for record in collection.list(lambda r: inside(r.scope)):
                collection.delete(record.id)
        for encounter in self._store.encounters.list(lambda e: inside(e.scope)):
            self._store.encounters.delete(encounter.id)

    def _promote(self, *, adventure_id: str | None = None, chapter_id: str | None = None) -> None:
        def lift(scope: Scope) -> Scope | None:
            if chapter_id is not None and scope.chapter_id == chapter_id:
                return scope.model_copy(update={"chapter_id": None})
            if adventure_id is not None and scope.adventure_id == adventure_id:
                return Scope(world_id=scope.world_id)
            return None

        for npc in self._store.npcs.list():
            lifted = lift(npc.scope)
            if lifted is not None:
                self._store.npcs.update(npc.id, scope=lifted)
        for treasure in self._store.treasures.list():
            lifted = lift(treasure.scope)
            if lifted is not None:
                self._store.treasures.update(treasure.id, scope=lifted)
        for location in self._store.locations.list():
            lifted = lift(location.scope)
            if lifted is not None:
                self._store.locations.update(location.id, scope=lifted)
        for encounter in self._store.encounters.list():
            current = encounter.scope
            lifted = lift(current) if current is not None else None
            if lifted is not None:
                self._store.encounters.update(
                    encounter.id, adventure_id=lifted.adventure_id, chapter_id=lifted.chapter_id
                )

    # -- chapters --------------------------------------------------------------------

    def create_chapter(self, adventure_id: str, title: str) -> Chapter:
        """A new chapter at the end of the adventure."""
        adventure = self.get(adventure_id)
        order = max((c.order for c in self.chapters(adventure_id)), default=0) + 1
        return self._store.chapters.create(
            Chapter(
                world_id=adventure.world_id,
                adventure_id=adventure_id,
                title=clean_name(title),
                order=order,
            )
        )

    def update_chapter(
        self, chapter_id: str, *, title: str, status: str, body: str, base: str | None = None
    ) -> Chapter:
        """Save a chapter's title, status and text; a new title follows into the texts.
        ``base`` is the :func:`text_version` the editor opened; a text changed since raises
        :class:`EditConflict`."""
        before = self.get_chapter(chapter_id)
        check_base(before.body, body, base)
        saved = self._store.chapters.update(
            chapter_id,
            title=clean_name(title),
            status=status if status in STATUSES else "planned",
            body=body,
        )
        if saved.title != before.title:
            self.rewrite_references(
                saved.world_id, "chapter", before.title, saved.title, saved.adventure_id
            )
        return saved

    def delete_chapter(self, chapter_id: str, *, contents: bool = False) -> Chapter:
        """Move a chapter's file to the trash; returns what it was. What is placed in it
        moves up to the adventure, or with ``contents`` is deleted with it."""
        chapter = self.get_chapter(chapter_id)
        self._store.chapters.delete(chapter_id)
        if contents:
            self._remove(chapter_id=chapter_id)
        else:
            self._promote(chapter_id=chapter_id)
        return chapter

    # -- references ------------------------------------------------------------------

    def rewrite_references(
        self, world_id: str, kind: str, old: str, new: str, within: str | None = None
    ) -> int:
        """After a rename, point ``{@kind old…}`` and ``::: kind old`` at the new name.

        Every text in the world is checked — its description, its adventures, their
        chapters — and only the ones that change are written. Returns how many. ``within``
        is the adventure the renamed record lies in: a rename in a revision stays in it
        until it is accepted (documentation/MCP.md §9).
        """
        if not old.strip() or old == new:
            return 0
        name = re.escape(old.strip())
        tag = re.compile(rf"(\{{@{kind}\s+){name}(?=\s*[|}}])", re.IGNORECASE)
        block = re.compile(rf"^(\s*:{{3,}}\s*{kind}\s+){name}\s*$", re.IGNORECASE | re.MULTILINE)

        def fix(text: str) -> str:
            return block.sub(lambda m: m.group(1) + new, tag.sub(lambda m: m.group(1) + new, text))

        copy = self._store.adventures.find(within) if within else None
        if copy is not None and copy.revision_of:
            return self._rewrite_adventure(copy, fix)
        return self._rewrite_world(world_id, fix)

    def rewrite_all_texts(self, fix: Callable[[str], str]) -> int:
        """Apply ``fix`` to every text of every world — descriptions, adventures, chapters,
        and the notes of NPCs, treasure, encounters and places — writing only those that
        change.
        Returns how many did."""
        changed = sum(self._rewrite_world(world.id, fix) for world in self._store.worlds.list())
        for collection in (
            self._store.npcs,
            self._store.treasures,
            self._store.encounters,
            self._store.locations,
        ):
            for record in collection.list():
                if fix(record.notes) != record.notes:
                    collection.update(record.id, notes=fix(record.notes))
                    changed += 1
        return changed

    def _rewrite_world(self, world_id: str, fix: Callable[[str], str]) -> int:
        changed = 0
        world = self.world(world_id)
        if fix(world.description) != world.description:
            self._store.worlds.update(world_id, description=fix(world.description))
            changed += 1
        for adventure in self.adventures(world_id):
            if not adventure.revision_of:  # a revision keeps what it was copied with
                changed += self._rewrite_adventure(adventure, fix)
        return changed

    def _rewrite_adventure(self, adventure: Adventure, fix: Callable[[str], str]) -> int:
        changed = 0
        if fix(adventure.body) != adventure.body:
            self._store.adventures.update(adventure.id, body=fix(adventure.body))
            changed += 1
        for chapter in self.chapters(adventure.id):
            if fix(chapter.body) != chapter.body:
                self._store.chapters.update(chapter.id, body=fix(chapter.body))
                changed += 1
        return changed

    # -- order -----------------------------------------------------------------------

    def move(self, kind: Literal["adventure", "chapter"], record_id: str, step: int) -> None:
        """Move an adventure or chapter one place up (-1) or down (+1)."""
        siblings: list[Adventure] | list[Chapter]
        if kind == "adventure":
            siblings = self.adventures(self.get(record_id).world_id)
        else:
            siblings = self.chapters(self.get_chapter(record_id).adventure_id)
        ids = [s.id for s in siblings]
        index = ids.index(record_id)
        target = index + (1 if step > 0 else -1)
        if not 0 <= target < len(ids):
            return
        ids[index], ids[target] = ids[target], ids[index]
        collection = self._store.adventures if kind == "adventure" else self._store.chapters
        for position, sibling_id in enumerate(ids, start=1):
            current = collection.find(sibling_id)
            if current is not None and current.order != position:
                collection.update(sibling_id, order=position)

    # -- images ----------------------------------------------------------------------

    def _kind_of(self, record_id: str) -> Literal["world", "adventure", "chapter"]:
        if self._store.worlds.find(record_id) is not None:
            return "world"
        if self._store.adventures.find(record_id) is not None:
            return "adventure"
        if self._store.chapters.find(record_id) is not None:
            return "chapter"
        raise AdventureNotFound(record_id)

    def upload(self, record_id: str, filename: str, data: bytes, *, as_cover: bool) -> str:
        """Store an image beside the record; with ``as_cover`` it becomes the cover."""
        self._kind_of(record_id)
        relative = media.store_upload(self._store.prose, record_id, filename, data)
        if as_cover:
            self.set_cover(record_id, relative)
        return relative

    def set_cover(self, record_id: str, relative: str | None) -> None:
        """Use one of the record's images as its cover, or none; ``relative`` may carry
        its effects (``media/x.jpg|torn``, image_effects.py)."""
        kind = self._kind_of(record_id)
        path = image_effects.split(relative)[0]
        if relative and media.resolve(self._store.prose, record_id, path) is None:
            raise AdventureError("adventures.error.cover")
        cover = relative or None
        if kind == "world":
            self._store.worlds.update(record_id, cover=cover)
        elif kind == "adventure":
            self._store.adventures.update(record_id, cover=cover)
        else:
            self._store.chapters.update(record_id, cover=cover)

    def images(self, record_id: str) -> list[str]:
        """The images in the record's own ``media/`` folder, as paths to write."""
        folder = self._store.prose.folder(record_id)
        if folder is None:
            return []
        directory = folder / MEDIA_DIR
        if not directory.is_dir():
            return []
        return [
            f"{MEDIA_DIR}/{path.name}"
            for path in sorted(directory.iterdir())
            if path.suffix.lower() in media.IMAGE_TYPES and not path.name.startswith(".")
        ]

    def media_path(self, record_id: str, relative: str) -> Path | None:
        """The image file a record's text or cover names, if it may be served."""
        return media.resolve(self._store.prose, record_id, relative)

    def find_media(self, record_id: str, world_id: str, name: str) -> tuple[str, str] | None:
        """``(record, path)`` of the image ``![[name]]`` means in a record's text."""
        return media.find(self._store.prose, record_id, world_id, name)


__all__ = [
    "STATUSES",
    "AdventureError",
    "AdventureNotFound",
    "AdventureService",
    "AdventureView",
    "ChapterView",
    "EditConflict",
    "check_base",
    "clean_name",
    "text_version",
]
