"""Lucide icons from the bundled sprite (documentation/DESIGN.md §5).

``static/icons.svg`` holds every symbol the UI uses. It is inlined once per page by
:func:`sprite`, and :func:`icon` emits a ``<use>`` reference into it, so a page carries
each glyph once no matter how often it appears.

An unknown name raises: there is no build step to catch a typo, and a missing glyph
must fail in a test rather than render as an empty box.
"""

from __future__ import annotations

import re
from functools import lru_cache

from htpy import Element, svg, use
from markupsafe import Markup

from app.config import STATIC_DIR

SPRITE_PATH = STATIC_DIR / "icons.svg"

# The optical size ramp from DESIGN.md §5 — stroke weight is set by the class.
SIZES = frozenset({"xs", "sm", "md", "lg", "xl"})


class UnknownIcon(KeyError):
    """An icon name that is not in the sprite."""


@lru_cache(maxsize=1)
def _sprite_text() -> str:
    return SPRITE_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def available() -> frozenset[str]:
    """Every icon name the sprite defines, without the ``i-`` prefix."""
    return frozenset(re.findall(r'<symbol id="i-([a-z0-9-]+)"', _sprite_text()))


def sprite() -> Markup:
    """The whole sprite, for inlining once at the top of ``<body>``."""
    return Markup(_sprite_text())


def icon(name: str, size: str | None = None, *, muted: bool = False) -> Element:
    """An inline icon. Decorative by default — the adjacent label carries the meaning."""
    if name not in available():
        raise UnknownIcon(name)
    classes = ["ic"]
    if size is not None:
        if size not in SIZES:
            raise ValueError(f"Unknown icon size {size!r}")
        classes.append(f"ic-{size}")
    if muted:
        classes.append("ic-muted")
    return svg(class_=" ".join(classes), aria_hidden="true", focusable="false")[
        use(href=f"#i-{name}")
    ]


__all__ = ["UnknownIcon", "available", "icon", "sprite"]
