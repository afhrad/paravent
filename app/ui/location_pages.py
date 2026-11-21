"""Places as pages and cards (documentation/ADVENTURES.md §4): a world's places, one
place — picture, tags, where it is placed, its notes and what they name — the form, and
the card ``{@place Name}`` shows alone on a line of a chapter."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    details,
    div,
    form,
    h1,
    h2,
    label,
    li,
    p,
    section,
    span,
    summary,
    textarea,
    ul,
)
from htpy import input as input_el
from markupsafe import Markup

from app.models.gamestate import Location, World
from app.services.naming import PERSON, StyleEntry
from app.services.scope import encode
from app.ui.cast_pages import role_pills
from app.ui.context import Page
from app.ui.forms import alert, hidden, notice, scope_select, text_field
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import (
    WORLDS_URL,
    confirm_page,
    copy_snippet,
    crumbs,
    delete_link,
    location_url,
    saved_version,
    world_url,
)
from app.ui.markdown import Rendered
from app.ui.name_roller import name_roller
from app.ui.title_images import title_image, title_image_panel, title_thumb
from app.ui.tokens import token
from app.ui.tool_dialog import create_form, new_button, tool_dialog


@dataclass(frozen=True)
class Here:
    """An NPC, encounter or treasure a place's notes name; ``url`` None when nothing
    in the world has that name (it shows grey)."""

    kind: str
    name: str
    url: str | None
    icon: str


def world_locations_url(world_id: str) -> str:
    """A world's places."""
    return f"{world_url(world_id)}/places"


def _title(location: Location) -> tuple[str, str] | None:
    """Where the place's title image is, for ``media_url``; None without one."""
    return (location.scope.record_id, location.picture) if location.picture else None


def _picture(page: Page, location: Location) -> Node:
    """A small square of the place's title image, else its kind's disc: a place carries
    no token of its own (documentation/TOKENS.md)."""
    return title_thumb(_title(location), location.name) or token(
        page.token_of("place", location.scope.world_id, location.id), "lg"
    )


def _where(names: Sequence[str]) -> str:
    return " › ".join(names)


def location_card(page: Page, location: Location, notes: Markup | None) -> Element:
    """What ``{@place Name}`` shows alone on a line: the place, its tags, its notes
    folded until opened."""
    t = page.t
    return div(class_="npc-embed place-embed")[
        div(class_="md-embed-head npc-embed-head")[
            _picture(page, location),
            div(class_="npc-embed-main")[
                span(class_="md-embed-kind")[t("md.embed.place")],
                a(href=location_url(location.id), class_="npc-embed-name")[location.name],
                span(class_="npc-roles")[role_pills(page, location.tags)],
            ],
        ],
        details(class_="md-statblock")[
            summary(class_="md-embed-head")[span(class_="md-embed-kind")[t("places.notes")]],
            div(class_="md-body md-embed-notes")[notes],
        ]
        if notes is not None and notes.strip()
        else None,
    ]


def locations_page(
    page: Page,
    world: World,
    rows: Sequence[tuple[Location, Sequence[str]]],
    scopes: Sequence[tuple[str, str, int]],
    *,
    selected: str,
    notice_text: str | None = None,
    error: str | None = None,
    new: bool = False,
) -> Element:
    """A world's places by name, with where each is placed, and **New place**, which
    opens a dialog for one (open from the start with ``new``, or after a refused one)."""
    t = page.t
    items = [
        li(class_="list-row")[
            _picture(page, location),
            div(class_="list-main")[
                a(href=location_url(location.id), class_="list-title list-link")[location.name],
                span(class_="list-meta")[role_pills(page, location.tags), " ", _where(names)],
            ],
            delete_link(page, f"{location_url(location.id)}/delete", t("places.delete")),
        ]
        for location, names in rows
    ]
    return app_shell(
        page,
        t("places.page_title", world=world.name),
        div(class_="prose-page")[
            crumbs(
                page,
                (t("nav.worlds"), WORLDS_URL),
                (world.name, world_url(world.id)),
                (t("places.heading"), None),
            ),
            page_head(
                t("adventures.world_eyebrow"),
                t("places.heading"),
                t("places.lede"),
                new_button(page, "place", t("places.new")),
            ),
            notice(notice_text),
            alert(error) if not new else None,
            ul(class_="list")[items]
            if items
            else empty_state("map", t("places.empty_heading"), t("places.empty_text")),
            _new_place_dialog(page, world, error if new else None, new),
        ],
        world_id=world.id,
    )


def _new_place_dialog(page: Page, world: World, error: str | None, is_open: bool) -> Element:
    """A new place of the table's world in one go: its name, its tags, a first note and
    its picture (framed later on its page, in the image dialog)."""
    t = page.t
    return tool_dialog(
        page,
        "place",
        t("places.new"),
        create_form(
            page,
            action=world_locations_url(world.id),
            submit=t("places.create"),
            upload=True,
            body=[
                alert(error),
                # In the table's world (documentation/CURRENT-WORLD.md); moved into an
                # adventure or chapter on its form if wanted.
                hidden("place", f"w:{world.id}"),
                text_field("name", t("adventures.name"), value="", autocomplete="off"),
                text_field(
                    "tags",
                    t("places.tags"),
                    value="",
                    autocomplete="off",
                    required=False,
                    optional_text=t("common.optional"),
                    hint=t("places.tags_hint"),
                ),
                div(class_="field")[
                    label(for_="f-new-notes")[t("places.notes")],
                    textarea(
                        id="f-new-notes",
                        name="notes",
                        rows="4",
                        class_="prose-editor",
                        data_scope=f"w:{world.id}",
                    ),
                ],
                label(for_="f-new-picture", class_="tok-drop img-drop")[
                    icon("upload"),
                    span[t("places.picture_drop")],
                    input_el(id="f-new-picture", type="file", name="picture", accept="image/*"),
                ],
            ],
        ),
        is_open=is_open,
    )


def location_page(
    page: Page,
    location: Location,
    world: World,
    where: Sequence[str],
    notes: Rendered,
    here: Sequence[Here],
    *,
    notice_text: str | None = None,
) -> Element:
    """One place: picture, tags, where it is placed, its notes and what they name."""
    t = page.t
    return app_shell(
        page,
        location.name,
        div(class_="prose-page")[
            crumbs(
                page,
                (t("nav.worlds"), WORLDS_URL),
                (world.name, world_url(world.id)),
                (t("places.heading"), world_locations_url(world.id)),
                (location.name, None),
            ),
            notice(notice_text),
            title_image(_title(location), location.name),
            div(class_="npc-head")[
                div(class_="npc-head-main")[
                    span(class_="eyebrow")[_where(where)],
                    h1[location.name],
                    div(class_="npc-roles")[role_pills(page, location.tags)],
                    p(class_="field-hint")[
                        t("holdings.paste"),
                        " ",
                        copy_snippet(page, f"{{@place {location.name}}}"),
                    ],
                ],
                div(class_="npc-head-actions")[
                    a(href=f"{location_url(location.id)}/edit", class_="btn")[
                        icon("pencil"), span[t("players.edit")]
                    ],
                    a(href=f"{location_url(location.id)}/settlement", class_="btn btn-ghost")[
                        icon("dices", "sm"), span[t("settlement.roll")]
                    ],
                    delete_link(
                        page, f"{location_url(location.id)}/delete", t("places.delete"), text=True
                    ),
                ],
            ],
            div(class_="md-body")[notes.html] if notes.html.strip() else None,
            section(class_="prose-section")[
                h2(class_="section-title")[t("places.here")],
                ul(class_="place-here")[
                    [
                        li[
                            a(href=target.url, class_=f"ref lib {target.kind}")[
                                icon(target.icon, "xs"), target.name
                            ]
                            if target.url
                            else span(class_="ref missing")[target.name]
                        ]
                        for target in here
                    ]
                ],
            ]
            if here
            else None,
        ],
        world_id=world.id,
    )


def place_name_field(
    page: Page,
    location: Location,
    name: str,
    name_styles: Sequence[StyleEntry],
    style: str = "",
) -> Element:
    """The place's name with dice for one, which a roll swaps in place, keeping the
    ``style`` it was rolled in for the next."""
    return span(class_="name-field")[
        input_el(
            id="f-name", name="name", type="text", value=name, required=True, autocomplete="off"
        ),
        name_roller(
            page,
            f"{location_url(location.id)}/edit/name",
            name_styles,
            field_id="f-name",
            places=True,
            selected=style,
        )
        if any(e.subject != PERSON for e in name_styles)
        else None,
    ]


def location_form_page(
    page: Page,
    location: Location,
    world: World,
    scopes: Sequence[tuple[str, str, int]],
    *,
    name: str,
    place: str,
    tags: Sequence[str],
    notes: str,
    base: str = "",
    conflict: str | None = None,
    error: str | None = None,
    notice_text: str | None = None,
    name_styles: Sequence[StyleEntry] = (),
) -> Element:
    """Edit a place: name (with dice for one), where it is placed, tags, notes and
    picture."""
    t = page.t
    url = location_url(location.id)
    return app_shell(
        page,
        t("adventures.edit_title", name=location.name),
        div(class_="formpage formpage-wide")[
            a(href=url, class_="backlink")[icon("arrow-left"), span[location.name]],
            notice(notice_text),
            div(class_="panel")[
                div(class_="panel-head")[
                    span(class_="eyebrow")[t("places.eyebrow")],
                    b[t("adventures.edit_title", name=location.name)],
                ],
                alert(error),
                form(method="post", action=f"{url}/edit", class_="stack-form")[
                    div(class_="field")[
                        label(for_="f-name")[t("adventures.name")],
                        place_name_field(page, location, name, name_styles),
                    ],
                    scope_select(
                        "place", t("scope.label"), scopes, selected=place, hint=t("scope.hint")
                    ),
                    text_field(
                        "tags",
                        t("places.tags"),
                        value=", ".join(tags),
                        autocomplete="off",
                        required=False,
                        optional_text=t("common.optional"),
                        hint=t("places.tags_hint"),
                    ),
                    div(class_="field")[
                        label(for_="f-notes")[t("places.notes")],
                        p(class_="field-hint", id="f-notes-hint")[t("places.notes_hint")],
                        textarea(
                            id="f-notes",
                            name="notes",
                            rows="12",
                            class_="prose-editor",
                            data_scope=encode(location.scope),
                            aria_describedby="f-notes-hint",
                        )[notes],
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
            title_image_panel(
                page, "place", location.id, _title(location), owner=location.scope.record_id
            ),
        ],
        world_id=world.id,
        insert_layer=True,
        extra_scripts=("/static/js/htmx.min.js",),
    )


def location_delete_page(page: Page, location: Location) -> Element:
    """Confirm deleting a place."""
    t = page.t
    url = location_url(location.id)
    return confirm_page(
        page,
        title=t("places.delete"),
        heading=t("chapters.delete_heading", name=location.name),
        lede=t("places.delete_lede"),
        action=f"{url}/delete",
        back=url,
        submit=t("places.delete"),
    )


__all__ = [
    "Here",
    "location_card",
    "location_delete_page",
    "location_form_page",
    "location_page",
    "locations_page",
    "world_locations_url",
]
