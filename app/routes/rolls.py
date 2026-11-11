"""Telling the browser a roll happened, so the device that rolled plays its sound.

A response to an action that rolled dice carries ``HX-Trigger: {"paravent-roll":
{"outcome": …}}``; htmx raises that as an event, and ``static/js/sound.js`` plays the
dice, plus a chime on a natural 20 or a "wah-wah" on a natural 1. Only the device that
asked hears it — the others see the roll in their log.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from fastapi import Response

from app.models.protocol import ProtocolEntry

ROLL_EVENT = "paravent-roll"


def new_roll(added: Sequence[ProtocolEntry]) -> ProtocolEntry | None:
    """The roll among what an action just logged (newest first), or None. When it rolled
    several — an attack and, in a fight, its damage — a natural 20 or 1 among them wins,
    so its sound is not lost behind the damage roll."""
    rolls = [e for e in added if e.roll is not None]
    marked = next((e for e in rolls if e.outcome in ("crit", "fumble")), None)
    return marked or (rolls[0] if rolls else None)


def announce_roll(response: Response, outcome: str = "") -> Response:
    """Have the browser play the roll's sound: ``crit``, ``fumble`` or a plain roll."""
    response.headers["HX-Trigger"] = json.dumps({ROLL_EVENT: {"outcome": outcome}})
    return response


__all__ = ["ROLL_EVENT", "announce_roll", "new_roll"]
