"""The adventure Markdown dialect (documentation/ADVENTURES.md §5)."""

from __future__ import annotations

from htpy import div

from app.services.i18n import UiCatalogue
from app.ui.markdown import (
    LibraryLink,
    MarkdownContext,
    embeds_to_tags,
    protect_pipes,
    render_markdown,
)


def html(text: str, ctx: MarkdownContext | None = None) -> str:
    return str(render_markdown(text, ctx).html)


NPCS = {"Hob Wade": "/npc/1"}


def library(kind: str, name: str) -> LibraryLink | None:
    url = NPCS.get(name) if kind == "npc" else None
    return LibraryLink(url, f"{kind}: {name}") if url else None


class TestCommonMark:
    def test_raw_html_is_escaped(self) -> None:
        out = html("A <script>alert(1)</script> <b onclick=x>b</b>\n\n<div>block</div>\n")
        assert "<script>" not in out and "<b" not in out and "<div>block" not in out
        assert "&lt;script&gt;" in out

    def test_javascript_links_are_not_links(self) -> None:
        out = html("[x](javascript:alert(1)) [y](https://example.org)")
        assert "javascript:" not in out.replace("[x](javascript:alert(1))", "")
        assert 'href="https://example.org"' in out

    def test_tables_and_strikethrough(self) -> None:
        out = html("| a | b |\n|---|---|\n| {@dice 1d6} | ~~no~~ |\n")
        assert "<table>" in out and "<s>no</s>" in out and 'data-roll="1d6"' in out


class TestTags:
    def test_5etools_tags_render_through_the_tag_renderer(self) -> None:
        out = html("Four {@creature goblin|MM}s.")
        assert 'class="ref creature inert"' in out and "goblin" in out

    def test_markdown_never_reaches_inside_a_tag(self) -> None:
        out = html("*{@spell Melf's_acid_arrow}* and {@spell a_b_c} and {@note *x*}")
        assert "<em>" in out  # the outer emphasis still works
        assert "Melf&#39;s_acid_arrow" in out and "a_b_c" in out

    def test_code_and_escapes_keep_the_braces(self) -> None:
        out = html("`{@spell fireball}` and \\{@spell fireball}")
        assert "<code>{@spell fireball}</code>" in out
        assert "and {@spell fireball}" in out
        assert "ref spell" not in out

    def test_unterminated_tag_is_text(self) -> None:
        assert "{@spell fireball" in html("{@spell fireball and more")

    def test_a_library_tag_links(self) -> None:
        out = html("Meet {@npc Hob Wade|Hob}.", MarkdownContext(link=library))
        assert '<a href="/npc/1" class="ref lib npc" title="npc: Hob Wade">' in out
        assert ">Hob</a>" in out

    def test_a_library_tag_label_may_hold_tags(self) -> None:
        out = html("{@npc Hob Wade|{@b Hob}}", MarkdownContext(link=library))
        assert "<b>Hob</b>" in out or "<strong>Hob</strong>" in out

    def test_an_unknown_name_is_inert_not_an_error(self) -> None:
        for ctx in (MarkdownContext(link=library), MarkdownContext()):
            out = html("{@encounter Nowhere}", ctx)
            assert 'class="ref missing"' in out and "is not in this world" in out

    def test_adventure_keeps_its_5etools_meaning(self) -> None:
        calls: list[tuple[str, str]] = []

        def spy(kind: str, name: str) -> None:
            calls.append((kind, name))

        html("{@adventure The Drowned Bell|LMoP|1}", MarkdownContext(link=spy))  # type: ignore[arg-type]
        assert calls == []


class TestTablesAndPipes:
    def test_a_tag_with_pipes_stays_in_its_cell(self) -> None:
        out = html("| Room | What |\n|---|---|\n| 1 | {@creature wolf||Three wolves} |\n")
        assert out.count("<td>") == 2 and ">Three wolves</span></td>" in out

    def test_code_keeps_its_pipes(self) -> None:
        assert protect_pipes("```\n{@x a|b}\n```") == "```\n{@x a|b}\n```"
        assert protect_pipes("`{@x a|b}` {@y c|d}") == "`{@x a|b}` {@y c\\|d}"

    def test_an_escaped_pipe_is_not_escaped_twice(self) -> None:
        assert protect_pipes("{@y c\\|d}") == "{@y c\\|d}"


class TestCallouts:
    def test_read_aloud(self) -> None:
        out = html("> [!read]\n> You've been on the trail.\n")
        assert out.startswith('<aside class="md-callout md-read">')
        assert "Read aloud" in out and "<p>You've been on the trail.</p>" in out
        assert "[!read]" not in out

    def test_sidebar_title(self) -> None:
        out = html("> [!SIDEBAR] Running Gnasher\n> Gnasher is a coward.\n")
        assert 'class="md-callout md-sidebar"' in out
        assert '<div class="md-callout-title">Running Gnasher</div>' in out

    def test_a_marker_on_its_own_paragraph_leaves_no_empty_paragraph(self) -> None:
        out = html("> [!secret]\n>\n> The Pale Weaver is Vessk.\n")
        assert "<p></p>" not in out and "Vessk" in out and "md-secret" in out

    def test_other_quotes_stay_quotes(self) -> None:
        assert html("> just a quote\n").startswith("<blockquote>")
        assert html("> [not a callout] hi\n").startswith("<blockquote>")

    def test_obsidian_kinds(self) -> None:
        for kind, title in (("info", "Info"), ("abstract", "Summary"), ("danger", "Danger")):
            out = html(f"> [!{kind}]\n> Text.\n")
            assert out.startswith(f'<aside class="md-callout md-{kind}">'), out
            assert f"{title}</div>" in out and "[!" not in out
        out = html("> [!quote] Read: Arrival\n> The road turns to a causeway.\n")
        assert 'class="md-callout md-quote"' in out and "Read: Arrival</div>" in out

    def test_aliases_follow_obsidian(self) -> None:
        assert 'class="md-callout md-abstract"' in html("> [!TLDR]\n> x\n")
        assert 'class="md-callout md-danger"' in html("> [!error]\n> x\n")
        assert 'class="md-callout md-quote"' in html("> [!cite]\n> x\n")

    def test_an_unknown_kind_is_a_note_titled_as_written(self) -> None:
        out = html("> [!lore]\n> x\n")
        assert 'class="md-callout md-note"' in out and "Lore</div>" in out

    def test_folded_closed_and_open(self) -> None:
        closed = html("> [!abstract]- Agnes Holler\n> **Role:** accused.\n")
        assert closed.startswith('<details class="md-callout md-abstract">')
        assert "<summary" in closed and "Agnes Holler</span></summary>" in closed
        assert closed.rstrip().endswith("</details>")
        assert html("> [!danger]+ Hag\n> x\n").startswith(
            '<details class="md-callout md-danger" open>'
        )

    def test_a_title_reads_markdown_and_keeps_the_toc(self) -> None:
        rendered = render_markdown("# Top\n\n> [!info] **Bold** title\n> x\n")
        assert "<strong>Bold</strong> title" in str(rendered.html)
        assert [h.anchor for h in rendered.toc] == ["top"]

    def test_nested_quote_inside_a_callout(self) -> None:
        out = html("> [!sidebar] Outer\n> text\n> > inner\n")
        assert out.count("</aside>") == 1 and "<blockquote>" in out


class TestBookCallouts:
    """What the published books print besides read-aloud text and sidebars."""

    def test_a_variant_is_titled_as_the_books_print_it(self) -> None:
        out = html("> [!variant] Encumbrance\n> Carry less.\n")
        assert 'class="md-callout md-variant"' in out
        assert "Variant: Encumbrance</div>" in out
        assert "Variant</div>" in html("> [!variant]\n> x\n")

    def test_layout_kinds_carry_no_title_of_their_own(self) -> None:
        for kind in ("pullquote", "flowchart", "gallery", "handout"):
            out = html(f"> [!{kind}]\n> x\n")
            assert f'class="md-callout md-{kind}"' in out and "md-callout-title" not in out
        assert "A torn letter</div>" in html("> [!handout] A torn letter\n> Meet me.\n")
        assert 'class="md-callout md-handout"' in html("> [!letter]\n> x\n")
        assert 'class="md-callout md-flowchart"' in html("> [!flow]\n> - a\n> - b\n")

    def test_a_flowcharts_steps_are_its_list(self) -> None:
        out = html("> [!flowchart]\n> - **Start.** Roll.\n> - **End.** Done.\n")
        assert out.count("<li>") == 2 and "<ul>" in out

    def test_a_quotes_last_line_names_who_said_it(self) -> None:
        out = html("> [!quote]\n> Magic is a river.\n> — Orrin the Grey\n")
        assert "<p>Magic is a river.</p>" in out
        assert '<p class="md-quote-by">— Orrin the Grey</p>' in out
        apart = html("> [!pullquote]\n> Run.\n>\n> -- Volo\n")
        assert '<p class="md-quote-by">— Volo</p>' in apart and "<p>Run.</p>" in apart

    def test_a_dash_elsewhere_is_text(self) -> None:
        out = html("> [!quote]\n> — first line\n> then this\n")
        assert "md-quote-by" not in out
        assert "md-quote-by" not in html("> [!sidebar]\n> x\n> — y\n")


class TestTagAloneOnItsLine:
    """A paragraph that is one tag shows the object; anywhere else a tag is a link."""

    @staticmethod
    def ctx(seen: list[tuple[str, str]]) -> MarkdownContext:
        def embed(kind: str, argument: str) -> div:
            seen.append((kind, argument))
            return div(class_="card")[argument]

        return MarkdownContext(embed=embed, link=library)

    def test_the_campaigns_objects_show_as_their_card(self) -> None:
        seen: list[tuple[str, str]] = []
        out = html(
            "Before.\n\n{@encounter Goblin Ambush}\n\n{@npc Hob Wade|Hob}\n\n"
            "{@treasure Gnasher's chest}\n\n{@character Brunhild}\n",
            self.ctx(seen),
        )
        assert seen == [
            ("encounter", "Goblin Ambush"),
            ("npc", "Hob Wade"),  # the label is a link's, not the card's
            ("treasure", "Gnasher's chest"),
            ("character", "Brunhild"),
        ]
        assert '<div class="md-embed md-embed-encounter"><div class="card">Goblin Ambush' in out
        assert out.count("<div") == out.count("</div>")

    def test_a_reference_folds_with_its_book_and_label(self) -> None:
        seen: list[tuple[str, str]] = []
        html("  {@spell Fireball|PHB|Feuerball}  \n", self.ctx(seen))
        assert seen == [("spell", "Fireball|PHB|Feuerball")]

    def test_inside_a_sentence_a_list_or_a_table_it_is_a_link(self) -> None:
        seen: list[tuple[str, str]] = []
        out = html(
            "Meet {@npc Hob Wade}.\n\n- {@npc Hob Wade}\n\n| who |\n|---|\n| {@npc Hob Wade} |\n",
            self.ctx(seen),
        )
        assert seen == []
        assert out.count('href="/npc/1"') == 3

    def test_two_tags_or_a_chapter_or_a_roll_stay_inline(self) -> None:
        seen: list[tuple[str, str]] = []
        out = html("{@npc A} {@npc B}\n\n{@chapter Grey Reed}\n\n{@dice 2d6}\n", self.ctx(seen))
        assert seen == [] and 'data-roll="2d6"' in out

    def test_nothing_to_show_is_the_link_in_its_inert_state(self) -> None:
        out = html("{@npc Mira Fenn}\n", MarkdownContext(embed=lambda _k, _a: None))
        assert out.startswith('<p><span class="ref missing"') and "md-embed" not in out
        assert "“Mira Fenn” is not in this world." in out


class TestEmbedsToTags:
    def test_blocks_become_tags_with_their_caption_after(self) -> None:
        text = "Intro\n::: encounter Goblin Ambush\nThey flee.\n:::\nAfter\n"
        assert embeds_to_tags(text) == (
            "Intro\n\n{@encounter Goblin Ambush}\n\nThey flee.\n\nAfter\n"
        )

    def test_statblock_is_a_creature_and_open_is_dropped(self) -> None:
        text = "::: statblock Gnasher|LMoP\n:::\n\n::: item Potion of Healing open\n:::"
        assert embeds_to_tags(text) == ("{@creature Gnasher|LMoP}\n\n{@item Potion of Healing}")

    def test_anchors_unknown_blocks_and_code_stay(self) -> None:
        text = (
            ":::: at 00e\n::: npc Hob\n:::\nNotes.\n::::\n\n::: bogus x\n:::\n\n"
            "```\n::: npc X\n:::\n```\n"
        )
        assert embeds_to_tags(text) == (
            ":::: at 00e\n\n{@npc Hob}\n\nNotes.\n::::\n\n::: bogus x\n:::\n\n"
            "```\n::: npc X\n:::\n```\n"
        )

    def test_a_text_without_blocks_is_unchanged(self) -> None:
        text = "# Title\n\n{@npc Hob}\n\n> [!read]\n> Hi.\n"
        assert embeds_to_tags(text) == text


class TestEmbeds:
    def test_the_page_supplies_the_object(self) -> None:
        seen: list[tuple[str, str]] = []

        def embed(kind: str, argument: str) -> div:
            seen.append((kind, argument))
            return div(class_="card")[argument]

        out = html(
            "::: encounter Goblin Ambush\nThey flee at half strength.\n:::\n",
            MarkdownContext(embed=embed),
        )
        assert seen == [("encounter", "Goblin Ambush")]
        assert (
            '<div class="md-embed md-embed-encounter"><div class="card">Goblin Ambush</div>' in out
        )
        assert "They flee at half strength." in out

    def test_a_statblock_reference_passes_through_whole(self) -> None:
        seen: list[str] = []
        html(
            "::: statblock Gnasher|LMoP\n:::\n", MarkdownContext(embed=lambda k, a: seen.append(a))
        )  # type: ignore[func-returns-value]
        assert seen == ["Gnasher|LMoP"]

    def test_an_unresolved_embed_says_so(self) -> None:
        out = html("::: treasure Gnasher's chest\n:::\n")
        assert "md-embed-treasure" in out and "is not in this world" in out

    def test_an_unknown_block_is_visible(self) -> None:
        out = html("::: frobnicate\nx\n:::\n")
        assert "Unknown block “frobnicate”" in out and "<p>x</p>" in out

    def test_anchored_notes_nest_with_a_longer_fence(self) -> None:
        out = html(":::: at 00e\n::: encounter Ambush\n:::\nTwo are Yeemik's.\n::::\n")
        assert out.startswith('<div class="md-anchor" data-anchor="00e">')
        assert out.index("md-embed-encounter") < out.index("Two are Yeemik")
        assert out.count("<div") == out.count("</div>")

    def test_divs_balance_for_every_kind(self) -> None:
        text = "\n".join(
            f"::: {kind} X\ncaption\n:::\n" for kind in ("encounter", "npc", "bogus", "statblock")
        )
        out = html(text + ":::: at 1\n::: npc Y\n:::\n::::\n")
        assert out.count("<div") == out.count("</div>")


class TestImages:
    def test_relative_paths_go_through_the_media_callback(self) -> None:
        asked: list[str] = []

        def media(path: str) -> str | None:
            asked.append(path)
            return f"/media/a1/{path}" if path == "media/map.webp" else None

        out = html(
            "![Rotfang](media/map.webp) ![gone](media/nope.png)", MarkdownContext(media=media)
        )
        assert '<img src="/media/a1/media/map.webp" alt="Rotfang" loading="lazy">' in out
        assert "Image not found: media/nope.png" in out
        assert asked == ["media/map.webp", "media/nope.png"]

    def test_nothing_from_elsewhere(self) -> None:
        asked: list[str] = []
        ctx = MarkdownContext(media=lambda p: asked.append(p) or "/served")  # type: ignore[func-returns-value]
        out = html(
            "![a](https://example.org/x.png) ![b](/etc/x.png) ![c](//host/x.png) "
            "![d](data:image/png;base64,AAAA)",
            ctx,
        )
        assert asked == [] and "<img" not in out


class TestHeadings:
    def test_shifted_below_the_page_title_and_anchored(self) -> None:
        rendered = render_markdown(
            "# Goblin *Ambush*\n## Gnasher's {@creature cave}\n###### Deep\n"
        )
        out = str(rendered.html)
        assert '<h2 id="goblin-ambush">Goblin <em>Ambush</em></h2>' in out
        assert '<h3 id="gnasher-s-cave">' in out and "<h6" in out and "<h7" not in out
        assert [(h.level, h.text) for h in rendered.toc] == [
            (1, "Goblin Ambush"),
            (2, "Gnasher's cave"),
            (6, "Deep"),
        ]

    def test_repeated_headings_get_distinct_anchors(self) -> None:
        toc = render_markdown("# Room\n# Room\n# Room\n").toc
        assert [h.anchor for h in toc] == ["room", "room-2", "room-3"]


class TestLanguage:
    def test_the_dialects_own_words_are_translated(self) -> None:
        t = UiCatalogue(None).translator("de")
        out = html("> [!read]\n> Text\n\n::: npc Niemand\n:::\n", MarkdownContext(t=t))
        assert "Vorlesen" in out and "NSC" in out and "gibt es in dieser Welt nicht" in out


class TestHeadingLinks:
    CHAPTERS = {"Grey Reed": "/gm/chapters/2"}

    def ctx(self) -> MarkdownContext:
        def link(kind: str, name: str) -> LibraryLink | None:
            url = self.CHAPTERS.get(name) if kind == "chapter" else None
            return LibraryLink(url, name) if url else None

        return MarkdownContext(link=link)

    def test_a_heading_in_this_text(self) -> None:
        out = html("See [[#Appendix B – Scaling]].\n\n## Appendix B – Scaling\n")
        assert '<a href="#appendix-b-scaling" class="md-wikilink">Appendix B – Scaling</a>' in out

    def test_with_a_label_even_in_a_table(self) -> None:
        text = (
            "| Foe |\n|---|\n| 5 × [[#Bog Zombie (Zombie)|Bog Zombie]] |\n\n"
            "### Bog Zombie (Zombie)\n"
        )
        out = html(text)
        assert '<a href="#bog-zombie-zombie" class="md-wikilink">Bog Zombie</a>' in out
        assert out.count("<td>") == 1

    def test_a_missing_heading_says_so(self) -> None:
        out = html("[[#Nowhere]]\n")
        assert 'class="ref missing"' in out and "Nowhere" in out

    def test_another_chapter(self) -> None:
        out = html("[[Grey Reed#The Inn]] and [[Grey Reed|the town]] and [[Lowmere]]\n", self.ctx())
        assert 'href="/gm/chapters/2#the-inn"' in out and "Grey Reed › The Inn" in out
        assert 'href="/gm/chapters/2"' in out and "the town</a>" in out
        assert 'class="ref missing"' in out and "Lowmere" in out


class TestLineBreaks:
    def test_a_newline_is_a_break(self) -> None:
        out = html("**AC** 17\n**HP** 82\n")
        assert "<strong>AC</strong> 17<br />" in out

    def test_a_tag_alone_still_ignores_the_break(self) -> None:
        assert "<br" not in html("{@npc Hob Wade}\n", MarkdownContext(link=library))

    def test_off_for_hard_wrapped_text(self) -> None:
        assert "<br" not in html("one\ntwo\n", MarkdownContext(breaks=False))


class TestImageEmbeds:
    """Obsidian's ``![[map.png]]``, found by name (ADVENTURES.md §5.5)."""

    FILES = {"map.png": "/media/w/maps/map.png"}

    def ctx(self) -> MarkdownContext:
        return MarkdownContext(find_media=self.FILES.get)

    def test_by_name_with_width_and_alt(self) -> None:
        out = html("![[map.png]]\n\n![[map.png|The map|300]]\n\n![[map.png|300x200]]\n", self.ctx())
        assert '<img src="/media/w/maps/map.png" alt="" loading="lazy">' in out
        assert 'alt="The map" width="300"' in out
        assert 'width="300" height="200"' in out

    def test_without_a_finder_it_tries_media(self) -> None:
        seen: list[str] = []

        def media(path: str) -> str | None:
            seen.append(path)
            return "/m/x" if path == "media/x.webp" else None

        assert 'src="/m/x"' in html("![[x.webp]]\n", MarkdownContext(media=media))
        assert seen == ["x.webp", "media/x.webp"]

    def test_a_missing_image_says_so(self) -> None:
        out = html("![[gone.jpg]]\n", self.ctx())
        assert 'class="md-image missing"' in out and "gone.jpg" in out

    def test_a_note_embed_is_a_link(self) -> None:
        out = html("![[#Somewhere]]\n\n## Somewhere\n")
        assert '<a href="#somewhere" class="md-wikilink">Somewhere</a>' in out
