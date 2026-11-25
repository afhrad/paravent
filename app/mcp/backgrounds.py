"""Claude's backgrounds (app/services/background_builder.py): drafts in the background
builder for a world, published only once the gamemaster accepts them there.

Typed is what the character wizard reads: skills, tools and languages (fixed or of the
player's choice), starting equipment and gold, a 2014 feature, and for the 2024 rules the
three abilities its increases fall on and its origin feat.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from app.mcp.common import current_or, tool_errors
from app.models.gamestate import BackgroundDesign, BackgroundItem
from app.services.background_builder import BackgroundError, BackgroundNotFound

if TYPE_CHECKING:
    from app.store.registry import StoreRegistry


class BackgroundItemInput(BaseModel):
    """A piece of starting equipment."""

    item: Annotated[
        str, Field(description='An item of the world\'s books, "Book|XPHB"; or leave empty')
    ] = ""
    special: Annotated[str, Field(description="Anything the books do not list")] = ""
    quantity: Annotated[int, Field(ge=1, le=999)] = 1


class BackgroundDraft(BaseModel):
    """A drafted background, as the gamemaster will see it."""

    id: str
    name: str
    edition: str
    background: dict[str, Any] = Field(description="The 5eTools background it publishes as")
    problems: list[str] = Field(default_factory=list, description="What the books lack")
    url: str = Field(description="Where the gamemaster reviews and accepts it")


FIELDS = (
    "edition", "skills", "any_skills", "tools", "any_artisan", "any_gaming",
    "any_instrument", "languages", "any_languages", "gold", "abilities", "feat",
    "feature_name", "feature_text", "text",
)  # fmt: skip


def register_backgrounds(server: MCPServer, stores: Callable[[], StoreRegistry]) -> None:
    """Add the background tools to ``server``."""
    from app.mcp.drafts import bearer_of

    def problems_of(design: BackgroundDesign) -> list[str]:
        registry = stores()
        world = registry.worlds.find(design.world_id) if design.world_id else None
        out = []
        checks = [("item", i.item) for i in design.equipment if i.item.strip()]
        if design.feat.strip():
            checks.append(("feat", design.feat))
        for category, typed in checks:
            name, _, source = typed.partition("|")
            hit = registry.references.resolver().resolve_parts(
                category, name.strip(), source.strip() or None, world.source_set if world else None
            )
            if hit.entry is None:
                out.append(f"No {category} {typed!r} in this world's books.")
        return out

    def answer(design: BackgroundDesign) -> BackgroundDraft:
        registry = stores()
        return BackgroundDraft(
            id=design.id,
            name=design.name,
            edition=design.edition,
            background=registry.background_builder.background(design),
            problems=problems_of(design),
            url=f"/gm/backgrounds/{design.id}",
        )

    def apply(design: BackgroundDesign, changes: dict[str, Any]) -> BackgroundDesign:
        changed = BackgroundDesign.model_validate({**design.model_dump(), **changes})
        with tool_errors():
            try:
                return stores().background_builder.save(changed)
            except BackgroundError as exc:
                raise ToolError(str(exc)) from exc

    def changes_of(given: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
        out = {k: given[k] for k in keys if given.get(k) is not None}
        equipment = given.get("equipment")
        if equipment is not None:
            out["equipment"] = [
                BackgroundItem(item=e.item, special=e.special, quantity=e.quantity)
                for e in equipment
            ]
        return out

    @server.tool()
    async def create_background(
        ctx: Context,
        world_id: str,
        name: str,
        based_on: Annotated[
            str,
            Field(description="A background of the world's books to start from; empty for blank"),
        ] = "",
        edition: Annotated[
            Literal["one", "classic"] | None,
            Field(
                description='"one" for the 2024 rules, "classic" for 2014; omit for the world\'s'
            ),
        ] = None,
        skills: Annotated[list[str] | None, Field(description='"insight", "religion"')] = None,
        any_skills: Annotated[int | None, Field(ge=0, le=4)] = None,
        tools: Annotated[list[str] | None, Field(description='"thieves\' tools"')] = None,
        any_artisan: Annotated[int | None, Field(ge=0, le=3)] = None,
        any_gaming: Annotated[int | None, Field(ge=0, le=3)] = None,
        any_instrument: Annotated[int | None, Field(ge=0, le=3)] = None,
        languages: list[str] | None = None,
        any_languages: Annotated[int | None, Field(ge=0, le=4)] = None,
        equipment: list[BackgroundItemInput] | None = None,
        gold: Annotated[int | None, Field(ge=0, description="In gp")] = None,
        abilities: Annotated[
            list[str] | None, Field(description='2024: three abilities, "str", "wis", "cha"')
        ] = None,
        feat: Annotated[
            str | None, Field(description='2024: its origin feat, "Alert|XPHB"')
        ] = None,
        feature_name: Annotated[str | None, Field(description="2014: its feature's name")] = None,
        feature_text: str | None = None,
        text: Annotated[
            str | None, Field(description="Its description, in the world's language")
        ] = None,
    ) -> BackgroundDraft:
        """Draft a background in the background builder for a world. It waits there as
        Claude's draft and the character wizard offers it only once the gamemaster
        accepts it."""
        registry = stores()
        found = registry.mcp_tokens.verify(bearer_of(ctx))
        if found is None:
            raise ToolError("This request carries no valid Paravent key.")
        world = registry.worlds.find(current_or(registry, world_id))
        if world is None:
            raise ToolError(f"No world with id {world_id}")
        base = ""
        if based_on.strip():
            bg_name, _, source = based_on.partition("|")
            hit = registry.references.resolver().resolve_parts(
                "background", bg_name.strip(), source.strip() or None, world.source_set
            )
            if hit.entry is None:
                raise ToolError(f"{based_on!r} is not a background in this world's books.")
            base = f"{hit.entry.name}|{getattr(hit.entry, 'source', '')}"
        with tool_errors():
            try:
                design = registry.background_builder.create(
                    name, world.id, base, drafted_by=found[0].id
                )
            except BackgroundError as exc:
                raise ToolError(str(exc)) from exc
        changes = changes_of(locals(), FIELDS)
        return answer(apply(design, changes) if changes else design)

    @server.tool()
    async def update_background(
        design_id: str,
        name: str | None = None,
        edition: Literal["one", "classic"] | None = None,
        skills: list[str] | None = None,
        any_skills: Annotated[int | None, Field(ge=0, le=4)] = None,
        tools: list[str] | None = None,
        any_artisan: Annotated[int | None, Field(ge=0, le=3)] = None,
        any_gaming: Annotated[int | None, Field(ge=0, le=3)] = None,
        any_instrument: Annotated[int | None, Field(ge=0, le=3)] = None,
        languages: list[str] | None = None,
        any_languages: Annotated[int | None, Field(ge=0, le=4)] = None,
        equipment: list[BackgroundItemInput] | None = None,
        gold: Annotated[int | None, Field(ge=0)] = None,
        abilities: list[str] | None = None,
        feat: str | None = None,
        feature_name: str | None = None,
        feature_text: str | None = None,
        text: str | None = None,
    ) -> BackgroundDraft:
        """Change a background you drafted: each field given replaces the design's;
        `equipment` replaces the whole list. The gamemaster's own designs are not yours."""
        registry = stores()
        try:
            design = registry.background_builder.get(design_id)
        except BackgroundNotFound as exc:
            raise ToolError(f"No background design with id {design_id}") from exc
        if not design.drafted_by:
            raise ToolError(
                "Only drafts you created can be changed; this is the gamemaster's. "
                "To change it, open a revision with `start_background_revision`."
            )
        if design.revision_of and name is not None and name.strip() != design.name:
            raise ToolError("A revision keeps its background's name: characters know it by it.")
        return answer(apply(design, changes_of(locals(), ("name", *FIELDS))))

    @server.tool()
    async def start_background_revision(
        ctx: Context,
        background: Annotated[
            str, Field(description="The accepted background's design id, or its name")
        ],
    ) -> BackgroundDraft:
        """A draft copy of a background the gamemaster accepted (or made in the builder), to
        change with `update_background`. Answers with the copy (write to its id), or the copy
        already open. The background stays as it is, published, until the gamemaster accepts
        the revision; it keeps its name. Your own drafts need no copy."""
        registry = stores()
        found = registry.mcp_tokens.verify(bearer_of(ctx))
        if found is None:
            raise ToolError("This request carries no valid Paravent key.")
        builder = registry.background_builder
        try:
            found_design = builder.get(background)
        except BackgroundNotFound:
            named = builder.named(background)
            if named is None:
                raise ToolError(f"No background design {background!r}") from None
            found_design = named
        if found_design.revision_of:
            return answer(found_design)
        if found_design.drafted_by:
            raise ToolError(
                f"{found_design.name} is still your draft: change it with update_background."
            )
        with tool_errors():
            try:
                found_design = builder.start_revision(found_design.id, found[0].id)
            except BackgroundError as exc:
                raise ToolError(str(exc)) from exc
        return answer(found_design)


__all__ = ["BackgroundDraft", "BackgroundItemInput", "register_backgrounds"]
