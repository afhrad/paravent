"""Encounters the gamemaster prepares: which monsters, how many, and how hard that is.

An encounter belongs to a world. Its monsters are references into the compendium
(``Goblin|MM``), counted, never copied statblocks — the XP is looked up each time, so a
corpus update re-rates the fight. The party it is rated against is the characters in
that world, or a size and level the gamemaster states when planning ahead of the table.

Running the fight (initiative, hit points, turns) is the combat tracker's job; this
module only plans it.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.models.gamestate import Encounter, EncounterMonster, PartySpec, Scope, SourceSet, World
from app.services import scope
from app.services.difficulty import DifficultyRules, Rating, rules_for
from app.services.i18n import Translatable
from app.services.naming import Naming, NamingError, creature_races
from app.store.gamestate import GameStateStore
from app.store.projections import MonsterProjection, project_monster

MAX_COUNT = 99
NAME_LIMIT = 120


class EncounterError(Translatable):
    """A change the encounter cannot take: no name, an unknown monster, an unknown world."""


class EncounterNotFound(LookupError):
    """No encounter with that id."""


@dataclass(frozen=True)
class PlannedMonster:
    """One line of the plan, with what the compendium says about it.

    ``monster`` is None when the reference no longer resolves — a book removed from the
    corpus, a renamed entry. The line stays, visibly unrated, rather than vanishing.
    """

    index: int
    planned: EncounterMonster
    monster: MonsterProjection | None

    @property
    def xp(self) -> int | None:
        """XP for one of these, or None when unrated."""
        if self.monster is None or self.monster.cr is None:
            return None
        return self.monster.cr.xp


@dataclass(frozen=True)
class PartyMember:
    """Someone the fight is rated against: a character, or one of a stated party."""

    name: str | None
    level: int


@dataclass(frozen=True)
class EncounterView:
    """Everything the builder shows."""

    encounter: Encounter
    world: World | None
    lines: tuple[PlannedMonster, ...]
    party: tuple[PartyMember, ...]
    party_stated: bool
    rating: Rating
    rules_inferred: str
    # What the fight can start: the sound desk's sounds and scenes, by name.
    sound_names: tuple[str, ...] = ()
    # The creature-builder designs it plans, by name (casefolded) to design id, so each
    # line can open its creature in the builder.
    designs: Mapping[str, str] = field(default_factory=dict)

    @property
    def unrated(self) -> int:
        """How many planned creatures carry no XP (no CR, or no longer in the data)."""
        return sum(line.planned.count for line in self.lines if line.xp is None)

    @property
    def source_set(self) -> SourceSet | None:
        """The world's books, which the monster picker searches."""
        return self.world.source_set if self.world is not None else None


class EncounterService:
    """Encounters in the library, rated against the compendium."""

    def __init__(
        self,
        store: GameStateStore,
        compendium: Any,
        rules: DifficultyRules | None = None,
        renamed: Callable[[str, str, str, str | None], object] | None = None,
        unpublished: Callable[[str, str], dict[str, Any] | None] | None = None,
    ) -> None:
        """Bind to the library, the loaded compendium and the difficulty tables.

        ``renamed(world_id, old, new, adventure_id)`` is told about a rename, so references
        follow. ``unpublished(name, source)`` is the statblock of a creature-builder design
        not yet in the compendium (Claude's draft, planned in a draft adventure), so its
        fight is rated before the gamemaster accepts it.
        """
        self._store = store
        self._compendium = compendium
        self._rules = rules or DifficultyRules()
        self._renamed = renamed
        self._unpublished = unpublished
        self._index_for: object | None = None
        self._index: dict[str, MonsterProjection] = {}

    # -- reading ---------------------------------------------------------------------

    @property
    def difficulty(self) -> DifficultyRules:
        """The difficulty tables encounters are rated by."""
        return self._rules

    def list(self, world_id: str | None = None) -> list[Encounter]:
        """Every encounter, or a world's, newest first."""
        found = self._store.encounters.list(
            (lambda e: e.world_id == world_id) if world_id else None
        )
        return sorted(found, key=lambda e: e.updated_at, reverse=True)

    def get(self, encounter_id: str) -> Encounter:
        """One encounter, or :class:`EncounterNotFound`."""
        encounter = self._store.encounters.find(encounter_id)
        if encounter is None:
            raise EncounterNotFound(encounter_id)
        return encounter

    def monster(self, name: str, source: str) -> MonsterProjection | None:
        """The loaded creature with exactly this name and source."""
        projections = self._compendium.projections
        if projections is not self._index_for:
            self._index = {f"{m.name}|{m.source}".lower(): m for m in projections.monsters}
            self._index_for = projections
        found = self._index.get(f"{name}|{source}".lower())
        if found is None and self._unpublished is not None:
            statblock = self._unpublished(name, source)
            return project_monster(statblock) if statblock is not None else None
        return found

    def view(self, encounter: Encounter) -> EncounterView:
        """The plan, its party and its rating."""
        world = self._store.worlds.find(encounter.world_id) if encounter.world_id else None
        lines = tuple(
            PlannedMonster(i, planned, self.monster(planned.name, planned.source))
            for i, planned in enumerate(encounter.monsters)
        )
        party, stated = self._party(encounter)
        inferred = rules_for(world.source_set if world else None)
        rating = self._rate(encounter.rules or inferred, [m.level for m in party], lines)
        planned = {m.name.casefold() for m in encounter.monsters}
        designs = {
            d.name.casefold(): d.id
            for d in self._store.creatures.list()
            if d.name.casefold() in planned and not d.revision_of
        }
        return EncounterView(
            encounter, world, lines, party, stated, rating, inferred, designs=designs
        )

    def rate_plan(
        self, world: World | None, monsters: Sequence[EncounterMonster], levels: Sequence[int]
    ) -> tuple[Rating, tuple[PlannedMonster, ...]]:
        """A plan rated for a party of ``levels``, nothing saved (documentation/MCP.md §4)."""
        lines = tuple(
            PlannedMonster(i, planned, self.monster(planned.name, planned.source))
            for i, planned in enumerate(monsters)
        )
        rules = rules_for(world.source_set if world else None)
        return self._rate(rules, levels, lines), lines

    def _rate(self, rules: str, levels: Sequence[int], lines: Sequence[PlannedMonster]) -> Rating:
        # Allies join the party at a level equal to their challenge rating (at least 1),
        # as the combat tracker rates them (COMBAT.md §3); foes are rated by their XP.
        allies = [
            max(1, int(line.monster.cr.value))
            for line in lines
            if line.planned.ally and line.monster is not None and line.monster.cr is not None
            for _ in range(line.planned.count)
        ]
        return self._rules.rate(
            rules,
            [*levels, *allies],
            [
                (line.xp, line.planned.count)
                for line in lines
                if line.xp is not None and not line.planned.ally
            ],
        )

    def _party(self, encounter: Encounter) -> tuple[tuple[PartyMember, ...], bool]:
        if encounter.party is not None:
            spec = encounter.party
            return tuple(PartyMember(None, spec.level) for _ in range(spec.size)), True
        if not encounter.world_id:
            return (), False
        # An adventure written for some of the world's characters rates its fights for
        # them (documentation/MCP.md §6).
        adventure = (
            self._store.adventures.find(encounter.adventure_id) if encounter.adventure_id else None
        )
        chosen = set(adventure.party) if adventure is not None and adventure.party else None
        characters = self._store.characters.list(
            lambda pc: (
                pc.world_id == encounter.world_id
                and pc.total_level > 0
                and (chosen is None or pc.id in chosen)
            )
        )
        members = sorted(
            (PartyMember(pc.name or "?", pc.total_level) for pc in characters),
            key=lambda m: (m.name or "").casefold(),
        )
        return tuple(members), False

    # -- writing ---------------------------------------------------------------------

    def _check_world(self, world_id: str | None) -> str | None:
        if world_id and self._store.worlds.find(world_id) is None:
            raise EncounterError("encounters.error.world")
        return world_id or None

    @staticmethod
    def _clean_name(name: str) -> str:
        cleaned = " ".join(name.split())[:NAME_LIMIT]
        if not cleaned:
            raise EncounterError("encounters.error.name")
        return cleaned

    def create(self, name: str, world_id: str | None, scope: Scope | None = None) -> Encounter:
        """A new, empty encounter — in a world, or placed in an adventure or chapter."""
        if scope is not None:
            world_id = scope.world_id
        return self._store.encounters.create(
            Encounter(
                name=self._clean_name(name),
                world_id=self._check_world(world_id),
                adventure_id=scope.adventure_id if scope else None,
                chapter_id=scope.chapter_id if scope else None,
            )
        )

    def within(self, here: Scope) -> builtins.list[Encounter]:
        """The encounters inside a world, adventure or chapter, newest first."""
        return [e for e in self.list(here.world_id) if e.scope and scope.within(e.scope, here)]

    def find(self, name: str, here: Scope) -> Encounter | None:
        """The encounter called ``name`` nearest to ``here``."""
        return scope.nearest(
            self._store.encounters.list(), name, here, lambda e: e.scope, lambda e: e.name
        )

    def update(
        self,
        encounter_id: str,
        *,
        name: str,
        scope: Scope | None,
        notes: str,
        rules: str | None,
        party: PartySpec | None,
        sound: str = "",
    ) -> Encounter:
        """Save the encounter's settings; the monster list is changed separately.

        ``scope`` None takes the encounter out of any world. A new name follows into
        the world's texts (``{@encounter …}``, ``::: encounter …``).
        """
        before = self.get(encounter_id)
        saved = self._store.encounters.update(
            encounter_id,
            name=self._clean_name(name),
            world_id=self._check_world(scope.world_id if scope else None),
            adventure_id=scope.adventure_id if scope else None,
            chapter_id=scope.chapter_id if scope else None,
            notes=notes.strip(),
            rules=rules if rules in ("classic", "one") else None,
            party=party,
            sound=sound,
        )
        if self._renamed is not None and saved.world_id and saved.name != before.name:
            self._renamed(saved.world_id, before.name, saved.name, saved.adventure_id)
        return saved

    def set_monsters(
        self, encounter_id: str, monsters: builtins.list[EncounterMonster]
    ) -> Encounter:
        """Replace the whole plan (the MCP tools write an encounter at once)."""
        self.get(encounter_id)
        return self._store.encounters.update(encounter_id, monsters=monsters)

    def delete(self, encounter_id: str) -> None:
        """Delete an encounter."""
        self.get(encounter_id)
        self._store.encounters.delete(encounter_id)

    def add_monster(self, encounter_id: str, ref: str, count: int = 1) -> Encounter:
        """Add ``count`` of the creature ``Name|SOURCE``; a second add raises the count."""
        name, _, source = ref.partition("|")
        monster = self.monster(name.strip(), source.strip())
        if monster is None:
            raise EncounterError("encounters.error.monster", ref=ref)
        self.get(encounter_id)
        count = max(1, min(MAX_COUNT, count))

        def add(encounter: Encounter) -> None:
            for planned in encounter.monsters:
                same = (planned.name, planned.source) == (monster.name, monster.source)
                if same and not planned.ally:
                    planned.count = min(MAX_COUNT, planned.count + count)
                    return
            encounter.monsters.append(
                EncounterMonster(name=monster.name, source=monster.source, count=count)
            )

        return self._store.encounters.mutate(encounter_id, add)

    def remember_table(self, encounter_id: str, table: str) -> Encounter:
        """Keep the random-encounter table rolled on, offered first the next time."""
        return self._store.encounters.update(encounter_id, roll_table=table)

    def apply_roll(
        self, encounter_id: str, monsters: Sequence[tuple[str, str, int]], note: str
    ) -> Encounter:
        """A table roll's creatures replace the plan; what was rolled heads the notes."""
        self.get(encounter_id)

        def change(encounter: Encounter) -> None:
            encounter.monsters = [
                EncounterMonster(name=name, source=source, count=max(1, min(MAX_COUNT, count)))
                for name, source, count in monsters
            ]
            encounter.notes = f"{note}\n\n{encounter.notes}".strip() if note else encounter.notes

        return self._store.encounters.mutate(encounter_id, change)

    def set_ally(self, encounter_id: str, index: int, ally: bool) -> Encounter:
        """Put line ``index`` on the characters' side, or back against them."""
        encounter = self.get(encounter_id)
        if not 0 <= index < len(encounter.monsters):
            raise EncounterError("encounters.error.line")

        def change(encounter: Encounter) -> None:
            encounter.monsters[index].ally = ally

        return self._store.encounters.mutate(encounter_id, change)

    def set_label(self, encounter_id: str, index: int, label: str) -> Encounter:
        """Show line ``index`` under another name — a reskin; empty shows the book's."""
        encounter = self.get(encounter_id)
        if not 0 <= index < len(encounter.monsters):
            raise EncounterError("encounters.error.line")
        shown = " ".join(label.split())[:NAME_LIMIT] or None

        def change(encounter: Encounter) -> None:
            line = encounter.monsters[index]
            line.label = shown if shown and shown != line.name else None

        return self._store.encounters.mutate(encounter_id, change)

    def roll_label(
        self,
        encounter_id: str,
        index: int,
        naming: Naming,
        edition: str,
        style: str = "",
        language: str = "",
    ) -> Encounter:
        """Show line ``index`` under a name rolled in ``style``, or in the style its
        creature finds by its race, subtype or name (NAMES.md §5): an orc gets an
        orc's name. A group gets one name for each, comma-separated, as many as fit the
        label; the fight gives them out in order and numbers the rest. Names already in
        the world or the plan are not given twice."""
        encounter = self.get(encounter_id)
        if not 0 <= index < len(encounter.monsters):
            raise EncounterError("encounters.error.line")
        planned = encounter.monsters[index]
        if style:
            key = style
        else:
            monster = self.monster(planned.name, planned.source)
            races = creature_races(
                planned.name, monster.kind.tags if monster else (), planned.reskin or ""
            )
            key = naming.for_creature(races, edition, language).key
        world = encounter.world_id
        avoid = [
            *(n for i, m in enumerate(encounter.monsters) if i != index for n in m.names),
            *(m.reskin for i, m in enumerate(encounter.monsters) if i != index and m.reskin),
            *(n.name for n in self._store.npcs.list() if n.scope.world_id == world),
            *(c.name for c in self._store.characters.list() if c.world_id == world),
        ]
        try:
            names = naming.names(
                key, count=planned.count, avoid=avoid, spread=True, language=language
            )
        except NamingError as exc:
            raise EncounterError("naming.error.roll") from exc
        if not names:
            raise EncounterError("naming.error.roll")
        if planned.count == 1:
            return self.set_label(encounter_id, index, names[0])
        listed: list[str] = []
        for name in dict.fromkeys(names):
            if len(", ".join([*listed, name])) > NAME_LIMIT:
                break
            listed.append(name)
        # A list of one would read as a reskin; a trailing comma keeps it a name.
        return self.set_label(
            encounter_id, index, ", ".join(listed) + ("," if len(listed) == 1 else "")
        )

    def set_count(self, encounter_id: str, index: int, count: int) -> Encounter:
        """Set how many of line ``index`` there are; 0 removes the line."""
        encounter = self.get(encounter_id)
        if not 0 <= index < len(encounter.monsters):
            raise EncounterError("encounters.error.line")

        def change(encounter: Encounter) -> None:
            if count <= 0:
                del encounter.monsters[index]
            else:
                encounter.monsters[index].count = min(MAX_COUNT, count)

        return self._store.encounters.mutate(encounter_id, change)


__all__ = [
    "EncounterError",
    "EncounterNotFound",
    "EncounterService",
    "EncounterView",
    "PartyMember",
    "PlannedMonster",
]
