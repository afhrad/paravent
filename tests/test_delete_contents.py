"""Deleting an adventure or a chapter with everything placed in it — or keeping it
(documentation/ADVENTURES.md §4): cleaning up generated content."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from app.models.gamestate import Scope
from tests.test_mcp import PASSWORD, call, session, table  # noqa: F401 - the fixture

Table = tuple[httpx.AsyncClient, Any]


def _fill(registry: Any, world_id: str) -> tuple[Any, Any]:
    """An adventure with a chapter, and an NPC, place, encounter and treasure in each."""
    adventure = registry.adventures.create(world_id, "Generated")
    chapter = registry.adventures.create_chapter(adventure.id, "One")
    for where in (f"a:{adventure.id}", f"c:{chapter.id}"):
        registry.npcs.create("Npc " + where, where, world_id)
        registry.locations.create("Place " + where, where, world_id)
        registry.treasure.create("Hoard " + where, where, world_id)
    registry.encounters.create(
        "Fight a", world_id, Scope(world_id=world_id, adventure_id=adventure.id)
    )
    registry.encounters.create(
        "Fight c",
        world_id,
        Scope(world_id=world_id, adventure_id=adventure.id, chapter_id=chapter.id),
    )
    return adventure, chapter


def _counts(registry: Any, world_id: str) -> tuple[int, int, int, int]:
    return (
        len(registry.npcs.list(world_id)),
        len(registry.locations.list(world_id)),
        len(registry.encounters.list(world_id)),
        len(registry.treasure.list(world_id)),
    )


class TestPages:
    async def test_the_choice_with_its_counts(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        adventure, chapter = _fill(registry, facts["world"].id)
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        page = (await raw.get(f"/gm/adventures/{adventure.id}/delete")).text
        assert 'name="contents" value="all"' in page and "2 NPCs" in page and "2 places" in page
        assert "2 encounters" in page and "2 treasures" in page
        assert "1 NPC" in (await raw.get(f"/gm/chapters/{chapter.id}/delete")).text


class TestDeleting:
    async def test_a_chapter_with_everything(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        before = _counts(registry, world_id)  # Hob, the fixture's NPC, stays
        adventure, chapter = _fill(registry, world_id)
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        await raw.post(f"/gm/chapters/{chapter.id}/delete", data={"contents": "all"})
        # The chapter's own went; the adventure's stayed.
        assert _counts(registry, world_id) == (before[0] + 1, 1, 1, 1)
        assert all(n.scope.chapter_id is None for n in registry.npcs.list(world_id))

    async def test_an_adventure_kept_or_with_everything(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        kept, _ = _fill(registry, world_id)
        await raw.post(f"/gm/adventures/{kept.id}/delete")  # the default keeps them
        assert _counts(registry, world_id) == (3, 2, 2, 2)
        assert all(n.scope.adventure_id is None for n in registry.npcs.list(world_id))

        gone, _ = _fill(registry, world_id)
        note = registry.session_notes.create(world_id, date(2026, 10, 28), gone.id, "played it")
        fight = registry.combats.start(
            next(e for e in registry.encounters.list(world_id) if e.adventure_id == gone.id),
            by="dm",
        )
        await raw.post(f"/gm/adventures/{gone.id}/delete", data={"contents": "all"})
        assert _counts(registry, world_id) == (3, 2, 2, 2)  # only the kept ones remain
        assert registry.session_notes.get(note.id).adventure_id is None
        assert registry.combats.get(fight.id) is not None  # history stays
        assert (await raw.get(f"/gm/worlds/{world_id}/sessions")).status_code == 200

    async def test_claude_deleting_its_draft_leaves_nothing(self, table: Table) -> None:  # noqa: F811
        _raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        before = _counts(registry, world_id)
        _lyra, brunhild = facts["ids"]
        async with session(table) as mcp:
            draft = await call(
                mcp,
                "create_adventure",
                world_id=world_id,
                name="Scrap",
                summary="",
                character_ids=[brunhild],
            )
            await call(mcp, "create_npc", adventure_id=draft["id"], name="Temp", notes="")
            await call(mcp, "create_place", adventure_id=draft["id"], name="Nowhere", notes="")
            await call(mcp, "delete", kind="adventure", id=draft["id"])
        assert _counts(registry, world_id) == before


class TestButtons:
    async def test_delete_is_in_reach(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        adventure, chapter = _fill(registry, world_id)
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        to_adventure = f'href="/gm/adventures/{adventure.id}/delete"'
        to_chapter = f'href="/gm/chapters/{chapter.id}/delete"'
        assert to_adventure in (await raw.get(f"/gm/worlds/{world_id}")).text
        on_adventure = (await raw.get(f"/gm/adventures/{adventure.id}")).text
        assert to_adventure in on_adventure and to_chapter in on_adventure
        assert to_chapter in (await raw.get(f"/gm/chapters/{chapter.id}")).text

    async def test_every_object_has_its_delete_in_reach(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        adventure, _chapter = _fill(registry, world_id)
        where = f"a:{adventure.id}"
        npc = next(n for n in registry.npcs.list(world_id) if n.name == f"Npc {where}")
        place = registry.locations.list(world_id)[0]
        hoard = next(x for x in registry.treasure.list(world_id) if x.name == f"Hoard {where}")
        fight = next(e for e in registry.encounters.list(world_id) if e.name == "Fight a")
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        targets = {
            "npc": (f"/gm/npcs/{npc.id}", f"/gm/worlds/{world_id}/npcs"),
            "place": (f"/gm/places/{place.id}", f"/gm/worlds/{world_id}/places"),
            "treasure": (f"/gm/treasure/{hoard.id}", f"/gm/worlds/{world_id}/treasure"),
            "encounter": (f"/gm/encounters/{fight.id}", f"/gm/encounters?world={world_id}"),
        }
        holdings = (await raw.get(f"/gm/adventures/{adventure.id}")).text
        for kind, (own_page, listing) in targets.items():
            link = f'href="{own_page}/delete"'
            assert link in (await raw.get(own_page)).text, kind  # beside Edit / Run
            assert link in (await raw.get(listing)).text, kind  # on its row
            assert link in holdings, kind  # in the adventure's panel
            assert (await raw.get(f"{own_page}/delete")).status_code == 200, kind

    async def test_never_in_an_edit_form(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        adventure, chapter = _fill(registry, world_id)
        npc = registry.npcs.list(world_id)[0]
        place = registry.locations.list(world_id)[0]
        note = registry.session_notes.create(world_id, date(2026, 10, 29), text="x")
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        forms = [
            f"/gm/worlds/{world_id}/edit",
            f"/gm/adventures/{adventure.id}/edit",
            f"/gm/chapters/{chapter.id}/edit",
            f"/gm/npcs/{npc.id}/edit",
            f"/gm/places/{place.id}/edit",
            f"/gm/sessions/{note.id}/edit",
        ]
        for url in forms:
            page = (await raw.get(url)).text
            assert "/delete" not in page[page.index("<main") :], url
        # The encounter's settings panel keeps none; its head has it.
        fight = registry.encounters.list(world_id)[0]
        builder = (await raw.get(f"/gm/encounters/{fight.id}")).text
        assert builder.count(f'href="/gm/encounters/{fight.id}/delete"') == 1
        # Worlds and session notes have theirs where they are read.
        assert (
            f'href="/gm/worlds/{world_id}/delete"' in (await raw.get(f"/gm/worlds/{world_id}")).text
        )
        assert (
            f'href="/gm/sessions/{note.id}/delete"'
            in (await raw.get(f"/gm/worlds/{world_id}/sessions")).text
        )
