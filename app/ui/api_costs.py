"""What the Claude API cost, as the pages say it (app/services/api_costs.py): always
"about", since Anthropic's bill is the authority and Paravent cannot read the credit
left."""

from __future__ import annotations

from collections.abc import Mapping

from htpy import Element, b, div, h3, li, p, span, ul

from app.services.api_costs import Spend
from app.ui.context import Page


def usd(page: Page, amount: float) -> str:
    """A sum in dollars, cents shown: "$1.20", "1,20 $"; under a cent "< $0.01"."""
    if 0 < amount < 0.005:
        return page.t("costs.under_cent", usd=page.t("costs.usd", amount=_cents(page, 0.01)))
    return page.t("costs.usd", amount=_cents(page, amount))


def _cents(page: Page, amount: float) -> str:
    text = f"{amount:,.2f}"
    if page.lang == "de":
        text = text.replace(",", " ").replace(".", ",").replace(" ", ".")
    return text


def tokens(page: Page, count: int) -> str:
    """A count of tokens, short: "820", "12.4k", "1.3M"."""
    if count >= 1_000_000:
        return page.t("costs.millions", n=page.u.number(round(count / 1_000_000, 1)))
    if count >= 10_000:
        return page.t("costs.thousands", n=page.u.number(round(count / 1000)))
    if count >= 1000:
        return page.t("costs.thousands", n=page.u.number(round(count / 1000, 1)))
    return page.u.number(count)


def spend_text(page: Page, spend: Spend) -> str:
    """A run's or a month's tokens and their price: "1.3M tokens in, 210k out · about
    $9.40"."""
    text = page.t(
        "costs.spend",
        tokens_in=tokens(page, spend.tokens_in),
        tokens_out=tokens(page, spend.output),
        usd=usd(page, spend.usd),
    )
    if spend.unpriced:
        text += " · " + page.t("costs.unpriced", tokens=tokens(page, spend.unpriced))
    return text


def run_cost(page: Page, spend: Spend) -> str:
    """What a run cost, for its progress and its toast; empty when it asked nothing."""
    return spend_text(page, spend) if spend.requests else ""


def costs_box(page: Page, months: Mapping[str, Spend]) -> Element | None:
    """The months' sums, newest first (at most three), and that the bill decides."""
    if not months:
        return None
    t = page.t
    shown = list(months.items())[:3]
    return div(class_="tr-costs")[
        h3[t("costs.heading")],
        ul(class_="tr-costs-list")[
            [li[b[key], " ", span(class_="muted")[spend_text(page, spend)]] for key, spend in shown]
        ],
        p(class_="field-hint")[t("costs.hint")],
    ]
