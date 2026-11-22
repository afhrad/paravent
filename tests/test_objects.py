"""Objects and doors in the tracker (app/services/objects.py), their numbers read from the
guides in the corpus."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.gamestate import Combat, SourceSet
from app.services.combat import CombatError
from app.services.i18n import UiCatalogue
from app.services.monster_attacks import attack_of
from app.services.objects import label_for, offered, statblock
from app.services.search import SearchResult
from app.store.compendium import CompendiumStore
from app.ui.combat_pages import add_panel
from app.ui.context import Page
from tests.conftest import requires_corpus
from tests.test_combat import GM
from tests.test_combat_rules import NO_SIDES, RulesTable, _begun

pytestmark = requires_corpus


@pytest.fixture
def table(tmp_path: Path, loaded_compendium: CompendiumStore) -> RulesTable:
    made = RulesTable(tmp_path / "lib", loaded_compendium, NO_SIDES)
    books = SourceSet(sources=["PHB", "MM", "DMG", "XDMG"])
    made.store.worlds.update(made.world.id, source_set=books)
    return made


def _thing(table: RulesTable, combat: Combat, label: str) -> str:
    return next(c.id for c in table.now(combat).combatants if c.label == label)


class TestTheTables:
    def test_both_guides_read(self, table: RulesTable) -> None:
        offer = offered(table.finder())
        assert ("Wood, bone", 15) in [(s.name, s.ac) for s in offer.substances]
        assert [(s.fragile, s.resilient) for s in offer.sizes][2] == (4, 18)  # Medium
        door = next(d for d in offer.doors if d.name == "Wooden door")
        assert (door.ac, door.hp, door.dc) == (15, 18, 15)

    def test_a_world_without_the_guides_offers_nothing(self, table: RulesTable) -> None:
        table.store.worlds.update(table.world.id, source_set=SourceSet(sources=["PHB", "MM"]))
        assert not offered(table.finder()).any

    def test_label(self) -> None:
        assert label_for("Wood, bone", "Medium (barrel, chandelier)") == "Wood (Medium)"


class TestInTheFight:
    def test_a_door_joins_takes_no_turn_and_breaks(self, table: RulesTable) -> None:
        combat = _begun(table, table.start())
        offer = table.combats.objects_offered(combat.world_id)
        wooden = next(i for i, d in enumerate(offer.doors) if d.name == "Wooden door")
        table.combats.add_from_guide(combat.id, GM, door=wooden)
        door = table.now(combat).combatant(_thing(table, combat, "Wooden door"))
        assert door is not None and door.thing is not None
        assert (door.ac, door.hp, door.thing.kind, door.thing.dc) == (15, 18, "door", 15)
        order = table.combats.order(table.now(combat))
        assert order[-1].members[0].id == door.id  # after everyone
        waiting = table.combats.view(table.now(combat)).waiting
        assert door.id not in [r.combatant.id for r in waiting]
        for _ in range(len(order) + 1):  # a whole round: the door never acts
            table.combats.next_turn(combat.id, GM)
            current = table.combats.current(table.now(combat))
            assert current is not None and current.members[0].id != door.id
        table.combats.damage(combat.id, door.id, 18, GM)
        broken = table.combats.view(table.now(combat)).row(door.id)
        assert broken is not None and broken.dead
        assert door.id not in [c.id for c in table.combats.summary(table.now(combat)).fallen]

    def test_an_object_by_substance_and_size(self, table: RulesTable) -> None:
        combat = table.start()
        offer = table.combats.objects_offered(combat.world_id)
        stone = next(i for i, s in enumerate(offer.substances) if s.name == "Stone")
        small = next(i for i, s in enumerate(offer.sizes) if s.name.startswith("Small"))
        table.combats.add_from_guide(
            combat.id, GM, substance=stone, size=small, resilient=True, label="Altar"
        )
        altar = table.now(combat).combatant(_thing(table, combat, "Altar"))
        assert altar is not None and (altar.ac, altar.hp) == (17, 10)

    def test_immune_to_poison_and_psychic(self, table: RulesTable) -> None:
        combat = table.start()
        table.combats.add_object(combat.id, "Chest", GM, ac=15, hp=10)
        chest = table.now(combat).combatant(_thing(table, combat, "Chest"))
        assert chest is not None
        assert table.combats._defenses_of(chest) == {"immune": ["poison", "psychic"]}

    def test_refused_numbers(self, table: RulesTable) -> None:
        combat = table.start()
        with pytest.raises(CombatError):
            table.combats.add_object(combat.id, "Nothing", GM, ac=15, hp=0)
        with pytest.raises(CombatError):
            table.combats.add_from_guide(combat.id, GM, door=99)


class TestTheForm:
    def test_offered_in_the_add_panel(self, table: RulesTable) -> None:
        combat = table.start()
        page = Page(UiCatalogue().translator("en"))
        html = str(
            add_panel(
                page,
                table.combats.view(combat),
                SearchResult(query="", group=None, groups=()),
                [],
                offer=table.combats.objects_offered(combat.world_id),
            )
        )
        assert "Wooden door · AC 15 · 18 HP · DC 15" in html
        assert 'name="substance"' in html


class TestSiege:
    @pytest.fixture
    def siege(self, table: RulesTable, loaded_compendium: CompendiumStore) -> RulesTable:
        rows = [
            e.model_dump(by_alias=True, exclude_none=True)
            for e in loaded_compendium.all("object")
            if e.source == "DMG"
        ]
        table.combats._objects_of = lambda world_id: rows
        return table

    def test_offered_from_the_books(self, siege: RulesTable) -> None:
        offer = siege.combats.objects_offered(siege.world.id)
        names = [x.reference for x in offer.siege]
        assert "Ballista|DMG" in names and "Siege Tower|DMG" not in names  # it has no attack

    def test_a_ballista_fights_for_a_side_and_rolls_its_bolt(self, siege: RulesTable) -> None:
        combat = _begun(siege, siege.start())
        siege.combats.add_siege(combat.id, "Ballista|DMG", GM, ally=True)
        ballista = siege.now(combat).combatant(_thing(siege, combat, "Ballista"))
        assert ballista is not None and ballista.thing is not None
        assert (ballista.ac, ballista.hp, ballista.ally, ballista.thing.kind) == (
            15,
            50,
            True,
            "siege",
        )
        data = siege.combats.siege_data(siege.now(combat), ballista)
        assert data is not None
        attack = attack_of(data["action"][0])
        assert attack is not None and attack.hit == 6
        assert [(d.expression, d.kind) for d in attack.damage] == [("3d10", "piercing")]
        assert siege.combats.order(siege.now(combat))[-1].members[0].id == ballista.id

    def test_unknown_weapon_refused(self, siege: RulesTable) -> None:
        combat = siege.start()
        with pytest.raises(CombatError):
            siege.combats.add_siege(combat.id, "Death Star|DMG", GM)

    def test_the_statblock_has_no_mind(self, siege: RulesTable) -> None:
        cauldron = next(
            e for e in siege.combats._objects_of(None) if e["name"] == "Suspended Cauldron"
        )
        block = statblock(cauldron)
        assert block["type"] == "object" and "str" not in block
        assert block["immune"] == ["poison", "psychic"]
