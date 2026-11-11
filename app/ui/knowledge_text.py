"""How a fact the party knows about a creature reads (documentation/COMBAT.md §5): on
the players' Details and on the gamemaster's list of what they know."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.models.gamestate import KnownFact
from app.services.knowledge import action_name
from app.services.translations import translation_key
from app.ui.context import Page
from app.ui.names import ITEM, named
from app.ui.rules_text import damage_word

# The statblock parts whose entries have names a fact can be about.
_NAMED_PARTS = ("action", "bonus", "reaction", "legendary", "trait")

# The order facts are listed in, by kind.
ORDER = ("action", "spell", "trait", "damage", "immune", "ac", "cr", "note")


def fact_kind(fact_id: str) -> str:
    """``action:Scimitar`` → ``action``; ``ac`` → ``ac``."""
    return fact_id.partition(":")[0]


def action_names(page: Page, data: Mapping[str, Any] | None) -> dict[str, str]:
    """A statblock's action and trait names in the reader's language, where its
    translation gives them: English name (as facts know it) → translated."""
    if data is None or page.lang == "en":
        return {}
    shown = page.t.overlay("monster", translation_key("monster", data), dict(data)).data
    names: dict[str, str] = {}
    for part in _NAMED_PARTS:
        for english, local in zip(data.get(part) or [], shown.get(part) or [], strict=False):
            if isinstance(english, Mapping) and isinstance(local, Mapping):
                before, after = english.get("name"), local.get("name")
                if before and after and before != after:
                    names[action_name(str(before))] = action_name(str(after))
    return names


def fact_label(page: Page, fact_id: str, names: Mapping[str, str] | None = None) -> str:
    """What the fact is about: "Scimitar", "Fire", "Poisoned", "Armour". An action or
    trait takes its name from ``names`` (:func:`action_names`), else from the glossary
    when it is named after a weapon ("Scimitar" → "Krummsäbel")."""
    t = page.t
    kind, _, name = fact_id.partition(":")
    if kind in ("action", "trait"):
        return (names or {}).get(name) or named(page, ITEM, name) or name
    if kind == "spell":
        return named(page, "spell", name) or name
    if kind == "damage":
        return damage_word(page, name).capitalize()
    if kind == "immune":
        return t("combat.known.immune_to", condition=t(f"condition.{name}"))
    return t(f"combat.known.{kind}")


def fact_detail(page: Page, fact_id: str, fact: KnownFact) -> str:
    """What is known of it: "Dexterity save, fire", "half damage", "hard to hit"."""
    t = page.t
    kind = fact_kind(fact_id)
    if kind in ("action", "spell"):
        notes = [t("combat.known.save", ability=t(f"ability.{fact.save}"))] if fact.save else []
        return ", ".join([*notes, *(damage_word(page, d) for d in fact.types if d)])
    if kind == "damage":
        return t(f"combat.known.{fact.effect}") if fact.effect else ""
    if kind in ("ac", "cr"):
        return t(f"combat.known.{kind}_band.{fact.text}") if fact.text else ""
    if kind in ("trait", "note"):
        return fact.text
    return ""


def ordered(facts: dict[str, KnownFact]) -> list[tuple[str, KnownFact]]:
    """Facts by kind, then by name."""
    return sorted(
        facts.items(),
        key=lambda item: (
            ORDER.index(fact_kind(item[0])) if fact_kind(item[0]) in ORDER else len(ORDER),
            item[0],
        ),
    )


__all__ = ["ORDER", "action_names", "fact_detail", "fact_kind", "fact_label", "ordered"]
