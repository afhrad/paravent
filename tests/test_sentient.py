"""Sentient items and artifacts (app/services/sentient.py): a mind rolled on the world's
guide, an artifact's further properties, and the conflict with the bearer."""

from __future__ import annotations

from collections.abc import Sequence

from httpx import AsyncClient

from app.models.pc import ChosenOption, InventoryItem, ItemMind
from app.services.book_tables import Rows
from app.services.sentient import (
    conflict_dc,
    guide_for,
    reroll,
    roll_mind,
    roll_part,
    room,
    score,
)
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table  # noqa: F401


class Fixed:
    """Dice that always roll the same number, capped by the die."""

    def __init__(self, value: int) -> None:
        self.value = value

    def die(self, faces: int) -> int:
        return min(self.value, faces)


def _find(caption: str, sources: Sequence[str]) -> Rows | None:
    tables: dict[tuple[str, str], Rows] = {
        ("Sentient Magic Item Alignment", "DMG"): [
            ["01-50", "Lawful good"],
            ["51-00", "Chaotic evil"],
        ],
        ("Sentient Magic Item Senses", "DMG"): [["1", "Hearing"], ["2", "Darkvision"]],
        ("Minor Beneficial Properties", "DMG"): [["01-100", "Immune to {@condition disease}."]],
    }
    return next((tables[(caption, s)] for s in sources if (caption, s) in tables), None)


def test_a_mind_rolled_on_the_guide() -> None:
    guide = guide_for(_find, rules_2024=True)  # no 2024 guide here: the 2014 one
    assert guide.source == "DMG" and guide.contested
    assert guide.has("alignment") and not guide.has("purpose")
    mind = roll_mind(guide, Fixed(2))  # type: ignore[arg-type]
    assert (mind.intelligence, mind.alignment, mind.senses, mind.purpose) == (
        6,
        "Lawful good",
        "Darkvision",
        "",
    )
    assert score(Fixed(6)) == 18  # type: ignore[arg-type]
    assert roll_part(guide, "minor_beneficial", Fixed(70)) == "Immune to disease."  # type: ignore[arg-type]
    assert reroll(mind, "alignment", guide, Fixed(99)).alignment == "Chaotic evil"  # type: ignore[arg-type]
    # A part without a table stays as written.
    assert reroll(mind, "purpose", guide, Fixed(1)) == mind  # type: ignore[arg-type]
    assert conflict_dc(ItemMind(charisma=16)) == 15 and conflict_dc(ItemMind(charisma=7)) == 10
    # No guide: the world's rules decide the conflict.
    assert not guide_for(lambda c, s: None, rules_2024=True).contested


def test_room_for_properties() -> None:
    class P:
        def __init__(self, kind: str) -> None:
            self.kind = kind

    assert room([P("major_beneficial")], "major_beneficial")
    assert not room([P("major_beneficial")] * 2, "major_beneficial")
    assert not room([], "huge")


def test_kept_in_the_file_only_when_there() -> None:
    plain = InventoryItem(item=ChosenOption(name="Longsword", source="PHB"))
    assert "mind" not in plain.model_dump() and "artifact" not in plain.model_dump()
    mind = plain.model_copy(update={"mind": ItemMind(alignment="Neutral")})
    assert mind.model_dump()["mind"]["alignment"] == "Neutral"


@requires_corpus
class TestAtTheTable:
    async def test_an_item_awakens_and_argues_with_its_bearer(
        self,
        table: tuple[AsyncClient, StoreRegistry, str],  # noqa: F811
    ) -> None:
        ac, registry, lyra = table
        pc = registry.gamestate.characters.get(lyra)
        world = pc.world_id
        assert world is not None
        sword = InventoryItem(item=ChosenOption(name="Longsword", source="PHB"))
        registry.gamestate.characters.update(lyra, inventory=[*pc.inventory, sword])
        index = len(pc.inventory)
        url = f"/characters/{lyra}/mind"
        keep = {"index": str(index), "ref": "Longsword"}
        page = (await ac.get(url, params=keep)).text
        assert "Roll a mind" in page
        # The 2024 guide makes the world's rules 2024: its tables, the bearer's save.
        await ac.post(url, data={**keep, "op": "awaken"})
        carried = registry.gamestate.characters.get(lyra).inventory[index]
        assert carried.mind is not None
        assert all(3 <= s <= 18 for s in (carried.mind.intelligence, carried.mind.charisma))
        assert carried.mind.alignment and carried.mind.communication and carried.mind.purpose
        page = (await ac.get(url, params=keep)).text
        assert "(2024 guide)" in page and "Special purpose" in page
        # Written over, then one part rolled again: the rest stays as written.
        await ac.post(
            url,
            data={
                **keep,
                "op": "save",
                "charisma": "14",
                "languages": "Common, Elvish",
                "senses": "Darkvision 60 ft.",
                "communication": "It speaks Common.",
                "reroll": "alignment",
            },
        )
        mind = registry.gamestate.characters.get(lyra).inventory[index].mind
        assert mind is not None
        assert (mind.charisma, mind.languages, mind.senses) == (
            14,
            "Common, Elvish",
            "Darkvision 60 ft.",
        )
        # Artifact properties: rolled, written, taken off.
        await ac.post(url, data={**keep, "op": "roll_property", "kind": "major_detrimental"})
        await ac.post(
            url, data={**keep, "op": "add_property", "kind": "minor_beneficial", "text": "Glows."}
        )
        props = registry.gamestate.characters.get(lyra).inventory[index].artifact
        assert [p.kind for p in props] == ["major_detrimental", "minor_beneficial"]
        assert props[0].text
        await ac.post(url, data={**keep, "op": "drop_property", "n": "0"})
        props = registry.gamestate.characters.get(lyra).inventory[index].artifact
        assert [p.text for p in props] == ["Glows."]
        sheet = (await ac.get(f"/characters/{lyra}")).text
        assert "Sentient" in sheet and "It speaks Common." in sheet and "Glows." in sheet
        assert f"/characters/{lyra}/mind?index={index}" in sheet
        menu = (await ac.get(f"/menu/item/{lyra}/{index}")).text
        assert "Mind and properties" in menu
        # The conflict: the bearer's Charisma save against DC 12 + 2.
        done = await ac.post(url, data={**keep, "op": "conflict"})
        assert "what=conflict" in done.headers["location"] and "dc=14" in done.headers["location"]
        page = (await ac.get(done.headers["location"])).text
        assert "against DC 14" in page
        log = (await ac.get(f"/log/panel?world={world}")).text
        assert "Longsword" in log and "DC 14" in log
        # A world of the 2014 guide alone: a contest of Charisma checks.
        registry.gamestate.worlds.update(
            world,
            source_set=registry.gamestate.worlds.get(world).source_set.model_copy(
                update={"sources": ["PHB", "MM", "XGE", "DMG"]}
            ),
        )
        done = await ac.post(url, data={**keep, "op": "conflict"})
        assert "item=" in done.headers["location"] and "dc=&" in done.headers["location"]
        # Taking control: charmed on a failed save, as a charm the clock wears off.
        done = await ac.post(url, data={**keep, "op": "control"})
        charmed = "won=1" in done.headers["location"]
        after = registry.gamestate.characters.get(lyra)
        charms = [a for a in after.state.afflictions if a.kind == "charm"]
        assert bool(charms) == charmed
        if charmed:
            assert charms[0].ends is not None and "charmed" in after.state.conditions
        # Its mind taken away; the properties stay.
        await ac.post(url, data={**keep, "op": "forget"})
        carried = registry.gamestate.characters.get(lyra).inventory[index]
        assert carried.mind is None and carried.artifact
        # A page for another item than the one named is not found; players have none.
        assert (await ac.get(url, params={**keep, "ref": "Dagger|PHB"})).status_code == 404
