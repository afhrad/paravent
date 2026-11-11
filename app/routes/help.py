"""``GET /help/{topic}`` — a help page in the reader's language (app/services/help.py)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.dependencies import IdentityDep, PageDep
from app.routes.auth import html
from app.services.help import help_text
from app.ui.help_pages import help_page

router = APIRouter(tags=["help"])


@router.get("/help/{topic}", response_class=HTMLResponse)
async def help_topic(
    _identity: IdentityDep, page: PageDep, topic: str, back: str = "/"
) -> HTMLResponse:
    """The page; ``back`` returns to where it was opened (a local path only)."""
    text = help_text(topic, page.lang)
    if text is None:
        raise HTTPException(status_code=404)
    title = text.lstrip("# ").splitlines()[0] if text.startswith("#") else topic
    safe_back = back if back.startswith("/") and not back.startswith("//") else "/"
    return html(help_page(page, title, text, safe_back))


__all__ = ["router"]
