"""The players' journal (documentation/JOURNAL.md): what the gamemaster hands to chosen
characters, as a frozen copy.

A box of a chapter or a picture in it is shared with characters of its world. The box's
Markdown is copied as it reads then, a nested secret taken out, and every picture it
shows is copied beside it; a picture is copied with its effects. So a later edit of the
chapter never reaches the players, and they never need the gamemaster's media route.
Sharing writes one private entry per character to the log, which pops up as a toast on
the player's screen; the journal marks a handout unread until its player opens it.
"""

from __future__ import annotations

import copy
import re
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.models.gamestate import Handout
from app.models.pc import DraftStatus
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services import image_effects, media
from app.services.adventures import AdventureService, ChapterView
from app.services.auth import Identity
from app.services.characters import CharacterService
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.store.gamestate import GameStateStore
from app.ui.markdown import Callout, callouts

# ``![[name|…]]`` and ``![alt](path)``: the pictures a box shows.
_WIKI_IMAGE = re.compile(r"(!\[\[)([^\]|\n]+)")
_MD_IMAGE = re.compile(r"(!\[[^\]\n]*\]\()([^)\s]+)")


class HandoutError(Translatable):
    """A handout that cannot be made or read."""


class HandoutService:
    """Shares, lists, opens and withdraws handouts."""

    def __init__(
        self,
        gamestate: GameStateStore,
        adventures: AdventureService,
        characters: CharacterService,
        protocol: ProtocolService,
        root: Path,
    ) -> None:
        self.gamestate = gamestate
        self.adventures = adventures
        self.characters = characters
        self.protocol = protocol
        self.root = root

    # -- what may be shared ---------------------------------------------------------

    def box(self, chapter_id: str, key: str) -> Callout | None:
        """The box of the chapter its share button names, or None (a secret never)."""
        view = self.adventures.chapter_view(chapter_id)
        return next((c for c in callouts(view.chapter.body) if c.key == key), None)

    def picture(self, chapter_id: str, spec: str) -> Path | None:
        """The file a picture of the chapter names (``map.png|torn``), or None."""
        view = self.adventures.chapter_view(chapter_id)
        return self._find(view, image_effects.split(spec)[0])

    def _find(self, view: ChapterView, name: str) -> Path | None:
        record = view.chapter.id
        direct = self.adventures.media_path(record, name)
        if direct is not None:
            return direct
        found = self.adventures.find_media(record, view.world.id, name)
        return self.adventures.media_path(*found) if found is not None else None

    def recipients(self, world_id: str) -> list[tuple[str, str]]:
        """``(id, name)`` of the characters a handout of this world may go to: those in
        play, not a draft still in the wizard nor one retired."""
        return sorted(
            (
                (pc.id, pc.name or pc.id)
                for pc in self.characters.in_world(world_id)
                if pc.status is DraftStatus.READY
            ),
            key=lambda item: item[1].lower(),
        )

    # -- sharing --------------------------------------------------------------------

    def share_box(
        self,
        chapter_id: str,
        key: str,
        character_ids: Sequence[str],
        title: str,
        by: Identity,
    ) -> Handout:
        """Hand a chapter's box to ``character_ids``, its pictures copied with it."""
        view = self.adventures.chapter_view(chapter_id)
        found = next((c for c in callouts(view.chapter.body) if c.key == key), None)
        if found is None:
            raise HandoutError("journal.error.gone")
        handout = self._new(view, character_ids, title or found.title, by, kind="box")
        handout.callout = found.kind
        handout.text = self._copy_pictures(view, handout.id, found.text)
        return self._publish(view, handout, by)

    def share_image(
        self,
        chapter_id: str,
        spec: str,
        character_ids: Sequence[str],
        title: str,
        by: Identity,
    ) -> Handout:
        """Hand a chapter's picture to ``character_ids``, with its effects."""
        view = self.adventures.chapter_view(chapter_id)
        name, effects = image_effects.split(spec)
        source = self._find(view, name)
        if source is None:
            raise HandoutError("journal.error.gone")
        handout = self._new(view, character_ids, title, by, kind="image")
        handout.image = image_effects.join(self._copy(source, handout.id), effects)
        return self._publish(view, handout, by)

    def share_entry(
        self,
        chapter_id: str,
        entry: dict[str, Any],
        character_ids: Sequence[str],
        title: str,
        by: Identity,
    ) -> Handout:
        """Hand a box of the chapter's book text to ``character_ids``, as it reads now."""
        view = self.adventures.chapter_view(chapter_id)
        name = entry.get("name")
        handout = self._new(
            view, character_ids, title or (name if isinstance(name, str) else ""), by, kind="box"
        )
        handout.callout = str(entry.get("type", ""))
        handout.entry = copy.deepcopy(entry)
        return self._publish(view, handout, by)

    def share_file(
        self,
        chapter_id: str,
        source: Path,
        character_ids: Sequence[str],
        title: str,
        by: Identity,
    ) -> Handout:
        """Hand a picture found outside the chapter's folder (a book's) to the characters."""
        view = self.adventures.chapter_view(chapter_id)
        handout = self._new(view, character_ids, title, by, kind="image")
        handout.image = self._copy(source, handout.id)
        return self._publish(view, handout, by)

    def _new(
        self,
        view: ChapterView,
        character_ids: Sequence[str],
        title: str,
        by: Identity,
        *,
        kind: str,
    ) -> Handout:
        allowed = {pc_id for pc_id, _ in self.recipients(view.world.id)}
        chosen = [c for c in dict.fromkeys(character_ids) if c in allowed]
        if not chosen:
            raise HandoutError("journal.error.nobody")
        return Handout(
            world_id=view.world.id,
            chapter_id=view.chapter.id,
            kind="box" if kind == "box" else "image",
            title=title.strip() or view.chapter.title,
            characters=chosen,
            shared_by=by.user.username,
        )

    def _copy(self, source: Path, handout_id: str) -> str:
        """Copy a picture into the handout's folder; its name there."""
        folder = self.root / handout_id
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / source.name
        number = 2
        while target.exists() and target.read_bytes() != source.read_bytes():
            target = folder / f"{source.stem}-{number}{source.suffix}"
            number += 1
        shutil.copyfile(source, target)
        return target.name

    def _copy_pictures(self, view: ChapterView, handout_id: str, text: str) -> str:
        """``text`` with each picture it shows copied beside the handout and named by
        its file name there; a picture not found stays as written (and shows nothing)."""

        def swap(match: re.Match[str]) -> str:
            source = self._find(view, match.group(2).strip())
            if source is None:
                return match.group(0)
            return f"{match.group(1)}{self._copy(source, handout_id)}"

        return _MD_IMAGE.sub(swap, _WIKI_IMAGE.sub(swap, text))

    def _publish(self, view: ChapterView, handout: Handout, by: Identity) -> Handout:
        self.gamestate.handouts.create(handout)
        names = dict(self.recipients(view.world.id))
        scope = Scope(world=view.world.id, adventure=view.adventure.id, chapter=view.chapter.id)
        self.protocol.append(
            ProtocolEntry(
                kind=EntryKind.HANDOUT,
                actor=by.user.username,
                actor_name=by.user.display_name or by.user.username,
                subject=f"character:{character}",
                subject_name=names.get(character, character),
                what=handout.id,
                note=handout.title,
                visibility=Visibility.PRIVATE,
                scope=scope,
                origin="table",
            )
            for character in handout.characters
        )
        return handout

    # -- reading --------------------------------------------------------------------

    def _own(self, identity: Identity) -> set[str]:
        return {pc.id for pc in self.characters.for_player(identity)}

    def may_read(self, identity: Identity, handout: Handout) -> bool:
        """The gamemaster reads every handout; a player those given to their characters."""
        if identity.is_gamemaster:
            return True
        return bool(self._own(identity) & set(handout.characters))

    def readable(self, identity: Identity, world_id: str | None = None) -> list[Handout]:
        """The handouts ``identity`` may read, newest first; the gamemaster's by world."""
        own = None if identity.is_gamemaster else self._own(identity)
        found = [
            h
            for h in self.gamestate.handouts.list()
            if (own is None or own & set(h.characters))
            and (world_id is None or h.world_id == world_id)
        ]
        return sorted(found, key=lambda h: h.created_at, reverse=True)

    def unread(self, identity: Identity) -> int:
        """How many of a player's handouts they have not opened."""
        name = identity.user.username
        return sum(1 for h in self.readable(identity) if name not in h.read_by)

    def get(self, identity: Identity, handout_id: str) -> Handout:
        """One handout ``identity`` may read; raises :class:`HandoutError` otherwise."""
        handout = self.gamestate.handouts.find(handout_id)
        if handout is None or not self.may_read(identity, handout):
            raise HandoutError("journal.error.missing")
        return handout

    def opened(self, identity: Identity, handout: Handout) -> None:
        """A player opened it: it is read for them."""
        name = identity.user.username
        if identity.is_gamemaster or name in handout.read_by:
            return
        self.gamestate.handouts.mutate(handout.id, lambda h: h.read_by.append(name))

    def picture_file(self, handout: Handout, name: str) -> Path | None:
        """A picture copied for ``handout``, by its name there, or None."""
        folder = (self.root / handout.id).resolve()
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        path = folder / name
        if path.suffix.lower() not in media.IMAGE_TYPES or not path.is_file():
            return None
        return path

    def names(self, handout: Handout) -> list[str]:
        """The names of the characters it went to."""
        names = dict(self.recipients(handout.world_id))
        return [names.get(c, c) for c in handout.characters]

    def delete(self, handout_id: str) -> None:
        """Withdraw a handout: gone from every journal, its pictures with it."""
        self.gamestate.handouts.delete(handout_id)
        shutil.rmtree(self.root / handout_id, ignore_errors=True)


__all__ = ["HandoutError", "HandoutService"]
