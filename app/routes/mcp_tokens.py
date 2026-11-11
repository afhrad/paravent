"""Claude's keys to the MCP server (documentation/MCP.md §3), for the gamemaster.

* ``GET  /gm/mcp``             — the tokens, and the form for a new one
* ``POST /gm/mcp``             — make one; the answer shows it this once
* ``POST /gm/mcp/{id}/revoke`` — remove one
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, Response

from app.dependencies import GamemasterDep, PageDep, RegistryDep
from app.routes.auth import html, redirect
from app.services.mcp_tokens import McpTokenError
from app.ui.mcp_pages import MCP_URL, mcp_page

router = APIRouter(tags=["mcp"])


def _endpoint(request: Request) -> str:
    return f"{str(request.base_url).rstrip('/')}/mcp"


@router.get(MCP_URL, response_class=HTMLResponse)
async def show(
    _gm: GamemasterDep, page: PageDep, registry: RegistryDep, request: Request, revoked: str = ""
) -> HTMLResponse:
    """The tokens made, and the form for a new one."""
    return html(
        mcp_page(
            page,
            endpoint=_endpoint(request),
            tokens=registry.mcp_tokens.list(),
            message=page.t("mcp.revoked", name=revoked) if revoked else None,
        )
    )


@router.post(MCP_URL, response_class=HTMLResponse)
async def create(
    gm: GamemasterDep,
    page: PageDep,
    registry: RegistryDep,
    request: Request,
    name: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """A new token, shown in this answer only — never in an address or a later page."""
    service = registry.mcp_tokens
    try:
        made = service.create(name, gm.user)
    except McpTokenError as exc:
        return html(
            mcp_page(
                page,
                endpoint=_endpoint(request),
                tokens=service.list(),
                error=page.t(exc.key, **exc.params),
            ),
            status_code=422,
        )
    response = html(
        mcp_page(
            page,
            endpoint=_endpoint(request),
            tokens=service.list(),
            made=(made.record, made.token),
        )
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post(f"{MCP_URL}/{{token_id}}/revoke")
async def revoke(_gm: GamemasterDep, registry: RegistryDep, token_id: str) -> Response:
    """Remove a token; Claude using it is refused from the next request."""
    found = next((x for x in registry.mcp_tokens.list() if x.id == token_id), None)
    registry.mcp_tokens.revoke(token_id)
    return redirect(f"{MCP_URL}?{urlencode({'revoked': found.name if found else ''})}")
