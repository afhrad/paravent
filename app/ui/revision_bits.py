"""What the item, spell and background builders show of a revision
(app/services/design_revisions.py): on the copy, what it changes and a way to the
design as it is; on the design, that a revision waits."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from htpy import Element, a, div, p

from app.ui.context import Page


def revision_status(
    page: Page, hint: str, original_url: str, changes: Sequence[str], labels: Mapping[str, str]
) -> Element:
    """The copy's status: what it is, the fields it changes, and the design as it is."""
    t = page.t
    return div(class_="ib-revision")[
        p(class_="pill accent")[hint],
        p(class_="muted")[
            t("itembuilder.revision_changes", fields=", ".join(labels.get(k, k) for k in changes))
            if changes
            else t("itembuilder.revision_same")
        ],
        a(href=original_url)[t("itembuilder.revision_original")],
    ]


def revision_notice(page: Page, text: str, revision_url: str) -> Element:
    """On the design: Claude proposes changes, with a way to them."""
    return p(class_="notice")[text, " ", a(href=revision_url)[page.t("itembuilder.revision_see")]]


__all__ = ["revision_notice", "revision_status"]
