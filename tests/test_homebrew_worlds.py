"""Each world's builder designs under its own source (app/services/homebrew.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.models.gamestate import ItemDesign, SourceSet, world_homebrew
from app.services.worlds import WorldInput
from app.store.jsonfile import write_json
from app.store.registry import StoreRegistry


@pytest.fixture
def registry(tmp_path: Path) -> StoreRegistry:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    return registry


def _sees(registry: StoreRegistry, world_id: str, name: str, source: str | None) -> bool:
    world = registry.worlds.get(world_id)
    hit = registry.compendium.resolver().resolve_parts("item", name, source, world.source_set)
    return hit.entry is not None


class TestTheSourceSet:
    def test_a_worlds_own_homebrew_narrows_no_books(self) -> None:
        mine = SourceSet(sources=[world_homebrew("aaaaaaaaaaaa")])
        assert mine.is_unrestricted and not mine.books
        assert mine.allows("PHB") and mine.allows("Homebrew")  # a design of no world
        assert mine.allows("Homebrew-aaaaaaaaaaaa")
        assert not mine.allows("Homebrew-bbbbbbbbbbbb")
        assert not SourceSet().allows("Homebrew-bbbbbbbbbbbb")  # never another world's
        assert mine.identity != SourceSet().identity  # caches tell them apart

    def test_declared_books_keep_their_edition(self) -> None:
        books = SourceSet(sources=["XPHB", world_homebrew("aaaaaaaaaaaa"), "Homebrew"])
        assert books.books == ["XPHB", "Homebrew"] and books.is_single_edition


def test_each_world_reads_only_its_own_designs(registry: StoreRegistry) -> None:
    reed = registry.worlds.create(WorldInput(name="Reed", sources=("PHB",)))
    ash = registry.worlds.create(WorldInput(name="Ash"))  # every book
    builder = registry.item_builder
    builder.save(builder.create("Ember Ring", reed.id))
    builder.save(builder.create("Ash Crown", ash.id))
    builder.publish()
    assert registry.worlds.get(reed.id).source_set.sources == ["PHB", world_homebrew(reed.id)]
    assert registry.worlds.get(ash.id).source_set.is_unrestricted  # still every book
    for name, home, away in (("Ember Ring", reed, ash), ("Ash Crown", ash, reed)):
        for source in (None, "Homebrew"):  # by name, and as written before
            assert _sees(registry, home.id, name, source)
            assert not _sees(registry, away.id, name, source)


def test_a_file_from_before_moves_onto_the_worlds(registry: StoreRegistry) -> None:
    reed = registry.worlds.create(WorldInput(name="Reed", sources=("PHB", "Homebrew")))
    registry.gamestate.item_designs.create(ItemDesign(name="Ember Ring", world_id=reed.id))
    path = registry.item_builder.homebrew_file
    legacy = {
        "_meta": {"sources": [{"json": "Homebrew", "abbreviation": "Homebrew", "full": "x"}]},
        "item": [{"name": "Ember Ring", "source": "Homebrew", "type": "R", "rarity": "rare"}],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, legacy)
    assert registry.restamp_homebrew()
    data = json.loads(path.read_text())
    assert data["item"][0]["source"] == world_homebrew(reed.id) and data["_meta"]["perWorld"]
    assert world_homebrew(reed.id) in registry.worlds.get(reed.id).source_set.sources
    assert not registry.restamp_homebrew()  # once
    registry.reload_compendium()
    assert _sees(registry, reed.id, "Ember Ring", "Homebrew")


def test_a_moved_design_follows_to_its_new_world(registry: StoreRegistry) -> None:
    reed = registry.worlds.create(WorldInput(name="Reed"))
    ash = registry.worlds.create(WorldInput(name="Ash"))
    builder = registry.item_builder
    ring = builder.create("Ember Ring", reed.id)
    builder.save(ring)
    builder.publish()
    registry.world_copy.move("item_design", [ring.id], ash.id)
    assert registry.restamp_homebrew()
    registry.reload_compendium()
    assert _sees(registry, ash.id, "Ember Ring", world_homebrew(reed.id))  # its old address
    assert not _sees(registry, reed.id, "Ember Ring", None)
