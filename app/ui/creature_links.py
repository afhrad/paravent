"""The creature builder's addresses, apart from its pages so any page can link to a
design without importing the builder (which imports the statblock, which imports the
encounter pages)."""

from __future__ import annotations

CREATURES_URL = "/gm/creatures"


def creature_url(design_id: str) -> str:
    """One design's builder."""
    return f"{CREATURES_URL}/{design_id}"


__all__ = ["CREATURES_URL", "creature_url"]
