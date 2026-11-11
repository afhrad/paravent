"""The MCP server's campaign memory: what happened in a world so far
(documentation/MCP.md §4), so a new adventure can follow on from the last session.

Read from what Paravent already keeps — the adventures and how far their chapters got,
the fights the tracker ran (recapped from the log, PROTOCOL.md §8), and the experience
and levels the log recorded — grouped by the day they happened, newest first.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from app.mcp.common import tool_errors
from app.mcp.models import (
    AdventureRecord,
    CampaignHistory,
    ChapterState,
    FightRecord,
    NoteRecord,
    SessionRecord,
)
from app.models.gamestate import Combat
from app.models.protocol import EntryKind, ProtocolEntry
from app.services.recap import recap
from app.store.protocol import log_key

if TYPE_CHECKING:
    from app.store.registry import StoreRegistry


def _day(moment: datetime) -> str:
    return moment.astimezone().date().isoformat()


def progress_line(entry: ProtocolEntry) -> str | None:
    """An experience award or a level taken, as one line; None for anything else."""
    who = entry.subject_name or "?"
    if entry.kind is EntryKind.EXPERIENCE:
        reason = f" ({entry.note})" if entry.note else ""
        return f"{who} gained {entry.amount or 0} XP{reason}"
    if entry.kind is EntryKind.LEVEL_UP:
        _, _, rest = entry.what.partition(":")
        name, _, level = rest.partition(":")
        return f"{who} reached {name} level {level}" if level else f"{who} gained a level"
    return None


def register_history(server: MCPServer, stores: Callable[[], StoreRegistry]) -> None:
    """Add `campaign_history` to ``server``."""

    @server.tool()
    def campaign_history(
        world_id: str,
        sessions: Annotated[int, Field(ge=1, le=50, description="How many days of play")] = 10,
    ) -> CampaignHistory:
        """What happened in a world so far: its adventures and how far each chapter got,
        and per day of play the gamemaster's own notes (what happened, whom the party met,
        what is still open — the story), the fights run (who was defeated, who went down,
        the biggest hit) and the experience and levels gained. Read it before writing what comes
        next, so the story follows on from the table."""
        registry = stores()
        with tool_errors():
            world = registry.worlds.get(world_id)
        adventures = registry.adventures.adventures(world.id)
        names = {a.id: a.name for a in adventures}
        chapters = {
            ch.id: ch.title for a in adventures for ch in registry.adventures.chapters(a.id)
        }
        characters = registry.characters.in_world(world.id)
        by_id = {pc.id: pc.name for pc in characters}

        fights: dict[str, list[FightRecord]] = defaultdict(list)
        for combat in sorted(registry.combats.list(world_id=world.id), key=lambda c: c.created_at):
            fights[_day(combat.created_at)].append(fight_record(registry, combat, names, chapters))

        progress: dict[str, list[str]] = defaultdict(list)
        for entry in sorted(registry.protocol.store.entries(log_key(world.id)), key=lambda e: e.at):
            line = progress_line(entry)
            if line is not None:
                progress[_day(entry.at)].append(line)

        notes: dict[str, list[NoteRecord]] = defaultdict(list)
        for note in reversed(registry.session_notes.list(world.id)):
            notes[note.day.isoformat()].append(
                NoteRecord(adventure=names.get(note.adventure_id or ""), text=note.text)
            )

        days = sorted(set(fights) | set(progress) | set(notes), reverse=True)
        return CampaignHistory(
            world_id=world.id,
            characters=[
                f"{pc.name}, level {pc.total_level}"
                + (f", {pc.experience} XP" if pc.experience is not None else "")
                for pc in characters
                if pc.total_level > 0
            ],
            adventures=[
                AdventureRecord(
                    id=a.id,
                    name=a.name,
                    status=a.status,
                    summary=a.summary,
                    level_start=a.level.start if a.level else None,
                    level_end=a.level.end if a.level else None,
                    party=[by_id[i] for i in a.party or [] if i in by_id],
                    chapters=[
                        ChapterState(title=ch.title, status=ch.status)
                        for ch in registry.adventures.chapters(a.id)
                    ],
                )
                for a in adventures
            ],
            sessions=[
                SessionRecord(
                    date=day,
                    notes=notes.get(day, []),
                    fights=fights.get(day, []),
                    progress=progress.get(day, []),
                )
                for day in days[:sessions]
            ],
            older_sessions=max(0, len(days) - sessions),
        )


def fight_record(
    registry: StoreRegistry, combat: Combat, adventures: dict[str, str], chapters: dict[str, str]
) -> FightRecord:
    """One fight, recapped from the log as the recap page does."""
    service = registry.combats
    summary = service.summary(combat)
    told = recap([combat], service, lambda c: registry.protocol_reader.fight(c.id, c.world_id))
    encounter = (
        registry.gamestate.encounters.find(combat.encounter_id) if combat.encounter_id else None
    )
    biggest = told.biggest
    return FightRecord(
        name=combat.name,
        encounter=encounter.name if encounter else None,
        adventure=adventures.get(encounter.adventure_id or "") if encounter else None,
        chapter=chapters.get(encounter.chapter_id or "") if encounter else None,
        finished=combat.ended is not None,
        rounds=summary.rounds,
        defeated=[label for label, _xp in summary.defeated],
        xp=summary.xp,
        party=list(summary.party),
        party_down=list(summary.party_down),
        biggest_hit=(
            f"{biggest.attacker} hit {biggest.target} for {biggest.amount} ({biggest.what})"
            if biggest
            else None
        ),
    )


__all__ = ["fight_record", "progress_line", "register_history"]
