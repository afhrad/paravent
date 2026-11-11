"""Localized names for the magic items Paravent makes from variants (TRANSLATION.md §4).

"+1 Longsword", "Flame Tongue Greatsword" and "Mithral Plate Armor" are not records in
the corpus: they are made at load from a generic variant and a base item
(app/store/magicvariants.py), so the glossary cannot name them one by one. Their name in
another language is put together from the two names the glossary does have:

* a bonus variant ("+1 Weapon", "+2 Armor") → the base item and the bonus,
  "Langschwert +1", as German item lists write it;
* any other variant → the variant, then the base item in brackets,
  "Flammenzunge (Zweihandschwert)", "Mithrilrüstung (Ritterrüstung)".

When either name is missing from the glossary, nothing is composed and the English name
stands — half a translation is worse than none.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

_BONUS = re.compile(r"^\+(\d) ")

Lookup = Callable[[str, str], str | None]


class ItemNames:
    """Composes names for generated items from the glossary's variant and base names."""

    def __init__(self, compendium: Any) -> None:
        """Bind to the loaded compendium; the index follows its reloads."""
        self._compendium = compendium
        self._for: Any = None
        self._made: dict[str, tuple[str, str]] = {}

    def _index(self) -> dict[str, tuple[str, str]]:
        loaded = getattr(self._compendium, "projections", None)
        if loaded is self._for and self._made:
            return self._made
        made: dict[str, tuple[str, str]] = {}
        for model in self._compendium.all("item"):
            extra = getattr(model, "model_extra", None) or {}
            variant = extra.get("genericVariant") or getattr(model, "genericVariant", None)
            base = extra.get("baseItem") or getattr(model, "baseItem", None)
            if isinstance(variant, dict) and isinstance(base, str):
                made[str(model.name).lower()] = (
                    str(variant.get("name", "")),
                    base.split("|", 1)[0],
                )
        self._made, self._for = made, loaded
        return made

    def compose(self, category: str, english: str, term: Lookup) -> str | None:
        """The localized name of a generated item, or None.

        ``term(category, name)`` is the glossary of the reader's language.
        """
        if category != "item":
            return None
        found = self._index().get(english.lower())
        if found is None:
            return None
        variant, base = found
        base_name = term("baseitem", base) or term("item", base)
        if not base_name:
            return None
        bonus = _BONUS.match(variant)
        if bonus:
            return f"{base_name} +{bonus.group(1)}"
        variant_name = term("magicvariant", variant)
        return f"{variant_name} ({base_name})" if variant_name else None


__all__ = ["ItemNames"]
