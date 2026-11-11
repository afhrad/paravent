"""Spells a feat, species or background grants (``additionalSpells``): read, asked in the
wizard and on level-up, kept in ``spells.granted``, cast on the sheet with their own
ability (app/services/granted_spells.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.models.gamestate import Role, SourceSet, World
from app.models.pc import PlayerCharacter
from app.services.auth import Identity
from app.services.chargen import Catalogue, Planner
from app.services.chargen.levelup import LevelUpPlanner
from app.services.granted_spells import blocks, chosen_block
from app.services.play import PlayError
from app.store.compendium import CompendiumStore
from app.store.lookups import LookupTables
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"
MI = "feat:Magic Initiate|XPHB"

MAGIC_INITIATE = {
    "additionalSpells": [
        {
            "name": "Cleric Spells",
            "ability": {"choose": ["int", "wis", "cha"]},
            "innate": {"_": {"daily": {"1": [{"choose": "level=1|class=Cleric"}]}}},
            "known": {"_": [{"choose": "level=0|class=Cleric", "count": 2}]},
        },
        {
            "name": "Wizard Spells",
            "ability": "int",
            "known": {"_": [{"choose": "level=0|class=Wizard|spell attack=m;r;o", "count": 2}]},
        },
    ]
}


class TestReader:
    def test_blocks_lists_and_choices(self) -> None:
        found = blocks(MAGIC_INITIATE)
        assert [b.name for b in found] == ["Cleric Spells", "Wizard Spells"]
        cleric = chosen_block(found, "cleric")  # a feat version names its list
        assert cleric is not None and cleric.ability_options == ("int", "wis", "cha")
        cantrips, daily = (line for _i, line in cleric.choices(1))
        assert cantrips.count == 2 and cantrips.choice is not None
        assert cantrips.choice.cantrips_only and cantrips.choice.classes == ("Cleric",)
        assert (daily.kind, daily.use, daily.times) == ("innate", "daily", 1)
        wizard = chosen_block(found, "Wizard Spells")
        assert wizard is not None and wizard.ability == "int"
        attack = wizard.lines[0].choice
        assert attack is not None and attack.attack == frozenset({"M", "R", "O"})
        assert attack.admits({"level": 0, "spellAttack": ["R"]}, lambda c: c == "Wizard")
        assert not attack.admits({"level": 0}, lambda c: c == "Wizard")
        assert chosen_block(found, None) is None

    def test_fixed_spells_by_level_and_how_often(self) -> None:
        tiefling = blocks(
            {
                "additionalSpells": [
                    {
                        "ability": "cha",
                        "known": {"1": ["thaumaturgy#c"]},
                        "innate": {
                            "3": {"daily": {"1": ["hellish rebuke"]}},
                            "5": {"daily": {"pb": ["darkness"]}, "will": ["light"]},
                        },
                    }
                ]
            }
        )[0]
        assert [x.spell for x in tiefling.fixed(1)] == ["thaumaturgy"]
        assert tiefling.fixed(1)[0].cantrip
        assert [(x.spell, x.times) for x in tiefling.fixed(5) if x.use] == [
            ("hellish rebuke", 1),
            ("darkness", -1),
            ("light", 0),
        ]

    def test_a_list_to_choose_from(self) -> None:
        found = blocks(
            {
                "additionalSpells": [
                    {"known": {"_": [{"choose": {"from": ["hex", "bane"], "count": 1}}]}}
                ]
            }
        )
        choice = found[0].lines[0].choice
        assert (
            choice is not None
            and choice.admits({"name": "Hex"})
            and not choice.admits({"name": "Bless"})
        )

    def test_the_corpus(self, corpus: Path) -> None:
        import json

        feats = json.loads((corpus / "feats.json").read_text(encoding="utf-8"))["feat"]
        races = json.loads((corpus / "races.json").read_text(encoding="utf-8"))
        records = [*feats, *races["race"], *races["subrace"]]
        assert sum(1 for r in records if blocks(r)) > 120


def _alice(registry: StoreRegistry) -> Identity:
    user = next(u for u in registry.gamestate.users.list() if u.username == "alice")
    return Identity(user=user, session=None)  # type: ignore[arg-type]


@pytest.fixture
def registry(tmp_path: Path, corpus: Path) -> StoreRegistry:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
    registry.gamestate.worlds.create(World(name="2024", source_set=SourceSet(sources=["XPHB"])))
    return registry


def _acolyte_fighter(registry: StoreRegistry) -> str:
    """A 2024 fighter whose Acolyte background grants Magic Initiate (Cleric)."""
    alice = _alice(registry)
    drafts = registry.drafts
    draft = drafts.start(alice, registry.gamestate.worlds.list()[0].id)
    drafts.save(alice, draft.id, "class", {"class": ["Fighter|XPHB"]})
    drafts.save(alice, draft.id, "background", {"background": ["Acolyte|XPHB"]})
    return draft.id


class TestWizard:
    def test_the_acolytes_magic_initiate_asks_its_spells(self, registry: StoreRegistry) -> None:
        draft = _acolyte_fighter(registry)
        alice = _alice(registry)
        plan = registry.drafts.plan(registry.characters.get_for(alice, draft))
        step = plan.step("spells")
        assert step is not None  # a fighter has a spells step now
        picks = {p.id: p for p in step.picks}
        assert picks[f"granted.{MI}.ability"].options == ("int", "wis", "cha")
        cantrips = picks[f"granted.{MI}.Cleric Spells.0"]
        assert cantrips.kind == "cantrip" and cantrips.count == 2
        assert "Guidance|XPHB" in cantrips.options and "Fire Bolt|XPHB" not in cantrips.options
        daily = picks[f"granted.{MI}.Cleric Spells.1"]
        assert daily.kind == "spell" and "Bless|XPHB" in daily.options
        assert not any(p.id.endswith(".list") for p in step.picks)  # the version fixed it

        pc, plan = registry.drafts.save(
            alice,
            draft,
            "spells",
            {
                f"granted.{MI}.ability": ["wis"],
                f"granted.{MI}.Cleric Spells.0": ["Guidance|XPHB", "Sacred Flame|XPHB"],
                f"granted.{MI}.Cleric Spells.1": ["Bless|XPHB"],
            },
        )
        assert plan.step("spells").state == "done"  # type: ignore[union-attr]
        granted = pc.spells.granted[0]
        assert granted.source == MI and granted.from_list == "Cleric Spells"
        assert granted.ability == "wis"
        assert [s.reference for s in granted.spells] == [
            "Guidance|XPHB",
            "Sacred Flame|XPHB",
            "Bless|XPHB",
        ]


class TestSheet:
    def _sheet_pc(self, registry: StoreRegistry) -> str:
        draft = _acolyte_fighter(registry)
        alice = _alice(registry)
        registry.drafts.save(
            alice,
            draft,
            "spells",
            {
                f"granted.{MI}.ability": ["wis"],
                f"granted.{MI}.Cleric Spells.0": ["Guidance|XPHB", "Sacred Flame|XPHB"],
                f"granted.{MI}.Cleric Spells.1": ["Bless|XPHB"],
            },
        )
        registry.gamestate.characters.update(
            draft,
            abilities={"str": 15, "dex": 12, "con": 14, "int": 8, "wis": 16, "cha": 10},
        )
        return draft

    def test_its_own_ability_and_one_free_casting(self, registry: StoreRegistry) -> None:
        cid = self._sheet_pc(registry)
        pc = registry.gamestate.characters.get(cid)
        sheet = registry.characters.sheet(pc)
        flame = sheet.spell("Sacred Flame|XPHB")
        assert flame is not None and flame.origin == "Magic Initiate" and flame.ability == "wis"
        dc = sheet.save_dc_for(flame)
        assert dc is not None and dc.value == 8 + 2 + 3  # proficiency 2, WIS 16
        assert sheet.spell_save_dc is None  # a fighter has no class spellcasting
        bless = sheet.spell("Bless|XPHB")
        assert bless is not None and bless.free == "Bless (Magic Initiate)"
        resource = next(r for r in sheet.resources if r.name == bless.free)
        assert (resource.max, resource.used) == (1, 0)

        actor = registry.characters
        alice = _alice(registry)
        actor.act(alice, cid, "cast", {"spell": "Bless|XPHB", "level": "free"})
        pc = registry.gamestate.characters.get(cid)
        assert pc.state.resources_used == {"Bless (Magic Initiate)": 1}
        with pytest.raises(PlayError):  # the free casting is spent; no slots either
            actor.act(alice, cid, "cast", {"spell": "Bless|XPHB", "level": "free"})
        actor.act(alice, cid, "rest", {"kind": "long"})
        assert registry.gamestate.characters.get(cid).state.resources_used == {}

    def test_a_tieflings_spells_come_by_level(
        self, loaded_compendium: CompendiumStore, corpus: Path
    ) -> None:
        from app.services.sheet import build_sheet

        pc = PlayerCharacter.model_validate(
            {
                "name": "Vex",
                "species": "Tiefling|PHB",
                "classes": [{"class": "Fighter|PHB", "level": 3}],
                "abilities": {"str": 15, "dex": 12, "con": 14, "int": 8, "wis": 10, "cha": 16},
            }
        )
        sheet = build_sheet(pc, loaded_compendium, source_set=SourceSet(sources=["PHB"]))
        names = {s.name: s for s in sheet.spells}
        assert "Thaumaturgy" in names and "Hellish Rebuke" in names and "Darkness" not in names
        rebuke = names["Hellish Rebuke"]
        assert rebuke.ability == "cha" and rebuke.free == "Hellish Rebuke (Tiefling)"
        dc = sheet.save_dc_for(rebuke)
        assert dc is not None and dc.value == 8 + 2 + 4  # CHA 16, +2 as a 2014 tiefling

    def test_a_worn_items_bonus_counts_for_granted_spells(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        from app.services.sheet import build_sheet

        def vex(*inventory: dict[str, object]) -> PlayerCharacter:
            return PlayerCharacter.model_validate(
                {
                    "name": "Vex",
                    "species": "Tiefling|PHB",
                    "classes": [{"class": "Fighter|PHB", "level": 3}],
                    "abilities": {"str": 15, "dex": 12, "con": 14, "int": 8, "wis": 10, "cha": 16},
                    "inventory": list(inventory),
                }
            )

        books = SourceSet(sources=["PHB", "DMG"])
        plain = build_sheet(vex(), loaded_compendium, source_set=books)
        wand = {"item": "+1 Wand of the War Mage|DMG", "equipped": True, "attuned": True}
        armed = build_sheet(vex(wand), loaded_compendium, source_set=books)
        rebuke = armed.spell("Hellish Rebuke|PHB") or armed.spell("Hellish Rebuke")
        assert rebuke is not None
        before = plain.attack_for(rebuke)
        after = armed.attack_for(rebuke)
        assert before is not None and after is not None
        assert after.value == (before.value or 0) + 1
        assert "Wand of the War Mage" in after.explanation


class TestLevelUp:
    def test_a_feat_taken_asks_its_spells(
        self, loaded_compendium: CompendiumStore, corpus: Path
    ) -> None:
        lookups = LookupTables()
        lookups.load(corpus)
        levels = LevelUpPlanner(
            Planner(Catalogue(loaded_compendium, SourceSet(sources=["PHB"]), lookups))
        )
        pc = PlayerCharacter.model_validate(
            {
                "status": "ready",
                "pending_levels": 1,
                "classes": [{"class": "Fighter|PHB", "level": 3, "subclass": "Champion|PHB"}],
                "abilities": {"str": 15, "dex": 14, "con": 14, "int": 8, "wis": 10, "cha": 10},
            }
        )
        source = "feat:Magic Initiate|PHB"
        levels.answer(pc, "asi", ["feat:Magic Initiate|PHB"])
        lists = next(p for p in levels.plan(pc).picks if p.id == f"granted.{source}.list")
        assert "Wizard Spells" in lists.details
        wizard = str(lists.details.index("Wizard Spells"))
        levels.answer(pc, f"granted.{source}.list", [wizard])
        picks = {p.id: p for p in levels.plan(pc).picks}
        assert f"granted.{source}.Wizard Spells.0" in picks
        levels.answer(pc, f"granted.{source}.Wizard Spells.0", ["Fire Bolt|PHB", "Light|PHB"])
        levels.answer(pc, f"granted.{source}.Wizard Spells.1", ["Shield|PHB"])
        levels.answer(pc, "hp", ["average"])
        levels.take(pc)
        granted = pc.spells.granted[0]
        assert granted.source == source and granted.from_list == "Wizard Spells"
        assert [s.name for s in granted.spells] == ["Fire Bolt", "Light", "Shield"]
