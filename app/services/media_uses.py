"""Where a world's pictures are used, renaming one with every use, deleting an unused one.

A picture is used by a field — the cover of the world, an adventure or a chapter, an NPC's
portrait, a place's picture, the title image of an encounter, quest or faction — or by a
text that shows it: ``![[name]]`` (found by name in the world, ADVENTURES.md §5.3) or
``![alt](path)`` in a chapter, an adventure, the world's description, the notes of an NPC,
place, treasure or encounter, or a session note. Each is read as the page reading it does,
from the record whose folder its paths start in.

**Renaming** keeps the folder and the file type: the file is moved, its tags with it, and
every field and text that showed it is written again with the new name, its frame and
effects kept. **Deleting** is refused while anything uses the picture.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services import image_effects, media
from app.services.i18n import Translatable
from app.services.media_tags import MediaTags
from app.store.gamestate import GameStateStore
from app.ui.markdown import embedded_image

# Obsidian's ![[file|…]] and Markdown's ![alt|…](path "title").
_WIKI = re.compile(r"!\[\[([^\[\]\n]+?)\]\]")
_MARKDOWN = re.compile(r"!\[([^\]\n]*)\]\((<[^>\n]+>|[^)\s]+)((?:\s+\"[^\"\n]*\")?)\)")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:|^/")
# A file name: letters, digits, spaces, dots, dashes and underscores.
_NAME = re.compile(r"^[\w .\-]+$", re.UNICODE)


class MediaUseError(Translatable):
    """A rename or delete refused: the name, the file, or the picture still in use."""


@dataclass(frozen=True)
class Use:
    """One use of a picture: what kind of thing, its name, and where it is edited."""

    kind: str  # cover, portrait, picture, title, text
    owner: (
        str  # world, adventure, chapter, npc, place, treasure, encounter, quest, faction, session
    )
    name: str
    url: str


@dataclass(frozen=True)
class _Field:
    """A record field holding a picture, and how to write it again."""

    record_id: str  # the record its path starts from
    value: str
    use: Use
    write: Callable[[str], None]


@dataclass(frozen=True)
class _Text:
    """A Markdown text that may show pictures, and how to write it again."""

    record_id: str
    text: str
    use: Use
    write: Callable[[str], None]


class MediaUses:
    """Finds a world's pictures' uses; renames and deletes pictures by them."""

    def __init__(self, store: GameStateStore, tags: MediaTags) -> None:
        self._store = store
        self._tags = tags

    # -- what uses what --------------------------------------------------------------

    def _fields(self, world_id: str) -> Iterator[_Field]:
        store = self._store
        world = store.worlds.find(world_id)
        if world is None:
            return
        if world.cover:
            yield _Field(
                world.id,
                world.cover,
                Use("cover", "world", world.name, f"/gm/worlds/{world.id}/edit"),
                _setter(store.worlds, world.id, "cover"),
            )
        for adventure in store.adventures.list(lambda a: a.world_id == world_id):
            if adventure.cover:
                yield _Field(
                    adventure.id,
                    adventure.cover,
                    Use("cover", "adventure", adventure.name, f"/gm/adventures/{adventure.id}"),
                    _setter(store.adventures, adventure.id, "cover"),
                )
        for chapter in store.chapters.list(lambda c: c.world_id == world_id):
            if chapter.cover:
                yield _Field(
                    chapter.id,
                    chapter.cover,
                    Use("cover", "chapter", chapter.title, f"/gm/chapters/{chapter.id}"),
                    _setter(store.chapters, chapter.id, "cover"),
                )
        for npc in store.npcs.list(lambda n: n.scope.world_id == world_id):
            if npc.portrait:
                yield _Field(
                    npc.scope.record_id,
                    npc.portrait,
                    Use("portrait", "npc", npc.name, f"/gm/npcs/{npc.id}/edit"),
                    _setter(store.npcs, npc.id, "portrait"),
                )
        for place in store.locations.list(lambda x: x.scope.world_id == world_id):
            if place.picture:
                yield _Field(
                    place.scope.record_id,
                    place.picture,
                    Use("title", "place", place.name, f"/gm/places/{place.id}/edit"),
                    _setter(store.locations, place.id, "picture"),
                )
        for encounter in store.encounters.list(lambda e: e.world_id == world_id):
            if encounter.cover:
                yield _Field(
                    world_id,
                    encounter.cover,
                    Use("title", "encounter", encounter.name, f"/gm/encounters/{encounter.id}"),
                    _setter(store.encounters, encounter.id, "cover"),
                )
        for quest in store.quests.list(lambda q: q.world_id == world_id):
            if quest.cover:
                yield _Field(
                    world_id,
                    quest.cover,
                    Use("title", "quest", quest.name, f"/gm/quests?world={world_id}"),
                    _setter(store.quests, quest.id, "cover"),
                )
        for faction in store.factions.list(lambda f: f.world_id == world_id):
            if faction.cover:
                yield _Field(
                    world_id,
                    faction.cover,
                    Use("title", "faction", faction.name, f"/gm/factions?world={world_id}"),
                    _setter(store.factions, faction.id, "cover"),
                )

    def _texts(self, world_id: str) -> Iterator[_Text]:
        store = self._store
        world = store.worlds.find(world_id)
        if world is None:
            return
        yield _Text(
            world.id,
            world.description,
            Use("text", "world", world.name, f"/gm/worlds/{world.id}/edit"),
            _setter(store.worlds, world.id, "description"),
        )
        for adventure in store.adventures.list(lambda a: a.world_id == world_id):
            yield _Text(
                adventure.id,
                adventure.body,
                Use("text", "adventure", adventure.name, f"/gm/adventures/{adventure.id}/edit"),
                _setter(store.adventures, adventure.id, "body"),
            )
        for chapter in store.chapters.list(lambda c: c.world_id == world_id):
            yield _Text(
                chapter.id,
                chapter.body,
                Use("text", "chapter", chapter.title, f"/gm/chapters/{chapter.id}/edit"),
                _setter(store.chapters, chapter.id, "body"),
            )
        owned: list[tuple[str, Any, str]] = [
            ("npc", store.npcs, "/gm/npcs/{}/edit"),
            ("place", store.locations, "/gm/places/{}/edit"),
            ("treasure", store.treasures, "/gm/treasure/{}"),
        ]
        for owner, collection, url in owned:
            for item in collection.list(lambda x: x.scope.world_id == world_id):
                yield _Text(
                    item.scope.record_id,
                    item.notes,
                    Use("text", owner, item.name, url.format(item.id)),
                    _setter(collection, item.id, "notes"),
                )
        for encounter in store.encounters.list(lambda e: e.world_id == world_id):
            where = encounter.scope
            yield _Text(
                where.record_id if where is not None else world_id,
                encounter.notes,
                Use("text", "encounter", encounter.name, f"/gm/encounters/{encounter.id}"),
                _setter(store.encounters, encounter.id, "notes"),
            )
        for note in store.session_notes.list(lambda n: n.world_id == world_id):
            yield _Text(
                note.adventure_id or world_id,
                note.text,
                Use("text", "session", note.day.isoformat(), f"/gm/sessions/{note.id}/edit"),
                _setter(store.session_notes, note.id, "text"),
            )

    def _world_path(self, record_id: str, relative: str) -> str | None:
        return self._tags.world_path(record_id, relative) if relative else None

    def _in_text(self, record_id: str, world_id: str, text: str) -> Iterator[tuple[str, str]]:
        """``(world path, the reference as written)`` of every picture a text shows."""
        for found in _WIKI.finditer(text):
            spec = embedded_image(found.group(1))
            if spec is None:
                continue
            where = media.find(self._store.prose, record_id, world_id, spec[0])
            if where is not None:
                path = self._world_path(where[0], where[1])
                if path:
                    yield path, found.group(0)
        for found in _MARKDOWN.finditer(text):
            target = found.group(2).strip("<>")
            if _SCHEME.match(target):
                continue
            path = self._world_path(record_id, target)
            if path:
                yield path, found.group(0)

    def uses(self, world_id: str) -> dict[str, list[Use]]:
        """Every used picture of the world, by its path from the world's folder."""
        out: dict[str, list[Use]] = {}
        for field in self._fields(world_id):
            path = self._world_path(field.record_id, image_effects.split(field.value)[0])
            if path:
                out.setdefault(path, []).append(field.use)
        for text in self._texts(world_id):
            if not text.text:
                continue
            for path in dict.fromkeys(
                p for p, _ in self._in_text(text.record_id, world_id, text.text)
            ):
                out.setdefault(path, []).append(text.use)
        return out

    # -- renaming and deleting -------------------------------------------------------

    def _file(self, world_id: str, world_path: str) -> Path:
        world = self._store.prose.world_folder(world_id)
        if world is None:
            raise MediaUseError("images.error.gone")
        path = (world / world_path).resolve()
        try:
            path.relative_to(world.resolve())
        except ValueError as exc:
            raise MediaUseError("images.error.gone") from exc
        if not path.is_file() or path.suffix.lower() not in media.IMAGE_TYPES:
            raise MediaUseError("images.error.gone")
        return path

    def rename(self, world_id: str, world_path: str, name: str) -> str:
        """Give a picture a new name in its folder (its file type kept) and write every
        use of it again; the new path from the world's folder."""
        old = self._file(world_id, world_path)
        stem = name.strip()
        if stem.lower().endswith(old.suffix.lower()):
            stem = stem[: -len(old.suffix)]
        stem = stem.strip().strip(".")
        if not stem or not _NAME.match(stem) or "/" in stem:
            raise MediaUseError("images.error.name")
        new = old.with_name(stem + old.suffix.lower())
        if new == old:
            return world_path
        if new.exists():
            raise MediaUseError("images.error.taken")
        new_path = str(Path(world_path).with_name(new.name).as_posix())
        # What showed the picture, read before it moves.
        fields = [
            f
            for f in self._fields(world_id)
            if self._world_path(f.record_id, image_effects.split(f.value)[0]) == world_path
        ]
        texts = [
            (t, [ref for p, ref in self._in_text(t.record_id, world_id, t.text) if p == world_path])
            for t in self._texts(world_id)
            if t.text
        ]
        tags = self._tags.of(world_id, world_path)
        old.rename(new)
        prose = self._store.prose
        for field in fields:
            seen = media.seen_from(prose, field.record_id, new_path)
            if seen:
                _, effects = image_effects.split(field.value)
                field.write(image_effects.join(seen, list(effects)))
        for text, refs in texts:
            if not refs:
                continue
            body = text.text
            for ref in dict.fromkeys(refs):
                body = body.replace(ref, _renamed(ref, new.name, prose, text.record_id, new_path))
            if body != text.text:
                text.write(body)
        if tags:
            self._tags.set(world_id, new_path, tags)
        return new_path

    def delete(self, world_id: str, world_path: str) -> None:
        """Delete a picture nothing uses; refused while anything does."""
        path = self._file(world_id, world_path)
        if self.uses(world_id).get(world_path):
            raise MediaUseError("images.error.in_use")
        path.unlink()
        self._tags.set(world_id, world_path, [])


def _setter(collection: Any, ident: str, field: str) -> Callable[[str], None]:
    """Writes ``field`` of the record ``ident`` again."""

    def write(value: str) -> None:
        collection.update(ident, **{field: value})

    return write


def _renamed(ref: str, new_name: str, prose: Any, record_id: str, new_path: str) -> str:
    """A reference written again for the picture's new name: ``![[old.png|…]]`` names it
    by its new name (with the path it had, if it had one), ``![alt](…)`` by its path."""
    wiki = _WIKI.fullmatch(ref)
    if wiki is not None:
        file, sep, rest = wiki.group(1).partition("|")
        head = file.rsplit("/", 1)[0] + "/" if "/" in file else ""
        return f"![[{head}{new_name}{sep}{rest}]]"
    markdown = _MARKDOWN.fullmatch(ref)
    if markdown is not None:
        seen = media.seen_from(prose, record_id, new_path) or new_name
        return f"![{markdown.group(1)}]({seen}{markdown.group(3)})"
    return ref


__all__ = ["MediaUseError", "MediaUses", "Use"]
