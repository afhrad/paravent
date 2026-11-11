"""The table's own work against the corpus: what a new 5eTools release left behind.

The library addresses the corpus by name, source and position, and nothing fails when
those change: a translation keeps showing, a link turns grey, a fight loses a monster's
statblock. The corpus check (app/services/corpus_check.py) runs this after its own
passes, so the first check of a new release lists all of it:

1. **Translations** — passages whose English changed (stale) or went (orphaned), records
   whose entry, chapter or book is gone, tags of the English they lack, links of their
   own that lead nowhere.
2. **Names** — glossary names of entries that are gone.
3. **Books** — books a world or adventure declares, or an adventure is based on, that the
   corpus no longer holds.
4. **References** — encounters, NPCs, treasure, characters and running fights pointing at
   an entry by name and source, and notes placed in a published adventure's text.
5. **Links** — every text of the campaign through the chapter check
   (app/ui/prose_check.py), so that it and the editor cannot disagree.

Nothing is changed: a finding says where, and links to where it can be mended.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from app.models.gamestate import Scope
from app.services.corpus_check import EntityTags, Kind, _Findings
from app.services.translation_targets import Target

# What the chapter check reports that is a link; the rest (an empty encounter, an unclosed
# block) is about the writing, and no release changes it.
LINK_KINDS = frozenset({"reference", "excluded", "library", "image", "heading"})
ITEMS = ("item", "baseitem", "magicvariant", "itemGroup")
MAX_NAMED = 3


def _few(values: Sequence[str]) -> str:
    """Up to :data:`MAX_NAMED` of ``values``, and how many more."""
    shown = ", ".join(values[:MAX_NAMED])
    more = len(values) - MAX_NAMED
    return f"{shown} (+{more})" if more > 0 else shown


class LibraryCheck:
    """One pass over the library; call it with the check's findings."""

    def __init__(self, stores: Any) -> None:
        """``stores`` is the registry."""
        self._stores = stores
        self._found = _Findings()
        self._tags: EntityTags | None = None

    def __call__(self, found: _Findings) -> None:
        """Add what is out of step with the loaded corpus to ``found``."""
        self._found = found
        self._tags = EntityTags(self._stores.compendium)
        for lang in self._stores.translations.languages():
            self._translations(lang)
            self._glossary(lang)
        self._books()
        self._references()
        self._links()

    # -- 1. translations -------------------------------------------------------------------

    def _translations(self, lang: str) -> None:
        from app.ui.translate_pages import TRANSLATIONS_URL

        coverage = self._stores.translation_coverage
        for lost in coverage.lost(lang):
            detail = f"{lost.passages} passage(s)"
            self._found.add(
                Kind.TRANSLATION_LOST,
                f"i18n/{lang}/{lost.category}.json",
                f"{lost.category}: {lost.key}",
                f"{detail}; perhaps {lost.near}" if lost.near else detail,
                href=f"{TRANSLATIONS_URL}?lang={lang}#lost",
            )
        for audit in coverage.audit(lang):
            if audit.target is None:
                continue
            subject = f"{audit.category}: {audit.key}"
            file = f"i18n/{lang}/{audit.category}.json"
            href = _editor(audit.target, lang)
            if audit.stale:
                self._found.add(Kind.TRANSLATION_STALE, file, subject, _few(audit.stale), href=href)
            if audit.orphans:
                self._found.add(
                    Kind.TRANSLATION_ORPHAN, file, subject, _few(audit.orphans), href=href
                )
            if audit.missing:
                self._found.add(
                    Kind.TRANSLATION_TAGS, file, subject, _few(audit.missing), href=href
                )
            dead = [
                f"{{@{tag} {name}}}" for _p, text in audit.held for tag, name in self._dead(text)
            ]
            if dead:
                self._found.add(Kind.TRANSLATION_LINK, file, subject, _few(dead), href=href)

    def _dead(self, text: str) -> Iterable[tuple[str, str]]:
        return self._tags.dead(text) if self._tags is not None else ()

    # -- 2. names --------------------------------------------------------------------------

    def _glossary(self, lang: str) -> None:
        compendium = self._stores.compendium
        known = set(compendium.categories())
        names: dict[str, set[str]] = {}
        for term in self._stores.glossary.terms(lang):
            if term.category not in known:
                continue  # terms of no category of the corpus: nothing to compare with
            if term.category not in names:
                names[term.category] = {
                    str(getattr(e, "name", "")).lower() for e in compendium.all(term.category)
                }
            if term.english.lower() not in names[term.category]:
                self._found.add(
                    Kind.GLOSSARY_LOST,
                    f"i18n/{lang}/_glossary.json",
                    f"{term.category}: {term.english}",
                    term.localized,
                )

    # -- 3. books --------------------------------------------------------------------------

    def _books(self) -> None:
        from app.ui.library_links import adventure_url, world_url

        stores = self._stores
        compendium = stores.compendium
        published = {x.id.lower() for x in stores.published.all()}
        known = published | {x.id.lower() for x in stores.rulebooks.all()}
        for category in compendium.categories():
            known.update(source.lower() for source in compendium.sources(category))

        def declared(subject: str, sources: Iterable[str], href: str) -> None:
            for source in sources:
                if source.lower() not in known:
                    self._found.add(Kind.LIBRARY_BOOK, subject=subject, detail=source, href=href)

        for world in stores.gamestate.worlds.list():
            declared(world.name, world.source_set.sources, world_url(world.id))
        for adventure in stores.gamestate.adventures.list():
            href = adventure_url(adventure.id)
            if adventure.source_set is not None:
                declared(adventure.name, adventure.source_set.sources, href)
            if adventure.based_on and adventure.based_on.lower() not in published:
                self._found.add(
                    Kind.LIBRARY_BOOK, subject=adventure.name, detail=adventure.based_on, href=href
                )

    # -- 4. references ---------------------------------------------------------------------

    def _exists(self, categories: Sequence[str], reference: str) -> bool:
        name, _, source = reference.partition("|")
        compendium = self._stores.compendium
        return any(compendium.find(c, name, source or None) is not None for c in categories)

    def _missing(
        self, subject: str, href: str, categories: Sequence[str], reference: str | None
    ) -> None:
        if reference and not self._exists(categories, reference):
            self._found.add(Kind.LIBRARY_REFERENCE, subject=subject, detail=reference, href=href)

    def _references(self) -> None:
        from app.ui.encounter_pages import encounter_url
        from app.ui.library_links import npc_url, treasure_url

        state = self._stores.gamestate
        for encounter in state.encounters.list():
            for monster in encounter.monsters:
                self._missing(
                    f"encounter: {encounter.name}",
                    encounter_url(encounter.id),
                    ("monster",),
                    monster.ref,
                )
        for npc in state.npcs.list():
            self._missing(f"NPC: {npc.name}", npc_url(npc.id), ("monster",), npc.statblock)
        for treasure in state.treasures.list():
            for item in treasure.items:
                self._missing(
                    f"treasure: {treasure.name}", treasure_url(treasure.id), ITEMS, item.ref
                )
        for combat in state.combats.list():
            if combat.ended is None:
                for ref in sorted({c.ref for c in combat.combatants if c.ref}):
                    self._missing(f"fight: {combat.name}", "", ("monster",), ref)
        for character in self._stores.gamestate.characters.list():
            self._character(character)
        self._anchors()

    def _character(self, pc: Any) -> None:
        """Only what names its book: a bare skill or free-text gear means no entry."""
        from app.ui.library_links import character_url

        subject, href = f"character: {pc.name}", character_url(pc.id)

        def check(categories: Sequence[str], chosen: Any) -> None:
            if chosen is not None and chosen.source:
                self._missing(subject, href, categories, chosen.reference)

        check(("race",), pc.species)
        check(("background",), pc.background)
        for level in pc.classes:
            if level.source:
                self._missing(subject, href, ("class",), level.reference)
            sub = level.subclass
            if sub is not None and sub.source and not self._named("subclass", sub):
                self._missing_named(subject, href, sub.reference)
        sub = pc.subspecies
        if sub is not None and sub.source and not self._named("subrace", sub):
            self._missing_named(subject, href, sub.reference)
        for feat in pc.feats:
            check(("feat",), feat)
        for option in pc.optional_features:
            check(("optionalfeature",), option)
        for spell in (*pc.spells.cantrips, *pc.spells.known, *pc.spells.prepared):
            check(("spell",), spell)
        for carried in pc.inventory:
            check(ITEMS, carried.item)

    def _missing_named(self, subject: str, href: str, reference: str) -> None:
        self._found.add(Kind.LIBRARY_REFERENCE, subject=subject, detail=reference, href=href)

    def _named(self, category: str, chosen: Any) -> bool:
        """Characters name a subclass or subspecies short ("Devotion|PHB"), not as its
        key does."""
        name, source = chosen.name.lower(), str(chosen.source).lower()
        return any(
            str(getattr(entry, "source", "")).lower() == source
            and name
            in (
                str(getattr(entry, "name", "")).lower(),
                str(getattr(entry, "shortName", "") or "").lower(),
            )
            for entry in self._stores.compendium.all(category)
        )

    def _anchors(self) -> None:
        """A chapter on a published section, and the notes placed in its text by id."""
        from app.ui.library_links import chapter_url
        from app.ui.markdown import split_anchors

        adventures = self._stores.adventures
        for chapter in self._stores.gamestate.chapters.list():
            if not chapter.section:
                continue
            view = adventures.chapter_view(chapter.id)
            if view.book is None:
                continue  # the adventure's book is gone: reported once, on the adventure
            href = chapter_url(chapter.id)
            found = adventures.book_section(view)
            if found is None:
                self._found.add(
                    Kind.LIBRARY_REFERENCE,
                    subject=f"chapter: {chapter.title}",
                    detail=f"{view.book.id}#{chapter.section}",
                    href=href,
                )
                continue
            body, part = found
            _rest, anchored = split_anchors(chapter.body)
            for entry_id, _text in anchored:
                if body.section_of(entry_id) != part.id:
                    self._found.add(
                        Kind.LIBRARY_LINK,
                        subject=f"chapter: {chapter.title}",
                        detail=f":::: at {entry_id}",
                        href=href,
                    )

    # -- 5. links --------------------------------------------------------------------------

    def _links(self) -> None:
        # The chapter check's helpers live with the routes that serve the editor.
        from app.routes.adventures import chapter_problems, notes_problems
        from app.ui.context import Page
        from app.ui.encounter_pages import encounter_url
        from app.ui.library_links import (
            adventure_url,
            chapter_url,
            location_url,
            npc_url,
            treasure_url,
            world_url,
        )

        stores = self._stores
        state = stores.gamestate
        page = Page(
            t=stores.ui.translator(
                "en", stores.glossary, stores.translations, stores.item_names.compose
            )
        )

        def notes(subject: str, href: str, here: Scope, text: str) -> None:
            if text:
                self._report(subject, href, notes_problems(page, stores, here, text))

        for world in state.worlds.list():
            scope = Scope(world_id=world.id)
            notes(f"world: {world.name}", world_url(world.id), scope, world.description)
        for adventure in state.adventures.list():
            scope = Scope(world_id=adventure.world_id, adventure_id=adventure.id)
            notes(
                f"adventure: {adventure.name}", adventure_url(adventure.id), scope, adventure.body
            )
        for chapter in state.chapters.list():
            problems = chapter_problems(page, stores, chapter.id, chapter.body)
            self._report(f"chapter: {chapter.title}", chapter_url(chapter.id), problems)
        for npc in state.npcs.list():
            notes(f"NPC: {npc.name}", npc_url(npc.id), npc.scope, npc.notes)
        for place in state.locations.list():
            notes(f"place: {place.name}", location_url(place.id), place.scope, place.notes)
        for encounter in state.encounters.list():
            if encounter.scope is not None:
                href = encounter_url(encounter.id)
                notes(f"encounter: {encounter.name}", href, encounter.scope, encounter.notes)
        for treasure in state.treasures.list():
            notes(
                f"treasure: {treasure.name}",
                treasure_url(treasure.id),
                treasure.scope,
                treasure.notes,
            )
        for note in state.session_notes.list():
            scope = Scope(world_id=note.world_id, adventure_id=note.adventure_id)
            notes(f"session: {note.day}", world_url(note.world_id), scope, note.text)

    def _report(self, subject: str, href: str, problems: Iterable[Any]) -> None:
        for problem in problems:
            if problem.kind in LINK_KINDS:
                detail = f"line {problem.line}: {problem.text}"
                if problem.instead:
                    detail += f" → {problem.instead[0]}"
                self._found.add(Kind.LIBRARY_LINK, subject=subject, detail=detail, href=href)


def _editor(target: Target, lang: str) -> str:
    from app.ui.corpus_pages import CORPUS_URL
    from app.ui.translate_pages import translate_url

    return translate_url(target.kind, *target.where, lang=lang, show="todo", back=CORPUS_URL)


__all__ = ["LibraryCheck"]
