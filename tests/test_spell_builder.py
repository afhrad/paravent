"""The spell builder (app/services/spell_builder.py): spells of the table's own,
published as homebrew, on class lists and cast from the sheet like the books' own."""

from __future__ import annotations

from httpx import AsyncClient

from app.models.gamestate import SpellDesign
from app.models.pc import ChosenOption
from app.services.spell_builder import cantrip_scaling, spell_of
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table

__all__ = ["table"]


def words(key: str, **params: object) -> str:
    return f"{key}{params}"


def test_the_spell_it_publishes_as() -> None:
    burst = SpellDesign(
        name="Ember Burst",
        level=3,
        school="V",
        range_amount=120,
        area="sphere",
        area_size=20,
        material="a pinch of ash",
        duration_kind="instant",
        effect="save",
        save="dex",
        dice="6d6",
        damage_types=["fire"],
        upcast="1d6",
        text="Embers settle afterwards.",
        classes=["Wizard|PHB"],
    )
    spell = spell_of(burst, words)
    assert spell["savingThrow"] == ["dexterity"] and spell["damageInflict"] == ["fire"]
    assert spell["range"] == {"type": "point", "distance": {"type": "feet", "amount": 120}}
    assert spell["components"] == {"v": True, "s": True, "m": "a pinch of ash"}
    assert (
        "{@damage 6d6}" in spell["entries"][0]
        and spell["entries"][1] == "Embers settle afterwards."
    )
    assert "{@scaledamage 6d6|3-9|1d6}" in str(spell["entriesHigherLevel"])
    assert cantrip_scaling("1d10") == {"1": "1d10", "5": "2d10", "11": "3d10", "17": "4d10"}
    mend = SpellDesign(
        name="Mend", level=1, effect="heal", dice="2d8", heal_targets=3, upcast="1d8"
    )
    healing = spell_of(mend, words)
    assert healing["miscTags"] == ["HL"]
    assert healing["healing"] == {"dice": "2d8", "mod": True, "targets": 3, "upcast": "1d8"}
    assert "{@scaledice 2d8|1-9|1d8}" in str(healing["entriesHigherLevel"])


@requires_corpus
async def test_published_on_the_list_and_cast(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id
    builder = registry.spell_builder
    # From a printed spell: its numbers, text and class lists come along.
    copy = builder.create("Bright Burst", world, based_on="Fireball|PHB")
    assert (copy.level, copy.effect, copy.save, copy.dice, copy.upcast) == (
        3,
        "save",
        "dex",
        "8d6",
        "1d6",
    )
    assert "Wizard|PHB" in copy.classes and not copy.write_effect
    bolt = builder.create("Frost Lance", world)
    bolt.level, bolt.effect, bolt.attack, bolt.dice = 0, "attack", "R", "1d10"
    bolt.damage_types, bolt.classes = ["cold"], ["Wizard|PHB"]
    builder.save(bolt)
    assert builder.publish().count == 2
    assert registry.compendium.get("spell", "Frost Lance|Homebrew") is not None
    lists = registry.lookups.spell_sources.classes_for("Frost Lance", "Homebrew")
    assert lists == {"PHB": {"Wizard"}}
    # On the sheet: a cantrip's damage by the caster's level, cast with a roll.
    pc = registry.gamestate.characters.get(lyra)
    spells = pc.spells.model_copy(
        update={
            "cantrips": [*pc.spells.cantrips, ChosenOption(name="Frost Lance", source="Homebrew")]
        }
    )
    registry.gamestate.characters.update(lyra, spells=spells)
    sheet = registry.characters.sheet(registry.gamestate.characters.get(lyra))
    view = next(s for s in sheet.spells if s.name == "Frost Lance")
    assert view.attack and view.damage == "2d10"  # level 10
    done = await ac.post(f"/characters/{lyra}/act/cast", data={"spell": "Frost Lance|Homebrew"})
    assert done.status_code in (200, 303)


@requires_corpus
async def test_the_builder_pages(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id
    assert world is not None
    registry.table.select(world)
    assert "Spell builder" in (await ac.get("/gm/spells")).text
    made = await ac.post("/gm/spells", data={"name": "Mire Bloom"})
    design_id = made.headers["location"].rsplit("/", 1)[1]
    page = (await ac.get(f"/gm/spells/{design_id}")).text
    assert "On the spell lists of" in page and "Wizard (PHB)" in page
    done = await ac.post(
        f"/gm/spells/{design_id}",
        data={
            "action": "publish",
            "name": "Mire Bloom",
            "world": world,
            "level": "2",
            "school": "C",
            "time_number": "1",
            "time_unit": "action",
            "range_kind": "feet",
            "range_amount": "90",
            "area": "cube",
            "area_size": "10",
            "verbal": "1",
            "somatic": "1",
            "material": "",
            "duration_kind": "instant",
            "effect": "save",
            "save": "con",
            "half_on_save": "1",
            "dice": "3d8",
            "damage_types": "poison",
            "upcast": "1d8",
            "write_effect": "1",
            "classes": ["Wizard|PHB", "Druid|PHB"],
            "text": "Pale flowers burst from the mud.",
        },
    )
    assert "published=1" in done.headers["location"]
    design = registry.spell_builder.get(design_id)
    assert (design.level, design.save, design.dice, design.classes) == (
        2,
        "con",
        "3d8",
        ["Wizard|PHB", "Druid|PHB"],
    )
    shown = (await ac.get(f"/ref/spell/Homebrew/Mire Bloom?world={world}")).text
    assert "Pale flowers burst from the mud" in shown
    bad = await ac.post(
        f"/gm/spells/{design_id}", data={"action": "save", "name": "Mire Bloom", "dice": "lots"}
    )
    assert bad.status_code == 422 and "is not dice" in bad.text
    assert "Make my own from this" in (await ac.get(f"/ref/spell/PHB/Fireball?world={world}")).text
    assert (await ac.get("/gm/spells/nope")).status_code == 404
    await ac.post(f"/gm/spells/{design_id}/delete")
    assert registry.spell_builder.named("Mire Bloom") is None
