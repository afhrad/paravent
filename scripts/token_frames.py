"""Draw the empty token frames (documentation/TOKENS.md §1): one ring per kind, its
centre see-through, where a picture or the kind's icon goes.

    python scripts/token_frames.py

Writes ``static/tokens/frame-<kind>.png`` (512 px). The ring's inner edge is
``app.services.tokens.FRAME_INNER`` of the radius; a picture is fitted to it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.tokens import FRAME_COLOURS, FRAME_INNER, SIZE  # noqa: E402

SCALE = 4  # drawn four times larger, then scaled down for smooth edges
OUT = ROOT / "static" / "tokens"


def _mix(colour: str, other: tuple[int, int, int], share: float) -> tuple[int, int, int]:
    r, g, b = (int(colour[i : i + 2], 16) for i in (1, 3, 5))
    return tuple(round(c + (o - c) * share) for c, o in zip((r, g, b), other, strict=True))  # type: ignore[return-value]


def _disc(size: int, radius: float) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    centre = size / 2
    ImageDraw.Draw(mask).ellipse(
        (centre - radius, centre - radius, centre + radius, centre + radius), fill=255
    )
    return mask


def frame(colour: str) -> Image.Image:
    """One frame: a bevelled ring in ``colour``, light falling from the top left."""
    big = SIZE * SCALE
    outer = big / 2 - 6 * SCALE  # room for the shadow
    inner = big / 2 * FRAME_INNER
    light, dark = _mix(colour, (255, 255, 255), 0.45), _mix(colour, (0, 0, 0), 0.45)
    shade = Image.linear_gradient("L").rotate(-45, expand=False).resize((big, big))
    ring_fill = ImageOps.colorize(shade, light, dark).convert("RGBA")
    ring = ImageChops.subtract(_disc(big, outer), _disc(big, inner))

    out = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    # A soft shadow under the ring.
    shadow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    shadow.putalpha(_disc(big, outer).point(lambda v: v * 90 // 255))
    shadow = shadow.filter(ImageFilter.GaussianBlur(5 * SCALE))
    moved = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    moved.alpha_composite(shadow, (0, 3 * SCALE))
    # Only outside the opening: the centre stays clear for the picture.
    moved.putalpha(ImageChops.subtract(moved.getchannel("A"), _disc(big, inner)))
    out.alpha_composite(moved)
    ring_fill.putalpha(ring)
    out.alpha_composite(ring_fill)
    # Dark edges outside and inside, a bright line just inside the outer edge.
    draw = ImageDraw.Draw(out)
    c = big / 2
    edge = _mix(colour, (0, 0, 0), 0.65) + (255,)
    shine = _mix(colour, (255, 255, 255), 0.75) + (200,)
    for radius, colour_, width in (
        (outer, edge, 3),
        (inner, edge, 3),
        (outer - 7 * SCALE, shine, 2),
        (inner + 5 * SCALE, edge[:3] + (120,), 2),
    ):
        draw.ellipse(
            (c - radius, c - radius, c + radius, c + radius), outline=colour_, width=width * SCALE
        )
    return out.resize((SIZE, SIZE), Image.Resampling.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for kind, colour in FRAME_COLOURS.items():
        frame(colour).save(OUT / f"frame-{kind}.png", optimize=True)
        print(f"frame-{kind}.png")


if __name__ == "__main__":
    main()
