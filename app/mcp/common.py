"""What the MCP tools share: creatures and parties resolved in a world, a rating as an
answer, and service errors as tool errors (documentation/MCP.md §4)."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from fractions import Fraction
from typing import TYPE_CHECKING

from mcp.server.mcpserver.exceptions import ToolError

from app.mcp.models import MonsterInput, RatedMonster, RatingAnswer
from app.models.gamestate import EncounterMonster, World
from app.services.adventures import AdventureNotFound, EditConflict
from app.services.creature_builder import HOMEBREW_SOURCE
from app.services.difficulty import Rating
from app.services.encounters import EncounterNotFound, PlannedMonster
from app.services.i18n import Translatable
from app.services.npcs import ObjectNotFound
from app.services.worlds import WorldNotFound

if TYPE_CHECKING:
    from app.store.registry import StoreRegistry


@contextmanager
def tool_errors() -> Iterator[None]:
    """Turn what the services refuse into an answer Claude can act on."""
    try:
        yield
    except EditConflict as exc:
        raise ToolError(
            "The text changed since you read it (the gamemaster edited it). Nothing was "
            "saved. Merge your change into the current text below and write again with "
            f"its version.\n\n--- current text ---\n{exc.current}"
        ) from exc
    except Translatable as exc:
        raise ToolError(str(exc)) from exc
    except WorldNotFound as exc:
        raise ToolError(f"No world with id {exc}") from exc
    except (AdventureNotFound, EncounterNotFound, ObjectNotFound) as exc:
        raise ToolError(f"Nothing with id {exc} (or it was deleted)") from exc


def cr_text(value: Fraction | None) -> str | None:
    """A challenge rating as the books write it: ``1/4``, ``3``."""
    if value is None:
        return None
    return str(value.numerator) if value.denominator == 1 else str(value)


def resolve_monsters(
    registry: StoreRegistry, world: World, monsters: Sequence[MonsterInput]
) -> tuple[list[EncounterMonster], list[str]]:
    """Each creature resolved in the world's books; the names that are not in them."""
    resolver = registry.references.resolver()
    planned, unresolved = [], []
    for wanted in monsters:
        found = resolver.resolve_parts("monster", wanted.name, wanted.source, world.source_set)
        design = (
            registry.creatures.named(wanted.name, world.id)
            if found.entry is None and (wanted.source or HOMEBREW_SOURCE) == HOMEBREW_SOURCE
            else None
        )
        if design is not None:  # the world's own creature, not yet published (MCP.md §10)
            planned.append(
                EncounterMonster(
                    name=design.name,
                    source=HOMEBREW_SOURCE,
                    count=wanted.count,
                    ally=wanted.ally,
                    label=" ".join((wanted.label or "").split()) or None,
                )
            )
            continue
        if found.entry is None:
            unresolved.append(
                wanted.name if not wanted.source else f"{wanted.name}|{wanted.source}"
            )
            continue
        planned.append(
            EncounterMonster(
                name=str(found.entry.name),
                source=str(found.entry.source),
                count=wanted.count,
                ally=wanted.ally,
                label=" ".join((wanted.label or "").split()) or None,
            )
        )
    return planned, unresolved


def party_levels(
    registry: StoreRegistry,
    world: World,
    character_ids: Sequence[str] | None,
    size: int | None = None,
    level: int | None = None,
) -> list[int]:
    """The levels a fight is rated against: the characters named, else a stated size
    and level, else every character of the world."""
    present = {pc.id: pc for pc in registry.characters.in_world(world.id)}
    if character_ids:
        return [present[i].total_level for i in character_ids if i in present]
    if size and level:
        return [level] * size
    return [pc.total_level for pc in present.values() if pc.total_level > 0]


def rating_answer(
    rating: Rating,
    lines: Sequence[PlannedMonster],
    levels: Sequence[int],
    unresolved: Sequence[str] = (),
) -> RatingAnswer:
    """A rating as the tools report it."""
    return RatingAnswer(
        rules=rating.rules,
        difficulty=rating.level,
        base_xp=rating.base_xp,
        multiplier=rating.multiplier,
        adjusted_xp=rating.adjusted_xp,
        bands={band.key: band.xp for band in rating.bands},
        party_levels=list(levels),
        monsters=[
            RatedMonster(
                name=line.planned.name,
                source=line.planned.source,
                label=line.planned.label,
                count=line.planned.count,
                ally=line.planned.ally,
                cr=cr_text(line.monster.cr.value) if line.monster and line.monster.cr else None,
                xp_each=line.xp,
            )
            for line in lines
        ],
        unresolved=list(unresolved),
    )


__all__ = ["cr_text", "party_levels", "rating_answer", "resolve_monsters", "tool_errors"]
