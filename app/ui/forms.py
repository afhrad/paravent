"""Form building blocks shared by every screen with a form.

Every control has a visible ``<label>``, and hints are tied to their control with
``aria-describedby`` so a screen reader announces them. Inputs are 44px tall and 16px
text (documentation/DESIGN.md §2; 16px also stops iOS zooming into a focused field).
"""

from __future__ import annotations

from collections.abc import Sequence

from htpy import Element, Node, VoidElement, datalist, div, label, option, p, select, span

# htpy's ``input_`` renders a bogus ``<input->`` element; the real one is ``input``,
# aliased so it does not shadow the builtin.
from htpy import input as input_el

from app.ui.context import Page
from app.ui.icons import icon


def alert(message: str | None) -> Node:
    """An error banner, announced immediately."""
    if not message:
        return None
    return div(class_="alert", role="alert")[icon("triangle-alert"), span[message]]


def notice(message: str | None) -> Node:
    """A success or status banner, announced politely."""
    if not message:
        return None
    return div(class_="notice", role="status")[icon("check"), span[message]]


def hidden(name: str, value: str) -> VoidElement:
    """A hidden input."""
    return input_el(type="hidden", name=name, value=value)


def _label(field_id: str, caption: str, optional_text: str | None) -> Element:
    return label(for_=field_id)[
        caption,
        [" ", span(class_="opt")[optional_text]] if optional_text else None,
    ]


def _hint(field_id: str, hint: str | None) -> tuple[Node, str | None]:
    if not hint:
        return None, None
    hint_id = f"{field_id}-hint"
    return p(class_="field-hint", id=hint_id)[hint], hint_id


def text_field(
    name: str,
    caption: str,
    *,
    kind: str = "text",
    value: str = "",
    autocomplete: str,
    required: bool = True,
    autofocus: bool = False,
    hint: str | None = None,
    optional_text: str | None = None,
) -> Element:
    """A labelled text or password input. Password fields never echo a value back."""
    field_id = f"f-{name}"
    hint_node, hint_id = _hint(field_id, hint)
    is_text = kind == "text"
    return div(class_="field")[
        _label(field_id, caption, optional_text),
        input_el(
            id=field_id,
            name=name,
            type=kind,
            value=value if kind != "password" else None,
            autocomplete=autocomplete,
            required=required,
            autofocus=autofocus,
            autocapitalize="none" if is_text else None,
            spellcheck="false" if is_text else None,
            aria_describedby=hint_id,
        ),
        hint_node,
    ]


def select_field(
    name: str,
    caption: str,
    choices: Sequence[tuple[str, str]],
    *,
    selected: str,
    hint: str | None = None,
) -> Element:
    """A labelled ``<select>``. ``choices`` are ``(value, label)`` pairs."""
    field_id = f"f-{name}"
    hint_node, hint_id = _hint(field_id, hint)
    return div(class_="field")[
        label(for_=field_id)[caption],
        select(id=field_id, name=name, aria_describedby=hint_id)[
            [option(value=value, selected=value == selected)[text] for value, text in choices]
        ],
        hint_node,
    ]


def scope_select(
    name: str,
    caption: str,
    options: Sequence[tuple[str, str, int]],
    *,
    selected: str,
    none_label: str | None = None,
    hint: str | None = None,
) -> Element:
    """Where something belongs: worlds, their adventures and chapters, indented.

    ``options`` are ``(value, label, depth)`` from :func:`app.services.scope.choices`;
    ``none_label`` adds a first choice of belonging nowhere.
    """
    field_id = f"f-{name}"
    hint_node, hint_id = _hint(field_id, hint)
    indent = "\u2003"  # an em space per level: a select cannot be styled per option
    return div(class_="field")[
        label(for_=field_id)[caption],
        select(id=field_id, name=name, aria_describedby=hint_id)[
            option(value="", selected=not selected)[none_label] if none_label else None,
            [
                option(value=value, selected=value == selected)[
                    f"{indent * depth}{'› ' if depth else ''}{text}"
                ]
                for value, text, depth in options
            ],
        ],
        hint_node,
    ]


def origin_fields(
    page: Page, race: str, culture: str, races: Sequence[str], cultures: Sequence[str]
) -> Element:
    """Race and culture, which find a naming style: free text, offering what exists."""
    t = page.t

    def line(name: str, caption: str, value: str, known: Sequence[str]) -> Element:
        return div(class_="field")[
            label(for_=f"f-{name}")[caption],
            input_el(
                id=f"f-{name}",
                name=name,
                type="text",
                value=value,
                list=f"known-{name}",
                autocomplete="off",
            ),
            datalist(id=f"known-{name}")[[option(value=v) for v in known]],
        ]

    return div(class_="naming-origin")[
        line("race", t("naming.race"), race, races),
        line("culture", t("naming.culture"), culture, cultures),
    ]


def checkbox_field(name: str, caption: str, *, checked: bool, hint: str | None = None) -> Element:
    """A labelled checkbox. Submits ``on`` when ticked and nothing when not."""
    field_id = f"f-{name}"
    hint_node, hint_id = _hint(field_id, hint)
    return div(class_="field field-check")[
        input_el(
            id=field_id, name=name, type="checkbox", checked=checked, aria_describedby=hint_id
        ),
        div[label(for_=field_id)[caption], hint_node],
    ]


__all__ = [
    "alert",
    "checkbox_field",
    "hidden",
    "notice",
    "origin_fields",
    "scope_select",
    "select_field",
    "text_field",
]
