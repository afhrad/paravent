"""What a review of the builders found (2026-11-25): designs travel with a world, an
open revision goes with its design, the library check reads the designs, an ability
raise needs attunement, and the table's homebrew is not called untranslated."""

from __future__ import annotations

from httpx import AsyncClient

from app.models.gamestate import ItemSpell
from app.services.item_builder import ItemError
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table

__all__ = ["table"]


@requires_corpus
async def test_the_builders_round_off(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id
    assert world is not None
    items, spells = registry.item_builder, registry.spell_builder
    ring = items.create("Ember Ring", world, "ring")
    spell = spells.create("Frost Lance", world)
    registry.background_builder.create("Ferryman", world)
    items.publish()
    # A duplicated world carries its designs; the table's own names stay its own.
    made = registry.world_transfer.duplicate(world, name="Second table")
    assert made.creatures_kept and {"Ember Ring", "Frost Lance", "Ferryman"} <= set(
        made.creatures_kept
    )
    assert items.named("Ember Ring") is not None
    # A design deleted with its revision open takes the copy along.
    copy = spells.start_revision(spell.id, "key")
    spells.delete(spell.id)
    assert registry.gamestate.spell_designs.find(copy.id) is None
    # An ability raise needs attunement, as the sheet applies it.
    try:
        items.save(items.get(ring.id).model_copy(update={"ability_raise": {"con": 2}}))
    except ItemError as exc:
        assert exc.key == "itembuilder.error.raise_attune"
    else:
        raise AssertionError("a raise without attunement")
    # The library check names what the books lack.
    items.save(
        items.get(ring.id).model_copy(update={"spells": [ItemSpell(spell="No Such Spell|PHB")]})
    )
    found: list[str] = []

    class Findings:
        def add(self, kind: object, *, subject: str, detail: str, href: str) -> None:
            found.append(f"{subject}: {detail}")

    from app.services.library_check import LibraryCheck

    check = LibraryCheck(registry)
    check._found = Findings()  # type: ignore[assignment]
    check._designs()
    assert "item design: Ember Ring: No Such Spell|PHB" in found
    # A homebrew item reads as the table's own on a German page.
    await ac.post("/language", data={"lang": "de"})
    page = (await ac.get(f"/ref/item/Homebrew/Ember Ring?world={world}")).text
    assert "Noch nicht übersetzt" not in page
