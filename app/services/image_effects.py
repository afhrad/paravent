"""Image effects: a world's pictures as the books set them (documentation/ADVENTURES.md §5.5).

An image is named with the effects it wears, in the same notation everywhere: an
Obsidian embed's part made only of effect words (``![[tavern.jpg|torn sepia|400]]``),
Markdown's alt text (``![The tavern|torn](media/tavern.jpg)``), or a cover in front
matter (``cover: media/tavern.jpg|fade``). The original is never changed: the effects
are rendered from it once, kept in the cache as WebP with transparency, and served in
its place. Transparency is what lets an edge melt into whatever lies beneath, so one
rendering fits every theme and both colour modes.

The crop runs first, then the colour, then the edges:

* ``banner`` — cut to a title image's wide shape, centred; a cover with effects wears it
  by itself, so its edges are the ones the page shows.

* ``sepia`` — brown ink on old paper; ``aged`` — a faded, grainy print with darker
  corners; ``mono`` — black and white.
* ``torn`` — ripped edges, browned along the tear; ``fade`` — edges that dissolve into
  the page; ``oval`` — a portrait's oval, softly cut.

A torn edge is the same each time for the same picture: its tear is seeded by the file.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

# Bump when the rendering changes, so cached images are made again.
VERSION = 5
EFFECTS = ("banner", "sepia", "aged", "mono", "torn", "fade", "oval")
# A title image's shape: wide, as the pages show a cover.
BANNER = 10 / 3
ALIASES = {"ripped": "torn", "soft": "fade", "vignette": "fade", "old": "aged", "bw": "mono"}
# The longest side a rendering keeps; a page never shows more.
MAX_SIDE = 1600
_WORDS = re.compile(r"[\s,+]+")


def parse(text: str) -> tuple[str, ...] | None:
    """The effects ``text`` names, in rendering order, or None when it is not made only of
    effect words (then it is alt text, a size, or nothing)."""
    words = [w for w in _WORDS.split(text.strip().lower()) if w]
    if not words:
        return None
    named = {ALIASES.get(w, w) for w in words}
    if not named <= set(EFFECTS):
        return None
    return tuple(e for e in EFFECTS if e in named)


def split(spec: str | None) -> tuple[str, tuple[str, ...]]:
    """``media/tavern.jpg|torn fade`` as ``("media/tavern.jpg", ("torn", "fade"))``; a
    spec without effects is its path alone."""
    if not spec:
        return "", ()
    path, _, rest = spec.partition("|")
    effects = parse(rest) if rest else None
    return path.strip(), effects or ()


def join(path: str, effects: tuple[str, ...] | list[str]) -> str:
    """The spec for ``path`` wearing ``effects``."""
    named = parse(" ".join(effects)) if effects else None
    return f"{path}|{' '.join(named)}" if named else path


def query(effects: tuple[str, ...]) -> str:
    """What an image's URL carries to be served with ``effects`` (``fx=torn,fade``)."""
    return f"fx={','.join(effects)}" if effects else ""


def with_effects(url: str, effects: tuple[str, ...]) -> str:
    """``url`` asking for ``effects``."""
    if not effects:
        return url
    return f"{url}{'&' if '?' in url else '?'}{query(effects)}"


# -- rendering ----------------------------------------------------------------------


def _sepia(image: Image.Image, strength: float) -> Image.Image:
    rgb = image.convert("RGB")
    toned = ImageOps.colorize(
        ImageOps.grayscale(rgb), black="#2a1a0c", mid="#9a7650", white="#f6ead0"
    )
    return Image.blend(rgb, toned, strength)


def _aged(image: Image.Image, rng: random.Random) -> Image.Image:
    toned = _sepia(image, 0.6)
    toned = ImageEnhance.Contrast(toned).enhance(0.86)
    grain = Image.effect_noise(toned.size, 28).convert("RGB")
    toned = Image.blend(toned, ImageChops.multiply(toned, grain), 0.12)
    # Darker toward the corners, as old prints yellow and darken at their edges.
    width, height = toned.size
    shade = Image.new("L", (width, height), 0)
    inset = int(min(width, height) * 0.08)
    ImageDraw.Draw(shade).ellipse((-inset, -inset, width + inset, height + inset), fill=255)
    shade = shade.filter(ImageFilter.GaussianBlur(min(width, height) * 0.12))
    dark = ImageEnhance.Brightness(toned).enhance(0.72)
    return Image.composite(toned, dark, shade)


def _mono(image: Image.Image) -> Image.Image:
    return ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1).convert("RGB")


def _edge_points(length: int, depth: float, step: float, rng: random.Random) -> list[float]:
    """A tear's offsets along one edge: a random walk between a tenth of ``depth`` and
    ``depth``, a point every ``step`` pixels."""
    count = max(2, int(length / step))
    offset = rng.uniform(0.3, 0.7) * depth
    out = []
    for _ in range(count + 1):
        offset = min(depth, max(depth * 0.1, offset + rng.uniform(-0.45, 0.45) * depth))
        out.append(offset)
    return out


def _torn_mask(size: tuple[int, int], rng: random.Random) -> Image.Image:
    """Opaque inside a ragged outline: each edge a random walk, each corner torn off by an
    amount of its own, so no corner stays square."""
    width, height = size
    side = min(width, height)
    depth = max(5.0, side * 0.03)
    step = max(4.0, side / 45)
    tl, tr, br, bl = (rng.uniform(0.6, 1.0) * depth for _ in range(4))

    def edge(length: int, start: float, end: float) -> list[tuple[float, float]]:
        """(position along the edge, offset into the picture), corner to corner."""
        offsets = _edge_points(length, depth, step, rng)
        offsets[0], offsets[-1] = start, end
        n = len(offsets) - 1
        span = length - start - end
        return [(start + span * i / n, o) for i, o in enumerate(offsets)]

    points: list[tuple[float, float]] = []
    points += [(a, o) for a, o in edge(width, tl, tr)]
    points += [(width - o, a) for a, o in edge(height, tr, br)]
    points += [(width - a, height - o) for a, o in edge(width, br, bl)]
    points += [(o, height - a) for a, o in edge(height, bl, tl)]
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(0.7))


def _ramp(length: int, band: float) -> list[int]:
    """Opacity along one side: 0 at both ends, rising over ``band`` pixels on a gentle
    curve (smootherstep) to full, so an edge vanishes completely and without a seam."""
    out = []
    for i in range(length):
        t = min(1.0, min(i + 0.5, length - i - 0.5) / band)
        out.append(round(255 * t * t * t * (t * (6 * t - 15) + 10)))
    return out


def _fade_mask(size: tuple[int, int]) -> Image.Image:
    """Edges that dissolve to nothing: a ramp in from each side — a sixth of the width
    from left and right, a quarter of the height from top and bottom — the two
    directions multiplied."""
    width, height = size
    across = Image.new("L", (width, 1))
    across.putdata(_ramp(width, max(2.0, width / 6)))
    down = Image.new("L", (1, height))
    down.putdata(_ramp(height, max(2.0, height / 4)))
    return ImageChops.multiply(across.resize(size), down.resize(size))


def _oval_mask(size: tuple[int, int]) -> Image.Image:
    width, height = size
    soft = max(2, int(min(width, height) * 0.025))
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).ellipse((soft, soft, width - soft, height - soft), fill=255)
    return mask.filter(ImageFilter.GaussianBlur(soft * 0.8))


def _trimmed(image: Image.Image) -> Image.Image:
    """``image`` cut to what shows: a picture with a transparent margin of its own (the
    books' framed art) loses it, so a fade or tear starts at the visible edge."""
    if "A" not in image.getbands():
        return image
    shown = image.getchannel("A").point(lambda v: 255 if v > 16 else 0).getbbox()
    if shown and shown != (0, 0, image.width, image.height):
        return image.crop(shown)
    return image


def apply(image: Image.Image, effects: tuple[str, ...], seed: str) -> Image.Image:
    """``image`` wearing ``effects``, with transparency where an edge falls away."""
    rng = random.Random(seed)
    image = ImageOps.exif_transpose(image)
    image = _trimmed(image)
    if max(image.size) > MAX_SIDE:
        scale = MAX_SIDE / max(image.size)
        image = image.resize(
            (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    if "banner" in effects and image.width / image.height < BANNER:
        height = max(1, round(image.width / BANNER))
        top = (image.height - height) // 2
        image = _trimmed(image.crop((0, top, image.width, top + height)))
    alpha = image.getchannel("A") if "A" in image.getbands() else None
    rgb = image.convert("RGB")
    if "mono" in effects:
        rgb = _mono(rgb)
    if "sepia" in effects:
        rgb = _sepia(rgb, 0.85)
    if "aged" in effects:
        rgb = _aged(rgb, rng)
    mask = alpha or Image.new("L", rgb.size, 255)
    if "torn" in effects:
        torn = _torn_mask(rgb.size, rng)
        # Browned along the tear: a thin band inside the edge, darkened.
        inner = torn.filter(ImageFilter.MinFilter(max(3, 2 * math.ceil(min(rgb.size) / 300) + 1)))
        band = ImageChops.subtract(torn, inner).filter(ImageFilter.GaussianBlur(1.2))
        brown = Image.new("RGB", rgb.size, (110, 74, 34))
        rgb = Image.composite(brown, rgb, band.point(lambda v: int(v * 0.55)))
        mask = ImageChops.multiply(mask, torn)
    if "fade" in effects:
        mask = ImageChops.multiply(mask, _fade_mask(rgb.size))
    if "oval" in effects:
        mask = ImageChops.multiply(mask, _oval_mask(rgb.size))
    out = rgb.convert("RGBA")
    out.putalpha(mask)
    return out


class ImageEffects:
    """Renders effects into a cache folder, once per picture, its version and effects."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir

    def _key(self, source: Path, effects: tuple[str, ...]) -> str:
        stat = source.stat()
        text = f"{source.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{','.join(effects)}|{VERSION}"
        return hashlib.sha256(text.encode()).hexdigest()[:32]

    def render(self, source: Path, effects: tuple[str, ...]) -> Path:
        """The rendering of ``source`` with ``effects``, made now if it is not cached."""
        key = self._key(source, effects)
        target = self.cache_dir / key[:2] / f"{key}.webp"
        if target.is_file():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        seed = hashlib.sha256(source.read_bytes()).hexdigest()
        with Image.open(source) as image:
            image.load()
            rendered = apply(image, effects, seed)
        tmp = target.with_name(f".{target.name}.tmp")
        rendered.save(tmp, "WEBP", quality=82, method=4)
        tmp.replace(target)
        return target


__all__ = [
    "ALIASES",
    "EFFECTS",
    "ImageEffects",
    "apply",
    "join",
    "parse",
    "query",
    "split",
    "with_effects",
]
