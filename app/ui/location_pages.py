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
    img,
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
    crumbs,
    delete_link,
    location_url,
    media_url,
    saved_version,
    world_url,
)
from app.ui.markdown import Rendered
from app.ui.name_roller import name_roller
from app.ui.tokens import token, token_panel


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


def _picture(location: Location, size: str = "thumb") -> Node:
    if not location.picture:
        return span(class_=f"avatar {'avatar-lg' if size == 'large' else ''}", aria_hidden="true")[
            icon("map")
        ]
    return img(
        class_="portrait" if size == "large" else "thumb",
        src=media_url(location.scope.record_id, location.picture),
        alt="",
    )


def _where(names: Sequence[str]) -> str:
    return " › ".join(names)


def location_card(page: Page, location: Location, notes: Markup | None) -> Element:
    """What ``{@place Name}`` shows alone on a line: the place, its tags, its notes
    folded until opened."""
    t = page.t
    return div(class_="npc-embed place-embed")[
        div(class_="md-embed-head npc-embed-head")[
            _picture(location),
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
) -> Element:
    """A world's places by name, with where each is placed and a form for a new one."""
    t = page.t
    items = [
        li(class_="list-row")[
            token(page.token_of("place", location.scope.world_id, location.id)),
            _picture(location) if location.picture else None,
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
            page_head(t("adventures.world_eyebrow"), t("places.heading"), t("places.lede")),
            notice(notice_text),
            alert(error),
            ul(class_="list")[items]
            if items
            else empty_state("map", t("places.empty_heading"), t("places.empty_text")),
            form(
                method="post",
                action=world_locations_url(world.id),
                class_="panel stack-form create-form",
            )[
                text_field("name", t("adventures.name"), value="", autocomplete="off"),
                scope_select(
                    "place", t("scope.label"), scopes, selected=selected, hint=t("scope.hint")
                ),
                div(class_="form-actions")[
                    button(type="submit", class_="btn btn-primary")[
                        icon("plus"), span[t("places.new")]
                    ],
                ],
            ],
        ],
        world_id=world.id,
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
            div(class_="npc-head")[
                _picture(location, "large"),
                div(class_="npc-head-main")[
                    span(class_="eyebrow")[_where(where)],
                    h1[location.name],
                    div(class_="npc-roles")[role_pills(page, location.tags)],
                    p(class_="field-hint")[
                        t("holdings.paste"),
                        " ",
                        span(class_="image-snippet mono")[f"{{@place {location.name}}}"],
                    ],
                ],
                div(class_="npc-head-actions")[
                    a(href=f"{location_url(location.id)}/edit", class_="btn")[
                        icon("pencil"), span[t("players.edit")]
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
            token_panel(
                page,
                page.token_of("place", location.scope.world_id, location.id),
                "place",
                location.id,
            ),
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
            section(class_="panel images-panel")[
                div(class_="panel-head")[b[t("places.picture")]],
                div(class_="image-row")[
                    _picture(location),
                    form(
                        method="post",
                        action=f"{url}/picture",
                        enctype="multipart/form-data",
                        class_="upload-form",
                    )[
                        label(for_="f-picture", class_="sr-only")[t("adventures.image_file")],
                        input_el(
                            id="f-picture",
                            type="file",
                            name="file",
                            accept="image/png,image/jpeg,image/webp,image/gif,image/avif",
                            required=True,
                        ),
                        button(type="submit", class_="btn")[
                            icon("upload"), span[t("adventures.upload")]
                        ],
                    ],
                    form(method="post", action=f"{url}/picture/clear")[
                        button(type="submit", class_="btn btn-sm btn-ghost")[
                            t("places.clear_picture")
                        ]
                    ]
                    if location.picture
                    else None,
                ],
            ],
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
