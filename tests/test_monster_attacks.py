"""Rolling statblock attacks in the tracker (app/services/monster_attacks.py)."""

from __future__ import annotations

import json
import random
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import (
    CombatLogEntry,
    Encounter,
    EncounterMonster,
    Role,
    SourceSet,
    World,
)
from app.models.protocol import EntryKind, ProtocolEntry
from app.services.combat import CombatNotFound
from app.services.dice import Dice
from app.services.monster_attacks import (
    Damage,
    against,
    attack_of,
    critical,
    decode,
    encode,
    save_of,
)
from app.services.protocol import attack_entry_id
from app.store.compendium import CompendiumStore
from app.store.protocol import log_key
from app.store.registry import StoreRegistry
from tests.conftest import requires_corpus

PASSWORD = "correct horse battery"
HTMX = {"HX-Request": "true"}


class TestReading:
    def test_a_plain_attack(self) -> None:
        entry = {
            "name": "Scimitar",
            "entries": [
                "{@atk mw} {@hit 4} to hit, reach 5 ft., one target. "
                "{@h}5 ({@damage 1d6 + 2}) slashing damage."
            ],
        }
        attack = attack_of(entry)
        assert attack is not None and attack.hit == 4
        assert attack.damage == (Damage("1d6+2", "slashing"),)

    def test_plus_adds_and_or_is_an_alternative(self) -> None:
        bite = {
            "name": "Bite",
            "entries": [
                "{@hit 6} to hit. {@h}8 ({@damage 1d10 + 3}) piercing damage plus 7 "
                "({@damage 2d6}) poison damage."
            ],
        }
        assert [d.kind for d in attack_of(bite).damage] == ["piercing", "poison"]  # type: ignore[union-attr]
        sword = {
            "name": "Longsword",
            "entries": [
                "{@hit 5} to hit. {@h}7 ({@damage 1d8 + 3}) slashing damage, or 8 "
                "({@damage 1d10 + 3}) slashing damage if used with two hands."
            ],
        }
        assert attack_of(sword).damage == (Damage("1d8+3", "slashing"),)  # type: ignore[union-attr]

    def test_no_to_hit_is_no_attack(self) -> None:
        assert attack_of({"name": "Multiattack", "entries": ["It makes two attacks."]}) is None

    def test_a_critical_doubles_the_dice_only(self) -> None:
        assert critical("1d6+2") == "2d6+2"
        assert critical("2d8+1d6-1") == "4d8+2d6-1"

    def test_the_form_field_keeps_only_dice(self) -> None:
        field = encode(attack_of({"name": "x", "entries": ["{@hit 1} {@damage 1d4} fire"]}))  # type: ignore[arg-type]
        assert decode(field) == (Damage("1d4", "fire"),)
        assert decode("rm -rf:slashing;1d4:fire bolt;2d6:cold") == (Damage("2d6", "cold"),)

    @requires_corpus
    def test_every_goblin_attack_reads(self, loaded_compendium: CompendiumStore) -> None:
        goblin = loaded_compendium.get("monster", "goblin|mm").model_dump(by_alias=True)
        attacks = [a for e in goblin["action"] if (a := attack_of(e)) is not None]
        assert [a.hit for a in attacks] == [4, 4]
        assert attacks[0].damage == (Damage("1d6+2", "slashing"),)


@requires_corpus
class TestSaves:
    def _action(self, store: CompendiumStore, key: str, name: str) -> dict[str, object]:
        data = store.get("monster", key).model_dump(by_alias=True, exclude_none=True)
        actions = [
            a for s in ("action", "bonus", "reaction", "legendary") for a in data.get(s) or []
        ]
        return next(a for a in actions if a["name"].startswith(name))

    def test_breath_in_both_editions(self, loaded_compendium: CompendiumStore) -> None:
        old = save_of(self._action(loaded_compendium, "adult red dragon|mm", "Fire Breath"))
        new = save_of(self._action(loaded_compendium, "adult red dragon|xmm", "Fire Breath"))
        assert old is not None and (old.dc, old.ability, old.half) == (21, "dex", True)
        assert old.damage == (Damage("18d6", "fire"),)
        assert new is not None and (new.dc, new.ability, new.half) == (21, "dex", True)

    def test_an_attack_is_not_a_save_effect(self, loaded_compendium: CompendiumStore) -> None:
        bite = self._action(loaded_compendium, "adult red dragon|mm", "Bite")
        assert save_of(bite) is None

    def test_conditions_and_their_duration(self, loaded_compendium: CompendiumStore) -> None:
        fear = save_of(self._action(loaded_compendium, "adult red dragon|mm", "Frightful Presence"))
        assert fear is not None and not fear.damage
        assert (fear.ability, fear.condition, fear.rounds) == ("wis", "frightened", 10)
        assert fear.repeat == "end"  # "can repeat the saving throw at the end of each of its turns"
        web = save_of(self._action(loaded_compendium, "giant spider|xmm", "Web"))
        assert web is not None and (web.condition, web.rounds) == ("restrained", None)
        # "if the medusa isn't incapacitated" names an exception, not the effect
        gaze = save_of(self._action(loaded_compendium, "medusa|xmm", "Petrifying Gaze"))
        assert gaze is not None and gaze.condition == "restrained"

    def test_later_damage_is_another_effect(self, loaded_compendium: CompendiumStore) -> None:
        engulf = save_of(self._action(loaded_compendium, "gelatinous cube|xmm", "Engulf"))
        assert engulf is not None and engulf.damage == (Damage("3d6", "acid"),)


class TestAgainst:
    def test_immune_resistant_vulnerable(self) -> None:
        skeleton = {
            "immune": ["poison"],
            "resist": ["fire", {"resist": ["slashing"], "note": "from nonmagical attacks"}],
            "vulnerable": ["bludgeoning"],
        }
        assert against({"poison": 7}, skeleton) == 0
        assert against({"fire": 7}, skeleton) == 3  # half, rounded down
        assert against({"bludgeoning": 5}, skeleton) == 10
        assert against({"slashing": 6}, skeleton) == 6  # conditional: the gamemaster decides
        assert against({"slashing": 6, "poison": 4}, None) == 10  # no statblock: as rolled
        # a character's sheet gives the same shape (CharacterSide.defenses)
        assert against({"fire": 9}, {"resist": ["fire"], "immune": [], "vulnerable": []}) == 4


class _Fixed(random.Random):
    """Every die shows ``face`` (capped at its size)."""

    def __init__(self, face: int) -> None:
        super().__init__(0)
        self.face = face

    def randint(self, a: int, b: int) -> int:
        return min(self.face, b)


@pytest.fixture
async def tracker(
    tmp_path: Path, corpus: Path
) -> AsyncIterator[tuple[AsyncClient, StoreRegistry, str]]:
    registry = StoreRegistry(
        Settings(
            reference_dir=corpus,
            library_dir=tmp_path / "lib",
            # No books' pictures: the corpus folder may have an img/ beside it.
            image_dir=tmp_path / "no-images",
            watch_files=False,
        )
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    world = registry.gamestate.worlds.create(World(name="W", source_set=SourceSet(sources=["MM"])))
    encounter = registry.gamestate.encounters.create(
        Encounter(
            name="Ambush",
            world_id=world.id,
            monsters=[EncounterMonster(name="Goblin", source="MM", count=2)],
        )
    )
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        url = (await ac.post(f"/gm/encounters/{encounter.id}/run")).headers["location"]
        yield ac, registry, url


def _pending(registry: StoreRegistry, combat_id: str) -> CombatLogEntry:
    """The newest attack or save effect waiting for the gamemaster."""
    return registry.combats.get(combat_id).pending[0]


def _fight(registry: StoreRegistry, combat_id: str) -> list[ProtocolEntry]:
    """The fight's entries in the log, newest first."""
    world = registry.combats.get(combat_id).world_id
    return [
        e
        for e in reversed(registry.protocol.store.entries(log_key(world)))
        if e.scope.combat == combat_id
    ]


def _about(registry: StoreRegistry, character_id: str) -> list[ProtocolEntry]:
    """The log's entries about a character, newest first — in every log: a character
    without a world rolls into the global one, its fight's lines go to the fight's world."""
    store = registry.protocol.store
    ref = f"character:{character_id}"
    found = [e for key in store.logs() for e in store.entries(key) if e.subject == ref]
    return sorted(found, key=lambda e: e.at, reverse=True)


@requires_corpus
class TestInTheTracker:
    async def test_the_statblock_offers_the_attack(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, url = tracker
        goblin = registry.combats.get(url.rsplit("/", 1)[1]).combatants[0]
        page = (await ac.get(url, params={"sel": goblin.id})).text
        assert f"/c/{goblin.id}/attack" in page and 'name="dmg" value="1d6+2:slashing"' in page

    async def test_a_natural_20_doubles_and_sounds(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        goblin = registry.combats.get(combat_id).combatants[0]
        registry.combats._dice = Dice(_Fixed(20))  # type: ignore[attr-defined]
        answer = await ac.post(
            f"{url}/c/{goblin.id}/attack",
            data={"name": "Scimitar", "hit": "4", "dmg": "1d6+2:slashing"},
            headers=HTMX,
        )
        assert json.loads(answer.headers["HX-Trigger"]) == {"paravent-roll": {"outcome": "crit"}}
        entry = _pending(registry, combat_id)
        assert (entry.kind, entry.amount, entry.damage, entry.outcome) == ("attack", 24, 14, "crit")
        assert "2d6" in entry.dice  # the dice doubled: 6 + 6 + 2
        assert "Scimitar: 24 to hit, 14 damage" in answer.text  # waiting to be resolved
        rolled = _fight(registry, combat_id)[0]
        assert rolled.kind is EntryKind.ATTACK and rolled.outcome == "crit"
        assert rolled.damage_rolls[0].expression == "2d6+2"

    async def test_a_natural_1_misses(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        goblin = registry.combats.get(combat_id).combatants[0]
        registry.combats._dice = Dice(_Fixed(1))  # type: ignore[attr-defined]
        answer = await ac.post(
            f"{url}/c/{goblin.id}/attack",
            data={"name": "Scimitar", "hit": "4", "dmg": "1d6+2:slashing"},
            headers=HTMX,
        )
        assert json.loads(answer.headers["HX-Trigger"])["paravent-roll"]["outcome"] == "fumble"
        entry = _fight(registry, combat_id)[0]
        assert entry.kind is EntryKind.ATTACK and entry.outcome == "fumble"
        assert entry.damage_rolls == [] and not registry.combats.get(combat_id).pending

    async def test_a_hit_applies_the_damage_and_a_miss_does_not(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        one, two = registry.combats.get(combat_id).combatants[:2]
        registry.combats._dice = Dice(_Fixed(3))  # type: ignore[attr-defined]
        rolled = await ac.post(
            f"{url}/c/{one.id}/attack",
            data={"name": "Scimitar", "hit": "4", "dmg": "1d6+2:slashing"},
            headers=HTMX,
        )
        assert "Attacks to resolve" in rolled.text
        entry = _pending(registry, combat_id)
        assert (entry.damage, entry.damage_types, entry.actor) == (5, {"slashing": 5}, one.id)
        hp = next(c for c in registry.combats.get(combat_id).combatants if c.id == two.id).hp
        hit = await ac.post(
            f"{url}/attack", data={"at": entry.at.isoformat(), "target": two.id}, headers=HTMX
        )
        after = next(c for c in registry.combats.get(combat_id).combatants if c.id == two.id)
        assert after.hp == hp - 5
        assert "Attacks to resolve" not in hit.text
        verdict = next(e for e in _fight(registry, combat_id) if e.kind is EntryKind.ATTACK)
        assert (verdict.outcome, verdict.target_name, verdict.amount) == ("hit", "Goblin 2", 5)
        again = await ac.post(
            f"{url}/attack", data={"at": entry.at.isoformat(), "target": two.id}, headers=HTMX
        )
        assert "already resolved" in again.text  # no second helping

        await ac.post(
            f"{url}/c/{one.id}/attack",
            data={"name": "Scimitar", "hit": "4", "dmg": "1d6+2:slashing"},
            headers=HTMX,
        )
        second = _pending(registry, combat_id)
        missed = await ac.post(
            f"{url}/attack", data={"at": second.at.isoformat(), "miss": "1"}, headers=HTMX
        )
        assert missed.status_code == 200
        assert _fight(registry, combat_id)[0].outcome == "miss"
        assert (
            next(c for c in registry.combats.get(combat_id).combatants if c.id == two.id).hp
            == hp - 5
        )

    async def test_a_character_target_resists_by_its_sheet(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        from app.models.pc import PlayerCharacter

        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        pc = registry.gamestate.characters.create(
            PlayerCharacter.model_validate(
                {
                    "name": "Kallista",
                    "species": "Tiefling|PHB",
                    "classes": [{"class": "Warlock|PHB", "level": 3}],
                    "abilities": {"con": 14},
                    "state": {"hp": 20},
                }
            )
        )
        registry.combats.add_character(combat_id, pc.id, "dm")
        goblin = registry.combats.get(combat_id).combatants[0]
        target = next(c for c in registry.combats.get(combat_id).combatants if c.character_id)
        registry.combats._dice = Dice(_Fixed(3))  # type: ignore[attr-defined]
        await ac.post(
            f"{url}/c/{goblin.id}/attack",
            data={"name": "Fire Bolt", "hit": "4", "dmg": "2d6:fire"},
            headers=HTMX,
        )
        entry = _pending(registry, combat_id)
        await ac.post(
            f"{url}/attack", data={"at": entry.at.isoformat(), "target": target.id}, headers=HTMX
        )
        assert registry.gamestate.characters.get(pc.id).state.hp == 20 - 3  # 6 fire, halved

    async def test_a_breath_weapon_settled_per_target(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        registry.combats.add_monster(combat_id, "Young White Dragon|MM", 1, "dm")
        combatants = registry.combats.get(combat_id).combatants
        one, two = combatants[0], combatants[1]
        dragon = next(c for c in combatants if c.ref == "Young White Dragon|MM")
        registry.combats._dice = Dice(_Fixed(3))  # type: ignore[attr-defined]
        page = (await ac.get(url, params={"sel": dragon.id})).text
        assert f"/c/{dragon.id}/save_effect" in page and 'name="dmg" value="10d8:cold"' in page
        rolled = await ac.post(
            f"{url}/c/{dragon.id}/save_effect",
            data={
                "name": "Cold Breath",
                "dc": "15",
                "ability": "con",
                "dmg": "10d8:cold",
                "half": "1",
            },
            headers=HTMX,
        )
        assert "DC 15 Constitution save, 30 damage" in rolled.text
        assert f'name="v.{one.id}"' in rolled.text
        effect = _pending(registry, combat_id)
        await ac.post(
            f"{url}/save",
            data={"at": effect.at.isoformat(), f"v.{one.id}": "save", f"v.{two.id}": "roll"},
            headers=HTMX,
        )
        after = {c.id: c for c in registry.combats.get(combat_id).combatants}
        assert after[one.id].hp == max(0, one.hp - 15)  # saved: half of 30
        assert after[two.id].hp == 0  # rolled 3 + CON 0 = 3 < 15: the full 30
        assert after[dragon.id].hp == dragon.hp  # left out ("—")
        saves = [e for e in _fight(registry, combat_id) if e.kind is EntryKind.SAVE]
        assert len(saves) == 2  # one line per target, each referring to the breath
        assert {e.refers_to for e in saves} == {attack_entry_id(combat_id, effect.at)}
        again = await ac.post(
            f"{url}/save", data={"at": effect.at.isoformat(), f"v.{one.id}": "fail"}, headers=HTMX
        )
        assert "already resolved" in again.text

    async def test_evasion_rage_and_a_chosen_resistance(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        from app.models.pc import PlayerCharacter
        from app.services.play import Actor, PlayError

        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        store = registry.gamestate.characters

        def make(name: str, **data: object) -> str:
            pc = store.create(
                PlayerCharacter.model_validate(
                    {
                        "name": name,
                        "abilities": {"dex": 10, "con": 10},
                        "state": {"hp": 40},
                        "overrides": {"hp_max": 40},
                    }
                    | data
                )
            )
            registry.combats.add_character(combat_id, pc.id, "dm")
            return pc.id

        rogue = make("Nim", classes=[{"class": "Rogue|PHB", "level": 7}])
        brute = make("Grosh", classes=[{"class": "Barbarian|PHB", "level": 2}])
        drake = make(
            "Vex", species="Dragonborn|PHB", classes=[{"class": "Fighter|PHB", "level": 1}]
        )
        row = {c.character_id: c.id for c in registry.combats.get(combat_id).combatants}
        goblin = registry.combats.get(combat_id).combatants[0]

        # Rage from the tracker: resist slashing while on
        await ac.post(f"{url}/c/{row[brute]}/rage", headers=HTMX)
        assert store.get(brute).state.raging
        # a dragonborn picks fire; anything outside the ancestry list is refused
        pc = store.get(drake)
        sheet = registry.characters.sheet(pc)
        with pytest.raises(PlayError):
            registry.characters.play.act(
                drake, "resistance", {"damage": "psychic"}, sheet, Actor("p")
            )
        registry.characters.play.act(drake, "resistance", {"damage": "fire"}, sheet, Actor("p"))
        assert store.get(drake).resistances == ["fire"]

        registry.combats._dice = Dice(_Fixed(3))  # type: ignore[attr-defined]
        await ac.post(
            f"{url}/c/{goblin.id}/save_effect",
            data={"name": "Burst", "dc": "15", "ability": "dex", "dmg": "4d6:fire", "half": "1"},
            headers=HTMX,
        )
        effect = _pending(registry, combat_id)
        await ac.post(
            f"{url}/save",
            data={
                "at": effect.at.isoformat(),
                f"v.{row[rogue]}": "save",  # Evasion: nothing
                f"v.{row[brute]}": "fail",  # 12 fire, not resisted by Rage
                f"v.{row[drake]}": "fail",  # 12 fire, halved by the ancestry
            },
            headers=HTMX,
        )
        assert store.get(rogue).state.hp == 40
        assert store.get(brute).state.hp == 40 - 12
        assert store.get(drake).state.hp == 40 - 6

    async def test_a_failed_save_imposes_the_condition(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        registry.combats.add_monster(combat_id, "Skeleton|MM", 1, "dm")
        combatants = registry.combats.get(combat_id).combatants
        one, two = combatants[0], combatants[1]
        skeleton = next(c for c in combatants if c.ref == "Skeleton|MM")
        rolled = await ac.post(
            f"{url}/c/{one.id}/save_effect",
            data={
                "name": "Stench",
                "dc": "12",
                "ability": "con",
                "dmg": "",
                "condition": "poisoned",
                "rounds": "10",
            },
            headers=HTMX,
        )
        assert "DC 12 Constitution save · Poisoned for 10 rounds on a failure" in rolled.text
        effect = _pending(registry, combat_id)
        await ac.post(
            f"{url}/save",
            data={
                "at": effect.at.isoformat(),
                f"v.{two.id}": "fail",
                f"v.{skeleton.id}": "fail",
                f"v.{one.id}": "save",
            },
            headers=HTMX,
        )
        after = {c.id: c for c in registry.combats.get(combat_id).combatants}
        assert after[two.id].conditions == ["poisoned"]
        assert [(tm.condition, tm.rounds) for tm in after[two.id].timers] == [("poisoned", 10)]
        assert after[skeleton.id].conditions == []  # immune to poison, poisoned too
        assert after[one.id].conditions == []  # saved

    async def test_a_player_rolls_their_own_save(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        from app.models.pc import PlayerCharacter

        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        pc = registry.gamestate.characters.create(
            PlayerCharacter.model_validate(
                {
                    "name": "Lyra",
                    "player": "alice",
                    "abilities": {"dex": 14},
                    "state": {"hp": 30},
                    "overrides": {"hp_max": 30},
                }
            )
        )
        registry.combats.add_character(combat_id, pc.id, "dm")
        goblin = registry.combats.get(combat_id).combatants[0]
        row = next(c for c in registry.combats.get(combat_id).combatants if c.character_id)
        await ac.post(
            f"{url}/c/{goblin.id}/save_effect",
            data={"name": "Burst", "dc": "30", "ability": "dex", "dmg": "2d6:fire", "half": "1"},
            headers=HTMX,
        )
        effect = _pending(registry, combat_id)

        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        strip = (await ac.get(f"/characters/{pc.id}/combat")).text
        assert "Roll your Dexterity save" in strip
        assert "DC" not in strip and ">30<" not in strip  # no DC for players (ids are random hex)
        rolled = await ac.post(
            f"/play/combat/{combat_id}/save",
            data={"at": effect.at.isoformat(), "character_id": pc.id, "back": "strip"},
            headers=HTMX,
        )
        assert "paravent-roll" in rolled.headers["HX-Trigger"]
        own = _about(registry, pc.id)[0]
        assert own.kind is EntryKind.SAVE and own.roll is not None
        total = own.roll.total
        assert _pending(registry, combat_id).rolls == {row.id: total}
        assert f"Dexterity save rolled: {total}" in rolled.text
        twice = await ac.post(
            f"/play/combat/{combat_id}/save",
            data={"at": effect.at.isoformat(), "character_id": pc.id, "back": "strip"},
            headers=HTMX,
        )
        assert "already rolled" in twice.text

        await ac.post("/logout")
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        board = (await ac.get(url)).text
        assert '<option value="fail" selected>' in board  # under DC 30: a failure
        await ac.post(
            f"{url}/save", data={"at": effect.at.isoformat(), f"v.{row.id}": "roll"}, headers=HTMX
        )
        line = next(e for e in _fight(registry, combat_id) if e.kind is EntryKind.SAVE)
        assert line.outcome == "failure"  # under DC 30: the player's roll, kept

    async def test_a_condition_shaken_off_by_a_repeated_save(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        one, two = registry.combats.get(combat_id).combatants[:2]
        await ac.post(f"{url}/begin", headers=HTMX)
        await ac.post(
            f"{url}/c/{one.id}/save_effect",
            data={
                "name": "Fear",
                "dc": "13",
                "ability": "wis",
                "dmg": "",
                "condition": "frightened",
                "repeat": "end",
            },
            headers=HTMX,
        )
        effect = _pending(registry, combat_id)
        board = await ac.post(
            f"{url}/save", data={"at": effect.at.isoformat(), f"v.{two.id}": "fail"}, headers=HTMX
        )
        assert "repeat the Wisdom save (DC 13) to end Frightened" in board.text

        registry.combats._dice = Dice(_Fixed(1))  # type: ignore[attr-defined]
        kept = await ac.post(
            f"{url}/c/{two.id}/repeat_save", data={"condition": "frightened"}, headers=HTMX
        )
        assert "Saves to repeat" not in kept.text
        assert _fight(registry, combat_id)[0].outcome == "failure"  # still Frightened
        assert registry.combats.get(combat_id).combatants[1].conditions == ["frightened"]
        again = await ac.post(
            f"{url}/c/{two.id}/repeat_save", data={"condition": "frightened"}, headers=HTMX
        )
        assert "No save to repeat now" in again.text  # once per turn

        await ac.post(f"{url}/next", headers=HTMX)  # one group: a new round, its turn again
        registry.combats._dice = Dice(_Fixed(20))  # type: ignore[attr-defined]
        await ac.post(
            f"{url}/c/{two.id}/repeat_save", data={"condition": "frightened"}, headers=HTMX
        )
        repeat = _fight(registry, combat_id)
        assert any(e.what == "repeat:wis:frightened" and e.outcome == "success" for e in repeat)
        row = registry.combats.get(combat_id).combatants[1]
        assert row.conditions == [] and row.repeat_saves == []

    async def test_a_player_repeats_their_own_save(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        from app.models.pc import PlayerCharacter

        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        pc = registry.gamestate.characters.create(
            PlayerCharacter.model_validate({"name": "Lyra", "player": "alice"})
        )
        registry.combats.add_character(combat_id, pc.id, "dm")
        row = next(c for c in registry.combats.get(combat_id).combatants if c.character_id)
        goblin = registry.combats.get(combat_id).combatants[0]
        registry.combats.set_initiative(combat_id, row.id, 30, "dm")  # Lyra acts first
        await ac.post(f"{url}/begin", headers=HTMX)
        await ac.post(
            f"{url}/c/{goblin.id}/save_effect",
            data={
                "name": "Fear",
                "dc": "1",
                "ability": "wis",
                "dmg": "",
                "condition": "frightened",
                "repeat": "end",
            },
            headers=HTMX,
        )
        effect = _pending(registry, combat_id)
        await ac.post(
            f"{url}/save", data={"at": effect.at.isoformat(), f"v.{row.id}": "fail"}, headers=HTMX
        )
        assert registry.gamestate.characters.get(pc.id).state.conditions == ["frightened"]

        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        strip = (await ac.get(f"/characters/{pc.id}/combat")).text
        assert "Roll your Wisdom save to end Frightened" in strip
        await ac.post(
            f"/play/combat/{combat_id}/repeat",
            data={"character_id": pc.id, "condition": "frightened", "back": "strip"},
            headers=HTMX,
        )
        assert registry.gamestate.characters.get(pc.id).state.conditions == []  # DC 1: always
        assert _about(registry, pc.id)[0].kind is EntryKind.SAVE

    async def test_a_players_sheet_attack_waits_in_the_fight(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        from app.models.pc import PlayerCharacter

        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        pc = registry.gamestate.characters.create(
            PlayerCharacter.model_validate(
                {
                    "name": "Brunhild",
                    "player": "alice",
                    "abilities": {"str": 16},
                    "classes": [{"class": "Fighter|PHB", "level": 1}],
                    "inventory": [{"item": "Longsword|PHB", "equipped": True}],
                }
            )
        )
        registry.combats.add_character(combat_id, pc.id, "dm")
        row = next(c for c in registry.combats.get(combat_id).combatants if c.character_id)
        registry.characters.play._dice = Dice(_Fixed(20))  # type: ignore[attr-defined]
        registry.combats._dice = Dice(_Fixed(4))  # type: ignore[attr-defined]

        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        rolled = await ac.post(
            f"/characters/{pc.id}/act/roll",
            data={"kind": "attack", "subject": "Longsword"},
            headers=HTMX,
        )
        assert json.loads(rolled.headers["HX-Trigger"])["paravent-roll"]["outcome"] == "crit"
        log = _about(registry, pc.id)
        assert [e.kind for e in log[:2]] == [EntryKind.DAMAGE, EntryKind.ATTACK]
        assert log[0].roll is not None and log[0].roll.total == 4 + 4 + 3  # 2d8 + STR 3
        # both in the fight: the sheet's roll once, not again from the tracker
        assert {e.scope.combat for e in log[:2]} == {combat_id}
        assert [e.kind for e in _fight(registry, combat_id)].count(EntryKind.ATTACK) == 1
        entry = _pending(registry, combat_id)
        assert (entry.kind, entry.actor, entry.outcome, entry.damage) == (
            "attack",
            row.id,
            "crit",
            11,
        )
        assert entry.damage_types == {"slashing": 11}

        await ac.post("/logout")
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        board = (await ac.get(url)).text
        assert "Attacks to resolve" in board
        goblin = registry.combats.get(combat_id).combatants[0]
        hp = goblin.hp
        await ac.post(
            f"{url}/attack", data={"at": entry.at.isoformat(), "target": goblin.id}, headers=HTMX
        )
        assert registry.combats.get(combat_id).combatants[0].hp == max(0, hp - 11)
        verdict = _fight(registry, combat_id)[0]
        assert verdict.refers_to == log[1].id  # the verdict refers to the sheet's entry

    async def test_a_players_target_comes_first_for_the_gamemaster(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        from app.models.pc import PlayerCharacter

        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        pc = registry.gamestate.characters.create(
            PlayerCharacter.model_validate(
                {
                    "name": "Brunhild",
                    "player": "alice",
                    "abilities": {"str": 16},
                    "classes": [{"class": "Fighter|PHB", "level": 1}],
                    "inventory": [{"item": "Longsword|PHB", "equipped": True}],
                }
            )
        )
        registry.combats.add_character(combat_id, pc.id, "dm")
        second = registry.combats.get(combat_id).combatants[1]  # Goblin 2, not first
        board_url = f"/play/combat/{combat_id}"

        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        board = (await ac.get(board_url)).text
        assert f'data-member="{second.id}"' in board and "Details" in board
        # the menu aims by the character's name; Details is the "i" alone
        assert 'title="Target"' in board and "<span>Brunhild</span>" in board
        assert 'aria-label="Details"' in board and ">Details<" not in board
        aimed = await ac.post(
            f"{board_url}/target",
            data={"character_id": pc.id, "target": second.id},
            headers=HTMX,
        )
        assert "Your target" in aimed.text and 'title="No longer the target"' in aimed.text
        assert "target of" not in aimed.text.lower()
        strip = (await ac.get(f"/characters/{pc.id}/combat")).text
        assert f"Target: {second.label}" in strip
        # a player can't aim another player's character, nor a hidden creature
        refused = await ac.post(
            f"{board_url}/target", data={"character_id": "nobody", "target": second.id}
        )
        assert refused.status_code == 404
        # Not a natural 1, which rolls no damage and leaves nothing to resolve.
        registry.characters.play._dice = Dice(_Fixed(10))  # type: ignore[attr-defined]
        await ac.post(
            f"/characters/{pc.id}/act/roll",
            data={"kind": "attack", "subject": "Longsword"},
            headers=HTMX,
        )

        await ac.post("/logout")
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        tracker_page = (await ac.get(url)).text
        assert f"Target: {second.label}" in tracker_page  # on the character's row
        form = tracker_page.split('name="target"', 1)[1]
        assert form.split("<option", 2)[1].startswith(f' value="{second.id}"')

        registry.combats.hide(combat_id, second.id, True, "dm")
        assert pc.id not in registry.combats.player_view(registry.combats.get(combat_id)).targets
        with pytest.raises(CombatNotFound):  # a hidden creature can't be picked
            registry.combats.choose_target(combat_id, pc.id, second.id)
        registry.combats.choose_target(combat_id, pc.id, None)
        me = next(c for c in registry.combats.get(combat_id).combatants if c.character_id)
        assert me.target is None

    async def test_details_show_what_anyone_sees(
        self, tracker: tuple[AsyncClient, StoreRegistry, str], tmp_path: Path
    ) -> None:
        from app.models.pc import PlayerCharacter

        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        pc = registry.gamestate.characters.create(
            PlayerCharacter.model_validate({"name": "Lyra", "player": "alice"})
        )
        registry.combats.add_character(combat_id, pc.id, "dm")
        goblin, other = registry.combats.get(combat_id).combatants[:2]
        registry.combats.hide(combat_id, other.id, True, "dm")
        images = tmp_path / "img"
        (images / "bestiary" / "MM").mkdir(parents=True)
        (images / "bestiary" / "MM" / "Goblin.webp").write_bytes(b"RIFF....WEBP")
        registry.settings.image_dir = images

        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        base = f"/play/combat/{combat_id}"
        board = (await ac.get(base)).text
        assert f'hx-get="{base}/c/{goblin.id}/details"' in board
        shown = (await ac.get(f"{base}/c/{goblin.id}/details")).text
        assert "Small humanoid (goblinoid)" in shown
        assert "Leather Armor, Shield, Scimitar, Shortbow" in shown
        assert "Goblins are small, black-hearted" in shown
        assert 'src="/play/img/bestiary/MM/Goblin.webp"' in shown
        assert "15" not in shown and "Nimble Escape" not in shown  # no AC, no traits
        picture = await ac.get("/play/img/bestiary/MM/Goblin.webp")
        assert picture.status_code == 200 and picture.content.startswith(b"RIFF")
        assert (await ac.get("/play/img/../secret")).status_code == 404
        assert (await ac.get(f"{base}/c/{other.id}/details")).status_code == 404  # hidden
        lyra = next(c for c in registry.combats.get(combat_id).combatants if c.character_id)
        mine = (await ac.get(f"{base}/c/{lyra.id}/details")).text
        # a character has no Details: its row says it all; side, state and conditions
        # are on the row, not repeated
        assert "Nothing more is known." in mine and "Gear" not in mine
        assert "Foe" not in shown and "Unhurt" not in shown
        board = (await ac.get(base)).text
        # the player's own row has nothing to do: no menu at all
        assert f'data-member="{lyra.id}"' not in board and "Lyra" in board

    async def test_outside_a_fight_the_sheet_is_unchanged(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        from app.models.pc import PlayerCharacter

        ac, registry, _url = tracker
        pc = registry.gamestate.characters.create(
            PlayerCharacter.model_validate(
                {"name": "Solo", "inventory": [{"item": "Dagger|PHB", "equipped": True}]}
            )
        )
        await ac.post(
            f"/characters/{pc.id}/act/roll",
            data={"kind": "attack", "subject": "Dagger"},
            headers=HTMX,
        )
        assert [e.kind for e in _about(registry, pc.id)] == [EntryKind.ATTACK]

    async def test_a_players_save_spell_joins_the_fight(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        from app.models.pc import PlayerCharacter

        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        pc = registry.gamestate.characters.create(
            PlayerCharacter.model_validate(
                {
                    "name": "Lyra",
                    "player": "alice",
                    "abilities": {"int": 16},
                    "classes": [{"class": "Wizard|PHB", "level": 5}],
                    "spells": {
                        "known": ["Fireball|PHB", "Hold Person|PHB"],
                        "prepared": ["Fireball|PHB", "Hold Person|PHB"],
                    },
                }
            )
        )
        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        assert "Call for saves" not in (await ac.get(f"/characters/{pc.id}")).text  # no fight

        registry.combats.add_character(combat_id, pc.id, "dm")
        row = next(c for c in registry.combats.get(combat_id).combatants if c.character_id)
        sheet = (await ac.get(f"/characters/{pc.id}")).text
        assert "Call for saves" in sheet
        sent = await ac.post(
            f"/characters/{pc.id}/fight-spell", data={"spell": "Fireball|PHB"}, headers=HTMX
        )
        assert "Sent to the fight" in sent.text and "paravent-roll" in sent.headers["HX-Trigger"]
        fire = _pending(registry, combat_id)
        assert (fire.kind, fire.actor, fire.amount, fire.save_ability, fire.half) == (
            "save_effect",
            row.id,
            14,
            "dex",
            True,
        )
        assert list(fire.damage_types) == ["fire"] and fire.detail == "Fireball"

        await ac.post(
            f"/characters/{pc.id}/fight-spell", data={"spell": "Hold Person|PHB"}, headers=HTMX
        )
        hold = _pending(registry, combat_id)
        assert (hold.save_ability, hold.condition, hold.repeat, hold.damage) == (
            "wis",
            "paralyzed",
            "end",
            0,
        )

    async def test_an_upcast_fireball(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        from app.models.pc import PlayerCharacter

        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        pc = registry.gamestate.characters.create(
            PlayerCharacter.model_validate(
                {
                    "name": "Lyra",
                    "abilities": {"int": 16},
                    "classes": [{"class": "Wizard|PHB", "level": 9}],
                    "spells": {"known": ["Fireball|PHB"], "prepared": ["Fireball|PHB"]},
                }
            )
        )
        rolled = await ac.post(
            f"/characters/{pc.id}/act/roll",
            data={"kind": "damage", "subject": "Fireball|PHB", "level": "5"},
            headers=HTMX,
        )
        assert rolled.status_code == 200
        damage = _about(registry, pc.id)[0].roll
        assert damage is not None and damage.expression.startswith("10d6")
        registry.combats.add_character(combat_id, pc.id, "dm")
        sheet = (await ac.get(f"/characters/{pc.id}")).text
        assert 'name="level"' in sheet and "/fight-spell" in sheet
        await ac.post(
            f"/characters/{pc.id}/fight-spell",
            data={"spell": "Fireball|PHB", "level": "5"},
            headers=HTMX,
        )
        assert registry.gamestate.characters.get(pc.id).state.slots_used == {"5": 1}
        effect = _pending(registry, combat_id)
        assert effect.kind == "save_effect" and effect.dice.startswith("10d6")

    async def test_weapon_mastery_after_a_hit(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        from app.models.pc import PlayerCharacter

        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]

        def fighter(name: str, weapon: str, book: str = "XPHB") -> str:
            pc = registry.gamestate.characters.create(
                PlayerCharacter.model_validate(
                    {
                        "name": name,
                        "abilities": {"str": 16},
                        "classes": [{"class": f"Fighter|{book}", "level": 1}],
                        "inventory": [{"item": weapon, "equipped": True}],
                    }
                )
            )
            return pc.id

        bruno = fighter("Bruno", "Quarterstaff|XPHB")
        old = fighter("Oldtimer", "Longsword|PHB", "PHB")
        sap = fighter("Sappy", "Longsword|XPHB")
        sheet = (await ac.get(f"/characters/{sap}")).text
        assert "/ref/itemMastery/XPHB/Sap" in sheet  # a 2024 fighter has Weapon Mastery
        assert "itemMastery" not in (await ac.get(f"/characters/{old}")).text
        assert (await ac.get("/ref/itemMastery/XPHB/Topple")).status_code == 200

        registry.combats.add_character(combat_id, bruno, "dm")
        registry.combats.add_character(combat_id, sap, "dm")
        goblin = registry.combats.get(combat_id).combatants[0]
        registry.characters.play._dice = Dice(_Fixed(10))  # type: ignore[attr-defined]
        registry.combats._dice = Dice(_Fixed(1))  # type: ignore[attr-defined]
        await ac.post(
            f"/characters/{bruno}/act/roll",
            data={"kind": "attack", "subject": "Quarterstaff"},
            headers=HTMX,
        )
        hit = _pending(registry, combat_id)
        assert hit.mastery == "Topple:13"  # 8 + STR 3 + proficiency 2
        await ac.post(
            f"{url}/attack", data={"at": hit.at.isoformat(), "target": goblin.id}, headers=HTMX
        )
        row = next(c for c in registry.combats.get(combat_id).combatants if c.id == goblin.id)
        assert "prone" in row.conditions  # rolled a 1 on its Constitution save

        await ac.post(
            f"/characters/{sap}/act/roll",
            data={"kind": "attack", "subject": "Longsword"},
            headers=HTMX,
        )
        second = _pending(registry, combat_id)
        board = await ac.post(
            f"{url}/attack", data={"at": second.at.isoformat(), "target": goblin.id}, headers=HTMX
        )
        assert board.status_code == 200
        fold = await ac.get(
            "/log/panel", params={"filter": "fight", "combat": combat_id, "embed": "1"}
        )
        assert "Disadvantage on its next attack roll" in fold.text

    async def test_healing_an_ally_and_drinking_a_potion(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        from app.models.pc import PlayerCharacter

        ac, registry, _url = tracker
        world = registry.gamestate.worlds.create(
            World(name="Aldmere", source_set=SourceSet(sources=["PHB", "DMG", "MM"]))
        ).id
        store = registry.gamestate.characters
        cleric = store.create(
            PlayerCharacter.model_validate(
                {
                    "name": "Mira",
                    "world_id": world,
                    "abilities": {"wis": 16},
                    "classes": [{"class": "Cleric|PHB", "level": 3}],
                    "spells": {"prepared": ["Cure Wounds|PHB"]},
                    "inventory": [{"item": "Potion of Healing|DMG", "quantity": 2}],
                    "state": {"hp": 3},
                }
            )
        )
        ally = store.create(
            PlayerCharacter.model_validate(
                {
                    "name": "Bruno",
                    "world_id": world,
                    "state": {"hp": 2},
                    "overrides": {"hp_max": 30},
                }
            )
        )
        stranger = store.create(PlayerCharacter.model_validate({"name": "Far", "state": {"hp": 1}}))
        draft = store.create(
            PlayerCharacter.model_validate(
                {"name": "", "world_id": world, "status": "draft", "wizard_step": "class"}
            )
        )
        registry.characters.play._dice = Dice(_Fixed(5))  # type: ignore[attr-defined]

        sheet = (await ac.get(f"/characters/{cleric.id}")).text
        assert f'<option value="{ally.id}">Bruno</option>' in sheet and stranger.id not in sheet
        assert draft.id not in sheet  # still in the wizard: no one to heal yet
        healed = await ac.post(
            f"/characters/{cleric.id}/heal",
            data={"spell": "Cure Wounds|PHB", "level": "2", "target": ally.id},
            headers=HTMX,
        )
        assert "Bruno regains 13 hit points" in healed.text  # 2d8 (5 + 5) + WIS 3
        assert store.get(ally.id).state.hp == 2 + 13
        assert store.get(cleric.id).state.slots_used == {"2": 1}
        assert _about(registry, cleric.id)[0].kind is EntryKind.HEAL
        refused = await ac.post(
            f"/characters/{cleric.id}/heal",
            data={"spell": "Cure Wounds|PHB", "level": "1", "target": stranger.id},
            headers=HTMX,
        )
        assert store.get(stranger.id).state.hp == 1 and "cs-flash" in refused.text

        mira = store.get(cleric.id)
        store.update(
            cleric.id,
            classes=[{"class": "Cleric|PHB", "level": 9}],
            spells={"prepared": ["Cure Wounds|PHB", "Mass Cure Wounds|PHB"]},
            state=mira.state.model_copy(update={"slots_used": {}}),
        )
        before = store.get(ally.id).state.hp or 0
        mass = await ac.post(
            f"/characters/{cleric.id}/heal",
            data={"spell": "Mass Cure Wounds|PHB", "level": "5", "target": [cleric.id, ally.id]},
            headers=HTMX,
        )
        assert "Mira, Bruno each regain" in mass.text
        assert (store.get(ally.id).state.hp or 0) > before
        too_many = await ac.post(
            f"/characters/{cleric.id}/heal",
            data={"spell": "Cure Wounds|PHB", "level": "1", "target": [cleric.id, ally.id]},
            headers=HTMX,
        )
        assert "cs-flash" in too_many.text  # one creature only
        store.update(cleric.id, state=store.get(cleric.id).state.model_copy(update={"hp": 3}))

        await ac.post(f"/characters/{cleric.id}/act/drink", data={"index": "0"}, headers=HTMX)
        after = store.get(cleric.id)
        assert (
            after.inventory[0].quantity == 1 and after.state.hp == 3 + 4 + 4 + 2
        )  # 2d4+2, d4s at 4


@requires_corpus
class TestRecap:
    async def test_a_fight_and_a_session_recapped(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        one, two = registry.combats.get(combat_id).combatants[:2]
        registry.combats._dice = Dice(_Fixed(20))  # type: ignore[attr-defined]
        await ac.post(
            f"{url}/c/{one.id}/attack",
            data={"name": "Scimitar", "hit": "4", "dmg": "1d6+2:slashing"},
            headers=HTMX,
        )
        hit = _pending(registry, combat_id)
        await ac.post(
            f"{url}/attack", data={"at": hit.at.isoformat(), "target": two.id}, headers=HTMX
        )
        await ac.post(
            f"{url}/c/{one.id}/save_effect",
            data={
                "name": "Fear",
                "dc": "30",
                "ability": "wis",
                "dmg": "",
                "condition": "frightened",
            },
            headers=HTMX,
        )
        fear = _pending(registry, combat_id)
        await ac.post(
            f"{url}/save", data={"at": fear.at.isoformat(), f"v.{two.id}": "fail"}, headers=HTMX
        )

        page = (await ac.get(f"{url}/recap")).text
        assert "Biggest hit: Goblin 1 with Scimitar on Goblin 2, 14 damage" in page  # crit: 2d6+2
        assert "Natural 20s: Goblin 1 (1)" in page
        assert "Goblin 2: Frightened (Fear)" in page
        assert "The whole session" not in page  # only one fight today

        encounter = registry.gamestate.encounters.list()[0]
        registry.combats.end(combat_id, "dm")
        await ac.post(f"/gm/encounters/{encounter.id}/run")  # a second fight the same day
        again = (await ac.get(f"{url}/recap")).text
        assert "The whole session" in again
        session = again.split('href="/gm/recap?')[1].split('"')[0].replace("&amp;", "&")
        whole = (await ac.get(f"/gm/recap?{session}")).text
        assert whole.count('href="/gm/combat/') >= 2 and "Session of" in whole
        assert (
            await ac.get("/gm/recap", params={"world": "nowhere", "day": "2000-01-01"})
        ).status_code == 404

        await ac.post("/language", data={"lang": "de", "next": "/"})
        german = (await ac.get(f"{url}/recap")).text
        assert "Größter Treffer: Goblin 1 mit Scimitar gegen Goblin 2, 14 Schaden" in german

    async def test_a_hit_made_critical_and_the_targets_condition_shown(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        one, two = registry.combats.get(combat_id).combatants[:2]
        registry.combats.toggle_condition(combat_id, two.id, "paralyzed", "dm")
        registry.combats._dice = Dice(_Fixed(3))  # type: ignore[attr-defined]
        rolled = await ac.post(
            f"{url}/c/{one.id}/attack",
            data={"name": "Scimitar", "hit": "4", "dmg": "1d6+2:slashing"},
            headers=HTMX,
        )
        assert "Goblin 2 · Paralyzed (advantage; a critical within 5 ft.)" in rolled.text
        assert 'name="crit"' in rolled.text
        entry = _pending(registry, combat_id)
        assert entry.damage == 5 and entry.damage_dice == "1d6+2:slashing"
        hp = next(c for c in registry.combats.get(combat_id).combatants if c.id == two.id).hp
        await ac.post(
            f"{url}/attack",
            data={"at": entry.at.isoformat(), "target": two.id, "crit": "1"},
            headers=HTMX,
        )
        after = next(c for c in registry.combats.get(combat_id).combatants if c.id == two.id)
        assert after.hp == max(0, hp - 8)  # 5 rolled + 1d6 more (a 3), the +2 once
        verdict = _fight(registry, combat_id)[0]  # the critical's extra dice in what landed
        assert (verdict.kind, verdict.outcome, verdict.amount) == (EntryKind.ATTACK, "hit", 8)


class TestWhatThePartyLearns:
    """Details fills in from the log: what a creature used, what damage did to it."""

    async def test_attacks_hits_and_saves_teach_the_party(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        from app.models.pc import PlayerCharacter

        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        registry.combats.add_monster(combat_id, "Skeleton|MM", 1, "dm")
        combatants = registry.combats.get(combat_id).combatants
        goblin = combatants[0]
        skeleton = next(c for c in combatants if c.ref == "Skeleton|MM")
        registry.combats._dice = Dice(_Fixed(3))  # type: ignore[attr-defined]

        async def hit(name: str, dmg: str) -> None:
            await ac.post(
                f"{url}/c/{goblin.id}/attack",
                data={"name": name, "hit": "4", "dmg": dmg},
                headers=HTMX,
            )
            at = _pending(registry, combat_id).at.isoformat()
            await ac.post(f"{url}/attack", data={"at": at, "target": skeleton.id}, headers=HTMX)

        await hit("Scimitar", "1d6+2:slashing")
        await hit("Club", "1d4:bludgeoning")
        await ac.post(
            f"{url}/c/{goblin.id}/save_effect",
            data={"name": "Poison Spray", "dc": "12", "ability": "con", "dmg": "2d6:poison"},
            headers=HTMX,
        )
        at = _pending(registry, combat_id).at.isoformat()
        await ac.post(f"{url}/save", data={"at": at, f"v.{skeleton.id}": "fail"}, headers=HTMX)

        world = registry.combats.get(combat_id).world_id
        goblins = registry.knowledge.of(world, "Goblin|MM")
        assert goblins["action:Scimitar"].types == ["slashing"]
        assert goblins["action:Poison Spray"].save == "con"
        skeletons = registry.knowledge.of(world, "Skeleton|MM")
        assert {k: f.effect for k, f in skeletons.items()} == {
            "damage:slashing": "normal",
            "damage:bludgeoning": "vulnerable",  # a skeleton's statblock
            "damage:poison": "immune",
        }
        assert "action:Scimitar" not in skeletons  # the goblin used it, not the skeleton

        # a hidden creature teaches nothing
        registry.combats.hide(combat_id, skeleton.id, True, "dm")
        await hit("Torch", "1d4:fire")
        assert "damage:fire" not in registry.knowledge.of(world, "Skeleton|MM")
        registry.combats.hide(combat_id, skeleton.id, False, "dm")

        # the player reads it in Details — in this fight and the next one in the world
        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        pc = registry.gamestate.characters.create(
            PlayerCharacter.model_validate({"name": "Lyra", "player": "alice"})
        )
        registry.combats.add_character(combat_id, pc.id, "dm")
        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        shown = (await ac.get(f"/play/combat/{combat_id}/c/{skeleton.id}/details")).text
        assert "What you know" in shown
        assert "double damage" in shown and "unharmed" in shown
        shown = (await ac.get(f"/play/combat/{combat_id}/c/{goblin.id}/details")).text
        assert "Scimitar" in shown and "Constitution save" in shown
        assert "DC" not in shown and "12" not in shown  # what it called for, not how hard

        later = registry.combats.start(registry.gamestate.encounters.list()[0], "dm")
        if not any(c.character_id == pc.id for c in later.combatants):
            registry.combats.add_character(later.id, pc.id, "dm")
        fresh = next(c for c in later.combatants if c.ref == "Goblin|MM")
        shown = (await ac.get(f"/play/combat/{later.id}/c/{fresh.id}/details")).text
        assert "Scimitar" in shown  # learned of goblins, not of that goblin

    async def test_the_gamemaster_tells_and_the_party_forgets(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        from app.models.pc import PlayerCharacter

        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        registry.combats.add_monster(combat_id, "Skeleton|MM", 1, "dm")
        skeleton = next(
            c for c in registry.combats.get(combat_id).combatants if c.ref == "Skeleton|MM"
        )
        side = (await ac.get(url, params={"sel": skeleton.id})).text
        assert "What the players know" in side and "Nothing yet." in side
        assert 'value="damage:bludgeoning"' in side and 'value="immune:poisoned"' in side
        assert 'value="ac"' in side and 'value="cr"' in side

        known = f"{url}/known/{skeleton.id}"
        told = await ac.post(
            known,
            data={
                "do": "reveal",
                "fact": ["damage:bludgeoning", "ac", "action:Shortsword"],
                "note": "Raised by the necromancer in the crypt.",
            },
            headers=HTMX,
        )
        assert told.text.startswith('<div id="combat-side"')
        world = registry.combats.get(combat_id).world_id
        facts = registry.knowledge.of(world, "Skeleton|MM")
        assert {k: f.how for k, f in facts.items()} == {
            "damage:bludgeoning": "revealed",
            "ac": "revealed",
            "action:Shortsword": "revealed",
            "note": "revealed",
        }
        assert facts["ac"].text == "medium"  # AC 13: a band, never the number

        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        pc = registry.gamestate.characters.create(
            PlayerCharacter.model_validate({"name": "Lyra", "player": "alice"})
        )
        registry.combats.add_character(combat_id, pc.id, "dm")
        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        shown = (await ac.get(f"/play/combat/{combat_id}/c/{skeleton.id}/details")).text
        assert "Bludgeoning" in shown and "double damage" in shown
        assert "average to hit" in shown and "13" not in shown
        assert "Shortsword" in shown and "Raised by the necromancer" in shown
        assert "Poisoned" not in shown  # not told
        assert (await ac.post(known, data={"do": "forget", "fact": "ac"})).status_code == 403

        await ac.post("/logout")
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        await ac.post(known, data={"do": "forget", "fact": "ac"}, headers=HTMX)
        await ac.post(known, data={"do": "reveal", "note": ""}, headers=HTMX)
        assert set(registry.knowledge.of(world, "Skeleton|MM")) == {
            "damage:bludgeoning",
            "action:Shortsword",
        }


class TestRevealable:
    def test_a_statblock_offers_its_facts_as_bands_and_names(self) -> None:
        from app.services.knowledge import action_name, revealable

        offer = {
            r.id: r
            for r in revealable(
                {
                    "ac": [{"ac": 19, "from": ["natural armor"]}],
                    "cr": "17",
                    "trait": [{"name": "Legendary Resistance (3/Day)", "entries": ["It can."]}],
                    "action": [{"name": "Fire Breath {@recharge 5}", "entries": ["Hot."]}],
                    "immune": ["fire"],
                    "conditionImmune": ["frightened"],
                }
            )
        }
        assert set(offer) == {
            "action:Fire Breath",
            "trait:Legendary Resistance (3/Day)",
            "damage:fire",
            "immune:frightened",
            "ac",
            "cr",
        }
        assert offer["ac"].fact.text == "very_high" and offer["cr"].fact.text == "legendary"
        assert offer["damage:fire"].fact.effect == "immune"
        assert offer["trait:Legendary Resistance (3/Day)"].fact.text == "It can."
        assert action_name("Fire Breath {@recharge 5}") == "Fire Breath"

    async def test_spells_cast_and_conditions_shrugged_off_are_logged_and_learned(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        registry.combats.add_monster(combat_id, "Mage|MM", 1, "dm")
        registry.combats.add_monster(combat_id, "Skeleton|MM", 1, "dm")
        combatants = registry.combats.get(combat_id).combatants
        goblin = combatants[0]
        mage = next(c for c in combatants if c.ref == "Mage|MM")
        skeleton = next(c for c in combatants if c.ref == "Skeleton|MM")
        await ac.post(f"{url}/begin", headers=HTMX)
        world = registry.combats.get(combat_id).world_id

        side = (await ac.get(url, params={"sel": mage.id})).text
        assert f"/c/{mage.id}/cast" in side and 'name="spell" value="Fireball"' in side
        await ac.post(f"{url}/c/{mage.id}/cast", data={"spell": "Fireball"}, headers=HTMX)
        cast = _fight(registry, combat_id)[0]
        assert (cast.kind, cast.what) == (EntryKind.CAST, "cast:Fireball")
        assert "spell:Fireball" in registry.knowledge.of(world, "Mage|MM")
        refused = await ac.post(f"{url}/c/{mage.id}/cast", data={"spell": "Wish"}, headers=HTMX)
        assert "did not make sense" in refused.text  # not one of its spells
        assert 'value="spell:Counterspell"' in side  # also offered to reveal

        await ac.post(
            f"{url}/c/{goblin.id}/save_effect",
            data={"name": "Stench", "dc": "10", "ability": "con", "condition": "poisoned"},
            headers=HTMX,
        )
        at = _pending(registry, combat_id).at.isoformat()
        await ac.post(f"{url}/save", data={"at": at, f"v.{skeleton.id}": "fail"}, headers=HTMX)
        panel = await ac.get(
            "/log/panel", params={"filter": "fight", "combat": combat_id, "embed": "1"}
        )
        assert "Immune to Poisoned" in panel.text and "Cast Fireball" in panel.text
        log = _fight(registry, combat_id)  # newest first
        immune = next(i for i, e in enumerate(log) if e.kind is EntryKind.CONDITION)
        failed = next(i for i, e in enumerate(log) if e.what.startswith("save:"))
        assert log[immune].what == "immune:poisoned"
        assert immune < failed  # the failed save first, then that it did not take
        bones = next(c for c in registry.combats.get(combat_id).combatants if c.id == skeleton.id)
        assert "poisoned" not in bones.conditions
        assert registry.knowledge.of(world, "Skeleton|MM")["immune:poisoned"].how == "observed"


def _spell_button(page: str, spell: str) -> str:
    """The tracker's Cast button of one spell, as rendered."""
    start = page.index(f'name="spell" value="{spell}"')
    return page[page.rindex("<button", 0, start) : page.index("</button>", start)]


class TestMonsterSpells:
    """Cast does the spell: a save spell waits for targets, an attack spell for Hit."""

    async def test_save_and_attack_spells_are_rolled_from_the_statblock(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        world = registry.combats.get(combat_id).world_id
        assert world is not None
        registry.gamestate.worlds.update(world, source_set=SourceSet(sources=["MM", "PHB"]))
        registry.combats.add_monster(combat_id, "Mage|MM", 1, "dm")
        mage = next(c for c in registry.combats.get(combat_id).combatants if c.ref == "Mage|MM")
        await ac.post(f"{url}/begin", headers=HTMX)
        registry.combats._dice = Dice(_Fixed(3))  # type: ignore[attr-defined]

        await ac.post(f"{url}/c/{mage.id}/cast", data={"spell": "Fireball"}, headers=HTMX)
        effect = _pending(registry, combat_id)
        # its level-3 list, the block's DC: 8d6 fire, a Dexterity save for half
        assert (effect.kind, effect.detail, effect.amount) == ("save_effect", "Fireball", 14)
        assert (effect.save_ability, effect.half, effect.damage) == ("dex", True, 24)
        assert effect.damage_types == {"fire": 24}

        await ac.post(f"{url}/c/{mage.id}/cast", data={"spell": "Fire Bolt"}, headers=HTMX)
        bolt = _pending(registry, combat_id)
        # a cantrip at the mage's caster level 9: 2d10, to hit with the block's +6
        assert (bolt.kind, bolt.detail, bolt.damage) == ("attack", "Fire Bolt", 6)
        assert bolt.damage_types == {"fire": 6}

        waiting = len(registry.combats.get(combat_id).pending)
        await ac.post(f"{url}/c/{mage.id}/cast", data={"spell": "Shield"}, headers=HTMX)
        assert len(registry.combats.get(combat_id).pending) == waiting  # nothing to roll
        assert _fight(registry, combat_id)[0].what == "cast:Shield"

        known = registry.knowledge.of(world, "Mage|MM")
        assert known["spell:Fireball"].save == "dex" and known["spell:Fireball"].types == ["fire"]
        assert known["spell:Fire Bolt"].types == ["fire"]
        assert not any(k.startswith("action:") for k in known)  # learned as spells

    async def test_slots_are_spent_and_refused_when_gone(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        registry.combats.add_monster(combat_id, "Mage|MM", 1, "dm")
        mage = next(c for c in registry.combats.get(combat_id).combatants if c.ref == "Mage|MM")
        await ac.post(f"{url}/begin", headers=HTMX)

        page = (await ac.get(url, params={"sel": mage.id})).text
        assert "3 left" in _spell_button(page, "Fireball")
        assert "left" not in _spell_button(page, "Fire Bolt")  # a cantrip: no count
        # Fireball and Counterspell share the mage's three 3rd-level slots
        for spell in ("Fireball", "Counterspell", "Fireball"):
            await ac.post(f"{url}/c/{mage.id}/cast", data={"spell": spell}, headers=HTMX)
        assert registry.combats.get(combat_id).combatant(mage.id).spent == {"slot:3": 3}
        casts = len(_fight(registry, combat_id))
        refused = await ac.post(f"{url}/c/{mage.id}/cast", data={"spell": "Fly"}, headers=HTMX)
        assert "No uses of that spell are left." in refused.text
        assert len(_fight(registry, combat_id)) == casts  # nothing logged

        page = (await ac.get(url, params={"sel": mage.id})).text
        assert " disabled>" in _spell_button(page, "Fireball")
        assert "used up" in _spell_button(page, "Fireball")
        assert " disabled>" not in _spell_button(page, "Fire Bolt")
        assert "Restore spells" in page
        await ac.post(f"{url}/c/{mage.id}/restore_spells", headers=HTMX)
        assert registry.combats.get(combat_id).combatant(mage.id).spent == {}

    async def test_a_spell_outside_the_worlds_books_is_only_logged(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        registry.combats.add_monster(combat_id, "Mage|MM", 1, "dm")
        mage = next(c for c in registry.combats.get(combat_id).combatants if c.ref == "Mage|MM")
        await ac.post(f"{url}/begin", headers=HTMX)
        await ac.post(f"{url}/c/{mage.id}/cast", data={"spell": "Fireball"}, headers=HTMX)
        assert registry.combats.get(combat_id).pending == []  # the world has MM alone
        assert _fight(registry, combat_id)[0].what == "cast:Fireball"


class TestMonsterSpellcasting:
    def test_both_editions_name_slot_dc_and_bonus(self) -> None:
        from app.services.monster_attacks import monster_spells

        spells = monster_spells(
            {
                "spellcasting": [
                    {
                        "headerEntries": [
                            "It is a 9th-level spellcaster (spell save {@dc 14}, {@hit 6} to hit)."
                        ],
                        "spells": {
                            "0": {"spells": ["{@spell fire bolt}"]},
                            "3": {"slots": 3, "spells": ["{@spell fireball}"]},
                        },
                    },
                    {
                        "headerEntries": ["Using the same ability as Spellcasting."],
                        "daily": {
                            "2e": ["{@spell Fireball|XPHB} (level 4 version)", "{@spell Fly|XPHB}"]
                        },
                    },
                ]
            }
        )
        found = {(s.name, s.source): s for s in spells}
        assert set(found) == {("Fire Bolt", None), ("Fireball", None), ("Fly", "XPHB")}
        assert found[("Fireball", None)].slot == 3 and found[("Fire Bolt", None)].slot is None
        assert found[("Fly", "XPHB")].dc == 14  # the block before it states the DC
        assert found[("Fire Bolt", None)].caster_level == 9
        assert found[("Fire Bolt", None)].hit == 6

    def test_what_each_spell_spends(self) -> None:
        from app.services.monster_attacks import monster_spells

        spells = monster_spells(
            {
                "spellcasting": [
                    {
                        "will": ["{@spell mage hand}"],
                        "daily": {
                            "1e": ["{@spell fly}", "{@spell haste}"],
                            "3": ["{@spell shield}"],
                        },
                        "restLong": {"1": ["{@spell augury}"]},
                        "spells": {
                            "0": {"spells": ["{@spell fire bolt}"]},
                            "3": {
                                "slots": 3,
                                "spells": ["{@spell fireball}", "{@spell counterspell}"],
                            },
                        },
                    }
                ]
            }
        )
        pools = {s.name: (s.pool, s.uses) for s in spells}
        assert pools["Mage Hand"] == pools["Fire Bolt"] == (None, None)
        # "1e": one a day each; "3": three between the list's spells
        assert pools["Fly"] == ("0:daily:1e:Fly", 1) and pools["Haste"] == ("0:daily:1e:Haste", 1)
        assert pools["Shield"] == ("0:daily:3", 3)
        assert pools["Augury"] == ("0:restLong:1", 1)
        # a level's slots, shared by its spells
        assert pools["Fireball"] == pools["Counterspell"] == ("slot:3", 3)


class TestTranslateFromTheTracker:
    """A creature not yet in the gamemaster's language offers Translate in a popup."""

    async def test_name_and_passages_saved_from_the_popup(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        goblin = registry.combats.get(combat_id).combatants[0]
        english = (await ac.get(url, params={"sel": goblin.id})).text
        assert "data-translate=" not in english  # nothing to translate into

        dm = next(u for u in registry.gamestate.users.list() if u.username == "dm")
        registry.gamestate.users.update(dm.id, language="de")
        side = (await ac.get(f"{url}/side", params={"sel": goblin.id})).text
        assert "Übersetzen" in side and "Name fehlt" in side and "offen" in side
        assert 'data-url="' in side  # what the popup's save reloads
        popup_url = side.split('data-translate="', 1)[1].split('"', 1)[0].replace("&amp;", "&")
        assert "embed=1" in popup_url

        popup = (await ac.get(popup_url)).text
        assert popup.startswith('<div class="tr-popup">') and 'name="name"' in popup
        target = registry.translation_targets.entry("monster", "MM", "Goblin")
        rows = registry.translations.rows("de", "monster", target.key, target.data, target.fields)
        first = rows[0].path
        saved = await ac.post(
            popup_url,
            data={"lang": "de", "name": "Goblin (de)", f"p:{first}": "Ein Text.", "action": "save"},
            headers=HTMX,
        )
        assert json.loads(saved.headers["HX-Trigger"]) == {"paravent-translated": {"close": True}}
        assert registry.glossary.term("de", "monster", "Goblin") == "Goblin (de)"
        again = registry.translations.rows("de", "monster", target.key, target.data, target.fields)
        assert next(r for r in again if r.path == first).text == "Ein Text."

        side = (await ac.get(f"{url}/side", params={"sel": goblin.id})).text
        assert "Name fehlt" not in side  # named now; the other passages are still open
        if len(rows) == 1:
            assert "Übersetzen" not in side

        confirmed = await ac.post(
            popup_url, data={"lang": "de", "action": "confirm_all"}, headers=HTMX
        )
        # confirming is the review done: the popup closes, as after a save
        assert json.loads(confirmed.headers["HX-Trigger"])["paravent-translated"]["close"] is True


class TestTranslatedDescription:
    """The books' description players read in Details is translated from the tracker."""

    async def test_description_translated_in_a_popup_and_read_in_details(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        from app.models.pc import PlayerCharacter

        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        goblin = registry.combats.get(combat_id).combatants[0]
        dm = next(u for u in registry.gamestate.users.list() if u.username == "dm")
        registry.gamestate.users.update(dm.id, language="de")
        side = (await ac.get(f"{url}/side", params={"sel": goblin.id})).text
        assert "Beschreibung" in side and "Beschreibung: " in side  # passages open
        popup_url = next(
            part.split('"', 1)[0].replace("&amp;", "&")
            for part in side.split('data-translate="')[1:]
            if "/fluff/" in part.split('"', 1)[0]
        )
        popup = (await ac.get(popup_url)).text
        assert popup.startswith('<div class="tr-popup">') and 'name="name"' not in popup

        target = registry.translation_targets.fluff("monster", "MM", "Goblin")
        assert (target.category, target.key) == ("monsterFluff", "Goblin|MM")
        rows = registry.translations.rows(
            "de", target.category, target.key, target.data, ("entries",)
        )
        texts = {f"p:{r.path}": f"Übersetzt {i}." for i, r in enumerate(rows)}
        saved = await ac.post(
            popup_url, data={"lang": "de", "action": "save", **texts}, headers=HTMX
        )
        assert json.loads(saved.headers["HX-Trigger"]) == {"paravent-translated": {"close": True}}
        side = (await ac.get(f"{url}/side", params={"sel": goblin.id})).text
        assert "/fluff/" not in side  # nothing open in the description now

        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        pc = registry.gamestate.characters.create(
            PlayerCharacter.model_validate({"name": "Lyra", "player": "alice"})
        )
        registry.combats.add_character(combat_id, pc.id, "dm")
        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        details = f"/play/combat/{combat_id}/c/{goblin.id}/details"
        assert "Goblins are small, black-hearted" in (await ac.get(details)).text  # English reader
        alice = next(u for u in registry.gamestate.users.list() if u.username == "alice")
        registry.gamestate.users.update(alice.id, language="de")
        shown = (await ac.get(details)).text
        assert "Übersetzt" in shown and "black-hearted" not in shown

    def test_no_description_no_target(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        from app.services.translation_targets import TargetNotFound

        _, registry, _ = tracker
        with pytest.raises(TargetNotFound):
            registry.translation_targets.fluff("spell", "PHB", "Fireball")  # only creatures
        with pytest.raises(TargetNotFound):
            registry.translation_targets.fluff("monster", "MM", "No Such Thing")


class TestFactNames:
    """Action and trait names read in the reader's language where they can."""

    async def test_from_the_creatures_translation_or_the_weapon_glossary(
        self, tracker: tuple[AsyncClient, StoreRegistry, str]
    ) -> None:
        from app.services.translations import passages, translation_key

        ac, registry, url = tracker
        combat_id = url.rsplit("/", 1)[1]
        goblin = registry.combats.get(combat_id).combatants[0]
        dm = next(u for u in registry.gamestate.users.list() if u.username == "dm")
        registry.gamestate.users.update(dm.id, language="de")
        registry.glossary.set("de", "baseitem", "Scimitar", "Krummsäbel")
        side = (await ac.get(f"{url}/side", params={"sel": goblin.id})).text
        assert "Krummsäbel" in _offer(side)  # named after a weapon: the glossary's word

        data = registry.references.get("monster", "MM", "Goblin", None).data
        path = next(
            p for p, text in passages(data, ("trait",)) if p.endswith("/name") and "Nimble" in text
        )
        registry.translations.save(
            "de",
            "monster",
            translation_key("monster", data),
            data,
            {path: "Flinke Flucht"},
            by="dm",
            fields=("trait",),
        )
        side = (await ac.get(f"{url}/side", params={"sel": goblin.id})).text
        assert "Flinke Flucht" in _offer(side)  # from the creature's own translation


def _offer(side: str) -> str:
    """The side panel's list of facts the gamemaster can still tell."""
    start = side.index('class="cb-known-offer"')
    return side[start : side.index('class="cb-known-note"', start)]
