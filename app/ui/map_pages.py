"""The gamemaster's maps as pages (documentation/MAPS.md): a world's maps, an adventure's
maps on its page, one map in its viewer with the ruler and the scale, its form, and the
question before it is deleted."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    details,
    div,
    fieldset,
    form,
    h1,
    h2,
    label,
    legend,
    li,
    link,
    nav,
    option,
    p,
    section,
    select,
    span,
    summary,
    textarea,
    ul,
)
from htpy import input as input_el
from markupsafe import Markup

from app.models.gamestate import Adventure, MapView, World, WorldMap, shown_source
from app.services.map_tiles import MAX_BYTES, TileJob, max_zoom, needs_tiles
from app.services.maps import LONG_UNITS, UNITS
from app.services.search import SearchHit
from app.services.travel import TERRAINS
from app.ui.clock_pages import route_travel_form
from app.ui.context import Page
from app.ui.context_menu import row_menu
from app.ui.forms import alert, hidden, notice, text_field
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.layout import app_shell, asset
from app.ui.library_links import (
    WORLDS_URL,
    adventure_url,
    confirm_page,
    crumbs,
    delete_link,
    media_url,
    world_url,
)
from app.ui.tool_dialog import create_form, new_button, tool_dialog

MAPS_URL = "/gm/maps"
# The players' maps (documentation/MAPS.md §9).
PLAYER_MAPS_URL = "/maps"
# The scripts of a map's page: Leaflet first, then Paravent's part.
MAP_SCRIPTS = ("/static/js/leaflet/leaflet.js", "/static/js/map.js")


def map_url(map_id: str) -> str:
    """One map's page."""
    return f"{MAPS_URL}/{map_id}"


def player_map_url(map_id: str) -> str:
    """One map as the players see it."""
    return f"{PLAYER_MAPS_URL}/{map_id}"


def world_maps_url(world_id: str) -> str:
    """A world's maps."""
    return f"{world_url(world_id)}/maps"


def is_long(found: WorldMap) -> bool:
    """Whether the map measures a land (miles or km) rather than a town (feet or m)."""
    return found.scale is not None and found.scale.unit in LONG_UNITS


def _rounded(value: float) -> float:
    """A measured distance as it is worth saying: whole from 100, one place from 10."""
    if value >= 100:
        return round(value)
    if value >= 10:
        return round(value, 1)
    return round(value, 2)


def distance_text(page: Page, feet: float, long: bool) -> str:
    """A distance on a map in the reader's units: miles or km on a land's map, feet or
    m on a town's."""
    if long:
        miles = feet / 5280
        return page.u.miles(_rounded(miles * 1.5) / 1.5 if page.u.metric else _rounded(miles))
    return page.u.distance(_rounded(feet * 0.3) / 0.3 if page.u.metric else _rounded(feet))


def scale_badge(page: Page, found: WorldMap) -> Element:
    """How far the map reaches across, or that it has no scale yet."""
    t = page.t
    if found.scale is None:
        return span(class_="pill cond map-scale-badge")[
            icon("ruler", "xs"), span[t("atlas.no_scale")]
        ]
    across = distance_text(page, found.scale.feet_per_px * found.width, is_long(found))
    return span(class_="pill neutral map-scale-badge")[
        icon("ruler", "xs"), span[t("atlas.across", distance=across)]
    ]


def _thumb(found: WorldMap, player: bool = False) -> Markup:
    url = player_map_url(found.id) if player else map_url(found.id)
    return Markup(
        f'<img class="title-thumb map-thumb" src="{url}/thumb" alt="" '
        'loading="lazy" decoding="async">'
    )


def map_rows(page: Page, maps: Sequence[WorldMap]) -> Element:
    """Maps as list rows: a thumbnail, the name, the scale, whether players see it."""
    t = page.t
    return ul(class_="list")[
        [
            li(class_="list-row")[
                _thumb(found),
                div(class_="list-main")[
                    a(href=map_url(found.id), class_="list-title list-link")[found.name],
                    span(class_="list-meta")[
                        scale_badge(page, found),
                        span(class_="pill neutral")[icon("users", "xs"), t("atlas.players.badge")]
                        if found.players
                        else None,
                    ],
                ],
                row_menu(page, map_url(found.id), found.name),
            ]
            for found in maps
        ]
    ]


def player_maps_page(
    page: Page,
    world: World | None,
    maps: Sequence[WorldMap],
    adventures: Mapping[str, str],
) -> Element:
    """The maps the gamemaster has shown the players of the table's world."""
    t = page.t
    rows = [
        li(class_="list-row")[
            _thumb(found, player=True),
            div(class_="list-main")[
                a(href=player_map_url(found.id), class_="list-title list-link")[found.name],
                span(class_="list-meta")[
                    span(class_="muted")[
                        adventures.get(found.scope.adventure_id or "", "") or t("atlas.world_map")
                    ],
                    scale_badge(page, found) if found.scale is not None else None,
                ],
            ],
        ]
        for found in maps
    ]
    return app_shell(
        page,
        t("nav.maps"),
        div(class_="prose-page")[
            page_head(
                world.name if world is not None else "", t("nav.maps"), t("atlas.players.lede")
            ),
            ul(class_="list")[rows]
            if rows
            else empty_state(
                "map", t("atlas.players.empty_heading"), t("atlas.players.empty_text")
            ),
        ],
    )


def new_map_dialog(
    page: Page,
    world_id: str,
    *,
    adventure_id: str | None = None,
    error: str | None = None,
    is_open: bool = False,
) -> Element:
    """A new map in one go: its picture and, if wanted, a name (else the file's)."""
    t = page.t
    return tool_dialog(
        page,
        "map",
        t("atlas.new"),
        create_form(
            page,
            action=world_maps_url(world_id),
            submit=t("atlas.create"),
            upload=True,
            body=[
                alert(error),
                hidden("adventure", adventure_id or ""),
                label(for_="f-map-picture", class_="tok-drop img-drop")[
                    icon("upload"),
                    span[t("atlas.picture_drop", mb=MAX_BYTES // (1024 * 1024))],
                    input_el(
                        id="f-map-picture",
                        type="file",
                        name="picture",
                        accept=".jpg,.jpeg,.png,.webp",
                        required=True,
                    ),
                ],
                text_field(
                    "name",
                    t("adventures.name"),
                    value="",
                    autocomplete="off",
                    required=False,
                    optional_text=t("common.optional"),
                    hint=t("atlas.name_hint"),
                ),
            ],
        ),
        is_open=is_open,
    )


def maps_page(
    page: Page,
    world: World,
    maps: Sequence[WorldMap],
    *,
    notice_text: str | None = None,
    error: str | None = None,
    new: bool = False,
) -> Element:
    """A world's own maps, and **New map**. An adventure's maps are on its page."""
    t = page.t
    return app_shell(
        page,
        t("atlas.page_title", world=world.name),
        div(class_="prose-page")[
            crumbs(
                page,
                (t("nav.worlds"), WORLDS_URL),
                (world.name, world_url(world.id)),
                (t("atlas.heading"), None),
            ),
            page_head(
                t("adventures.world_eyebrow"),
                t("atlas.heading"),
                t("atlas.lede"),
                new_button(page, "map", t("atlas.new")),
            ),
            notice(notice_text),
            map_rows(page, maps)
            if maps
            else empty_state("map", t("atlas.empty_heading"), t("atlas.empty_text")),
            new_map_dialog(page, world.id, error=error, is_open=new),
        ],
        world_id=world.id,
    )


def adventure_maps_section(
    page: Page,
    adventure: Adventure,
    maps: Sequence[WorldMap],
    *,
    error: str | None = None,
) -> Element:
    """An adventure's own maps on its page; they stick to it (MAPS.md §3)."""
    t = page.t
    return section(class_="prose-section", id="maps")[
        div(class_="section-head")[
            h2(class_="section-title")[t("atlas.heading")],
            new_button(page, "map", t("atlas.new")),
        ],
        map_rows(page, maps) if maps else p(class_="field-hint")[t("atlas.adventure_empty")],
        new_map_dialog(
            page, adventure.world_id, adventure_id=adventure.id, error=error, is_open=bool(error)
        ),
    ]


def _crumbs(
    page: Page, world: World, adventure: Adventure | None, last: tuple[str, str | None]
) -> Element:
    t = page.t
    trail: list[tuple[str, str | None]] = [
        (t("nav.worlds"), WORLDS_URL),
        (world.name, world_url(world.id)),
    ]
    if adventure is not None:
        trail.append((adventure.name, f"{adventure_url(adventure.id)}#maps"))
    else:
        trail.append((t("atlas.heading"), world_maps_url(world.id)))
    trail.append(last)
    return crumbs(page, *trail)


def _words(page: Page) -> str:
    """What the map's script writes, in the reader's language."""
    t = page.t
    return json.dumps(
        {
            "total": t("atlas.ruler.total"),
            "pace": {k: t(f"travel.pace.{k}") for k in ("slow", "normal", "fast")},
            "days": {"one": t("atlas.ruler.days.one"), "other": t("atlas.ruler.days.other")},
            "hours": t("atlas.ruler.hours"),
            "start": t("atlas.ruler.start"),
            "line_start": t("atlas.scale.line_start"),
            "line_end": t("atlas.scale.line_end"),
            "line_drawn": t("atlas.scale.drawn"),
            "no_scale": t("atlas.no_scale"),
            "pin_place": t("atlas.pin.tap"),
            "pin_for": t("atlas.pin.tap_for"),
            "frame_start": t("atlas.frame.corner_start"),
            "frame_end": t("atlas.frame.corner_end"),
            "frame_drawn": t("atlas.frame.drawn"),
            "open": t("menu.open"),
            "go_to_map": t("atlas.pin.go_to_map"),
            "label": t("atlas.pin.label"),
            "note_text": t("atlas.pin.note_text"),
            "save": t("worlds.save"),
            "remove": t("atlas.pin.remove"),
            "gone": t("atlas.pin.gone"),
            "unframe": t("atlas.frame.remove"),
            "kinds": {k: t(f"atlas.pin.kind.{k}") for k in (*_PIN_ORDER, "reference", "note")},
            "label_place": t("atlas.label.tap"),
            "layer": t("atlas.layer.one"),
            "text": t("atlas.label.text"),
            "style": t("atlas.label.style"),
            "styles": {k: t(f"atlas.label.style.{k}") for k in LABEL_STYLES},
            "size": t("atlas.label.size"),
            "angle": t("atlas.label.angle"),
            "remove_label": t("atlas.label.remove"),
            "locked": t("atlas.layer.locked_note"),
            "route_place": t("atlas.route.tap"),
            "route_kinds": {k: t(f"atlas.route.kind.{k or 'country'}") for k in ROUTE_KINDS},
            "terrains": {n: t(f"travel.terrain.{n.lower()}") for n in TERRAINS},
            "no_terrain": t("atlas.route.no_terrain"),
            "difficult": t("travel.difficult"),
            "travel": t("atlas.route.travel"),
            "edit_points": t("atlas.route.edit_points"),
            "split": t("atlas.route.split"),
            "remove_point": t("atlas.route.remove_point"),
            "remove_route": t("atlas.route.remove"),
            "route_start": t("atlas.route.start"),
            "route_end": t("atlas.route.end"),
            "point": t("atlas.route.point"),
            "party": t("atlas.route.party"),
            "route_name": t("atlas.route.name"),
            "edit": t("players.edit"),
            "grid_place": t("atlas.grid.tap"),
            "grid_placed": t("atlas.grid.placed"),
            "hexes": {"one": t("atlas.grid.hexes.one"), "other": t("atlas.grid.hexes.other")},
        },
        ensure_ascii=False,
    )


def _path(page: Page, path: Sequence[WorldMap], *, player: bool = False) -> Node:
    """The maps this one lies in, the widest first, each a link."""
    if not path:
        return None
    parts: list[Node] = []
    for found in path:
        href = player_map_url(found.id) if player else map_url(found.id)
        parts += [a(href=href)[found.name], " › "]
    return nav(class_="map-path", aria_label=page.t("atlas.frame.path"))[parts[:-1]]


def _disagree(page: Page, found: WorldMap, disagree: tuple[float, float] | None) -> Node:
    """Says when the frame on the larger map and the map's own scale disagree."""
    if disagree is None:
        return None
    framed, own = disagree
    long = is_long(found)
    return p(class_="field-hint map-disagree")[
        icon("ruler", "xs"),
        " ",
        page.t(
            "atlas.frame.disagree",
            framed=distance_text(page, framed, long),
            own=distance_text(page, own, long),
        ),
    ]


def tiling_panel(
    page: Page, found: WorldMap, job: TileJob | None, *, player: bool = False
) -> Element:
    """While a large map's tiles are cut: how far it got, asked again every second; once
    they are there, the answer reloads the page. A failure says so."""
    t = page.t
    if job is not None and job.error:
        return div(class_="panel map-tiling", id="map-tiling")[
            alert(t("atlas.tiling.failed", error=job.error)),
        ]
    done, total = (job.done, job.total) if job is not None else (0, 0)
    return div(
        class_="panel map-tiling",
        id="map-tiling",
        hx_get=f"{player_map_url(found.id) if player else map_url(found.id)}/status",
        hx_trigger="every 1s",
        hx_swap="outerHTML",
    )[
        p[icon("loader-circle", "sm"), " ", t("atlas.tiling.running")],
        div(class_="map-progress")[
            Markup(f'<progress max="{max(total, 1)}" value="{done}"></progress>'),
            span(class_="muted")[
                t("atlas.tiling.count", done=page.u.number(done), total=page.u.number(total))
            ],
        ],
        p(class_="field-hint")[t("atlas.tiling.hint")],
    ]


def _scale_dialog(page: Page, found: WorldMap) -> Element:
    """The scale: a line drawn on the map and how long it is, or the map's width."""
    t = page.t
    scale = found.scale
    way = scale.way if scale is not None else "line"
    unit = scale.unit if scale is not None else ("km" if page.u.metric else "mi")
    amount = page.u.number(scale.amount) if scale is not None else ""
    line = scale.line if scale is not None and len(scale.line) == 4 else []
    url = map_url(found.id)
    return tool_dialog(
        page,
        "scale",
        t("atlas.scale.title"),
        div[
            form(method="post", action=f"{url}/scale", class_="stack-form map-scale-form")[
                p(class_="field-hint")[t("atlas.scale.lede")],
                fieldset(class_="delete-choice")[
                    legend(class_="field-label")[t("atlas.scale.way")],
                    label[
                        input_el(type="radio", name="way", value="line", checked=way == "line"),
                        span[t("atlas.scale.way.line")],
                    ],
                    label[
                        input_el(type="radio", name="way", value="width", checked=way == "width"),
                        span[t("atlas.scale.way.width")],
                    ],
                ],
                div(class_="map-scale-line", data_scale_line="")[
                    button(type="button", class_="btn btn-sm", data_map_draw="")[
                        icon("pen-line", "sm"), span[t("atlas.scale.draw")]
                    ],
                    span(class_="muted", data_line_state="")[
                        t("atlas.scale.drawn") if line else t("atlas.scale.not_drawn")
                    ],
                    [
                        hidden(name, f"{value:.1f}" if line else "")
                        for name, value in zip(
                            ("x1", "y1", "x2", "y2"), line or [0.0] * 4, strict=True
                        )
                    ],
                ],
                div(class_="field map-scale-amount")[
                    label(for_="f-scale-amount")[t("atlas.scale.amount")],
                    div(class_="inline-fields")[
                        input_el(
                            id="f-scale-amount",
                            name="amount",
                            type="text",
                            inputmode="decimal",
                            value=amount,
                            required=True,
                            autocomplete="off",
                        ),
                        select(name="unit", aria_label=t("atlas.scale.unit"))[
                            [
                                option(value=u, selected=u == unit)[t(f"atlas.unit.{u}")]
                                for u in UNITS
                            ]
                        ],
                    ],
                    p(class_="field-hint")[t("atlas.scale.hint")],
                ],
                div(class_="form-actions")[
                    button(type="submit", class_="btn btn-primary")[
                        icon("check"), span[t("worlds.save")]
                    ],
                ],
            ],
            form(method="post", action=f"{url}/scale/clear", class_="form-actions")[
                button(type="submit", class_="btn btn-sm btn-ghost btn-danger")[
                    icon("x", "sm"), span[t("atlas.scale.clear")]
                ]
            ]
            if scale is not None
            else None,
        ],
        keep_open=True,
    )


def _grid_dialog(page: Page, found: WorldMap) -> Element:
    """The hex grid: how far a hex is across, which way the hexes stand, and a tap on
    a hex's centre to line it up with a grid printed on the picture."""
    t = page.t
    grid = found.grid
    unit = grid.unit if grid is not None else (found.scale.unit if found.scale else "mi")
    url = map_url(found.id)
    orientation = grid.orientation if grid is not None else "flat"
    return tool_dialog(
        page,
        "grid",
        t("atlas.grid.title"),
        div[
            form(method="post", action=f"{url}/grid", class_="stack-form map-grid-form")[
                p(class_="field-hint")[t("atlas.grid.lede")],
                div(class_="field")[
                    label(for_="f-grid-amount")[t("atlas.grid.amount")],
                    div(class_="inline-fields")[
                        input_el(
                            id="f-grid-amount",
                            name="amount",
                            type="text",
                            inputmode="decimal",
                            value=page.u.number(grid.amount) if grid is not None else "",
                            required=True,
                            autocomplete="off",
                        ),
                        select(name="unit", aria_label=t("atlas.scale.unit"))[
                            [
                                option(value=u, selected=u == unit)[t(f"atlas.unit.{u}")]
                                for u in UNITS
                            ]
                        ],
                    ],
                    p(class_="field-hint")[t("atlas.grid.amount_hint")],
                ],
                fieldset(class_="delete-choice")[
                    legend(class_="field-label")[t("atlas.grid.orientation")],
                    [
                        label[
                            input_el(
                                type="radio",
                                name="orientation",
                                value=way,
                                checked=orientation == way,
                            ),
                            span[t(f"atlas.grid.orientation.{way}")],
                        ]
                        for way in ("flat", "pointy")
                    ],
                ],
                div(class_="map-scale-line")[
                    button(type="button", class_="btn btn-sm", data_map_grid_place="")[
                        icon("target", "sm"), span[t("atlas.grid.place")]
                    ],
                    span(class_="muted", data_grid_state="")[
                        t("atlas.grid.placed") if grid is not None else ""
                    ],
                    hidden("x", f"{grid.x:.1f}" if grid is not None else ""),
                    hidden("y", f"{grid.y:.1f}" if grid is not None else ""),
                ],
                div(class_="form-actions")[
                    button(type="submit", class_="btn btn-primary")[
                        icon("check"), span[t("worlds.save")]
                    ],
                ],
            ],
            form(method="post", action=f"{url}/grid/clear", class_="form-actions")[
                button(type="submit", class_="btn btn-sm btn-ghost btn-danger")[
                    icon("x", "sm"), span[t("atlas.grid.clear")]
                ]
            ]
            if grid is not None
            else None,
        ],
        keep_open=True,
    )


@dataclass(frozen=True)
class PinView:
    """A pin as the viewer draws it: where, what it is called, where it leads, its
    token, a line of its target's notes, and whether its target is gone."""

    id: str
    layer: str
    x: float
    y: float
    kind: str
    name: str
    label: str
    url: str | None
    map_url: str | None
    token: str
    excerpt: str
    gone: bool
    note: str = ""


@dataclass(frozen=True)
class FrameView:
    """A map lying on this one: its name, its page, its rectangle here."""

    id: str
    name: str
    url: str
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class LayerView:
    """A layer as the panel and the viewer show it: whether a newcomer sees it
    (``visible``) and whether this user does (``shown``), and how much sits on it."""

    id: str
    name: str
    role: str
    visible: bool
    shown: bool
    locked: bool
    from_zoom: float | None
    count: int
    players: bool = False


@dataclass(frozen=True)
class LabelView:
    """A name written on the map."""

    id: str
    layer: str
    x: float
    y: float
    text: str
    style: str
    size: float
    angle: float


@dataclass(frozen=True)
class RouteView:
    """A road as the viewer draws it: its points, its stretches (start, terrain,
    difficult, miles), its length in miles (None without a scale) and its stops."""

    id: str
    layer: str
    name: str
    kind: str
    points: list[list[float]]
    stretches: list[dict[str, Any]]
    miles: float | None
    stops: list[dict[str, Any]]


@dataclass(frozen=True)
class Candidate:
    """Something a pin may point at, for the pin dialog."""

    kind: str
    id: str
    name: str
    where: str = ""


@dataclass(frozen=True)
class MapContext:
    """What lies on a map and around it (documentation/MAPS.md §6): its pins, the maps
    lying on it, the maps it lies in, what may be pinned or framed on it, and whether
    its frame and its own scale disagree (``(by the frame, its own)`` in feet)."""

    pins: Sequence[PinView] = ()
    frames: Sequence[FrameView] = ()
    path: Sequence[WorldMap] = ()
    candidates: Sequence[Candidate] = ()
    # Opened to pin this, from its own page: the pin tool on, it chosen in the dialog.
    pin_for: Candidate | None = None
    framable: Sequence[WorldMap] = ()
    disagree: tuple[float, float] | None = None
    focus: str = ""
    layers: Sequence[LayerView] = ()
    labels: Sequence[LabelView] = ()
    routes: Sequence[RouteView] = ()
    # Where the party stopped on one of the map's roads: x, y, route, at, to.
    party: dict[str, Any] | None = None
    # Whether the party may travel along the map's roads: the world plays with the road,
    # and the map has a scale.
    travel: bool = False
    # The hex grid as the viewer draws it: across (px), orientation, x, y, shown.
    grid: dict[str, Any] | None = None
    # The party's trail: its legs (points, text), shown, and whether players see it.
    trail: dict[str, Any] | None = None


# The kinds in the pin dialog's list, in its order; entries of the books are searched.
_PIN_ORDER = (
    "place",
    "npc",
    "encounter",
    "treasure",
    "adventure",
    "chapter",
    "map",
    "character",
    "quest",
    "faction",
    "handout",
    "session",
)
# How many entries of the books the pin dialog's search offers.
PIN_SEARCH_LIMIT = 20


def pin_entries(page: Page, hits: Sequence[SearchHit], query: str) -> Element:
    """Entries of the books a pin may point at, found by the dialog's search, each a
    choice like the world's own objects."""
    t = page.t
    rows = [
        label(class_="map-pin-choice")[
            input_el(
                type="radio",
                name="choice",
                value=f"reference:{h.row.category}|{h.row.name}|{h.row.source}",
                required=True,
            ),
            span(class_="map-pin-choice-kind muted")[t(f"search.kind.{h.row.category}")],
            span[h.label or h.row.name],
            span(class_="muted")[shown_source(h.row.source)],
        ]
        for h in hits
    ]
    return div(class_="map-pin-list map-pin-books", id="map-pin-books")[
        rows if rows else p(class_="field-hint")[t("atlas.pin.books_none") if query.strip() else ""]
    ]


def _pin_dialog(page: Page, found: WorldMap, candidates: Sequence[Candidate]) -> Element:
    """What a new pin points at: something of the world from a filtered list, a new
    place made here, or a note. The spot comes from the tap on the map."""
    t = page.t
    rows = [
        label(class_="map-pin-choice", data_pin_text=f"{c.name} {c.where}".casefold())[
            input_el(type="radio", name="choice", value=f"{c.kind}:{c.id}", required=True),
            span(class_="map-pin-choice-kind muted")[t(f"atlas.pin.kind.{c.kind}")],
            span[c.name],
            span(class_="muted")[c.where] if c.where else None,
        ]
        for kind in _PIN_ORDER
        for c in candidates
        if c.kind == kind
    ]
    return tool_dialog(
        page,
        "pin",
        t("atlas.pin.title"),
        form(method="post", action=f"{map_url(found.id)}/pins", class_="stack-form map-pin-form")[
            hidden("x", ""),
            hidden("y", ""),
            hidden("layer", ""),
            p(class_="field-hint")[t("atlas.pin.lede")],
            label(class_="map-pin-choice map-pin-new")[
                input_el(type="radio", name="choice", value="new", required=True),
                span[t("atlas.pin.new_place")],
                input_el(
                    type="text",
                    name="place_name",
                    placeholder=t("atlas.pin.new_place_name"),
                    aria_label=t("atlas.pin.new_place_name"),
                    autocomplete="off",
                ),
            ],
            label(class_="map-pin-choice map-pin-new")[
                input_el(type="radio", name="choice", value="note", required=True),
                span[t("atlas.pin.note")],
            ],
            input_el(
                type="search",
                class_="map-pin-filter",
                placeholder=t("atlas.pin.filter"),
                aria_label=t("atlas.pin.filter"),
                data_pin_filter="",
            )
            if rows
            else None,
            div(class_="map-pin-list")[rows] if rows else None,
            div(class_="field map-pin-books-field")[
                label(for_="f-pin-books")[t("atlas.pin.books")],
                input_el(
                    id="f-pin-books",
                    type="search",
                    name="q",
                    placeholder=t("atlas.pin.books_hint"),
                    autocomplete="off",
                    hx_get=f"{map_url(found.id)}/pin-search",
                    hx_trigger="input changed delay:250ms, search",
                    hx_target="#map-pin-books",
                    hx_swap="outerHTML",
                ),
                div(class_="map-pin-list map-pin-books", id="map-pin-books"),
            ],
            text_field(
                "label",
                t("atlas.pin.label"),
                value="",
                autocomplete="off",
                required=False,
                optional_text=t("common.optional"),
                hint=t("atlas.pin.label_hint"),
            ),
            div(class_="field map-pin-note")[
                label(for_="f-pin-note")[t("atlas.pin.note_text")],
                textarea(id="f-pin-note", name="note", rows="3"),
            ],
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-primary")[
                    icon("map-pin", "sm"), span[t("atlas.pin.submit")]
                ]
            ],
        ],
        keep_open=True,
    )


def _frame_dialog(page: Page, found: WorldMap, framable: Sequence[WorldMap]) -> Element:
    """Lay another map on this one: which, then its rectangle drawn with two taps."""
    t = page.t
    return tool_dialog(
        page,
        "frame",
        t("atlas.frame.title"),
        form(
            method="post", action=f"{map_url(found.id)}/frames", class_="stack-form map-frame-form"
        )[
            p(class_="field-hint")[t("atlas.frame.lede")],
            div(class_="field")[
                label(for_="f-frame-child")[t("atlas.frame.which")],
                select(id="f-frame-child", name="child")[
                    [option(value=m.id)[m.name] for m in framable]
                ],
            ],
            div(class_="map-scale-line")[
                button(type="button", class_="btn btn-sm", data_map_frame_draw="")[
                    icon("pen-line", "sm"), span[t("atlas.frame.draw")]
                ],
                span(class_="muted", data_frame_state="")[t("atlas.frame.not_drawn")],
                [hidden(name, "") for name in ("x1", "y1", "x2", "y2")],
            ],
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-primary")[
                    icon("check"), span[t("atlas.frame.submit")]
                ]
            ],
        ],
        keep_open=True,
    )


LABEL_STYLES = ("settlement", "region", "water", "feature")


def _label_dialog(page: Page, found: WorldMap) -> Element:
    """A name written where the map was tapped: its words, style, size and turn. The
    size is in the picture's pixels; the script fills one that reads well at the zoom
    it was tapped at."""
    t = page.t
    return tool_dialog(
        page,
        "label",
        t("atlas.label.title"),
        form(
            method="post", action=f"{map_url(found.id)}/labels", class_="stack-form map-label-form"
        )[
            hidden("x", ""),
            hidden("y", ""),
            hidden("layer", ""),
            text_field("text", t("atlas.label.text"), value="", autocomplete="off"),
            div(class_="field")[
                label(for_="f-label-style")[t("atlas.label.style")],
                select(id="f-label-style", name="style")[
                    [option(value=k)[t(f"atlas.label.style.{k}")] for k in LABEL_STYLES]
                ],
            ],
            div(class_="inline-fields map-label-numbers")[
                div(class_="field")[
                    label(for_="f-label-size")[t("atlas.label.size")],
                    input_el(id="f-label-size", name="size", type="number", min="1", step="any"),
                ],
                div(class_="field")[
                    label(for_="f-label-angle")[t("atlas.label.angle")],
                    input_el(
                        id="f-label-angle",
                        name="angle",
                        type="number",
                        min="-180",
                        max="180",
                        step="1",
                        value="0",
                    ),
                ],
            ],
            p(class_="field-hint")[t("atlas.label.hint")],
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-primary")[
                    icon("check"), span[t("atlas.label.submit")]
                ]
            ],
        ],
        keep_open=True,
    )


def _grid_row(page: Page, grid: Mapping[str, Any] | None, *, player: bool = False) -> Node:
    """The hex grid in the layer panel: shown or hidden for this user."""
    if grid is None:
        return None
    t = page.t
    return li(class_="map-layer" + ("" if grid["shown"] else " is-hidden"), data_layer="grid")[
        div(class_="map-layer-row")[
            None if player else span(class_="map-layer-current"),
            button(
                type="button",
                class_="btn btn-sm btn-ghost map-layer-eye",
                data_layer_eye="grid",
                aria_pressed="true" if grid["shown"] else "false",
                aria_label=t("atlas.layer.shown_of", name=t("atlas.grid.layer")),
                title=t("atlas.layer.shown"),
            )[icon("eye", "sm")],
            span(class_="map-layer-name")[icon("hexagon", "xs"), " ", t("atlas.grid.layer")],
        ]
    ]


def _trail_row(
    page: Page, url: str, trail: Mapping[str, Any] | None, *, player: bool = False
) -> Node:
    """The party's trail in the layer panel: shown or hidden for this user; for the
    gamemaster under its ⋯ whether the players see it, and clearing it."""
    if trail is None:
        return None
    t = page.t
    name = t("atlas.trail.layer")
    return li(class_="map-layer" + ("" if trail["shown"] else " is-hidden"), data_layer="trail")[
        div(class_="map-layer-row")[
            None if player else span(class_="map-layer-current"),
            button(
                type="button",
                class_="btn btn-sm btn-ghost map-layer-eye",
                data_layer_eye="trail",
                aria_pressed="true" if trail["shown"] else "false",
                aria_label=t("atlas.layer.shown_of", name=name),
                title=t("atlas.layer.shown"),
            )[icon("eye", "sm")],
            span(class_="map-layer-name")[icon("footprints", "xs"), " ", name],
            span(class_="muted map-layer-players", title=t("atlas.layer.players"))[
                icon("users", "xs")
            ]
            if trail["players"] and not player
            else None,
            span(class_="map-layer-count muted")[str(len(trail["legs"]))] if not player else None,
        ],
        None
        if player
        else details(class_="map-layer-more")[
            summary(aria_label=t("menu.more", name=name))[icon("ellipsis", "sm")],
            form(method="post", action=f"{url}/trail/players", class_="stack-form")[
                hidden("shown", "" if trail["players"] else "1"),
                button(type="submit", class_="btn btn-sm")[
                    icon("users", "sm"),
                    span[
                        t(
                            "atlas.trail.hide_players"
                            if trail["players"]
                            else "atlas.trail.show_players"
                        )
                    ],
                ],
            ],
            form(method="post", action=f"{url}/trail/clear", class_="stack-form")[
                button(type="submit", class_="btn btn-sm btn-ghost btn-danger")[
                    icon("trash-2", "sm"), span[t("atlas.trail.clear")]
                ],
            ],
        ],
    ]


def _player_layer_panel(
    page: Page,
    layers: Sequence[LayerView],
    grid: Mapping[str, Any] | None,
    trail: Mapping[str, Any] | None = None,
) -> Element:
    """The layers a player sees, the top one first, each to show or hide for them."""
    t = page.t
    rows = [
        li(class_="map-layer" + (" is-hidden" if not layer.shown else ""), data_layer=layer.id)[
            div(class_="map-layer-row")[
                button(
                    type="button",
                    class_="btn btn-sm btn-ghost map-layer-eye",
                    data_layer_eye=layer.id,
                    aria_pressed="true" if layer.shown else "false",
                    aria_label=t("atlas.layer.shown_of", name=layer.name),
                    title=t("atlas.layer.shown"),
                )[icon("eye", "sm")],
                span(class_="map-layer-name")[layer.name],
            ]
        ]
        for layer in reversed(layers)
    ]
    return details(class_="map-layers panel", id="layers", open=True)[
        summary(class_="map-layers-head")[icon("map", "sm"), span[t("atlas.layer.heading")]],
        ul(class_="map-layer-list")[
            rows, _grid_row(page, grid, player=True), _trail_row(page, "", trail, player=True)
        ],
    ]


def _layer_panel(
    page: Page,
    found: WorldMap,
    layers: Sequence[LayerView],
    grid: Mapping[str, Any] | None = None,
    trail: Mapping[str, Any] | None = None,
) -> Element:
    """The map's layers, the top one first: show or hide each (for this user), choose the
    one the tools add to, lock it, and under its ⋯ rename it, say whether a newcomer
    sees it and whether the players do, show it only from the current zoom in, move it,
    delete it. The hex grid last, to show or hide."""
    t = page.t
    url = map_url(found.id)
    rows = []
    shown_order = list(reversed(layers))
    for index, layer in enumerate(shown_order):
        base = f"{url}/layers/{layer.id}"
        others = [x for x in layers if x.id != layer.id]
        rows.append(
            li(
                class_="map-layer" + (" is-hidden" if not layer.shown else ""),
                data_layer=layer.id,
            )[
                div(class_="map-layer-row")[
                    label(class_="map-layer-current", title=t("atlas.layer.current"))[
                        input_el(
                            type="radio",
                            name="current-layer",
                            value=layer.id,
                            disabled=layer.locked,
                            aria_label=t("atlas.layer.current_of", name=layer.name),
                        ),
                    ],
                    button(
                        type="button",
                        class_="btn btn-sm btn-ghost map-layer-eye",
                        data_layer_eye=layer.id,
                        aria_pressed="true" if layer.shown else "false",
                        aria_label=t("atlas.layer.shown_of", name=layer.name),
                        title=t("atlas.layer.shown"),
                    )[icon("eye", "sm")],
                    span(class_="map-layer-name")[layer.name],
                    span(class_="muted map-layer-players", title=t("atlas.layer.players"))[
                        icon("users", "xs")
                    ]
                    if layer.players
                    else None,
                    span(class_="map-layer-count muted")[str(layer.count)],
                    span(class_="muted map-layer-zoom", title=t("atlas.layer.from_zoom_hint"))[
                        icon("scaling", "xs")
                    ]
                    if layer.from_zoom is not None
                    else None,
                    button(
                        type="button",
                        class_="btn btn-sm btn-ghost map-layer-lock",
                        data_layer_lock=layer.id,
                        aria_pressed="true" if layer.locked else "false",
                        aria_label=t("atlas.layer.locked_of", name=layer.name),
                        title=t("atlas.layer.locked"),
                    )[icon("lock", "sm")],
                ],
                details(class_="map-layer-more")[
                    summary(aria_label=t("menu.more", name=layer.name))[icon("ellipsis", "sm")],
                    form(method="post", action=base, class_="stack-form map-layer-form")[
                        text_field(
                            "name", t("atlas.layer.name"), value=layer.name, autocomplete="off"
                        ),
                        label(class_="check-inline")[
                            input_el(
                                type="checkbox", name="visible", value="1", checked=layer.visible
                            ),
                            span[t("atlas.layer.visible")],
                        ],
                        label(class_="check-inline")[
                            input_el(
                                type="checkbox", name="locked", value="1", checked=layer.locked
                            ),
                            span[t("atlas.layer.locked")],
                        ],
                        label(class_="check-inline")[
                            input_el(
                                type="checkbox", name="players", value="1", checked=layer.players
                            ),
                            span[t("atlas.layer.players")],
                        ],
                        button(type="submit", class_="btn btn-sm")[t("worlds.save")],
                    ],
                    div(class_="map-layer-actions")[
                        form(method="post", action=f"{base}/zoom", class_="inline-form")[
                            hidden("zoom", ""),
                            button(type="submit", class_="btn btn-sm", data_layer_zoom_here="")[
                                t("atlas.layer.from_here")
                            ],
                        ],
                        form(method="post", action=f"{base}/zoom", class_="inline-form")[
                            button(type="submit", class_="btn btn-sm btn-ghost")[
                                t("atlas.layer.always")
                            ]
                        ]
                        if layer.from_zoom is not None
                        else None,
                        form(method="post", action=f"{base}/move", class_="inline-form")[
                            hidden("step", "1"),
                            button(
                                type="submit",
                                class_="btn btn-sm btn-ghost",
                                disabled=index == 0,
                                aria_label=t("atlas.layer.up"),
                            )[icon("arrow-up", "sm")],
                        ],
                        form(method="post", action=f"{base}/move", class_="inline-form")[
                            hidden("step", "-1"),
                            button(
                                type="submit",
                                class_="btn btn-sm btn-ghost",
                                disabled=index == len(shown_order) - 1,
                                aria_label=t("atlas.layer.down"),
                            )[icon("arrow-down", "sm")],
                        ],
                    ],
                    form(
                        method="post", action=f"{base}/delete", class_="stack-form map-layer-delete"
                    )[
                        div(class_="field")[
                            label(for_=f"f-del-{layer.id}")[t("atlas.layer.delete_contents")],
                            select(id=f"f-del-{layer.id}", name="contents")[
                                [
                                    option(value=x.id)[t("atlas.layer.move_to", name=x.name)]
                                    for x in others
                                ],
                                option(value="")[t("atlas.layer.delete_too")],
                            ],
                        ]
                        if layer.count
                        else None,
                        button(type="submit", class_="btn btn-sm btn-ghost btn-danger")[
                            icon("trash-2", "sm"), span[t("atlas.layer.delete")]
                        ],
                    ]
                    if others
                    else None,
                ],
            ]
        )
    return details(class_="map-layers panel", id="layers", open=True)[
        summary(class_="map-layers-head")[icon("map", "sm"), span[t("atlas.layer.heading")]],
        ul(class_="map-layer-list")[
            rows, _grid_row(page, grid), _trail_row(page, map_url(found.id), trail)
        ],
        form(method="post", action=f"{url}/layers", class_="map-layer-new inline-form")[
            input_el(
                type="text",
                name="name",
                placeholder=t("atlas.layer.new"),
                aria_label=t("atlas.layer.new"),
                autocomplete="off",
                maxlength="60",
                required=True,
            ),
            button(type="submit", class_="btn btn-sm")[icon("plus", "sm")],
        ],
    ]


def _layers_json(layers: Sequence[LayerView]) -> str:
    return json.dumps([vars(x) for x in layers], ensure_ascii=False)


def _labels_json(labels: Sequence[LabelView]) -> str:
    return json.dumps([vars(x) for x in labels], ensure_ascii=False)


ROUTE_KINDS = ("road", "trail", "river", "sea", "")


def _route_dialog(page: Page, found: WorldMap) -> Element:
    """A road just drawn: its name and kind. Its points and each segment's terrain come
    from the drawing."""
    t = page.t
    return tool_dialog(
        page,
        "route",
        t("atlas.route.title"),
        form(
            method="post", action=f"{map_url(found.id)}/routes", class_="stack-form map-route-form"
        )[
            hidden("points", ""),
            hidden("terrains", ""),
            hidden("difficult", ""),
            hidden("layer", ""),
            text_field(
                "name",
                t("atlas.route.name"),
                value="",
                autocomplete="off",
                required=False,
                optional_text=t("common.optional"),
            ),
            div(class_="field")[
                label(for_="f-route-kind")[t("atlas.route.kind")],
                select(id="f-route-kind", name="kind")[
                    [option(value=k)[t(f"atlas.route.kind.{k or 'country'}")] for k in ROUTE_KINDS]
                ],
                p(class_="field-hint")[t("atlas.route.kind_hint")],
            ],
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-primary")[
                    icon("check"), span[t("atlas.route.submit")]
                ]
            ],
        ],
        keep_open=True,
    )


def _route_bar(page: Page) -> Element:
    """While a road is drawn: the terrain the next segments go through, whether it is
    difficult, and done or cancel."""
    t = page.t
    return div(class_="map-route-bar", hidden=True, data_route_bar="")[
        label(class_="map-route-bar-field")[
            span[t("travel.terrain")],
            select(name="terrain", data_route_terrain="")[
                option(value="")[t("atlas.route.no_terrain")],
                [option(value=n)[t(f"travel.terrain.{n.lower()}")] for n in TERRAINS],
            ],
        ],
        label(class_="check-inline")[
            input_el(type="checkbox", data_route_difficult=""), span[t("travel.difficult")]
        ],
        button(type="button", class_="btn btn-sm btn-primary", data_route_done="")[
            icon("check", "sm"), span[t("atlas.route.done")]
        ],
        button(type="button", class_="btn btn-sm btn-ghost", data_route_cancel="")[
            t("common.cancel")
        ],
    ]


def _travel_dialog(page: Page, found: WorldMap, choices: Mapping[str, Any]) -> Element:
    """Travel along a road: from where to where (filled from the road's stops by the
    script), then the road's choices as the clock's travel form asks them."""
    t = page.t
    head = div(class_="tool-grid map-travel-head")[
        hidden("route", ""),
        div(class_="field")[
            label(for_="f-travel-from")[t("atlas.route.from")],
            select(id="f-travel-from", name="from", data_travel_from=""),
        ],
        div(class_="field")[
            label(for_="f-travel-to")[t("atlas.route.to")],
            select(id="f-travel-to", name="to", data_travel_to=""),
        ],
        p(class_="field-hint tool-span", data_travel_way="")[""],
    ]
    return tool_dialog(
        page,
        "travel",
        t("atlas.route.travel_title"),
        route_travel_form(page, f"{map_url(found.id)}/travel", head, **choices),
        keep_open=True,
    )


def _picture_dialog(
    page: Page,
    found: WorldMap,
    layers: Sequence[LayerView],
    grid: Mapping[str, Any] | None,
    recipients: Sequence[tuple[str, str]],
) -> Element:
    """The map as one picture: the part in view or the whole, the layers drawn on it
    (those the players see, to begin with), then downloaded or handed to characters."""
    t = page.t
    url = map_url(found.id)
    return tool_dialog(
        page,
        "picture",
        t("atlas.picture.title"),
        form(method="post", action=f"{url}/handout", class_="stack-form map-picture-form")[
            p(class_="field-hint")[t("atlas.picture.lede")],
            [hidden("area", "") for _ in range(4)],
            fieldset(class_="delete-choice")[
                legend(class_="field-label")[t("atlas.picture.what")],
                label[
                    input_el(type="radio", name="whole", value="", checked=True),
                    span[t("atlas.picture.view")],
                ],
                label[
                    input_el(type="radio", name="whole", value="1"),
                    span[t("atlas.picture.whole")],
                ],
            ],
            fieldset(class_="delete-choice")[
                legend(class_="field-label")[t("atlas.picture.layers")],
                [
                    label[
                        input_el(type="checkbox", name="layers", value=x.id, checked=x.players),
                        span[x.name],
                    ]
                    for x in reversed(layers)
                ],
                label[
                    input_el(type="checkbox", name="grid", value="1"),
                    span[t("atlas.grid.layer")],
                ]
                if grid is not None
                else None,
            ],
            div(class_="form-actions")[
                button(
                    type="submit",
                    class_="btn",
                    formaction=f"{url}/picture",
                    data_picture_download="",
                )[icon("download", "sm"), span[t("atlas.picture.download")]],
            ],
            text_field(
                "title",
                t("journal.title_label"),
                value=found.name,
                autocomplete="off",
                required=False,
            ),
            fieldset(class_="delete-choice")[
                legend(class_="field-label")[t("atlas.picture.share")],
                [
                    label[
                        input_el(type="checkbox", name="characters", value=ident, checked=True),
                        span[name],
                    ]
                    for ident, name in recipients
                ],
                p(class_="field-hint")[t("atlas.picture.nobody")] if not recipients else None,
            ],
            div(class_="form-actions")[
                button(type="submit", class_="btn btn-primary", disabled=not recipients)[
                    icon("send", "sm"), span[t("atlas.picture.send")]
                ],
            ],
        ],
        keep_open=True,
    )


def _pins_json(pins: Sequence[PinView]) -> str:
    return json.dumps([vars(p) for p in pins], ensure_ascii=False)


def _frames_json(frames: Sequence[FrameView]) -> str:
    return json.dumps([vars(f) for f in frames], ensure_ascii=False)


def map_page(
    page: Page,
    found: WorldMap,
    world: World,
    adventure: Adventure | None,
    *,
    ready: bool,
    job: TileJob | None,
    view: MapView | None,
    around: MapContext | None = None,
    notes: Markup | None = None,
    travel_choices: Mapping[str, Any] | None = None,
    notice_text: str | None = None,
    error: str | None = None,
    player: bool = False,
    recipients: Sequence[tuple[str, str]] = (),
) -> Element:
    """One map in its viewer: zoom and drag, the ruler, the scale, its pins and the maps
    lying on it. As a ``player`` sees it: what lies on the layers they see, the ruler,
    nothing to change."""
    around = around or MapContext()
    t = page.t
    url = player_map_url(found.id) if player else map_url(found.id)
    tiled = needs_tiles(found.width, found.height)
    scale = found.scale
    image = f"{url}/image" if player else media_url(found.scope.record_id, found.image)
    stage = div(
        id="map-stage",
        class_="map-stage",
        data_map=found.id,
        data_width=str(found.width),
        data_height=str(found.height),
        data_zoom=str(max_zoom(found.width, found.height)),
        data_tiles=f"{url}/tiles" if tiled else None,
        data_digest=found.digest[:16],
        data_image=None if tiled else image,
        data_base=url,
        data_player="1" if player else None,
        data_feet=f"{scale.feet_per_px:.8g}" if scale is not None else "",
        data_long="1" if is_long(found) else "",
        data_metric="1" if page.u.metric else "",
        data_lang=page.lang,
        data_view=json.dumps(view.model_dump()) if view is not None else "",
        data_words=_words(page),
        data_pins=_pins_json(around.pins),
        data_frames=_frames_json(around.frames),
        data_layers=_layers_json(around.layers),
        data_labels=_labels_json(around.labels),
        data_routes=json.dumps([vars(r) for r in around.routes], ensure_ascii=False),
        data_party=json.dumps(around.party) if around.party else None,
        data_travel="1" if around.travel and travel_choices is not None else None,
        data_focus=around.focus or None,
        data_pin_for=f"{around.pin_for.kind}:{around.pin_for.id}" if around.pin_for else None,
        data_pin_for_name=around.pin_for.name if around.pin_for else None,
        data_grid=json.dumps(around.grid) if around.grid else None,
        data_trail=json.dumps(around.trail, ensure_ascii=False) if around.trail else None,
        role="application",
        aria_label=t("atlas.viewer", name=found.name),
    )
    if player:
        return app_shell(
            page,
            found.name,
            div(class_="map-page")[
                link(rel="stylesheet", href=asset("/static/js/leaflet/leaflet.css")),
                crumbs(page, (t("nav.maps"), PLAYER_MAPS_URL), (found.name, None)),
                div(class_="map-head")[
                    div(class_="map-head-main")[
                        span(class_="eyebrow")[
                            adventure.name if adventure is not None else t("atlas.world_map")
                        ],
                        _path(page, around.path, player=True),
                        h1[found.name],
                        scale_badge(page, found),
                    ],
                ],
                div(class_="map-toolbar", role="toolbar", aria_label=t("atlas.tools"))[
                    button(
                        type="button",
                        class_="btn btn-sm",
                        data_map_tool="ruler",
                        aria_pressed="false",
                    )[icon("ruler", "sm"), span[t("atlas.ruler.button")]]
                    if scale is not None
                    else None,
                    span(class_="map-readout muted", aria_live="polite", data_map_readout=""),
                ],
                div(class_="map-body")[
                    stage if ready else tiling_panel(page, found, job, player=True),
                    _player_layer_panel(page, around.layers, around.grid, around.trail)
                    if ready and (around.layers or around.grid)
                    else None,
                ],
            ],
            main_class="shell-main map-main",
            extra_scripts=(*MAP_SCRIPTS, "/static/js/htmx.min.js"),
        )
    return app_shell(
        page,
        found.name,
        div(class_="map-page")[
            link(rel="stylesheet", href=asset("/static/js/leaflet/leaflet.css")),
            _crumbs(page, world, adventure, (found.name, None)),
            notice(notice_text),
            alert(error),
            div(class_="map-head")[
                div(class_="map-head-main")[
                    span(class_="eyebrow")[
                        adventure.name if adventure is not None else t("atlas.world_map")
                    ],
                    _path(page, around.path),
                    h1[found.name],
                    scale_badge(page, found),
                    _disagree(page, found, around.disagree),
                ],
                div(class_="map-head-actions")[
                    form(method="post", action=f"{url}/players", class_="inline-form")[
                        hidden("shown", "" if found.players else "1"),
                        button(
                            type="submit",
                            class_="btn btn-ghost map-players-switch",
                            aria_pressed="true" if found.players else "false",
                            title=t("atlas.players.hide_hint")
                            if found.players
                            else t("atlas.players.show_hint"),
                        )[
                            icon("users" if found.players else "eye-off", "sm"),
                            span[
                                t("atlas.players.shown")
                                if found.players
                                else t("atlas.players.show")
                            ],
                        ],
                    ],
                    a(href=f"{url}/edit", class_="btn btn-ghost")[
                        icon("pencil", "sm"), span[t("players.edit")]
                    ],
                    row_menu(page, url, found.name),
                ],
            ],
            div(class_="map-toolbar", role="toolbar", aria_label=t("atlas.tools"))[
                button(
                    type="button",
                    class_="btn btn-sm",
                    data_map_tool="ruler",
                    aria_pressed="false",
                    disabled=scale is None,
                    title=None if scale is not None else t("atlas.ruler.needs_scale"),
                )[icon("ruler", "sm"), span[t("atlas.ruler.button")]],
                button(
                    type="button", class_="btn btn-sm", data_map_tool="pin", aria_pressed="false"
                )[icon("map-pin", "sm"), span[t("atlas.pin.button")]],
                button(
                    type="button", class_="btn btn-sm", data_map_tool="label", aria_pressed="false"
                )[icon("pen-line", "sm"), span[t("atlas.label.button")]],
                button(
                    type="button", class_="btn btn-sm", data_map_tool="route", aria_pressed="false"
                )[icon("footprints", "sm"), span[t("atlas.route.button")]],
                button(type="button", class_="btn btn-sm btn-ghost", data_map_tool="frame")[
                    icon("map", "sm"), span[t("atlas.frame.button")]
                ]
                if around.framable
                else None,
                a(href="?tool=scale", class_="btn btn-sm btn-ghost", data_dialog="tool-scale")[
                    icon("scaling", "sm"), span[t("atlas.scale.button")]
                ],
                a(
                    href="?tool=picture",
                    class_="btn btn-sm btn-ghost",
                    data_dialog="tool-picture",
                    data_picture_open="",
                )[icon("image", "sm"), span[t("atlas.picture.button")]]
                if ready
                else None,
                a(href="?tool=grid", class_="btn btn-sm btn-ghost", data_dialog="tool-grid")[
                    icon("hexagon", "sm"), span[t("atlas.grid.button")]
                ]
                if scale is not None
                else None,
                span(class_="map-readout muted", aria_live="polite", data_map_readout="")[
                    t("atlas.ruler.needs_scale") if scale is None else None
                ],
            ],
            _route_bar(page),
            div(class_="map-body")[
                stage if ready else tiling_panel(page, found, job),
                _layer_panel(page, found, around.layers, around.grid, around.trail)
                if ready and around.layers
                else None,
            ],
            details(class_="map-notes")[summary[t("atlas.notes")], div(class_="md-body")[notes]]
            if notes is not None and notes.strip()
            else None,
            _scale_dialog(page, found),
            _grid_dialog(page, found) if scale is not None else None,
            _picture_dialog(page, found, around.layers, around.grid, recipients) if ready else None,
            _pin_dialog(page, found, around.candidates),
            _label_dialog(page, found),
            _route_dialog(page, found),
            _travel_dialog(page, found, travel_choices)
            if around.travel and travel_choices is not None
            else None,
            _frame_dialog(page, found, around.framable) if around.framable else None,
        ],
        world_id=world.id,
        main_class="shell-main map-main",
        extra_scripts=(*MAP_SCRIPTS, "/static/js/htmx.min.js"),
    )


def map_form_page(
    page: Page,
    found: WorldMap,
    world: World,
    adventure: Adventure | None,
    *,
    name: str,
    notes: str,
    error: str | None = None,
    notice_text: str | None = None,
) -> Element:
    """A map's name and notes, and another picture under it."""
    t = page.t
    url = map_url(found.id)
    return app_shell(
        page,
        t("adventures.edit_title", name=found.name),
        div(class_="formpage formpage-wide")[
            a(href=url, class_="backlink")[icon("arrow-left"), span[found.name]],
            notice(notice_text),
            alert(error),
            div(class_="panel")[
                div(class_="panel-head")[
                    span(class_="eyebrow")[
                        adventure.name if adventure is not None else t("atlas.world_map")
                    ],
                    b[t("adventures.edit_title", name=found.name)],
                ],
                form(method="post", action=f"{url}/edit", class_="stack-form")[
                    text_field("name", t("adventures.name"), value=name, autocomplete="off"),
                    div(class_="field")[
                        label(for_="f-notes")[t("atlas.notes")],
                        textarea(id="f-notes", name="notes", rows="8", class_="prose-editor")[
                            notes
                        ],
                    ],
                    div(class_="form-actions")[
                        button(type="submit", class_="btn btn-primary")[
                            icon("check"), span[t("worlds.save")]
                        ],
                        a(href=url, class_="btn btn-ghost")[t("common.cancel")],
                    ],
                ],
            ],
            div(class_="panel")[
                div(class_="panel-head")[b[t("atlas.replace.title")]],
                p(class_="field-hint")[t("atlas.replace.hint")],
                form(
                    method="post",
                    action=f"{url}/image",
                    enctype="multipart/form-data",
                    class_="stack-form",
                )[
                    label(for_="f-replace", class_="tok-drop img-drop")[
                        icon("upload"),
                        span[t("atlas.picture_drop", mb=MAX_BYTES // (1024 * 1024))],
                        input_el(
                            id="f-replace",
                            type="file",
                            name="picture",
                            accept=".jpg,.jpeg,.png,.webp",
                            required=True,
                        ),
                    ],
                    div(class_="form-actions")[
                        button(type="submit", class_="btn")[
                            icon("upload", "sm"), span[t("atlas.replace.submit")]
                        ]
                    ],
                ],
            ],
            div(class_="form-actions")[
                delete_link(page, f"{url}/delete", t("atlas.delete"), text=True)
            ],
        ],
        world_id=world.id,
    )


def map_delete_page(page: Page, found: WorldMap) -> Element:
    """Ask before a map goes to the trash."""
    t = page.t
    url = map_url(found.id)
    return confirm_page(
        page,
        title=t("atlas.delete"),
        heading=t("chapters.delete_heading", name=found.name),
        lede=t("atlas.delete_lede"),
        action=f"{url}/delete",
        back=url,
        submit=t("atlas.delete"),
    )


__all__ = [
    "RouteView",
    "LabelView",
    "LayerView",
    "Candidate",
    "FrameView",
    "MapContext",
    "PinView",
    "MAPS_URL",
    "MAP_SCRIPTS",
    "adventure_maps_section",
    "distance_text",
    "map_delete_page",
    "map_form_page",
    "map_page",
    "map_rows",
    "map_url",
    "maps_page",
    "new_map_dialog",
    "scale_badge",
    "tiling_panel",
    "world_maps_url",
]
