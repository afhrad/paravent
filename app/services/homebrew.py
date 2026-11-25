"""The table's builder designs as homebrew, one source per world.

A design publishes under its world's own source, ``Homebrew-<world id>``
(``app.models.gamestate.world_homebrew``), so a world reads only its own designs; one of no
world under plain ``Homebrew``. The world gets its source declared on publishing; it narrows
none of its books (``SourceSet``). A ``Name|Homebrew`` reference written before still finds
the design (``app.store.copyresolver.homebrew_alias``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from app.models.gamestate import (
    HOMEBREW,
    SourceSet,
    is_homebrew,
    is_world_homebrew,
    world_homebrew,
)
from app.store.gamestate import GameStateStore
from app.store.jsonfile import write_json

logger = logging.getLogger(__name__)

# Written into a homebrew file's ``_meta`` once its designs carry their world's source.
PER_WORLD = "perWorld"


def meta_sources(sources: Iterable[str], full: str) -> list[dict[str, str]]:
    """The ``_meta.sources`` of a homebrew file: each source shows as "Homebrew"."""
    return [
        {"json": s, "abbreviation": HOMEBREW, "full": full}
        for s in sorted(set(sources)) or [HOMEBREW]
    ]


def declare(store: GameStateStore, world_ids: Iterable[str]) -> list[str]:
    """Declare each world's own homebrew source in its books, so its designs resolve
    there; the names of the worlds that had it not yet."""
    named: list[str] = []
    for world_id in sorted(set(world_ids)):
        world = store.worlds.find(world_id)
        own = world_homebrew(world_id)
        if world is None or own.lower() in world.source_set.normalized:
            continue
        sources = [*world.source_set.sources, own]
        store.worlds.update(world.id, source_set=SourceSet(sources=sources))
        named.append(world.name)
    return sorted(named)


def finder(worlds: dict[str, str | None]) -> Callable[[str], str | None]:
    """A design's world by its name, from ``worlds`` keyed by the casefolded name."""
    return lambda name: worlds.get(name.casefold())


def restamp(path: Path, key: str, world_of: Callable[[str], str | None]) -> set[str]:
    """Give each ``key`` entry of a homebrew file its design's world's source
    (``world_of(name)``): a file from before worlds had their own (2026-11-25), or a
    design moved to another world. Only the sources change, so a design's unpublished
    edits stay unpublished; the file is written only when one did. The worlds that
    gained a design."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(data, dict) or not isinstance(data.get("_meta"), dict):
        return set()
    meta, entries = data["_meta"], data.get(key, [])
    worlds: set[str] = set()
    changed = not meta.get(PER_WORLD)
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or not is_homebrew(str(entry.get("source", ""))):
            continue
        world_id = world_of(str(entry.get("name", "")))
        wanted = world_homebrew(world_id)
        if entry["source"] != wanted:
            entry["source"] = wanted
            changed = True
            if world_id:
                worlds.add(world_id)
    if not changed:
        return set()
    full = next((str(s.get("full")) for s in meta.get("sources", []) if s.get("full")), HOMEBREW)
    used = {str(e.get("source")) for e in entries if isinstance(e, dict)}
    meta["sources"] = meta_sources(used, full)
    meta[PER_WORLD] = True
    write_json(path, data)
    logger.info("Homebrew %s: sources set by world", path.name)
    return worlds


def restamp_lookup(path: Path, world_of: Callable[[str], str | None]) -> bool:
    """The spell-list lookup likewise: each spell's lists under its world's source;
    True when it changed."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    out: dict[str, Any] = {}
    for source, spells in data.items():
        if not is_homebrew(source) or not isinstance(spells, dict):
            out[source] = spells
            continue
        for spell, lists in spells.items():
            out.setdefault(world_homebrew(world_of(spell)), {})[spell] = lists
    if out == data:
        return False
    write_json(path, out)
    return True


__all__ = [
    "PER_WORLD",
    "declare",
    "finder",
    "is_world_homebrew",
    "meta_sources",
    "restamp",
    "restamp_lookup",
]
