"""The features a world plays with, switched on and off in World settings.

What a table may leave out has a switch here: the road and its survival, a dungeon's
encounter checks, hazards scaled to the party, the planes' rules, shops, lifestyle costs,
followers, the running costs of property, downtime, bastions, quests, factions, chases,
madness and the safety tools. One switched off is gone from the gamemaster's and the
players' screens, and what the clock would do for it (lifestyle, wages, upkeep, rations,
bastion turns, a dungeon's checks, a plane's hourly saves) stops; its records are kept for
when it comes back.

A world written before the switches (``features`` None) plays with all of them; a new
world starts with the basics. The guides' variant rules keep their own switches
(app/services/combat_rules.py, app/services/character_rules.py).
"""

from __future__ import annotations

from collections.abc import Iterable

from app.models.gamestate import World

# Each feature by its key, in the groups World settings shows them in.
GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("exploration", ("travel", "survival", "dungeon", "hazards", "planes")),
    ("money", ("shops", "lifestyle", "followers", "upkeep")),
    ("between", ("downtime", "bastions", "quests", "factions")),
    ("table", ("chases", "madness", "safety")),
)
FEATURES: tuple[str, ...] = tuple(key for _, keys in GROUPS for key in keys)
# What a new world starts with.
BASICS: tuple[str, ...] = ("travel", "shops", "quests", "safety")
# A feature that is part of another: off with it.
PART_OF = {"survival": "travel"}


def kept(keys: Iterable[str]) -> list[str]:
    """Known features from a form or a file, in their own order, each once."""
    wanted = {k.strip() for k in keys}
    return [k for k in FEATURES if k in wanted]


def on(world: World | None, key: str) -> bool:
    """Whether ``world`` plays with the feature ``key``: no world, or one from before the
    switches, plays with all of them."""
    if world is None or world.features is None:
        return True
    parent = PART_OF.get(key)
    if parent is not None and parent not in world.features:
        return False
    return key in world.features


def switched_on(world: World | None) -> frozenset[str]:
    """Every feature the world plays with."""
    return frozenset(k for k in FEATURES if on(world, k))


__all__ = ["BASICS", "FEATURES", "GROUPS", "PART_OF", "kept", "on", "switched_on"]
