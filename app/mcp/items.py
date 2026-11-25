"""Claude's magic items (MCP.md §10, app/services/item_builder.py): drafts in the item
builder for a world, published only once the gamemaster accepts them there.

Only what the character sheet applies is typed — bonuses, damage defences, ability
scores, charges and the spells it casts; anything else the item does is its text. A
weapon, armour, shield, ammunition or staff is made on a base item of the world's books.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Any, Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from app.mcp.common import current_or, tool_errors
from app.models.gamestate import ItemDesign, ItemSpell
from app.services.item_builder import BASED, ItemError, ItemNotFound

if TYPE_CHECKING:
    from app.store.registry import StoreRegistry

Kind = Literal[
    "weapon", "armor", "shield", "ammunition", "staff", "wondrous", "ring", "rod", "wand",
    "potion", "scroll",
]  # fmt: skip
RarityIn = Literal["common", "uncommon", "rare", "very rare", "legendary", "artifact"]
Recharge = Literal["", "dawn", "dusk", "midnight", "restLong"]


class ItemSpellInput(BaseModel):
    """A spell the item casts."""

    spell: Annotated[str, Field(description='"Fireball" or "Fireball|XPHB", of the world\'s books')]
    charges: Annotated[int, Field(ge=0, le=20, description="Charges per cast; 0 at will")] = 0


class ItemDraft(BaseModel):
    """A drafted item, as the gamemaster will see it."""

    id: str
    name: str
    kind: str
    rarity: str
    item: dict[str, Any] = Field(description="The 5eTools item it publishes as")
    problems: list[str] = Field(
        default_factory=list, description="Spells or a base item the world's books lack"
    )
    url: str = Field(description="Where the gamemaster reviews and accepts it")


def register_items(server: MCPServer, stores: Callable[[], StoreRegistry]) -> None:
    """Add the item tools to ``server``."""
    from app.mcp.drafts import bearer_of

    def answer(design: ItemDesign) -> ItemDraft:
        registry = stores()
        world = registry.worlds.find(design.world_id) if design.world_id else None
        problems = []
        for spell in design.spells:
            name, _, source = spell.spell.partition("|")
            found = registry.references.resolver().resolve_parts(
                "spell", name.strip(), source.strip() or None, world.source_set if world else None
            )
            if found.entry is None:
                problems.append(f"No spell {spell.spell!r} in this world's books.")
        return ItemDraft(
            id=design.id,
            name=design.name,
            kind=design.kind,
            rarity=design.rarity,
            item=registry.item_builder.item(design),
            problems=problems,
            url=f"/gm/items/{design.id}",
        )

    def apply(design: ItemDesign, changes: dict[str, Any]) -> ItemDesign:
        registry = stores()
        changed = ItemDesign.model_validate({**design.model_dump(), **changes})
        with tool_errors():
            try:
                return registry.item_builder.save(changed)
            except ItemError as exc:
                raise ToolError(str(exc)) from exc

    def changes_of(**given: Any) -> dict[str, Any]:
        out = {k: v for k, v in given.items() if v is not None}
        if "spells" in out:
            out["spells"] = [ItemSpell(spell=s.spell, charges=s.charges) for s in out["spells"]]
        return out

    @server.tool()
    async def create_item(
        ctx: Context,
        world_id: str,
        name: str,
        kind: Annotated[Kind, Field(description="What it is")] = "wondrous",
        based_on: Annotated[
            str,
            Field(
                description="A magic item of the world's books to start from, \"Cloak of "
                'Protection" or "Sun Blade|DMG"; empty to start blank'
            ),
        ] = "",
        base_item: Annotated[
            str,
            Field(
                description="For a weapon, armour, shield, ammunition or staff: the base item "
                'of the world\'s books it is made on, "Longsword" or "Longsword|XPHB"'
            ),
        ] = "",
        rarity: RarityIn | None = None,
        attunement: bool | None = None,
        attune_by: Annotated[
            str | None, Field(description='"by a wizard"; empty for anyone')
        ] = None,
        bonus_weapon: Annotated[int | None, Field(ge=0, le=3)] = None,
        bonus_ac: Annotated[int | None, Field(ge=0, le=3)] = None,
        bonus_saving_throw: Annotated[int | None, Field(ge=0, le=3)] = None,
        bonus_spell_attack: Annotated[int | None, Field(ge=0, le=3)] = None,
        bonus_spell_save_dc: Annotated[int | None, Field(ge=0, le=3)] = None,
        bonus_ability_check: Annotated[int | None, Field(ge=0, le=3)] = None,
        resist: Annotated[list[str] | None, Field(description='Damage types: "fire", …')] = None,
        immune: list[str] | None = None,
        vulnerable: list[str] | None = None,
        ability_set: Annotated[
            dict[str, int] | None, Field(description='Scores it sets: {"str": 19}')
        ] = None,
        ability_raise: Annotated[
            dict[str, int] | None, Field(description='Scores it raises while attuned: {"con": 2}')
        ] = None,
        charges: Annotated[int | None, Field(ge=0, le=100)] = None,
        recharge: Recharge | None = None,
        recharge_amount: Annotated[
            str | None, Field(description='"1d6+1", "2", or empty to restore all')
        ] = None,
        spells: list[ItemSpellInput] | None = None,
        text: Annotated[
            str | None,
            Field(
                description="Its look and everything it does beyond the typed numbers, in the "
                "world's language; paragraphs split by a blank line; {@spell …} tags allowed"
            ),
        ] = None,
        weight: Annotated[float | None, Field(ge=0)] = None,
        value_gp: Annotated[
            int | None, Field(ge=0, description="Price; omit for its rarity's")
        ] = None,
    ) -> ItemDraft:
        """Draft a magic item in the item builder for a world. It waits there as Claude's
        draft and joins the world's books only once the gamemaster accepts it. Type only
        what the character sheet counts; describe everything else in `text`."""
        registry = stores()
        found = registry.mcp_tokens.verify(bearer_of(ctx))
        if found is None:
            raise ToolError("This request carries no valid Paravent key.")
        world = registry.worlds.find(current_or(registry, world_id))
        if world is None:
            raise ToolError(f"No world with id {world_id}")

        def resolved(category: str, typed: str, what: str) -> str:
            name_part, _, source = typed.partition("|")
            hit = registry.references.resolver().resolve_parts(
                category, name_part.strip(), source.strip() or None, world.source_set
            )
            if hit.entry is None:
                raise ToolError(f"{typed!r} is not {what} in this world's books.")
            return f"{hit.entry.name}|{getattr(hit.entry, 'source', '')}"

        base_ref = resolved("item", based_on, "a magic item") if based_on.strip() else ""
        base = ""
        if base_item.strip():
            base = resolved("baseitem", base_item, "a base item")
        elif not base_ref and kind in BASED and kind != "staff":
            raise ToolError(f"A {kind} needs base_item: the base item of the books it is made on.")
        with tool_errors():
            try:
                design = registry.item_builder.create(
                    name, world.id, kind, base_ref, base, drafted_by=found[0].id
                )
            except ItemError as exc:
                raise ToolError(str(exc)) from exc
        changes = changes_of(
            rarity=rarity,
            attunement=attunement,
            attune_by=attune_by,
            bonus_weapon=bonus_weapon,
            bonus_ac=bonus_ac,
            bonus_saving_throw=bonus_saving_throw,
            bonus_spell_attack=bonus_spell_attack,
            bonus_spell_save_dc=bonus_spell_save_dc,
            bonus_ability_check=bonus_ability_check,
            resist=resist,
            immune=immune,
            vulnerable=vulnerable,
            ability_set=ability_set,
            ability_raise=ability_raise,
            charges=charges,
            recharge=recharge,
            recharge_amount=recharge_amount,
            spells=spells,
            text=text,
            weight=weight,
            value=value_gp * 100 if value_gp is not None else None,
        )
        return answer(apply(design, changes) if changes else design)

    @server.tool()
    async def update_item(
        design_id: str,
        name: str | None = None,
        rarity: RarityIn | None = None,
        attunement: bool | None = None,
        attune_by: str | None = None,
        bonus_weapon: Annotated[int | None, Field(ge=0, le=3)] = None,
        bonus_ac: Annotated[int | None, Field(ge=0, le=3)] = None,
        bonus_saving_throw: Annotated[int | None, Field(ge=0, le=3)] = None,
        bonus_spell_attack: Annotated[int | None, Field(ge=0, le=3)] = None,
        bonus_spell_save_dc: Annotated[int | None, Field(ge=0, le=3)] = None,
        bonus_ability_check: Annotated[int | None, Field(ge=0, le=3)] = None,
        resist: list[str] | None = None,
        immune: list[str] | None = None,
        vulnerable: list[str] | None = None,
        ability_set: dict[str, int] | None = None,
        ability_raise: dict[str, int] | None = None,
        charges: Annotated[int | None, Field(ge=0, le=100)] = None,
        recharge: Recharge | None = None,
        recharge_amount: str | None = None,
        spells: list[ItemSpellInput] | None = None,
        text: str | None = None,
        weight: Annotated[float | None, Field(ge=0)] = None,
        value_gp: Annotated[int | None, Field(ge=0)] = None,
    ) -> ItemDraft:
        """Change an item you drafted: each field given replaces the design's; `spells`
        replaces the whole list. The gamemaster's own designs are not yours."""
        registry = stores()
        try:
            design = registry.item_builder.get(design_id)
        except ItemNotFound as exc:
            raise ToolError(f"No item design with id {design_id}") from exc
        if not design.drafted_by:
            raise ToolError("Only drafts you created can be changed; this is the gamemaster's.")
        changes = changes_of(
            name=name,
            rarity=rarity,
            attunement=attunement,
            attune_by=attune_by,
            bonus_weapon=bonus_weapon,
            bonus_ac=bonus_ac,
            bonus_saving_throw=bonus_saving_throw,
            bonus_spell_attack=bonus_spell_attack,
            bonus_spell_save_dc=bonus_spell_save_dc,
            bonus_ability_check=bonus_ability_check,
            resist=resist,
            immune=immune,
            vulnerable=vulnerable,
            ability_set=ability_set,
            ability_raise=ability_raise,
            charges=charges,
            recharge=recharge,
            recharge_amount=recharge_amount,
            spells=spells,
            text=text,
            weight=weight,
            value=value_gp * 100 if value_gp is not None else None,
        )
        return answer(apply(design, changes))


__all__ = ["ItemDraft", "ItemSpellInput", "register_items"]
