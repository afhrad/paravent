"""The background builder (app/services/background_builder.py): backgrounds of the
table's own, published as homebrew, offered by the character wizard."""

from __future__ import annotations

from httpx import AsyncClient

from app.models.gamestate import BackgroundDesign, BackgroundItem
from app.services.background_builder import background_of, from_background
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table

__all__ = ["table"]


def words(key: str, **params: object) -> str:
    return f"{key}{params}"


def test_a_printed_skill_choice_is_read() -> None:
    fields = from_background(
        {
            "name": "Cloistered Scholar",
            "source": "SCAG",
            "skillProficiencies": [
                {"history": True, "choose": {"from": ["arcana", "nature", "religion"]}}
            ],
        }
    )
    assert fields["skills"] == ["history"]
    assert (fields["skill_choices"], fields["choose_skills"]) == (
        ["arcana", "nature", "religion"],
        1,
    )


def test_the_background_it_publishes_as() -> None:
    ferry = BackgroundDesign(
        name="Ferryman",
        edition="one",
        abilities=["str", "con", "wis"],
        feat="Tough|XPHB",
        skills=["athletics", "survival"],
        tools=["vehicles (water)"],
        any_languages=1,
        equipment=[
            BackgroundItem(item="Pole (10 feet)|XPHB"),
            BackgroundItem(special="ferry token"),
        ],
        gold=12,
    )
    bg = background_of(ferry, words)
    assert bg["edition"] == "one" and bg["feats"] == [{"tough|xphb": True}]
    assert bg["ability"][0]["choose"]["weighted"] == {
        "from": ["str", "con", "wis"],
        "weights": [2, 1],
    }
    assert bg["skillProficiencies"] == [{"athletics": True, "survival": True}]
    assert bg["languageProficiencies"] == [{"anyStandard": 1}]
    assert bg["startingEquipment"] == [
        {
            "A": [{"item": "pole (10 feet)|xphb"}, {"special": "ferry token"}, {"value": 1200}],
            "B": [{"value": 5000}],
        }
    ]
    choosing = background_of(
        ferry.model_copy(update={"skill_choices": ["arcana", "history"], "choose_skills": 1}),
        words,
    )
    assert choosing["skillProficiencies"] == [
        {"athletics": True, "survival": True, "choose": {"from": ["arcana", "history"], "count": 1}}
    ]
    old = background_of(ferry.model_copy(update={"edition": "classic"}), words)
    assert "edition" not in old and "feats" not in old
    assert old["startingEquipment"] == [
        {
            "_": [
                {"item": "pole (10 feet)|xphb"},
                {"special": "ferry token"},
                {"item": "pouch|phb", "containsValue": 1200},
            ]
        }
    ]


@requires_corpus
async def test_published_and_offered_by_the_wizard(
    table: tuple[AsyncClient, StoreRegistry, str],
) -> None:
    _ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id
    builder = registry.background_builder
    copy = builder.create("Wandering Acolyte", world, based_on="Acolyte|PHB")
    assert copy.edition == "classic" and copy.skills == ["insight", "religion"]
    assert copy.any_languages == 2 and copy.gold == 15
    assert copy.feature_name == "Shelter of the Faithful" and copy.feature_text
    sage = builder.create("", world, based_on="Sage|XPHB")
    assert sage.skills and sage.edition == "one" and len(sage.abilities) == 3
    fresh = builder.create("Ferryman", world)
    fresh.edition, fresh.skills, fresh.abilities = "one", ["athletics"], ["str", "con", "wis"]
    fresh.feat = "Tough|XPHB"
    builder.save(fresh)
    assert builder.publish().count == 3  # with the Sage
    assert registry.compendium.get("background", "Ferryman|Homebrew") is not None
    offered = {o.name for o in registry.catalogue(world).backgrounds()}
    assert {"Ferryman", "Wandering Acolyte"} <= offered


@requires_corpus
async def test_the_builder_pages(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, lyra = table
    world = registry.gamestate.characters.get(lyra).world_id
    assert world is not None
    registry.table.select(world)
    assert "Background builder" in (await ac.get("/gm/backgrounds")).text
    made = await ac.post("/gm/backgrounds", data={"name": "Bell Ringer"})
    design_id = made.headers["location"].rsplit("/", 1)[1]
    page = (await ac.get(f"/gm/backgrounds/{design_id}")).text
    assert "Origin feat" in page and "Sleight of Hand" in page
    done = await ac.post(
        f"/gm/backgrounds/{design_id}",
        data={
            "action": "publish",
            "name": "Bell Ringer",
            "world": world,
            "edition": "one",
            "abilities": ["str", "wis", "cha"],
            "feat": "Alert|XPHB",
            "skills": ["perception", "religion"],
            "tools": "",
            "languages": "",
            "any_languages": "1",
            "eq_item": ["Bell|XPHB", ""],
            "eq_special": ["", "a frayed rope"],
            "eq_quantity": ["1", "1"],
            "gold": "10",
            "text": "You rang the hours over the river.",
        },
    )
    assert "published=1" in done.headers["location"]
    design = registry.background_builder.get(design_id)
    assert design.abilities == ["str", "wis", "cha"] and design.feat == "Alert|XPHB"
    assert [(i.item, i.special) for i in design.equipment] == [
        ("Bell|XPHB", ""),
        ("", "a frayed rope"),
    ]
    shown = (await ac.get(f"/ref/background/Homebrew/Bell Ringer?world={world}")).text
    assert "You rang the hours over the river" in shown and ">Background<" in shown
    bad = await ac.post(
        f"/gm/backgrounds/{design_id}",
        data={
            "action": "save",
            "name": "Bell Ringer",
            "edition": "one",
            "abilities": ["str"],
            "feat": "",
        },
    )
    assert bad.status_code == 422 and "three abilities" in bad.text
    assert (await ac.get("/gm/backgrounds/nope")).status_code == 404
    await ac.post(f"/gm/backgrounds/{design_id}/delete")
    assert registry.background_builder.named("Bell Ringer") is None
