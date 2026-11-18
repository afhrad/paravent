"""Title images on the page (app/services/title_images.py): drawn at the head of a place,
encounter, quest or faction, and the panel that takes one, dropped or chosen."""

from __future__ import annotations

from urllib.parse import parse_qs, urlencode, urlsplit

from htpy import Element, Node, button, div, form, h2, img, label, p, section, span
from htpy import input as input_el

from app.ui.context import Page
from app.ui.icons import icon
from app.ui.library_links import cover, media_url

TITLE_URL = "/gm/title-image"


def title_image(found: tuple[str, str] | None, alt: str) -> Node:
    """The title image at the head of a page, as a world's or adventure's cover."""
    return cover(found[0], found[1], alt) if found else None


def title_thumb(found: tuple[str, str] | None, alt: str = "") -> Node:
    """A small square of the title image, for a list row or a card; None without one."""
    if not found:
        return None
    return img(class_="title-thumb", src=media_url(found[0], found[1]), alt=alt, loading="lazy")


def title_image_panel(
    page: Page, kind: str, ident: str, found: tuple[str, str] | None, *, heading: bool = True
) -> Element:
    """Drop or choose a picture to make it the title image, or take it away. The page
    comes back to here (its own anchor: a page lists several quests), with what went
    wrong (``?title_error=``). Without ``heading`` it sits inside another panel."""
    t = page.t
    parts = urlsplit(page.path)
    query = parse_qs(parts.query)
    problem = (query.get("title_error") or [""])[0]
    anchor = f"title-{kind}-{ident}"
    # Back to the same page as it was asked for (a quest list keeps its world), without
    # the last upload's complaint.
    kept = urlencode([(k, v) for k, vs in query.items() if k != "title_error" for v in vs])
    back = parts.path + (f"?{kept}" if kept else "") + f"#{anchor}"
    action = f"{TITLE_URL}/{kind}/{ident}"
    field_id = f"f-title-{kind}-{ident}"
    return section(class_="panel title-panel" if heading else "title-panel", id=anchor)[
        div(class_="panel-head")[h2[t("title_image.heading")]] if heading else None,
        div(class_="title-panel-body")[
            title_thumb(found),
            div(class_="title-panel-forms")[
                p(class_="alert", role="alert")[t(f"title_image.error.{problem}")]
                if problem in ("none", "image", "owner")
                else None,
                form(
                    method="post",
                    action=action,
                    enctype="multipart/form-data",
                    class_="stack-form title-upload",
                )[
                    input_el(type="hidden", name="next", value=back),
                    label(for_=field_id, class_="tok-drop title-drop")[
                        icon("upload"),
                        span[t("title_image.drop")],
                        input_el(id=field_id, type="file", name="image", accept="image/*"),
                    ],
                    button(type="submit", class_="btn btn-sm title-plain")[
                        icon("upload", "sm"), span[t("tokens.upload")]
                    ],
                ],
                form(method="post", action=action)[
                    input_el(type="hidden", name="next", value=back),
                    input_el(type="hidden", name="do", value="remove"),
                    button(type="submit", class_="btn btn-sm btn-ghost")[t("title_image.remove")],
                ]
                if found
                else None,
            ],
        ],
    ]


__all__ = ["TITLE_URL", "title_image", "title_image_panel", "title_thumb"]
