"""Round tokens for every element (documentation/TOKENS.md).

A token is found in this order: the table's own upload (an NPC, a player character, a
creature of the builder, a place, an encounter, a treasure), a 5eTools token from the
image folder (creatures, vehicles, objects), else the kind's default disc, which the page
draws itself. An upload is cropped round here and kept in the element's world folder.
"""

from __future__ import annotations

import io
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from app.services.media import MediaError, checked_image, write_image
from app.store.prose import MEDIA_DIR, ProseStore

FOLDER = "tokens"
SIZE = 512
OWN_KINDS = frozenset({"npc", "character", "creature", "place", "encounter", "treasure"})
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


def crop_round(data: bytes) -> bytes:
    """Any picture as a token: its centre square at 512 px, masked round, as WebP.
    Raises :class:`~app.services.media.MediaError` when it cannot be read."""
    try:
        picture = Image.open(io.BytesIO(data))
        picture.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise MediaError("media.error.content") from exc
    square = ImageOps.fit(ImageOps.exif_transpose(picture).convert("RGBA"), (SIZE, SIZE))
    # The mask drawn four times larger and scaled down, for a smooth edge.
    mask = Image.new("L", (SIZE * 4, SIZE * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, SIZE * 4 - 1, SIZE * 4 - 1), fill=255)
    alpha = Image.composite(
        square.getchannel("A"), Image.new("L", square.size, 0), mask.resize(square.size)
    )
    square.putalpha(alpha)
    out = io.BytesIO()
    square.save(out, "WEBP", quality=88)
    return out.getvalue()


class Tokens:
    """Finds, stores and removes tokens."""

    def __init__(
        self,
        prose: ProseStore,
        library_media: Path,
        images_root: Callable[[], Path | None],
    ) -> None:
        """``prose`` finds a world's folder, ``library_media`` holds the tokens of what
        belongs to no world, ``images_root`` is the 5eTools image folder (or None)."""
        self._prose = prose
        self._library_media = library_media
        self._images_root = images_root

    # -- the table's own -------------------------------------------------------------

    def folder(self, world_id: str | None, kind: str) -> Path | None:
        """Where a world's tokens of a kind live; None for a world that is gone."""
        if world_id is None:
            return self._library_media / FOLDER / kind
        world = self._prose.world_folder(world_id)
        return world / MEDIA_DIR / FOLDER / kind if world is not None else None

    def file(self, world_id: str | None, kind: str, ident: str) -> Path | None:
        """The table's own token of an element, or None."""
        if kind not in OWN_KINDS or not ident or "/" in ident or ident.startswith("."):
            return None
        folder = self.folder(world_id, kind)
        path = folder / f"{ident}.webp" if folder is not None else None
        return path if path is not None and path.is_file() else None

    def own(self, world_id: str | None, kind: str, ident: str) -> Token | None:
        """The table's own token as a page draws it, its address carrying its version."""
        path = self.file(world_id, kind, ident)
        if path is None:
            return None
        where = world_id or "-"
        version = path.stat().st_mtime_ns
        return Token(kind, f"{TOKEN_URL}/{where}/{kind}/{quote(ident)}?v={version}")

    def set(self, world_id: str | None, kind: str, ident: str, filename: str, data: bytes) -> Path:
        """Crop an upload round and keep it as the element's token. Raises
        :class:`~app.services.media.MediaError` for what is not an image."""
        if kind not in OWN_KINDS:
            raise ValueError(kind)
        checked_image(filename, data)
        folder = self.folder(world_id, kind)
        if folder is None:
            raise FileNotFoundError(world_id)
        target = folder / f"{ident}.webp"
        write_image(target, crop_round(data))
        return target

    def remove(self, world_id: str | None, kind: str, ident: str) -> None:
        """Forget the element's own token; its next source shows again."""
        path = self.file(world_id, kind, ident)
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


__all__ = ["OWN_KINDS", "TOKEN_URL", "Token", "Tokens", "crop_round", "token_name"]
