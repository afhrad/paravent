"""A character's story: what it has done and whom it knows, gathered in one place.

Its quests (finished, failed and open), its standing with the world's factions, its
marks of prestige, its contacts, its followers, its bastion, and a timeline from the
log of the moments that changed it: experience awarded, levels gained, renown won or
lost, marks of prestige granted, called in or taken back, contacts made or called on,
spells written into its book. Read by its player and the gamemaster.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.models.gamestate import Bastion, Contact, Faction, Follower, Mark, Quest
from app.models.pc import PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry
from app.store.gamestate import GameStateStore

# What of the character's log tells its story: experience and levels, and these.
STORY_LINES = ("renown:", "contact:", "learn:spell:", "mark:", "mark_spent:", "mark_gone:")
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass
class Story:
    """Everything the story page shows of one character."""

    quests: list[Quest] = field(default_factory=list)
    factions: list[tuple[Faction, int]] = field(default_factory=list)
    contacts: list[Contact] = field(default_factory=list)
    followers: list[Follower] = field(default_factory=list)
    marks: list[Mark] = field(default_factory=list)
    bastion: Bastion | None = None
    timeline: list[ProtocolEntry] = field(default_factory=list)  # newest first


def tells_story(entry: ProtocolEntry) -> bool:
    """Whether a log entry is one of the moments the story keeps."""
    if entry.kind in (EntryKind.EXPERIENCE, EntryKind.LEVEL_UP):
        return True
    return entry.kind == EntryKind.CUSTOM and entry.what.startswith(STORY_LINES)


class StoryService:
    """Gathers a character's story from the library and the log."""

    def __init__(
        self,
        store: GameStateStore,
        *,
        quests: Callable[[str, list[str]], list[Quest]],
        standing: Callable[[str, str | None], list[tuple[Faction, int]]],
        log: Callable[[str, str | None, datetime], list[ProtocolEntry]],
    ) -> None:
        """``quests(world, characters)`` the quests they are on; ``standing(character,
        world)`` its factions with its renown; ``log(subject, world, since)`` what the
        log holds about a subject since a moment, newest first."""
        self._store = store
        self._quests = quests
        self._standing = standing
        self._log = log

    def of(self, pc: PlayerCharacter, *, gamemaster: bool, limit: int = 60) -> Story:
        """The character's story; a player sees only the quests shown to it."""
        world = pc.world_id
        quests: list[Quest] = []
        if world:
            mine = self._quests(world, [pc.id])
            quests = (
                mine
                if not gamemaster
                else [
                    q
                    for q in self._store.quests.list(lambda q: q.world_id == world)
                    if not q.characters or pc.id in q.characters
                ]
            )
            quests.sort(key=lambda q: (q.status == "open", q.updated_at), reverse=True)
        contacts = sorted(
            self._store.contacts.list(lambda c: c.character_id == pc.id),
            key=lambda c: c.created_at,
        )
        followers = sorted(
            self._store.followers.list(lambda f: f.character_id == pc.id),
            key=lambda f: f.created_at,
        )
        timeline = [e for e in self._log(f"character:{pc.id}", world, EPOCH) if tells_story(e)][
            :limit
        ]
        return Story(
            quests=quests,
            factions=self._standing(pc.id, world),
            contacts=contacts,
            followers=followers,
            marks=sorted(
                self._store.marks.list(lambda m: m.character_id == pc.id),
                key=lambda m: m.created_at,
            ),
            bastion=self._store.bastions.find(pc.id),
            timeline=timeline,
        )


__all__ = ["STORY_LINES", "Story", "StoryService", "tells_story"]
