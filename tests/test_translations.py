"""Translated corpus text (documentation/TRANSLATION.md §5) — against the real corpus."""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services.i18n import LANG_COOKIE, UiCatalogue
from app.services.translations import (
    CHAPTER_FIELDS,
    TranslationStore,
    english_hash,
    marks_in,
    missing_tags,
    passages,
    record_key,
)
from app.store.compendium import CompendiumStore
from app.store.registry import StoreRegistry

PASSWORD = "correct horse battery"


def _fireball(compendium: CompendiumStore) -> dict[str, Any]:
    data: dict[str, Any] = compendium.get("spell", "fireball|phb").model_dump(
        by_alias=True, exclude_none=True
    )
    return data


class TestPassages:
    def test_a_spells_text_and_its_higher_levels(self, loaded_compendium: CompendiumStore) -> None:
        found = dict(passages(_fireball(loaded_compendium)))
        assert found["entries/0"].startswith("A bright streak flashes")
        assert any(path.startswith("entriesHigherLevel/0/entries/") for path in found)
        assert "name" not in found  # the entry's name is the glossary's

    def test_structure_is_not_text(self) -> None:
        data = {
            "entries": [
                {"type": "statblock", "tag": "creature", "name": "Goblin", "source": "MM"},
                {
                    "type": "table",
                    "caption": "Loot",
                    "colLabels": ["d6", "Item"],
                    "rows": [
                        ["1", "a {@item dagger|phb}"],
                        [{"type": "cell", "roll": {"exact": 2}}, "rope"],
                    ],
                },
                {"type": "list", "items": ["one", {"type": "item", "name": "Two", "entry": "two"}]},
            ]
        }
        found = dict(passages(data))
        assert "entries/0/name" not in found  # a statblock's name points at a creature
        assert found["entries/1/caption"] == "Loot"
        assert found["entries/1/rows/0/1"] == "a {@item dagger|phb}"
        assert "entries/1/rows/1/0/roll" not in str(found)
        assert found["entries/2/items/1/name"] == "Two" and found["entries/2/items/0"] == "one"

    def test_a_chapter_translates_its_own_name(self) -> None:
        chapter = {"type": "section", "name": "Combat", "entries": ["Text"]}
        assert dict(passages(chapter, CHAPTER_FIELDS)) == {"name": "Combat", "entries/0": "Text"}

    def test_placeholders_are_kept_like_tags(self) -> None:
        from app.services.machine_translation import _accepted

        english = "{@hit <$to_hit__str$>} to hit; <$title_short_name$> bites (DC <$dc__str$>)."
        assert marks_in(english) == [
            "{@hit <$to_hit__str$>}",
            "<$title_short_name$>",
            "<$dc__str$>",
        ]
        kept = "{@hit <$to_hit__str$>} zum Treffen; <$title_short_name$> beißt (SG <$dc__str$>)."
        lost = "{@hit <$to_hit__str$>} zum Treffen; der Hund beißt (SG <$dc__str$>)."
        assert missing_tags(english, kept) == [] and missing_tags(english, lost) == [
            "<$title_short_name$>"
        ]
        assert marks_in("{=amount1/v} cups flour") == ["{=amount1/v}"]  # a recipe's amount
        # A machine draft that drops one stays English.
        batch = [("a", english), ("b", english)]
        accepted, rejected, _ = _accepted(batch, [{"id": 0, "text": kept}, {"id": 1, "text": lost}])
        assert accepted == {"a": kept} and rejected == 1


class TestStore:
    def test_a_partial_translation_falls_back_per_passage(
        self, tmp_path: Path, loaded_compendium: CompendiumStore
    ) -> None:
        store = TranslationStore(tmp_path)
        data = _fireball(loaded_compendium)
        before = copy.deepcopy(data)
        key = record_key("Fireball", "PHB")
        store.save("de", "spell", key, data, {"entries/0": "Ein greller Lichtstreif …"}, by="dm")
        shown = store.overlay("de", "spell", key, data)
        assert shown.data["entries"][0] == "Ein greller Lichtstreif …"
        assert shown.data["entries"][1:] == data["entries"][1:]  # English where untranslated
        assert shown.coverage.translated == 1 and not shown.coverage.complete
        assert data == before  # the English record is never changed
        stored = json.loads((tmp_path / "de" / "spell.json").read_text())
        assert stored["Fireball|PHB"]["passages"]["entries/0"]["en"] == english_hash(
            before["entries"][0]
        )

    def test_changed_english_is_stale_and_a_lost_path_an_orphan(self, tmp_path: Path) -> None:
        store = TranslationStore(tmp_path)
        data = {"entries": ["Old text.", "Second."]}
        store.save("de", "rule", "X|Y", data, {"entries/0": "Alt.", "entries/1": "Zwei."}, by="dm")
        changed = {"entries": ["New text."]}
        shown = store.overlay("de", "rule", "X|Y", changed)
        assert shown.data["entries"] == ["Alt."]  # still shown …
        assert shown.coverage.stale == 1 and shown.coverage.orphans == 1  # … but flagged

    def test_drafts_until_confirmed_and_empty_removes(self, tmp_path: Path) -> None:
        store = TranslationStore(tmp_path)
        data = {"entries": ["One.", "Two."]}
        store.save(
            "de",
            "rule",
            "X|Y",
            data,
            {"entries/0": "Eins.", "entries/1": "Zwei."},
            by="mt",
            draft=True,
        )
        assert store.overlay("de", "rule", "X|Y", data).coverage.drafts == 2
        # sending a draft's text back unchanged is not a review
        store.save("de", "rule", "X|Y", data, {"entries/0": "Eins."}, by="dm")
        assert store.overlay("de", "rule", "X|Y", data).coverage.drafts == 2
        # editing it is; confirming is explicit, by passage or all
        store.save("de", "rule", "X|Y", data, {"entries/0": "Eins!"}, by="dm")
        assert store.overlay("de", "rule", "X|Y", data).coverage.drafts == 1
        assert store.confirm("de", "rule", "X|Y", by="dm", paths={"entries/0"}) == 0
        assert store.confirm("de", "rule", "X|Y", by="dm") == 1
        assert store.overlay("de", "rule", "X|Y", data).coverage.drafts == 0
        store.save("de", "rule", "X|Y", data, {"entries/0": "", "entries/1": ""}, by="dm")
        assert store.record("de", "rule", "X|Y") is None
        # a path the English does not have is never stored
        store.save("de", "rule", "X|Y", data, {"entries/9": "Neun."}, by="dm")
        assert store.record("de", "rule", "X|Y") is None

    def test_a_book_has_a_file_of_its_own(self, tmp_path: Path) -> None:
        store = TranslationStore(tmp_path)
        assert store.path("de", "book", "PHB|9") == tmp_path / "de" / "book" / "phb.json"
        assert store.path("de", "spell", "Fireball|PHB") == tmp_path / "de" / "spell.json"

    def test_english_readers_get_english(self, tmp_path: Path) -> None:
        store = TranslationStore(tmp_path)
        data = {"entries": ["One."]}
        store.save("de", "rule", "X|Y", data, {"entries/0": "Eins."}, by="dm")
        english = UiCatalogue().translator("en", None, store).overlay("rule", "X|Y", data)
        german = UiCatalogue().translator("de", None, store).overlay("rule", "X|Y", data)
        assert english.data is data and german.data["entries"] == ["Eins."]


@pytest.fixture
async def client(tmp_path: Path, corpus: Path) -> AsyncIterator[tuple[AsyncClient, StoreRegistry]]:
    registry = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    registry.load()
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER, language="de")
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", cookies={LANG_COOKIE: "de"}
    ) as ac:
        await ac.post("/login", data={"username": "dm", "password": PASSWORD})
        yield ac, registry


class TestPages:
    async def test_the_reference_page_and_preview_read_german(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        data = _fireball(registry.compendium)
        url = "/ref/spell/PHB/Fireball"
        assert "Noch nicht übersetzt" in (await ac.get(url)).text
        registry.translations.save(
            "de",
            "spell",
            "Fireball|PHB",
            data,
            {"entries/0": "Ein greller {@b Lichtstreif} blitzt auf."},
            by="dm",
            draft=True,
        )
        page = (await ac.get(url)).text
        assert "Ein greller <strong>Lichtstreif</strong> blitzt auf." in page  # tags still render
        assert "Maschinelle Übersetzung" in page
        assert "Ein greller" in (await ac.get(f"{url}/preview")).text

    async def test_a_rulebook_chapter_keeps_english_anchors(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        body = registry.rulebooks.body("PHB")
        assert body is not None
        index = next(i for i, s in enumerate(body.sections) if s.name == "Combat")
        chapter = body.sections[index].entry
        names = {p: t for p, t in passages(chapter, CHAPTER_FIELDS)}
        grappling = next(p for p, t in names.items() if t == "Grappling")
        registry.translations.save(
            "de",
            "book",
            f"PHB|{index}",
            chapter,
            {"name": "Kampf", grappling: "Ringen"},
            by="dm",
            fields=CHAPTER_FIELDS,
        )
        page = (await ac.get(f"/books/PHB/{index}")).text
        assert "<h1>Kampf</h1>" in page and "Ringen" in page
        assert 'id="h-grappling"' in page  # search still links here
        assert "Teilweise übersetzt" in page


class TestEditor:
    async def test_translate_a_spell_side_by_side(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        assert (
            "/gm/translate/ref/spell/PHB/Fireball" in (await ac.get("/ref/spell/PHB/Fireball")).text
        )
        url = "/gm/translate/ref/spell/PHB/Fireball"
        editor = (await ac.get(url)).text
        assert 'name="p:entries/0"' in editor and "A bright streak flashes" in editor
        assert 'data-insert="{@damage 8d6}"' in editor  # the English tags, as chips
        saved = await ac.post(
            url, data={"lang": "de", "p:entries/0": "Ein greller Lichtstreif.", "action": "save"}
        )
        assert saved.status_code == 303 and "done=translate.saved" in saved.headers["location"]
        page = (await ac.get(saved.headers["location"])).text
        assert "Gespeichert." in page and "Ein greller Lichtstreif." in page
        assert "Tag fehlt" in page  # the English passage's {@damage} is not in it
        record = registry.translations.record("de", "spell", "Fireball|PHB")
        assert record is not None and not record.passages["entries/0"].draft
        assert "Ein greller Lichtstreif." in (await ac.get("/ref/spell/PHB/Fireball")).text

    async def test_save_and_return_goes_where_the_editor_was_opened_from(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _ = client
        url = "/gm/translate/ref/spell/PHB/Fireball"
        origin = "/ref/spell/PHB/Fireball?world=w"
        editor = (await ac.get(url, params={"lang": "de", "back": origin})).text
        assert f'value="{origin.replace("&", "&amp;")}"' in editor
        # the filter and language links keep it
        assert editor.count("back=%2Fref%2Fspell%2FPHB%2FFireball%3Fworld%3Dw") >= 2
        data = {"lang": "de", "back": origin, "action": "return"}
        left = await ac.post(url, data=data)
        assert left.status_code == 303 and left.headers["location"] == origin
        swapped = await ac.post(url, data=data, headers={"HX-Request": "true"})
        assert swapped.headers["HX-Redirect"] == origin
        # another site is never a way out; without a page opened from, the entry's own
        elsewhere = await ac.post(url, data={**data, "back": "//evil.example"})
        assert elsewhere.headers["location"] == "/ref/spell/PHB/Fireball"

    async def test_the_overview_is_where_its_entries_return_to(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _ = client
        page = (await ac.get("/gm/translations")).text
        assert "back=%2Fgm%2Ftranslations" in page

    async def test_drafts_are_reviewed_by_tick_or_all_at_once(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        data = _fireball(registry.compendium)
        texts = {path: f"Entwurf {i}" for i, (path, _) in enumerate(passages(data))}
        registry.translations.save("de", "spell", "Fireball|PHB", data, texts, by="mt", draft=True)
        url = "/gm/translate/ref/spell/PHB/Fireball"
        assert 'name="c:entries/0"' in (await ac.get(url)).text
        await ac.post(url, data={"lang": "de", "c:entries/0": "1", "action": "save"})
        assert (
            registry.translations.overlay("de", "spell", "Fireball|PHB", data).coverage.drafts
            == len(texts) - 1
        )
        left = await ac.post(
            url, data={"lang": "de", "action": "confirm_all", "back": "/gm/translations/drafts"}
        )
        assert left.status_code == 303 and left.headers["location"] == "/gm/translations/drafts"
        assert (
            registry.translations.overlay("de", "spell", "Fireball|PHB", data).coverage.drafts == 0
        )
        # drafts without the English tags stay open: a lost roll or link is worth a look
        todo = (await ac.get(url, params={"show": "todo"})).text
        assert "Entwurf</span>" not in todo and "fehl" in todo

    async def test_a_reference_screen_reads_in_the_language(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        editor = (await ac.get("/gm/translate/screen/dm/1")).text
        assert 'name="p:name"' in editor and "Combat" in editor
        assert "/gm/translate/screen/dm/1" in (await ac.get("/screen?ref=dm")).text
        assert (await ac.get("/gm/translate/screen/dm/99")).status_code == 404
        assert (await ac.get("/gm/translate/screen/nope/0")).status_code == 404

        target = registry.translation_targets.screen("dm", 1)
        registry.translations.save(
            "de",
            "screen",
            target.key,
            target.data,
            {"name": "Kampf"},
            by="dm",
            fields=target.fields,
        )
        assert registry.translations.path("de", "screen", target.key).name == "dm.json"
        page = (await ac.get("/screen?ref=dm")).text
        assert "Spielleiterschirm" in page and ">Kampf<" in page
        assert 'id="h-combat"' in page  # the anchor stays English

        counted = next(x for x in registry.translation_coverage.books("de") if x.kind == "screen")
        assert (counted.id, counted.started, counted.chapters) == ("dm", 1, 3)
        overview = (await ac.get("/gm/translations", params={"lang": "de"})).text
        assert 'href="/screen?ref=dm"' in overview
        label, targets = registry.translation_jobs.scope("de", "screen:quick")
        assert label == "quick" and len(targets) == 5

    async def test_a_reference_page_shows_the_books_description(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        page = (await ac.get("/ref/race/PHB/Dwarf")).text
        assert "Beschreibung" in page and "ancient grandeur" in page
        url = "/gm/translate/fluff/race/PHB/Dwarf"
        assert url in page
        assert (await ac.get(url)).status_code == 200

        target = registry.translation_targets.fluff("race", "PHB", "Dwarf")
        assert target.category == "raceFluff"
        path, _text = passages(target.data, target.fields)[0]
        registry.translations.save(
            "de",
            target.category,
            target.key,
            target.data,
            {path: "Uralte Zwergenhallen"},
            by="dm",
            fields=target.fields,
        )
        assert "Uralte Zwergenhallen" in (await ac.get("/ref/race/PHB/Dwarf")).text

        # Nothing in the books beyond the rules: no empty panel.
        assert 'class="panel ref-fluff"' not in (await ac.get("/ref/baseitem/PHB/Longsword")).text

    async def test_chapters_and_sections_and_who_may(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        body = registry.rulebooks.body("PHB")
        assert body is not None
        index = next(i for i, s in enumerate(body.sections) if s.name == "Combat")
        assert f"/gm/translate/book/PHB/{index}" in (await ac.get(f"/books/PHB/{index}")).text
        chapter = (await ac.get(f"/gm/translate/book/PHB/{index}")).text
        assert 'name="p:name"' in chapter and "Grappling" in chapter
        adventure = registry.published.body("LMoP")
        assert adventure is not None
        part = adventure.sections[0]
        section = await ac.get(f"/gm/translate/adventure/LMoP/{part.id}")
        assert section.status_code == 200 and 'name="p:entries/0"' in section.text
        assert (await ac.get("/gm/translate/book/PHB/999")).status_code == 404
        registry.auth.create_user("alice", PASSWORD, Role.PLAYER)
        await ac.post("/logout")
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        assert (await ac.get("/gm/translate/ref/spell/PHB/Fireball")).status_code == 403


class _Reply:
    def __init__(self, text: str, stop: str = "end_turn") -> None:
        self.stop_reason = stop
        self.content = [type("Block", (), {"type": "text", "text": text})()]


class _FakeStream:
    def __init__(self, reply: _Reply) -> None:
        self.reply = reply

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get_final_message(self) -> _Reply:
        return self.reply


class _FakeMessages:
    """Stands in for the Claude API: answers from a function of the request.

    Only ``stream`` exists, as with the real SDK at this request's ``max_tokens``: a
    plain ``create`` is refused there (it could outlast the ten-minute timeout)."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.requests: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> _FakeStream:
        self.requests.append(kwargs)
        request = json.loads(kwargs["messages"][0]["content"])
        return _FakeStream(self.answer(request))


class _FakeClient:
    def __init__(self, answer: Any) -> None:
        self.messages = _FakeMessages(answer)
        self.beta = type("Beta", (), {"messages": self.messages})()


def _german(request: dict[str, Any]) -> _Reply:
    out = [
        {"id": p["id"], "text": "DE: " + p["text"].replace("A bright", "Ein greller")}
        for p in request["passages"]
    ]
    return _Reply(json.dumps({"translations": out}))


class TestMachineDrafts:
    def _machine(self, store: TranslationStore, answer: Any) -> tuple[Any, _FakeClient]:
        from app.services.machine_translation import MachineTranslator

        fake = _FakeClient(answer)
        machine = MachineTranslator(
            store,
            lambda lang: {"Fireball": "Feuerball", "Prone": "Liegend"},
            lambda code: "German",
            api_key="test",
            model="claude-opus-5-5",
            client=fake,  # type: ignore[arg-type]
        )
        return machine, fake

    async def test_drafts_keep_tags_and_are_marked(
        self, tmp_path: Path, loaded_compendium: CompendiumStore
    ) -> None:
        from app.services.translation_targets import Target
        from app.services.translations import ENTRY_FIELDS

        store = TranslationStore(tmp_path)
        data = _fireball(loaded_compendium)
        target = Target("ref", "spell", "Fireball|PHB", data, ENTRY_FIELDS, "Fireball", ())
        machine, fake = self._machine(store, _german)
        count = await machine.draft("de", target, by="dm")
        assert count == len(passages(data))
        shown = store.overlay("de", "spell", "Fireball|PHB", data)
        assert shown.data["entries"][0].startswith("DE: Ein greller")
        assert shown.coverage.drafts == count  # marked until reviewed
        sent = fake.messages.requests[0]
        assert sent["model"] == "claude-opus-5-5" and sent["fallbacks"] == "default"
        # only terms the batch mentions travel: Fireball's text never names itself
        assert json.loads(sent["messages"][0]["content"])["glossary"] == {}
        # a second run finds nothing left to do and asks nothing
        assert await machine.draft("de", target, by="dm") == 0
        assert len(fake.messages.requests) == 1

    async def test_a_lost_tag_keeps_the_english(self, tmp_path: Path) -> None:
        from app.services.translation_targets import Target
        from app.services.translations import ENTRY_FIELDS

        store = TranslationStore(tmp_path)
        data = {"entries": ["Take {@damage 2d6} fire damage.", "Plain words, then prone."]}
        target = Target("ref", "rule", "X|Y", data, ENTRY_FIELDS, "X", ())

        def lossy(request: dict[str, Any]) -> _Reply:
            return _Reply(
                json.dumps(
                    {
                        "translations": [
                            {"id": 0, "text": "Nimm 2W6 Feuerschaden."},
                            {"id": 1, "text": "Schlichte Worte."},
                        ]
                    }
                )
            )

        machine, fake = self._machine(store, lossy)
        assert await machine.draft("de", target, by="dm") == 1
        shown = store.overlay("de", "rule", "X|Y", data)
        assert shown.data["entries"] == ["Take {@damage 2d6} fire damage.", "Schlichte Worte."]
        sent = json.loads(fake.messages.requests[0]["messages"][0]["content"])
        assert sent["glossary"] == {"Prone": "Liegend"}

    async def test_a_refused_or_failed_service_changes_nothing(self, tmp_path: Path) -> None:
        from app.services.i18n import Translatable
        from app.services.translation_targets import Target
        from app.services.translations import ENTRY_FIELDS

        store = TranslationStore(tmp_path)
        target = Target("ref", "rule", "X|Y", {"entries": ["One."]}, ENTRY_FIELDS, "X", ())
        machine, _ = self._machine(store, lambda request: _Reply("", stop="refusal"))
        with pytest.raises(Translatable):
            await machine.draft("de", target, by="dm")
        assert store.record("de", "rule", "X|Y") is None

    def test_batches_split_by_count_and_size(self) -> None:
        from app.services.machine_translation import BATCH_CHARS, BATCH_PASSAGES, _batches

        many = [(f"entries/{i}", "x") for i in range(BATCH_PASSAGES + 1)]
        assert [len(b) for b in _batches(many)] == [BATCH_PASSAGES, 1]
        big = [("a", "x" * (BATCH_CHARS - 10)), ("b", "x" * 20)]
        assert [len(b) for b in _batches(big)] == [1, 1]

    async def test_the_editor_button(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        url = "/gm/translate/ref/spell/PHB/Fireball"
        assert 'value="machine"' not in (await ac.get(url)).text  # no service configured
        machine, _ = self._machine(registry.translations, _german)
        registry.__dict__["machine_translator"] = machine
        assert 'value="machine"' in (await ac.get(url)).text
        done = await ac.post(url, data={"lang": "de", "action": "machine"})
        assert "done=translate.machine_done" in done.headers["location"]
        page = (await ac.get("/ref/spell/PHB/Fireball")).text
        assert "DE: Ein greller" in page and "Maschinelle Übersetzung" in page


class TestOverview:
    async def test_coverage_next_up_and_problems(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        page = (await ac.get("/gm/translations")).text
        assert "Als Nächstes übersetzen" in page and "/gm/translate/ref/" in page
        assert "PARAVENT_TRANSLATE_API_KEY" in page  # no service configured
        data = _fireball(registry.compendium)
        registry.translations.save(
            "de", "spell", "Fireball|PHB", data, {"entries/0": "Eins."}, by="dm"
        )
        # make the stored English fingerprint wrong: the corpus "changed" since
        path = registry.translations.path("de", "spell", "Fireball|PHB")
        raw = json.loads(path.read_text())
        raw["Fireball|PHB"]["passages"]["entries/0"]["en"] = "0" * 16
        path.write_text(json.dumps(raw))
        page = (await ac.get("/gm/translations")).text
        assert "Nach Kategorie" in page and "1 veraltet" in page
        assert "/gm/translate/ref/spell/PHB/Fireball?lang=de&amp;show=todo" in page

    async def test_drafts_are_listed_until_confirmed(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        assert "Keine Entwürfe zum Durchsehen" in (await ac.get("/gm/translations/drafts")).text
        assert "Entwürfe zum Durchsehen" not in (await ac.get("/gm/translations")).text
        data = _fireball(registry.compendium)
        registry.translations.save(
            "de",
            "spell",
            "Fireball|PHB",
            data,
            {"entries/0": "Eins.", "entries/1": "Zwei."},
            by="claude",
            draft=True,
        )
        assert "1 Eintrag enthält Entwürfe" in (await ac.get("/gm/translations")).text
        page = (await ac.get("/gm/translations/drafts")).text
        assert "2 Entwürfe" in page and "claude" in page
        back = quote("/gm/translations/drafts?lang=de", safe="")
        assert f"/gm/translate/ref/spell/PHB/Fireball?lang=de&amp;show=todo&amp;back={back}" in page

        assert "Die Entwürfe von 1 Eintrag bestätigen" in page
        done = await ac.post("/gm/translations/drafts/confirm", data={"lang": "de"})
        assert done.headers["location"] == "/gm/translations/drafts?lang=de&done=2"
        page = (await ac.get(done.headers["location"])).text
        assert "2 Passagen bestätigt" in page and "Keine Entwürfe" in page
        record = registry.translations.record("de", "spell", "Fireball|PHB")
        assert record is not None and not any(p.draft for p in record.passages.values())

    async def test_partly_translated_records_are_listed_and_retried(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        import asyncio

        ac, registry = client
        assert "Nichts ist nur teilweise" in (await ac.get("/gm/translations/unfinished")).text
        data = _fireball(registry.compendium)
        total = len(registry.translations.rows("de", "spell", "Fireball|PHB", data))
        registry.translations.save(
            "de", "spell", "Fireball|PHB", data, {"entries/0": "Eins."}, by="claude", draft=True
        )
        assert "1 Eintrag ist nur teilweise" in (await ac.get("/gm/translations")).text
        page = (await ac.get("/gm/translations/unfinished")).text
        assert f"{total - 1} von {total} offen" in page
        back = quote("/gm/translations/unfinished?lang=de", safe="")
        assert f"/gm/translate/ref/spell/PHB/Fireball?lang=de&amp;show=todo&amp;back={back}" in page
        assert "noch einmal maschinell" not in page  # no service configured

        machine, _fake = TestMachineDrafts()._machine(registry.translations, _german)
        registry.__dict__["machine_translator"] = machine
        registry.__dict__.pop("translation_jobs", None)
        assert "noch einmal maschinell" in (await ac.get("/gm/translations/unfinished")).text
        await ac.post("/gm/translations/jobs", data={"lang": "de", "scope": "unfinished"})
        job = registry.translation_jobs.jobs("de")[0]
        for _ in range(100):
            if job.finished:
                break
            await asyncio.sleep(0.05)
        assert job.finished and job.total == 1 and job.passages == total - 1
        record = registry.translations.record("de", "spell", "Fireball|PHB")
        assert record is not None and record.passages["entries/0"].text == "Eins."
        assert registry.translation_coverage.unfinished("de") == []

    async def test_what_players_read_is_one_scope(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        from app.services.player_view import Reader

        _ac, registry = client
        coverage = registry.translation_coverage
        found = coverage.for_players("de", Reader(frozenset({"PHB"})))
        keys = {(x.category, x.key) for x in found}
        assert ("spell", "Fireball|PHB") in keys
        assert any(c == "classFeature" for c, _ in keys)  # features, which nothing links
        assert any(c == "raceFluff" for c, _ in keys)  # what the wizard shows
        assert not any(c in ("monster", "magicvariant") for c, _ in keys)
        assert all(x.data.get("source") == "PHB" for x in found if x.kind == "ref")
        # a record translated in full drops out
        data = _fireball(registry.compendium)
        rows = registry.translations.rows("de", "spell", "Fireball|PHB", data)
        registry.translations.save(
            "de", "spell", "Fireball|PHB", data, {r.path: "Text." for r in rows}, by="dm"
        )
        again = coverage.for_players("de", Reader(frozenset({"PHB"})))
        assert ("spell", "Fireball|PHB") not in {(x.category, x.key) for x in again}
        label, targets = registry.translation_jobs.scope("de", "players")
        assert label == "players" and targets  # every world's player books

    async def test_a_creature_description_counts_under_its_creature(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        target = registry.translation_targets.fluff("monster", "MM", "Goblin")
        first = registry.translations.rows(
            "de", target.category, target.key, target.data, target.fields
        )[0].path
        registry.translations.save(
            "de", target.category, target.key, target.data, {first: "Eins."}, by="dm"
        )
        assert (await ac.get("/gm/translations")).status_code == 200
        counted = registry.translation_coverage.categories("de")
        fluff = next(c for c in counted if c.category == "monsterFluff")
        assert fluff.translated == 1 and fluff.entries == len(registry.compendium.all("monster"))

    async def test_machine_drafts_in_the_background(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        import asyncio

        ac, registry = client
        machine, fake = TestMachineDrafts()._machine(registry.translations, _german)
        registry.__dict__["machine_translator"] = machine
        registry.__dict__.pop("translation_jobs", None)
        started = await ac.post("/gm/translations/jobs", data={"lang": "de", "scope": "top:2"})
        assert "done=translations.started" in started.headers["location"]
        job = registry.translation_jobs.jobs("de")[0]
        for _ in range(100):
            if job.finished:
                break
            await asyncio.sleep(0.05)
        assert job.finished and job.done == 2 and job.passages > 0
        progress = (await ac.get("/gm/translations/jobs", params={"lang": "de"})).text
        assert "Fertig" in progress and "hx-trigger" not in progress  # stops polling
        assert (
            (await ac.post("/gm/translations/jobs", data={"lang": "de", "scope": "nonsense"}))
            .headers["location"]
            .endswith("error=translations.nothing")
        )


class TestSheetAndItems:
    """Feature texts on the sheet and names of made magic items (TRANSLATION.md §4–5)."""

    def test_made_items_take_composed_names(
        self, tmp_path: Path, loaded_compendium: CompendiumStore
    ) -> None:
        from app.services.glossary import Glossary
        from app.services.item_names import ItemNames

        glossary = Glossary(tmp_path)
        glossary.set("de", "baseitem", "Longsword", "Langschwert")
        glossary.set("de", "baseitem", "Greatsword", "Zweihandschwert")
        names = ItemNames(loaded_compendium)
        german = UiCatalogue().translator("de", glossary, None, names.compose)
        assert german.term("item", "+1 Longsword") == "Langschwert +1"
        assert german.term("item", "Flame Tongue Greatsword") is None  # no name for the variant
        glossary.set("de", "magicvariant", "Flame Tongue", "Flammenzunge")
        assert german.term("item", "Flame Tongue Greatsword") == "Flammenzunge (Zweihandschwert)"
        assert german.terms()("item", "+1 Longsword") == "Langschwert +1"
        assert (
            UiCatalogue()
            .translator("en", glossary, None, names.compose)
            .term("item", "+1 Longsword")
            is None
        )

    async def test_features_on_the_sheet_read_their_translation(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        from app.models.pc import PlayerCharacter

        ac, registry = client
        example = (
            Path(__file__).resolve().parent.parent
            / "documentation"
            / "examples"
            / "brunhild-ironvow.json"
        )
        brunhild = registry.gamestate.characters.create(
            PlayerCharacter.model_validate(json.loads(example.read_text(encoding="utf-8")))
        )
        sense = registry.compendium.get("classFeature", "divine sense|phb|paladin|1")
        sense_data = sense.model_dump(by_alias=True, exclude_none=True)
        registry.translations.save(
            "de",
            "classFeature",
            "Divine Sense|PHB|Paladin|1",
            sense_data,
            {"entries/0": "Die Anwesenheit großen Übels nimmst du wahr."},
            by="dm",
        )
        dwarf = registry.compendium.get("race", "dwarf|phb").model_dump(
            by_alias=True, exclude_none=True
        )
        registry.translations.save(
            "de",
            "race",
            "Dwarf|PHB",
            dwarf,
            {
                "entries/4/name": "Zwergische Unverwüstlichkeit",
                "entries/4/entries/0": "Du bist im Vorteil bei Rettungswürfen gegen Gift.",
            },
            by="dm",
        )
        sheet = (await ac.get(f"/characters/{brunhild.id}")).text
        assert "Die Anwesenheit großen Übels nimmst du wahr." in sheet
        assert "Zwergische Unverwüstlichkeit" in sheet and "gegen Gift." in sheet
        # the gamemaster reaches each feature's editor from the sheet
        assert "/gm/translate/feature/classFeature/" in sheet
        assert "/gm/translate/ref/race/PHB/Dwarf" in sheet
        editor = await ac.get(
            "/gm/translate/feature/classFeature/Divine%20Sense%7CPHB%7CPaladin%7C1"
        )
        assert editor.status_code == 200 and "Die Anwesenheit großen Übels" in editor.text
        assert (await ac.get("/gm/translate/feature/classFeature/nope")).status_code == 404
