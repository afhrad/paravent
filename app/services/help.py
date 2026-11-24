"""Help pages that ship with the app, one Markdown file per language (``app/help/``).

Long prose does not belong in the UI string catalogue, so a help page is a file per
language — ``combat.de.md`` — falling back to English, like every other text.

A passage that belongs to a world's feature or rule is marked, each marker alone on its
line, and may nest::

    <!-- feature: shops -->
    - **A shop** opens from the party view …
    <!-- /feature -->

It is read when the table's world plays with any key the marker names (its features,
app/services/features.py, and its character and combat rules), and left out otherwise,
so a table reads only of what it uses. Sections numbered ``## 3.`` are numbered again
after, leaving no gap.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import Path

HELP_DIR = Path(__file__).resolve().parent.parent / "help"
TOPICS = frozenset({"between", "combat", "mcp", "player", "start"})


_OPEN = re.compile(r"^\s*<!--\s*feature:\s*([\w\s-]+?)\s*-->\s*$")
_CLOSE = re.compile(r"^\s*<!--\s*/feature\s*-->\s*$")
_NUMBERED = re.compile(r"^## \d+\. ")
_BLANKS = re.compile(r"\n{3,}")


def help_text(topic: str, lang: str, played: Collection[str] | None = None) -> str | None:
    """The page for ``topic`` in ``lang``, else in English; None for an unknown topic.
    ``played`` are the keys the table's world plays with: passages marked for others are
    left out (None keeps every passage)."""
    if topic not in TOPICS:
        return None
    for code in (lang, "en"):
        path = HELP_DIR / f"{topic}.{code}.md"
        if path.is_file():
            return shown(path.read_text(encoding="utf-8"), played)
    return None


def shown(text: str, played: Collection[str] | None) -> str:
    """``text`` without its markers, and without the passages of keys not ``played``."""
    kept: list[str] = []
    open_: list[bool] = []
    for line in text.split("\n"):
        if (found := _OPEN.match(line)) is not None:
            open_.append(played is None or any(k in played for k in found[1].split()))
            continue
        if _CLOSE.match(line):
            if open_:
                open_.pop()
            continue
        if all(open_):
            kept.append(line)
    number = 0
    for n, line in enumerate(kept):
        if _NUMBERED.match(line):
            number += 1
            kept[n] = _NUMBERED.sub(f"## {number}. ", line)
    return _BLANKS.sub("\n\n", "\n".join(kept))


__all__ = ["HELP_DIR", "TOPICS", "help_text", "shown"]
