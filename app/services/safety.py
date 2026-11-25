"""Safety tools for the table: the X-card, and lines and veils.

The X-card is a button on a player's phone. Pressed, it tells the gamemaster, and no one
else, that someone wants the scene to change: an entry in the gamemaster's log with no
name on it, which always pops up for the gamemaster. Lines (what does not happen in the
game) and veils (what happens off screen) are a world's agreements, kept by the
gamemaster and readable by its players, who may suggest more without a name.

Session zero follows the 2024 guide. The gamemaster writes the game's theme and flavor
and the potentially sensitive elements (the top of its Game Expectations sheet), what
"Setting the Stage" asks for (the starting location and key events, house rules), how
the party came together (one of its five ways, or in words) and the starting level.
Each player fills a sheet without a name: a hard (X) or soft (?) limit on each element,
anything else to avoid, hopes and expectations, concerns about the table. The guide
gathers the limits into an anonymous document the whole group can read; so does the
Safety page, counting each element's limits and listing the rest in no one's order.
A player's sheet is kept under a token made from the name with the server's key, so it
can be changed later but not traced back to its writer from the file.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.models.gamestate import ExpectationSheet, SafetySuggestion, TableSafety
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.protocol import ProtocolService
from app.store.gamestate import GameStateStore

XCARD = "xcard"
TOGETHER = ("bonding", "happenstance", "acquaintance", "history", "tavern")
# Where the 2024 guide prints each way the party came together.
TOGETHER_HEADINGS = {
    "bonding": "Bonding Event",
    "happenstance": "Happenstance",
    "acquaintance": "Mutual Acquaintance",
    "history": "Shared History",
    "tavern": "Tavern Gathering",
}


@dataclass
class Gathered:
    """The players' sheets gathered, with no one's name or order: how many sheets,
    each sensitive element's hard and soft limits, and the rest, sorted."""

    sheets: int = 0
    limits: dict[str, tuple[int, int]] = field(default_factory=dict)  # (hard, soft)
    avoid: list[str] = field(default_factory=list)
    hopes: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)


def gather(record: TableSafety) -> Gathered:
    """The anonymous summary of a world's expectations sheets."""
    out = Gathered(sheets=len(record.sheets))
    for element in record.sensitive:
        marks = [sheet.limits.get(element) for sheet in record.sheets.values()]
        out.limits[element] = (marks.count("hard"), marks.count("soft"))
    for sheet in record.sheets.values():
        out.avoid += [x for x in (sheet.avoid.strip(),) if x]
        out.hopes += [x for x in (sheet.hopes.strip(),) if x]
        out.concerns += [x for x in (sheet.concerns.strip(),) if x]
    out.avoid.sort(key=str.casefold)
    out.hopes.sort(key=str.casefold)
    out.concerns.sort(key=str.casefold)
    return out


def sheet_token(key: str, world_id: str, username: str) -> str:
    """A player's anonymous token in a world: an HMAC of the name with the server's key."""
    digest = hmac.new(key.encode(), f"{world_id}:{username}".encode(), hashlib.sha256)
    return digest.hexdigest()[:24]


class SafetyService:
    """Reads and writes ``library/safety.json``; the X-card goes to the log."""

    def __init__(self, store: GameStateStore, protocol: ProtocolService | None) -> None:
        self._store = store
        self._protocol = protocol

    def xcard(self, world_id: str | None) -> None:
        """Someone tapped the X-card: the gamemaster is told, with no name."""
        if self._protocol is None:
            return
        entry = ProtocolEntry(
            kind=EntryKind.CUSTOM,
            what=XCARD,
            visibility=Visibility.GM,
            scope=Scope(world=world_id),
        )
        self._protocol.append([entry])

    def of(self, world_id: str) -> TableSafety:
        """A world's agreements (empty when none were written)."""
        return self._store.safety.find(world_id) or TableSafety(id=world_id)

    def set_lists(self, world_id: str, lines: Sequence[str], veils: Sequence[str]) -> TableSafety:
        """The gamemaster's lines and veils, one per entry, blanks left out."""
        record = self.of(world_id).model_copy(
            update={"lines": _clean(lines), "veils": _clean(veils)}
        )
        record.touch()
        return self._store.safety.upsert(record)

    def suggest(self, world_id: str, kind: str, text: str) -> TableSafety:
        """A player puts a line or veil forward; no name is kept."""
        cleaned = " ".join(text.split())[:300]
        if kind not in ("line", "veil") or not cleaned:
            return self.of(world_id)
        record = self.of(world_id)
        suggestion = SafetySuggestion.model_validate({"kind": kind, "text": cleaned})
        record = record.model_copy(update={"suggested": [*record.suggested, suggestion]})
        record.touch()
        return self._store.safety.upsert(record)

    def settle(self, world_id: str, index: int, take: bool) -> TableSafety:
        """The gamemaster takes a suggestion into its list, or drops it."""
        record = self.of(world_id)
        if not 0 <= index < len(record.suggested):
            return record
        chosen = record.suggested[index]
        rest = [s for n, s in enumerate(record.suggested) if n != index]
        lines, veils = list(record.lines), list(record.veils)
        if take:
            (lines if chosen.kind == "line" else veils).append(chosen.text)
        record = record.model_copy(update={"suggested": rest, "lines": lines, "veils": veils})
        record.touch()
        return self._store.safety.upsert(record)

    def set_zero(self, world_id: str, values: Mapping[str, object]) -> TableSafety:
        """The gamemaster's half of session zero; a sheet's limits on an element no
        longer listed are dropped."""
        sensitive = values.get("sensitive") or []
        assert isinstance(sensitive, list)
        elements = list(dict.fromkeys(_clean([str(x) for x in sensitive])))[:30]
        together = str(values.get("together") or "")
        level = str(values.get("starting_level") or "")
        record = self.of(world_id)
        sheets = {
            token: sheet.model_copy(
                update={"limits": {k: v for k, v in sheet.limits.items() if k in elements}}
            )
            for token, sheet in record.sheets.items()
        }
        record = record.model_copy(
            update={
                "theme": str(values.get("theme") or "").strip()[:4000],
                "sensitive": elements,
                "start": str(values.get("start") or "").strip()[:4000],
                "house_rules": str(values.get("house_rules") or "").strip()[:4000],
                "together": together if together in TOGETHER else "",
                "together_note": str(values.get("together_note") or "").strip()[:2000],
                "starting_level": int(level) if level.isdigit() and int(level) <= 20 else 0,
                "sheets": sheets,
            }
        )
        record.touch()
        return self._store.safety.upsert(record)

    def fill(
        self,
        world_id: str,
        token: str,
        limits: Mapping[str, str],
        avoid: str,
        hopes: str,
        concerns: str,
    ) -> TableSafety:
        """A player's expectations sheet, written or written again under its token; an
        empty sheet is taken back."""
        record = self.of(world_id)
        kept = {k: v for k, v in limits.items() if k in record.sensitive and v in ("hard", "soft")}
        sheet = ExpectationSheet.model_validate(
            {
                "limits": kept,
                "avoid": avoid.strip()[:1000],
                "hopes": hopes.strip()[:2000],
                "concerns": concerns.strip()[:2000],
            }
        )
        sheets = dict(record.sheets)
        if kept or sheet.avoid or sheet.hopes or sheet.concerns:
            sheets[token] = sheet
        else:
            sheets.pop(token, None)
        record = record.model_copy(update={"sheets": sheets})
        record.touch()
        return self._store.safety.upsert(record)

    def adopt(self, world_id: str, element: str, kind: str) -> TableSafety:
        """A sensitive element the sheets limit made a line or a veil."""
        record = self.of(world_id)
        if element not in record.sensitive or kind not in ("line", "veil"):
            return record
        lines, veils = list(record.lines), list(record.veils)
        target = lines if kind == "line" else veils
        if element not in target:
            target.append(element)
        record = record.model_copy(update={"lines": lines, "veils": veils})
        record.touch()
        return self._store.safety.upsert(record)


def _clean(values: Sequence[str]) -> list[str]:
    return [" ".join(v.split())[:300] for v in values if v.strip()]


__all__ = [
    "TOGETHER",
    "TOGETHER_HEADINGS",
    "XCARD",
    "Gathered",
    "SafetyService",
    "gather",
    "sheet_token",
]
