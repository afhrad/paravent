"""Previews of the campaign's own objects: what a link to an NPC, place, encounter,
treasure, chapter or character shows on hover or tap (DESIGN.md §7). A glance, not the
page: "Open" is the way there, as in a reference's preview."""

from __future__ import annotations

import re
from collections.abc import Sequence

from htpy import Element, Node, a, b, div, p, span

from app.services.text import strip_tags
from app.services.tokens import Token
from app.ui.context import Page
from app.ui.icons import icon
from app.ui.tokens import token as draw_token

EXCERPT_LIMIT = 280
# Markdown that means nothing in one line of plain text: callout heads, block quotes,
# headings, emphasis, and cards alone on a line (a preview does not unfold them).
_CALLOUT = re.compile(r"^\s*>?\s*\[![^\]]*\][-+]?.*$", re.MULTILINE)
_LINE_MARK = re.compile(r"^\s*(?:>\s*|#{1,6}\s+|[-*+]\s+)+", re.MULTILINE)
_LONE_TAG = re.compile(r"^\s*\{@[^}]*\}\s*$", re.MULTILINE)
_EMPHASIS = re.compile(r"[*_`]{1,3}")


def excerpt(text: str) -> str:
    """The first words of Paravent Markdown as plain text."""
    plain = _LONE_TAG.sub("", _CALLOUT.sub("", text))
    plain = _EMPHASIS.sub("", _LINE_MARK.sub("", strip_tags(plain)))
    plain = " ".join(plain.split())
    if len(plain) <= EXCERPT_LIMIT:
        return plain
    return plain[:EXCERPT_LIMIT].rsplit(" ", 1)[0] + " …"


def library_card(
    page: Page,
    *,
    kind: str,
    glyph: str,
    name: str,
    url: str,
    meta: Sequence[str] = (),
    pills: Node = None,
    text: str = "",
    token: Token | None = None,
) -> Element:
    """The card of one object, headed by its round token where it has a kind of one."""
    t = page.t
    return div(class_=f"tip-card tip-lib tip-{kind}", data_close=t("tip.close"))[
        div(class_="tip-head")[
            draw_token(token, "sm") if token else icon(glyph, "sm"),
            b(class_="tip-name")[name],
        ],
        div(class_="tip-body")[
            div(class_="tip-pills")[pills] if pills else None,
            [p(class_="tip-meta")[line] for line in meta if line],
            p(class_="tip-text")[excerpt(text)] if excerpt(text) else None,
        ],
        div(class_="tip-foot")[
            a(href=url, class_="btn btn-sm btn-primary tip-open")[
                icon("book-open"), span[t("tip.open")]
            ]
        ],
    ]


__all__ = ["excerpt", "library_card"]
