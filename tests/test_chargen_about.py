"""What the books say about an option, on the wizard's cards (CHARGEN-WIZARD.md §8)."""

from __future__ import annotations

from httpx import AsyncClient

from app.models.gamestate import SourceSet
from app.services.chargen import Offer
from app.services.chargen.catalogue import Catalogue
from app.store.fluff import FluffStore
from app.store.registry import StoreRegistry
from app.ui.translate_pages import translate_url
from tests.test_chargen import BOOKS_2014, HTMX, PASSWORD, client  # noqa: F401 - the fixture


def _catalogue(registry: StoreRegistry, books: list[str]) -> Catalogue:
    books_set = SourceSet(sources=books)
    return Catalogue(registry.compendium, books_set, registry.lookups, registry.fluff)


class TestAbout:
    async def test_each_kind_finds_its_text(
        self,
        client: tuple[AsyncClient, StoreRegistry],  # noqa: F811
    ) -> None:
        _, registry = client
        old = _catalogue(registry, BOOKS_2014)
        assert old.about("class", old.find("class", "Wizard|PHB")).entries
        assert old.about("race", old.find("race", "Dwarf|PHB")).category == "raceFluff"
        assert old.about("background", old.find("background", "Acolyte|PHB")).entries

    async def test_a_subrace_is_filed_as_a_race(
        self,
        client: tuple[AsyncClient, StoreRegistry],  # noqa: F811
    ) -> None:
        _, registry = client
        cat = _catalogue(registry, BOOKS_2014)
        hill = next(o for o in cat.rows("subrace") if o.name == "Hill" and o.source == "PHB")
        about = cat.about("subrace", hill)
        assert about is not None and about.key == "Dwarf (Hill)|PHB"

    async def test_a_subclass_needs_its_class(
        self,
        client: tuple[AsyncClient, StoreRegistry],  # noqa: F811
    ) -> None:
        _, registry = client
        cat = _catalogue(registry, BOOKS_2014 + ["XGE", "TCE", "SCAG"])
        found = [o for o in cat.rows("subclass") if cat.about("subclass", o) is not None]
        assert found

    def test_nothing_without_the_store(self) -> None:
        nobody = Offer("Nobody", "PHB", {})
        assert Catalogue(None, fluff=FluffStore()).about("race", nobody) is None
        assert Catalogue(None).about("race", nobody) is None


class TestCards:
    async def test_a_line_on_each_card_and_all_of_it_when_chosen(
        self,
        client: tuple[AsyncClient, StoreRegistry],  # noqa: F811
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        draft = (await ac.post("/chargen", data={"world": world.id})).headers["location"]
        step = f"/chargen/{draft.split('/')[2]}/class"
        before = (await ac.get(step)).text
        assert ">Fighters rule many battlefields" in before  # without the books' heading
        assert 'class="cg-about"' not in before  # nothing chosen yet
        chosen = (await ac.post(step, data={"class": "Fighter|XPHB"}, headers=HTMX)).text
        assert 'class="cg-about"' in chosen and "About Fighter" in chosen
        assert "/ref/class/XPHB/Fighter" in chosen


async def _as_gamemaster(ac: AsyncClient) -> None:
    await ac.post("/logout")
    await ac.post("/login", data={"username": "dm", "password": PASSWORD})


class TestTranslating:
    async def test_a_subclass_is_translated_under_its_class(
        self,
        client: tuple[AsyncClient, StoreRegistry],  # noqa: F811
    ) -> None:
        ac, registry = client
        cat = _catalogue(registry, BOOKS_2014 + ["XGE", "TCE", "SCAG"])
        offer, about = next(
            (o, a)
            for o in cat.rows("subclass")
            if (a := cat.about("subclass", o)) is not None and a.entries
        )
        assert about.key.count("|") == 3 and about.where[0] == "subclass"
        target = registry.translation_targets.fluff(*about.where)
        assert (target.category, target.key) == (about.category, about.key)
        url = translate_url("fluff", *about.where)
        assert (await ac.get(url)).status_code == 403  # a player does not translate
        await _as_gamemaster(ac)
        page = await ac.get(url)
        assert page.status_code == 200 and offer.name in page.text
        row = registry.translations.rows(
            "de", target.category, target.key, target.data, target.fields
        )
        await ac.post(url, data={"lang": "de", f"p:{row[0].path}": "Deutsch"})
        kept = registry.translations.record("de", about.category, about.key)
        assert kept is not None
        assert registry.translation_coverage._entry_target(about.category, about.key) is not None

    async def test_a_subspecies_opens_its_editor_and_comes_back(
        self,
        client: tuple[AsyncClient, StoreRegistry],  # noqa: F811
    ) -> None:
        ac, registry = client
        cat = _catalogue(registry, BOOKS_2014)
        about = next(
            a
            for o in cat.rows("subrace")
            if (a := cat.about("subrace", o)) is not None and a.entries
        )
        url = translate_url("fluff", *about.where, back="/chargen/x/species")
        await _as_gamemaster(ac)
        page = await ac.get(url)
        assert page.status_code == 200 and "/chargen/x/species" in page.text

    async def test_the_chosen_card_offers_the_gamemaster_the_editor(
        self,
        client: tuple[AsyncClient, StoreRegistry],  # noqa: F811
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        draft = (await ac.post("/chargen", data={"world": world.id})).headers["location"]
        step = f"/chargen/{draft.split('/')[2]}/class"
        chosen = (await ac.post(step, data={"class": "Fighter|XPHB"}, headers=HTMX)).text
        assert "/gm/translate/fluff/" not in chosen
        await _as_gamemaster(ac)
        draft = (await ac.post("/chargen", data={"world": world.id})).headers["location"]
        step = f"/chargen/{draft.split('/')[2]}/class"
        chosen = (await ac.post(step, data={"class": "Fighter|XPHB"}, headers=HTMX)).text
        assert "/gm/translate/fluff/class/XPHB/Fighter" in chosen

    async def test_the_popups_full_editor_returns_to_the_page_beneath(
        self,
        client: tuple[AsyncClient, StoreRegistry],  # noqa: F811
    ) -> None:
        ac, _ = client
        await _as_gamemaster(ac)
        back = "/gm/combat/abc?sel=x"
        url = translate_url("ref", "monster", "MM", "Goblin", lang="de", embed="1", back=back)
        popup = await ac.post(url, data={"action": "machine", "lang": "de"})
        wanted = translate_url("ref", "monster", "MM", "Goblin", lang="de", back=back)
        assert wanted.replace("&", "&amp;") in popup.text
        assert 'target="_blank"' not in popup.text
