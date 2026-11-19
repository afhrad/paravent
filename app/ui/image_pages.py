"""The image dialog (documentation/ADVENTURES.md §5.5): drop or choose a picture, or pick
one the world already has, then frame it and give it effects.

The picture itself is never cut. What is kept is a reference with the frame and effects
beside the path (``media/x.jpg|crop:0.1000_0.0500_0.8000_0.4500 torn``, see
``app/services/image_effects.py``); Paravent renders it from the original, and the frame
can be moved again later. Used for covers (worlds, adventures, chapters), title images
(places, encounters, quests, factions) and pictures in a chapter's text. Without
JavaScript the form still uploads the picture, uncut.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from htpy import (
    Element,
    Node,
    b,
    button,
    dialog,
    div,
    fieldset,
    form,
    h3,
    img,
    label,
    legend,
    p,
    script,
    small,
    span,
    ul,
)
from htpy import input as input_el

from app.services import image_effects
from app.services.media_uses import Use
from app.ui.context import Page
from app.ui.forms import hidden
from app.ui.icons import icon
from app.ui.library_links import MEDIA_UPLOAD_URL, MEDIA_URL, media_url

IMAGES_URL = "/gm/images"
SCRIPT = "/static/js/image_editor.js"
# The frame's shapes: free, or width to height. A title image's banner comes first.
ASPECTS = ("free", "10:3", "16:9", "3:2", "4:3", "1:1", "3:4")
# Effects a picture may wear in the dialog (a cover wears the banner by itself).
DIALOG_EFFECTS = tuple(e for e in image_effects.EFFECTS if e != "banner")


def image_editor(
    page: Page,
    *,
    record_id: str,
    action: str,
    reference: str | None,
    next_url: str,
    file_field: str = "file",
    aspect: str = "10:3",
    extra: Node = None,
) -> Element:
    """A picture's drop zone, with the dialog to pick, frame and dress it.

    ``record_id`` is the record whose folder the reference is written from (a cover's
    own record, a title image's owner); ``reference`` is what is set now, if anything;
    ``action`` receives ``path`` (a picture of the world), or the upload in
    ``file_field``, with ``crop`` and ``effects``."""
    t = page.t
    path, effects = image_effects.split(reference)
    frame = image_effects.frame_of(effects)
    worn = [e for e in effects if not e.startswith(image_effects.CROP)]
    field_id = f"img-file-{record_id}-{abs(hash(action)) % 10_000}"
    return div(
        class_="img-editor",
        data_image_editor="1",
        data_browse=f"{IMAGES_URL}/{record_id}",
        data_aspect=aspect,
        data_src=media_url(record_id, path) if path else None,
        data_frame=",".join(f"{v:.4f}" for v in frame) if frame else None,
    )[
        # What is set now, as it shows (framed and dressed).
        img(class_="img-current", src=media_url(record_id, reference), alt="") if path else None,
        form(method="post", action=action, enctype="multipart/form-data", class_="img-form")[
            hidden("next", next_url),
            input_el(type="hidden", name="path", value=path, class_="img-path"),
            input_el(
                type="hidden",
                name="crop",
                value=image_effects.frame_word(*frame) if frame else "",
                class_="img-crop",
            ),
            extra,
            label(for_=field_id, class_="tok-drop img-drop")[
                icon("upload"),
                span[t("images.drop")],
                input_el(id=field_id, type="file", name=file_field, accept="image/*"),
            ],
            div(class_="img-actions")[
                button(type="button", class_="btn btn-sm img-browse")[
                    icon("images", "sm"), span[t("images.browse")]
                ],
                button(type="button", class_="btn btn-sm img-adjust")[
                    icon("crop", "sm"), span[t("images.adjust")]
                ]
                if path
                else None,
                button(type="submit", class_="btn btn-sm img-plain")[
                    icon("upload", "sm"), span[t("tokens.upload")]
                ],
            ],
            image_dialog(page, worn),
        ],
        script(src=SCRIPT, defer=True),
    ]


def image_insert(page: Page, *, record_id: str, area_id: str) -> Element:
    """The dialog a Markdown editor's Image button opens (insert mode): a picture of the
    world, or one dropped or chosen there, framed and written at the cursor as
    ``![[path|crop:… effects]]``; on a picture already written, the same to reframe it.
    Kept outside the editor's form, so its fields are not saved with the text."""
    return div(
        class_="img-editor img-insert",
        data_image_editor="1",
        data_mode="insert",
        data_for=area_id,
        data_aspect="free",
        data_browse=f"{IMAGES_URL}/{record_id}",
        data_upload=f"{MEDIA_UPLOAD_URL}/{record_id}/inline",
        data_media=f"{MEDIA_URL}/{record_id}/",
    )[image_dialog(page, upload=True), script(src=SCRIPT, defer=True)]


def image_dialog(page: Page, worn: Sequence[str] = (), *, upload: bool = False) -> Element:
    """The dialog itself: the world's pictures to pick from, then the frame and effects.
    With ``upload`` it carries its own drop zone (insert mode has no form around it).
    ``static/js/image_editor.js`` drives it."""
    t = page.t
    return dialog(class_="img-dialog", aria_label=t("images.dialog"))[
        div(class_="img-dialog-head")[
            b[t("images.dialog")],
            button(
                type="button",
                class_="btn btn-sm btn-ghost img-cancel",
                aria_label=t("images.close"),
            )[icon("x", "sm")],
        ],
        label(class_="tok-drop img-drop img-upload")[
            icon("upload"),
            span[t("images.drop")],
            input_el(type="file", accept="image/*"),
        ]
        if upload
        else None,
        div(class_="img-browser-slot"),
        div(class_="img-frame-step", hidden=True)[
            div(class_="chips img-aspects", role="group", aria_label=t("images.aspect"))[
                [
                    button(type="button", class_="chip", data_aspect=a)[
                        t("images.free") if a == "free" else a
                    ]
                    for a in ASPECTS
                ]
            ],
            div(class_="img-stage")[
                img(class_="img-src", alt=""),
                div(class_="img-frame")[
                    [span(class_="img-handle", data_corner=c) for c in ("nw", "ne", "sw", "se")]
                ],
            ],
            p(class_="field-hint")[t("images.frame_hint")],
            label(class_="img-tags-field")[
                span[t("images.tags")],
                input_el(
                    type="text",
                    name="tags",
                    class_="img-tags",
                    placeholder=t("images.tags_placeholder"),
                    autocomplete="off",
                ),
            ],
            fieldset(class_="img-effects")[
                legend[t("adventures.effects")],
                [
                    label(class_="check-inline", title=t(f"adventures.effect_hint.{name}"))[
                        input_el(type="checkbox", name="effects", value=name, checked=name in worn),
                        t(f"adventures.effect.{name}"),
                    ]
                    for name in DIALOG_EFFECTS
                ],
            ],
        ],
        div(class_="img-dialog-foot")[
            button(type="button", class_="btn btn-sm img-back", hidden=True)[
                icon("images", "sm"), span[t("images.browse")]
            ],
            button(type="button", class_="btn btn-sm btn-primary img-save", hidden=True)[
                icon("check", "sm"), span[t("images.save")]
            ],
        ],
    ]


@dataclass(frozen=True)
class Picture:
    """A picture in the browser: its path as the record writes it, its address, file
    name, own tags and shape (``landscape``, ``portrait``, ``square`` or "")."""

    path: str
    src: str
    preview: str  # the same, shrunk for the browser's grid
    name: str
    tags: tuple[str, ...] = ()
    shape: str = ""
    uses: tuple[Use, ...] = ()  # where it is used (app/services/media_uses.py)


def _uses_data(page: Page, uses: Sequence[Use]) -> str:
    """A picture's uses for the browser's script: what each is and where it is edited."""
    t = page.t
    return json.dumps(
        [
            {
                "label": t(
                    "images.use",
                    owner=t(f"images.owner.{u.owner}"),
                    name=u.name,
                    kind=t(f"images.kind.{u.kind}"),
                ),
                "url": u.url,
            }
            for u in uses
        ],
        ensure_ascii=False,
    )


def image_browser(
    page: Page,
    groups: Sequence[tuple[str, Sequence[Picture]]],
    vocabulary: Sequence[tuple[str, int]] = (),
    *,
    tags_url: str = "",
    rename_url: str = "",
    delete_url: str = "",
    message: str | None = None,
) -> Element:
    """The world's pictures, grouped by where they live: a click picks one. A search
    over names, tags and shapes (every word must match), the world's tags as chips that
    filter (several together), and **Select**, which ticks pictures to tag or untag
    together (``tags_url``); with one ticked, where it is used, **Rename** (``rename_url``)
    and **Delete** while nothing uses it (``delete_url``). Each picture says how often it
    is used. ``message`` is why the last rename or delete was refused."""
    t = page.t
    if not groups:
        return p(class_="muted img-none")[t("images.none")]

    def words(picture: Picture) -> str:
        shape = t(f"images.shape.{picture.shape}") if picture.shape else ""
        return " ".join([picture.name.lower(), *picture.tags, shape.lower()])

    return div(
        class_="img-browser",
        data_tags_url=tags_url or None,
        data_rename_url=rename_url or None,
        data_delete_url=delete_url or None,
    )[
        p(class_="alert img-message", role="alert")[message] if message else None,
        div(class_="img-browser-tools")[
            input_el(
                type="search",
                class_="img-filter",
                placeholder=t("images.filter"),
                aria_label=t("images.filter"),
            ),
            button(type="button", class_="btn btn-sm img-select", aria_pressed="false")[
                icon("check", "sm"), span[t("images.select")]
            ]
            if tags_url
            else None,
        ],
        div(class_="chips img-tagstrip", role="group", aria_label=t("images.tags"))[
            [
                button(
                    type="button", class_="chip img-tagchip", data_tag=tag, aria_pressed="false"
                )[tag, small(class_="muted")[f" {count}"]]
                for tag, count in vocabulary
            ]
        ]
        if vocabulary
        else None,
        div(class_="img-bulk", hidden=True)[
            input_el(
                type="text",
                class_="img-bulk-tags",
                placeholder=t("images.tags_placeholder"),
                aria_label=t("images.tags"),
                autocomplete="off",
            ),
            button(type="button", class_="btn btn-sm img-bulk-add")[t("images.tag_add")],
            button(type="button", class_="btn btn-sm btn-ghost img-bulk-remove")[
                t("images.tag_remove")
            ],
            span(class_="muted img-bulk-n"),
        ],
        # One picture ticked: where it is used, a new name, and delete while unused.
        div(class_="img-one", hidden=True)[
            p(class_="img-one-head")[b(class_="img-one-name"), span(class_="muted img-one-count")],
            ul(class_="img-uses"),
            p(class_="field-hint img-unused")[t("images.unused_hint")],
            div(class_="img-one-actions")[
                input_el(
                    type="text",
                    class_="img-rename-name",
                    aria_label=t("images.rename_to"),
                    placeholder=t("images.rename_to"),
                    autocomplete="off",
                ),
                button(type="button", class_="btn btn-sm img-rename")[
                    icon("pencil", "sm"), span[t("images.rename")]
                ],
                button(
                    type="button",
                    class_="btn btn-sm btn-ghost btn-danger img-delete",
                    data_confirm=t("images.delete_confirm"),
                )[icon("trash-2", "sm"), span[t("images.delete")]],
            ],
            p(class_="field-hint img-in-use")[t("images.in_use_hint")],
        ]
        if rename_url
        else None,
        [
            div(class_="img-group")[
                h3(class_="eyebrow")[label_],
                div(class_="img-grid")[
                    [
                        button(
                            type="button",
                            class_="img-pick",
                            data_path=pic.path,
                            data_src=pic.src,
                            data_name=pic.name.lower(),
                            data_words=words(pic),
                            data_tags=", ".join(pic.tags),
                            data_uses=_uses_data(page, pic.uses),
                            title=pic.name,
                        )[
                            img(src=pic.preview, alt="", loading="lazy"),
                            span(
                                class_="img-pick-used" + ("" if pic.uses else " is-unused"),
                                title=t.plural("images.used_n", len(pic.uses))
                                if pic.uses
                                else t("images.unused"),
                            )[f"{len(pic.uses)}×" if pic.uses else t("images.unused")],
                            span(class_="img-pick-name")[pic.name],
                            span(class_="img-pick-tags")[
                                [span(class_="tag")[tag] for tag in pic.tags]
                            ]
                            if pic.tags
                            else None,
                        ]
                        for pic in pictures
                    ]
                ],
            ]
            for label_, pictures in groups
        ],
    ]


__all__ = [
    "ASPECTS",
    "IMAGES_URL",
    "Picture",
    "image_browser",
    "image_dialog",
    "image_editor",
    "image_insert",
]
