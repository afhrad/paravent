"""Worlds, adventures and chapters as pages (documentation/ADVENTURES.md).

* the **world** — its cover, its description, its adventures in order;
* an **adventure** — cover, summary, its text, its chapters in order;
* a **chapter** — the text to run the table from, with a table of contents, and an
  editor beside a live preview.

Text is the gamemaster's Markdown (:mod:`app.ui.markdown`). What its names mean —
which chapter ``{@chapter Grey Reed}`` is, what ``::: encounter Goblin Ambush`` shows —
is answered here, in :func:`prose_context`, from the services.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from htpy import (
    Element,
    Node,
    a,
    article,
    aside,
    b,
    button,
    code,
    details,
    div,
    fieldset,
    form,
    h1,
    h2,
    img,
    label,
    legend,
    li,
    nav,
    ol,
    option,
    p,
    pre,
    section,
    select,
    span,
    summary,
    textarea,
    ul,
)
from htpy import input as input_el
from markupsafe import Markup

from app.models.gamestate import Adventure, Chapter, Encounter, Npc, Scope, SourceSet, World
from app.services import image_effects
from app.services.adventures import STATUSES, AdventureService, AdventureView, ChapterView
from app.services.characters import CharacterService
from app.services.combat import CombatService
from app.services.difficulty import Rating
from app.services.encounters import EncounterService
from app.services.locations import LocationService
from app.services.npcs import NpcService, TreasureService
from app.services.references import ReferenceEntry, ReferenceNotFound, ReferenceService
from app.services.revisions import Change
from app.services.sound import SoundLibrary
from app.store.published import PublishedAdventure
from app.store.reference import parse_reference
from app.ui.auth_pages import message_panel
from app.ui.cast_pages import npc_card, treasure_card
from app.ui.combat_pages import combat_url
from app.ui.context import Page
from app.ui.encounter_pages import encounter_url, level_pill
from app.ui.forms import alert, hidden, notice, select_field, text_field
from app.ui.gm_pages import party_card
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import (
    MEDIA_UPLOAD_URL,
    WORLDS_URL,
    adventure_url,
    chapter_url,
    character_url,
    confirm_page,
    cover,
    crumbs,
    delete_link,
    location_url,
    media_url,
    npc_url,
    saved_version,
    treasure_url,
    world_url,
)
from app.ui.location_pages import location_card
from app.ui.markdown import Heading, LibraryLink, MarkdownContext, Rendered, render_markdown
from app.ui.palette import CATEGORY_ICON
from app.ui.prose_check import Problem
from app.ui.ref_pages import CATEGORY_KEY, statblock
from app.ui.sound_panel import SOUNDS_URL
from app.ui.tags import ENTITY_CATEGORY, RenderContext, localized_context, ref_url

STATUS_TONE = {"draft": "magic", "planned": "neutral", "running": "accent", "finished": "heal"}


# -- what the text links to -------------------------------------------------------------


@dataclass(frozen=True)
class ProseServices:
    """What resolving a text's names needs."""

    adventures: AdventureService
    encounters: EncounterService
    references: ReferenceService
    npcs: NpcService
    treasure: TreasureService
    # Offers "Run" on an embedded encounter, or the way into its running combat.
    combats: CombatService | None = None
    # ``{@character}``: the world's player characters, a party card when alone.
    characters: CharacterService | None = None
    # ``{@place}``: the campaign's places, a card when alone.
    locations: LocationService | None = None
    # ``{@sound}``: the sound desk's library, shared by every world.
    sounds: SoundLibrary | None = None


def _run(page: Page, combats: CombatService | None, encounter_id: str, planned: bool) -> Node:
    """ "Run" for an encounter, or the way into the combat already running for it."""
    if combats is None:
        return None
    running = next((c for c in combats.list(encounter_id=encounter_id) if c.running), None)
    if running is not None:
        state = (
            page.t("combat.round_n", n=running.round) if running.round else page.t("combat.rolling")
        )
        return a(href=combat_url(running.id), class_="btn btn-sm btn-primary enc-run")[
            icon("zap", "sm"), span[state]
        ]
    return form(method="post", action=f"{encounter_url(encounter_id)}/run", class_="enc-run")[
        button(type="submit", class_="btn btn-sm btn-primary", disabled=not planned)[
            icon("play", "sm"), span[page.t("combat.run")]
        ]
    ]


def _fold(
    page: Page,
    references: ReferenceService,
    kind: str,
    argument: str,
    world_id: str,
    ctx: RenderContext,
) -> Node:
    """``::: spell Fireball`` — the entry folded into the text: a row naming it (like a
    preview's head), opening to the whole entry as its reference page shows it. ``open``
    after the reference starts it open. None when the world's books do not hold it."""
    reference, opened = argument, False
    if reference.lower().endswith(" open"):
        reference, opened = reference[: -len(" open")].rstrip(), True
    tag = "creature" if kind == "statblock" else kind
    name, source, _display = parse_reference(reference, tag)
    entry = None
    for category in ENTITY_CATEGORY.get(tag, ()):
        try:
            entry = references.get(category, source or "-", name, world_id)
            break
        except ReferenceNotFound:
            continue
    if entry is None:
        return None
    glyph, tone = CATEGORY_ICON.get(entry.category, ("book-open", ""))
    shown = page.t.term(entry.category, entry.name) or entry.name
    return details(class_=f"md-fold md-fold-{entry.category}", open=opened)[
        summary(class_="md-embed-head md-fold-head")[
            span(class_=f"res-ico {tone}".strip())[icon(glyph)],
            b(class_="md-fold-name")[shown],
            span(class_="md-embed-kind")[page.t(CATEGORY_KEY.get(entry.category, "ref.kind.rule"))],
            span(class_="res-src mono")[entry.source],
            a(
                href=ref_url(entry.category, entry.name, entry.source, world_id),
                class_="md-fold-open",
                title=page.t("ref.open"),
            )[icon("book-open", "sm")],
        ],
        statblock(page, entry, ctx),
    ]


def prose_context(
    page: Page,
    services: ProseServices,
    *,
    record_id: str,
    world: World,
    adventure: Adventure | None,
    source_set: SourceSet,
    chapter_id: str | None = None,
) -> MarkdownContext:
    """How one record's Markdown resolves: its books, its world, its own images.

    Names of NPCs, treasure and encounters are looked up nearest first from here — this
    chapter, this adventure, the world (:mod:`app.services.scope`).
    """
    tag_ctx = localized_context(
        page.t,
        resolver=services.references.resolver(),
        source_set=source_set,
        world_id=world.id,
    )
    here = Scope(
        world_id=world.id,
        adventure_id=adventure.id if adventure else None,
        chapter_id=chapter_id,
    )

    def creature(ref: str) -> ReferenceEntry | None:
        name, source, _display = parse_reference(ref, "creature")
        try:
            return services.references.get("monster", source or "-", name, world.id)
        except ReferenceNotFound:
            return None

    def link(kind: str, name: str) -> LibraryLink | None:
        if kind == "chapter":
            chapter = services.adventures.find_chapter(
                world.id, name, prefer=adventure.id if adventure else None
            )
            return LibraryLink(chapter_url(chapter.id), chapter.title) if chapter else None
        if kind == "encounter":
            encounter = services.encounters.find(name, here)
            return LibraryLink(encounter_url(encounter.id), encounter.name) if encounter else None
        if kind == "npc":
            npc = services.npcs.find(name, here)
            return LibraryLink(npc_url(npc.id), npc.name) if npc else None
        if kind == "treasure":
            treasure = services.treasure.find(name, here)
            return LibraryLink(treasure_url(treasure.id), treasure.name) if treasure else None
        if kind == "character" and services.characters is not None:
            pc = services.characters.named(world.id, name)
            return LibraryLink(character_url(pc.id), pc.name) if pc else None
        if kind == "place" and services.locations is not None:
            location = services.locations.find(name, here)
            return LibraryLink(location_url(location.id), location.name) if location else None
        if kind == "sound" and services.sounds is not None:
            sound = services.sounds.named(name)
            return LibraryLink(SOUNDS_URL, sound.name) if sound else None
        return None

    def inner(text: str) -> Markup | None:
        """A card's own notes: Paravent Markdown with links only, so a card never
        unfolds cards (a place's notes may name the place itself)."""
        if not text.strip():
            return None
        context = MarkdownContext(
            tags=tag_ctx, link=link, media=media, find_media=find_media, t=page.t
        )
        return render_markdown(text, context).html

    def embed(kind: str, argument: str) -> Node:
        if kind == "encounter":
            encounter = services.encounters.find(argument, here)
            if encounter is None:
                return None
            view = services.encounters.view(encounter)
            monsters = ", ".join(
                f"{ln.planned.count}× {ln.planned.label or ln.planned.name}" for ln in view.lines
            )
            return div(class_="md-embed-head enc-embed")[
                span(class_="md-embed-kind")[page.t("md.embed.encounter")],
                a(href=encounter_url(encounter.id), class_="enc-embed-name")[encounter.name],
                level_pill(page, view.rating),
                _run(page, services.combats, encounter.id, bool(encounter.monsters)),
                span(class_="enc-embed-monsters")[monsters or page.t("adventures.embed_empty")],
                _folded_notes(page, inner(encounter.notes)),
            ]
        if kind == "npc":
            npc = services.npcs.find(argument, here)
            if npc is None:
                return None
            entry = creature(npc.statblock) if npc.statblock else None
            return npc_card(page, npc, entry, tag_ctx)
        if kind == "treasure":
            treasure = services.treasure.find(argument, here)
            if treasure is None:
                return None
            return treasure_card(page, treasure, services.treasure.lines(treasure), world.id)
        if kind == "place" and services.locations is not None:
            location = services.locations.find(argument, here)
            if location is None:
                return None
            return location_card(page, location, inner(location.notes))
        if kind == "character" and services.characters is not None:
            pc = services.characters.named(world.id, argument)
            if pc is None:
                return None
            member = services.characters.party_member(pc.id)
            # data-party-world: party.js refetches the card as this world shows it.
            return ul(class_="party-grid party-embed", data_party_world=world.id)[
                party_card(
                    page, member.sheet, owner=member.owner, world=None, world_filter=world.id
                )
            ]
        if kind == "statblock" or kind in ENTITY_CATEGORY:
            return _fold(page, services.references, kind, argument, world.id, tag_ctx)
        return None

    def media(relative: str) -> str | None:
        if services.adventures.media_path(record_id, relative) is None:
            return None
        return media_url(record_id, relative)

    def find_media(name: str) -> str | None:
        found = services.adventures.find_media(record_id, world.id, name)
        return media_url(*found) if found is not None else None

    return MarkdownContext(
        tags=tag_ctx, link=link, embed=embed, media=media, find_media=find_media, t=page.t
    )


def _folded_notes(page: Page, notes: Markup | None) -> Node:
    """An encounter card's notes — tactics, terrain — folded until opened."""
    if notes is None:
        return None
    return details(class_="md-statblock enc-embed-notes")[
        summary(class_="md-embed-head")[span(class_="md-embed-kind")[page.t("combat.notes")]],
        div(class_="md-body md-embed-notes")[notes],
    ]


def render_body(text: str, ctx: MarkdownContext) -> Rendered:
    """Render a body; a thin alias so routes need not import the dialect."""
    return render_markdown(text, ctx)


# -- small pieces ---------------------------------------------------------------------


def status_pill(page: Page, status: str) -> Element:
    """Planned, running or finished."""
    return span(class_=f"pill {STATUS_TONE.get(status, 'neutral')}")[
        page.t(f"adventures.status.{status}")
    ]


def _levels(page: Page, adventure: Adventure) -> str | None:
    if adventure.level is None:
        return None
    lo, hi = adventure.level.start, adventure.level.end
    return (
        page.t("adventures.levels", start=lo, end=hi)
        if lo != hi
        else page.t("adventures.level_one", level=lo)
    )


def _move_buttons(page: Page, action: str, *, first: bool, last: bool) -> Element:
    t = page.t
    return form(method="post", action=action, class_="order-buttons")[
        button(
            type="submit",
            name="step",
            value="-1",
            class_="btn btn-ghost btn-icon btn-sm",
            disabled=first,
            aria_label=t("adventures.move_up"),
            title=t("adventures.move_up"),
        )["↑"],
        button(
            type="submit",
            name="step",
            value="1",
            class_="btn btn-ghost btn-icon btn-sm",
            disabled=last,
            aria_label=t("adventures.move_down"),
            title=t("adventures.move_down"),
        )["↓"],
    ]


def _add_form(page: Page, action: str, field: str, caption: str, submit: str) -> Element:
    return form(method="post", action=action, class_="add-row")[
        label(for_=f"f-new-{field}", class_="sr-only")[caption],
        input_el(
            id=f"f-new-{field}",
            name=field,
            type="text",
            required=True,
            placeholder=caption,
            autocomplete="off",
        ),
        button(type="submit", class_="btn btn-primary")[icon("plus"), span[submit]],
    ]


def _status_choices(page: Page) -> list[tuple[str, str]]:
    return [(s, page.t(f"adventures.status.{s}")) for s in STATUSES]


def images_panel(
    page: Page, record_id: str, cover: str | None, images: Sequence[str], next_url: str
) -> Element:
    """Upload an image, see the record's images, pick or clear the cover."""
    t = page.t
    upload = form(
        method="post",
        action=f"{MEDIA_UPLOAD_URL}/{record_id}",
        enctype="multipart/form-data",
        class_="upload-form",
    )[
        hidden("next", next_url),
        label(for_=f"f-upload-{record_id}", class_="sr-only")[t("adventures.image_file")],
        input_el(
            id=f"f-upload-{record_id}",
            type="file",
            name="file",
            accept="image/png,image/jpeg,image/webp,image/gif,image/avif",
            required=True,
        ),
        label(class_="check-inline")[
            input_el(type="checkbox", name="as_cover", value="1"), t("adventures.as_cover")
        ],
        button(type="submit", class_="btn")[icon("upload"), span[t("adventures.upload")]],
    ]
    cover_path, cover_effects = image_effects.split(cover)

    def effects_form(path: str) -> Element:
        """The cover's effects, ticked and saved together (ADVENTURES.md §5.5)."""
        return form(
            method="post", action=f"{MEDIA_UPLOAD_URL}/{record_id}/cover", class_="image-fx"
        )[
            hidden("next", next_url),
            hidden("path", path),
            span(class_="image-fx-label")[t("adventures.effects")],
            [
                label(class_="check-inline", title=t(f"adventures.effect_hint.{name}"))[
                    input_el(
                        type="checkbox",
                        name="effects",
                        value=name,
                        checked=name in cover_effects,
                    ),
                    t(f"adventures.effect.{name}"),
                ]
                for name in image_effects.EFFECTS
                if name != "banner"  # a cover wears it by itself
            ],
            button(type="submit", class_="btn btn-sm")[t("adventures.effects_apply")],
        ]

    rows = [
        li(class_="image-row")[
            img(
                src=media_url(record_id, cover if cover and path == cover_path else path),
                alt="",
                loading="lazy",
            ),
            div(class_="image-main")[
                code(class_="image-snippet")[f"![]({path})"],
                span(class_="pill accent")[t("adventures.is_cover")]
                if path == cover_path
                else None,
            ],
            form(method="post", action=f"{MEDIA_UPLOAD_URL}/{record_id}/cover")[
                hidden("next", next_url),
                hidden("path", "" if path == cover_path else path),
                button(type="submit", class_="btn btn-sm btn-ghost")[
                    t("adventures.clear_cover") if path == cover_path else t("adventures.use_cover")
                ],
            ],
            effects_form(path) if path == cover_path else None,
        ]
        for path in images
    ]
    return section(class_="panel images-panel")[
        div(class_="panel-head")[b[t("adventures.images")]],
        p(class_="field-hint")[t("adventures.images_hint")],
        upload,
        ul(class_="image-list")[rows] if rows else None,
    ]


# -- the world -------------------------------------------------------------------------


def _book_label(page: Page, book: PublishedAdventure) -> str:
    """``Title of the Adventure (SOURCE, levels 1–5)``."""
    parts = [book.source]
    if book.level_start:
        end = book.level_end or book.level_start
        parts.append(page.t("adventures.levels", start=book.level_start, end=end))
    return f"{book.name} ({', '.join(parts)})"


def world_page(
    page: Page,
    world: World,
    adventures: Sequence[tuple[Adventure, int]],
    description: Rendered,
    *,
    bosses: Sequence[Npc] = (),
    counts: tuple[int, int] = (0, 0),
    sessions: int = 0,
    places: int = 0,
    books: Sequence[PublishedAdventure] = (),
    notice_text: str | None = None,
    error: str | None = None,
) -> Element:
    """A world: cover, description, its bosses, its adventures in order.

    ``counts`` is how many NPCs and treasures the world holds, for the links to them.
    """
    t = page.t
    book_count = (
        t("worlds.books_n", n=len(world.source_set))
        if len(world.source_set)
        else t("worlds.all_books")
    )
    rows = [
        li(class_="list-row adv-row")[
            img(class_="thumb", src=media_url(adv.id, adv.cover), alt="")
            if adv.cover
            else span(class_="avatar", aria_hidden="true")[icon("map")],
            div(class_="list-main")[
                a(href=adventure_url(adv.id), class_="list-title list-link")[adv.name],
                span(class_="list-meta")[
                    status_pill(page, adv.status),
                    " ",
                    " · ".join(
                        x
                        for x in (_levels(page, adv), t.plural("adventures.chapters_n", count))
                        if x
                    ),
                ],
            ],
            delete_link(page, f"{adventure_url(adv.id)}/delete", t("adventures.delete")),
            _move_buttons(
                page,
                f"{adventure_url(adv.id)}/move",
                first=index == 0,
                last=index == len(adventures) - 1,
            ),
        ]
        for index, (adv, count) in enumerate(adventures)
    ]
    return app_shell(
        page,
        world.name,
        div(class_="prose-page")[
            crumbs(page, (t("nav.worlds"), WORLDS_URL), (world.name, None)),
            cover(world.id, world.cover, world.name),
            page_head(
                t("adventures.world_eyebrow"),
                world.name,
                book_count,
                div(class_="head-actions")[
                    a(href=f"{world_url(world.id)}/edit", class_="btn")[
                        icon("pencil"), span[t("players.edit")]
                    ],
                    delete_link(
                        page, f"{world_url(world.id)}/delete", t("worlds.delete"), text=True
                    ),
                ],
            ),
            notice(notice_text),
            alert(error),
            div(class_="md-body")[description.html] if description.html.strip() else None,
            nav(class_="world-links")[
                a(href=f"/journal?world={world.id}", class_="btn")[
                    icon("scroll"), span[t("journal.title")]
                ],
                a(href=f"{world_url(world.id)}/npcs", class_="btn")[
                    icon("circle-user"), span[t.plural("npcs.count", counts[0])]
                ],
                a(href=f"{world_url(world.id)}/treasure", class_="btn")[
                    icon("gem"), span[t.plural("treasure.count", counts[1])]
                ],
                a(href=f"{world_url(world.id)}/places", class_="btn")[
                    icon("map"), span[t.plural("places.count", places)]
                ],
                a(href=f"{world_url(world.id)}/names", class_="btn")[
                    icon("dices"), span[t("naming.world.title")]
                ],
                a(href=f"{world_url(world.id)}/claude", class_="btn")[
                    icon("sparkles"), span[t("instructions.title")]
                ],
                a(href=f"/gm/encounters?world={world.id}", class_="btn")[
                    icon("swords"), span[t("nav.encounters")]
                ],
                a(href=f"{world_url(world.id)}/sessions", class_="btn")[
                    icon("scroll"), span[t.plural("sessions.count", sessions)]
                ],
            ],
            section(class_="prose-section")[
                h2(class_="section-title")[t("npcs.bosses")],
                ul(class_="boss-list")[
                    [
                        li[
                            a(href=npc_url(n.id), class_="boss")[
                                img(src=media_url(n.scope.record_id, n.portrait), alt="")
                                if n.portrait
                                else icon("skull"),
                                span[n.name],
                            ]
                        ]
                        for n in bosses
                    ]
                ],
            ]
            if bosses
            else None,
            section(class_="prose-section")[
                h2(class_="section-title")[t("adventures.heading")],
                ul(class_="list")[rows]
                if rows
                else empty_state("map", t("adventures.empty_heading"), t("adventures.empty_text")),
                _add_form(
                    page,
                    f"{world_url(world.id)}/adventures",
                    "name",
                    t("adventures.new_name"),
                    t("adventures.new"),
                ),
                form(
                    method="post",
                    action=f"{world_url(world.id)}/adventures/from-book",
                    class_="add-row from-book",
                )[
                    label(for_="f-book", class_="sr-only")[t("book.choose")],
                    select(id="f-book", name="book", required=True)[
                        option(value="")[t("book.choose")],
                        [option(value=book.id)[_book_label(page, book)] for book in books],
                    ],
                    button(type="submit", class_="btn")[icon("book-open"), span[t("book.start")]],
                ]
                if books
                else None,
            ],
        ],
        world_id=world.id,
    )


def world_images_section(
    page: Page, world: World, images: Sequence[str], *, error: str | None = None
) -> Element:
    """The images panel for the world form."""
    return div[
        alert(error),
        images_panel(page, world.id, world.cover, images, f"{world_url(world.id)}/edit"),
    ]


# -- an adventure ----------------------------------------------------------------------


@dataclass(frozen=True)
class DraftReview:
    """What a draft Claude wrote holds, for the gamemaster to review (MCP.md §6)."""

    key_name: str | None  # the MCP key that drafted it; None once the key is revoked
    party: tuple[str, ...]
    chapters: tuple[tuple[Chapter, int], ...]  # each chapter and its check's problems
    encounters: tuple[tuple[Encounter, Rating], ...]
    npcs: int
    treasures: int
    fight_xp: int  # the adjusted XP of all its fights together
    day_xp: int | None  # the party's adventuring day (2014), None under 2024 rules


def _change_row(page: Page, change: Change) -> Element:
    """One record a revision changed, with its text's difference folded."""
    t = page.t
    pill: Node = None
    if change.held:
        pill = span(class_="pill cond")[t("revision.held")]
    elif change.conflict:
        pill = span(class_="pill dmg")[t("revision.conflict")]
    head: list[Node] = [
        span(class_="pill neutral")[t(f"revision.kind.{change.kind}")],
        " ",
        span[change.title],
        " ",
        span(class_="muted")[t(f"revision.action.{change.action}")],
        " ",
        pill,
    ]
    if not change.diff:
        return li[head]
    return li[
        details[
            summary[head],
            pre(class_="revision-diff")[
                [
                    span(class_="add" if line[0] == "+" else "del")[line, "\n"]
                    for line in change.diff
                ]
            ],
        ]
    ]


def revision_waiting(page: Page, copy_id: str) -> Element:
    """On the original: a revision waits to be read."""
    t = page.t
    return section(class_="panel draft-review")[
        p[t("revision.waiting")],
        div(class_="form-actions")[
            a(href=adventure_url(copy_id), class_="btn btn-primary")[t("revision.open")]
        ],
    ]


def draft_review_panel(
    page: Page,
    adventure_id: str,
    review: DraftReview,
    changes: Sequence[Change] | None = None,
) -> Element:
    """The banner over a draft: who wrote it, what it holds, accept or discard; for a
    revision also what it changes (``changes``, documentation/MCP.md §9)."""
    t = page.t
    url = adventure_url(adventure_id)
    lede = t("draft.lede", key=review.key_name) if review.key_name else t("draft.lede_revoked")
    xp = t.number(review.fight_xp)
    return section(class_="panel draft-review")[
        div(class_="panel-head")[
            span(class_="eyebrow")[icon("sparkles", "sm"), t("draft.eyebrow")],
            b[t("revision.title") if changes is not None else t("draft.title")],
        ],
        p(class_="muted")[t("revision.lede") if changes is not None else lede],
        div[
            h2(class_="draft-sub")[t("revision.changes")],
            ul(class_="draft-list revision-list")[[_change_row(page, c) for c in changes]]
            if changes
            else p(class_="muted")[t("revision.none")],
        ]
        if changes is not None
        else None,
        p[t("draft.party", names=", ".join(review.party))] if review.party else None,
        div(class_="draft-grid")[
            div[
                h2(class_="draft-sub")[t("chapters.heading")],
                ul(class_="draft-list")[
                    [
                        li[
                            a(href=chapter_url(ch.id))[ch.title],
                            " ",
                            span(class_="pill dmg")[t.plural("draft.problems", problems)]
                            if problems
                            else span(class_="pill heal")[t("draft.clean")],
                        ]
                        for ch, problems in review.chapters
                    ]
                ]
                if review.chapters
                else p(class_="muted")[t("chapters.empty_heading")],
            ],
            div[
                h2(class_="draft-sub")[t("draft.fights")],
                ul(class_="draft-list")[
                    [
                        li[a(href=encounter_url(enc.id))[enc.name], " ", level_pill(page, rating)]
                        for enc, rating in review.encounters
                    ]
                ]
                if review.encounters
                else p(class_="muted")[t("draft.no_fights")],
                p(class_="muted")[
                    t("draft.fight_xp_day", xp=xp, day=t.number(review.day_xp))
                    if review.day_xp
                    else t("draft.fight_xp", xp=xp)
                ]
                if review.encounters
                else None,
                p(class_="draft-over")[t("draft.over_day")]
                if review.day_xp and review.fight_xp > review.day_xp
                else None,
            ],
        ],
        p(class_="muted")[
            t.plural("draft.npcs", review.npcs),
            " · ",
            t.plural("draft.treasures", review.treasures),
        ],
        div(class_="form-actions")[
            form(method="post", action=f"{url}/accept")[
                button(type="submit", class_="btn btn-primary")[
                    icon("check"),
                    span[t("revision.accept") if changes is not None else t("draft.accept")],
                ]
            ],
            a(href=f"{url}/delete", class_="btn btn-ghost btn-danger")[t("draft.discard")],
        ],
    ]


def adventure_page(
    page: Page,
    view: AdventureView,
    body: Rendered,
    *,
    holdings: Node = None,
    notice_text: str | None = None,
    error: str | None = None,
    review: Node = None,
) -> Element:
    """An adventure: cover, summary, text, its chapters in order, and what it holds."""
    t = page.t
    adv, world = view.adventure, view.world
    chapters = view.chapters
    rows = [
        li(class_="list-row chapter-row")[
            span(class_="chapter-number mono", aria_hidden="true")[str(index + 1)],
            div(class_="list-main")[
                a(href=chapter_url(ch.id), class_="list-title list-link")[ch.title],
                span(class_="list-meta")[status_pill(page, ch.status)],
            ],
            a(href=f"{chapter_url(ch.id)}/edit", class_="btn btn-sm btn-ghost")[
                icon("pencil"), span(class_="list-action-label")[t("players.edit")]
            ],
            delete_link(page, f"{chapter_url(ch.id)}/delete", t("chapters.delete")),
            _move_buttons(
                page,
                f"{chapter_url(ch.id)}/move",
                first=index == 0,
                last=index == len(chapters) - 1,
            ),
        ]
        for index, ch in enumerate(chapters)
    ]
    based = t("book.based_on", name=view.book.name) if view.book else adv.based_on
    meta = [x for x in (_levels(page, adv), based) if x]
    return app_shell(
        page,
        adv.name,
        div(class_="prose-page")[
            crumbs(
                page,
                (t("nav.worlds"), WORLDS_URL),
                (world.name, world_url(world.id)),
                (adv.name, None),
            ),
            cover(adv.id, adv.cover, adv.name),
            page_head(
                t("adventures.eyebrow"),
                adv.name,
                adv.summary,
                div(class_="head-actions")[
                    a(href=f"{adventure_url(adv.id)}/edit", class_="btn")[
                        icon("pencil"), span[t("players.edit")]
                    ],
                    delete_link(
                        page, f"{adventure_url(adv.id)}/delete", t("adventures.delete"), text=True
                    ),
                ],
            ),
            p(class_="prose-meta")[status_pill(page, adv.status), " ", " · ".join(meta)],
            notice(notice_text),
            alert(error),
            review,
            div(class_="md-body")[body.html] if body.html.strip() else None,
            section(class_="prose-section")[
                h2(class_="section-title")[t("chapters.heading")],
                ol(class_="list")[rows]
                if rows
                else empty_state(
                    "book-open", t("chapters.empty_heading"), t("chapters.empty_text")
                ),
                _add_form(
                    page,
                    f"{adventure_url(adv.id)}/chapters",
                    "title",
                    t("chapters.new_title"),
                    t("chapters.new"),
                ),
            ],
            holdings,
        ],
        world_id=world.id,
    )


def adventure_form_page(
    page: Page,
    view: AdventureView,
    images: Sequence[str],
    *,
    name: str,
    summary_text: str,
    status: str,
    level_start: str,
    level_end: str,
    body: str,
    base: str = "",
    conflict: str | None = None,
    error: str | None = None,
    notice_text: str | None = None,
    characters: Sequence[tuple[str, str]] = (),
    party: Sequence[str] = (),
) -> Element:
    """Edit an adventure's fields, text and images. ``characters`` are the world's as
    ``(id, name)``; ``party`` the ids ticked."""
    t = page.t
    adv = view.adventure
    url = adventure_url(adv.id)
    return app_shell(
        page,
        t("adventures.edit_title", name=adv.name),
        div(class_="formpage formpage-wide")[
            a(href=url, class_="backlink")[icon("arrow-left"), span[adv.name]],
            notice(notice_text),
            div(class_="panel")[
                div(class_="panel-head")[
                    span(class_="eyebrow")[t("adventures.eyebrow")],
                    b[t("adventures.edit_title", name=adv.name)],
                ],
                alert(error),
                form(method="post", action=f"{url}/edit", class_="stack-form")[
                    text_field("name", t("adventures.name"), value=name, autocomplete="off"),
                    text_field(
                        "summary",
                        t("adventures.summary"),
                        value=summary_text,
                        autocomplete="off",
                        required=False,
                        optional_text=t("common.optional"),
                    ),
                    div(class_="field-row")[
                        select_field(
                            "status", t("adventures.status"), _status_choices(page), selected=status
                        ),
                        _number_field("level_start", t("adventures.level_start"), level_start),
                        _number_field("level_end", t("adventures.level_end"), level_end),
                    ],
                    _party_field(page, characters, party),
                    div(class_="field")[
                        label(for_="f-body")[t("adventures.body")],
                        p(class_="field-hint", id="f-body-hint")[t("prose.hint")],
                        textarea(
                            id="f-body",
                            name="body",
                            rows="12",
                            class_="prose-editor",
                            data_scope=f"a:{adv.id}",
                            data_upload=f"{MEDIA_UPLOAD_URL}/{adv.id}/inline",
                            aria_describedby="f-body-hint",
                        )[body],
                        hidden("base", base),
                    ],
                    saved_version(page, conflict),
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-primary")[
                            icon("check"), span[t("worlds.save")]
                        ],
                        a(href=url, class_="btn btn-ghost")[t("common.cancel")],
                    ],
                ],
            ],
            images_panel(page, adv.id, adv.cover, images, f"{url}/edit"),
        ],
        world_id=view.world.id,
        insert_layer=True,
    )


def _party_field(page: Page, characters: Sequence[tuple[str, str]], party: Sequence[str]) -> Node:
    """Who the adventure is written for; nobody ticked is the whole world."""
    if not characters:
        return None
    t = page.t
    return div(class_="field")[
        span(class_="field-label")[t("adventures.party")],
        p(class_="field-hint")[t("adventures.party_hint")],
        div(class_="party-choice")[
            [
                label(class_="check-inline", for_=f"party-{cid}")[
                    input_el(
                        type="checkbox",
                        id=f"party-{cid}",
                        name="party",
                        value=cid,
                        checked=cid in party,
                    ),
                    span[name],
                ]
                for cid, name in characters
            ]
        ],
    ]


def _number_field(name: str, caption: str, value: str) -> Element:
    return div(class_="field field-narrow")[
        label(for_=f"f-{name}")[caption],
        input_el(
            id=f"f-{name}",
            name=name,
            type="number",
            min="1",
            max="20",
            inputmode="numeric",
            value=value,
        ),
    ]


def contents_choice(page: Page, counts: Mapping[str, int], keep: str) -> Node:
    """What happens to the NPCs, places, encounters and treasure placed here: they move
    up (``keep`` says where), or they are deleted too. Nothing to ask when none."""
    t = page.t
    if not any(counts.values()):
        return None
    held = " · ".join(t.plural(f"count.{key}", count) for key, count in counts.items() if count)
    return fieldset(class_="delete-choice")[
        legend(class_="field-label")[t("delete.contents", held=held)],
        label[input_el(type="radio", name="contents", value="keep", checked=True), span[keep]],
        label[
            input_el(type="radio", name="contents", value="all"),
            span[t("delete.contents_all")],
        ],
    ]


def adventure_delete_page(
    page: Page, view: AdventureView, counts: Mapping[str, int] | None = None
) -> Element:
    """Confirm, saying the chapters and images go with it — to the trash — and asking
    what happens to what is placed in it."""
    t = page.t
    adv = view.adventure
    url = adventure_url(adv.id)
    return confirm_page(
        page,
        title=t("adventures.delete"),
        heading=t("adventures.delete_heading", name=adv.name),
        lede=t.plural("adventures.delete_lede", len(view.chapters)),
        action=f"{url}/delete",
        back=url,
        submit=t("adventures.delete"),
        hint=t("adventures.trash_hint"),
        choice=contents_choice(page, counts or {}, t("delete.keep_world")),
    )


# -- a chapter -------------------------------------------------------------------------


def _toc(page: Page, toc: Sequence[Heading]) -> Node:
    if len(toc) < 2:
        return None
    top = min(h.level for h in toc)
    return details(class_="toc", open=True)[
        summary[page.t("chapters.contents")],
        ol[
            [li(class_=f"toc-{min(h.level - top, 2)}")[a(href=f"#{h.anchor}")[h.text]] for h in toc]
        ],
    ]


def _neighbours(page: Page, view: ChapterView) -> Element:
    t = page.t
    return nav(class_="chapter-nav", aria_label=t("chapters.nav"))[
        a(href=chapter_url(view.previous.id), class_="btn btn-ghost", rel="prev")[
            icon("arrow-left"), span[view.previous.title]
        ]
        if view.previous
        else span,
        a(href=chapter_url(view.next.id), class_="btn btn-ghost", rel="next")[
            span[view.next.title], icon("arrow-right")
        ]
        if view.next
        else span,
    ]


def chapter_body(page: Page, view: ChapterView, body: Rendered, revision: str | None) -> Element:
    """What the live view swaps: the table of contents and the text."""
    return div(
        id="chapter-live",
        class_="chapter-layout",
        data_live=f"{chapter_url(view.chapter.id)}/events",
        data_rev=revision or "",
    )[
        aside(class_="chapter-aside")[_toc(page, body.toc)],
        article(class_="md-body chapter-text")[
            body.html
            if body.html.strip()
            else p(class_="empty-text")[page.t("chapters.empty_body")]
        ],
    ]


def chapter_page(
    page: Page,
    view: ChapterView,
    body: Rendered,
    revision: str | None,
    *,
    holdings: Node = None,
    notice_text: str | None = None,
    error: str | None = None,
) -> Element:
    """A chapter to run the table from, with what it holds folded away below."""
    t = page.t
    ch, adv, world = view.chapter, view.adventure, view.world
    return app_shell(
        page,
        ch.title,
        div(class_="prose-page chapter-page")[
            crumbs(
                page,
                (t("nav.worlds"), WORLDS_URL),
                (world.name, world_url(world.id)),
                (adv.name, adventure_url(adv.id)),
                (ch.title, None),
            ),
            cover(ch.id, ch.cover, ch.title),
            div(class_="chapter-head")[
                h1[ch.title],
                status_pill(page, ch.status),
                a(href=f"{chapter_url(ch.id)}/edit", class_="btn")[
                    icon("pencil"), span[t("players.edit")]
                ],
                delete_link(page, f"{chapter_url(ch.id)}/delete", t("chapters.delete"), text=True),
            ],
            notice(notice_text),
            alert(error),
            chapter_body(page, view, body, revision),
            holdings,
            _neighbours(page, view),
            p(class_="ref-roll-result", aria_live="polite", hidden=True),
        ],
        # htmx and party.js keep a {@character} card live, as on the gamemaster's table.
        extra_scripts=(
            "/static/js/roll.js",
            "/static/js/prose.js",
            "/static/js/htmx.min.js",
            "/static/js/party.js",
        ),
        world_id=world.id,
    )


def check_panel(page: Page, problems: Sequence[Problem]) -> Element:
    """What in the chapter leads nowhere (ADVENTURES.md §5.7): each problem with its line
    — a press goes there in the editor — and what would mend it."""
    t = page.t
    if not problems:
        return section(class_="check-pane is-clear")[
            span(class_="eyebrow")[t("check.title")],
            p(class_="muted")[icon("check", "sm"), " ", t("check.none")],
        ]
    return section(class_="check-pane")[
        span(class_="eyebrow")[t("check.title"), " ", span(class_="pill cond")[str(len(problems))]],
        ul(class_="check-list")[
            [
                li(class_=f"check-item check-{x.kind}")[
                    button(
                        type="button",
                        class_="check-line mono",
                        data_line=str(x.line),
                        title=t("check.go"),
                    )[t("check.line", n=x.line)],
                    span(class_="check-what")[
                        t(f"check.kind.{x.kind}", name=x.name or x.text),
                        " ",
                        code(class_="check-text")[x.text],
                    ],
                    span(class_="check-fixes")[
                        [
                            button(
                                type="button",
                                class_="btn btn-sm check-fix",
                                data_line=str(x.line),
                                data_from=x.text,
                                data_to=instead,
                            )[instead]
                            for instead in x.instead
                        ],
                        button(
                            type="button",
                            class_="btn btn-sm check-create",
                            data_kind=x.create[0],
                            data_name=x.create[1],
                        )[
                            icon("plus", "sm"),
                            span[t(f"search.create.{x.create[0]}", name=x.create[1])],
                        ]
                        if x.create
                        else None,
                    ],
                ]
                for x in problems
            ]
        ],
    ]


def chapter_form_page(
    page: Page,
    view: ChapterView,
    images: Sequence[str],
    preview: Rendered,
    *,
    title: str,
    status: str,
    body: str,
    base: str = "",
    conflict: str | None = None,
    problems: Sequence[Problem] = (),
    places: Sequence[tuple[str, str, int]] = (),
    error: str | None = None,
    notice_text: str | None = None,
) -> Element:
    """The chapter's editor beside a preview that follows the typing, and the check of
    what in the text leads nowhere.

    ``base`` is the fingerprint of the text the editor opened (a save from an older text
    is refused); ``conflict`` the text saved meanwhile, shown beside the editor's.
    ``places`` are a book chapter's entries — where a ``:::: at`` note can go.
    """
    t = page.t
    ch = view.chapter
    url = chapter_url(ch.id)
    return app_shell(
        page,
        t("chapters.edit_title", name=ch.title),
        div(class_="prose-page chapter-edit")[
            a(href=url, class_="backlink")[icon("arrow-left"), span[ch.title]],
            notice(notice_text),
            alert(error),
            form(method="post", action=f"{url}/edit", class_="stack-form editor-form")[
                div(class_="field-row")[
                    text_field("title", t("chapters.title"), value=title, autocomplete="off"),
                    select_field(
                        "status", t("adventures.status"), _status_choices(page), selected=status
                    ),
                ],
                div(class_="editor-panes")[
                    div(class_="field editor-pane")[
                        label(for_="f-body")[t("chapters.body")],
                        textarea(
                            id="f-body",
                            name="body",
                            rows="24",
                            class_="prose-editor",
                            spellcheck="true",
                            data_preview=f"{url}/preview",
                            data_scope=f"c:{ch.id}",
                            data_upload=f"{MEDIA_UPLOAD_URL}/{ch.id}/inline",
                            aria_describedby="f-body-hint",
                        )[body],
                        p(class_="field-hint", id="f-body-hint")[t("prose.hint")],
                        hidden("base", base),
                    ],
                    section(class_="editor-pane preview-pane", aria_label=t("chapters.preview"))[
                        div(id="check", data_check=f"{url}/check")[check_panel(page, problems)],
                        span(class_="eyebrow")[t("chapters.preview")],
                        div(id="preview", class_="md-body", aria_live="polite")[preview.html],
                    ],
                ],
                saved_version(page, conflict),
                div(class_="form-actions")[
                    button(type="submit", class_="btn btn-primary")[
                        icon("check"), span[t("worlds.save")]
                    ],
                    button(
                        type="submit",
                        name="action",
                        value="preview",
                        class_="btn js-hide",
                    )[icon("eye"), span[t("chapters.preview_button")]],
                    a(href=url, class_="btn btn-ghost")[t("common.cancel")],
                ],
            ],
            details(class_="panel places-panel")[
                summary(class_="panel-head")[b[t("book.places")]],
                p(class_="field-hint")[t("book.places_hint")],
                ul(class_="places-list")[
                    [
                        li(class_=f"places-{min(depth, 2)}")[
                            span[name], code(class_="image-snippet")[f":::: at {ident}"]
                        ]
                        for ident, name, depth in places
                    ]
                ],
            ]
            if places
            else None,
            images_panel(page, ch.id, ch.cover, images, f"{url}/edit"),
        ],
        extra_scripts=("/static/js/prose.js",),
        world_id=view.world.id,
        insert_layer=True,
    )


def chapter_delete_page(
    page: Page, view: ChapterView, counts: Mapping[str, int] | None = None
) -> Element:
    """Confirm deleting a chapter, asking what happens to what is placed in it."""
    t = page.t
    url = chapter_url(view.chapter.id)
    return confirm_page(
        page,
        title=t("chapters.delete"),
        heading=t("chapters.delete_heading", name=view.chapter.title),
        lede=t("chapters.delete_lede"),
        action=f"{url}/delete",
        back=url,
        submit=t("chapters.delete"),
        hint=t("adventures.trash_hint"),
        choice=contents_choice(page, counts or {}, t("delete.keep_adventure")),
    )


def adventure_not_found_page(page: Page) -> Element:
    """The adventure or chapter does not exist (any more)."""
    t = page.t
    return app_shell(
        page,
        t("notfound.page_title"),
        message_panel(
            "map",
            t("adventures.not_found"),
            t("adventures.not_found_lede"),
            a(href=WORLDS_URL, class_="btn btn-primary")[
                icon("arrow-left"), span[t("worlds.back")]
            ],
        ),
    )


__all__ = [
    "ProseServices",
    "adventure_delete_page",
    "adventure_form_page",
    "adventure_not_found_page",
    "adventure_page",
    "chapter_body",
    "chapter_delete_page",
    "chapter_form_page",
    "chapter_page",
    "images_panel",
    "prose_context",
    "render_body",
    "status_pill",
    "world_images_section",
    "world_page",
]
