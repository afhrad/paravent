"""The sound desk (documentation/SOUND.md): a library of sounds shared by every world, and
what is playing now.

A file is played by the **speaker**, a page left open on one device (``/gm/sound``); any
gamemaster's page only says what should sound. So the state lives here, in memory, and
every change sends all of it to the speakers and open panels — a speaker that reconnects
catches up by itself. Only an effect is an event rather than a state: it travels in
``shots``, numbered, and is dropped after a few seconds so a reconnecting speaker does not
fire off what it missed.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, BinaryIO

from app.models.gamestate import Sound, SoundKind, SoundLayer, SoundScene
from app.services.events import ChangeHub, revision_stream
from app.services.i18n import Translatable
from app.store.gamestate import Collection

# Watched by the open panels, and by the speakers: their number is "is a speaker there".
SOUND = "*sound"
SPEAKER = "*sound-speaker"

AUDIO_TYPES: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
}
# Larger files stream through an audio element instead of being decoded whole into memory.
STREAM_FROM_BYTES = 8 * 1024 * 1024
SHOT_SECONDS = 5.0
MAX_SHOTS = 8

_NAME = re.compile(r"[^a-z0-9]+")


class SoundError(Translatable):
    """A sound that cannot be stored."""


def sniff(head: bytes) -> str | None:
    """The audio type a file's first bytes declare, as its extension, or None."""
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ".wav"
    if head[:4] == b"OggS":
        return ".ogg"
    if head[4:8] == b"ftyp":
        return ".m4a"
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return ".mp3"
    return None


def display_name(filename: str) -> str:
    """``creature_roar-01.mp3`` as ``Creature Roar 01``."""
    words = re.sub(r"[_\-.]+", " ", Path(filename).stem).split()
    return " ".join(w[:1].upper() + w[1:] for w in words) or "Sound"


class SoundLibrary:
    """The sounds and scenes on disk: ``library/sounds/`` beside two collections."""

    def __init__(
        self, sounds: Collection[Sound], scenes: Collection[SoundScene], folder: Path
    ) -> None:
        """``folder`` holds the files; it is inside the library, so backups take it along."""
        self.sounds = sounds
        self.scenes = scenes
        self._folder = folder

    def all(self) -> list[Sound]:
        """Every sound by name."""
        return sorted(self.sounds.list(), key=lambda s: s.name.casefold())

    def all_scenes(self) -> list[SoundScene]:
        """Every scene by name."""
        return sorted(self.scenes.list(), key=lambda s: s.name.casefold())

    def names(self) -> list[str]:
        """What ``{@sound}`` can name: the scenes, then the sounds."""
        return [s.name for s in self.all_scenes()] + [s.name for s in self.all()]

    def path(self, sound: Sound) -> Path | None:
        """The sound's file, or None when it has gone; the record's name stays inside the
        folder whatever a hand edit wrote."""
        candidate = (self._folder / sound.file).resolve()
        if candidate.parent != self._folder.resolve() or not candidate.is_file():
            return None
        return candidate

    def add(
        self,
        filename: str,
        data: BinaryIO,
        kind: SoundKind = "effect",
        *,
        credit: str = "",
        name: str = "",
    ) -> Sound:
        """Store an upload, refusing anything that is not audio by content; a file of the
        same name is kept, the new one numbered."""
        plain = Path(filename.replace("\\", "/")).name
        suffix = Path(plain).suffix.lower()
        kind_of = sniff(data.read(16))
        data.seek(0)
        if suffix not in AUDIO_TYPES or kind_of is None or kind_of != suffix:
            raise SoundError("sound.error.type", name=plain)
        stem = _NAME.sub("-", Path(plain).stem.lower()).strip("-")[:64] or "sound"
        self._folder.mkdir(parents=True, exist_ok=True)
        target = self._folder / f"{stem}{suffix}"
        number = 2
        while target.exists():
            target = self._folder / f"{stem}-{number}{suffix}"
            number += 1
        with target.open("wb") as out:
            shutil.copyfileobj(data, out, 1024 * 1024)
        return self.sounds.create(
            Sound(
                name=name or display_name(plain),
                kind=kind,
                file=target.name,
                bytes=target.stat().st_size,
                credit=credit,
            )
        )

    def edit(
        self, sound_id: str, *, name: str, kind: SoundKind, volume: float, credit: str
    ) -> None:
        """Change what a sound is called, how it plays and how loud."""
        name = name.strip()
        if not name:
            raise SoundError("sound.error.name")
        self.sounds.update(
            sound_id,
            name=name,
            kind=kind,
            volume=max(0.0, min(1.0, volume)),
            credit=credit.strip(),
        )

    def delete(self, sound_id: str) -> None:
        """Remove a sound and its file, and take it out of every scene."""
        sound = self.sounds.get(sound_id)
        path = self.path(sound)
        self.sounds.delete(sound_id)
        if path is not None:
            path.unlink(missing_ok=True)
        for scene in self.scenes.list():
            kept = [layer for layer in scene.layers if layer.sound_id != sound_id]
            if len(kept) != len(scene.layers):
                self.scenes.update(scene.id, layers=kept)

    def named(self, name: str) -> Sound | SoundScene | None:
        """A scene or sound by name, as ``{@sound Tavern}`` writes it; a scene first, the
        thing one means by a name shared with one of its sounds."""
        wanted = name.strip().casefold()
        scene = next((s for s in self.all_scenes() if s.name.casefold() == wanted), None)
        return scene or next((s for s in self.all() if s.name.casefold() == wanted), None)


class SoundDesk:
    """What should be sounding now; kept in memory, gone with a restart like the music."""

    def __init__(self, library: SoundLibrary, hub: ChangeHub) -> None:
        """Bind to the library, and the hub that reaches speakers and panels."""
        self.library = library
        self._hub = hub
        self._loops: dict[str, float] = {}
        self._music: tuple[str, float] | None = None
        self.master = 1.0
        self._shots: list[dict[str, Any]] = []
        self._shot = 0

    # -- what the state looks like -------------------------------------------------

    def _item(self, sound: Sound, volume: float) -> dict[str, Any]:
        return {
            "id": sound.id,
            "name": sound.name,
            "url": f"/gm/sound/file/{sound.id}",
            "volume": volume,
            "streamed": sound.kind == "music" or sound.bytes >= STREAM_FROM_BYTES,
        }

    def _sound(self, sound_id: str) -> Sound | None:
        return self.library.sounds.find(sound_id)

    @property
    def speakers(self) -> int:
        """How many speakers are listening."""
        return self._hub.subscriber_count(SPEAKER)

    def state(self) -> dict[str, Any]:
        """All of it, as speakers and panels receive it."""
        now = time.monotonic()
        self._shots = [s for s in self._shots if now - s["at"] < SHOT_SECONDS]
        loops = []
        for sound_id, volume in self._loops.items():
            sound = self._sound(sound_id)
            if sound is not None:
                loops.append(self._item(sound, volume))
        music = None
        if self._music is not None:
            sound = self._sound(self._music[0])
            if sound is not None:
                music = self._item(sound, self._music[1])
        return {
            "master": self.master,
            "speakers": self.speakers,
            "loops": loops,
            "music": music,
            "shot": self._shot,
            "shots": [{k: v for k, v in s.items() if k != "at"} for s in self._shots],
        }

    def message(self) -> str:
        """The state on one line, for the event stream."""
        return json.dumps(self.state(), separators=(",", ":"))

    def publish(self) -> None:
        """Tell every speaker and panel."""
        text = self.message()
        self._hub.publish(SPEAKER, text)
        self._hub.publish(SOUND, text)

    # -- what the buttons do --------------------------------------------------------

    def press(self, sound_id: str) -> None:
        """A pad: an effect plays, a loop or the music goes on or off."""
        sound = self.library.sounds.get(sound_id)
        if sound.kind == "effect":
            self._shot += 1
            shot = {"n": self._shot, "at": time.monotonic(), **self._item(sound, sound.volume)}
            self._shots = [*self._shots, shot][-MAX_SHOTS:]
        elif sound.kind == "ambience":
            if self._loops.pop(sound.id, None) is None:
                self._loops[sound.id] = sound.volume
        elif self._music is not None and self._music[0] == sound.id:
            self._music = None
        else:
            self._music = (sound.id, sound.volume)
        self.publish()

    def volume(self, sound_id: str, volume: float) -> None:
        """One layer's volume, while it plays."""
        volume = max(0.0, min(1.0, volume))
        if sound_id in self._loops:
            self._loops[sound_id] = volume
        elif self._music is not None and self._music[0] == sound_id:
            self._music = (sound_id, volume)
        self.publish()

    def set_master(self, volume: float) -> None:
        """The volume of everything."""
        self.master = max(0.0, min(1.0, volume))
        self.publish()

    def stop(self) -> None:
        """Silence."""
        self._loops.clear()
        self._music = None
        self.publish()

    def scene(self, scene_id: str) -> None:
        """A scene replaces what is on; sounds it shares keep playing, the speaker fades
        the rest."""
        self._apply(self.library.scenes.get(scene_id))
        self.publish()

    def _apply(self, scene: SoundScene) -> None:
        self._loops.clear()
        self._music = None
        for layer in scene.layers:
            sound = self._sound(layer.sound_id)
            if sound is None:
                continue
            if sound.kind == "music":
                self._music = (sound.id, layer.volume)
            elif sound.kind == "ambience":
                self._loops[sound.id] = layer.volume

    def trigger(self, name: str) -> bool:
        """Play what ``name`` names; False when nothing has that name. Unlike a pad it
        never turns a loop off: a chapter's or fight's sound means "this now"."""
        found = self.library.named(name)
        if isinstance(found, SoundScene):
            self.scene(found.id)
        elif found is None:
            return False
        elif (found.kind == "ambience" and found.id in self._loops) or (
            self._music is not None and self._music[0] == found.id
        ):
            return True
        else:
            self.press(found.id)
        return True

    def save_scene(self, name: str) -> SoundScene:
        """What is on now, under a name; one of that name is replaced."""
        name = name.strip()
        if not name:
            raise SoundError("sound.error.name")
        layers = [SoundLayer(sound_id=k, volume=v) for k, v in self._loops.items()]
        if self._music is not None:
            layers.append(SoundLayer(sound_id=self._music[0], volume=self._music[1]))
        if not layers:
            raise SoundError("sound.error.nothing_on")
        for scene in self.library.scenes.list():
            if scene.name.casefold() == name.casefold():
                return self.library.scenes.update(scene.id, layers=layers)
        return self.library.scenes.create(SoundScene(name=name, layers=layers))

    def forget(self, sound_id: str) -> None:
        """A deleted sound stops."""
        self._loops.pop(sound_id, None)
        if self._music is not None and self._music[0] == sound_id:
            self._music = None
        self.publish()

    # -- the streams ----------------------------------------------------------------

    async def stream(
        self, *, speaker: bool, is_disconnected: Callable[[], Awaitable[bool]]
    ) -> AsyncIterator[str]:
        """The event stream of a speaker or panel; a speaker coming or going tells the
        panels, whose "no speaker" line depends on it."""
        key = SPEAKER if speaker else SOUND
        stream = revision_stream(self._hub, key, None, is_disconnected)
        try:
            first = True
            async for chunk in stream:
                yield chunk
                if first and chunk.startswith("retry"):
                    first = False
                    # Subscribed now, so the state counts this speaker.
                    self.publish()
        finally:
            await stream.aclose()
            if speaker:
                self.publish()


__all__ = [
    "AUDIO_TYPES",
    "SOUND",
    "SPEAKER",
    "SoundDesk",
    "SoundError",
    "SoundLibrary",
    "display_name",
    "sniff",
]
