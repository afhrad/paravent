"""The sound desk's pages (documentation/SOUND.md): the speaker, left open on the device
by the loudspeaker, and the library the sounds are uploaded to."""

from __future__ import annotations

from collections.abc import Sequence

from htpy import Element, a, button, div, form, label, li, option, p, section, select, span, ul
from htpy import input as input_el

from app.models.gamestate import Sound
from app.ui.context import Page
from app.ui.forms import alert, notice, text_field
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import confirm_page, delete_link
from app.ui.sound_panel import SOUND_KINDS, SOUND_URL, SOUNDS_URL


def speaker_page(page: Page) -> Element:
    """The page that plays: a tap to start, as phones require, then what is sounding."""
    t = page.t
    return app_shell(
        page,
        t("sound.speaker"),
        div(class_="prose-page sd-speaker", id="sound-speaker")[
            page_head(t("sound.eyebrow"), t("sound.speaker"), t("sound.speaker_lede")),
            button(type="button", class_="btn btn-primary sd-start", data_speaker_start=True)[
                icon("play"), span[t("sound.start")]
            ],
            p(class_="muted", data_speaker_state=True, data_on=t("sound.speaker_on"), hidden=True),
            ul(class_="sd-now", data_speaker_now=True),
            p(class_="field-hint")[t("sound.speaker_hint")],
            p(class_="field-hint", data_speaker_awake=True, hidden=True)[t("sound.awake_hint")],
        ],
        extra_scripts=("/static/js/speaker.js",),
    )


def _kind_select(page: Page, field: str, selected: str) -> Element:
    return select(name=field, aria_label=page.t("sound.kind_label"))[
        [
            option(value=kind, selected=kind == selected)[page.t(f"sound.kind.{kind}")]
            for kind in SOUND_KINDS
        ]
    ]


def _row(page: Page, sound: Sound) -> Element:
    t = page.t
    url = f"{SOUNDS_URL}/{sound.id}"
    return li(class_="list-row sd-row")[
        form(method="post", action=url, class_="sd-edit")[
            input_el(
                type="text",
                name="name",
                value=sound.name,
                required=True,
                aria_label=t("adventures.name"),
            ),
            _kind_select(page, "kind", sound.kind),
            input_el(
                type="range",
                name="volume",
                min="0",
                max="1",
                step="0.01",
                value=str(sound.volume),
                aria_label=t("sound.volume"),
            ),
            input_el(
                type="text",
                name="credit",
                value=sound.credit,
                placeholder=t("sound.credit"),
                aria_label=t("sound.credit"),
            ),
            button(type="submit", class_="btn btn-sm")[t("worlds.save")],
        ],
        delete_link(page, f"{url}/delete", t("sound.delete")),
    ]


def sounds_page(
    page: Page,
    sounds: Sequence[Sound],
    *,
    notice_text: str | None = None,
    error: str | None = None,
) -> Element:
    """Upload files or a whole folder; every sound with what it is and how loud."""
    t = page.t
    return app_shell(
        page,
        t("sound.library"),
        div(class_="prose-page")[
            page_head(t("sound.eyebrow"), t("sound.library"), t("sound.library_lede")),
            notice(notice_text),
            alert(error),
            form(
                method="post",
                action=SOUNDS_URL,
                enctype="multipart/form-data",
                class_="panel stack-form create-form",
            )[
                div(class_="field")[
                    label(for_="f-files")[t("sound.files")],
                    input_el(
                        id="f-files",
                        type="file",
                        name="files",
                        multiple=True,
                        accept=".mp3,.m4a,.wav,.ogg,audio/*",
                    ),
                ],
                div(class_="field")[
                    label(for_="f-folder")[t("sound.folder")],
                    input_el(id="f-folder", type="file", name="files", webkitdirectory=True),
                ],
                div(class_="field")[
                    label(for_="f-kind")[t("sound.kind_label")],
                    _kind_select(page, "kind", "effect"),
                ],
                text_field(
                    "credit",
                    t("sound.credit"),
                    value="",
                    autocomplete="off",
                    required=False,
                    optional_text=t("common.optional"),
                ),
                p(class_="field-hint")[t("sound.upload_hint")],
                div(class_="form-actions")[
                    button(type="submit", class_="btn btn-primary")[
                        icon("upload"), span[t("adventures.upload")]
                    ],
                ],
            ],
            section[
                ul(class_="list")[[_row(page, sound) for sound in sounds]]
                if sounds
                else empty_state("volume-2", t("sound.empty_heading"), t("sound.empty_text"))
            ],
            p(class_="sd-links")[a(href=SOUND_URL)[t("sound.speaker")]],
        ],
    )


def sound_delete_page(page: Page, sound: Sound) -> Element:
    """Confirm deleting a sound."""
    t = page.t
    url = f"{SOUNDS_URL}/{sound.id}"
    return confirm_page(
        page,
        title=t("sound.delete"),
        heading=t("chapters.delete_heading", name=sound.name),
        lede=t("sound.delete_lede"),
        action=f"{url}/delete",
        back=SOUNDS_URL,
        submit=t("sound.delete"),
    )


__all__ = ["sound_delete_page", "sounds_page", "speaker_page"]
