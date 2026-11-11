"""The naming workshop: the form, raw text, samples, rejecting, a world's choice, the
wizard's suggestion and what Claude receives (documentation/NAMES.md §5)."""

from __future__ import annotations

import html
import re
from typing import Any

import httpx
import pytest

from app.models.gamestate import GivenName, Npc, Scope, World
from app.models.naming import ElementsPart, KinPart, LearnedPart, ListPart, NamingStyle, SoundsPart
from app.services.dice import Dice
from app.services.naming import FoundStyle, NameMaker, Naming, cluster, refused, sample_notes
from app.services.naming_workshop import (
    PartFields,
    StyleError,
    StyleFields,
    fields_of,
    races,
    raw_text,
    search,
    style_from,
    style_from_raw,
)
from app.store.npc_tables import NpcTables
from app.store.registry import StoreRegistry
from tests.test_chargen import HTMX, client  # noqa: F401 - the fixture
from tests.test_mcp import PASSWORD, call, session, table  # noqa: F401 - the fixture

Table = tuple[httpx.AsyncClient, Any]


def _style(*names: str, **extra: Any) -> NamingStyle:
    return NamingStyle(
        name="Test", pattern="{given}", parts={"given": ListPart(names=list(names))}, **extra
    )


def _fields(**changes: Any) -> StyleFields:
    fields = StyleFields(
        name="Vale",
        pattern="{given} {link}",
        parts=[
            PartFields(key="given", female="Ana, Bea\nCora", male="Dan"),
            PartFields(key="link", kind="by_gender", fixed_female="dotter", fixed_male="son"),
            PartFields(),
        ],
    )
    for key, value in changes.items():
        setattr(fields, key, value)
    return fields


class TestFormAndRaw:
    def test_a_form_makes_a_style_and_shows_it_again(self) -> None:
        style = style_from(_fields(rewrites="anason = anson\n"))
        assert style.parts["given"].female == ["Ana", "Bea", "Cora"]  # type: ignore[union-attr]
        assert style.rewrites == {"anason": "anson"}
        again = fields_of(style)
        assert style_from(again).parts == style.parts
        assert again.rewrites == "anason = anson"

    @pytest.mark.parametrize(
        ("changes", "key"),
        [
            ({"name": " "}, "naming.error.name"),
            ({"pattern": "plain"}, "naming.error.pattern"),
            ({"pattern": "{nobody}"}, "naming.error.unknown_part"),
            ({"pattern": "{given:shout}"}, "naming.error.modifier"),
            ({"rewrites": "no equals"}, "naming.error.rewrite"),
            ({"pattern": "{given}", "parts": [PartFields(key="given")]}, "naming.error.empty_part"),
            ({"parts": [PartFields(key="a b", names="x")]}, "naming.error.part_key"),
            (
                {"parts": [PartFields(key="a", names="x"), PartFields(key="a", names="y")]},
                "naming.error.part_twice",
            ),
            (
                {"pattern": "{a}", "parts": [PartFields(key="a", kind="elements", second="x")]},
                "naming.error.empty_part",
            ),
        ],
    )
    def test_what_will_not_do_says_why(self, changes: dict[str, Any], key: str) -> None:
        with pytest.raises(StyleError) as caught:
            style_from(_fields(**changes))
        assert caught.value.key == key

    def test_an_edit_keeps_identity_and_rejected_names(self) -> None:
        base = style_from(_fields()).model_copy(update={"rejected": ["Ana son"]})
        edited = style_from(_fields(name="New"), base=base)
        assert (edited.id, edited.rejected, edited.name) == (base.id, ["Ana son"], "New")

    def test_raw_round_trip_ignores_a_pasted_id(self) -> None:
        base = _style("Ana", rejected=["Bo"])
        text = raw_text(base)
        assert '"id"' not in text
        again = style_from_raw(text, base=base)
        assert again.model_dump(exclude={"updated_at"}) == base.model_dump(exclude={"updated_at"})
        pasted = style_from_raw(text.replace("{", '{"id": "evil", ', 1), base=base)
        assert pasted.id == base.id

    @pytest.mark.parametrize(
        ("text", "key"),
        [
            ("{not json", "naming.error.json"),
            ("[]", "naming.error.format"),
            ('{"name": "x", "pattern": "{a}", "parts": {}, "extra": 1}', "naming.error.format"),
            (
                '{"name": "x", "pattern": "{a}", "parts": {"a": {"kind": "list"}}}',
                "naming.error.empty_part",
            ),
        ],
    )
    def test_raw_refusals(self, text: str, key: str) -> None:
        with pytest.raises(StyleError) as caught:
            style_from_raw(text)
        assert caught.value.key == key


class TestNaming:
    def test_a_rejected_name_is_never_made(self) -> None:
        naming = Naming(NpcTables(None), lambda: [_style("Ana", "Bo", rejected=["ANA"], id="x")])
        assert naming.names("own:x", count=5) == ["Bo"]
        assert {naming.name("own:x") for _ in range(20)} == {"Bo"}

    def test_samples_of_an_unsaved_style(self) -> None:
        naming = Naming(NpcTables(None))
        assert sorted(naming.sample(_style("Ana", "Bo", "Cy"))) == ["Ana", "Bo", "Cy"]

    def test_a_starter_has_its_race_and_culture(self) -> None:
        entry = Naming(NpcTables(None)).entry("starter:old-norse")
        assert (entry.race, entry.culture) == ("Human", "Old Norse")

    def test_a_world_file_from_before_still_loads(self) -> None:
        assert World.model_validate({"name": "Azeroth", "naming": {"default": "x"}})


def _people() -> Naming:
    styles = [
        _style("Mu", id="t1", race="Tauren", culture="Shu'halo"),
        _style("Ba", id="t2", race="Tauren", culture="Grimtotem"),
        _style("Sa", id="e1", race="Blood Elf", culture="Sin'dorei"),
        _style("Dr", id="draft", race="Draenei", draft=True),
    ]
    return Naming(NpcTables(None), lambda: styles)


class TestFinding:
    def test_race_and_culture(self) -> None:
        assert _people().find("tauren", "grimtotem") == FoundStyle("own:t2")

    def test_a_race_rolls_among_its_cultures(self) -> None:
        naming = _people()
        found = naming.find("Tauren")
        assert found == FoundStyle("race:Tauren")
        assert set(naming.names(found.key, count=2)) == {"Mu", "Ba"}

    def test_a_culture_alone(self) -> None:
        assert _people().find("", "Sin'dorei") == FoundStyle("own:e1")

    def test_an_unknown_culture_falls_back_to_the_race(self) -> None:
        assert _people().find("Tauren", "Nobody") == FoundStyle("race:Tauren", fallback=True)

    def test_a_race_with_another_races_culture_is_marked(self) -> None:
        assert _people().find("Orc", "Sin'dorei") == FoundStyle("own:e1", fallback=True)

    def test_nothing_defined_falls_back_to_the_default(self) -> None:
        naming = _people()
        found = naming.find("Murloc")
        assert found.fallback and found.key == naming.default_key("2014")
        assert naming.names(found.key)

    def test_asking_for_nothing_is_no_fallback(self) -> None:
        assert not _people().find().fallback

    def test_a_draft_serves_nobody(self) -> None:
        assert _people().find("Draenei").fallback

    def test_an_alias_serves_a_species(self) -> None:
        own = _style("Murga", id="z", race="Zwerg", culture="Eisenschmiede", aliases=["Dwarf"])
        naming = Naming(NpcTables(None), lambda: [own])
        assert naming.find("dwarf") == FoundStyle("race:Zwerg")
        assert naming.find("Dwarf", "Eisenschmiede") == FoundStyle("own:z")
        assert naming.names(naming.find("Dwarf").key) == ["Murga"]
        assert search(naming.entries(), race="Dwarf")[0].key == "own:z"

    def test_the_tables_own_style_beats_the_books_for_an_alias(self) -> None:
        own = _style("Murga", id="z", race="Zwerg", aliases=["Human"])
        naming = Naming(NpcTables(None), lambda: [own])
        assert naming.entry("starter:tamil").race == "Human"
        assert naming.find("Human") == FoundStyle("race:Zwerg")

    def test_an_alias_round_trips_the_form(self) -> None:
        style = style_from(_fields(aliases="Dwarf, Dwarves"))
        assert style.aliases == ["Dwarf", "Dwarves"]
        assert fields_of(style).aliases == "Dwarf, Dwarves"

    def test_search_and_the_index(self) -> None:
        entries = _people().entries()
        assert [e.key for e in search(entries, "grim tauren")] == ["own:t2"]
        assert [e.key for e in search(entries, race="blood elf")] == ["own:e1"]
        tauren = next(g for g in races(entries) if g.race == "Tauren")
        assert tauren.styles == 2 and set(tauren.cultures) == {"Shu'halo", "Grimtotem"}


def _sounds(**extra: Any) -> NamingStyle:
    part = SoundsPart(
        groups={"C": ["k", "th"], "V": ["a"]},
        syllables=["CV", "CVn"],
        **extra,
    )
    return NamingStyle(name="Sound", pattern="{given}", parts={"given": part})


class TestSounds:
    def test_templates_groups_and_capitals(self) -> None:
        maker = NameMaker(Dice())
        for _ in range(50):
            name = maker.make(_sounds())
            assert name[0].isupper()
            rest = name.lower().replace("th", "t").replace("k", "t").replace("n", "")
            assert rest in ("tata", "tatata")

    def test_syllable_counts(self) -> None:
        maker = NameMaker(Dice())
        lengths = {
            len(maker.make(_sounds(min_syllables=1, max_syllables=1)).lower().replace("th", "k"))
            for _ in range(30)
        }
        assert lengths <= {2, 3}

    def test_endings_follow_the_person(self) -> None:
        style = _sounds(ending_female=["ra"], ending_male=["ros"])
        maker = NameMaker(Dice())
        assert maker.make(style, "female").endswith("ra")
        assert maker.make(style, "male").endswith("ros")

    def test_a_form_round_trip(self) -> None:
        fields = fields_of(_sounds(max_syllables=4, ending_female=["ra"]))
        assert fields.parts[0].groups == "C = k th\nV = a"
        again = style_from(fields)
        assert again.parts == _sounds(max_syllables=4, ending_female=["ra"]).parts

    @pytest.mark.parametrize(
        ("row", "key"),
        [
            ({"groups": "C = k", "syllables": "CV"}, "naming.error.no_group"),
            ({"groups": "c = k", "syllables": "c"}, "naming.error.group"),
            ({"groups": "C = k\nV =", "syllables": "CV"}, "naming.error.empty_group"),
            ({"groups": "C = k", "syllables": ""}, "naming.error.empty_part"),
            (
                {"groups": "C = k", "syllables": "C", "min_syllables": "4", "max_syllables": "2"},
                "naming.error.syllables",
            ),
            ({"groups": "C = k", "syllables": "C", "min_syllables": "x"}, "naming.error.number"),
        ],
    )
    def test_refusals(self, row: dict[str, str], key: str) -> None:
        parts = [PartFields(key="given", kind="sounds", **row)]
        with pytest.raises(StyleError) as caught:
            style_from(_fields(pattern="{given}", parts=parts))
        assert caught.value.key == key


class TestChecks:
    @pytest.mark.parametrize(
        ("name", "most"),
        [("Thorstein", 3), ("Mary-Beth", 2), ("Ka'thra", 3), ("Ślęzak", 2), ("Aaala", 9)],
    )
    def test_clusters(self, name: str, most: int) -> None:
        assert cluster(name) == most

    def test_banned_words_and_clusters_are_rolled_again(self) -> None:
        style = _style("Shitra", "Kstrna", "Ana", banned=["SHIT"], max_cluster=3)
        assert refused(style, "Shitra") and refused(style, "Kstrna")
        naming = Naming(NpcTables(None))
        assert naming.sample(style) == ["Ana"]

    def test_spread_starts_differently_then_gives_in(self) -> None:
        naming = Naming(NpcTables(None), lambda: [_style("Ana", "Abe", "Bo", "Bea", "Cy", id="x")])
        three = naming.names("own:x", count=3, spread=True)
        assert len({n[0] for n in three}) == 3
        assert len(naming.names("own:x", count=5, spread=True)) == 5

    def test_notes(self) -> None:
        assert sample_notes(["Ana"], 30)[0][0] == "naming.check.runs_out"
        alike = sample_notes(["Ab", "Ac", "Ad", "Ae", "Af", "Ag", "Bo", "Co", "Do", "Eo"], 10)
        assert alike == [("naming.check.alike", {"letter": "A", "count": "6"})]
        assert sample_notes(["Ab", "Bo", "Co"], 3) == []


LEARNED = [
    "Aldara",
    "Belira",
    "Calista",
    "Daria",
    "Elira",
    "Faelira",
    "Galira",
    "Halara",
    "Ilara",
    "Jalira",
    "Kalara",
    "Lirana",
]


def _kin() -> NamingStyle:
    return NamingStyle(
        name="Kin",
        pattern="{given} {father}son",
        parts={
            "given": ListPart(names=["Arn", "Bjorn", "Carl"]),
            "father": KinPart(part="given", fallback="invented"),
            "invented": ListPart(names=["Dag"]),
        },
        id="kin",
    )


class TestLearned:
    def test_new_words_that_sound_alike(self) -> None:
        part = LearnedPart(names=LEARNED)
        style = NamingStyle(name="L", pattern="{given}", parts={"given": part})
        names = Naming(NpcTables(None)).sample(style, count=20)
        assert len(names) >= 10
        assert not set(names) & set(LEARNED)
        assert all(n[0].isupper() for n in names)

    def test_by_gender(self) -> None:
        part = LearnedPart(
            female=["Anna" + c for c in "abcdefghij"], male=["Bor" + c for c in "abcdefghij"]
        )
        style = NamingStyle(name="L", pattern="{given}", parts={"given": part})
        maker = NameMaker(Dice())
        assert maker.make(style, "female").startswith("A")
        assert maker.make(style, "male").startswith("B")

    def test_too_few_examples_and_a_form_round_trip(self) -> None:
        row = PartFields(key="given", kind="learned", names="Ana, Bo")
        with pytest.raises(StyleError) as caught:
            style_from(_fields(pattern="{given}", parts=[row]))
        assert caught.value.key == "naming.error.learned_few"
        style = NamingStyle(
            name="L", pattern="{given}", parts={"given": LearnedPart(names=LEARNED, order=3)}
        )
        assert style_from(fields_of(style)).parts == style.parts


class TestKin:
    def test_reads_the_parent_or_falls_back(self) -> None:
        naming = Naming(NpcTables(None), lambda: [_kin()])
        child = naming.made("own:kin", kin={"given": "Arn"})[0]
        assert child.text.endswith(" Arnson") and child.parts["father"] == "Arn"
        assert naming.names("own:kin")[0].endswith(" Dagson")

    def test_never_the_parents_own_name_twice(self) -> None:
        naming = Naming(NpcTables(None), lambda: [_kin()])
        for _ in range(20):
            given, father = naming.made("own:kin", kin={"given": "Arn"})[0].parts.values()
            assert given != father

    def test_a_repeat_is_rolled_again_with_no_parent(self) -> None:
        part = ElementsPart(first=["Gud", "Vig"], second=["vald", "mund"])
        style = NamingStyle(
            name="N", pattern="{given} {father}son", parts={"given": part, "father": part}
        )
        for name in Naming(NpcTables(None)).sample(style):
            first, second = name.split()
            assert second != f"{first}son"

    def test_a_fallback_must_exist_and_not_be_kin(self) -> None:
        rows = [PartFields(key="a", kind="kin", kin_part="given", fallback="b")]
        with pytest.raises(StyleError) as caught:
            style_from(_fields(pattern="{a}", parts=rows))
        assert caught.value.key == "naming.error.fallback"
        loop = '{"name":"x","pattern":"{a}","parts":{"a":{"kind":"kin","part":"g","fallback":"a"}}}'
        with pytest.raises(StyleError):
            style_from_raw(loop)


async def _login(raw: httpx.AsyncClient) -> None:
    await raw.post("/login", data={"username": "dm", "password": PASSWORD})


FORM = {
    "name": "Vale",
    "summary": "",
    "pattern": "{given}",
    "rewrites": "",
    "part-0-key": "given",
    "part-0-kind": "list",
    "part-0-names": "Ana\nBo\nCy",
    "part-1-key": "",
    "part-1-kind": "list",
}


class TestPages:
    async def test_write_judge_reject_delete(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        await _login(raw)
        assert 'href="/gm/names"' in (await raw.get("/gm")).text
        assert "Old Norse" in (await raw.get("/gm/names", params={"race": "Human"})).text
        trial = await raw.post("/gm/names/preview", data=FORM)
        assert "Ana" in trial.text and registry.naming_workshop.styles() == []
        refused = await raw.post("/gm/names/new", data={**FORM, "pattern": "{nobody}"})
        assert refused.status_code == 422 and 'value="{nobody}"' in refused.text
        made = await raw.post("/gm/names/new", data=FORM)
        url = made.headers["location"]
        style = registry.naming_workshop.styles()[0]
        assert url == f"/gm/names/{style.id}"
        page = (await raw.get(url)).text
        assert "Ana" in page and f"{url}/delete" in page
        assert "delete" not in (await raw.get(f"{url}/edit")).text.split("<main")[-1].lower()
        gone = await raw.post(f"{url}/reject", data={"name": "Ana"}, headers={"HX-Request": "true"})
        assert gone.status_code == 200 and gone.text == ""
        assert "Ana" not in registry.naming.names(f"own:{style.id}", count=3)
        assert "Take back" in (await raw.get(url)).text
        await raw.post(f"{url}/edit", data={**FORM, "name": "Renamed"})
        kept = registry.naming_workshop.get(style.id)
        assert kept.name == "Renamed" and kept.rejected == ["Ana"]
        await raw.post(f"{url}/restore", data={"name": "Ana"})
        assert registry.naming_workshop.get(style.id).rejected == []
        bad = await raw.post(f"{url}/raw", data={"raw": "{oops"})
        assert bad.status_code == 422 and "{oops" in html.unescape(bad.text)
        await raw.post(f"{url}/delete")
        assert registry.naming_workshop.styles() == []

    async def test_a_starter_is_copied_not_edited(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        await _login(raw)
        shown = await raw.get("/gm/names/samples", params={"key": "starter:old-norse"})
        assert "/edit" not in shown.text and "/gm/names/copy" in shown.text
        await raw.post("/gm/names/copy", data={"key": "starter:old-norse"})
        copied = registry.naming_workshop.styles()[0]
        assert copied.name == "Old Norse (saga age)" and copied.id != "old-norse"
        assert len(registry.naming.names(f"own:{copied.id}", count=5)) == 5
        assert (await raw.post("/gm/names/copy", data={"key": "x"})).status_code == 404
        unknown = await raw.get("/gm/names/samples", params={"key": "nope"})
        assert unknown.status_code == 404

    async def test_closed_to_anyone_logged_out(self, table: Table) -> None:  # noqa: F811
        raw, _ = table
        assert (await raw.get("/gm/names")).status_code in (302, 303, 401, 403)

    async def test_many_styles_stay_findable(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        for index in range(120):
            registry.naming_workshop.create(
                _style(f"Mu{index}", race="Tauren", culture=f"Tribe {index}", id=f"t{index}")
            )
        await _login(raw)
        index_page = (await raw.get("/gm/names")).text
        assert "Tauren" in index_page and "Tribe 7" not in index_page.split("Tauren")[0]
        assert "Mu7" not in index_page  # the index samples nothing
        listed = await raw.get("/gm/names", params={"race": "Tauren"})
        assert "120 styles" in listed.text and "Showing 100 of 120" in listed.text
        found = await raw.get("/gm/names", params={"q": "tribe 119"})
        assert "Tribe 119" in found.text and "Tribe 7<" not in found.text
        loose = await raw.get("/gm/names", params={"race": "-"})
        assert "Tribe 7" not in loose.text
        new = await raw.get("/gm/names/new", params={"race": "Tauren"})
        assert 'value="Tauren"' in new.text

    async def test_a_style_shows_where_it_came_from(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        style = registry.naming_workshop.create(
            _style("A", sources=["https://example.org/x", "javascript:alert(1)", "a book"])
        )
        await _login(raw)
        page = (await raw.get(f"/gm/names/{style.id}")).text
        assert 'href="https://example.org/x"' in page
        assert 'href="javascript' not in page and "a book" in page

    async def test_an_npc_is_named_by_race_and_culture(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        registry.naming_workshop.create(_style("Mu", id="t", race="Tauren", culture="Shu'halo"))
        npc = registry.gamestate.npcs.create(Npc(scope=Scope(world_id=world_id), name="New"))
        await _login(raw)
        url = f"/gm/npcs/{npc.id}"
        form = (await raw.get(f"{url}/edit")).text
        assert 'name="race"' in form and 'name="culture"' in form
        await raw.post(
            f"{url}/edit",
            data={
                "name": "New",
                "place": f"w:{world_id}",
                "race": " Tauren ",
                "culture": "Shu'halo",
            },
        )
        kept = registry.gamestate.npcs.get(npc.id)
        assert (kept.race, kept.culture) == ("Tauren", "Shu'halo")
        shown = (await raw.get(f"{url}/generate")).text
        assert 'value="own:t" selected' in shown and "No style for" not in shown
        await raw.post(
            f"{url}/edit", data={"name": "New", "place": f"w:{world_id}", "race": "Murloc"}
        )
        assert "No style for Murloc" in (await raw.get(f"{url}/generate")).text
        async with session(table) as mcp:
            good = await call(mcp, "generate_names", world_id=world_id, race="Tauren", count=1)
            assert (good["names"], good["fallback"]) == (["Mu"], False)
            none = await call(mcp, "generate_names", world_id=world_id, race="Murloc", count=1)
            assert none["fallback"] and none["names"]
            only = await call(mcp, "list_naming_styles", race="tauren")
            assert {s["key"] for s in only} == {"own:t", "race:Tauren"}
            rules = await call(mcp, "naming_style_rules")
            assert "actually read" in rules
            prompt = await mcp.get_prompt("new_naming_style", {"race": "Tauren"})
            assert "Tauren" in prompt.messages[0].content.text  # type: ignore[union-attr]
            asked = await mcp.get_prompt(
                "new_naming_style",
                {"description": "harbour taverns of a pirate coast", "category": "building"},
            )
            text = asked.messages[0].content.text  # type: ignore[union-attr]
            assert "pirate coast" in text and "It names: building" in text
            assert "For places" in text
            drafted = await call(
                mcp,
                "draft_naming_style",
                name="Pirate taverns",
                subject="building",
                culture="Pirate coast",
                pattern="The {adjective} {sign}",
                parts={
                    "adjective": {"kind": "list", "names": ["Drunken", "Salty", "Rusty"]},
                    "sign": {"kind": "list", "names": ["Anchor", "Gull", "Kraken"]},
                },
            )
            assert drafted["samples"][0].startswith("The ")
            kept = registry.naming_workshop.get(drafted["id"])
            assert (kept.subject, kept.draft) == ("building", True)


class TestWizard:
    async def test_the_details_step_suggests_by_species_or_chosen_style(
        self,
        client: tuple[httpx.AsyncClient, StoreRegistry],  # noqa: F811
    ) -> None:
        ac, registry = client
        world = registry.gamestate.worlds.list()[0]
        registry.naming_workshop.create(_style("Ana", id="dwarf", race="Dwarf"))
        registry.naming_workshop.create(_style("Bo", id="other"))
        draft = (await ac.post("/chargen", data={"world": world.id})).headers["location"]
        pc_id = draft.split("/")[2]
        step = (await ac.get(f"/chargen/{pc_id}/details")).text
        assert 'name="name_style"' in step and 'value="own:other"' in step
        typed = {"appearance": "Tall", "name": "", "name_style": "own:other"}
        answer = await ac.post(f"/chargen/{pc_id}/details/name", data=typed, headers=HTMX)
        assert 'value="Bo"' in answer.text
        assert registry.gamestate.characters.get(pc_id).appearance == "Tall"
        registry.gamestate.characters.mutate(pc_id, lambda c: setattr(c, "species", "Dwarf|PHB"))
        await ac.post(f"/chargen/{pc_id}/details/name", data={}, headers=HTMX)
        assert registry.naming.find("Dwarf").key == "race:Dwarf"


class TestDraftsAndParents:
    async def test_a_claude_draft_waits_for_acceptance(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        async with session(table) as mcp:
            drafted = await call(
                mcp,
                "draft_naming_style",
                name="Marsh folk",
                pattern="{given}",
                parts={"given": {"kind": "list", "names": ["Ana", "Bo"]}},
            )
            assert sorted(drafted["samples"]) == ["Ana", "Bo"]
            assert drafted["problems"]  # it runs out at two
            styles = await call(mcp, "list_naming_styles")
            assert not any(s["name"] == "Marsh folk" for s in styles)
        key = f"own:{drafted['id']}"
        assert key not in {e.key for e in registry.naming.entries()}
        await _login(raw)
        assert "Proposed by Claude" in (await raw.get("/gm/names")).text
        page = (await raw.get(f"/gm/names/{drafted['id']}")).text
        assert "/accept" in page and "Ana" in page
        await raw.post(f"/gm/names/{drafted['id']}/accept")
        assert key in {e.key for e in registry.naming.entries()}
        with pytest.raises(StyleError) as caught:
            registry.naming_workshop.propose(_style("A"), drafted["id"])
        assert caught.value.key == "naming.error.not_draft"

    async def test_a_child_descends_from_a_named_npc(self, table: Table) -> None:  # noqa: F811
        raw, facts = table
        registry = facts["app"].state.stores
        world_id = facts["world"].id
        registry.naming_workshop.create(_kin())
        father = registry.gamestate.npcs.create(
            Npc(
                scope=Scope(world_id=world_id),
                name="Arn Dagson",
                given_name=GivenName(text="Arn Dagson", parts={"given": "Arn"}),
            )
        )
        renamed = registry.gamestate.npcs.create(
            Npc(
                scope=Scope(world_id=world_id),
                name="Now Different",
                given_name=GivenName(text="Old", parts={"given": "Old"}),
            )
        )
        workshop = registry.naming_workshop
        assert workshop.relatives(world_id) == [(f"npc:{father.id}", "Arn Dagson")]
        assert workshop.kin_parts(f"npc:{renamed.id}") == {}
        assert workshop.kin_parts("npc:gone") == {}
        async with session(table) as mcp:
            named = await call(
                mcp,
                "generate_names",
                world_id=world_id,
                style="own:kin",
                parent="arn dagson",
                count=1,
            )
            assert named["names"][0].endswith("Arnson")
            with pytest.raises(Exception, match="no name made"):
                await call(mcp, "generate_names", world_id=world_id, parent="Nobody")
        son = registry.gamestate.npcs.create(Npc(scope=Scope(world_id=world_id), name="Son"))
        await _login(raw)
        url = f"/gm/npcs/{son.id}/generate"
        shown = (await raw.get(url)).text
        assert "Child of" in shown and f"npc:{father.id}" in shown
        rolled = await raw.post(
            url, data={"action": "roll", "names": "own:kin", "parent": f"npc:{father.id}"}
        )
        assert "Arnson" in rolled.text
        name = re.search(r'name="v_name" value="([^"]+)"', rolled.text)[1]  # type: ignore[index]
        parts = html.unescape(re.search(r'name="v_name_parts" value="([^"]+)"', rolled.text)[1])  # type: ignore[index]
        await raw.post(
            url,
            data={
                "action": "apply",
                "names": "own:kin",
                "take": "name",
                "v_name": name,
                "v_name_parts": parts,
            },
        )
        kept = registry.gamestate.npcs.get(son.id)
        assert kept.name == name and kept.given_name.parts["father"] == "Arn"
