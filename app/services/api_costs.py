"""What the Claude API costs Paravent (documentation/TRANSLATION.md §7): every answer of
the machine translation and the name suggestions counted by its tokens and priced by
its model, per run and per month.

The API cannot say how much prepaid credit is left; Anthropic's bill is the authority,
so every sum here is "about". A month's sum is kept in ``library/i18n/_api_costs.json``,
beside the translations it paid for, so it travels with them (``sync-server.sh
--translations``). A run counts its own share: :meth:`ApiCosts.tally` opens one, and
every answer recorded while it is open, in that task and the tasks it starts, adds to it.
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.store.jsonfile import write_json

logger = logging.getLogger(__name__)

# US dollars per million tokens: input, output, cache read (platform.claude.com pricing,
# 2026-11). A cache write of five minutes costs 1.25 times the input.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-fable-5": (10.0, 50.0, 1.0),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 0.50),
    "claude-opus-4-7": (5.0, 25.0, 0.50),
    "claude-opus-4-6": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
}
CACHE_WRITE = 1.25
# What a run says when it stopped because the month's budget was spent (shown in the
# reader's language by the pages).
BUDGET_SPENT = "budget-spent"
# The share of the budget from which the pages warn.
BUDGET_WARN = 0.8


def price_of(model: str) -> tuple[float, float, float] | None:
    """A model's prices, matched by its id or the longest id it starts with (a dated
    snapshot, ``claude-opus-5-5-20260401``); None for a model not listed."""
    if model in PRICES:
        return PRICES[model]
    known = sorted((m for m in PRICES if model.startswith(m)), key=len, reverse=True)
    return PRICES[known[0]] if known else None


@dataclass
class Spend:
    """Tokens and their price, summed: one answer, one run, or one month."""

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    usd: float = 0.0
    requests: int = 0
    # Tokens of a model with no price here: counted, not priced.
    unpriced: int = 0

    def add(self, other: Spend) -> None:
        """Add ``other`` to this sum."""
        for f in fields(self):
            setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))

    @property
    def tokens_in(self) -> int:
        """Every input token, cached or not."""
        return self.input + self.cache_read + self.cache_write

    @classmethod
    def of(cls, data: Any) -> Spend:
        """A sum as kept in the file; anything unreadable counts as nothing."""
        if not isinstance(data, dict):
            return cls()
        out = cls()
        for f in fields(cls):
            value = data.get(f.name, 0)
            if isinstance(value, int | float) and not isinstance(value, bool):
                setattr(out, f.name, float(value) if f.name == "usd" else int(value))
        return out


def priced(model: str, input: int, output: int, cache_read: int, cache_write: int) -> Spend:
    """One model's tokens and what they cost."""
    spend = Spend(input, output, cache_read, cache_write, requests=0)
    prices = price_of(model)
    if prices is None:
        spend.unpriced = input + output + cache_read + cache_write
        return spend
    per_in, per_out, per_read = prices
    spend.usd = (
        input * per_in
        + cache_write * per_in * CACHE_WRITE
        + cache_read * per_read
        + output * per_out
    ) / 1_000_000
    return spend


def spend_of(response: Any) -> Spend:
    """What one answer of the Messages API cost: each model's part priced at its own
    rates when a declined request was carried on by a fallback model (its usage's
    ``iterations``), else the whole by the model that answered."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return Spend()
    answered = str(getattr(response, "model", "") or "")
    total = Spend(requests=1)
    parts = [
        part
        for part in (getattr(usage, "iterations", None) or [])
        if getattr(part, "type", "") in ("message", "fallback_message")
    ]
    for part in parts or [usage]:
        total.add(
            priced(
                str(getattr(part, "model", None) or answered),
                getattr(part, "input_tokens", 0) or 0,
                getattr(part, "output_tokens", 0) or 0,
                getattr(part, "cache_read_input_tokens", 0) or 0,
                getattr(part, "cache_creation_input_tokens", 0) or 0,
            )
        )
    return total


_TALLY: contextvars.ContextVar[Spend | None] = contextvars.ContextVar("api_tally", default=None)


def month_key(when: datetime | None = None) -> str:
    """The month a cost is counted in, ``2026-11``, by UTC."""
    return (when or datetime.now(UTC)).strftime("%Y-%m")


class ApiCosts:
    """The months' sums in a file, and the runs counting their own share."""

    def __init__(self, path: Path, budget: float = 0.0) -> None:
        """Keep the months' sums in ``path``; ``budget`` is what a month may cost, in US
        dollars (0: no limit)."""
        self._path = path
        self._budget = max(0.0, budget)
        self._lock = threading.Lock()

    @property
    def budget(self) -> float:
        """What a month may cost, in US dollars; 0 for no limit."""
        return self._budget

    def left(self) -> float | None:
        """What this month's budget still allows, never below 0; None without one."""
        if not self._budget:
            return None
        return max(0.0, self._budget - self.month().usd)

    def spent(self) -> bool:
        """True when this month's budget is used up: no further request is sent."""
        left = self.left()
        return left is not None and left <= 0

    @property
    def path(self) -> Path:
        """The file the months' sums are kept in."""
        return self._path

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            logger.warning("Could not read %s; counting from nothing", self._path)
            return {}
        return data if isinstance(data, dict) else {}

    def record(self, spend: Spend, when: datetime | None = None) -> None:
        """Count one answer: into its month, and into the run open in this context."""
        if not spend.requests:
            return
        tally = _TALLY.get()
        if tally is not None:
            tally.add(spend)
        with self._lock:
            data = self._read()
            found = data.get("months")
            months: dict[str, Any] = found if isinstance(found, dict) else {}
            key = month_key(when)
            month = Spend.of(months.get(key))
            month.add(spend)
            months[key] = asdict(month)
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                write_json(self._path, {"months": dict(sorted(months.items()))})
            except OSError:
                logger.exception("Could not keep the API's costs in %s", self._path)

    def month(self, key: str | None = None) -> Spend:
        """A month's sum, this month's when none is named."""
        months = self._read().get("months")
        return Spend.of(months.get(key or month_key())) if isinstance(months, dict) else Spend()

    def months(self) -> dict[str, Spend]:
        """Every month counted, newest first."""
        months = self._read().get("months")
        if not isinstance(months, dict):
            return {}
        return {k: Spend.of(v) for k, v in sorted(months.items(), reverse=True)}


@contextmanager
def tally(spend: Spend | None = None) -> Iterator[Spend]:
    """A run's own sum: every answer :meth:`ApiCosts.record` counts inside, in this task
    and the tasks it starts, adds to it as it comes, so a progress shows it growing."""
    mine = spend if spend is not None else Spend()
    token = _TALLY.set(mine)
    try:
        yield mine
    finally:
        _TALLY.reset(token)


__all__ = [
    "BUDGET_SPENT",
    "BUDGET_WARN",
    "CACHE_WRITE",
    "PRICES",
    "ApiCosts",
    "Spend",
    "month_key",
    "price_of",
    "spend_of",
    "tally",
]
