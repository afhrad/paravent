"""A map drawn as one picture, with what lies on it (documentation/MAPS.md §9b): the
part of the original asked for, scaled to a size a phone shows, its roads, names, pins
and hex grid drawn on in the picture's own proportions, for a handout or to download.

Drawn by Pillow from the original, never from the tiles, so it is as sharp as the
original allows; nothing here depends on the viewer.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.models.gamestate import MapLabel, MapRoute
from app.services.map_tiles import _allow

# The longest side of the picture made.
MAX_SIDE = 2400
# A pin's disc and its name, a road's width, in pixels of a picture 1200 across; a
# larger picture draws them larger, so they read the same on a screen.
PIN_RADIUS = 9
PIN_TEXT = 18
UNIT_SIDE = 1200
# A pin's colour by kind, and a road's by its kind (the viewer's own, MAPS.md §6, §8).
PIN_COLOURS = {
    "place": (69, 96, 220),
    "npc": (150, 70, 170),
    "encounter": (190, 50, 50),
    "treasure": (200, 150, 30),
    "adventure": (40, 130, 110),
    "chapter": (40, 130, 110),
    "map": (60, 60, 60),
    "character": (30, 120, 60),
    "quest": (200, 110, 30),
    "faction": (110, 60, 40),
    "handout": (90, 90, 150),
    "session": (120, 110, 90),
    "reference": (80, 80, 80),
    "note": (120, 110, 90),
}
ROUTE_COLOURS = {
    "road": (120, 72, 30),
    "trail": (160, 120, 70),
    "river": (40, 100, 190),
    "sea": (30, 130, 150),
    "": (90, 90, 90),
}
LABEL_COLOURS = {"water": (30, 80, 150)}
INK = (30, 26, 22)
PAPER = (255, 253, 248)


# From the original's pixels to the picture's.
Placer = Callable[[float, float], tuple[float, float]]


@dataclass(frozen=True)
class Mark:
    """A pin as the picture draws it: where, its name, its kind."""

    x: float
    y: float
    text: str
    kind: str


@dataclass(frozen=True)
class Hexes:
    """The hex grid as the picture draws it: across in the original's pixels."""

    across: float
    orientation: str
    x: float
    y: float


@lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=max(6, size))


def area_of(width: int, height: int, box: Sequence[float] | None) -> tuple[int, int, int, int]:
    """The part of the original to draw, ``(left, top, right, bottom)``: ``box`` (``x, y,
    width, height``) kept inside the picture, or the whole of it."""
    if box is None or len(box) != 4 or not all(math.isfinite(v) for v in box):
        return 0, 0, width, height
    x, y, w, h = box
    left, top = max(0, int(x)), max(0, int(y))
    right, bottom = min(width, int(math.ceil(x + w))), min(height, int(math.ceil(y + h)))
    if right - left < 16 or bottom - top < 16:
        return 0, 0, width, height
    return left, top, right, bottom


def render(
    source: Path,
    max_pixels: int,
    *,
    area: tuple[int, int, int, int],
    marks: Sequence[Mark] = (),
    labels: Sequence[MapLabel] = (),
    routes: Sequence[MapRoute] = (),
    hexes: Hexes | None = None,
    max_side: int = MAX_SIDE,
) -> bytes:
    """The part ``area`` of the original, at most ``max_side`` on its long side, with the
    roads, the hex grid, the names and the pins drawn on, as a PNG's bytes."""
    _allow(max_pixels)
    left, top, right, bottom = area
    with Image.open(source) as opened:
        picture = opened.crop(area).convert("RGB")
    w, h = right - left, bottom - top
    k = min(1.0, max_side / max(w, h))
    if k < 1:
        picture = picture.resize(
            (max(1, round(w * k)), max(1, round(h * k))), Image.Resampling.LANCZOS
        )

    def at(x: float, y: float) -> tuple[float, float]:
        return (x - left) * k, (y - top) * k

    draw = ImageDraw.Draw(picture)
    unit = max(1.0, max(picture.size) / UNIT_SIDE)
    if hexes is not None and hexes.across * k >= 10:
        _grid(draw, hexes, area, k, round(unit))
    for route in routes:
        _route(draw, route, at, unit)
    for label in labels:
        _label(picture, label, at, k)
    for mark in marks:
        _mark(draw, mark, at, picture.size, unit)
    out = io.BytesIO()
    picture.save(out, "PNG", optimize=True)
    return out.getvalue()


def _route(draw: ImageDraw.ImageDraw, route: MapRoute, at: Placer, unit: float) -> None:
    points = [at(x, y) for x, y in route.points]
    if len(points) < 2:
        return
    colour = ROUTE_COLOURS.get(route.kind, ROUTE_COLOURS[""])
    for index, stretch in enumerate(route.stretches):
        end = (
            route.stretches[index + 1].start
            if index + 1 < len(route.stretches)
            else len(points) - 1
        )
        part = points[stretch.start : end + 1]
        if len(part) < 2:
            continue
        width = round((6 if stretch.difficult else 4) * unit)
        draw.line(part, fill=PAPER, width=width + round(3 * unit), joint="curve")
        draw.line(part, fill=colour, width=width, joint="curve")


def _label(picture: Image.Image, label: MapLabel, at: Placer, k: float) -> None:
    size = round(label.size * k)
    if size < 6:
        return
    text = label.text
    if label.style == "region":
        text = " ".join(text.upper())
    font = _font(size)
    stroke = max(1, size // 8)
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    box = probe.textbbox((0, 0), text, font=font, stroke_width=stroke)
    tw, th = int(box[2] - box[0]) + 2, int(box[3] - box[1]) + 2
    stamp = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    ImageDraw.Draw(stamp).text(
        (-box[0] + 1, -box[1] + 1),
        text,
        font=font,
        fill=(*LABEL_COLOURS.get(label.style, INK), 255),
        stroke_width=stroke,
        stroke_fill=(*PAPER, 220),
    )
    if label.angle:
        # The viewer turns clockwise, Pillow the other way round.
        stamp = stamp.rotate(-label.angle, expand=True, resample=Image.Resampling.BICUBIC)
    x, y = at(label.x, label.y)
    picture.paste(stamp, (round(x - stamp.width / 2), round(y - stamp.height / 2)), stamp)


def _mark(
    draw: ImageDraw.ImageDraw, mark: Mark, at: Placer, size: tuple[int, int], unit: float
) -> None:
    x, y = at(mark.x, mark.y)
    r = PIN_RADIUS * unit
    if not (-r <= x <= size[0] + r and -r <= y <= size[1] + r):
        return
    edge = round(3 * unit)
    draw.ellipse(
        (x - r, y - r, x + r, y + r),
        fill=PIN_COLOURS.get(mark.kind, INK),
        outline=PAPER,
        width=edge,
    )
    if mark.text:
        draw.text(
            (x + r + 4 * unit, y),
            mark.text,
            font=_font(round(PIN_TEXT * unit)),
            fill=INK,
            anchor="lm",
            stroke_width=edge,
            stroke_fill=PAPER,
        )


def _grid(
    draw: ImageDraw.ImageDraw,
    hexes: Hexes,
    area: tuple[int, int, int, int],
    k: float,
    width: int = 1,
) -> None:
    """The hexes over ``area``, as the viewer draws them (static/js/map.js drawGrid)."""
    left, top, right, bottom = area
    d = hexes.across
    r = d / math.sqrt(3)
    flat = hexes.orientation != "pointy"
    step_a, step_b = (1.5 * r, d) if flat else (d, 1.5 * r)
    a_lo, a_hi = (left, right) if flat else (top, bottom)
    b_lo, b_hi = (top, bottom) if flat else (left, right)
    oa, ob = (hexes.x, hexes.y) if flat else (hexes.y, hexes.x)
    turn = 0.0 if flat else math.pi / 6
    for a in range(math.floor((a_lo - oa) / step_a) - 1, math.ceil((a_hi - oa) / step_a) + 2):
        shift = step_b / 2 if a % 2 else 0.0
        first = math.floor((b_lo - ob - shift) / step_b) - 1
        last = math.ceil((b_hi - ob - shift) / step_b) + 1
        for b in range(first, last + 1):
            ca, cb = oa + a * step_a, ob + b * step_b + shift
            cx, cy = (ca, cb) if flat else (cb, ca)
            corners = [
                (
                    (cx + r * math.cos(math.pi / 3 * i + turn) - left) * k,
                    (cy + r * math.sin(math.pi / 3 * i + turn) - top) * k,
                )
                for i in range(7)
            ]
            draw.line(corners, fill=(20, 20, 20), width=width)
