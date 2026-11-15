"""Coins: what a purse is worth, paying out of it and putting money in.

Prices are kept in copper, as the corpus keeps them (``"value": 1500`` is 15 gp).
Paying takes the smallest coins first and breaks a larger one only when it must, giving
the change back in smaller coins, so a purse of mixed coins keeps its shape instead of
being changed into gold.
"""

from __future__ import annotations

from app.models.pc import Currency

# Each denomination's worth in copper, smallest first.
VALUES = {"cp": 1, "sp": 10, "ep": 50, "gp": 100, "pp": 1000}
# Change and earnings are given in these (electrum only when a purse already holds it).
_CHANGE = ("gp", "sp", "cp")


def worth(purse: Currency) -> int:
    """A purse's worth in copper."""
    return sum(getattr(purse, d) * v for d, v in VALUES.items())


def coins_for(copper: int) -> dict[str, int]:
    """An amount in the fewest gold, silver and copper coins."""
    out: dict[str, int] = {}
    rest = max(0, copper)
    for denomination in _CHANGE:
        count, rest = divmod(rest, VALUES[denomination])
        if count:
            out[denomination] = count
    return out


def pay(purse: Currency, copper: int) -> Currency | None:
    """The purse after paying ``copper``; None when it holds too little."""
    if copper < 0 or worth(purse) < copper:
        return None
    held = purse.model_dump()
    rest = copper
    for denomination, value in VALUES.items():
        take = min(held[denomination], rest // value)
        held[denomination] -= take
        rest -= take * value
    while rest > 0:
        # Every coin still held is worth more than what is left: break the smallest.
        denomination = next(d for d in VALUES if held[d] > 0)
        held[denomination] -= 1
        change = VALUES[denomination] - rest
        rest = 0
        for coin, count in coins_for(change).items():
            held[coin] += count
    return Currency(**held)


def earn(purse: Currency, copper: int) -> Currency:
    """The purse with ``copper`` more, in gold, silver and copper."""
    held = purse.model_dump()
    for coin, count in coins_for(copper).items():
        held[coin] += count
    return Currency(**held)


def text(copper: int) -> str:
    """``"12gp,5sp"`` — an amount as the log writes a purse (the page names the coins)."""
    parts = [f"{n}{d}" for d, n in coins_for(copper).items()]
    return ",".join(parts) or "0cp"


__all__ = ["VALUES", "coins_for", "earn", "pay", "text", "worth"]
