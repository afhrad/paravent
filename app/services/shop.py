"""A shop during play: the gamemaster opens one in a world, its characters buy and sell.

The gamemaster names the shop, ticks who may use it (nobody ticked: every character
of the world), sets its prices as a share of the list price and what it pays for
goods. On the shelves is the same priced, mundane gear as the character wizard's shop,
from the world's books, in the world's edition, and whatever magic items the
gamemaster puts there: one by one, rolled by rarity from the world's books, or a
treasure's items. A magic item without a price of its own costs its rarity's value
(halved for a consumable other than a spell scroll, a scroll twice what scribing it
costs; app/store/registry.py). A purchase is paid from the purse (the smallest coins
first, change given) and lands in the pack; a carried item the books price is bought
back at the shop's share of its list price. Both are logged. The gamemaster closes
the shop again.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import ceil

from app.models.gamestate import Shop, ShopItem
from app.models.protocol import EntryKind, ProtocolEntry, Scope
from app.services.chargen.catalogue import Offer
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.store.gamestate import GameStateStore


class ShopError(Translatable):
    """A sale the shop refuses."""


@dataclass(frozen=True)
class Ware:
    """An item on the shelves and its price here, in copper."""

    offer: Offer
    price: int
    category: str = "item"  # or "baseitem": where its reference page is

    @property
    def reference(self) -> str:
        return self.offer.reference


def priced(list_price: int, percent: int) -> int:
    """A list price (copper) at ``percent``; a fraction of a copper rounds up."""
    return ceil(list_price * percent / 100)


def bought_back(list_price: int, percent: int) -> int:
    """What the shop pays for an item of ``list_price``; a fraction rounds down."""
    return list_price * percent // 100


class ShopService:
    """Reads and writes ``library/shops.json``; opening and closing go to the log."""

    def __init__(self, store: GameStateStore, protocol: ProtocolService | None) -> None:
        self._store = store
        self._protocol = protocol

    def of(self, world_id: str) -> Shop:
        """A world's shop (closed until opened)."""
        return self._store.shops.find(world_id) or Shop(id=world_id)

    def open(
        self,
        world_id: str,
        *,
        name: str,
        characters: Sequence[str],
        markup: int,
        buys: int,
        by: str,
        by_name: str,
    ) -> Shop:
        """Open (or change) the shop: its name, who may use it, its prices."""
        if not 10 <= markup <= 1000 or not 0 <= buys <= 100:
            raise ShopError("shop.error.prices")
        shop = self.of(world_id).model_copy(
            update={
                "open": True,
                "name": " ".join(name.split())[:80],
                "characters": list(dict.fromkeys(characters)),
                "markup": markup,
                "buys": buys,
            }
        )
        was_open = self.of(world_id).open
        saved = self._save(shop)
        if not was_open:
            self._log(world_id, by, by_name, f"shop:open:{saved.name}")
        return saved

    def close(self, world_id: str, by: str, by_name: str) -> Shop:
        shop = self.of(world_id)
        if shop.open:
            self._log(world_id, by, by_name, f"shop:close:{shop.name}")
        return self._save(shop.model_copy(update={"open": False}))

    def stock(self, world_id: str, items: Sequence[tuple[str, int, int | None]]) -> Shop:
        """Put magic items on the shelf: ``(reference, price in copper, count)``; one
        already there has its count raised (or made endless) and keeps its price."""
        shop = self.of(world_id)
        stock = list(shop.stock)
        for ref, price, count in items:
            held = next((n for n, s in enumerate(stock) if s.ref.lower() == ref.lower()), None)
            if held is None:
                stock.append(ShopItem(ref=ref, price=max(0, price), count=count))
            else:
                old = stock[held].count
                more = None if old is None or count is None else min(999, old + count)
                stock[held] = stock[held].model_copy(update={"count": more})
        return self._save(shop.model_copy(update={"stock": stock}))

    def reprice(self, world_id: str, index: int, price: int, count: int | None) -> Shop:
        """Change a stocked item's price or count."""
        shop = self.of(world_id)
        if not 0 <= index < len(shop.stock) or price < 0:
            raise ShopError("shop.error.prices")
        stock = [
            s.model_copy(update={"price": price, "count": count}) if n == index else s
            for n, s in enumerate(shop.stock)
        ]
        return self._save(shop.model_copy(update={"stock": stock}))

    def unstock(self, world_id: str, index: int) -> Shop:
        shop = self.of(world_id)
        kept = [s for n, s in enumerate(shop.stock) if n != index]
        return self._save(shop.model_copy(update={"stock": kept}))

    def sold(self, world_id: str, ref: str, count: int) -> ShopItem:
        """Take ``count`` of a stocked item off the shelf (it was bought): the item as it
        was, raising when the shelf holds fewer."""
        shop = self.of(world_id)
        index = next((n for n, s in enumerate(shop.stock) if s.ref.lower() == ref.lower()), None)
        if index is None:
            raise ShopError("shop.error.sold_out")
        item = shop.stock[index]
        if item.count is not None and item.count < count:
            raise ShopError("shop.error.sold_out")
        stock = list(shop.stock)
        if item.count is not None:
            if item.count == count:
                del stock[index]
            else:
                stock[index] = item.model_copy(update={"count": item.count - count})
        self._save(shop.model_copy(update={"stock": stock}))
        return item

    def serves(self, world_id: str | None, character_id: str) -> Shop | None:
        """The open shop a character may use in its world, if any."""
        if world_id is None:
            return None
        shop = self.of(world_id)
        if not shop.open or (shop.characters and character_id not in shop.characters):
            return None
        return shop

    @staticmethod
    def wares(
        shop: Shop, offers: Sequence[Offer], base: Callable[[Offer], bool] = lambda _o: False
    ) -> list[Ware]:
        """The shelves at this shop's prices; ``base(offer)`` tells a base item."""
        return [
            Ware(o, priced(_list_price(o), shop.markup), "baseitem" if base(o) else "item")
            for o in offers
        ]

    @staticmethod
    def buys_for(shop: Shop, offer: Offer | None) -> int:
        """What the shop pays for one of ``offer``; 0 for what the books don't price."""
        return bought_back(_list_price(offer), shop.buys) if offer is not None else 0

    def _log(self, world_id: str, by: str, by_name: str, what: str) -> None:
        if self._protocol is None:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    kind=EntryKind.CUSTOM,
                    what=what,
                    scope=Scope(world=world_id),
                )
            ]
        )

    def _save(self, shop: Shop) -> Shop:
        shop.touch()
        return self._store.shops.upsert(shop)


def _list_price(offer: Offer | None) -> int:
    return ceil(float(offer.data.get("value") or 0)) if offer is not None else 0


__all__ = ["ShopError", "ShopService", "Ware", "bought_back", "priced"]
