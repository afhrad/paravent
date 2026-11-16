"""Spell scrolls and spellbooks (app/services/spellbook.py)."""

from __future__ import annotations

import html

from httpx import AsyncClient

from app.models.pc import Currency
from app.services.play import Actor
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table

__all__ = ["table"]

SCROLL = "Spell Scroll (3rd Level)|DMG"


@requires_corpus
async def test_scrolls_and_the_spellbook(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    by = Actor("DM", username="dm")
    registry.gamestate.characters.update(lyra, currency=Currency(gp=500))
    registry.characters.receive(lyra, [(SCROLL, 1)], {}, by, spell="Fireball|PHB")
    pc = registry.gamestate.characters.get(lyra)
    index = next(n for n, i in enumerate(pc.inventory) if i.spell == "Fireball|PHB")
    sheet = html.unescape((await ac.get(f"/characters/{lyra}?tab=gear")).text)
    assert "Copy into the spellbook" in sheet and "Fireball" in sheet
    cast = await ac.post(f"/characters/{lyra}/scroll", data={"op": "cast", "index": str(index)})
    assert "spellbook.cast_ok" in cast.headers["location"]  # level 3: no check for her
    pc = registry.gamestate.characters.get(lyra)
    assert not any(i.spell == "Fireball|PHB" for i in pc.inventory)
    assert "Casts Fireball from a scroll" in html.unescape((await ac.get("/log")).text)

    # A scroll found without its spell: written in, then copied with a check.
    registry.characters.receive(lyra, [(SCROLL, 1)], {}, by)
    pc = registry.gamestate.characters.get(lyra)
    blank = next(n for n, i in enumerate(pc.inventory) if i.item.name.startswith("Spell Scroll"))
    await ac.post(
        f"/characters/{lyra}/scroll",
        data={"op": "inscribe", "index": str(blank), "spell": "Lightning Bolt"},
    )
    pc = registry.gamestate.characters.get(lyra)
    held = next(n for n, i in enumerate(pc.inventory) if i.spell.startswith("Lightning Bolt"))
    copied = await ac.post(f"/characters/{lyra}/scroll", data={"op": "copy", "index": str(held)})
    location = copied.headers["location"]
    pc = registry.gamestate.characters.get(lyra)
    assert pc.currency.gp == 500 - 150  # 50 gp a level, paid either way
    assert not any(i.spell.startswith("Lightning Bolt") for i in pc.inventory)
    in_book = any(o.name == "Lightning Bolt" for o in pc.spells.known)
    assert ("spellbook.copied" in location) == in_book
    assert in_book or "spellbook.copy_failed" in location

    # From another book: the prepare page offers what she may copy.
    page = html.unescape((await ac.get(f"/characters/{lyra}/prepare")).text)
    assert "Copy a spell into the spellbook" in page and "Sleep (level 1, 50 gp, 2 h)" in page
    sleep = next(o for o in registry.catalogue(pc.world_id).rows("spell") if o.name == "Sleep")
    await ac.post(f"/characters/{lyra}/scroll", data={"op": "book", "spell": sleep.reference})
    pc = registry.gamestate.characters.get(lyra)
    assert any(o.name == "Sleep" for o in pc.spells.known)
    # Not a wizard spell: refused.
    refused = await ac.post(
        f"/characters/{lyra}/scroll", data={"op": "book", "spell": "Cure Wounds|PHB"}
    )
    assert "spellbook.error.cannot_copy" in refused.headers["location"]
