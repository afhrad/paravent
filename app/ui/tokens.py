"""Drawing a token (documentation/TOKENS.md): the picture, or the kind's default disc."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from htpy import Element, button, dialog, div, form, h2, img, label, p, script, section, span
from htpy import input as input_el

from app.services.tokens import (
    FRAME_COLOURS,
    FRAME_INNER,
    FRAME_URL,
    PICTURE_KINDS,
    TOKEN_URL,
    Placement,
    Token,
)
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
SIZES = frozenset({"sm", "md", "lg", "xl", "xxl"})


def token(tok: Token, size: str = "md", *, badge: str | None = None, title: str = "") -> Element:
    """A round token: the picture where there is one, else the kind's icon on its disc;
    ``badge`` a short mark on its edge (a combatant's number). Decorative unless
    ``title`` names it."""
    if size not in SIZES:
        raise ValueError(f"Unknown token size {size!r}")
    shape = "tok-img" if tok.src else "tok-disc"
    kind = tok.kind if tok.kind in FRAME_COLOURS else "entry"
    return span(
        class_=f"tok tok-{size} tok-{tok.kind} {shape}",
        title=title or None,
        aria_hidden=None if title else "true",
    )[
        img(src=tok.src, alt="", loading="lazy", decoding="async")
        if tok.src
        else [
            # The kind's empty frame, its icon in the opening (documentation/TOKENS.md).
            icon(KIND_ICONS.get(tok.kind, "book-open")),
            img(class_="tok-ring", src=f"{FRAME_URL}/frame-{kind}.png", alt=""),
        ],
        span(class_="tok-badge")[badge] if badge else None,
    ]


def token_panel(
    page: Page,
    tok: Token,
    kind: str,
    ident: str,
    *,
    world_id: str | None = None,
    has_picture: bool = False,
) -> Element:
    """Upload an element's own token, or remove it: its current token, a place to drop or
    choose a picture, and the dialog that centres and zooms it in the kind's frame
    (``token_editor.js``; without it the picture is centred). Shows what went wrong with
    the last upload (``?token_error=``)."""
    t = page.t
    query = parse_qs(urlsplit(page.path).query)
    problem = (query.get("token_error") or [""])[0]
    back = page.path.split("?", 1)[0] + "#token"
    own = bool(tok.src and tok.src.startswith(TOKEN_URL))
    action = f"{TOKEN_URL}/{kind}/{ident}"
    frame = f"{FRAME_URL}/frame-{kind if kind in FRAME_COLOURS else 'entry'}.png"
    field_id = f"f-token-{ident}"
    # One picture for both: the element's own picture is placed in the frame (Adjust), and
    # an upload here becomes that picture.
    pictured = kind in PICTURE_KINDS
    placed = (
        page.token_placement(world_id, kind, ident)
        if pictured and has_picture and page.token_placement
        else Placement()
    )
    picture_url = (
        f"{TOKEN_URL}/{world_id or '-'}/{kind}/{ident}/picture"
        if pictured and has_picture
        else None
    )
    return section(
        class_="panel token-panel",
        id="token",
        aria_labelledby="token-h",
        data_token_editor="",
        data_inner=str(FRAME_INNER),
        data_picture=picture_url,
        data_x=str(placed.x),
        data_y=str(placed.y),
        data_zoom=str(placed.zoom),
    )[
        div(class_="panel-head")[h2(id="token-h")[t("tokens.title")]],
        div(class_="token-panel-body")[
            token(tok, "xl"),
            div(class_="token-panel-forms")[
                p(class_="alert", role="alert")[t(f"tokens.error.{problem}")]
                if problem in ("none", "image")
                else None,
                form(
                    method="post",
                    action=action,
                    enctype="multipart/form-data",
                    class_="stack-form tok-upload",
                )[
                    input_el(type="hidden", name="next", value=back),
                    input_el(type="hidden", name="x", value="0.5"),
                    input_el(type="hidden", name="y", value="0.5"),
                    input_el(type="hidden", name="zoom", value="1"),
                    label(for_=field_id, class_="tok-drop")[
                        icon("upload"),
                        span[t("tokens.drop")],
                        input_el(id=field_id, type="file", name="token", accept="image/*"),
                    ],
                    p(class_="field-hint")[t("tokens.hint_picture" if pictured else "tokens.hint")],
                    button(type="button", class_="btn btn-sm tok-adjust")[
                        icon("target", "sm"), span[t("tokens.adjust")]
                    ]
                    if picture_url
                    else None,
                    button(type="submit", class_="btn btn-sm tok-plain")[
                        icon("upload", "sm"), span[t("tokens.upload")]
                    ],
                ],
                form(method="post", action=action)[
                    input_el(type="hidden", name="next", value=back),
                    input_el(type="hidden", name="do", value="remove"),
                    button(type="submit", class_="btn btn-sm btn-ghost")[t("tokens.remove")],
                ]
                if own and not pictured
                else None,
            ],
        ],
        dialog(class_="tok-dialog", aria_labelledby=f"tok-dialog-h-{ident}")[
            h2(id=f"tok-dialog-h-{ident}")[t("tokens.place")],
            p(class_="field-hint")[t("tokens.place_hint")],
            div(class_="tok-stage")[
                # Cut to the frame's opening, as the saved token is.
                div(class_="tok-hole")[img(class_="tok-src", alt="", draggable="false")],
                img(class_="tok-frame-over", src=frame, alt="", draggable="false"),
            ],
            label(class_="tok-zoom-row")[
                span[t("tokens.zoom")],
                input_el(type="range", class_="tok-zoom", min="1", max="4", step="0.01", value="1"),
            ],
            div(class_="form-actions")[
                button(type="button", class_="btn btn-primary tok-save")[
                    icon("check", "sm"), span[t("tokens.save")]
                ],
                button(type="button", class_="btn btn-ghost tok-cancel")[t("common.cancel")],
            ],
        ],
        script(src="/static/js/token_editor.js", defer=True),
    ]


__all__ = ["KIND_ICONS", "token", "token_panel"]
