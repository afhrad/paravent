"""Revising an accepted adventure on a draft copy (documentation/MCP.md §9)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.models.gamestate import Combat, Encounter, Npc, Scope, Treasure
from app.services.revisions import RevisionError
from tests.test_mcp import PASSWORD, call, session, table  # noqa: F401 - the fixture

Table = tuple[httpx.AsyncClient, Any]


def _accepted(registry: Any, world_id: str) -> Any:
    """An adventure Claude wrote and the gamemaster accepted, with one of everything."""
    store = registry.gamestate
    adventure = registry.adventures.create(world_id, "Der Damm")
    store.adventures.update(adventure.id, status="planned", drafted_by="key", summary="Alt")
    chapter = registry.adventures.create_chapter(adventure.id, "Anfang")
    store.chapters.update(chapter.id, body="Hier trifft man {@npc Korak}.\n", status="running")
    here = Scope(world_id=world_id, adventure_id=adventure.id)
    npc = store.npcs.create(Npc(scope=here, name="Korak", notes="Alt", portrait="media/k.webp"))
    gone = store.npcs.create(Npc(scope=here, name="Fenn"))
    fight = store.encounters.create(
        Encounter(name="Kampf", world_id=world_id, adventure_id=adventure.id)
    )
    return adventure, chapter, npc, gone, fight


def _copied(copy: Any, registry: Any, original_id: str) -> str:
    return next(k for k, v in copy.origins.items() if v == original_id)


class TestStarting:
    async def test_a_copy_of_everything(self, table: Table) -> None:  # noqa: F811
        _, facts = table
        registry = facts["app"].state.stores
        adventure, chapter, npc, _, fight = _accepted(registry, facts["world"].id)
        copy = registry.revisions.start(adventure.id, "key")
        assert (copy.status, copy.drafted_by, copy.revision_of) == ("draft", "key", adventure.id)
        assert copy.summary == "Alt"
        chapters = registry.adventures.chapters(copy.id)
        assert [c.body.strip() for c in chapters] == ["Hier trifft man {@npc Korak}."]
        assert chapters[0].id != chapter.id and chapters[0].status == "running"
        twin = registry.gamestate.npcs.get(_copied(copy, registry, npc.id))
        assert twin.scope.adventure_id == copy.id and twin.portrait is None
        assert (
            registry.gamestate.encounters.get(_copied(copy, registry, fight.id)).adventure_id
            == copy.id
        )
        assert registry.revisions.changes(copy) == []

    async def test_one_at_a_time_and_only_claudes(self, table: Table) -> None:  # noqa: F811
        _, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        adventure, *_ = _accepted(registry, world_id)
        copy = registry.revisions.start(adventure.id, "key")
        assert registry.revisions.start(adventure.id, "key").id == copy.id
        with pytest.raises(RevisionError):
            registry.revisions.start(copy.id, "key")  # a draft
        by_hand = registry.adventures.create(world_id, "Von Hand")
        registry.gamestate.adventures.update(by_hand.id, status="planned")
        with pytest.raises(RevisionError) as caught:
            registry.revisions.start(by_hand.id, "key")
        assert caught.value.key == "revision.error.not_claudes"

    async def test_survives_a_reload_from_its_file(self, table: Table) -> None:  # noqa: F811
        _, facts = table
        registry = facts["app"].state.stores
        adventure, *_ = _accepted(registry, facts["world"].id)
        copy = registry.revisions.start(adventure.id, "key")
        registry.gamestate.prose.load()
        again = registry.gamestate.adventures.get(copy.id)
        assert again.revision_of == adventure.id and again.origins == copy.origins


class TestRenames:
    async def test_stay_in_the_copy_until_accepted(self, table: Table) -> None:  # noqa: F811
        _, facts = table
        registry = facts["app"].state.stores
        adventure, chapter, npc, *_ = _accepted(registry, facts["world"].id)
        copy = registry.revisions.start(adventure.id, "key")
        twin = registry.gamestate.npcs.get(_copied(copy, registry, npc.id))
        registry.npcs.update(
            twin.id,
            name="Gargran",
            scope_value=f"a:{copy.id}",
            roles=[],
            statblock="",
            notes="Neu",
        )
        assert "{@npc Korak}" in registry.adventures.get_chapter(chapter.id).body
        written = registry.adventures.chapters(copy.id)[0].body
        assert "{@npc Gargran}" in written
        registry.revisions.accept(copy.id)
        assert "{@npc Gargran}" in registry.adventures.get_chapter(chapter.id).body


class TestAccepting:
    async def test_replaces_and_keeps_what_the_table_owns(self, table: Table) -> None:  # noqa: F811
        _, facts = table
        registry = facts["app"].state.stores
        store = registry.gamestate
        world_id = facts["world"].id
        adventure, chapter, npc, gone, fight = _accepted(registry, world_id)
        copy = registry.revisions.start(adventure.id, "key")
        store.npcs.update(_copied(copy, registry, npc.id), notes="Neu")
        store.npcs.delete(_copied(copy, registry, gone.id))
        store.adventures.update(copy.id, summary="Neu")
        twin_chapter = registry.adventures.chapters(copy.id)[0]
        store.chapters.update(twin_chapter.id, body="Anders.\n", status="planned")
        extra = registry.adventures.create_chapter(copy.id, "Ende")
        place = Scope(world_id=world_id, adventure_id=copy.id, chapter_id=extra.id)
        store.treasures.create(Treasure(scope=place, name="Beute"))
        fresh = store.adventures.get(copy.id)
        actions = {(c.kind, c.action) for c in registry.revisions.changes(fresh)}
        assert actions == {
            ("adventure", "changed"),
            ("chapter", "changed"),
            ("chapter", "new"),
            ("npc", "changed"),
            ("npc", "removed"),
            ("treasure", "new"),
        }
        registry.revisions.accept(copy.id)
        kept = store.npcs.get(npc.id)
        assert kept.notes == "Neu" and kept.portrait == "media/k.webp"
        assert store.npcs.find(gone.id) is None
        assert store.encounters.get(fight.id).name == "Kampf"
        first = registry.adventures.get_chapter(chapter.id)
        assert first.body.strip() == "Anders." and first.status == "running"
        assert registry.adventures.get(adventure.id).summary == "Neu"
        titles = [c.title for c in registry.adventures.chapters(adventure.id)]
        assert titles == ["Anfang", "Ende"]
        loot = next(t for t in store.treasures.list() if t.name == "Beute")
        assert loot.scope.adventure_id == adventure.id
        assert loot.scope.chapter_id == registry.adventures.chapters(adventure.id)[1].id
        assert store.adventures.find(copy.id) is None
        assert store.npcs.find(_copied(copy, registry, npc.id)) is None

    async def test_the_gamemasters_edit_wins(self, table: Table) -> None:  # noqa: F811
        _, facts = table
        registry = facts["app"].state.stores
        store = registry.gamestate
        adventure, _, npc, *_ = _accepted(registry, facts["world"].id)
        copy = registry.revisions.start(adventure.id, "key")
        store.npcs.update(_copied(copy, registry, npc.id), notes="Von Claude")
        store.npcs.update(npc.id, notes="Von der Spielleitung")
        changes = registry.revisions.changes(copy)
        assert [(c.title, c.conflict) for c in changes] == [("Korak", True)]
        registry.revisions.accept(copy.id)
        assert store.npcs.get(npc.id).notes == "Von der Spielleitung"

    async def test_a_fought_fight_stays(self, table: Table) -> None:  # noqa: F811
        _, facts = table
        registry = facts["app"].state.stores
        store = registry.gamestate
        adventure, _, _, _, fight = _accepted(registry, facts["world"].id)
        store.combats.create(Combat(name="Lief", world_id=facts["world"].id, encounter_id=fight.id))
        copy = registry.revisions.start(adventure.id, "key")
        store.encounters.delete(_copied(copy, registry, fight.id))
        assert registry.revisions.changes(copy)[0].held
        registry.revisions.accept(copy.id)
        assert store.encounters.find(fight.id) is not None


class TestPages:
    async def test_review_accept_and_discard(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        store = registry.gamestate
        adventure, _, npc, *_ = _accepted(registry, facts["world"].id)
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        copy = registry.revisions.start(adventure.id, "key")
        store.npcs.update(_copied(copy, registry, npc.id), notes="Neu")
        assert "waiting" in (await raw.get(f"/gm/adventures/{adventure.id}")).text
        page = (await raw.get(f"/gm/adventures/{copy.id}")).text
        assert "What changes" in page and "Korak" in page
        answer = await raw.post(f"/gm/adventures/{copy.id}/delete")
        assert answer.headers["location"] == f"/gm/adventures/{adventure.id}"
        assert store.npcs.get(npc.id).notes == "Alt"
        assert store.adventures.find(copy.id) is None
        assert len(store.npcs.list(lambda n: n.name == "Korak")) == 1
        copy = registry.revisions.start(adventure.id, "key")
        store.npcs.update(_copied(copy, registry, npc.id), notes="Neu")
        done = await raw.post(f"/gm/adventures/{copy.id}/accept", follow_redirects=True)
        assert "Revision accepted: 1 changes" in done.text
        assert store.npcs.get(npc.id).notes == "Neu"


class TestTools:
    async def test_claude_revises_through_the_tools(self, table: Table) -> None:  # noqa: F811
        _, facts = table
        registry = facts["app"].state.stores
        adventure, *_ = _accepted(registry, facts["world"].id)
        async with session(table) as mcp:
            copy = await call(mcp, "start_revision", adventure_id=adventure.id)
            assert copy["writable"] and copy["revision_of"] == adventure.id
            chapter = copy["chapters"][0]
            written = await call(
                mcp,
                "write_chapter",
                adventure_id=copy["id"],
                title=chapter["title"],
                body="Neu geschrieben.\n",
                chapter_id=chapter["id"],
                base=chapter["version"],
            )
            assert written["id"] == chapter["id"]
            seen = await call(mcp, "revision_changes", adventure_id=copy["id"])
        assert [(c["kind"], c["action"]) for c in seen] == [("chapter", "changed")]
