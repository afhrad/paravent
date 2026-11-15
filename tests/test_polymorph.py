"""Polymorph and True Polymorph: the rules, a character or a monster in a shape, and the
shape ending with the caster's concentration (app/services/polymorph.py)."""

from __future__ import annotations

import json
import random
from collections.abc import AsyncIterator
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Combat, Encounter, EncounterMonster, Role, SourceSet, World
from app.models.pc import ChosenOption, PlayerCharacter
from app.services.combat import CombatError
from app.services.dice import Dice
from app.services.monster_attacks import Damage
from app.services.play import Actor
from app.services.polymorph import allows, edition, is_polymorph
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus

ROOT = Path(__file__).resolve().parent.parent
LYRA = ROOT / "documentation" / "examples" / "lyra-mooncaller.json"
GM = "dm"


def _creature(cr: str, *types: str) -> SimpleNamespace:
    return SimpleNamespace(
        cr=SimpleNamespace(value=Fraction(cr)), kind=SimpleNamespace(types=types)
    )


class TestRules:
    def test_which_spells(self) -> None:
        assert is_polymorph("Polymorph|PHB") and is_polymorph("True Polymorph")
        assert not is_polymorph("Shapechange|PHB")

    def test_edition_follows_the_printing(self) -> None:
        assert edition("Polymorph|PHB") == "2014" and edition("Polymorph|XPHB") == "2024"

    def test_a_beast_no_stronger_than_the_target(self) -> None:
        assert allows("Polymorph|PHB", Fraction(4), _creature("1", "beast"))
        assert not allows("Polymorph|PHB", Fraction(4), _creature("7", "beast"))
        assert not allows("Polymorph|PHB", Fraction(4), _creature("1", "humanoid"))
        assert allows("True Polymorph|PHB", Fraction(4), _creature("1", "humanoid"))


class _Fixed(random.Random):
    """Every die shows ``face`` (capped at its size)."""

    def __init__(self, face: int) -> None:
        super().__init__(0)
        self.face = face

    def randint(self, a: int, b: int) -> int:
        return min(self.face, b)


class Fight:
    """A registry with Lyra (wizard 10, who knows Polymorph) fighting goblins and a bugbear."""

    def __init__(self, root: Path, corpus: Path) -> None:
        self.registry = StoreRegistry(
            Settings(reference_dir=corpus, library_dir=root, watch_files=False)
        )
        self.registry.load()
        store = self.registry.gamestate
        world = store.worlds.create(World(name="W", source_set=SourceSet(sources=["PHB", "MM"])))
        data = json.loads(LYRA.read_text(encoding="utf-8"))
        data.pop("$schema", None)
        data["world_id"] = world.id
        self.lyra = store.characters.create(PlayerCharacter.model_validate(data))
        encounter = store.encounters.create(
            Encounter(
                name="Ambush",
                world_id=world.id,
                monsters=[
                    EncounterMonster(name="Goblin", source="MM", count=1),
                    EncounterMonster(name="Bugbear", source="MM", count=1),
                ],
            )
        )
        self.combat = self.registry.combats.start(encounter, GM)

    @property
    def now(self) -> Combat:
        return self.registry.combats.get(self.combat.id)

    def member(self, label: str) -> str:
        return next(c.id for c in self.now.combatants if self.registry.combats.label_of(c) == label)

    def lyra_row(self) -> str:
        return next(c.id for c in self.now.combatants if c.character_id == self.lyra.id)

    def concentrate_lyra(self, spell: str = "Polymorph|PHB") -> None:
        def held(pc: PlayerCharacter) -> None:
            pc.state.concentration = ChosenOption.model_validate(spell)

        self.registry.gamestate.characters.mutate(self.lyra.id, held)

    def pc(self) -> PlayerCharacter:
        return self.registry.gamestate.characters.get(self.lyra.id)


@pytest.fixture
def fight(tmp_path: Path, corpus: Path) -> Fight:
    return Fight(tmp_path / "lib", corpus)


@requires_corpus
class TestAMonsterInAShape:
    def test_a_goblin_turned_into_a_frog_fights_and_falls_back(self, fight: Fight) -> None:
        combats = fight.registry.combats
        goblin = fight.member("Goblin")
        combats.polymorph(fight.combat.id, goblin, "Frog|MM", "Polymorph|PHB", "x", GM)
        member = next(c for c in fight.now.combatants if c.id == goblin)
        frog = combats.monster_of(member)
        assert frog is not None and frog.name == "Frog"
        row = combats.row(member)
        assert (row.ac, row.hp, row.hp_max) == (frog.ac, frog.hp, frog.hp)
        before = member.hp
        combats.damage(fight.combat.id, goblin, 3, GM)  # the frog's 1, two carry over
        member = next(c for c in fight.now.combatants if c.id == goblin)
        assert member.form is None and member.hp == before - 2

    def test_the_spells_rules_refuse_a_stronger_shape(self, fight: Fight) -> None:
        goblin = fight.member("Goblin")
        with pytest.raises(CombatError) as refused:
            fight.registry.combats.polymorph(
                fight.combat.id, goblin, "Giant Ape|MM", "Polymorph|PHB", "x", GM
            )
        assert refused.value.key == "play.error.shape_refused"
        with pytest.raises(CombatError):  # not a beast
            fight.registry.combats.polymorph(
                fight.combat.id, goblin, "Kobold|MM", "Polymorph|PHB", "x", GM
            )
        fight.registry.combats.polymorph(
            fight.combat.id, goblin, "Kobold|MM", "True Polymorph|PHB", "x", GM
        )

    def test_a_2024_shape_lasts_while_its_temporary_hit_points_do(self, fight: Fight) -> None:
        combats = fight.registry.combats
        goblin = fight.member("Goblin")
        combats.polymorph(fight.combat.id, goblin, "Frog|MM", "Polymorph|XPHB", "x", GM)
        member = next(c for c in fight.now.combatants if c.id == goblin)
        assert member.form is not None and member.form.hp is None and member.hp_temp == 1
        combats.damage(fight.combat.id, goblin, 3, GM)
        member = next(c for c in fight.now.combatants if c.id == goblin)
        assert member.form is None and member.hp == member.hp_max - 2

    def test_the_shapes_offered(self, fight: Fight) -> None:
        goblin = next(c for c in fight.now.combatants if c.ref == "Goblin|MM")
        names = {s.name for s in fight.registry.combats.shapes(fight.now, goblin, "Polymorph|PHB")}
        assert "Frog" in names and "Giant Ape" not in names and "Kobold" not in names


@requires_corpus
class TestTheCastersConcentration:
    def test_a_character_dropping_it_frees_the_monster(self, fight: Fight) -> None:
        caster = f"character:{fight.lyra.id}"
        goblin = fight.member("Goblin")
        fight.concentrate_lyra()
        fight.registry.combats.polymorph(
            fight.combat.id, goblin, "Frog|MM", "Polymorph|PHB", caster, GM
        )
        characters = fight.registry.characters
        characters.play.act(
            fight.lyra.id, "concentration", {}, characters.sheet(fight.pc()), Actor(GM)
        )
        assert next(c for c in fight.now.combatants if c.id == goblin).form is None

    def test_a_failed_concentration_save_in_the_fight_frees_it_too(self, fight: Fight) -> None:
        caster = f"character:{fight.lyra.id}"
        goblin = fight.member("Goblin")
        fight.concentrate_lyra()
        combats = fight.registry.combats
        combats.polymorph(fight.combat.id, goblin, "Frog|MM", "Polymorph|PHB", caster, GM)
        fight.registry.characters.play._dice = Dice(_Fixed(1))  # the save fails
        combats.damage(fight.combat.id, fight.lyra_row(), 12, GM)
        assert fight.pc().state.concentration is None
        assert next(c for c in fight.now.combatants if c.id == goblin).form is None

    def test_a_character_in_a_monsters_shape_and_out_again(self, fight: Fight) -> None:
        combats = fight.registry.combats
        bugbear = fight.member("Bugbear")
        combats.concentrate(fight.combat.id, bugbear, "Polymorph", GM)
        combats.polymorph(
            fight.combat.id,
            fight.lyra_row(),
            "Giant Ape|MM",
            "Polymorph|PHB",
            f"combatant:{bugbear}",
            GM,
        )
        sheet = fight.registry.characters.sheet(fight.pc())
        assert sheet.spell_form is not None and sheet.spell_form.name == "Giant Ape"
        assert sheet.armor_class.value == 12 and sheet.form is not None
        hp = sheet.hp_current
        combats.damage(fight.combat.id, fight.lyra_row(), 10, GM)
        state = fight.pc().state
        assert state.polymorph is not None and state.polymorph.hp == 147
        assert fight.registry.characters.sheet(fight.pc()).hp_current == hp
        combats.concentrate(fight.combat.id, bugbear, None, GM)
        assert fight.pc().state.polymorph is None


PASSWORD = "correct horse battery"


@pytest.fixture
async def gm(fight: Fight) -> AsyncIterator[AsyncClient]:
    fight.registry.auth.create_user(GM, PASSWORD, Role.GAMEMASTER)
    app = create_app()
    app.state.stores = fight.registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": GM, "password": PASSWORD})
        yield ac


@requires_corpus
class TestASpellOverByItself:
    """A shape that ends by itself ends the spell, so the caster stops concentrating."""

    def test_the_frogs_fall_ends_its_casters_concentration(self, fight: Fight) -> None:
        goblin = fight.member("Goblin")
        fight.concentrate_lyra()
        combats = fight.registry.combats
        caster = f"character:{fight.lyra.id}"
        combats.polymorph(fight.combat.id, goblin, "Frog|MM", "Polymorph|PHB", caster, GM)
        combats.damage(fight.combat.id, goblin, 3, GM)
        assert fight.pc().state.concentration is None

    def test_a_characters_lost_shape_ends_a_monsters_concentration(self, fight: Fight) -> None:
        combats = fight.registry.combats
        bugbear = fight.member("Bugbear")
        combats.concentrate(fight.combat.id, bugbear, "Polymorph", GM)
        combats.polymorph(
            fight.combat.id,
            fight.lyra_row(),
            "Frog|MM",
            "Polymorph|XPHB",
            f"combatant:{bugbear}",
            GM,
        )
        combats.damage(fight.combat.id, fight.lyra_row(), 3, GM)  # the frog's 1 temporary HP
        assert fight.pc().state.polymorph is None
        assert next(c for c in fight.now.combatants if c.id == bugbear).concentration is None

    def test_the_caster_falling_unconscious_frees_the_frog(self, fight: Fight) -> None:
        goblin = fight.member("Goblin")
        fight.concentrate_lyra()
        combats = fight.registry.combats
        caster = f"character:{fight.lyra.id}"
        combats.polymorph(fight.combat.id, goblin, "Frog|MM", "Polymorph|PHB", caster, GM)
        combats.toggle_condition(fight.combat.id, fight.lyra_row(), "unconscious", GM)
        assert fight.pc().state.concentration is None
        assert next(c for c in fight.now.combatants if c.id == goblin).form is None

    def test_a_stunned_monster_lets_its_shape_go(self, fight: Fight) -> None:
        combats = fight.registry.combats
        bugbear = fight.member("Bugbear")
        goblin = fight.member("Goblin")
        combats.concentrate(fight.combat.id, bugbear, "Polymorph", GM)
        combats.polymorph(
            fight.combat.id, goblin, "Frog|MM", "Polymorph|PHB", f"combatant:{bugbear}", GM
        )
        combats.toggle_condition(fight.combat.id, bugbear, "stunned", GM)
        assert next(c for c in fight.now.combatants if c.id == bugbear).concentration is None
        assert next(c for c in fight.now.combatants if c.id == goblin).form is None

    def test_revert_by_hand_leaves_the_concentration(self, fight: Fight) -> None:
        goblin = fight.member("Goblin")
        fight.concentrate_lyra()
        combats = fight.registry.combats
        caster = f"character:{fight.lyra.id}"
        combats.polymorph(fight.combat.id, goblin, "Frog|MM", "Polymorph|PHB", caster, GM)
        combats.end_form(fight.combat.id, goblin, GM)
        held = fight.pc().state.concentration
        assert held is not None and held.name == "Polymorph"


@requires_corpus
class TestWhatThePartyLearns:
    def test_a_creature_in_a_shape_is_learned_as_the_shape(self, fight: Fight) -> None:
        combats = fight.registry.combats
        combats._dice = Dice(_Fixed(10))  # type: ignore[attr-defined]
        bugbear, goblin = fight.member("Bugbear"), fight.member("Goblin")
        combats.polymorph(fight.combat.id, bugbear, "Brown Bear|MM", "Polymorph|PHB", "x", GM)
        combats.attack(fight.combat.id, bugbear, "Bite", 5, [Damage("1d8+4", "piercing")], GM)
        combats.attack(fight.combat.id, goblin, "Scimitar", 4, [Damage("1d6+2", "slashing")], GM)
        scimitar = next(e for e in fight.now.pending if e.actor == goblin)
        combats.resolve_attack(fight.combat.id, scimitar.at.isoformat(), bugbear, GM)
        world = fight.now.world_id
        bears = fight.registry.knowledge.of(world, "Brown Bear|MM")
        assert "action:Bite" in bears and "damage:slashing" in bears
        assert fight.registry.knowledge.of(world, "Bugbear|MM") == {}


@requires_corpus
class TestOnScreen:
    async def test_the_tracker_turns_a_goblin_into_a_frog(
        self, fight: Fight, gm: AsyncClient
    ) -> None:
        goblin = fight.member("Goblin")
        url = f"/gm/combat/{fight.combat.id}/c/{goblin}/polymorph"
        form = (await gm.get(url)).text
        assert 'value="Frog|MM"' in form and 'value="Giant Ape|MM"' not in form
        await gm.post(url, data={"shape": "Frog", "spell": "Polymorph|PHB", "caster": ""})
        member = next(c for c in fight.now.combatants if c.id == goblin)
        assert member.form is not None and member.form.form == "Frog|MM"
        board = (await gm.get(f"/gm/combat/{fight.combat.id}")).text
        assert "Frog form" in board and "/end_form" in board

    async def test_the_casters_sheet_turns_a_creature_of_its_fight(
        self, fight: Fight, gm: AsyncClient
    ) -> None:
        goblin = fight.member("Goblin")
        fight.concentrate_lyra()
        sheet = (await gm.get(f"/characters/{fight.lyra.id}")).text
        assert f'value="combatant:{goblin}"' in sheet and 'value="Frog|MM"' in sheet
        await gm.post(
            f"/characters/{fight.lyra.id}/polymorph",
            data={"target": f"combatant:{goblin}", "shape": "Frog|MM"},
        )
        member = next(c for c in fight.now.combatants if c.id == goblin)
        assert member.form is not None and member.form.caster == f"character:{fight.lyra.id}"

    async def test_a_shape_is_not_taken_through_the_plain_sheet_action(
        self, fight: Fight, gm: AsyncClient
    ) -> None:
        await gm.post(
            f"/characters/{fight.lyra.id}/act/polymorph",
            data={"op": "assume", "form": "Giant Ape|MM", "spell": "Polymorph|PHB", "hp": "999"},
        )
        assert fight.pc().state.polymorph is None
