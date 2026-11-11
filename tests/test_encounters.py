"""Encounters: the difficulty tables, the arithmetic, the service and the builder."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import (
    EDITION_CLASSIC,
    EDITION_ONE,
    PartySpec,
    Role,
    Scope,
    SourceSet,
    World,
)
from app.models.pc import ClassLevel, PlayerCharacter
from app.services.difficulty import DifficultyRules, rules_for
from app.services.encounters import EncounterError, EncounterNotFound, EncounterService
from app.store import rulestables
from app.store.compendium import CompendiumStore
from app.store.gamestate import GameStateStore
from app.store.projections import XP_BY_CR
from app.store.registry import StoreRegistry
from tests.conftest import corpus_path

PASSWORD = "correct horse battery"


@pytest.fixture(scope="module")
def rules(corpus: Path) -> DifficultyRules:
    """The tables as a table with the books has them: the 2014 guide's from the corpus."""
    return DifficultyRules(reference=corpus)


def _tables(data: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in data.items() if key != "_meta"}


class TestRulesFile:
    """The tables are what the books say — re-read from the corpus each run. The file
    holds the ones the System Reference Documents have too; the 2014 guide's are the
    publisher's alone, so they are read from the corpus and never committed."""

    def test_the_tables_match_the_books(self, corpus: Path) -> None:
        loaded = rulestables.load(reference_dir=corpus)
        assert _tables(loaded) == _tables(rulestables.extract(corpus))

    def test_the_file_is_the_shipped_part_of_the_books(self, corpus: Path) -> None:
        committed = json.loads(rulestables.RULES_FILE.read_text(encoding="utf-8"))
        assert committed == rulestables.shipped(rulestables.extract(corpus))

    def test_the_file_carries_no_table_of_the_2014_guide(self) -> None:
        committed = json.loads(rulestables.RULES_FILE.read_text(encoding="utf-8"))
        assert set(_tables(committed)) == {"xp_by_cr", "advancement", "one"}
        assert {source["table"] for source in committed["_meta"]["sources"]} == {
            "Experience Points by Challenge Rating",
            "XP Budget per Character",
            "Character Advancement",
        }

    def test_the_xp_table_agrees_with_the_projections(self) -> None:
        assert rulestables.load()["xp_by_cr"] == XP_BY_CR

    def test_twenty_levels_each(self, corpus: Path) -> None:
        data = rulestables.load(reference_dir=corpus)
        assert set(data["classic"]["thresholds"]) == {str(n) for n in range(1, 21)}
        assert set(data["one"]["budgets"]) == {str(n) for n in range(1, 21)}

    def test_a_missing_book_is_reported(self, tmp_path: Path) -> None:
        with pytest.raises(rulestables.RulesTableMissing):
            rulestables.extract(tmp_path)


def _guide(root: Path, easy: int, *, without: str = "") -> Path:
    """A corpus holding only a 2014 guide with the three tables, level 1 alone."""
    tables = {
        "XP Thresholds by Character Level": [["1st", easy, easy * 2, easy * 3, easy * 4]],
        "Encounter Multipliers": [["1", "×1"], ["2", "×1.5"], ["3-6", "×2"], ["15 or more", "×4"]],
        "Adventuring Day XP": [["1st", "300"]],
    }
    entries = [
        {"type": "table", "caption": caption, "rows": rows}
        for caption, rows in tables.items()
        if caption != without
    ]
    book = root / rulestables.CLASSIC_BOOK
    book.parent.mkdir(parents=True, exist_ok=True)
    book.write_text(json.dumps({"data": [{"type": "section", "entries": entries}]}), "utf-8")
    return root


class TestTheGuideOfTheCorpus:
    """The 2014 thresholds, multipliers and adventuring day come from the user's own
    *Dungeon Master's Guide*; a corpus without it has no 2014 rating and nothing fails."""

    def test_read_from_the_corpus(self, tmp_path: Path) -> None:
        rules = DifficultyRules(reference=_guide(tmp_path, 25))
        rating = rules.rate(EDITION_CLASSIC, [1, 1, 1, 1], [(50, 4)])
        assert (rating.multiplier, rating.adjusted_xp, rating.level) == (2.0, 400, "deadly")
        assert [band.xp for band in rating.bands] == [100, 200, 300, 400]
        assert rules.day_budget(EDITION_CLASSIC, [1, 1]) == 600
        assert rules.has_tables(EDITION_CLASSIC)

    @pytest.mark.parametrize("reference", [None, "empty", "no-table", "broken"])
    def test_without_the_guide_there_is_no_2014_rating(
        self, tmp_path: Path, reference: str | None
    ) -> None:
        root: Path | None = None
        if reference == "empty":
            root = tmp_path
        elif reference == "no-table":
            root = _guide(tmp_path, 25, without="Encounter Multipliers")
        elif reference == "broken":
            root = _guide(tmp_path, 25)
            (root / rulestables.CLASSIC_BOOK).write_text("{not json", "utf-8")
        rules = DifficultyRules(reference=root)
        rating = rules.rate(EDITION_CLASSIC, [1, 1], [(50, 4)])
        assert rating.bands == () and rating.level is None and not rating.rated
        assert (rating.base_xp, rating.multiplier, rating.adjusted_xp) == (200, 1.0, 200)
        assert rules.day_budget(EDITION_CLASSIC, [1, 1]) is None
        assert not rules.has_tables(EDITION_CLASSIC)

    def test_the_2024_budgets_need_no_corpus(self) -> None:
        rules = DifficultyRules()
        rating = rules.rate(EDITION_ONE, [1, 1, 1, 1], [(50, 4)])
        assert [band.xp for band in rating.bands] == [200, 300, 400] and rating.level == "low"
        assert rules.has_tables(EDITION_ONE) and rules.xp_for_cr("1/4") == 50

    def test_a_changed_book_is_read_again(self, tmp_path: Path) -> None:
        rules = DifficultyRules(reference=_guide(tmp_path, 25))
        assert [band.xp for band in rules.bands(EDITION_CLASSIC, [1])] == [25, 50, 75, 100]
        book = _guide(tmp_path, 1000) / rulestables.CLASSIC_BOOK
        os.utime(book, ns=(1, 1))  # another time for sure, whatever the clock's grain
        assert [band.xp for band in rules.bands(EDITION_CLASSIC, [1])] == [1000, 2000, 3000, 4000]


class TestArithmetic:
    def test_the_dmg_example(self, rules: DifficultyRules) -> None:
        """Four goblins against four 1st-level characters: 200 XP ×2 = 400, Deadly."""
        rating = rules.rate(EDITION_CLASSIC, [1, 1, 1, 1], [(50, 4)])
        assert (rating.base_xp, rating.multiplier, rating.adjusted_xp) == (200, 2.0, 400)
        assert [(b.key, b.xp) for b in rating.bands] == [
            ("easy", 100),
            ("medium", 200),
            ("hard", 300),
            ("deadly", 400),
        ]
        assert rating.level == "deadly"

    def test_2024_has_no_multiplier(self, rules: DifficultyRules) -> None:
        rating = rules.rate(EDITION_ONE, [1, 1, 1, 1], [(50, 4)])
        assert (rating.multiplier, rating.adjusted_xp, rating.level) == (1.0, 200, "low")

    def test_beyond_the_2024_budget(self, rules: DifficultyRules) -> None:
        assert rules.rate(EDITION_ONE, [1], [(1800, 1)]).level == "beyond"

    def test_below_the_first_band(self, rules: DifficultyRules) -> None:
        assert rules.rate(EDITION_CLASSIC, [5, 5, 5, 5], [(10, 1)]).level == "trivial"

    @pytest.mark.parametrize(
        ("monsters", "party", "factor"),
        [
            (1, 4, 1.0),
            (2, 4, 1.5),
            (6, 4, 2.0),
            (7, 4, 2.5),
            (15, 4, 4.0),
            (1, 2, 1.5),  # a small party: one step up
            (1, 6, 0.5),  # a large party: one step down, to the ×0.5 the table omits
            (20, 2, 5.0),  # and up to the ×5 it omits
        ],
    )
    def test_multipliers(
        self, rules: DifficultyRules, monsters: int, party: int, factor: float
    ) -> None:
        assert rules.multiplier(monsters, party) == factor

    def test_levels_are_clamped(self, rules: DifficultyRules) -> None:
        assert rules.bands(EDITION_CLASSIC, [25]) == rules.bands(EDITION_CLASSIC, [20])

    def test_no_party_no_rating(self, rules: DifficultyRules) -> None:
        rating = rules.rate(EDITION_CLASSIC, [], [(50, 4)])
        assert not rating.rated and rating.level is None

    @pytest.mark.parametrize(
        ("sources", "expected"),
        [
            (["PHB", "MM", "DMG"], EDITION_CLASSIC),
            (["XPHB", "XMM"], EDITION_ONE),
            (["PHB", "XPHB"], EDITION_ONE),  # both: what supersession picks
            ([], EDITION_ONE),
        ],
    )
    def test_rules_follow_the_books(self, sources: list[str], expected: str) -> None:
        assert rules_for(SourceSet(sources=sources)) == expected

    def test_an_override_wins(self) -> None:
        assert rules_for(SourceSet(sources=["XPHB"]), EDITION_CLASSIC) == EDITION_CLASSIC


class TestService:
    @pytest.fixture
    def setup(
        self, tmp_path: Path, loaded_compendium: CompendiumStore
    ) -> tuple[EncounterService, GameStateStore, World]:
        store = GameStateStore(tmp_path)
        world = store.worlds.create(
            World(name="Phandalin", source_set=SourceSet(sources=["PHB", "MM"]))
        )
        rules = DifficultyRules(reference=corpus_path())
        return EncounterService(store, loaded_compendium, rules), store, world

    def test_a_name_is_required(
        self, setup: tuple[EncounterService, GameStateStore, World]
    ) -> None:
        service, _, world = setup
        with pytest.raises(EncounterError):
            service.create("   ", world.id)

    def test_an_unknown_world_is_refused(
        self, setup: tuple[EncounterService, GameStateStore, World]
    ) -> None:
        service, _, _ = setup
        with pytest.raises(EncounterError):
            service.create("Ambush", "nope")

    def test_adding_twice_raises_the_count(
        self, setup: tuple[EncounterService, GameStateStore, World]
    ) -> None:
        service, _, world = setup
        enc = service.create("Ambush", world.id)
        service.add_monster(enc.id, "Goblin|MM")
        enc = service.add_monster(enc.id, "goblin|mm", 3)
        assert [(m.name, m.source, m.count) for m in enc.monsters] == [("Goblin", "MM", 4)]

    def test_an_unknown_monster_is_refused(
        self, setup: tuple[EncounterService, GameStateStore, World]
    ) -> None:
        service, _, world = setup
        enc = service.create("Ambush", world.id)
        with pytest.raises(EncounterError):
            service.add_monster(enc.id, "Tarrasque|PHB")

    def test_a_count_of_zero_removes_the_line(
        self, setup: tuple[EncounterService, GameStateStore, World]
    ) -> None:
        service, _, world = setup
        enc = service.create("Ambush", world.id)
        service.add_monster(enc.id, "Goblin|MM")
        service.add_monster(enc.id, "Goblin Boss|MM")
        enc = service.set_count(enc.id, 0, 0)
        assert [m.name for m in enc.monsters] == ["Goblin Boss"]
        with pytest.raises(EncounterError):
            service.set_count(enc.id, 5, 1)

    def test_rated_against_the_worlds_characters(
        self, setup: tuple[EncounterService, GameStateStore, World]
    ) -> None:
        service, store, world = setup
        for name in ("Ana", "Bo", "Cy", "Di"):
            store.characters.create(
                PlayerCharacter(
                    name=name,
                    world_id=world.id,
                    classes=[ClassLevel(name="Fighter", source="PHB", level=1)],
                )
            )
        enc = service.create("Ambush", world.id)
        enc = service.add_monster(enc.id, "Goblin|MM", 4)
        view = service.view(enc)
        assert [m.level for m in view.party] == [1, 1, 1, 1] and not view.party_stated
        assert view.rating.rules == EDITION_CLASSIC  # PHB + MM declare 2014
        assert view.rating.level == "deadly"

    def test_a_stated_party_replaces_the_characters(
        self, setup: tuple[EncounterService, GameStateStore, World]
    ) -> None:
        service, _, world = setup
        enc = service.create("Ambush", world.id)
        service.add_monster(enc.id, "Goblin|MM", 4)
        enc = service.update(
            enc.id,
            name="Ambush",
            scope=Scope(world_id=world.id),
            notes="",
            rules="one",
            party=PartySpec(size=5, level=3),
        )
        view = service.view(enc)
        assert view.party_stated and [m.level for m in view.party] == [3] * 5
        assert view.rating.rules == EDITION_ONE

    def test_a_vanished_monster_stays_visible_unrated(
        self, setup: tuple[EncounterService, GameStateStore, World]
    ) -> None:
        service, store, world = setup
        enc = service.create("Ambush", world.id)
        enc = service.add_monster(enc.id, "Goblin|MM")

        def rename(e: object) -> None:
            e.monsters[0].source = "GONE"  # type: ignore[attr-defined]

        store.encounters.mutate(enc.id, rename)
        view = service.view(service.get(enc.id))
        assert view.lines[0].monster is None and view.unrated == 1

    def test_not_found(self, setup: tuple[EncounterService, GameStateStore, World]) -> None:
        service, _, _ = setup
        with pytest.raises(EncounterNotFound):
            service.get("nope")


@pytest.fixture
async def client(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER)
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry


class TestRoutes:
    async def test_plan_a_fight(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(
            World(name="Phandalin", source_set=SourceSet(sources=["PHB", "MM"]))
        )

        created = await ac.post(
            "/gm/encounters", data={"name": "Cragmaw ambush", "world": world.id}
        )
        assert created.status_code == 303
        url = created.headers["location"].split("#")[0]
        encounter_id = url.rsplit("/", 1)[1]

        page = await ac.get(url, params={"q": "goblin"})
        assert page.status_code == 200
        text = page.text
        assert 'formaction="/gm/encounters/' in text and 'value="Goblin|MM"' in text
        assert "XMM" not in text.split('id="picker-results"', 1)[1].split("</form>", 1)[0]

        added = await ac.post(
            f"/gm/encounters/{encounter_id}/monsters", data={"ref": "Goblin|MM", "q": "goblin"}
        )
        assert added.status_code == 303
        assert "q=goblin" in added.headers["location"]
        assert added.headers["location"].endswith("#picker")

        await ac.post(f"/gm/encounters/{encounter_id}/monsters/0", data={"count": "4"})
        await ac.post(
            f"/gm/encounters/{encounter_id}",
            data={
                "name": "Cragmaw ambush",
                "world": world.id,
                "use_party": "on",
                "party_size": "4",
                "party_level": "1",
            },
        )
        text = (await ac.get(url)).text
        assert "Deadly" in text and "400 XP" in text
        assert f'href="/ref/monster/MM/Goblin?world={world.id}"' in text  # previews work

        listing = (await ac.get("/gm/encounters")).text
        assert "Cragmaw ambush" in listing and "Deadly" in listing

    async def test_a_corpus_without_the_2014_guide_says_so(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        """No thresholds to hold the fight against: the page names the book, not a level."""
        ac, registry = client
        registry.__dict__["encounters"] = EncounterService(
            registry.gamestate, registry.compendium, DifficultyRules()
        )
        world = registry.gamestate.worlds.create(
            World(name="Ferry", source_set=SourceSet(sources=["PHB", "MM"]))
        )
        encounter = registry.encounters.create("Ambush", world.id)
        registry.encounters.add_monster(encounter.id, "Goblin|MM", 4)
        stated = PartySpec(size=4, level=1)
        within = Scope(world_id=world.id)
        registry.encounters.update(
            encounter.id, name="Ambush", scope=within, notes="", rules=None, party=stated
        )

        text = (await ac.get(f"/gm/encounters/{encounter.id}")).text
        assert "No 2014 thresholds to rate against" in text and "Deadly" not in text
        assert "200 XP" in text  # what the monsters are worth is still said

        registry.encounters.update(
            encounter.id, name="Ambush", scope=within, notes="", rules=EDITION_ONE, party=stated
        )
        text = (await ac.get(f"/gm/encounters/{encounter.id}")).text
        assert "No 2014 thresholds" not in text and "Low" in text

    async def test_the_picker_fragment(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        enc = registry.encounters.create("Ambush", None)
        fragment = (
            await ac.get("/search", params={"q": "goblin", "pick": enc.id, "partial": "1"})
        ).text
        assert fragment.startswith('<div id="picker-results"')
        assert "res-pick" in fragment and 'role="option"' in fragment
        assert "/ref/spell/" not in fragment  # creatures only

    async def test_an_unknown_monster_is_explained(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        enc = registry.encounters.create("Ambush", None)
        response = await ac.post(f"/gm/encounters/{enc.id}/monsters", data={"ref": "Nope|XX"})
        assert response.status_code == 422 and "Nope|XX" in response.text

    async def test_delete(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        enc = registry.encounters.create("Ambush", None)
        assert (await ac.get(f"/gm/encounters/{enc.id}/delete")).status_code == 200
        done = await ac.post(f"/gm/encounters/{enc.id}/delete")
        assert done.status_code == 303
        assert (await ac.get(f"/gm/encounters/{enc.id}")).status_code == 404

    async def test_players_may_not(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        enc = registry.encounters.create("Ambush", None)
        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        assert (await ac.get("/gm/encounters")).status_code == 403
        assert (
            await ac.post(f"/gm/encounters/{enc.id}/monsters", data={"ref": "Goblin|MM"})
        ).status_code == 403
        fragment = (
            await ac.get("/search", params={"q": "goblin", "pick": enc.id, "partial": "1"})
        ).text
        assert "res-pick" not in fragment  # a player's search ignores the picker


class TestAllies:
    """Planned allies (COMBAT.md §3): on the party's side in the rating and the fight."""

    def test_an_ally_joins_the_party_and_earns_nothing(
        self, tmp_path: Path, loaded_compendium: CompendiumStore, rules: DifficultyRules
    ) -> None:
        store = GameStateStore(tmp_path)
        world = store.worlds.create(World(name="P", source_set=SourceSet(sources=["PHB", "MM"])))
        service = EncounterService(store, loaded_compendium, rules)
        encounter = service.create("Ambush", world.id)
        service.add_monster(encounter.id, "Goblin|MM", 4)
        encounter = service.add_monster(encounter.id, "Wolf|MM", 2)
        encounter = service.update(
            encounter.id,
            name="Ambush",
            scope=encounter.scope,
            notes="",
            rules=None,
            party=PartySpec(size=2, level=1),
        )
        before = service.view(encounter).rating
        assert before.monster_count == 6 and before.party_size == 2
        encounter = service.set_ally(encounter.id, 1, True)  # the wolves
        rating = service.view(encounter).rating
        assert rating.monster_count == 4 and rating.party_size == 4  # two level-1 wolves join
        assert (
            rating.adjusted_xp == rules.rate(EDITION_CLASSIC, [1, 1, 1, 1], [(50, 4)]).adjusted_xp
        )
        # another wolf is a foe again, on a line of its own
        encounter = service.add_monster(encounter.id, "Wolf|MM", 1)
        assert [(m.name, m.count, m.ally) for m in encounter.monsters][1:] == [
            ("Wolf", 2, True),
            ("Wolf", 1, False),
        ]

    async def test_the_builder_switches_and_the_fight_starts_with_allies(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(
            World(name="P", source_set=SourceSet(sources=["PHB", "MM"]))
        )
        encounter = registry.encounters.create("Ambush", world.id)
        registry.encounters.add_monster(encounter.id, "Goblin|MM", 2)
        registry.encounters.add_monster(encounter.id, "Wolf|MM", 1)
        url = f"/gm/encounters/{encounter.id}"
        switched = await ac.post(f"{url}/monsters/1/side", data={"ally": "1"})
        assert switched.status_code == 303
        page = (await ac.get(url)).text
        assert "side-friend" in page and "side-foe" in page
        combat = registry.combats.start(registry.encounters.get(encounter.id), "dm")
        sides = {c.label: c.ally for c in combat.combatants if c.ref}
        assert sides == {"Goblin 1": False, "Goblin 2": False, "Wolf": True}


class TestNotesAsParaventMarkdown:
    async def test_rendered_where_the_encounter_is_placed(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        from app.models.gamestate import Npc, Scope, SourceSet, World

        ac, registry = client
        world = registry.gamestate.worlds.create(
            World(name="Phandalin", source_set=SourceSet(sources=["PHB", "MM"]))
        )
        adventure = registry.adventures.create(world.id, "Goblin Arrows")
        here = Scope(world_id=world.id, adventure_id=adventure.id)
        registry.gamestate.npcs.create(Npc(scope=here, name="Klarg"))
        encounter = registry.encounters.create("Cragmaw Hideout", world.id, here)
        registry.encounters.update(
            encounter.id,
            name="Cragmaw Hideout",
            scope=here,
            notes="> [!tip] Tactics\n> {@npc Klarg} hides; the {@creature Goblin}s **flee**.",
            rules=None,
            party=None,
        )
        page = (await ac.get(f"/gm/encounters/{encounter.id}")).text
        assert 'class="md-body enc-notes"' in page
        assert 'class="md-callout md-tip"' in page and "<strong>flee</strong>" in page
        assert "Klarg</a>" in page  # the adventure's own NPC resolves
        assert 'class="prose-editor"' in page and f'data-scope="a:{adventure.id}"' in page

    async def test_beside_the_board_in_the_tracker(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        from app.models.gamestate import Scope, SourceSet, World

        ac, registry = client
        world = registry.gamestate.worlds.create(
            World(name="Phandalin", source_set=SourceSet(sources=["PHB", "MM"]))
        )
        here = Scope(world_id=world.id)
        encounter = registry.encounters.create("Ambush", world.id, here)
        registry.encounters.add_monster(encounter.id, "Goblin|MM", 2)
        registry.encounters.update(
            encounter.id,
            name="Ambush",
            scope=here,
            notes="**Morale:** the {@creature Goblin}s flee at half their number.",
            rules=None,
            party=None,
        )
        fight = registry.combats.start(registry.encounters.get(encounter.id), by="dm")
        page = (await ac.get(f"/gm/combat/{fight.id}")).text
        assert '<details class="panel cb-notes">' in page
        assert "<strong>Morale:</strong>" in page and "Goblin" in page
        # A fight without notes shows no panel.
        bare = registry.encounters.create("Quiet", world.id, here)
        registry.encounters.add_monster(bare.id, "Goblin|MM", 1)
        quiet = registry.combats.start(registry.encounters.get(bare.id), by="dm")
        assert "cb-notes" not in (await ac.get(f"/gm/combat/{quiet.id}")).text
