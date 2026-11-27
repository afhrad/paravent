"""The gamemaster's maps as pages (documentation/MAPS.md): a world's maps, an adventure's
maps on its page, one map in its viewer with the ruler and the scale, its form, and the
question before it is deleted."""

from __future__ import annotations

import json
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

from app.models.gamestate import Adventure, MapView, World, WorldMap
from app.services.map_tiles import MAX_BYTES, TileJob, max_zoom, needs_tiles
from app.services.maps import LONG_UNITS, UNITS
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
# The scripts of a map's page: Leaflet first, then Paravent's part.
MAP_SCRIPTS = ("/static/js/leaflet/leaflet.js", "/static/js/map.js")


def map_url(map_id: str) -> str:
    """One map's page."""
    return f"{MAPS_URL}/{map_id}"


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


def _thumb(found: WorldMap) -> Markup:
    return Markup(
        f'<img class="title-thumb map-thumb" src="{map_url(found.id)}/thumb" alt="" '
        'loading="lazy" decoding="async">'
    )


def map_rows(page: Page, maps: Sequence[WorldMap]) -> Element:
    """Maps as list rows: a thumbnail, the name, the scale."""
    return ul(class_="list")[
        [
            li(class_="list-row")[
                _thumb(found),
                div(class_="list-main")[
                    a(href=map_url(found.id), class_="list-title list-link")[found.name],
                    span(class_="list-meta")[scale_badge(page, found)],
                ],
                row_menu(page, map_url(found.id), found.name),
            ]
            for found in maps
        ]
    ]


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
            "kinds": {k: t(f"atlas.pin.kind.{k}") for k in (*_PIN_ORDER, "note")},
            "label_place": t("atlas.label.tap"),
            "layer": t("atlas.layer.one"),
            "text": t("atlas.label.text"),
            "style": t("atlas.label.style"),
            "styles": {k: t(f"atlas.label.style.{k}") for k in LABEL_STYLES},
            "size": t("atlas.label.size"),
            "angle": t("atlas.label.angle"),
            "remove_label": t("atlas.label.remove"),
            "locked": t("atlas.layer.locked_note"),
        },
        ensure_ascii=False,
    )


def _path(page: Page, path: Sequence[WorldMap]) -> Node:
    """The maps this one lies in, the widest first, each a link."""
    if not path:
        return None
    parts: list[Node] = []
    for found in path:
        parts += [a(href=map_url(found.id))[found.name], " › "]
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


def tiling_panel(page: Page, found: WorldMap, job: TileJob | None) -> Element:
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
        hx_get=f"{map_url(found.id)}/status",
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
    framable: Sequence[WorldMap] = ()
    disagree: tuple[float, float] | None = None
    focus: str = ""
    layers: Sequence[LayerView] = ()
    labels: Sequence[LabelView] = ()


# The kinds in the pin dialog's list, in its order.
_PIN_ORDER = ("place", "npc", "encounter", "treasure", "adventure", "chapter", "map")


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


def _layer_panel(page: Page, found: WorldMap, layers: Sequence[LayerView]) -> Element:
    """The map's layers, the top one first: show or hide each (for this user), choose the
    one the tools add to, lock it, and under its ⋯ rename it, say whether a newcomer
    sees it, show it only from the current zoom in, move it, delete it."""
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
        ul(class_="map-layer-list")[rows],
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
    notice_text: str | None = None,
    error: str | None = None,
) -> Element:
    """One map in its viewer: zoom and drag, the ruler, the scale, its pins and the maps
    lying on it."""
    around = around or MapContext()
    t = page.t
    url = map_url(found.id)
    tiled = needs_tiles(found.width, found.height)
    scale = found.scale
    stage = div(
        id="map-stage",
        class_="map-stage",
        data_map=found.id,
        data_width=str(found.width),
        data_height=str(found.height),
        data_zoom=str(max_zoom(found.width, found.height)),
        data_tiles=f"{url}/tiles" if tiled else None,
        data_digest=found.digest[:16],
        data_image=None if tiled else media_url(found.scope.record_id, found.image),
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
        data_focus=around.focus or None,
        role="application",
        aria_label=t("atlas.viewer", name=found.name),
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
                button(type="button", class_="btn btn-sm btn-ghost", data_map_tool="frame")[
                    icon("map", "sm"), span[t("atlas.frame.button")]
                ]
                if around.framable
                else None,
                a(href="?tool=scale", class_="btn btn-sm btn-ghost", data_dialog="tool-scale")[
                    icon("scaling", "sm"), span[t("atlas.scale.button")]
                ],
                span(class_="map-readout muted", aria_live="polite", data_map_readout="")[
                    t("atlas.ruler.needs_scale") if scale is None else None
                ],
            ],
            div(class_="map-body")[
                stage if ready else tiling_panel(page, found, job),
                _layer_panel(page, found, around.layers) if ready and around.layers else None,
            ],
            details(class_="map-notes")[summary[t("atlas.notes")], div(class_="md-body")[notes]]
            if notes is not None and notes.strip()
            else None,
            _scale_dialog(page, found),
            _pin_dialog(page, found, around.candidates),
            _label_dialog(page, found),
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
