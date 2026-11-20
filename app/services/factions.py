"""A world's factions and the characters' renown with them (the guides' renown rules).

The gamemaster makes a faction by hand, or from the books: a renown table a world's book
prints ("Dale Renown", "Emerald Enclave Renown") gives the faction its name and its
rewards by renown score, and the 2014 guide's *Examples of Faction Ranks* gives five
factions their titles. Each character's renown with a faction is raised or lowered by
the gamemaster, logged; a character sees, on its downtime page, its renown, the title
it holds and the rewards the faction's table grants at that score. A contact may belong
to a faction.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.models.gamestate import Faction, FactionRank
from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.book_tables import Rows
from app.services.i18n import Translatable
from app.services.protocol import ProtocolService
from app.services.text import strip_tags
from app.services.trash import Trash
from app.store.gamestate import GameStateStore

# The most renown a character can hold with a faction, and the least.
MOST = 1000
_SCORE = re.compile(r"^\s*(\d+)")


class FactionError(Translatable):
    """A change to a faction the rules refuse."""


@dataclass(frozen=True)
class Reward:
    """What a faction grants from a renown score on."""

    renown: int
    text: str


def rewards_of(rows: Rows) -> list[Reward]:
    """A renown table's rewards ("3+" → 3), plain text."""
    out = []
    for row in rows:
        found = _SCORE.match(strip_tags(row[0])) if row else None
        if found is not None and len(row) > 1:
            out.append(Reward(int(found[1]), " ".join(strip_tags(row[1]).split())))
    return out


def ranks_from(columns: Sequence[str], rows: Rows) -> dict[str, list[FactionRank]]:
    """The 2014 guide's *Examples of Faction Ranks*: each faction column's titles by the
    renown in the first column."""
    out: dict[str, list[FactionRank]] = {}
    for n, faction in enumerate(columns[1:], start=1):
        ranks = []
        for row in rows:
            found = _SCORE.match(strip_tags(row[0])) if row else None
            if found is not None and len(row) > n and strip_tags(row[n]).strip():
                ranks.append(FactionRank(renown=int(found[1]), title=strip_tags(row[n]).strip()))
        out[strip_tags(faction).strip()] = ranks
    return out


def title_of(faction: Faction, renown: int) -> str:
    """The highest title the faction gives at ``renown``; "" for none."""
    held = [r for r in faction.ranks if r.renown <= renown]
    return max(held, key=lambda r: r.renown).title if held else ""


def granted(rewards: Sequence[Reward], renown: int) -> list[Reward]:
    """The rewards a renown score has reached."""
    return [r for r in rewards if r.renown <= renown]


class FactionService:
    """Each world's factions (``library/factions.json``); renown changes logged."""

    def __init__(self, store: GameStateStore, protocol: ProtocolService | None) -> None:
        self._store = store
        self._protocol = protocol

    def of(self, world_id: str) -> list[Faction]:
        """A world's factions, by name."""
        found = self._store.factions.list(lambda f: f.world_id == world_id)
        return sorted(found, key=lambda f: f.name.lower())

    def get(self, faction_id: str, world_id: str | None = None) -> Faction:
        faction = self._store.factions.find(faction_id)
        if faction is None or (world_id is not None and faction.world_id != world_id):
            raise FactionError("factions.error.unknown")
        return faction

    def create(
        self,
        world_id: str,
        name: str,
        *,
        table: str = "",
        ranks: Sequence[FactionRank] = (),
        note: str = "",
    ) -> Faction:
        name = " ".join(name.split())[:120]
        if not name:
            raise FactionError("factions.error.name")
        return self._store.factions.create(
            Faction(
                world_id=world_id,
                name=name,
                note=note.strip()[:4000],
                table=table,
                ranks=list(ranks),
            )
        )

    def edit(self, faction_id: str, world_id: str, name: str, note: str) -> Faction:
        faction = self.get(faction_id, world_id)
        name = " ".join(name.split())[:120]
        if not name:
            raise FactionError("factions.error.name")
        changed = faction.model_copy(update={"name": name, "note": note.strip()[:4000]})
        changed.touch()
        return self._store.factions.upsert(changed)

    def delete(self, faction_id: str, world_id: str) -> str | None:
        """Delete a faction, kept in the trash to restore with the quests and contacts it
        is taken from; the trash entry's id."""
        faction = self.get(faction_id, world_id)
        contacts = self._store.contacts.list(lambda c: c.faction_id == faction_id)
        quests = self._store.quests.list(lambda q: q.faction_id == faction_id)
        kept = Trash(self._store).keep(
            "faction",
            faction,
            links={"quests": [q.id for q in quests], "contacts": [c.id for c in contacts]},
        )
        self._store.factions.delete(faction_id)
        for contact in contacts:
            self._store.contacts.upsert(contact.model_copy(update={"faction_id": None}))
        for quest in quests:
            self._store.quests.upsert(quest.model_copy(update={"faction_id": None}))
        return kept

    def adjust(
        self, faction_id: str, world_id: str, pc: PlayerCharacter, delta: int, by: str, by_name: str
    ) -> Faction:
        """Raise or lower a character's renown with a faction (never below 0)."""
        faction = self.get(faction_id, world_id)
        if pc.world_id != world_id:
            raise FactionError("factions.error.unknown")
        held = faction.renown.get(pc.id, 0)
        total = max(0, min(MOST, held + delta))
        if total == held:
            return faction
        changed = faction.model_copy(update={"renown": {**faction.renown, pc.id: total}})
        changed.touch()
        saved = self._store.factions.upsert(changed)
        if self._protocol is not None:
            self._protocol.append(
                [
                    ProtocolEntry(
                        actor=by,
                        actor_name=by_name,
                        subject=f"character:{pc.id}",
                        subject_name=pc.name,
                        kind=EntryKind.CUSTOM,
                        what=f"renown:{faction.name.replace(':', ' ')}:{total - held:+d}:{total}",
                        visibility=Visibility.PUBLIC,
                        scope=Scope(world=world_id),
                    )
                ]
            )
        return saved

    def standing(self, character_id: str, world_id: str | None) -> list[tuple[Faction, int]]:
        """A character's factions in its world, with its renown: those it has renown
        with, and those its contacts belong to."""
        if not world_id:
            return []
        linked = {
            c.faction_id
            for c in self._store.contacts.list(lambda c: c.character_id == character_id)
            if c.faction_id
        }
        return [
            (f, f.renown.get(character_id, 0))
            for f in self.of(world_id)
            if f.renown.get(character_id) or f.id in linked
        ]


__all__ = [
    "MOST",
    "FactionError",
    "FactionService",
    "Reward",
    "granted",
    "ranks_from",
    "rewards_of",
    "title_of",
]
