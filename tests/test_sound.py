"""The sound desk (documentation/SOUND.md): the library, what the desk says should sound,
and the triggers from a chapter and a fight."""

from __future__ import annotations

import io
import json
import wave
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.models.gamestate import Role, Scope, Sound, SoundScene
from app.services.events import ChangeHub
from app.services.sound import SoundDesk, SoundError, SoundLibrary, display_name, sniff
from app.store.gamestate import GameStateStore
from tests.test_mcp import PASSWORD, table  # noqa: F401 - the fixture

Table = tuple[httpx.AsyncClient, Any]


def _wav() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 80)
    return out.getvalue()


@pytest.fixture
def library(tmp_path: Path) -> SoundLibrary:
    store = GameStateStore(tmp_path / "library")
    return SoundLibrary(store.sounds, store.sound_scenes, tmp_path / "library" / "sounds")


def _add(library: SoundLibrary, name: str, kind: Any = "effect") -> Sound:
    return library.add(f"{name}.wav", io.BytesIO(_wav()), kind, name=name)


class TestLibrary:
    def test_audio_by_content_not_by_name(self, library: SoundLibrary) -> None:
        assert sniff(_wav()[:16]) == ".wav" and sniff(b"ID3\x04") == ".mp3"
        assert sniff(b"\xff\xfb\x90\x00") == ".mp3" and sniff(b"OggS\0") == ".ogg"
        assert sniff(b"\0\0\0\x20ftypM4A ") == ".m4a" and sniff(b"<html>") is None
        with pytest.raises(SoundError):
            library.add("evil.mp3", io.BytesIO(b"<script>alert(1)</script>"))
        with pytest.raises(SoundError):
            library.add("sound.exe", io.BytesIO(_wav()))
        assert library.all() == []

    def test_names_and_a_second_file(self, library: SoundLibrary) -> None:
        assert display_name("creature_roar-01.wav") == "Creature Roar 01"
        first = library.add("../../rain.wav", io.BytesIO(_wav()))
        second = library.add("rain.wav", io.BytesIO(_wav()))
        assert (first.file, second.file) == ("rain.wav", "rain-2.wav")
        assert library.path(first) is not None and first.bytes > 0

    def test_a_hand_edited_file_stays_in_the_folder(self, library: SoundLibrary) -> None:
        sound = library.sounds.create(Sound(name="x", file="../sounds.json"))
        assert library.path(sound) is None

    def test_deleting_leaves_no_trace(self, library: SoundLibrary) -> None:
        rain = _add(library, "Rain", "ambience")
        path = library.path(rain)
        desk = SoundDesk(library, ChangeHub())
        desk.press(rain.id)
        scene = desk.save_scene("Storm")
        library.delete(rain.id)
        desk.forget(rain.id)
        assert path is not None and not path.exists()
        assert library.scenes.get(scene.id).layers == []
        assert desk.state()["loops"] == []


class TestDesk:
    def test_pads(self, library: SoundLibrary) -> None:
        desk = SoundDesk(library, ChangeHub())
        rain, fire = _add(library, "Rain", "ambience"), _add(library, "Fire", "ambience")
        one, two = _add(library, "One", "music"), _add(library, "Two", "music")
        sword = _add(library, "Sword")
        desk.press(rain.id)
        desk.press(fire.id)
        desk.press(one.id)
        desk.press(two.id)  # music is one track at a time
        desk.press(sword.id)
        state = desk.state()
        assert {x["name"] for x in state["loops"]} == {"Rain", "Fire"}
        assert state["music"]["name"] == "Two" and state["music"]["streamed"]
        assert [s["name"] for s in state["shots"]] == ["Sword"] and state["shot"] == 1
        desk.press(rain.id)  # off again
        assert [x["name"] for x in desk.state()["loops"]] == ["Fire"]
        json.dumps(state)  # the stream sends it as it is

    def test_a_scene_replaces_the_mix(self, library: SoundLibrary) -> None:
        desk = SoundDesk(library, ChangeHub())
        rain, fire = _add(library, "Rain", "ambience"), _add(library, "Fire", "ambience")
        with pytest.raises(SoundError):
            desk.save_scene("Empty")
        desk.press(rain.id)
        desk.volume(rain.id, 0.4)
        scene = desk.save_scene("Storm")
        desk.stop()
        desk.press(fire.id)
        desk.scene(scene.id)
        assert [(x["name"], x["volume"]) for x in desk.state()["loops"]] == [("Rain", 0.4)]
        assert desk.save_scene("storm").id == scene.id  # the same name replaces

    def test_a_trigger_never_turns_a_loop_off(self, library: SoundLibrary) -> None:
        desk = SoundDesk(library, ChangeHub())
        _add(library, "Rain", "ambience")
        assert desk.trigger("rain") and desk.trigger("Rain")
        assert len(desk.state()["loops"]) == 1
        assert not desk.trigger("Nothing")
        library.scenes.create(SoundScene(name="Rain"))  # a scene wins a shared name
        assert isinstance(library.named("Rain"), SoundScene)

    def test_old_shots_are_not_replayed(
        self, library: SoundLibrary, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        desk = SoundDesk(library, ChangeHub())
        desk.press(_add(library, "Sword").id)
        later = __import__("time").monotonic() + 60
        monkeypatch.setattr("app.services.sound.time.monotonic", lambda: later)
        assert desk.state()["shots"] == []


class TestRoutes:
    async def test_upload_play_and_who_may(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        answer = await raw.post(
            "/gm/sounds",
            data={"kind": "ambience", "credit": "CC0"},
            files=[
                ("files", ("rain.wav", _wav(), "audio/wav")),
                ("files", ("notes.txt", b"not audio", "text/plain")),
            ],
        )
        assert answer.status_code == 303 and "added=1" in answer.headers["location"]
        assert "skipped=1" in answer.headers["location"]
        rain = registry.sounds.all()[0]
        assert (rain.kind, rain.credit) == ("ambience", "CC0")
        shell = (await raw.get("/gm")).text
        # Apart from the log's button, which protocol.js finds by its class.
        assert 'class="btn btn-ghost shell-sound"' in shell
        assert shell.count("shell-log") == 1
        assert f'data-sound-id="{rain.id}"' in (await raw.get("/gm/sound/panel")).text
        assert "Rain" in (await raw.get("/gm/sounds")).text
        assert "data-speaker-start" in (await raw.get("/gm/sound")).text

        whole = await raw.get(f"/gm/sound/file/{rain.id}")
        assert whole.status_code == 200 and whole.headers["content-type"] == "audio/wav"
        part = await raw.get(f"/gm/sound/file/{rain.id}", headers={"Range": "bytes=0-9"})
        assert part.status_code == 206 and len(part.content) == 10

        assert (await raw.post(f"/gm/sound/press/{rain.id}")).status_code == 204
        assert registry.sound_desk.state()["loops"][0]["id"] == rain.id
        await raw.post("/gm/sound/volume", data={"id": rain.id, "value": "0.25"})
        assert registry.sound_desk.state()["loops"][0]["volume"] == 0.25
        assert (await raw.post("/gm/sound/scenes", data={"name": "Wet"})).status_code == 204
        await raw.post("/gm/sound/stop")
        assert (await raw.post("/gm/sound/scenes", data={"name": "X"})).status_code == 422

        await raw.post("/logout")
        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        await raw.post("/login", data={"username": "alice", "password": PASSWORD})
        assert (await raw.get("/gm/sound")).status_code == 403
        assert (await raw.post(f"/gm/sound/press/{rain.id}")).status_code == 403
        assert "shell-sound" not in (await raw.get("/play")).text

    async def test_a_chapter_and_a_fight_trigger_it(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        rain = registry.sounds.add("rain.wav", io.BytesIO(_wav()), "ambience")
        adventure = registry.adventures.create(world_id, "Sounds")
        chapter = registry.adventures.create_chapter(adventure.id, "One")
        registry.adventures.update_chapter(
            chapter.id,
            title="One",
            status=chapter.status,
            body="It pours. {@sound Rain} {@sound Gone}",
        )
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        page = (await raw.get(f"/gm/chapters/{chapter.id}")).text
        assert 'data-sound-trigger="Rain"' in page and 'data-sound-trigger="Gone"' not in page
        assert (await raw.post("/gm/sound/trigger", data={"name": "Rain"})).status_code == 204
        assert (await raw.post("/gm/sound/trigger", data={"name": "Gone"})).status_code == 404
        await raw.post("/gm/sound/stop")

        fight = registry.encounters.create("Ambush", world_id, Scope(world_id=world_id))
        builder = (await raw.get(f"/gm/encounters/{fight.id}")).text
        assert 'name="sound"' in builder and ">Rain</option>" in builder
        registry.gamestate.encounters.update(fight.id, sound="Rain")
        await raw.post(f"/gm/encounters/{fight.id}/run")
        assert registry.sound_desk.state()["loops"][0]["id"] == rain.id
        await raw.post("/gm/sound/stop")
        await raw.post(f"/gm/encounters/{fight.id}/run")  # back to the running fight
        assert registry.sound_desk.state()["loops"] == []
