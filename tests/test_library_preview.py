"""Previews of the campaign's own links (documentation/ADVENTURES.md §5.4): whatever a link
leads to answers at ``/library/preview``, with the way to its page."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app.models.gamestate import Coins, Role, Scope
from app.ui.library_preview import excerpt
from tests.test_mcp import PASSWORD, table  # noqa: F401 - the fixture

Table = tuple[httpx.AsyncClient, Any]


async def _card(raw: httpx.AsyncClient, href: str) -> httpx.Response:
    return await raw.get(f"/library/preview?href={quote(href, safe='')}")


def test_an_excerpt_is_plain_text() -> None:
    text = (
        "> [!read]\n> Rain falls.\n\n{@npc Hob}\n\n## Head\n\n"
        "**Bold** {@spell Fireball||Feuerball}"
    )
    assert excerpt(text) == "Rain falls. Head Bold Feuerball"
    assert excerpt("word " * 200).endswith(" …")


class TestCards:
    async def test_every_kind_of_link(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        adventure = registry.adventures.create(world_id, "Quest")
        chapter = registry.adventures.create_chapter(adventure.id, "One")
        registry.adventures.update_chapter(
            chapter.id, title="One", status=chapter.status, body="It rains on the road."
        )
        where = f"a:{adventure.id}"
        npc = registry.npcs.create("Herald", where, world_id)
        registry.npcs.update(
            npc.id,
            name="Herald",
            scope_value=where,
            roles=["ally"],
            statblock="",
            notes="Knows it.",
        )
        place = registry.locations.create("Keep", where, world_id)
        hoard = registry.treasure.create("Chest", where, world_id)
        registry.treasure.update(
            hoard.id, name="Chest", scope_value=where, coins=Coins(gp=7), notes=""
        )
        fight = registry.encounters.create(
            "Ambush", world_id, Scope(world_id=world_id, adventure_id=adventure.id)
        )
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        for href, seen in (
            (f"/gm/npcs/{npc.id}", "Knows it."),
            (f"/gm/places/{place.id}", "Keep"),
            (f"/gm/treasure/{hoard.id}", "7"),
            (f"/gm/encounters/{fight.id}", "Ambush"),
            (f"/gm/chapters/{chapter.id}#road", "It rains on the road."),
            (f"/characters/{facts['ids'][1]}", "Brunhild"),
        ):
            answer = await _card(raw, href)
            assert answer.status_code == 200, href
            assert (
                seen in answer.text
                and f'href="{href}"' in answer.text
                and "tip-open" in answer.text
            )

    async def test_only_campaign_addresses_and_who_may(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        npc = registry.npcs.create("Secret", f"w:{facts['world'].id}", facts["world"].id)
        await raw.post("/login", data={"username": "dm", "password": PASSWORD})
        assert (await _card(raw, "/gm/players")).status_code == 404
        assert (await _card(raw, "/gm/npcs/nobody")).status_code == 404
        assert (await _card(raw, "https://evil.example/gm/npcs/x")).status_code == 404
        await raw.post("/logout")
        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        await raw.post("/login", data={"username": "alice", "password": PASSWORD})
        assert (await _card(raw, f"/gm/npcs/{npc.id}")).status_code == 403
        assert (await _card(raw, f"/characters/{facts['ids'][1]}")).status_code == 404
