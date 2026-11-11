"""The sound desk's slide-over (documentation/SOUND.md): the top-bar button, the empty
panel on every gamemaster's page, and its pads, fetched when it opens."""

from __future__ import annotations

from collections.abc import Sequence

from htpy import Element, Node, a, b, button, dialog, div, form, h3, p, section, span
from htpy import input as input_el

from app.models.gamestate import Sound, SoundKind, SoundScene
from app.ui.context import Page
from app.ui.icons import icon

SOUND_URL = "/gm/sound"
SOUNDS_URL = "/gm/sounds"
SOUND_KINDS: tuple[SoundKind, ...] = ("ambience", "music", "effect")


def sound_button(page: Page) -> Element | None:
    """The desk in the top bar, the gamemaster's only. A link to the speaker page, which
    works without script; ``sound-panel.js`` opens the panel instead."""
    if page.identity is None or not page.identity.is_gamemaster:
        return None
    label = page.t("sound.panel")
    return a(
        href=SOUND_URL,
        class_="btn btn-ghost shell-sound",
        aria_label=label,
        title=label,
        aria_haspopup="dialog",
    )[icon("volume-2", "md")]


def sound_dialog(page: Page) -> Element | None:
    """The panel's frame. Not modal: the page beneath stays in use during play."""
    if page.identity is None or not page.identity.is_gamemaster:
        return None
    t = page.t
    return dialog(id="sound-panel", class_="logpanel soundpanel", aria_label=t("sound.panel"))[
        div(class_="logpanel-head")[
            b[t("sound.panel")],
            button(
                type="button",
                class_="btn btn-ghost logpanel-close",
                data_sound_close=True,
                aria_label=t("protocol.close"),
                title=t("protocol.close"),
            )[icon("x", "md")],
        ],
        div(class_="logpanel-content")[p(class_="muted")[t("protocol.loading")]],
    ]


def _pad(sound: Sound) -> Element:
    return button(
        type="button",
        class_=f"sd-pad sd-{sound.kind}",
        data_sound_id=sound.id,
        data_name=sound.name.casefold(),
    )[span[sound.name]]


def _scene_pad(page: Page, scene: SoundScene) -> Element:
    return div(class_="sd-scene", data_name=scene.name.casefold())[
        button(type="button", class_="sd-pad sd-scenepad", data_scene_id=scene.id)[
            span[scene.name]
        ],
        button(
            type="button",
            class_="btn btn-sm btn-ghost btn-danger",
            data_scene_delete=scene.id,
            aria_label=page.t("sound.scene_delete", name=scene.name),
            title=page.t("sound.scene_delete", name=scene.name),
        )[icon("trash-2")],
    ]


def sound_panel_body(page: Page, sounds: Sequence[Sound], scenes: Sequence[SoundScene]) -> Node:
    """What the panel shows: the speaker's state, the mix, scenes and pads by kind. The
    state itself comes over the event stream; the script fills it in."""
    t = page.t
    groups: list[Node] = []
    if scenes:
        groups.append(
            section(class_="sd-group")[
                h3[t("sound.scenes")],
                div(class_="sd-pads")[[_scene_pad(page, scene) for scene in scenes]],
            ]
        )
    for kind in SOUND_KINDS:
        pads = [_pad(sound) for sound in sounds if sound.kind == kind]
        if pads:
            groups.append(
                section(class_="sd-group")[h3[t(f"sound.kind.{kind}")], div(class_="sd-pads")[pads]]
            )
    return div(class_="sd-panel")[
        p(class_="sd-status", data_sound_status=True)[a(href=SOUND_URL)[t("sound.no_speaker")]],
        div(class_="sd-master")[
            icon("volume-2"),
            input_el(
                type="range",
                min="0",
                max="1",
                step="0.01",
                value="1",
                data_sound_master=True,
                aria_label=t("sound.master"),
            ),
            button(type="button", class_="btn btn-sm", data_sound_stop=True)[
                icon("volume-x"), span[t("sound.stop_all")]
            ],
        ],
        div(class_="sd-mix", data_sound_mix=True, data_label=t("sound.layer_volume")),
        form(class_="sd-save", data_scene_save=True)[
            input_el(
                type="text",
                name="name",
                autocomplete="off",
                required=True,
                placeholder=t("sound.scene_name"),
                aria_label=t("sound.scene_name"),
            ),
            button(type="submit", class_="btn btn-sm")[t("sound.scene_save")],
        ],
        input_el(
            type="search",
            class_="sd-search",
            data_sound_search=True,
            placeholder=t("sound.search"),
            aria_label=t("sound.search"),
        ),
        groups or p(class_="muted")[t("sound.empty_text")],
        p(class_="sd-links")[
            a(href=SOUNDS_URL)[t("sound.library")], " · ", a(href=SOUND_URL)[t("sound.speaker")]
        ],
    ]


__all__ = [
    "SOUNDS_URL",
    "SOUND_KINDS",
    "SOUND_URL",
    "sound_button",
    "sound_dialog",
    "sound_panel_body",
]
