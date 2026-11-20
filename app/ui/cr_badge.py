"""A creature's challenge rating as a badge: the measure a gamemaster looks for first when
judging an enemy, so it stands out the same way wherever a creature is shown — search and
pickers, the encounter builder, the tracker, statblocks, the creature builder."""

from __future__ import annotations

from htpy import Element, b, span

from app.ui.context import Page


def cr_badge(page: Page, cr: str, *, xp: int | None = None, size: str = "sm") -> Element:
    """ "CR" (the reader's abbreviation, "HG" in German) small above the rating, in a
    framed box of the danger colour; ``size`` ``sm`` beside a search result or in a list,
    ``inline`` on one line within a row of text (the tracker, the encounter builder, a
    preview), ``lg`` at the head of a statblock, where ``xp`` goes beneath."""
    t = page.t
    worth = t("rules.cr_xp", cr=cr, xp=t.number(xp)) if xp is not None else cr
    return span(class_=f"cr-badge cr-{size}", title=f"{t('ref.cr')} {worth}")[
        span(class_="cr-label")[t("rules.short.cr")],
        b(class_="cr-value")[cr],
        span(class_="cr-xp")[f"{t.number(xp)} {t('rules.short.xp')}"]
        if xp is not None and size == "lg"
        else None,
    ]


__all__ = ["cr_badge"]
