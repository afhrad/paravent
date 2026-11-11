"""Corpus names in the reader's language, from the term glossary (TRANSLATION.md §4).

The records keep English names — references resolve in English — and the screens show
the glossary's name where it has one, with the English name as the element's title, so
the original stays one hover away. Nothing here changes what is stored.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from htpy import Element, span

from app.models.pc import ChosenOption, PlayerCharacter
from app.ui.context import Page

# A row's kind → the categories its name may be filed under (items are base or magic).
ITEM = ("baseitem", "item")


def named(page: Page, categories: str | tuple[str, ...], english: str) -> str | None:
    """The glossary's name for ``english`` under the first category that has one."""
    for category in (categories,) if isinstance(categories, str) else categories:
        found = page.t.term(category, english)
        if found:
            return found
    return None


def name_text(page: Page, categories: str | tuple[str, ...], english: str) -> str:
    """The localized name, or the English one."""
    return named(page, categories, english) or english


def name_span(
    page: Page, categories: str | tuple[str, ...], english: str, *, cls: str = "nm"
) -> Element:
    """A name element: localized where named, the English name as its title."""
    localized = named(page, categories, english)
    return span(class_=cls, title=english if localized else None)[localized or english]


# Armor and weapon training as the class records write it → its UI string.
_TRAINING = {
    "light": "light",
    "medium": "medium",
    "heavy": "heavy",
    "shield": "shield",
    "shields": "shield",
    "simple": "simple",
    "martial": "martial",
    "firearms": "firearms",
    "martial weapons that have the finesse or light property": "martial_finesse_light",
    "martial weapons that have the light property": "martial_light",
}


def training_text(page: Page, word: str, items: Mapping[str, str]) -> str:
    """A training as the reader's language says it: "martial" → "Kriegswaffen"; a named
    weapon by its item's glossary name ("daggers" → "Dolch"); else as written."""
    key = _TRAINING.get(word.lower())
    if key is not None:
        return page.t(f"training.{key}")
    item = items.get(word.lower())
    return (named(page, ITEM, item) if item else None) or word


# Where a sheet feature comes from: a class or subclass with its level, a species or
# background by name, or one of two words.
_ORIGIN_WORDS = {"Feat": "chargen.pick.feat", "Option": "chargen.pick.optional"}
_ORIGIN_CATEGORIES = ("class", "subclass", "race", "subrace", "background")


def origin_text(page: Page, origin: str) -> str:
    """ "Fighter 3" → "Kämpfer 3", "Feat" → "Talent", "Soldier" → "Soldat"."""
    if origin in _ORIGIN_WORDS:
        return page.t(_ORIGIN_WORDS[origin])
    name, _, level = origin.rpartition(" ")
    if name and level.isdigit():
        return f"{name_text(page, _ORIGIN_CATEGORIES, name)} {level}"
    return name_text(page, _ORIGIN_CATEGORIES, origin)


def _option(page: Page, category: str, option: ChosenOption | None) -> str:
    return name_text(page, category, option.name) if option is not None else ""


def class_line(page: Page, pc: PlayerCharacter) -> str:
    """ "Magier 11 · Hervorrufung / Schurke 1": each class and subclass, localized."""
    return classes_text(
        page,
        [(c.name, c.level, c.subclass.name if c.subclass is not None else "") for c in pc.classes],
    )


def classes_text(page: Page, classes: Iterable[tuple[str, int, str]]) -> str:
    """:func:`class_line` from ``(class, level, subclass or "")`` parts."""
    parts = []
    for name, level, subclass in classes:
        text = f"{name_text(page, 'class', name)} {level}"
        if subclass:
            text += f" · {name_text(page, 'subclass', subclass)}"
        parts.append(text)
    return " / ".join(parts)


def summary(page: Page, pc: PlayerCharacter) -> str:
    """Species, classes and background, localized — the line under a character's name."""
    species = _option(page, "race", pc.species)
    if pc.subspecies is not None and pc.species is not None:
        # A subrace's localized name is the whole name ("Hügelzwerg"): German joins the
        # two into one word, where English writes "Hill Dwarf".
        local = named(page, "subrace", pc.subspecies.name)
        species = local or f"{pc.subspecies.name} {species}".strip()
    return " · ".join(
        p for p in (species, class_line(page, pc), _option(page, "background", pc.background)) if p
    )


def monster_label(page: Page, label: str, monster_name: str | None) -> str:
    """A combatant's label with its creature's name localized: "Owlbear 2" → "Eulenbär 2".

    Only the generated part is replaced — a label the gamemaster renamed is left as is.
    """
    if not monster_name or not label.startswith(monster_name):
        return label
    localized = named(page, "monster", monster_name)
    return f"{localized}{label[len(monster_name) :]}" if localized else label


__all__ = [
    "ITEM",
    "class_line",
    "monster_label",
    "name_span",
    "name_text",
    "named",
    "origin_text",
    "summary",
    "training_text",
]
