"""Creatures over MCP (documentation/MCP.md §10): Claude drafts a creature in the
creature builder from a description, a world's setting and a challenge rating.

A design Claude makes is a **draft**: it carries the key that wrote it, the builder
marks it, and a publish leaves it out until the gamemaster accepts it. Claude changes
only drafts. Every answer is the builder's own working: the CR the design plays at by
the guide's method, the statblock with its numbers filled in, and the references in its
text that the world's books do not have.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from importlib.resources import files
from typing import TYPE_CHECKING, Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, ValidationError

from app.mcp.common import tool_errors
from app.mcp.models import CreatureBlockInput, CreatureDraft, CreatureProblem
from app.models.gamestate import CreatureBlock, CreatureDesign, CreatureFeature, Scope
from app.services.creature_builder import (
    ABILITIES,
    ALIGNMENTS,
    CONDITIONS,
    DAMAGE_TYPES,
    SIZES,
    SPEEDS,
    TYPES,
    CreatureNotFound,
)
from app.ui.context import Page
from app.ui.creature_pages import creature_url

if TYPE_CHECKING:
    from app.store.registry import StoreRegistry

CREATURE_GUIDE = files("app.mcp").joinpath("creature_guide.md")
# Problems of the chapter check that concern a creature's text: references only.
REFERENCE_KINDS = frozenset({"reference", "excluded"})


def _blocks(given: Sequence[CreatureBlockInput] | None) -> list[CreatureBlock] | None:
    if given is None:
        return None
    return [CreatureBlock(name=" ".join(b.name.split()), text=b.text.strip()) for b in given]


def _changes(**fields: Any) -> dict[str, Any]:
    """The fields Claude gave, checked against what the builder knows; None is untouched."""
    changes: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        if key == "size" and value not in SIZES:
            raise ToolError(f"size is one of {', '.join(SIZES)}")
        if key == "type" and value.lower() not in TYPES:
            raise ToolError(f"type is one of {', '.join(TYPES)}")
        if key == "alignment":
            if value not in ALIGNMENTS:
                raise ToolError(f"alignment is one of {', '.join(ALIGNMENTS)}")
            value = value.split()
        if key == "speed" and not set(value) <= set(SPEEDS):
            raise ToolError(f"speed takes {', '.join(SPEEDS)}")
        if key == "abilities" and not set(value) <= set(ABILITIES):
            raise ToolError(f"abilities are {', '.join(ABILITIES)}")
        if key == "saves" and not set(value) <= set(ABILITIES):
            raise ToolError(f"saves are abilities: {', '.join(ABILITIES)}")
        if key in ("resist", "immune", "vulnerable") and not set(value) <= set(DAMAGE_TYPES):
            raise ToolError(f"{key} takes damage types: {', '.join(DAMAGE_TYPES)}")
        if key == "condition_immune" and not set(value) <= set(CONDITIONS):
            raise ToolError(f"condition_immune takes conditions: {', '.join(CONDITIONS)}")
        changes[key] = value.lower() if key == "type" else value
    return changes


def register_creatures(server: MCPServer, stores: Callable[[], StoreRegistry]) -> None:
    """Add the creature tools to ``server``."""
    from app.mcp.drafts import bearer_of

    def english_page() -> Page:
        registry = stores()
        return Page(
            t=registry.ui.translator(
                "en", registry.glossary, registry.translations, registry.item_names.compose
            )
        )

    def answer(design: CreatureDesign) -> CreatureDraft:
        from app.routes.adventures import notes_problems

        registry = stores()
        math_ = registry.creatures.math
        made = math_.statblock(design)
        lines = []
        texts = []
        for key in ("trait", "action", "bonus", "reaction", "legendary"):
            for block in made.get(key) or []:
                text = " ".join(e for e in block.get("entries") or [] if isinstance(e, str))
                lines.append(f"{block.get('name', '')}. {text}".strip())
        for kind in ("traits", "actions", "bonus", "reactions", "legendary"):
            texts += [b.text for b in getattr(design, kind)]
        problems: list[CreatureProblem] = []
        if design.world_id and texts:
            here = Scope(world_id=design.world_id)
            for p in notes_problems(english_page(), registry, here, "\n\n".join(texts)):
                if p.kind in REFERENCE_KINDS:
                    problems.append(CreatureProblem(text=p.text, instead=list(p.instead)))
        estimate = math_.estimate(design)
        hp, formula = math_.hp(design)
        advice = ""
        if estimate is not None and estimate.final != design.cr:
            rows = registry.creatures.cr_order()
            gap = rows.index(estimate.final) - rows.index(design.cr) if design.cr in rows else 0
            if abs(gap) > 1:
                side = "offence" if estimate.offensive != design.cr else "defence"
                advice = (
                    f"Plays at CR {estimate.final} against the target {design.cr}: "
                    f"{'lower' if gap > 0 else 'raise'} its {side} (defensive CR "
                    f"{estimate.defensive}, offensive CR {estimate.offensive}), or set "
                    "fit=true for the target's armour class and hit points."
                )
        return CreatureDraft(
            id=design.id,
            name=design.name,
            url=creature_url(design.id),
            based_on=design.based_on,
            target_cr=design.cr,
            plays_at=estimate.final if estimate else None,
            defensive_cr=estimate.defensive if estimate else None,
            offensive_cr=estimate.offensive if estimate else None,
            hit_points=hp,
            hit_point_formula=formula,
            armor_class=design.ac,
            damage_per_round=estimate.dpr if estimate else None,
            attack_bonus=estimate.attack if estimate else None,
            save_dc=estimate.dc if estimate else None,
            advice=advice,
            statblock=lines,
            problems=problems,
        )

    def apply(design: CreatureDesign, changes: dict[str, Any], fit: bool) -> CreatureDesign:
        registry = stores()
        if "features" in changes:  # by the guide's table's own spelling
            named = []
            for chosen in changes["features"]:
                found = registry.creatures.feature_name(chosen.name)
                if found is None:
                    raise ToolError(f"{chosen.name!r} is not a monster feature of the guide.")
                named.append(CreatureFeature(name=found, value=chosen.value))
            changes = {**changes, "features": named}
        if "abilities" in changes:  # only the scores given change
            changes = {**changes, "abilities": {**design.abilities, **changes["abilities"]}}
        try:
            changed = CreatureDesign.model_validate({**design.model_dump(), **changes})
        except ValidationError as exc:
            raise ToolError(f"Not a creature the builder can make: {exc}") from exc
        if fit:
            changed = registry.creatures.math.fit(changed)
        with tool_errors():
            return registry.creatures.save(changed)

    @server.tool()
    def creature_guide() -> str:
        """How to design a creature for the world: read it before `create_creature`."""
        return CREATURE_GUIDE.read_text(encoding="utf-8")

    @server.tool()
    def create_creature(
        ctx: Context,
        world_id: str,
        name: str,
        cr: Annotated[str, Field(description='The challenge rating it should play at: "5", "1/2"')],
        based_on: Annotated[
            str,
            Field(
                description='A creature of the world\'s books to start from, "Lich" or '
                '"Lich|MM"; empty to start blank at the CR'
            ),
        ] = "",
        size: Annotated[str | None, Field(description="T, S, M, L, H or G")] = None,
        type: Annotated[str | None, Field(description="undead, humanoid, fiend, …")] = None,
        type_tags: list[str] | None = None,
        alignment: Annotated[str | None, Field(description='"C E", "N", "U", "A", …')] = None,
        ac: Annotated[int | None, Field(ge=0, le=40)] = None,
        ac_from: Annotated[
            str | None, Field(description="What gives the AC, as tags: {@item chain mail|phb}")
        ] = None,
        hit_dice: Annotated[int | None, Field(ge=1, le=200)] = None,
        speed: Annotated[dict[str, int] | None, Field(description="walk, fly, swim, …")] = None,
        abilities: Annotated[dict[str, int] | None, Field(description="str … cha")] = None,
        saves: Annotated[
            list[str] | None, Field(description="Abilities it is proficient in")
        ] = None,
        skills: Annotated[list[str] | None, Field(description='"arcana", "perception", …')] = None,
        senses: str | None = None,
        languages: str | None = None,
        resist: list[str] | None = None,
        immune: list[str] | None = None,
        vulnerable: list[str] | None = None,
        condition_immune: list[str] | None = None,
        traits: list[CreatureBlockInput] | None = None,
        actions: list[CreatureBlockInput] | None = None,
        bonus: list[CreatureBlockInput] | None = None,
        reactions: list[CreatureBlockInput] | None = None,
        legendary: list[CreatureBlockInput] | None = None,
        features: Annotated[
            list[CreatureFeature] | None,
            Field(
                description="The guide's monster features that change its CR, by name: "
                '"Magic Resistance", "Legendary Resistance" with value 3'
            ),
        ] = None,
        damage_per_round: Annotated[
            int | None,
            Field(
                ge=0,
                description="Its damage per round when its text cannot tell, as for a "
                "spellcaster: the average of its best three rounds (the guide's method)",
            ),
        ] = None,
        fit: Annotated[
            bool,
            Field(description="Set armour class and hit dice to the CR table's (after the rest)"),
        ] = False,
    ) -> CreatureDraft:
        """Draft a creature in the creature builder for a world, after `creature_guide`.
        It waits there as Claude's draft and is published only once the gamemaster
        accepts it. The answer says the CR it plays at and what in its text the world's
        books lack: change it with `update_creature` until it fits."""
        registry = stores()
        found = registry.mcp_tokens.verify(bearer_of(ctx))
        if found is None:
            raise ToolError("This request carries no valid Paravent key.")
        world = registry.worlds.find(world_id)
        if world is None:
            raise ToolError(f"No world with id {world_id}")
        if not registry.creatures.known_cr(cr.strip()):
            raise ToolError(f"No challenge rating {cr!r}: 0, 1/8, 1/4, 1/2, 1 … 30")
        base = ""
        if based_on.strip():
            name_part, _, source = based_on.partition("|")
            resolved = registry.references.resolver().resolve_parts(
                "monster", name_part.strip(), source.strip() or None, world.source_set
            )
            if resolved.entry is None:
                raise ToolError(f"{based_on!r} is not a creature in this world's books.")
            base = f"{resolved.entry.name}|{resolved.entry.source}"
        changes = _changes(
            size=size,
            type=type,
            type_tags=type_tags,
            alignment=alignment,
            ac=ac,
            ac_from=ac_from,
            hit_dice=hit_dice,
            speed=speed,
            abilities=abilities,
            saves=saves,
            skills=skills,
            senses=senses,
            languages=languages,
            resist=resist,
            immune=immune,
            vulnerable=vulnerable,
            condition_immune=condition_immune,
            traits=_blocks(traits),
            actions=_blocks(actions),
            bonus=_blocks(bonus),
            reactions=_blocks(reactions),
            legendary=_blocks(legendary),
            features=features,
            dpr_override=damage_per_round,
        )
        with tool_errors():
            design = registry.creatures.create(name, world.id, cr.strip(), base)
        design = design.model_copy(update={"drafted_by": found[0].id})
        return answer(apply(design, changes, fit))

    @server.tool()
    def update_creature(
        design_id: str,
        name: str | None = None,
        cr: str | None = None,
        size: str | None = None,
        type: str | None = None,
        type_tags: list[str] | None = None,
        alignment: str | None = None,
        ac: Annotated[int | None, Field(ge=0, le=40)] = None,
        ac_from: str | None = None,
        hit_dice: Annotated[int | None, Field(ge=1, le=200)] = None,
        speed: dict[str, int] | None = None,
        abilities: dict[str, int] | None = None,
        saves: list[str] | None = None,
        skills: list[str] | None = None,
        senses: str | None = None,
        languages: str | None = None,
        resist: list[str] | None = None,
        immune: list[str] | None = None,
        vulnerable: list[str] | None = None,
        condition_immune: list[str] | None = None,
        traits: list[CreatureBlockInput] | None = None,
        actions: list[CreatureBlockInput] | None = None,
        bonus: list[CreatureBlockInput] | None = None,
        reactions: list[CreatureBlockInput] | None = None,
        legendary: list[CreatureBlockInput] | None = None,
        features: list[CreatureFeature] | None = None,
        damage_per_round: Annotated[int | None, Field(ge=0)] = None,
        fit: bool = False,
    ) -> CreatureDraft:
        """Change a creature you drafted: each field given replaces the design's, a block
        list replaces that whole list. The gamemaster's own designs are not yours."""
        registry = stores()
        try:
            design = registry.creatures.get(design_id)
        except CreatureNotFound as exc:
            raise ToolError(f"No creature design with id {design_id}") from exc
        if not design.drafted_by:
            raise ToolError("Only drafts you created can be changed; this is the gamemaster's.")
        if cr is not None and not registry.creatures.known_cr(cr.strip()):
            raise ToolError(f"No challenge rating {cr!r}: 0, 1/8, 1/4, 1/2, 1 … 30")
        changes = _changes(
            name=name,
            cr=cr.strip() if cr else None,
            size=size,
            type=type,
            type_tags=type_tags,
            alignment=alignment,
            ac=ac,
            ac_from=ac_from,
            hit_dice=hit_dice,
            speed=speed,
            abilities=abilities,
            saves=saves,
            skills=skills,
            senses=senses,
            languages=languages,
            resist=resist,
            immune=immune,
            vulnerable=vulnerable,
            condition_immune=condition_immune,
            traits=_blocks(traits),
            actions=_blocks(actions),
            bonus=_blocks(bonus),
            reactions=_blocks(reactions),
            legendary=_blocks(legendary),
            features=features,
            dpr_override=damage_per_round,
        )
        return answer(apply(design, changes, fit))


__all__ = ["register_creatures"]
