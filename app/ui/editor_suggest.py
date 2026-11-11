"""The Markdown editors' drop-down before a name is typed: which kind (static/js/complete.js).

``{@`` lists what a tag may name — a reference (spell, creature, item…) or one of the
campaign's own (npc, encounter, treasure, character, chapter) — and ``> [!`` the
callouts. The commonest come first; the rest follow alphabetically, as many
as the prefix typed still matches.
"""

from __future__ import annotations

from htpy import Element, b, li, span, ul

from app.ui.context import Page
from app.ui.icons import icon
from app.ui.markdown import CALLOUT_ICON, callout_label
from app.ui.palette import CATEGORY_ICON
from app.ui.ref_pages import CATEGORY_KEY
from app.ui.tags import ENTITY_CATEGORY

# What the editor is completing, by the text before the cursor.
INLINE, CALLOUT = "inline", "callout"

# The commonest first; every other entity tag follows alphabetically.
_FIRST_REFERENCES = ("creature", "spell", "item", "condition")
_CAMPAIGN_TAGS = ("npc", "encounter", "treasure", "place", "character", "chapter", "sound")
# Paravent's own and the books' first, then Obsidian's, the commonest first.
CALLOUTS = (
    "read", "secret", "sidebar", "variant", "handout", "pullquote", "flowchart", "gallery",
    "info", "note", "abstract", "quote", "tip",
    "warning", "danger", "example", "question", "success", "failure", "todo", "bug",
)  # fmt: skip


def kinds(context: str) -> list[str]:
    """Every kind ``context`` may name, the commonest first."""
    if context == CALLOUT:
        return list(CALLOUTS)
    rest = sorted(k for k in ENTITY_CATEGORY if k not in _FIRST_REFERENCES)
    if context == INLINE:
        return [*_FIRST_REFERENCES, *_CAMPAIGN_TAGS, *rest]
    return []


def _label(page: Page, kind: str) -> str:
    t = page.t
    if kind in CALLOUTS:
        return t(callout_label(kind))
    if kind in ("npc", "encounter", "treasure", "character", "place", "sound"):
        return t(f"md.embed.{kind}")
    if kind == "chapter":
        return t("search.kind.chapter")
    category = ENTITY_CATEGORY[kind][0]
    return t(CATEGORY_KEY.get(category, f"glossary.category.{category}"))


def _icon(kind: str) -> tuple[str, str]:
    if kind in CALLOUTS:
        if kind == "secret":
            return "eye-off", ""
        return CALLOUT_ICON.get(kind, "info"), ""
    category = kind if kind in CATEGORY_ICON else ENTITY_CATEGORY.get(kind, ("",))[0]
    return CATEGORY_ICON.get(category, ("book-open", ""))


def kind_suggestions(page: Page, context: str, prefix: str) -> Element:
    """The kinds starting with ``prefix``; each completes as ``spell `` (then the name is
    asked for) or, for a callout, ``read]``."""
    wanted = prefix.strip().lower()
    rows: list[Element] = []
    for kind in kinds(context):
        if wanted and not kind.lower().startswith(wanted):
            continue
        glyph, tone = _icon(kind)
        rows.append(
            li(
                class_="ac-item",
                role="option",
                id=f"ac-kind-{kind}",
                data_complete=f"{kind}]" if context == CALLOUT else f"{kind} ",
                data_kind="1",
                aria_selected="true" if not rows else "false",
            )[
                span(class_=f"res-ico {tone}".strip())[icon(glyph)],
                span(class_="ac-text")[b[kind], span(class_="rsub")[_label(page, kind)]],
            ]
        )
    return ul(class_="ac-list", role="listbox", aria_label=page.t("search.results"))[
        rows or li(class_="ac-empty")[page.t("search.no_suggestions")]
    ]


__all__ = ["CALLOUT", "CALLOUTS", "INLINE", "kind_suggestions", "kinds"]
