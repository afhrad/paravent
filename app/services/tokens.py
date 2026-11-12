"""Round tokens for every element (documentation/TOKENS.md).

A token is found in this order: the table's own upload (an NPC, a player character, a
creature of the builder, a place, an encounter, a treasure), a 5eTools token from the
image folder (creatures, vehicles, objects), else the kind's default disc, which the page
draws itself. An upload is cropped round here and kept in the element's world folder.
"""

from __future__ import annotations

import io
import json
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageChops, ImageDraw, ImageOps, UnidentifiedImageError

from app.services.media import MediaError, checked_image, write_image
from app.store.prose import MEDIA_DIR, ProseStore

FOLDER = "tokens"
SIZE = 512
# The empty frames (``static/tokens/frame-<kind>.png``, ``scripts/token_frames.py``): a
# ring whose inner edge is this share of the radius; a picture fills what is inside.
FRAME_INNER = 0.82
FRAME_DIR = Path(__file__).resolve().parent.parent.parent / "static" / "tokens"
FRAME_URL = "/static/tokens"
# Each kind's ring colour (the default theme's: dmg, accent, heal, magic, cond).
FRAME_COLOURS = {
    "creature": "#D93A3F",
    "encounter": "#B32227",
    "npc": "#4F6BED",
    "character": "#1F8A55",
    "item": "#8144B8",
    "spell": "#6A2F9C",
    "place": "#B57500",
    "treasure": "#C99A2E",
    "vehicle": "#3247B8",
    "object": "#6B7280",
    "entry": "#6B7280",
}
OWN_KINDS = frozenset({"npc", "character", "creature", "place", "encounter", "treasure"})
# Kinds whose own picture is their token's too: one picture for both.
PICTURE_KINDS = frozenset({"npc", "place", "creature"})
# Where 5eTools keeps the tokens of a category, under its image folder.
CORPUS_DIRS = {"monster": "bestiary", "vehicle": "vehicles", "object": "objects"}
# The kind a reference category's token is drawn as.
CATEGORY_KINDS = {
    "monster": "creature",
    "item": "item",
    "baseitem": "item",
    "magicvariant": "item",
    "itemGroup": "item",
    "spell": "spell",
    "vehicle": "vehicle",
    "vehicleUpgrade": "vehicle",
    "object": "object",
}
TOKEN_URL = "/tokens"


@dataclass(frozen=True)
class Token:
    """What a page draws: the kind (for the default disc and its colours) and, where
    there is one, the picture's address."""

    kind: str
    src: str | None = None


def token_name(name: str) -> str:
    """A name as 5eTools writes its token file: ASCII, without double quotes."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return ascii_name.replace('"', "")


@dataclass(frozen=True)
class Placement:
    """Where a picture sits in the frame, as the upload dialog set it: ``x`` and ``y`` the
    point of the picture (0–1 of its width and height) at the frame's centre, ``zoom``
    how much larger than just filling the frame's opening (1 or more)."""

    x: float = 0.5
    y: float = 0.5
    zoom: float = 1.0

    @classmethod
    def parse(cls, x: object, y: object, zoom: object) -> Placement:
        """From a form's text, kept within bounds; the centre, unzoomed, where unreadable."""

        def number(value: object, default: float, low: float, high: float) -> float:
            try:
                found = float(str(value))
            except ValueError:
                return default
            return min(high, max(low, found)) if found == found else default  # NaN

        return cls(number(x, 0.5, 0, 1), number(y, 0.5, 0, 1), number(zoom, 1.0, 1, 8))


def _picture(data: bytes) -> Image.Image:
    try:
        picture = Image.open(io.BytesIO(data))
        picture.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise MediaError("media.error.content") from exc
    return ImageOps.exif_transpose(picture).convert("RGBA")


def _round_mask(size: int, radius: float) -> Image.Image:
    """A disc of ``radius`` centred in a square, drawn large and scaled down for a smooth
    edge."""
    big = size * 4
    mask = Image.new("L", (big, big), 0)
    centre, r = big / 2, radius * 4
    ImageDraw.Draw(mask).ellipse((centre - r, centre - r, centre + r, centre + r), fill=255)
    return mask.resize((size, size), Image.Resampling.LANCZOS)


def compose(data: bytes, kind: str, placement: Placement | None = None) -> bytes:
    """A picture placed in the kind's frame, as WebP: scaled so its shorter side fills the
    frame's opening times ``zoom``, the chosen point at the centre, cut round to the
    opening, the ring on top. Without a frame on disk, cut round to the whole token.
    Raises :class:`~app.services.media.MediaError` when it cannot be read."""
    place = placement or Placement()
    picture = _picture(data)
    frame_file = FRAME_DIR / f"frame-{kind}.png"
    framed = frame_file.is_file()
    radius = SIZE / 2 * (FRAME_INNER if framed else 1.0)
    width, height = picture.size
    scale = (2 * radius) / min(width, height) * place.zoom
    shown = picture.resize(
        (max(1, round(width * scale)), max(1, round(height * scale))), Image.Resampling.LANCZOS
    )
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    left = round(SIZE / 2 - place.x * shown.width)
    top = round(SIZE / 2 - place.y * shown.height)
    canvas.paste(shown, (left, top), shown)
    canvas.putalpha(ImageChops.multiply(canvas.getchannel("A"), _round_mask(SIZE, radius)))
    if framed:
        canvas.alpha_composite(Image.open(frame_file).convert("RGBA"))
    out = io.BytesIO()
    canvas.save(out, "WEBP", quality=88)
    return out.getvalue()


def crop_round(data: bytes) -> bytes:
    """Any picture as a token cut round to its whole size, centred (no frame)."""
    square = ImageOps.fit(_picture(data), (SIZE, SIZE))
    square.putalpha(ImageChops.multiply(square.getchannel("A"), _round_mask(SIZE, SIZE / 2)))
    out = io.BytesIO()
    square.save(out, "WEBP", quality=88)
    return out.getvalue()


class Tokens:
    """Finds, makes, stores and removes tokens.

    An element with a picture of its own — an NPC's portrait, a place's picture, a
    creature's picture — has its token made from that picture (one picture for both),
    placed in its kind's frame as last set (``<id>.json`` beside the token, centred until
    then) and made again whenever the picture changes. Other elements (an encounter, a
    treasure, a character) take an uploaded token of their own.
    """

    def __init__(
        self,
        prose: ProseStore,
        library_media: Path,
        images_root: Callable[[], Path | None],
        picture: Callable[[str, str], Path | None] | None = None,
    ) -> None:
        """``prose`` finds a world's folder, ``library_media`` holds the tokens of what
        belongs to no world, ``images_root`` is the 5eTools image folder (or None),
        ``picture(kind, id)`` an element's own picture file (or None)."""
        self._prose = prose
        self._library_media = library_media
        self._images_root = images_root
        self._picture = picture

    # -- the table's own -------------------------------------------------------------

    def folder(self, world_id: str | None, kind: str) -> Path | None:
        """Where a world's tokens of a kind live; None for a world that is gone."""
        if world_id is None:
            return self._library_media / FOLDER / kind
        world = self._prose.world_folder(world_id)
        return world / MEDIA_DIR / FOLDER / kind if world is not None else None

    def _path(self, world_id: str | None, kind: str, ident: str, suffix: str) -> Path | None:
        if kind not in OWN_KINDS or not ident or "/" in ident or ident.startswith("."):
            return None
        folder = self.folder(world_id, kind)
        return folder / f"{ident}{suffix}" if folder is not None else None

    def file(self, world_id: str | None, kind: str, ident: str) -> Path | None:
        """The token file of an element (made from its picture, or uploaded), or None."""
        path = self._path(world_id, kind, ident, ".webp")
        return path if path is not None and path.is_file() else None

    def picture(self, kind: str, ident: str) -> Path | None:
        """The element's own picture, which its token is made from; None without one."""
        if kind not in PICTURE_KINDS or self._picture is None:
            return None
        return self._picture(kind, ident)

    def placement(self, world_id: str | None, kind: str, ident: str) -> Placement:
        """Where the element's picture sits in its frame, as last set; centred before."""
        sidecar = self._path(world_id, kind, ident, ".json")
        if sidecar is None or not sidecar.is_file():
            return Placement()
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return Placement()
        return Placement.parse(data.get("x", 0.5), data.get("y", 0.5), data.get("zoom", 1))

    def from_picture(
        self,
        world_id: str | None,
        kind: str,
        ident: str,
        picture: Path,
        placement: Placement | None = None,
    ) -> Path | None:
        """The token made from ``picture``, made (again) when there is none yet, the
        picture changed, or a new ``placement`` is given; None where it cannot be."""
        target = self._path(world_id, kind, ident, ".webp")
        sidecar = self._path(world_id, kind, ident, ".json")
        if target is None or sidecar is None:
            return None
        source = {"picture": str(picture), "mtime": picture.stat().st_mtime_ns}
        recorded: dict[str, object] = {}
        if sidecar.is_file():
            try:
                recorded = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                recorded = {}
        current = all(recorded.get(k) == v for k, v in source.items())
        if placement is None and current and target.is_file():
            return target
        place = placement or self.placement(world_id, kind, ident)
        try:
            made = compose(picture.read_bytes(), kind, place)
        except (OSError, MediaError):
            return None
        write_image(target, made)
        record = {"x": place.x, "y": place.y, "zoom": place.zoom, **source}
        write_image(sidecar, json.dumps(record).encode("utf-8"))
        return target

    def own(self, world_id: str | None, kind: str, ident: str) -> Token | None:
        """The table's own token as a page draws it, its address carrying its version:
        made from the element's picture where it has one, else an uploaded token."""
        if kind in PICTURE_KINDS:
            picture = self.picture(kind, ident)
            path = self.from_picture(world_id, kind, ident, picture) if picture else None
        else:
            path = self.file(world_id, kind, ident)
        return self.served(world_id, kind, ident, path) if path is not None else None

    def served(self, world_id: str | None, kind: str, ident: str, path: Path) -> Token:
        """A token file as a page draws it, its address carrying its version."""
        where = world_id or "-"
        version = path.stat().st_mtime_ns
        return Token(kind, f"{TOKEN_URL}/{where}/{kind}/{quote(ident)}?v={version}")

    def set(
        self,
        world_id: str | None,
        kind: str,
        ident: str,
        filename: str,
        data: bytes,
        placement: Placement | None = None,
    ) -> Path:
        """Place an upload in the kind's frame and keep it as the element's token (for an
        element without a picture of its own). Raises
        :class:`~app.services.media.MediaError` for what is not an image."""
        if kind not in OWN_KINDS:
            raise ValueError(kind)
        checked_image(filename, data)
        target = self._path(world_id, kind, ident, ".webp")
        if target is None:
            raise FileNotFoundError(world_id)
        write_image(target, compose(data, kind, placement))
        return target

    def place(
        self, world_id: str | None, kind: str, ident: str, placement: Placement
    ) -> Path | None:
        """Place the element's own picture anew in its frame; None without a picture."""
        picture = self.picture(kind, ident)
        if picture is None:
            return None
        return self.from_picture(world_id, kind, ident, picture, placement)

    def remove(self, world_id: str | None, kind: str, ident: str) -> None:
        """Forget the element's token and where its picture sat; its next source shows."""
        for suffix in (".webp", ".json"):
            path = self._path(world_id, kind, ident, suffix)
            if path is not None:
                path.unlink(missing_ok=True)

    # -- 5eTools ---------------------------------------------------------------------

    def corpus(self, category: str, name: str, source: str, token: object = None) -> str | None:
        """The address of a 5eTools token, when the image folder holds it. ``token`` is
        the record's own ``token`` field, which names another creature's."""
        root = self._images_root()
        folder = CORPUS_DIRS.get(category)
        if root is None or folder is None:
            return None
        if isinstance(token, dict) and token.get("name") and token.get("source"):
            name, source = str(token["name"]), str(token["source"])
        relative = f"{folder}/tokens/{source}/{token_name(name)}.webp"
        if not (root / relative).is_file():
            return None
        return f"/play/img/{quote(relative)}"

    # -- by element ------------------------------------------------------------------

    def of_entry(self, category: str, name: str, source: str, token: object = None) -> Token:
        """A reference entry's token: 5eTools's where it has one, else its kind's disc."""
        kind = CATEGORY_KINDS.get(category, "entry")
        return Token(kind, self.corpus(category, name, source, token))

    def of_record(self, kind: str, world_id: str | None, ident: str) -> Token:
        """An NPC's, character's, place's, encounter's or treasure's token."""
        return self.own(world_id, kind, ident) or Token(kind)


__all__ = [
    "FRAME_COLOURS",
    "FRAME_INNER",
    "FRAME_URL",
    "OWN_KINDS",
    "PICTURE_KINDS",
    "TOKEN_URL",
    "Placement",
    "Token",
    "Tokens",
    "compose",
    "crop_round",
    "token_name",
]
