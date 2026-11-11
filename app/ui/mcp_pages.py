"""Claude's keys at ``/gm/mcp`` (documentation/MCP.md §3): the tokens made, a new one
shown once with the command that connects Claude Code, and revoking."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    code,
    div,
    form,
    h2,
    h3,
    li,
    p,
    pre,
    section,
    small,
    span,
    ul,
)

from app.models.gamestate import McpToken
from app.ui.context import Page
from app.ui.forms import alert, notice, text_field
from app.ui.home_pages import page_head
from app.ui.icons import icon
from app.ui.layout import app_shell

MCP_URL = "/gm/mcp"


def _when(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%d %H:%M") if moment else "—"


def connect_command(endpoint: str, token: str) -> str:
    """What to paste into a terminal for Claude Code to use this server."""
    return (
        f"claude mcp add --transport http paravent {endpoint} \\\n"
        f'  --header "Authorization: Bearer {token}"'
    )


def desktop_config(endpoint: str, token: str) -> str:
    """Claude Desktop's ``mcpServers`` entry: the ``mcp-remote`` bridge, because its
    custom connectors connect from Anthropic's cloud and cannot reach a home network
    (app/help/mcp.en.md §3). No space in the header argument: Windows breaks it there."""
    entry = {
        "mcpServers": {
            "paravent": {
                "command": "npx",
                "args": [
                    "-y",
                    "mcp-remote",
                    endpoint,
                    "--allow-http",
                    "--header",
                    "Authorization:${PARAVENT_KEY}",
                ],
                "env": {"PARAVENT_KEY": f"Bearer {token}"},
            }
        }
    }
    return json.dumps(entry, indent=2)


def mcp_page(
    page: Page,
    *,
    endpoint: str,
    tokens: Sequence[McpToken],
    made: tuple[McpToken, str] | None = None,
    error: str | None = None,
    message: str | None = None,
) -> Element:
    """The tokens, the form for a new one, and a new one's token this once."""
    t = page.t
    shown: Node = None
    if made is not None:
        record, token = made
        shown = section(class_="panel mcp-made")[
            h2[t("mcp.made", name=record.name)],
            p(class_="muted")[t("mcp.made_once")],
            h3(class_="mcp-sub")[t("mcp.for_code")],
            pre(class_="mcp-command")[code[connect_command(endpoint, token)]],
            h3(class_="mcp-sub")[t("mcp.for_desktop")],
            p(class_="muted")[t("mcp.desktop_hint")],
            pre(class_="mcp-command")[code[desktop_config(endpoint, token)]],
        ]
    return app_shell(
        page,
        t("mcp.title"),
        div(class_="tr-page mcp-page")[
            page_head(
                t("settings.menu"),
                t("mcp.title"),
                t("mcp.lede"),
                a(href=f"/help/mcp?back={MCP_URL}", class_="btn")[
                    icon("book-open"), span[t("mcp.how")]
                ],
            ),
            alert(error),
            notice(message),
            shown,
            section(class_="panel")[
                h2[t("mcp.new")],
                form(method="post", action=MCP_URL, class_="mcp-new")[
                    text_field(
                        "name",
                        t("mcp.name"),
                        autocomplete="off",
                        hint=t("mcp.name_hint"),
                    ),
                    button(type="submit", class_="btn btn-primary")[
                        icon("plus"), span[t("mcp.create")]
                    ],
                ],
                p(class_="muted")[t("mcp.endpoint"), " ", code[endpoint]],
            ],
            section(class_="panel")[
                h2[t.plural("mcp.count", len(tokens))],
                ul(class_="mcp-list")[
                    [
                        li[
                            div[
                                b[x.name],
                                " ",
                                small(class_="muted")[
                                    t(
                                        "mcp.used",
                                        made=_when(x.created_at),
                                        used=_when(x.last_used_at),
                                    )
                                ],
                            ],
                            form(method="post", action=f"{MCP_URL}/{x.id}/revoke")[
                                button(type="submit", class_="btn btn-ghost btn-sm")[
                                    icon("trash-2"), span[t("mcp.revoke")]
                                ]
                            ],
                        ]
                        for x in tokens
                    ]
                ]
                if tokens
                else p(class_="muted")[t("mcp.none")],
            ],
        ],
    )


__all__ = ["MCP_URL", "connect_command", "desktop_config", "mcp_page"]
