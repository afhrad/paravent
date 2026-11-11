"""The term glossary (documentation/TRANSLATION.md §3–4) — against the real corpus."""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Encounter, EncounterMonster, Role
from app.models.pc import PlayerCharacter
from app.services.glossary import Glossary
from app.services.i18n import LANG_COOKIE, UiCatalogue
from app.services.name_suggestions import ApiRunner, NameSuggestions, SuggestError
from app.store.compendium import CompendiumStore
from app.store.registry import StoreRegistry
from app.ui.tags import localized_context, render

PASSWORD = "correct horse battery"


class TestStore:
    def test_names_are_set_found_and_removed(self, tmp_path: Path) -> None:
        glossary = Glossary(tmp_path)
        glossary.set("de", "spell", "Fireball", "Feuerball")
        assert glossary.term("de", "spell", "fireball") == "Feuerball"  # any case
        assert json.loads(glossary.path("de").read_text()) == {"spell": {"Fireball": "Feuerball"}}
        glossary.set("de", "spell", "Fireball", "  ")
        assert glossary.term("de", "spell", "Fireball") is None
        assert glossary.count("de") == 0
        # a cleared name stays in the file as one still to give
        assert json.loads(glossary.path("de").read_text()) == {"spell": {"Fireball": "-"}}

    def test_a_placeholder_is_no_name(self, tmp_path: Path) -> None:
        glossary = Glossary(tmp_path)
        path = glossary.path("de")
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"monster": {"Owlbear": "Eulenbär", "Animated Broom": "-", "X": "—"}}),
            encoding="utf-8",
        )
        assert glossary.term("de", "monster", "Animated Broom") is None  # English, not "-"
        assert glossary.term("de", "monster", "Owlbear") == "Eulenbär"
        glossary.set("de", "monster", "Owlbear", "-")
        assert glossary.term("de", "monster", "Owlbear") is None
        assert [t.english for t in glossary.terms("de")] == []  # no placeholder listed
        assert json.loads(path.read_text())["monster"]["Owlbear"] == "-"

    def test_fill_adds_the_missing_names_as_placeholders(self, tmp_path: Path) -> None:
        glossary = Glossary(tmp_path)
        glossary.set("de", "spell", "Fireball", "Feuerball")
        glossary.set("de", "spell", "Shield", "-")
        added = glossary.fill(
            "de", {"spell": ["fireball", "Shield", "Mage Hand"], "monster": ["Owlbear"]}
        )
        assert added == {"spell": 1, "monster": 1}  # names there, given or not, are kept
        assert json.loads(glossary.path("de").read_text()) == {
            "monster": {"Owlbear": "-"},
            "spell": {"Fireball": "Feuerball", "Mage Hand": "-", "Shield": "-"},
        }
        assert glossary.term("de", "spell", "Mage Hand") is None
        assert glossary.count("de") == 1
        assert glossary.fill("de", {"spell": ["Mage Hand"]}) == {}  # nothing new

    def test_a_file_dropped_in_is_read(self, tmp_path: Path) -> None:
        glossary = Glossary(tmp_path)
        assert glossary.term("de", "monster", "Owlbear") is None
        path = glossary.path("de")
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"monster": {"Owlbear": "Eulenbär"}}), encoding="utf-8")
        later = time.time() + 5
        os.utime(path, (later, later))  # a new modification time, as an import gives
        assert glossary.term("de", "monster", "Owlbear") == "Eulenbär"


class TestTranslator:
    def test_english_has_no_names_and_german_has_the_glossarys(self, tmp_path: Path) -> None:
        glossary = Glossary(tmp_path)
        glossary.set("de", "spell", "Fireball", "Feuerball")
        catalogue = UiCatalogue()
        assert catalogue.translator("en", glossary).term("spell", "Fireball") is None
        german = catalogue.translator("de", glossary)
        assert german.term("spell", "Fireball") == "Feuerball"
        assert german.terms()("spell", "Fireball") == "Feuerball"

    def test_conditions_and_skills_are_named_by_the_ui_catalogue(self, tmp_path: Path) -> None:
        german = UiCatalogue().translator("de", Glossary(tmp_path))
        assert german.term("condition", "Prone") == "Liegend"
        assert german.term("skill", "Stealth") is not None
        assert german.term("spell", "Prone") is None


class TestRendering:
    def test_a_link_shows_the_name_and_keeps_its_target(
        self, tmp_path: Path, loaded_compendium: CompendiumStore
    ) -> None:
        glossary = Glossary(tmp_path)
        glossary.set("de", "spell", "Fireball", "Feuerball")
        german = UiCatalogue().translator("de", glossary)
        ctx = localized_context(german, resolver=loaded_compendium.resolver(), source_set=None)
        html = "".join(
            str(n) for n in render("Cast {@spell fireball} at the {@condition prone} foe.", ctx)
        )
        assert ">Feuerball</a>" in html and "/ref/spell/" in html and "Fireball" in html
        assert ">Liegend</a>" in html


@pytest.fixture
async def client(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER, language="de")
    registry.auth.create_user("alice", PASSWORD, Role.PLAYER, language="de")
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={LANG_COOKIE: "de"},
    ) as ac:
        yield ac, registry


async def _login(ac: AsyncClient, user: str) -> None:
    await ac.post("/login", data={"username": user, "password": PASSWORD})


class TestPages:
    async def test_the_gamemaster_names_an_entry_and_every_page_follows(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        await _login(ac, "dm")
        url = "/ref/spell/PHB/Fireball"
        assert 'action="/glossary/term"' in (await ac.get(url)).text
        saved = await ac.post(
            "/glossary/term",
            data={
                "lang": "de",
                "category": "spell",
                "english": "Fireball",
                "value": "Feuerball",
                "next": url,
            },
        )
        assert saved.headers["location"] == url
        assert registry.glossary.term("de", "spell", "Fireball") == "Feuerball"
        page = (await ac.get(url)).text
        assert "<title>Feuerball" in page and 'class="sb-en">Fireball' in page
        found = (await ac.get("/search", params={"q": "feuerb", "partial": "1"})).text
        assert "Feuerball" in found and "Fireball" in found
        # English is never changed by it
        await ac.post(
            "/glossary/term",
            data={"lang": "en", "category": "spell", "english": "Fireball", "value": "X"},
        )
        assert registry.glossary.term("en", "spell", "Fireball") is None

    async def test_the_glossary_page_lists_what_to_name_first(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        await _login(ac, "dm")
        page = (await ac.get("/gm/glossary", params={"category": "spell"})).text
        assert "Mage Hand" in page  # among the most linked spells
        assert "Noch keine Namen." in page
        registry.glossary.set("de", "spell", "Mage Hand", "Magierhand")
        page = (await ac.get("/gm/glossary", params={"category": "spell"})).text
        assert 'value="Magierhand"' in page
        conditions = (await ac.get("/gm/glossary", params={"category": "condition"})).text
        assert ">Prone<" not in conditions  # the UI names it already

    async def test_names_still_open_are_given_a_page_at_a_time(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        from app.ui.glossary_pages import PER_PAGE

        ac, registry = client
        await _login(ac, "dm")
        registry.glossary.fill(
            "de",
            {
                "spell": ["Fireball", "Mage Hand", "Shield"],
                "monster": [f"Beast {i:03}" for i in range(PER_PAGE + 5)],
            },
        )
        glossary = (await ac.get("/gm/glossary")).text
        assert f"{PER_PAGE + 8} Namen noch offen" in glossary  # linked from the glossary
        page = (await ac.get("/gm/glossary/open", params={"category": "monster"})).text
        assert "Seite 1 von 2" in page and 'name="n:monster:Beast 000"' in page
        assert "Beast 104" not in page  # on page 2
        spells = (await ac.get("/gm/glossary/open", params={"q": "shi"})).text
        assert 'name="n:spell:Shield"' in spells and "Fireball" not in spells  # search

        saved = await ac.post(
            "/gm/glossary/open",
            data={
                "lang": "de",
                "category": "spell",
                "q": "",
                "page": "1",
                "n:spell:Fireball": "Feuerball",
                "n:spell:Mage Hand": "  ",  # empty: stays "-"
                "n:spell:Shield": "-",  # a placeholder is no name
                "n:nope:Thing": "X",  # not a category
            },
        )
        assert "saved=1" in saved.headers["location"]
        assert registry.glossary.term("de", "spell", "Fireball") == "Feuerball"
        assert registry.glossary.still_open("de")["spell"] == ["Mage Hand", "Shield"]
        after = (await ac.get(saved.headers["location"])).text
        assert "1 Name gespeichert." in after and 'name="n:spell:Fireball"' not in after
        raw = json.loads(registry.glossary.path("de").read_text())
        assert raw["spell"] == {"Fireball": "Feuerball", "Mage Hand": "-", "Shield": "-"}
        assert "nope" not in raw

    async def test_names_players_meet_come_alone(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        await _login(ac, "dm")
        registry.glossary.fill(
            "de", {"spell": ["Fireball", "Wish"], "monster": ["Goblin"], "item": ["Bag of Holding"]}
        )
        page = (await ac.get("/gm/glossary/open", params={"players": "1"})).text
        assert 'name="n:spell:Fireball"' in page and 'name="n:spell:Wish"' in page
        assert "n:monster:Goblin" not in page and "n:item:Bag of Holding" not in page
        assert 'name="players" value="1"' in page  # kept by search and saving
        assert "players=1" in page  # and by the chips
        everything = (await ac.get("/gm/glossary/open")).text
        assert 'name="n:monster:Goblin"' in everything
        saved = await ac.post(
            "/gm/glossary/open",
            data={"lang": "de", "players": "1", "n:spell:Fireball": "Feuerball"},
        )
        assert "players=1" in saved.headers["location"]

    async def test_players_cannot_name_entries(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        await _login(ac, "alice")
        assert 'action="/glossary/term"' not in (await ac.get("/ref/spell/PHB/Fireball")).text
        assert (await ac.get("/gm/glossary")).status_code == 403
        assert (await ac.get("/gm/glossary/open")).status_code == 403
        assert (await ac.post("/gm/glossary/open", data={"lang": "de"})).status_code == 403
        refused = await ac.post(
            "/glossary/term",
            data={"lang": "de", "category": "spell", "english": "Fireball", "value": "X"},
        )
        assert refused.status_code == 403
        assert registry.glossary.term("de", "spell", "Fireball") is None

    async def test_the_table_screens_show_the_names(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        examples = Path(__file__).resolve().parent.parent / "documentation" / "examples"
        lyra = registry.gamestate.characters.create(
            PlayerCharacter.model_validate(
                json.loads((examples / "lyra-mooncaller.json").read_text(encoding="utf-8"))
            )
        )
        for category, english, german in (
            ("spell", "Magic Missile", "Magisches Geschoss"),
            ("class", "Wizard", "Magier"),
            ("monster", "Owlbear", "Eulenbär"),
            ("spell", "Haste", "Hast"),
        ):
            registry.glossary.set("de", category, english, german)
        await _login(ac, "dm")

        sheet = (await ac.get(f"/characters/{lyra.id}?tab=spells")).text
        assert 'title="Magic Missile">Magisches Geschoss<' in sheet
        assert "Magier 10" in sheet  # the line under the name
        assert "Magier 10" in (await ac.get("/gm")).text  # the party card
        assert "Konzentration: Hast" in (await ac.get(f"/characters/{lyra.id}")).text
        registry.glossary.set("de", "race", "Dwarf", "Zwerg")
        assert "<b>Zwerg</b>" in (await ac.get("/gm/names")).text  # a people in the workshop

        encounter = registry.gamestate.encounters.create(
            Encounter(
                name="Woods", monsters=[EncounterMonster(name="Owlbear", source="MM", count=2)]
            )
        )
        assert "Eulenbär" in (await ac.get(f"/gm/encounters/{encounter.id}")).text
        combat = registry.combats.start(encounter, "dm", characters=[lyra.id])
        tracker = (await ac.get(f"/gm/combat/{combat.id}")).text
        assert ">Eulenbär 1<" in tracker and 'title="Owlbear 1"' in tracker
        assert "Magier 10" in tracker and "Wizard 10" not in tracker  # the character's row
        assert "Hast<" in tracker  # what Lyra concentrates on, in her row's pill
        assert "<span>Eulenbär</span>" in tracker  # the kind under a creature's label
        assert registry.combats.get(combat.id).combatants[0].label == "Owlbear 1"  # stored English

        await _login(ac, "alice")
        registry.gamestate.characters.update(lyra.id, player="alice")
        seen = (await ac.get(f"/play/combat/{combat.id}")).text
        assert "Eulenbär 1" in seen


class TestCharacterLine:
    def test_a_localized_subrace_is_the_whole_name(self, tmp_path: Path) -> None:
        from app.ui.context import Page
        from app.ui.names import summary

        brunhild = PlayerCharacter.model_validate(
            json.loads(
                (
                    Path(__file__).resolve().parent.parent
                    / "documentation"
                    / "examples"
                    / "brunhild-ironvow.json"
                ).read_text(encoding="utf-8")
            )
        )
        glossary = Glossary(tmp_path)
        catalogue = UiCatalogue()
        english = summary(Page(t=catalogue.translator("en", glossary)), brunhild)
        assert english.startswith("Mountain Dwarf · ")
        # untranslated subrace: its English name before the localized species
        glossary.set("de", "race", "Dwarf", "Zwerg")
        assert summary(Page(t=catalogue.translator("de", glossary)), brunhild).startswith(
            "Mountain Zwerg · "
        )
        # translated: the one German word, not "Gebirgszwerg Zwerg"
        glossary.set("de", "subrace", "Mountain", "Gebirgszwerg")
        glossary.set("de", "background", "Soldier", "Soldat")
        line = summary(Page(t=catalogue.translator("de", glossary)), brunhild)
        assert line.startswith("Gebirgszwerg · ") and line.endswith(" · Soldat")


class _FakeClaude:
    """Answers like the API would: German-ish names, one entry left out."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def __call__(
        self, system: str, prompt: str, schema: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        self.prompts.append(prompt)
        entries = [line for line in prompt.splitlines() if line[:1].isdigit()]
        names = []
        for line in entries:
            at, _, rest = line.partition(": ")
            english = rest.split(" (", 1)[0]
            if english != "Skip Me":
                names.append({"id": int(at), "name": f"{english} (de)"})
        names.append({"id": 99, "name": "Out of range"})
        return {"names": names}


def _suggester(registry: StoreRegistry, runner: Any) -> NameSuggestions:
    suggestions = NameSuggestions(
        registry.settings.i18n_dir,
        registry.glossary,
        runner,
        language_name=lambda code: "German",
        kind_name=lambda category: category,
        source_of=lambda category, name: "MM" if category == "monster" else None,
    )
    registry.name_suggestions = suggestions  # type: ignore[misc]
    return suggestions


class TestSuggestions:
    async def test_suggestions_are_asked_shown_and_taken(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        await _login(ac, "dm")
        fake = _FakeClaude()
        suggestions = _suggester(registry, fake)
        registry.glossary.set("de", "monster", "Goblin", "Goblin")
        registry.glossary.fill("de", {"monster": ["Owlbear", "Beholder", "Skip Me"]})

        page = (await ac.get("/gm/glossary/open")).text
        assert "Vorschläge holen" in page and "3 Namen in dieser Ansicht" in page
        await ac.post("/gm/glossary/open/suggest", data={"lang": "de", "category": "monster"})
        run = suggestions.progress("de")
        assert run is not None and run._task is not None
        await run._task
        assert (run.total, run.suggested, run.failed, run.finished) == (3, 2, 1, True)
        prompt = fake.prompts[0]
        assert "Language: German" in prompt and "Goblin = Goblin" in prompt  # the table's own
        assert "Owlbear (MM)" in prompt
        assert suggestions.all("de") == {
            "monster": {"Beholder": "Beholder (de)", "Owlbear": "Owlbear (de)"}
        }
        assert registry.glossary.term("de", "monster", "Owlbear") is None  # not a name yet
        status = (await ac.get("/gm/glossary/open/progress", params={"lang": "de"})).text
        assert "2 vorgeschlagen, 1 ohne Antwort" in status and "hx-get" not in status

        page = (await ac.get("/gm/glossary/open", params={"category": "monster"})).text
        assert "Vorschlag: Owlbear (de)" in page and 'value="monster:Owlbear"' in page
        assert "1 Name in dieser Ansicht" in page  # only Skip Me is left to ask

        # one suggestion taken; another field typed is saved with it
        await ac.post(
            "/gm/glossary/open",
            data={
                "lang": "de",
                "category": "monster",
                "page": "1",
                "take": "monster:Owlbear",
                "n:monster:Owlbear": "",
                "n:monster:Beholder": "",
                "n:monster:Skip Me": "Überspring mich",
            },
        )
        assert registry.glossary.term("de", "monster", "Owlbear") == "Owlbear (de)"
        assert registry.glossary.term("de", "monster", "Skip Me") == "Überspring mich"
        assert registry.glossary.term("de", "monster", "Beholder") is None
        assert suggestions.all("de") == {"monster": {"Beholder": "Beholder (de)"}}

        # all of a page: a typed name wins over the suggestion
        await ac.post(
            "/gm/glossary/open",
            data={"lang": "de", "action": "take_all", "n:monster:Beholder": "Betrachter"},
        )
        assert registry.glossary.term("de", "monster", "Beholder") == "Betrachter"
        assert suggestions.all("de") == {}

    async def test_take_all_fills_the_empty_fields(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        await _login(ac, "dm")
        suggestions = _suggester(registry, _FakeClaude())
        registry.glossary.fill("de", {"spell": ["Fireball", "Shield"]})
        run = suggestions.start("de", {"spell": ["Fireball", "Shield"]})
        assert run is not None and run._task is not None
        assert suggestions.start("de", {"spell": ["Fireball"]}) is None  # one run at a time
        await run._task
        assert suggestions.start("de", {"spell": ["Fireball", "Shield"]}) is None  # all asked
        await ac.post(
            "/gm/glossary/open",
            data={
                "lang": "de",
                "action": "take_all",
                "n:spell:Fireball": "",
                "n:spell:Shield": "",
            },
        )
        assert registry.glossary.still_open("de") == {}
        assert registry.glossary.term("de", "spell", "Shield") == "Shield (de)"

    async def test_without_claude_code_the_page_says_so(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        await _login(ac, "dm")
        _suggester(registry, None)
        registry.glossary.fill("de", {"spell": ["Fireball"]})
        page = (await ac.get("/gm/glossary/open")).text
        assert "Vorschläge holen" not in page and "PARAVENT_TRANSLATE_API_KEY" in page
        await ac.post("/gm/glossary/open/suggest", data={"lang": "de"})
        assert registry.name_suggestions.progress("de") is None

    async def test_a_failing_command_leaves_names_open(self, tmp_path: Path) -> None:
        async def broken(system: str, prompt: str, schema: Mapping[str, Any]) -> None:
            raise SuggestError("usage limit reached")

        glossary = Glossary(tmp_path)
        suggestions = NameSuggestions(
            tmp_path,
            glossary,
            broken,
            language_name=lambda code: "German",
            kind_name=lambda category: category,
            source_of=lambda category, name: None,
        )
        run = suggestions.start("de", {"spell": ["Fireball"]})
        assert run is not None and run._task is not None
        await run._task
        assert (run.failed, run.error, suggestions.all("de")) == (1, "usage limit reached", {})


class _ApiReply:
    def __init__(self, text: str, stop: str = "end_turn") -> None:
        self.stop_reason = stop
        self.content = [type("Block", (), {"type": "text", "text": text})()]


class _ApiStream:
    def __init__(self, reply: _ApiReply) -> None:
        self.reply = reply

    async def __aenter__(self) -> _ApiStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get_final_message(self) -> _ApiReply:
        return self.reply


class _ApiClient:
    """Stands in for the Claude API's streamed messages."""

    def __init__(self, reply: _ApiReply) -> None:
        self.requests: list[dict[str, Any]] = []
        self.reply = reply
        self.beta = type("Beta", (), {"messages": self})()

    def stream(self, **kwargs: Any) -> _ApiStream:
        self.requests.append(kwargs)
        return _ApiStream(self.reply)


class TestApiRunner:
    async def test_a_request_is_held_to_the_schema_and_read(self) -> None:
        reply = _ApiReply(json.dumps({"names": [{"id": 0, "name": "Eulenbär"}]}))
        client = _ApiClient(reply)
        runner = ApiRunner(api_key="", model="claude-opus-5-5", client=client)  # type: ignore[arg-type]
        answer = await runner("system", "0: Owlbear", {"type": "object"})
        assert answer == {"names": [{"id": 0, "name": "Eulenbär"}]}
        sent = client.requests[0]
        assert sent["model"] == "claude-opus-5-5"
        assert sent["output_config"]["format"] == {
            "type": "json_schema",
            "schema": {"type": "object"},
        }
        assert sent["messages"] == [{"role": "user", "content": "0: Owlbear"}]

    async def test_a_refusal_or_bad_answer_is_an_error(self) -> None:
        for reply in (_ApiReply("{}", stop="refusal"), _ApiReply("not json")):
            runner = ApiRunner(api_key="", model="m", client=_ApiClient(reply))  # type: ignore[arg-type]
            with pytest.raises(SuggestError):
                await runner("system", "0: Owlbear", {})
