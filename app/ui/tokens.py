"""Drawing a token (documentation/TOKENS.md): the picture, or the kind's default disc."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from htpy import Element, button, div, form, h2, img, label, p, section, span
from htpy import input as input_el

from app.services.tokens import TOKEN_URL, Token
from app.ui.context import Page
from app.ui.icons import icon

# The icon of each kind's default disc; its colours are the stylesheet's (``.tok-<kind>``).
KIND_ICONS = {
    "creature": "skull",
    "npc": "circle-user",
    "character": "shield",
    "item": "gem",
    "place": "map",
    "encounter": "swords",
    "treasure": "coins",
    "spell": "wand-sparkles",
    "vehicle": "footprints",
    "object": "vault",
}
SIZES = frozenset({"sm", "md", "lg", "xl"})


def token(tok: Token, size: str = "md", *, badge: str | None = None, title: str = "") -> Element:
    """A round token: the picture where there is one, else the kind's icon on its disc;
    ``badge`` a short mark on its edge (a combatant's number). Decorative unless
    ``title`` names it."""
    if size not in SIZES:
        raise ValueError(f"Unknown token size {size!r}")
    shape = "tok-img" if tok.src else "tok-disc"
    return span(
        class_=f"tok tok-{size} tok-{tok.kind} {shape}",
        title=title or None,
        aria_hidden=None if title else "true",
    )[
        img(src=tok.src, alt="", loading="lazy", decoding="async")
        if tok.src
        else icon(KIND_ICONS.get(tok.kind, "book-open")),
        span(class_="tok-badge")[badge] if badge else None,
    ]


def token_panel(page: Page, tok: Token, kind: str, ident: str) -> Element:
    """Upload an element's own token (cropped round), or remove it: its current token,
    a file field, and what went wrong with the last upload (``?token_error=``)."""
    t = page.t
    query = parse_qs(urlsplit(page.path).query)
    problem = (query.get("token_error") or [""])[0]
    back = page.path.split("?", 1)[0] + "#token"
    own = bool(tok.src and tok.src.startswith(TOKEN_URL))
    action = f"{TOKEN_URL}/{kind}/{ident}"
    return section(class_="panel token-panel", id="token", aria_labelledby="token-h")[
        div(class_="panel-head")[h2(id="token-h")[t("tokens.title")]],
        div(class_="token-panel-body")[
            token(tok, "xl"),
            div(class_="token-panel-forms")[
                p(class_="alert", role="alert")[t(f"tokens.error.{problem}")]
                if problem in ("none", "image")
                else None,
                form(
                    method="post", action=action, enctype="multipart/form-data", class_="stack-form"
                )[
                    input_el(type="hidden", name="next", value=back),
                    label(for_=f"f-token-{ident}", class_="field-label")[t("tokens.choose")],
                    input_el(id=f"f-token-{ident}", type="file", name="token", accept="image/*"),
                    p(class_="field-hint")[t("tokens.hint")],
                    button(type="submit", class_="btn btn-sm")[
                        icon("upload", "sm"), span[t("tokens.upload")]
                    ],
                ],
                form(method="post", action=action)[
                    input_el(type="hidden", name="next", value=back),
                    input_el(type="hidden", name="do", value="remove"),
                    button(type="submit", class_="btn btn-sm btn-ghost")[t("tokens.remove")],
                ]
                if own
                else None,
            ],
        ],
    ]


__all__ = ["KIND_ICONS", "token", "token_panel"]
