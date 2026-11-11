"""Help pages that ship with the app, one Markdown file per language (``app/help/``).

Long prose does not belong in the UI string catalogue, so a help page is a file per
language — ``combat.de.md`` — falling back to English, like every other text.
"""

from __future__ import annotations

from pathlib import Path

HELP_DIR = Path(__file__).resolve().parent.parent / "help"
TOPICS = frozenset({"combat", "mcp", "start"})


def help_text(topic: str, lang: str) -> str | None:
    """The page for ``topic`` in ``lang``, else in English; None for an unknown topic."""
    if topic not in TOPICS:
        return None
    for code in (lang, "en"):
        path = HELP_DIR / f"{topic}.{code}.md"
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return None


__all__ = ["HELP_DIR", "TOPICS", "help_text"]
