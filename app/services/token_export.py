"""An encounter's tokens to take to the table (documentation/TOKENS.md §4): one piece per
creature, numbered as the tracker numbers them, sized by the creature's size — printed on
paper, or as PNG files for a virtual tabletop.

Each piece is drawn here: the token's own file (the table's, or 5eTools's), else the
kind's empty frame with the name's first letter in its opening, scaled to the squares the
creature takes, its number on a badge at its edge.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from app.services.tokens import FRAME_COLOURS, FRAME_DIR, FRAME_INNER, Token

# The squares a creature of each size takes on a grid: Tiny and Small share one.
SQUARES = {"T": 1, "S": 1, "M": 1, "L": 2, "H": 3, "G": 4}
# Pixels per square a download offers; a virtual tabletop's grid is usually one of them.
PIXELS = (140, 200, 256, 280)
DEFAULT_PIXELS = 256
# Millimetres per square a print offers (25 is the one-inch grid of a battle map).
SQUARE_MM = (20, 25, 30)
DEFAULT_SQUARE_MM = 25
# What a printed piece is drawn at: 300 dots per inch of a 30 mm square, enough for any.
PRINT_PIXELS = 360
MAX_PIXELS = 400


@dataclass(frozen=True)
class Piece:
    """One token to take to the table: who, its token, the squares it takes, and the
    number on its badge (``Goblin 2``), if any."""

    label: str
    token: Token
    squares: int = 1
    number: str | None = None

    @property
    def filename(self) -> str:
        """``Goblin 2.png``: the label and number, without what a file name cannot hold."""
        name = " ".join(part for part in (self.label, self.number) if part)
        return (re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", name).strip() or "token") + ".png"


@dataclass(frozen=True)
class PlannedGroup:
    """Some number of one creature, as the builder plans it."""

    label: str
    token: Token
    count: int
    sizes: Sequence[str] = ()
    # The members' own names when the plan lists them ("Gruk, Ugrash").
    names: Sequence[str] = ()


def squares_of(sizes: Sequence[str]) -> int:
    """The squares a creature takes; the larger of "Medium or Small"; one when unknown."""
    return max((SQUARES.get(code.upper(), 1) for code in sizes), default=1)


def pieces(groups: Sequence[PlannedGroup], party: Sequence[tuple[str, Token]] = ()) -> list[Piece]:
    """Every piece of a fight: the party first (one square each), then each group's
    members, numbered 1… where a group has several and no names of their own."""
    out = [Piece(name, token) for name, token in party]
    for group in groups:
        squares = squares_of(group.sizes)
        for n in range(group.count):
            if n < len(group.names):
                out.append(Piece(group.names[n], group.token, squares))
            elif group.count > 1:
                out.append(Piece(group.label, group.token, squares, str(n + 1)))
            else:
                out.append(Piece(group.label, group.token, squares))
    return out


def _rgb(colour: str) -> tuple[int, int, int]:
    return (int(colour[1:3], 16), int(colour[3:5], 16), int(colour[5:7], 16))


def _mix(colour: str, other: tuple[int, int, int], share: float) -> tuple[int, int, int]:
    r, g, b = _rgb(colour)
    return (
        round(r + (other[0] - r) * share),
        round(g + (other[1] - g) * share),
        round(b + (other[2] - b) * share),
    )


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=max(8, size))


def _empty(kind: str, label: str, size: int) -> Image.Image:
    """The kind's empty frame, its opening filled in the kind's colour, the name's first
    letter in it."""
    colour = FRAME_COLOURS.get(kind, FRAME_COLOURS["entry"])
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    inner = size / 2 * FRAME_INNER
    centre = size / 2
    draw.ellipse(
        (centre - inner, centre - inner, centre + inner, centre + inner),
        fill=_mix(colour, (255, 255, 255), 0.8) + (255,),
    )
    letter = next((c for c in label if c.isalnum()), "?").upper()
    draw.text(
        (centre, centre),
        letter,
        fill=_mix(colour, (0, 0, 0), 0.35) + (255,),
        font=_font(round(inner)),
        anchor="mm",
    )
    frame_file = FRAME_DIR / f"frame-{kind if kind in FRAME_COLOURS else 'entry'}.png"
    if frame_file.is_file():
        frame = (
            Image.open(frame_file).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        )
        canvas.alpha_composite(frame)
    return canvas


def _badge(canvas: Image.Image, number: str) -> None:
    """The number on a round badge at the lower right, as the tracker shows it."""
    size = canvas.width
    radius = size * 0.16
    cx, cy = size - radius - size * 0.02, size - radius - size * 0.02
    draw = ImageDraw.Draw(canvas)
    edge = max(1, round(size * 0.012))
    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        fill=(255, 255, 255, 255),
        outline=(30, 30, 30, 255),
        width=edge,
    )
    text = round(radius * (1.15 if len(number) == 1 else 0.85))
    draw.text((cx, cy), number, fill=(20, 20, 20, 255), font=_font(text), anchor="mm")


def render(piece: Piece, path: Path | None, pixels: int) -> bytes:
    """One piece as a PNG, ``pixels`` per square: its token's file where there is one
    (else the kind's empty frame), its badge drawn on."""
    size = max(16, min(MAX_PIXELS, pixels)) * piece.squares
    canvas: Image.Image | None = None
    if path is not None:
        try:
            with Image.open(path) as found:
                canvas = found.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        except (OSError, UnidentifiedImageError):
            canvas = None
    if canvas is None:
        canvas = _empty(piece.token.kind, piece.label, size)
    if piece.number:
        _badge(canvas, piece.number)
    out = io.BytesIO()
    canvas.save(out, "PNG", optimize=True)
    return out.getvalue()


def archive(
    all_pieces: Sequence[Piece], path_of: Callable[[Token], Path | None], pixels: int
) -> bytes:
    """Every piece as a PNG in one ZIP, each file named for its piece (``Goblin 2.png``),
    a repeated name numbered on."""
    out = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as bundle:
        for piece in all_pieces:
            name = piece.filename
            stem, n = name.removesuffix(".png"), 2
            while name.lower() in used:
                name, n = f"{stem} ({n}).png", n + 1
            used.add(name.lower())
            bundle.writestr(name, render(piece, path_of(piece.token), pixels))
    return out.getvalue()


__all__ = [
    "DEFAULT_PIXELS",
    "DEFAULT_SQUARE_MM",
    "PIXELS",
    "PRINT_PIXELS",
    "SQUARE_MM",
    "Piece",
    "PlannedGroup",
    "archive",
    "pieces",
    "render",
    "squares_of",
]
