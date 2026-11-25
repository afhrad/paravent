"""Claude's spells (app/services/spell_builder.py): drafts in the spell builder for a
world, published only once the gamemaster accepts them there.

Typed is what casting it from the sheet uses — its header, the class lists it is on, and
its effect (a save or spell attack with damage, or healing, and what a higher slot adds);
anything else it does is its text. The builder writes the effect's sentence in the
world's language unless the text already says it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from app.mcp.common import current_or, tool_errors
from app.models.gamestate import SpellDesign
from app.services.spell_builder import SpellError, SpellNotFound

if TYPE_CHECKING:
    from app.store.registry import StoreRegistry

School = Literal["A", "C", "D", "E", "V", "I", "N", "T"]
Ability = Literal["str", "dex", "con", "int", "wis", "cha"]


class SpellDraft(BaseModel):
    """A drafted spell, as the gamemaster will see it."""

    id: str
    name: str
    level: int
    spell: dict[str, Any] = Field(description="The 5eTools spell it publishes as")
    classes: list[str]
    problems: list[str] = Field(default_factory=list, description="Classes the books lack")
    url: str = Field(description="Where the gamemaster reviews and accepts it")


FIELDS = (
    "level", "school", "time_number", "time_unit", "range_kind", "range_amount", "area",
    "area_size", "verbal", "somatic", "material", "material_cost", "material_consumed",
    "duration_kind", "duration_amount", "duration_unit", "concentration", "ritual",
    "effect", "save", "half_on_save", "attack", "dice", "damage_types", "heal_mod",
    "heal_targets", "upcast", "text", "higher_text",
)  # fmt: skip


def register_spells(server: MCPServer, stores: Callable[[], StoreRegistry]) -> None:
    """Add the spell tools to ``server``."""
    from app.mcp.drafts import bearer_of

    def classes_of(world_id: str, names: list[str]) -> tuple[list[str], list[str]]:
        """Class names ("Wizard", "Wizard|XPHB") as the world's books resolve them."""
        registry = stores()
        world = registry.worlds.find(world_id)
        out, problems = [], []
        for typed in names:
            name, _, source = typed.partition("|")
            hit = registry.references.resolver().resolve_parts(
                "class", name.strip(), source.strip() or None, world.source_set if world else None
            )
            if hit.entry is None:
                problems.append(f"No class {typed!r} in this world's books.")
            else:
                out.append(f"{hit.entry.name}|{getattr(hit.entry, 'source', '')}")
        return out, problems

    def answer(design: SpellDesign, problems: list[str]) -> SpellDraft:
        registry = stores()
        return SpellDraft(
            id=design.id,
            name=design.name,
            level=design.level,
            spell=registry.spell_builder.spell(design),
            classes=design.classes,
            problems=problems,
            url=f"/gm/spells/{design.id}",
        )

    def apply(design: SpellDesign, changes: dict[str, Any]) -> SpellDesign:
        changed = SpellDesign.model_validate({**design.model_dump(), **changes})
        with tool_errors():
            try:
                return stores().spell_builder.save(changed)
            except SpellError as exc:
                raise ToolError(str(exc)) from exc

    @server.tool()
    async def create_spell(
        ctx: Context,
        world_id: str,
        name: str,
        based_on: Annotated[
            str,
            Field(
                description="A spell of the world's books to start from, "
                '"Fireball"; empty for blank'
            ),
        ] = "",
        classes: Annotated[
            list[str] | None,
            Field(description='Classes whose lists it joins: "Wizard", "Druid|XPHB"'),
        ] = None,
        level: Annotated[int | None, Field(ge=0, le=9, description="0 for a cantrip")] = None,
        school: School | None = None,
        time_number: Annotated[int | None, Field(ge=1, le=99)] = None,
        time_unit: Literal["action", "bonus", "reaction", "minute", "hour"] | None = None,
        range_kind: Literal["feet", "miles", "self", "touch", "sight", "unlimited"] | None = None,
        range_amount: Annotated[int | None, Field(ge=0)] = None,
        area: Literal["", "cone", "cube", "cylinder", "line", "sphere", "emanation"] | None = None,
        area_size: Annotated[int | None, Field(ge=0, description="In feet")] = None,
        verbal: bool | None = None,
        somatic: bool | None = None,
        material: Annotated[str | None, Field(description="The component; empty for none")] = None,
        material_cost: Annotated[int | None, Field(ge=0, description="In gp")] = None,
        material_consumed: bool | None = None,
        duration_kind: Literal["instant", "timed", "permanent", "special"] | None = None,
        duration_amount: Annotated[int | None, Field(ge=1, le=999)] = None,
        duration_unit: Literal["round", "minute", "hour", "day"] | None = None,
        concentration: bool | None = None,
        ritual: bool | None = None,
        effect: Literal["none", "save", "attack", "heal"] | None = None,
        save: Ability | None = None,
        half_on_save: bool | None = None,
        attack: Literal["M", "R"] | None = None,
        dice: Annotated[str | None, Field(description='Damage or healing, "8d6"')] = None,
        damage_types: list[str] | None = None,
        heal_mod: bool | None = None,
        heal_targets: Annotated[int | None, Field(ge=1, le=12)] = None,
        upcast: Annotated[str | None, Field(description='What each slot above adds, "1d6"')] = None,
        text: Annotated[
            str | None,
            Field(
                description="Everything else it does, in the world's language; the effect's "
                "sentence is written for you"
            ),
        ] = None,
        higher_text: str | None = None,
    ) -> SpellDraft:
        """Draft a spell in the spell builder for a world. It waits there as Claude's draft
        and joins the world's books and the chosen classes' lists only once the
        gamemaster accepts it. Type what casting it uses; describe the rest in `text`."""
        registry = stores()
        found = registry.mcp_tokens.verify(bearer_of(ctx))
        if found is None:
            raise ToolError("This request carries no valid Paravent key.")
        world = registry.worlds.find(current_or(registry, world_id))
        if world is None:
            raise ToolError(f"No world with id {world_id}")
        base = ""
        if based_on.strip():
            spell_name, _, source = based_on.partition("|")
            hit = registry.references.resolver().resolve_parts(
                "spell", spell_name.strip(), source.strip() or None, world.source_set
            )
            if hit.entry is None:
                raise ToolError(f"{based_on!r} is not a spell in this world's books.")
            base = f"{hit.entry.name}|{getattr(hit.entry, 'source', '')}"
        with tool_errors():
            try:
                design = registry.spell_builder.create(name, world.id, base, drafted_by=found[0].id)
            except SpellError as exc:
                raise ToolError(str(exc)) from exc
        given = locals()
        changes = {k: given[k] for k in FIELDS if given.get(k) is not None}
        problems: list[str] = []
        if classes is not None:
            changes["classes"], problems = classes_of(world.id, classes)
        return answer(apply(design, changes) if changes else design, problems)

    @server.tool()
    async def update_spell(
        design_id: str,
        name: str | None = None,
        classes: list[str] | None = None,
        level: Annotated[int | None, Field(ge=0, le=9)] = None,
        school: School | None = None,
        time_number: Annotated[int | None, Field(ge=1, le=99)] = None,
        time_unit: Literal["action", "bonus", "reaction", "minute", "hour"] | None = None,
        range_kind: Literal["feet", "miles", "self", "touch", "sight", "unlimited"] | None = None,
        range_amount: Annotated[int | None, Field(ge=0)] = None,
        area: Literal["", "cone", "cube", "cylinder", "line", "sphere", "emanation"] | None = None,
        area_size: Annotated[int | None, Field(ge=0)] = None,
        verbal: bool | None = None,
        somatic: bool | None = None,
        material: str | None = None,
        material_cost: Annotated[int | None, Field(ge=0)] = None,
        material_consumed: bool | None = None,
        duration_kind: Literal["instant", "timed", "permanent", "special"] | None = None,
        duration_amount: Annotated[int | None, Field(ge=1, le=999)] = None,
        duration_unit: Literal["round", "minute", "hour", "day"] | None = None,
        concentration: bool | None = None,
        ritual: bool | None = None,
        effect: Literal["none", "save", "attack", "heal"] | None = None,
        save: Ability | None = None,
        half_on_save: bool | None = None,
        attack: Literal["M", "R"] | None = None,
        dice: str | None = None,
        damage_types: list[str] | None = None,
        heal_mod: bool | None = None,
        heal_targets: Annotated[int | None, Field(ge=1, le=12)] = None,
        upcast: str | None = None,
        text: str | None = None,
        higher_text: str | None = None,
    ) -> SpellDraft:
        """Change a spell you drafted: each field given replaces the design's; `classes`
        replaces its lists. The gamemaster's own designs are not yours."""
        registry = stores()
        try:
            design = registry.spell_builder.get(design_id)
        except SpellNotFound as exc:
            raise ToolError(f"No spell design with id {design_id}") from exc
        if not design.drafted_by:
            raise ToolError(
                "Only drafts you created can be changed; this is the gamemaster's. "
                "To change it, open a revision with `start_spell_revision`."
            )
        if design.revision_of and name is not None and name.strip() != design.name:
            raise ToolError("A revision keeps its spell's name: characters know it by it.")
        given = locals()
        changes = {k: given[k] for k in ("name", *FIELDS) if given.get(k) is not None}
        problems: list[str] = []
        if classes is not None:
            changes["classes"], problems = classes_of(design.world_id or "", classes)
        return answer(apply(design, changes), problems)

    @server.tool()
    async def start_spell_revision(
        ctx: Context,
        spell: Annotated[str, Field(description="The accepted spell's design id, or its name")],
    ) -> SpellDraft:
        """A draft copy of a spell the gamemaster accepted (or made in the builder), to
        change with `update_spell`. Answers with the copy (write to its id), or the copy
        already open. The spell stays as it is, published, until the gamemaster accepts
        the revision; it keeps its name. Your own drafts need no copy."""
        registry = stores()
        found = registry.mcp_tokens.verify(bearer_of(ctx))
        if found is None:
            raise ToolError("This request carries no valid Paravent key.")
        builder = registry.spell_builder
        try:
            found_design = builder.get(spell)
        except SpellNotFound:
            named = builder.named(spell)
            if named is None:
                raise ToolError(f"No spell design {spell!r}") from None
            found_design = named
        if found_design.revision_of:
            return answer(found_design, [])
        if found_design.drafted_by:
            raise ToolError(
                f"{found_design.name} is still your draft: change it with update_spell."
            )
        with tool_errors():
            try:
                found_design = builder.start_revision(found_design.id, found[0].id)
            except SpellError as exc:
                raise ToolError(str(exc)) from exc
        return answer(found_design, [])


__all__ = ["SpellDraft", "register_spells"]
