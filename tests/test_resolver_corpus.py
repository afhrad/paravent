"""The source-set resolver against the real corpus (documentation/TECHSTACK.md §6c).

Two claims the measurements in §6c make, held as tests so a corpus update or a resolver
change that breaks them is noticed:

* for spells, creatures and items, a declared set of books plus ``reprintedAs``
  supersession resolves every name with several printings in those books to exactly one
  entry from them; the few the corpus leaves unlinked fall back to declaration order,
  flagged ``AMBIGUOUS`` — they are listed here, so a new one fails the test;
* a reference to a book the world does not declare gives the "not in your books" state:
  never a wrong entry, never an exception.
"""

from __future__ import annotations

from collections import defaultdict

import pytest

from app.models.gamestate import SourceSet
from app.store.compendium import CompendiumStore
from app.store.reference import Outcome
from app.ui.tags import RenderContext, render

# The sets §6c measured.
SOURCE_SETS = {
    "2014 core + expansions": ["PHB", "DMG", "MM", "XGE", "TCE", "VGM", "MTF", "MPMM"],
    "2024 core only": ["XPHB", "XDMG", "XMM"],
    "2024 core + 2014 expansions": ["XPHB", "XDMG", "XMM", "XGE", "TCE", "VGM", "MTF", "MPMM"],
}
CATEGORIES = ("spell", "monster", "item")

# What the corpus leaves unlinked, measured 2026-10-21 (§6c). The Shadow Mastiff of
# VGM points at MPMM's "Shadow Mastiff Alpha", while MPMM also prints a plain Shadow
# Mastiff; XGE's "Adamantine Ammunition" and the 2024 DMG's "Adamantine Weapon" make
# the same ammunition with no reprint link between the two variants.
ADAMANTINE = {
    "adamantine arrow",
    "adamantine bolt",
    "adamantine firearm bullet",
    "adamantine needle",
    "adamantine sling bullet",
}
KNOWN_RESIDUE: dict[tuple[str, str], set[str]] = {
    ("2014 core + expansions", "monster"): {"shadow mastiff"},
    ("2024 core + 2014 expansions", "monster"): {"shadow mastiff"},
    ("2024 core + 2014 expansions", "item"): ADAMANTINE,
}


def _names_in(compendium: CompendiumStore, category: str, books: set[str]) -> dict[str, int]:
    """Names with how many printings the declared books hold."""
    counts: dict[str, int] = defaultdict(int)
    for model in compendium.all(category):
        if str(model.source).upper() in books:
            counts[str(model.name).lower()] += 1
    return counts


@pytest.mark.parametrize("label", list(SOURCE_SETS))
@pytest.mark.parametrize("category", CATEGORIES)
def test_every_ambiguous_name_resolves_to_one(
    loaded_compendium: CompendiumStore, label: str, category: str
) -> None:
    books = {b.upper() for b in SOURCE_SETS[label]}
    declared = SourceSet(sources=SOURCE_SETS[label])
    resolver = loaded_compendium.resolver()
    several = sorted(n for n, c in _names_in(loaded_compendium, category, books).items() if c > 1)
    wrong: list[str] = []
    flagged: set[str] = set()
    for name in several:
        result = resolver.resolve_parts(category, name, None, declared)
        if result.entry is None or str(result.entry.source).upper() not in books:
            wrong.append(f"{name}: {result.outcome}")  # no entry, or one from another book
        elif result.outcome is Outcome.AMBIGUOUS:
            flagged.add(name)
    assert not wrong, f"{label}, {category}: {wrong[:10]}"
    assert flagged == KNOWN_RESIDUE.get((label, category), set())


class TestOutsideTheBooks:
    DECLARED = SourceSet(sources=["PHB", "DMG", "MM"])

    def test_an_explicit_book_the_world_lacks(self, loaded_compendium: CompendiumStore) -> None:
        result = loaded_compendium.resolver().resolve_parts(
            "monster", "Xvart", "MPMM", self.DECLARED
        )
        assert result.entry is None and result.outcome is Outcome.NOT_IN_SOURCE_SET

    def test_a_bare_name_only_other_books_print(self, loaded_compendium: CompendiumStore) -> None:
        result = loaded_compendium.resolver().resolve_parts("monster", "xvart", None, self.DECLARED)
        assert result.entry is None and result.outcome is Outcome.NOT_IN_SOURCE_SET

    def test_a_name_nothing_prints(self, loaded_compendium: CompendiumStore) -> None:
        result = loaded_compendium.resolver().resolve_parts(
            "spell", "Paravent's Imaginary Spell", None, self.DECLARED
        )
        assert result.entry is None and result.outcome is Outcome.NOT_IMPORTED

    def test_the_link_shows_the_not_in_your_books_state(
        self, loaded_compendium: CompendiumStore
    ) -> None:
        ctx = RenderContext(resolver=loaded_compendium.resolver(), source_set=self.DECLARED)
        html = str(render("{@creature xvart|MPMM}", ctx)[0])
        assert 'class="ref missing"' in html and "href" not in html
        assert "MPMM is not among this world" in html
