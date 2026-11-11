"""Backups at ``/gm/backups``: where they go, when the next is due, the ones kept, and a
button to make one now (app/services/backup.py)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from htpy import Element, Node, b, button, code, div, form, h2, li, p, section, small, span, ul

from app.services.backup import Backup
from app.ui.context import Page
from app.ui.forms import alert, notice
from app.ui.home_pages import empty_state, page_head
from app.ui.icons import icon
from app.ui.layout import app_shell

BACKUPS_URL = "/gm/backups"


def _when(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")


def _size(page: Page, size: int) -> str:
    return page.t("backup.size", kb=page.t.number(max(1, round(size / 1024))))


def backup_page(
    page: Page,
    *,
    target: str,
    times: Sequence[str],
    keep: int,
    next_due: datetime | None,
    backups: Sequence[Backup],
    error: str | None = None,
    message: str | None = None,
) -> Element:
    """The backup folder's state and the button; ``target`` empty when none is set."""
    t = page.t
    head = page_head(t("settings.menu"), t("backup.title"), t("backup.lede"))
    if not target:
        body: Node = [
            alert(error),
            empty_state("vault", t("backup.off"), t("backup.off_hint")),
            p(class_="muted")[code["PARAVENT_BACKUP_DIR=/path/to/backups"]],
        ]
        return app_shell(page, t("backup.title"), div(class_="tr-page")[head, body])
    return app_shell(
        page,
        t("backup.title"),
        div(class_="tr-page backup-page")[
            head,
            alert(error),
            notice(message),
            section(class_="panel backup-head")[
                p[t("backup.target"), " ", code[target]],
                p(class_="muted")[
                    t("backup.schedule", times=", ".join(times), keep=keep)
                    if times
                    else t("backup.on_demand", keep=keep),
                    [" ", t("backup.next", when=_when(next_due))] if next_due else None,
                ],
                form(method="post", action=f"{BACKUPS_URL}/run")[
                    button(type="submit", class_="btn btn-primary")[
                        icon("vault"), span[t("backup.run")]
                    ]
                ],
            ],
            section(class_="panel")[
                h2[t.plural("backup.kept", len(backups))],
                ul(class_="backup-list")[
                    [
                        li[
                            b[_when(x.made)],
                            " ",
                            small(class_="muted mono")[x.path.name, " · ", _size(page, x.size)],
                        ]
                        for x in backups
                    ]
                ]
                if backups
                else p(class_="muted")[t("backup.none")],
            ],
        ],
    )


__all__ = ["BACKUPS_URL", "backup_page"]
