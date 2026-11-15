"""A shop during play (app/services/shop.py): the gamemaster's panel on the party view,
and the shop a character buys and sells at."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import quote, urlencode

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    details,
    div,
    form,
    h2,
    label,
    li,
    option,
    p,
    section,
    select,
    small,
    span,
    summary,
    ul,
)
from htpy import input as input_el

from app.models.gamestate import Shop, ShopItem
from app.models.pc import Currency, InventoryItem
from app.services.activities import RARITIES
from app.services.chargen.catalogue import SHELVES, shelf
from app.services.coins import VALUES, worth
from app.services.shop import Ware
from app.ui.context import Page
from app.ui.forms import alert, hidden, notice
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.names import name_text
from app.ui.tags import ref_url

SHOP_URL = "/gm/shop"


def shop_url(character_id: str) -> str:
    return f"/characters/{character_id}/shop"


def coin_text(page: Page, copper: int) -> str:
    """Copper as gold, silver and copper in the reader's coin names ("4 GM 5 SM")."""
    gp, rest = divmod(max(0, copper), 100)
    sp, cp = divmod(rest, 10)
    unit = {k: page.t(f"treasure.coin.{k}") for k in ("gp", "sp", "cp")}
    parts = [f"{n} {unit[k]}" for n, k in ((gp, "gp"), (sp, "sp"), (cp, "cp")) if n]
    return " ".join(parts) or f"0 {unit['cp']}"


def purse_text(page: Page, purse: Currency) -> str:
    """The coins a purse holds, largest first ("2 pp 15 gp 3 cp")."""
    parts = [
        f"{getattr(purse, d)} {page.t(f'treasure.coin.{d}')}"
        for d in reversed(VALUES)
        if getattr(purse, d)
    ]
    return " ".join(parts) or f"0 {page.t('treasure.coin.gp')}"


def shop_panel(
    page: Page,
    world_id: str,
    shop: Shop,
    members: Sequence[tuple[str, str]],
    *,
    treasures: Sequence[tuple[str, str]] = (),
) -> Element:
    """Open, change or close the world's shop, and stock its magic items: by name,
    rolled by rarity, or a treasure's items (``treasures`` are ``(id, name)``)."""
    t = page.t
    base = f"{SHOP_URL}/{quote(world_id)}"
    htmx = {"hx_target": "#party-shop", "hx_swap": "outerHTML"}
    settings = form(
        method="post", action=f"{base}/open", hx_post=f"{base}/open", class_="shop-form", **htmx
    )[
        label(class_="party-clock-label")[
            span[t("shop.name")],
            input_el(
                type="text",
                name="name",
                value=shop.name,
                maxlength="80",
                placeholder=t("shop.name_placeholder"),
            ),
        ],
        label(class_="party-clock-label")[
            span[t("shop.markup")],
            input_el(
                type="number",
                name="markup",
                min="10",
                max="1000",
                value=str(shop.markup),
                inputmode="numeric",
                class_="mono party-clock-amount",
            ),
            span[" %"],
        ],
        label(class_="party-clock-label")[
            span[t("shop.buys")],
            input_el(
                type="number",
                name="buys",
                min="0",
                max="100",
                value=str(shop.buys),
                inputmode="numeric",
                class_="mono party-clock-amount",
            ),
            span[" %"],
        ],
        div(class_="shop-who")[
            small(class_="muted")[t("shop.who")],
            [
                label(class_="party-clock-label")[
                    input_el(
                        type="checkbox",
                        name="characters",
                        value=cid,
                        checked=cid in shop.characters,
                    ),
                    span[name],
                ]
                for cid, name in members
            ],
        ]
        if members
        else None,
        button(type="submit", class_="btn btn-sm btn-primary")[
            icon("coins", "sm"), span[t("shop.change" if shop.open else "shop.open")]
        ],
    ]
    return div(id="party-shop", class_="panel party-shop")[
        div(class_="party-clock-head")[
            icon("coins"),
            b[t("shop.heading")],
            span(class_="pill accent")[shop.name or t("shop.open_now")]
            if shop.open
            else span(class_="muted")[t("shop.closed")],
            form(
                method="post",
                action=f"{base}/close",
                hx_post=f"{base}/close",
                class_="inline-form",
                **htmx,
            )[button(type="submit", class_="btn btn-sm")[icon("lock", "sm"), span[t("shop.close")]]]
            if shop.open
            else None,
        ],
        details(class_="party-clock-more")[
            summary[t("shop.settings" if shop.open else "shop.open")], settings
        ],
        details(class_="party-clock-more")[
            summary[t.plural("shop.magic_n", len(shop.stock), n=len(shop.stock))],
            _stock_forms(page, base, htmx, shop, treasures),
        ],
    ]


def _stock_forms(
    page: Page,
    base: str,
    htmx: dict[str, str],
    shop: Shop,
    treasures: Sequence[tuple[str, str]],
) -> Node:
    t = page.t

    def post(op: str, *content: Node) -> Element:
        return form(
            method="post",
            action=f"{base}/{op}",
            hx_post=f"{base}/{op}",
            class_="inline-form shop-stock-form",
            **htmx,
        )[content]

    def count_field(value: str = "") -> Node:
        return input_el(
            type="number",
            name="count",
            min="1",
            max="999",
            value=value,
            placeholder="∞",
            inputmode="numeric",
            class_="mono party-clock-amount",
            aria_label=t("shop.count"),
        )

    rows = [
        li(class_="shop-row")[
            span[name_text(page, "item", item.ref.partition("|")[0])],
            post(
                "price",
                hidden("index", str(n)),
                input_el(
                    type="number",
                    name="price",
                    min="0",
                    value=str(item.price // 100),
                    inputmode="numeric",
                    class_="mono party-clock-amount",
                    aria_label=t("shop.price_gp"),
                ),
                count_field("" if item.count is None else str(item.count)),
                button(type="submit", class_="btn btn-sm")[t("shop.change")],
            ),
            post(
                "remove",
                hidden("index", str(n)),
                button(
                    type="submit",
                    class_="btn btn-sm btn-ghost",
                    title=t("shop.unstock"),
                    aria_label=t("shop.unstock"),
                )[icon("x", "sm")],
            ),
        ]
        for n, item in enumerate(shop.stock)
    ]
    return div(class_="shop-stock")[
        ul(class_="shop-list")[rows] if rows else p(class_="muted")[t("shop.no_magic")],
        post(
            "add",
            input_el(
                type="text",
                name="item",
                required=True,
                maxlength="120",
                placeholder=t("shop.item_name"),
                aria_label=t("shop.item_name"),
            ),
            input_el(
                type="number",
                name="price",
                min="0",
                placeholder=t("shop.price_gp"),
                inputmode="numeric",
                class_="mono party-clock-amount",
                aria_label=t("shop.price_gp"),
            ),
            count_field(),
            button(type="submit", class_="btn btn-sm")[t("shop.add")],
        ),
        post(
            "roll",
            select(name="rarity", aria_label=t("shop.rarity"))[
                [option(value=r)[t(f"downtime.rarity.{r}")] for r in RARITIES]
            ],
            input_el(
                type="number",
                name="count",
                min="1",
                max="20",
                value="3",
                inputmode="numeric",
                class_="mono party-clock-amount",
                aria_label=t("shop.count"),
            ),
            button(type="submit", class_="btn btn-sm")[icon("dices", "sm"), span[t("shop.roll")]],
        ),
        post(
            "treasure",
            select(name="treasure", aria_label=t("shop.from_treasure"))[
                [option(value=tid)[tname] for tid, tname in treasures]
            ],
            button(type="submit", class_="btn btn-sm")[t("shop.from_treasure")],
        )
        if treasures
        else None,
        p(class_="muted")[t("shop.magic_hint")],
    ]


def shop_page(
    page: Page,
    *,
    character_id: str,
    character_name: str,
    world_id: str | None,
    shop: Shop,
    purse: Currency,
    wares: Sequence[Ware],
    sellable: Sequence[tuple[int, InventoryItem, int]],
    magic: Sequence[tuple[ShopItem, int]] = (),
    shelf_shown: str,
    query: str,
    error: str | None = None,
    message: str | None = None,
) -> Element:
    """The shelves at the shop's prices, a purse that runs down, and what the character
    could sell."""
    t = page.t
    url = shop_url(character_id)
    money = worth(purse)
    shown = [
        w
        for w in wares
        if (not shelf_shown or shelf(w.offer) == shelf_shown)
        and (
            not query
            or query.lower() in w.offer.name.lower()
            or query.lower() in str(name_text(page, ("item", "baseitem"), w.offer.name)).lower()
        )
    ]

    def chip(key: str, text: str) -> Element:
        params = {k: v for k, v in (("shelf", key), ("q", query)) if v}
        return a(
            href=f"{url}?{urlencode(params)}" if params else url,
            class_="chip" + (" is-on" if key == shelf_shown else ""),
        )[text]

    return app_shell(
        page,
        t("shop.title", name=shop.name or t("shop.heading")),
        div(class_="tr-page shop-page")[
            a(href=f"/characters/{character_id}", class_="backlink")[
                icon("arrow-left"), span[character_name]
            ],
            page_head(
                t("shop.eyebrow"),
                shop.name or t("shop.heading"),
                t("shop.lede", markup=shop.markup, buys=shop.buys),
            ),
            alert(error),
            notice(message),
            p(class_="shop-purse")[
                icon("coins", "sm"),
                b[t("shop.purse")],
                " ",
                span(class_="mono")[purse_text(page, purse)],
            ],
            section(class_="panel shop-magic")[
                div(class_="panel-head")[h2[t("shop.magic")]],
                ul(class_="shop-list")[
                    [_magic(page, url, item, cost, money, world_id) for item, cost in magic]
                ],
            ]
            if magic
            else None,
            section(class_="panel shop-wares")[
                div(class_="panel-head")[h2[t("shop.wares")]],
                form(method="get", action=url, class_="shop-search")[
                    hidden("shelf", shelf_shown) if shelf_shown else None,
                    input_el(
                        type="search",
                        name="q",
                        value=query,
                        placeholder=t("shop.search"),
                        aria_label=t("shop.search"),
                    ),
                    button(type="submit", class_="btn btn-sm")[icon("search", "sm")],
                ],
                div(class_="chips shop-shelves")[
                    chip("", t("chargen.shop.all")),
                    [chip(k, t(f"chargen.shop.kind.{k}")) for k in SHELVES],
                ],
                ul(class_="shop-list")[[_ware(page, url, w, money, world_id) for w in shown]]
                if shown
                else p(class_="muted")[t("shop.nothing")],
            ],
            section(class_="panel shop-sell")[
                div(class_="panel-head")[h2[t("shop.sell_heading")]],
                ul(class_="shop-list")[
                    [_sellable(page, url, n, item, price) for n, item, price in sellable]
                ]
                if sellable
                else p(class_="muted")[t("shop.nothing_to_sell")],
            ],
        ],
    )


def _ware(page: Page, url: str, ware: Ware, money: int, world_id: str | None) -> Element:
    t = page.t
    offer = ware.offer
    name = name_text(page, ("item", "baseitem"), offer.name)
    return li(class_="shop-row")[
        a(href=ref_url(ware.category, offer.name, offer.source, world_id), class_="ref")[name],
        small(class_="muted")[offer.source],
        span(class_="mono shop-price")[coin_text(page, ware.price)],
        form(method="post", action=url, class_="inline-form shop-trade")[
            hidden("op", "buy"),
            hidden("item", offer.reference),
            input_el(
                type="number",
                name="count",
                min="1",
                max="99",
                value="1",
                inputmode="numeric",
                class_="mono party-clock-amount",
                aria_label=t("shop.count"),
            ),
            button(
                type="submit",
                class_="btn btn-sm",
                disabled=ware.price > money,
                title=t("chargen.shop.buy", name=name),
            )[t("shop.buy")],
        ],
    ]


def _magic(
    page: Page, url: str, item: ShopItem, cost: int, money: int, world_id: str | None
) -> Element:
    t = page.t
    item_name, _, source = item.ref.partition("|")
    name = name_text(page, "item", item_name)
    return li(class_="shop-row")[
        a(href=ref_url("item", item_name, source, world_id), class_="ref")[name],
        small(class_="muted")[
            source, f" · {t('shop.left', n=item.count)}" if item.count is not None else ""
        ],
        span(class_="mono shop-price")[coin_text(page, cost)],
        form(method="post", action=url, class_="inline-form shop-trade")[
            hidden("op", "buy_magic"),
            hidden("item", item.ref),
            button(
                type="submit",
                class_="btn btn-sm",
                disabled=cost > money,
                title=t("chargen.shop.buy", name=name),
            )[t("shop.buy")],
        ],
    ]


def _sellable(page: Page, url: str, index: int, item: InventoryItem, price: int) -> Element:
    t = page.t
    name = name_text(page, ("item", "baseitem"), item.item.name)
    return li(class_="shop-row")[
        span[name, small(class_="muted")[f" ×{item.quantity}"] if item.quantity > 1 else None],
        span(class_="mono shop-price")[t("shop.pays", coins=coin_text(page, price))],
        form(method="post", action=url, class_="inline-form shop-trade")[
            hidden("op", "sell"),
            hidden("index", str(index)),
            input_el(
                type="number",
                name="count",
                min="1",
                max=str(item.quantity),
                value="1",
                inputmode="numeric",
                class_="mono party-clock-amount",
                aria_label=t("shop.count"),
            ),
            button(type="submit", class_="btn btn-sm")[t("shop.sell")],
        ],
    ]


def sheet_shop_link(page: Page, character_id: str, name: str) -> Node:
    """The sheet's way into an open shop (``name`` "" for an unnamed one)."""
    return a(href=shop_url(character_id), class_="btn btn-sm is-on")[
        icon("coins", "sm"), span[name or page.t("shop.heading")]
    ]


__all__ = [
    "SHOP_URL",
    "coin_text",
    "purse_text",
    "sheet_shop_link",
    "shop_page",
    "shop_panel",
    "shop_url",
]
