"""A character's deity and a cleric's Divine Intervention (app/services/intervention.py):
the 2014 plea and its waits, the 2024 spell cast without a slot, the world's pantheons."""

from __future__ import annotations

from httpx import AsyncClient

from app.models.gamestate import SourceSet
from app.models.pc import ClassLevel, PlayerCharacter
from app.services.clock import DAY
from app.services.intervention import Intervention, answered, intervention_of
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import FixedDice, table  # noqa: F401


def _cleric(level: int, source: str = "PHB") -> PlayerCharacter:
    return PlayerCharacter(
        name="Sera", classes=[ClassLevel(name="Cleric", source=source, level=level)]
    )


class TestRules:
    def test_from_level_ten_by_the_class_book(self) -> None:
        assert intervention_of(_cleric(9)) is None
        assert intervention_of(PlayerCharacter(name="W")) is None
        held = intervention_of(_cleric(12))
        assert held is not None and (held.rules, held.level, held.greater) == ("classic", 12, False)
        new = intervention_of(_cleric(20, "XPHB"))
        assert new is not None and new.rules == "one" and new.greater

    def test_the_plea_and_its_waits(self) -> None:
        assert answered(12, 12) and not answered(13, 12) and answered(100, 20)
        assert Intervention("classic", 12).ready(0)
        assert not Intervention("classic", 12, rests=1).ready(0)
        waits = Intervention("classic", 12, until=7 * DAY)
        assert not waits.ready(DAY) and waits.ready(7 * DAY)


@requires_corpus
class TestAtTheTable:
    async def test_a_2014_cleric_calls_on_the_deity(
        self,
        table: tuple[AsyncClient, StoreRegistry, str],  # noqa: F811
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        sera = _cleric(12).model_copy(update={"world_id": world})
        sera = registry.gamestate.characters.create(sera)
        # The deity, from the world's books; one outside them is refused.
        sheet = (await ac.get(f"/characters/{sera.id}")).text
        assert 'name="deity"' in sheet and "Lathander" in sheet
        await ac.post(f"/characters/{sera.id}/deity", data={"deity": "Lathander|PHB"})
        assert registry.gamestate.characters.get(sera.id).deity.name == "Lathander"  # type: ignore[union-attr]
        bad = await ac.post(f"/characters/{sera.id}/deity", data={"deity": "Nobody|PHB"})
        assert bad.status_code == 400
        sheet = (await ac.get(f"/characters/{sera.id}")).text
        assert "Divine Intervention" in sheet and "Call on Lathander" in sheet
        # Answered (05 against 12): not again for seven days of the world's clock.
        registry.characters.play._dice = FixedDice([5])
        now = registry.clock.of(world).minute
        called = await ac.post(f"/characters/{sera.id}/intervention")
        assert "flash=intervention.answered" in called.headers["location"]
        assert "Your deity answers" in (await ac.get(called.headers["location"])).text
        state = registry.gamestate.characters.get(sera.id).state
        assert state.intervention_until == now + 7 * DAY and state.intervention_rests == 0
        log = (await ac.get(f"/log/panel?world={world}")).text
        assert "Sera calls on Lathander: 5 against 12, answered" in log
        refused = await ac.post(f"/characters/{sera.id}/intervention")
        assert "flash=play.error.intervention_waits" in refused.headers["location"]
        assert "Again from" in (await ac.get(f"/characters/{sera.id}")).text
        # Unanswered (90): again after a long rest.
        registry.gamestate.characters.update(
            sera.id, state=state.model_copy(update={"intervention_until": None})
        )
        registry.characters.play._dice = FixedDice([90])
        await ac.post(f"/characters/{sera.id}/intervention")
        assert registry.gamestate.characters.get(sera.id).state.intervention_rests == 1
        await ac.post(f"/characters/{sera.id}/act/rest", data={"kind": "long"})
        assert registry.gamestate.characters.get(sera.id).state.intervention_rests == 0
        # The world's pantheons narrow the choice.
        registry.gamestate.worlds.update(world, pantheons=["Greyhawk"])
        sheet = (await ac.get(f"/characters/{sera.id}")).text
        assert "Bane" not in sheet

    async def test_a_2024_cleric_casts_without_a_slot(
        self,
        table: tuple[AsyncClient, StoreRegistry, str],  # noqa: F811
    ) -> None:
        ac, registry, lyra = table
        world = registry.gamestate.characters.get(lyra).world_id
        assert world is not None
        registry.gamestate.worlds.update(
            world, source_set=SourceSet(sources=["XPHB", "XDMG", "XMM"])
        )
        sera = registry.gamestate.characters.create(
            _cleric(10, "XPHB").model_copy(update={"world_id": world})
        )
        page = (await ac.get(f"/characters/{sera.id}/intervention")).text
        assert "Raise Dead" in page and "Spirit Guardians" in page and "Wish" not in page
        await ac.post(
            f"/characters/{sera.id}/intervention", data={"spell": "Spirit Guardians|XPHB"}
        )
        state = registry.gamestate.characters.get(sera.id).state
        assert state.intervention_rests == 1 and state.slots_used == {}
        assert state.concentration is not None and state.concentration.name == "Spirit Guardians"
        log = (await ac.get(f"/log/panel?world={world}")).text
        assert "calls on their god and casts Spirit Guardians" in log
        # Spent until the next long rest; a spell off the list is refused.
        assert (
            "Again after a long rest" in (await ac.get(f"/characters/{sera.id}/intervention")).text
        )
        off = await ac.post(f"/characters/{sera.id}/intervention", data={"spell": "Fireball|XPHB"})
        assert off.status_code == 400
