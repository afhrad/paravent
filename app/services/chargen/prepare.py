"""Preparing spells after a long rest (CHARGEN-WIZARD.md §10).

A cleric, druid, paladin or artificer (and the 2024 ranger) prepares from the whole
class list; a wizard from the spellbook. How many is the sheet's ``preparing`` — the
class's ``preparedSpellsProgression`` or ``preparedSpells`` formula at its own level.
The spells a subclass always has prepared are not chosen and not counted.

Which spells are on the list is the creation wizard's reading: the spell-source lookup
(CHARGEN.md trap #2), up to the highest spell level the class reaches on its own — a
multiclassed character prepares each class's spells as if it were the only one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.models.pc import PREPARED_PREFIX, ChosenOption, DraftStatus, PlayerCharacter
from app.services.auth import Identity
from app.services.chargen.catalogue import Catalogue, Offer
from app.services.chargen.drafts import Drafts
from app.services.i18n import Translatable
from app.services.sheet import Sheet, build_sheet
from app.store.gamestate import GameStateStore


class PrepareError(Translatable):
    """Preparation refused: nothing to prepare, too many, or not on the list."""


@dataclass(frozen=True)
class ClassPreparation:
    """One class's preparation: from its list or its book, and how many."""

    class_name: str
    limit: int
    from_book: bool
    pool: tuple[Offer, ...]
    chosen: tuple[str, ...]

    @property
    def options(self) -> tuple[str, ...]:
        """The pool as references, for the question renderer."""
        return tuple(o.reference for o in self.pool)

    @property
    def pick_id(self) -> str:
        """The form field and the ``choices`` key of this class's spells."""
        return f"{PREPARED_PREFIX}{self.class_name}"


@dataclass(frozen=True)
class Preparation:
    """What a character may prepare today, per class, and what is prepared.

    One class is the usual case; a wizard who is also a cleric prepares twice — from
    the book and from the list, each to its own limit — and each class's spells are
    recorded under ``choices["prepared.<Class>"]``.
    """

    sheet: Sheet
    classes: tuple[ClassPreparation, ...]
    always: tuple[str, ...]

    @property
    def limit(self) -> int:
        """How many in all."""
        return sum(c.limit for c in self.classes)

    @property
    def from_book(self) -> bool:
        """True when every class prepares from the spellbook."""
        return all(c.from_book for c in self.classes)

    @property
    def pool(self) -> tuple[Offer, ...]:
        """Every spell any class may prepare."""
        seen: dict[str, Offer] = {}
        for c in self.classes:
            for offer in c.pool:
                seen.setdefault(offer.name.lower(), offer)
        return tuple(seen.values())

    @property
    def chosen(self) -> tuple[str, ...]:
        """Everything prepared."""
        return tuple(ref for c in self.classes for ref in c.chosen)

    @property
    def options(self) -> tuple[str, ...]:
        """The pool as references."""
        return tuple(o.reference for o in self.pool)


def _sheet(pc: PlayerCharacter, catalogue: Catalogue) -> Sheet:
    return build_sheet(pc, catalogue.compendium, source_set=catalogue.source_set)


def _max_level(sheet: Sheet) -> int:
    levels = [s.level for s in sheet.slots]
    if sheet.pact is not None:
        levels.append(sheet.pact.level)
    return max(levels, default=0)


def _pool(
    pc: PlayerCharacter, catalogue: Catalogue, sheet: Sheet, class_name: str, from_book: bool
) -> dict[str, Offer]:
    pool: dict[str, Offer] = {}
    if from_book:
        for option in pc.spells.known:
            offer = catalogue.find("spell", option)
            if offer is not None and offer.data.get("level", 0) >= 1:
                pool.setdefault(offer.name.lower(), offer)
        return pool
    entry = next((c for c in pc.classes if c.name == class_name), None)
    if entry is None:
        return pool
    cls = catalogue.find("class", ChosenOption(name=entry.name, source=entry.source))
    if cls is None:
        return pool
    own = sheet
    if len(pc.classes) > 1:
        own = _sheet(pc.model_copy(update={"classes": [entry]}), catalogue)
    subclass = (
        next((o for o in catalogue.subclasses(cls) if o.matches(entry.subclass)), None)
        if entry.subclass is not None
        else None
    )
    for offer in catalogue.class_spells(cls, _max_level(own), subclass):
        if offer.data.get("level", 0) >= 1:
            pool.setdefault(offer.name.lower(), offer)
    return pool


def preparation(pc: PlayerCharacter, catalogue: Catalogue) -> Preparation:
    """The character's preparation over one world's books."""
    sheet = _sheet(pc, catalogue)
    if not sheet.preparing:
        raise PrepareError("prepare.error.nothing")
    always = tuple(s.name for s in sheet.spells if s.always)
    skip = {name.lower() for name in always}
    split = len(sheet.preparing) > 1
    classes = []
    for prep in sheet.preparing:
        pool = _pool(pc, catalogue, sheet, prep.class_name, prep.from_book)
        offers = sorted(
            (o for name, o in pool.items() if name not in skip),
            key=lambda o: (int(o.data.get("level", 0)), o.name.lower()),
        )
        by_name = {o.name.lower(): o.reference for o in offers}
        if split:
            recorded = pc.choices.get(f"{PREPARED_PREFIX}{prep.class_name}") or []
            names = [ChosenOption.model_validate(v).name.lower() for v in recorded]
        else:
            names = [o.name.lower() for o in pc.spells.prepared]
        chosen = tuple(dict.fromkeys(by_name[n] for n in names if n in by_name))
        classes.append(
            ClassPreparation(prep.class_name, prep.limit, prep.from_book, tuple(offers), chosen)
        )
    return Preparation(sheet, tuple(classes), always)


class Preparations:
    """Prepared spells on stored characters, for their player or the gamemaster."""

    def __init__(self, drafts: Drafts, store: GameStateStore) -> None:
        """Bind to the drafts service (for catalogues and access) and the library."""
        self._drafts = drafts
        self._store = store

    def _ready(self, identity: Identity, character_id: str) -> PlayerCharacter:
        pc = self._drafts.characters.get_for(identity, character_id)
        if pc.status is not DraftStatus.READY:
            raise PrepareError("prepare.error.nothing")
        return pc

    def get(self, identity: Identity, character_id: str) -> tuple[PlayerCharacter, Preparation]:
        """The character and what it may prepare."""
        pc = self._ready(identity, character_id)
        return pc, preparation(pc, self._drafts.planner(pc).catalogue)

    def save(
        self, identity: Identity, character_id: str, chosen: Mapping[str, Sequence[str]]
    ) -> tuple[PlayerCharacter, Preparation]:
        """Prepare each class's ``chosen`` (by class name); refused past a limit or off a list."""
        pc, prep = self.get(identity, character_id)
        picked: dict[str, list[str]] = {}
        for cls in prep.classes:
            allowed = {ref.lower(): ref for ref in cls.options}
            mine: list[str] = []
            for value in chosen.get(cls.class_name, ()):
                ref = allowed.get(value.strip().lower())
                if value.strip() and ref is None:
                    raise PrepareError("prepare.error.not_on_list", spell=value)
                if ref is not None and ref not in mine:
                    mine.append(ref)
            if len(mine) > cls.limit:
                raise PrepareError("prepare.error.too_many", n=cls.limit)
            picked[cls.class_name] = mine
        catalogue = self._drafts.planner(pc).catalogue
        split = len(prep.classes) > 1

        def option(ref: str) -> ChosenOption | None:
            offer = catalogue.find("spell", ref)
            return ChosenOption(name=offer.name, source=offer.source) if offer else None

        def change(record: PlayerCharacter) -> None:
            prepared: dict[str, ChosenOption] = {}
            choices = {k: v for k, v in record.choices.items() if not k.startswith(PREPARED_PREFIX)}
            for class_name, refs in picked.items():
                options = [o for ref in refs if (o := option(ref)) is not None]
                for o in options:
                    prepared.setdefault(o.name.lower(), o)
                if split:
                    choices[f"{PREPARED_PREFIX}{class_name}"] = [o.reference for o in options]
            record.choices = choices
            record.spells = record.spells.model_copy(update={"prepared": list(prepared.values())})

        saved = self._store.characters.mutate(character_id, change)
        return saved, preparation(saved, catalogue)


__all__ = ["ClassPreparation", "PrepareError", "Preparation", "Preparations", "preparation"]
