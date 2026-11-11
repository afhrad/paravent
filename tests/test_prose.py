"""Worlds, adventures and chapters as Markdown files (documentation/ADVENTURES.md §3)."""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Adventure, Chapter, LevelRange, Role, SourceSet, World
from app.services import media
from app.services.media import MediaError
from app.store.errors import EntryNotFound, InvalidEntry
from app.store.prose import (
    FrontMatterError,
    ProseStore,
    classify,
    compose,
    split_front_matter,
)
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class Recorder:
    """Collects (kind, id, revision) notifications."""

    def __init__(self, store: ProseStore) -> None:
        self.events: list[tuple[str, str, str | None]] = []
        store.subscribe(lambda kind, rid, rev: self.events.append((kind, rid, rev)))


@pytest.fixture
def store(tmp_path: Path) -> ProseStore:
    prose = ProseStore(tmp_path / "worlds", trash=tmp_path / ".trash")
    prose.load()
    return prose


def world_adventure_chapter(store: ProseStore) -> tuple[World, Adventure, Chapter]:
    world = store.create(
        "world", World(name="Sunken Marches", source_set=SourceSet(sources=["PHB"]))
    )
    adventure = store.create(
        "adventure",
        Adventure(world_id=world.id, name="The Drowned Bell", level=LevelRange(start=1, end=5)),
    )
    chapter = store.create(
        "chapter",
        Chapter(
            world_id=world.id,
            adventure_id=adventure.id,
            title="Fog on the River",
            body="# Ambush\n",
        ),
    )
    assert isinstance(world, World)
    assert isinstance(adventure, Adventure)
    assert isinstance(chapter, Chapter)
    return world, adventure, chapter


class TestFrontMatter:
    def test_fields_and_body(self) -> None:
        fields, body = split_front_matter("---\nid: a1\nname: X\n---\n\n# Title\n\nText\n")
        assert fields == {"id": "a1", "name": "X"}
        assert body == "# Title\n\nText\n"

    def test_no_front_matter_is_all_body(self) -> None:
        assert split_front_matter("\n# Just text\n") == ({}, "# Just text\n")

    def test_windows_line_endings_and_bom(self) -> None:
        fields, body = split_front_matter("﻿---\r\nname: X\r\n---\r\nBody\r\n")
        assert fields == {"name": "X"} and body == "Body\n"

    def test_bad_yaml_says_which_line(self) -> None:
        with pytest.raises(FrontMatterError, match="line 3"):
            split_front_matter("---\nname: X\nbooks: [PHB\n---\n")

    def test_a_list_is_not_front_matter(self) -> None:
        with pytest.raises(FrontMatterError):
            split_front_matter("---\n- a\n- b\n---\n")

    def test_unclosed(self) -> None:
        with pytest.raises(FrontMatterError, match="closing"):
            split_front_matter("---\nname: X\n")

    def test_round_trip(self) -> None:
        text = compose({"id": "a1", "books": ["PHB", "MM"], "level": {"start": 1, "end": 5}}, "Hi")
        assert "books: [PHB, MM]" in text and text.startswith("---\nid: a1\n")
        assert split_front_matter(text) == (
            {"id": "a1", "books": ["PHB", "MM"], "level": {"start": 1, "end": 5}},
            "Hi\n",
        )

    def test_classify(self, tmp_path: Path) -> None:
        root = tmp_path
        assert classify(root, root / "w" / "world.md").kind == "world"  # type: ignore[union-attr]
        assert classify(root, root / "w/adventures/a/adventure.md").kind == "adventure"  # type: ignore[union-attr]
        assert classify(root, root / "w/adventures/a/chapters/c.md").kind == "chapter"  # type: ignore[union-attr]
        for other in ("w/notes.md", "w/media/x.md", "w/adventures/a/notes.md", ".x/world.md"):
            assert classify(root, root / other) is None


class TestWriting:
    def test_files_land_where_the_hierarchy_says(self, store: ProseStore) -> None:
        world, adventure, chapter = world_adventure_chapter(store)
        root = store.root
        assert store.path(world.id) == root / "sunken-marches" / "world.md"
        assert (
            store.path(adventure.id)
            == root / "sunken-marches/adventures/the-drowned-bell/adventure.md"
        )
        assert store.path(chapter.id) == (
            root / "sunken-marches/adventures/the-drowned-bell/chapters/fog-on-the-river.md"
        )
        text = (root / "sunken-marches" / "world.md").read_text()
        assert text.startswith("---\nid: ") and "books: [PHB]" in text

    def test_a_new_store_reads_it_all_back(self, store: ProseStore, tmp_path: Path) -> None:
        world, adventure, chapter = world_adventure_chapter(store)
        again = ProseStore(store.root)
        found = again.find("chapter", chapter.id)
        assert isinstance(found, Chapter)
        assert (found.title, found.body, found.adventure_id, found.world_id) == (
            "Fog on the River",
            "# Ambush",  # surrounding whitespace is not kept in memory
            adventure.id,
            world.id,
        )
        a = again.find("adventure", adventure.id)
        assert isinstance(a, Adventure) and a.level == LevelRange(start=1, end=5)
        w = again.find("world", world.id)
        assert isinstance(w, World) and w.source_set.sources == ["PHB"]

    def test_same_name_gets_its_own_folder(self, store: ProseStore) -> None:
        threads = [
            threading.Thread(target=lambda: store.create("world", World(name="Twin")))
            for _ in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        folders = {store.folder(w.id) for w in store.records("world")}
        assert len(folders) == 5

    def test_saving_keeps_the_body_as_written(self, store: ProseStore) -> None:
        _, _, chapter = world_adventure_chapter(store)
        path = store.path(chapter.id)
        assert path is not None
        fields, _ = split_front_matter(path.read_text())
        body = "Odd  *spacing*   kept\n\n\n<!-- a comment -->\n\n| a | b |\n|---|---|\n"
        write(path, compose(fields, body))
        store.reload_paths([path])
        store.save("chapter", store.find("chapter", chapter.id).model_copy(update={"order": 3}))  # type: ignore[union-attr]
        _, after = split_front_matter(path.read_text())
        assert after == body.rstrip("\n") + "\n"

    def test_a_chapter_needs_its_adventures_world(self, store: ProseStore) -> None:
        world, adventure, _ = world_adventure_chapter(store)
        other = store.create("world", World(name="Other"))
        with pytest.raises(InvalidEntry):
            store.create(
                "chapter", Chapter(world_id=other.id, adventure_id=adventure.id, title="X")
            )

    def test_unknown_parent(self, store: ProseStore) -> None:
        with pytest.raises(EntryNotFound):
            store.create("adventure", Adventure(world_id="nope", name="X"))

    def test_delete_moves_the_folder_to_the_trash(self, store: ProseStore) -> None:
        world, adventure, chapter = world_adventure_chapter(store)
        folder = store.folder(world.id)
        assert folder is not None
        write(folder / "media" / "cover.png", "png")
        events = Recorder(store)
        target = store.delete("world", world.id)
        assert not folder.exists()
        assert (target / "media" / "cover.png").exists()
        assert (target / "adventures/the-drowned-bell/chapters/fog-on-the-river.md").exists()
        assert store.records("adventure") == [] and store.records("chapter") == []
        gone = {rid for _, rid, rev in events.events if rev is None}
        assert gone == {world.id, adventure.id, chapter.id}

    def test_effective_books_include_the_published_adventure(self) -> None:
        world = World(name="W", source_set=SourceSet(sources=["PHB", "MM"]))
        based = Adventure(world_id=world.id, name="A", based_on="LMoP")
        assert based.effective_source_set(world).sources == ["PHB", "MM", "LMoP"]
        unrestricted = World(name="W")
        assert based.effective_source_set(unrestricted).is_unrestricted


class TestHandEdits:
    def test_a_dropped_in_world_gets_an_id_written_back(self, store: ProseStore) -> None:
        path = write(store.root / "kestria" / "world.md", "Kestria, after the war.\n")
        store.reload_paths([path])
        (world,) = store.records("world")
        assert isinstance(world, World)
        assert world.name == "Kestria" and world.description == "Kestria, after the war."
        fields, body = split_front_matter(path.read_text())
        assert fields["id"] == world.id and body == "Kestria, after the war.\n"

    def test_writing_back_does_not_notify_twice(self, store: ProseStore) -> None:
        path = write(store.root / "kestria" / "world.md", "Text\n")
        events = Recorder(store)
        store.reload_paths([path])
        store.reload_paths([path])  # the watcher seeing our own write-back
        assert len(events.events) == 1

    def test_a_copied_chapter_is_a_new_chapter(self, store: ProseStore) -> None:
        _, _, chapter = world_adventure_chapter(store)
        original = store.path(chapter.id)
        assert original is not None
        copy = write(original.with_name("copy.md"), original.read_text())
        store.reload_paths([copy])
        chapters = store.records("chapter")
        assert len(chapters) == 2 and chapter.id in {c.id for c in chapters}
        assert split_front_matter(copy.read_text())[0]["id"] != chapter.id

    def test_a_broken_edit_keeps_the_last_good_version(self, store: ProseStore) -> None:
        _, _, chapter = world_adventure_chapter(store)
        path = store.path(chapter.id)
        assert path is not None
        good = path.read_text()
        events = Recorder(store)
        write(path, good.replace("title: Fog on the River", "title: [unclosed"))
        store.reload_paths([path])
        assert store.find("chapter", chapter.id).title == "Fog on the River"  # type: ignore[union-attr]
        assert "YAML" in (store.error(chapter.id) or "")
        assert events.events == [("chapter", chapter.id, store.revision(chapter.id))]
        write(path, good.replace("title: Fog on the River", "title: Fixed"))
        store.reload_paths([path])
        assert store.find("chapter", chapter.id).title == "Fixed"  # type: ignore[union-attr]
        assert store.error(chapter.id) is None

    def test_an_unknown_key_is_reported(self, store: ProseStore) -> None:
        path = write(store.root / "w" / "world.md", "---\nname: W\nbookz: [PHB]\n---\n")
        store.reload_paths([path])
        assert store.records("world") == []
        assert "bookz" in store.errors["w/world.md"]

    def test_a_deleted_file_disappears(self, store: ProseStore) -> None:
        _, _, chapter = world_adventure_chapter(store)
        path = store.path(chapter.id)
        assert path is not None
        events = Recorder(store)
        path.unlink()
        store.reload_paths([path])
        assert store.find("chapter", chapter.id) is None
        assert events.events == [("chapter", chapter.id, None)]

    def test_a_moved_folder_keeps_its_ids(self, store: ProseStore) -> None:
        world, adventure, chapter = world_adventure_chapter(store)
        old = store.folder(world.id)
        assert old is not None
        new = old.with_name("aldmere")
        old_files = list(old.rglob("*.md"))
        os.rename(old, new)
        store.reload_paths([*old_files, *new.rglob("*.md")])
        assert store.folder(world.id) == new
        moved = store.find("chapter", chapter.id)
        assert isinstance(moved, Chapter) and moved.adventure_id == adventure.id

    def test_an_adventure_waits_for_its_world(self, store: ProseStore) -> None:
        adventure = write(store.root / "w/adventures/a/adventure.md", "---\nname: A\n---\n")
        store.reload_paths([adventure])
        assert store.records("adventure") == []
        assert "world.md" in store.errors["w/adventures/a/adventure.md"]
        world = write(store.root / "w" / "world.md", "---\nname: W\n---\n")
        store.reload_paths([world])
        (found,) = store.records("adventure")
        assert found.world_id == store.records("world")[0].id  # type: ignore[attr-defined]
        assert store.errors == {}

    def test_removing_world_md_takes_its_adventures(self, store: ProseStore) -> None:
        world, _, _ = world_adventure_chapter(store)
        path = store.path(world.id)
        assert path is not None
        path.unlink()
        store.reload_paths([path])
        assert store.records("adventure") == [] and store.records("chapter") == []

    def test_a_world_id_edited_by_hand_reaches_its_children(self, store: ProseStore) -> None:
        world, adventure, chapter = world_adventure_chapter(store)
        path = store.path(world.id)
        assert path is not None
        # The id line, however the writer quoted it: an all-digit id is written quoted.
        text = re.sub(r"(?m)^id: .*$", "id: renamed", path.read_text(), count=1)
        write(path, text)
        store.reload_paths([path])
        assert store.find("world", world.id) is None
        assert store.find("adventure", adventure.id).world_id == "renamed"  # type: ignore[union-attr]
        assert store.find("chapter", chapter.id).world_id == "renamed"  # type: ignore[union-attr]

    def test_an_all_digit_id_survives_a_hand_edit(self, store: ProseStore) -> None:
        # Written quoted (id: '447488834296') so it stays text; read back the same.
        world = store.create(
            "world", World(id="447488834296", name="Digits", source_set=SourceSet())
        )
        path = store.path(world.id)
        assert path is not None and "id: '447488834296'" in path.read_text()
        store.reload_paths([path])
        found = store.find("world", "447488834296")
        assert found is not None and found.name == "Digits"


class TestMigration:
    def test_json_worlds_and_adventures_become_files(self, tmp_path: Path) -> None:
        library = tmp_path / "library"
        library.mkdir()
        (library / "worlds.json").write_text(
            json.dumps(
                {
                    "w1": {
                        "id": "w1",
                        "name": "Sunken Marches",
                        "description": "North.",
                        "owner_id": "u1",
                        "language": "de",
                        "source_set": {"sources": ["PHB", "MM"]},
                        "created_at": "2026-10-01T00:00:00Z",
                        "updated_at": "2026-10-01T00:00:00Z",
                    }
                }
            )
        )
        (library / "adventures.json").write_text(
            json.dumps(
                {
                    "a1": {"id": "a1", "world_id": "w1", "name": "LMoP", "module_ids": []},
                    "a2": {"id": "a2", "world_id": "gone", "name": "Orphan"},
                }
            )
        )
        registry = StoreRegistry(
            Settings(reference_dir=tmp_path / "ref", library_dir=library, watch_files=False)
        )
        registry.load()
        world = registry.gamestate.worlds.get("w1")
        assert (world.name, world.description, world.language) == ("Sunken Marches", "North.", "de")
        assert world.source_set.sources == ["PHB", "MM"]
        assert registry.gamestate.adventures.get("a1").world_id == "w1"
        assert "a2" not in registry.gamestate.adventures
        assert not (library / "worlds.json").exists()
        assert (library / ".worlds.json.migrated").exists()
        assert (library / "worlds" / "sunken-marches" / "world.md").exists()
        # A second start finds nothing left to move.
        assert registry.gamestate.prose.adopt_json(library) == 0


class TestMedia:
    def test_upload_and_resolve(self, store: ProseStore) -> None:
        _, adventure, _ = world_adventure_chapter(store)
        first = media.store_upload(store, adventure.id, "Rotfang Map.PNG", PNG)
        second = media.store_upload(store, adventure.id, "rotfang map.png", PNG)
        assert (first, second) == ("media/rotfang-map.png", "media/rotfang-map-2.png")
        path = media.resolve(store, adventure.id, first)
        assert path is not None and path.read_bytes() == PNG

    @pytest.mark.parametrize(
        ("name", "data", "key"),
        [
            ("map.svg", b"<svg onload='x'/>", "media.error.type"),
            ("map.png", b"GIF89a....", "media.error.content"),
            ("map.png", PNG + b"\0" * media.MAX_BYTES, "media.error.size"),
        ],
    )
    def test_refused_uploads(self, store: ProseStore, name: str, data: bytes, key: str) -> None:
        world, _, _ = world_adventure_chapter(store)
        with pytest.raises(MediaError) as raised:
            media.store_upload(store, world.id, name, data)
        assert raised.value.key == key

    def test_nothing_outside_the_world_is_served(self, store: ProseStore, tmp_path: Path) -> None:
        world, adventure, _ = world_adventure_chapter(store)
        other = store.create("world", World(name="Other"))
        other_folder = store.folder(other.id)
        assert other_folder is not None
        write(other_folder / "media" / "secret.png", "x")
        write(tmp_path / "outside.png", "x")
        folder = store.folder(world.id)
        assert folder is not None
        (folder / "media").mkdir()
        (folder / "media" / "link.png").symlink_to(tmp_path / "outside.png")
        write(folder / "media" / "notes.txt", "x")
        for relative in (
            "../other/media/secret.png",
            "../../../other/media/secret.png",
            str(tmp_path / "outside.png"),
            "media/link.png",
            "media/notes.txt",
            "media\\..\\..\\x.png",
            "",
        ):
            assert media.resolve(store, adventure.id, relative) is None, relative
        # An adventure may use its world's media by going up to it.
        write(folder / "media" / "cover.png", "x")
        assert media.resolve(store, adventure.id, "../../media/cover.png") is not None

    def test_found_by_name_nearest_first(self, store: ProseStore) -> None:
        world, adventure, chapter = world_adventure_chapter(store)
        world_folder, chapter_folder = store.folder(world.id), store.folder(chapter.id)
        assert world_folder is not None and chapter_folder is not None
        write(world_folder / "maps" / "rotfang.png", "x")
        write(world_folder / "maps" / "town.png", "x")
        write(chapter_folder / "media" / "town.png", "x")
        # As written, then in media/, then by name: the chapter's own before the world's.
        assert media.find(store, chapter.id, world.id, "media/town.png") == (
            chapter.id,
            "media/town.png",
        )
        assert media.find(store, chapter.id, world.id, "town.png") == (chapter.id, "media/town.png")
        assert media.find(store, chapter.id, world.id, "rotfang.png") == (
            world.id,
            "maps/rotfang.png",
        )
        assert media.find(store, adventure.id, world.id, "nowhere.png") is None
        assert media.find(store, chapter.id, world.id, "../secret.png") is None


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    (tmp_path / "ref").mkdir()
    registry = StoreRegistry(
        Settings(reference_dir=tmp_path / "ref", library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry


class TestMediaRoute:
    async def test_served_to_the_gamemaster_only(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        relative = media.store_upload(registry.gamestate.prose, world.id, "cover.png", PNG)
        got = await ac.get(f"/media/{world.id}/{relative}")
        assert got.status_code == 200
        assert got.headers["content-type"] == "image/png" and got.content == PNG
        assert (await ac.get(f"/media/{world.id}/media/missing.png")).status_code == 404
        assert (await ac.get(f"/media/{world.id}/..%2F..%2Fusers.json")).status_code == 404
        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        assert (await ac.get(f"/media/{world.id}/{relative}")).status_code == 403
