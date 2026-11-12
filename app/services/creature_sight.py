"""What anyone sees of a creature by looking at it (documentation/COMBAT.md §5, "What
players know about a creature"): size and kind, how it moves, the gear it carries, the
books' description and picture — and nothing that is not seen, no AC and no hit points.

Built from the statblock and its fluff for the players' Details on a combatant. The
gear is read two ways, as the editions print it: 2024 statblocks list it (``gear``);
2014 ones name armour in their AC (``"from": ["{@item leather armor|phb}"]``) and carry
weapons as attacks named after an item ("Scimitar"), unlike a dragon's Bite.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models.compendium import Fluff
from app.services.text import paragraphs, strip_tags

# An attack, in either edition's words: "{@atk mw} …", "{@atkr m} …".
_ATTACK = re.compile(r"^\s*\{@atkr?\s")
# Armour named in an AC: "{@item leather armor|phb}".
_ITEM = re.compile(r"\{@item ([^|}]+)")


@dataclass(frozen=True)
class Sight:
    """A creature as seen. ``kind`` is the statblock's own type value (type and tags
    only), ``speed`` its speed mapping; the screen words both in the reader's language."""

    sizes: tuple[str, ...] = ()
    kind: Any = None
    speed: Mapping[str, Any] = field(default_factory=dict)
    # English item names, as the books print them ("Leather Armor").
    gear: tuple[str, ...] = ()
    # The first paragraph of the books' description, 5eTools tags and all.
    description: str = ""
    # The picture's path under the image folder, when the file is there.
    image: str | None = None


def sight(
    data: Mapping[str, Any],
    fluff: Fluff | None,
    item_name: Callable[[str], str | None],
    images: Path | None,
) -> Sight:
    """What ``data`` (a statblock) shows. ``item_name(name)`` is the item's printed name,
    or None for no item; ``images`` the image folder, if there is one."""
    raw_speed = data.get("speed")
    speed = raw_speed if isinstance(raw_speed, Mapping) else {"walk": raw_speed or 0}
    return Sight(
        sizes=tuple(str(s) for s in data.get("size") or ()),
        kind=data.get("type"),
        speed=dict(speed),
        gear=_unique(_gear(data, item_name)),
        description=_description(fluff),
        image=fluff_image(fluff, images),
    )


def _gear(data: Mapping[str, Any], item_name: Callable[[str], str | None]) -> Iterable[str]:
    listed = data.get("gear") or []
    if listed:
        for entry in listed:
            ref = entry.get("item") if isinstance(entry, Mapping) else entry
            if isinstance(ref, str):
                found = item_name(ref.split("|", 1)[0])
                if found:
                    yield found
        return
    for ac in data.get("ac") or []:
        if not isinstance(ac, Mapping):
            continue
        for worn in ac.get("from") or []:
            for name in _ITEM.findall(str(worn)):
                found = item_name(name.strip())
                if found:
                    yield found
    for action in data.get("action") or []:
        if not isinstance(action, Mapping) or not _is_attack(action):
            continue
        found = item_name(strip_tags(str(action.get("name", ""))).strip())
        if found:
            yield found


def _is_attack(action: Mapping[str, Any]) -> bool:
    entries = action.get("entries") or []
    return bool(entries) and isinstance(entries[0], str) and bool(_ATTACK.match(entries[0]))


def _description(fluff: Fluff | None) -> str:
    """The first paragraph of the books' description, its tags kept for the renderer
    (a player's links only to what they may read)."""
    if fluff is None:
        return ""
    first = paragraphs(fluff.entries, limit=1, tags=True)
    return first[0] if first else ""


def fluff_image(fluff: Fluff | None, images: Path | None) -> str | None:
    """The first of an entry's pictures that the image folder holds."""
    if fluff is None or images is None:
        return None
    for picture in fluff.images:
        href = picture.get("href") if isinstance(picture, Mapping) else None
        if not isinstance(href, Mapping) or href.get("type") != "internal":
            continue
        path = str(href.get("path") or "")
        if path and image_file(images, path) is not None:
            return path
    return None


def image_file(images: Path, path: str) -> Path | None:
    """The image at ``path`` under ``images``, or None — never a file outside it."""
    root = images.resolve()
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def _unique(names: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(names))


__all__ = ["Sight", "fluff_image", "image_file", "sight"]
