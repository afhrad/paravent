"""The {@tag} renderer: parsing, inline rendering, entries, and the reference page."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from htpy import div
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role, SourceSet, World
from app.models.pc import PlayerCharacter
from app.store.compendium import CompendiumStore
from app.store.registry import StoreRegistry
from app.ui.tags import RenderContext, Tag, catalogue, parse, plain, render, render_entries
from tests.conftest import log_about, requires_corpus


def html(nodes: object) -> str:
    return str(div[nodes])


class TestParse:
    def test_plain_text_and_tags(self) -> None:
        assert parse("A {@spell fireball} here") == ["A ", Tag("spell", ("fireball",)), " here"]

    def test_slots(self) -> None:
        assert parse("{@creature troll||trolls}") == [Tag("creature", ("troll", "", "trolls"))]

    def test_nested_tags_stay_whole(self) -> None:
        [tag] = parse("{@note see {@spell fireball|PHB} or {@condition prone}}")
        assert tag == Tag("note", ("see {@spell fireball|PHB} or {@condition prone}",))

    def test_pipes_inside_nested_tags_do_not_split(self) -> None:
        [tag] = parse("{@footnote x|{@note a|b}}")
        assert tag.parts == ("x", "{@note a|b}")

    def test_no_payload(self) -> None:
        assert parse("{@h}5 damage") == [Tag("h", ()), "5 damage"]

    def test_unterminated_is_left_as_text(self) -> None:
        assert parse("broken {@spell fire") == ["broken {@spell fire"]

    def test_unit_braces_inside_a_tag(self) -> None:
        [tag] = parse("{@unit {=amount1/v}|egg|eggs}")
        assert tag.part(0) == "{=amount1/v}"


class TestPlain:
    @pytest.mark.parametrize(
        ("raw", "text"),
        [
            (
                "{@atk mw} {@hit 4} to hit. {@h}5 ({@damage 1d6 + 2}) slashing.",
                "Melee Weapon Attack: +4 to hit. Hit: 5 (1d6 + 2) slashing.",
            ),
            (
                "{@actSave dex} {@dc 13}. {@actSaveFail} The target",
                "Dexterity Saving Throw: DC 13. Failure: The target",
            ),
            ("{@creature troll||trolls}", "trolls"),
            ("{@deity Bahamut|Nonhuman|PHB}", "Bahamut"),
            ("{@b bold {@i nested}}", "bold nested"),
        ],
    )
    def test_display_text(self, raw: str, text: str) -> None:
        assert plain(raw) == text


class TestInline:
    def test_no_raw_brace_ever(self) -> None:
        out = html(render("{@b a} {@unknownTag x|y} {@spell fireball} {@5etools Items|items.html}"))
        assert "{@" not in out and "}" not in out.replace("{", "")

    def test_shorthand_and_attack(self) -> None:
        out = html(render("{@atk mw} {@hit 4} to hit. {@h}5"))
        assert "<em>Melee Weapon Attack:</em> " in out and "<em>Hit:</em> 5" in out

    def test_roll_chips_with_a_roll_form(self) -> None:
        out = html(render("{@hit 4} and {@damage 2d6 + 3}", RenderContext(roll_form="r")))
        assert 'form="r" name="expr" value="1d20+4"' in out
        assert 'value="2d6+3"' in out

    def test_roll_chips_without_a_form_roll_locally(self) -> None:
        assert 'data-roll="1d6"' in html(render("{@recharge 5}"))
        assert "(Recharge 5–6)" in html(render("{@recharge 5}"))

    def test_dc_is_a_static_chip(self) -> None:
        out = html(render("{@dc 15}", RenderContext(roll_form="r")))
        assert "DC 15" in out and "<button" not in out

    def test_colour_maps_to_a_token(self) -> None:
        assert 'class="tag-color dmg"' in html(render("{@color ♦|#ff0000}"))

    def test_external_links_open_safely(self) -> None:
        out = html(render("{@link Rules|https://example.com}"))
        assert 'target="_blank" rel="noopener noreferrer"' in out

    def test_entities_without_a_resolver_are_inert(self) -> None:
        out = html(render("{@spell fireball}"))
        assert "ref spell inert" in out and "<a " not in out

    def test_every_catalogued_tag_renders(self) -> None:
        for name, entry in catalogue().items():
            for example in entry.get("examples", []):
                out = html(render(example))
                assert "{@" not in out, (name, example)


class TestEntries:
    def test_the_books_boxes_match_the_callouts(self) -> None:
        """A variant reads "Variant: …", a pull quote and a flowchart keep their shape,
        as the Markdown callouts of the same kind do."""
        out = html(
            render_entries(
                [
                    {"type": "variant", "name": "Encumbrance", "entries": ["Carry less."]},
                    {"type": "quote", "style": "quote-pull", "entries": ["Run."], "by": "Volo"},
                    {
                        "type": "flowchart",
                        "blocks": [
                            {"type": "flowBlock", "name": "Start", "entries": ["Roll."]},
                            {"type": "flowBlock", "name": "End", "entries": ["Done."]},
                        ],
                    },
                ]
            )
        )
        assert '<aside class="entry-inset variant"><h4>Variant: Encumbrance</h4>' in out
        assert '<blockquote class="entry-quote pull">' in out
        assert out.count('class="entry-flowblock"') == 2 and 'class="entry-flowchart"' in out
        assert "<h4>Start</h4>" in out

    def test_named_traits_lead_their_paragraph(self) -> None:
        out = html(
            render_entries([{"type": "entries", "name": "Pack Tactics", "entries": ["Advantage."]}])
        )
        assert '<p><strong class="entry-name"><em>Pack Tactics.</em> </strong>Advantage.</p>' in out

    def test_lists_tables_and_insets(self) -> None:
        out = html(
            render_entries(
                [
                    {
                        "type": "list",
                        "items": ["One", {"type": "item", "name": "Two.", "entries": ["Second"]}],
                    },
                    {
                        "type": "table",
                        "caption": "Wild Magic",
                        "colLabels": ["d4", "Effect"],
                        "rows": [
                            [{"type": "cell", "roll": {"min": 1, "max": 2}}, "Sparks"],
                            ["3", "{@spell fireball}"],
                        ],
                    },
                    {"type": "inset", "name": "Variant", "entries": ["Text"]},
                    {"type": "insetReadaloud", "entries": ["Read this aloud."]},
                ]
            )
        )
        assert "<li>One</li>" in out and "<strong>Two. </strong>Second" in out
        assert "<caption>Wild Magic</caption>" in out and "<td>1–2</td>" in out
        assert 'class="entry-inset"' in out and "entry-inset readaloud" in out

    def test_unknown_types_keep_their_text(self) -> None:
        out = html(
            render_entries(
                [{"type": "image", "title": "A map"}, {"type": "mystery", "entries": ["kept"]}]
            )
        )
        assert "A map" in out and "kept" in out


@requires_corpus
class TestAgainstTheCorpus:
    def test_resolved_links_and_both_missing_cases(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        ctx = RenderContext(
            resolver=loaded_compendium.resolver(),
            source_set=SourceSet(sources=["PHB", "MM"]),
            world_id="w1",
        )
        out = html(
            render(
                "{@spell fireball} {@spell mind sliver} {@creature goblin} {@spell no such spell}",
                ctx,
            )
        )
        assert 'href="/ref/spell/PHB/Fireball?world=w1"' in out
        assert 'href="/ref/monster/MM/Goblin?world=w1"' in out
        assert "is not among this world&#39;s books" in out  # mind sliver: TCE/XPHB only
        assert "is not in your imported data" in out

    def test_a_german_label_keeps_the_english_target_and_the_books(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        """TRANSLATION.md §3: the link resolves in English, in the world's books; only the
        label is the glossary's."""
        german = {("spell", "fireball"): "Feuerball", ("spell", "mind sliver"): "Gedankensplitter"}

        def term(category: str, name: str) -> str | None:
            return german.get((category, name.lower()))

        prose = "Ein {@spell fireball} trifft, dann {@spell mind sliver}."

        def rendered(*books: str) -> str:
            ctx = RenderContext(
                resolver=loaded_compendium.resolver(),
                source_set=SourceSet(sources=list(books)),
                term=term,
            )
            return html(render(prose, ctx))

        old = rendered("PHB", "MM")
        assert 'href="/ref/spell/PHB/Fireball"' in old and ">Feuerball<" in old
        # Mind Sliver is in no declared book: not linked, even with a German name
        assert "is not among this world&#39;s books" in old
        assert "/ref/spell/TCE/Mind%20Sliver" not in old and "/ref/spell/XPHB/" not in old
        new = rendered("XPHB")
        assert 'href="/ref/spell/XPHB/Fireball"' in new and ">Feuerball<" in new
        assert "/ref/spell/XPHB/Mind" in new and ">Gedankensplitter<" in new

    def test_goblin_actions_get_chips(self, loaded_compendium: CompendiumStore) -> None:
        goblin = loaded_compendium.find("monster", "Goblin", "MM")
        assert goblin is not None
        data = goblin.model_dump(by_alias=True, exclude_none=True)
        out = html(render_entries(data["action"], RenderContext(roll_form="r")))
        assert "Scimitar." in out and 'value="1d20+4"' in out and 'value="1d6+2"' in out

    def test_a_corpus_sample_renders_without_raw_tags(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        ctx = RenderContext(resolver=loaded_compendium.resolver())
        for category in ("monster", "spell", "item", "feat", "race", "background", "classFeature"):
            for entry in loaded_compendium.all(category)[:150]:
                data = entry.model_dump(by_alias=True, exclude_none=True)
                for key in ("entries", "action", "trait"):
                    if data.get(key):
                        out = html(render_entries(data[key], ctx))
                        assert "{@" not in out, (category, data.get("name"))


# -- the reference page ------------------------------------------------------------------

PASSWORD = "correct horse"


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
        await ac.post("/login", data={"username": "alice", "password": PASSWORD})
        yield ac, registry


async def _as_gamemaster(ac: AsyncClient) -> None:
    await ac.post("/logout")
    await ac.post("/login", data={"username": "dm", "password": PASSWORD})


@requires_corpus
class TestReferencePage:
    async def test_a_creature_is_a_statblock_with_chips(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _ = client
        await _as_gamemaster(ac)  # creatures are the gamemaster's (PLAYER-VIEW.md)
        text = (await ac.get("/ref/monster/MM/Goblin")).text
        assert "Small humanoid (goblinoid), neutral evil" in text
        assert "Armor Class" in text and "15 (" in text and "leather armor" in text
        assert "1/4 (50 XP)" in text
        assert 'data-roll="1d20+4"' in text and 'data-roll="2d6"' in text  # attack and HP formula
        assert "/static/js/roll.js" in text

    async def test_legendary_actions_get_their_standard_header(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _ = client
        await _as_gamemaster(ac)  # creatures are the gamemaster's (PLAYER-VIEW.md)
        text = (await ac.get("/ref/monster/MM/Adult%20Red%20Dragon")).text
        assert "The adult red dragon can take 3 legendary actions" in text

    async def test_a_spell_has_its_header(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, _ = client
        text = (await ac.get("/ref/spell/PHB/Fireball")).text
        assert "Casting Time" in text and "150 ft" in text and "Instant" in text
        assert 'data-roll="8d6"' in text

    async def test_links_stay_in_the_world(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(
            World(name="W", source_set=SourceSet(sources=["XPHB", "XMM"]))
        )
        text = (await ac.get(f"/ref/spell/XPHB/Fireball?world={world.id}")).text
        assert f"?world={world.id}" in text or "ref missing" in text

    async def test_german_labels(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        await _as_gamemaster(ac)  # creatures are the gamemaster's (PLAYER-VIEW.md)
        dm = registry.auth.find_user("dm")
        assert dm is not None
        registry.auth.set_language(dm.id, "de")
        text = (await ac.get("/ref/monster/MM/Goblin")).text
        assert "Rüstungsklasse" in text and "Herausforderungsgrad" in text

    async def test_unknown_entry_is_404(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, _ = client
        assert (await ac.get("/ref/spell/PHB/No%20Such%20Spell")).status_code == 404
        assert (await ac.get("/ref/nonsense/PHB/x")).status_code == 404

    async def test_login_required(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, _ = client
        await ac.post("/logout")
        assert (await ac.get("/ref/spell/PHB/Fireball")).status_code == 303

    async def test_sheet_text_links_and_rolls_into_the_character(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, registry = client
        pc = registry.gamestate.characters.create(
            PlayerCharacter.model_validate(
                {
                    "name": "Lyra",
                    "player": "alice",
                    "classes": [{"class": "Wizard|PHB", "level": 5}],
                    "spells": {"prepared": ["Fireball|PHB"]},
                }
            )
        )
        text = (await ac.get(f"/characters/{pc.id}")).text
        assert f'<form id="roll-{pc.id}"' in text
        assert f'form="roll-{pc.id}" name="expr" value="8d6"' in text
        assert 'href="/ref/spell/PHB/Fireball"' in text
        response = await ac.post(
            f"/characters/{pc.id}/act/roll",
            data={"kind": "custom", "expr": "8d6"},
            headers={"HX-Request": "true"},
        )
        assert response.status_code == 200
        assert log_about(registry, pc.id)[0].what == "custom:8d6"


@requires_corpus
class TestPreviews:
    """The tooltip / bottom-sheet fragment: a glance per type, nothing that chains."""

    async def test_creature(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, _ = client
        await _as_gamemaster(ac)  # creatures are the gamemaster's (PLAYER-VIEW.md)
        text = (await ac.get("/ref/monster/MM/Goblin/preview")).text
        assert text.startswith('<div class="tip-card tip-monster"')
        assert "Small humanoid (goblinoid), neutral evil" in text and "1/4 (50 XP)" in text
        assert ">15<" in text and ">7<" in text  # AC and HP
        assert "STR" in text and "14 (+2)" in text  # the abilities

    async def test_spell_is_its_header_and_first_sentence(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _ = client
        text = (await ac.get("/ref/spell/PHB/Fireball/preview")).text
        assert "L3 Evocation" in text and "150 ft" in text
        assert "A bright streak flashes from your pointing finger" in text
        assert "The fire spreads around corners" not in text  # a glance, not the entry

    async def test_item(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, _ = client
        text = (await ac.get("/ref/baseitem/PHB/Longsword/preview")).text
        assert (
            "Melee weapon" in text
            and "1d8 slashing" in text
            and "3 lb." in text
            and "15 gp" in text
        )

    async def test_a_condition_is_shown_whole(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _ = client
        text = (await ac.get("/ref/condition/PHB/Prone/preview")).text
        assert "only movement option is to crawl" in text and "within 5 feet" in text

    async def test_nothing_in_a_preview_chains_or_rolls(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _ = client
        await _as_gamemaster(ac)  # creatures are the gamemaster's (PLAYER-VIEW.md)
        for url in (
            "/ref/monster/MM/Adult%20Red%20Dragon/preview",
            "/ref/condition/PHB/Paralyzed/preview",
        ):
            text = (await ac.get(url)).text
            body, foot = text.split('class="tip-body"', 1)[1].split('class="tip-foot"', 1)
            assert '<a href="/ref/' not in body  # references inside the preview are text
            assert "tip-open" in foot  # "Open" (and any other printing) is the way on
            assert "<button" not in text and "data-roll" not in text

    async def test_open_keeps_the_world(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.create(World(name="W"))
        text = (await ac.get(f"/ref/spell/PHB/Fireball/preview?world={world.id}")).text
        assert f'href="/ref/spell/PHB/Fireball?world={world.id}"' in text
        assert 'data-close="Close"' in text

    async def test_unknown_and_logged_out(self, client: tuple[AsyncClient, StoreRegistry]) -> None:
        ac, _ = client
        assert (await ac.get("/ref/spell/PHB/Nope/preview")).status_code == 404
        await ac.post("/logout")
        assert (await ac.get("/ref/spell/PHB/Fireball/preview")).status_code == 303

    async def test_the_script_is_on_every_page(
        self, client: tuple[AsyncClient, StoreRegistry]
    ) -> None:
        ac, _ = client
        assert "/static/js/tooltips.js" in (await ac.get("/play")).text
