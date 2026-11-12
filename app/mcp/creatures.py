"""Creatures over MCP (documentation/MCP.md §10): Claude drafts a creature in the
creature builder from a description, a world's setting and a challenge rating.

A design Claude makes is a **draft**: it carries the key that wrote it, the builder
marks it, and a publish leaves it out until the gamemaster accepts it. Claude changes
only drafts. Every answer is the builder's own working: the CR the design plays at by
the guide's method, the statblock with its numbers filled in, and the references in its
text that the world's books do not have. A picture comes from a web address, fetched
once when given and kept as the world's picture of the creature it publishes as.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from importlib.resources import files
from typing import TYPE_CHECKING, Annotated, Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field, ValidationError

from app.mcp.common import tool_errors
from app.mcp.models import (
    CreatureBlockInput,
    CreatureDraft,
    CreatureProblem,
    ReadyMadeInput,
    ReadyMadeText,
)
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
    entries_text,
    feature_block,
)
from app.services.creature_pictures import design_ref, fetch
from app.services.translation_targets import BUILDER_FIELDS, builder_key
from app.ui.context import Page
from app.ui.creature_pages import creature_url

if TYPE_CHECKING:
    from app.store.registry import StoreRegistry

CREATURE_GUIDE = files("app.mcp").joinpath("creature_guide.md")
# A placeholder the statblock still shows: the builder could not fill it.
PLACEHOLDER = re.compile(r"<\$[^$]+\$>")
# Problems of the chapter check that concern a creature's text: references only.
REFERENCE_KINDS = frozenset({"reference", "excluded"})


def _blocks(given: Sequence[CreatureBlockInput] | None) -> list[CreatureBlock] | None:
    if given is None:
        return None
    return [CreatureBlock(name=" ".join(b.name.split()), text=b.text.strip()) for b in given]


READY_MADE = Field(
    description="Ready-made texts of the builder to add, after the block lists given "
    "(`ready_made_texts` lists them): written in the world's language where the table "
    "translated them, their numbers following the creature"
)
READY_MADE_LIMIT = 40

IMAGE_URL = Field(
    description="A web address of a picture of it (PNG, JPEG, WebP, GIF or AVIF, at most "
    "20 MB): fetched once and shown to the players in a fight; empty removes its picture"
)


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
        for left in dict.fromkeys(PLACEHOLDER.findall("\n".join(lines))):
            problems.append(
                CreatureProblem(
                    kind="placeholder",
                    text=left,
                    instead=["<$to_hit__int$>", "<$dc__int$>", "<$damage_avg__4.5+str$>"],
                )
            )
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
            revision_of=design.revision_of,
            changes=registry.creatures.changes(design),
            picture=f"{creature_url(design.id)}/picture"
            if registry.creature_pictures.find(design.world_id, design_ref(design.name))
            else None,
        )

    def template_name(kind: str, name: str) -> str:
        """A ready-made block's full name, from it or from a title only one block has."""
        tables = stores().creature_tables
        blocks = tables.traits() if kind == "trait" else tables.actions()
        wanted = " ".join(name.split()).lower()
        exact = [b.name for b in blocks if b.name.lower() == wanted]
        if exact:
            return exact[0]
        titled = [b.name for b in blocks if b.title.lower() == wanted]
        if len(titled) == 1:
            return titled[0]
        if titled:
            raise ToolError(f"Several {kind}s are called {name!r}: {', '.join(titled[:12])}.")
        raise ToolError(f"No ready-made {kind} {name!r}: see `ready_made_texts`.")

    def add_ready_made(design: CreatureDesign, given: Sequence[ReadyMadeInput]) -> None:
        """Add ready-made texts to ``design`` in its world's language and units."""
        registry = stores()
        world = registry.worlds.find(design.world_id) if design.world_id else None
        wording = registry.creature_wording(design.world_id, world.language if world else "en")
        service = registry.creatures
        for item in given:
            if item.kind != "feature":
                service.add_template(
                    design, item.kind, template_name(item.kind, item.name), wording
                )
                continue
            feature = registry.creature_tables.feature(item.name)
            if feature is None:
                raise ToolError(f"{item.name!r} is not a monster feature of the guide.")
            if any(f.name == feature.name for f in design.features):
                # Counted already (`features`): only its text.
                block = feature_block(feature)
                design.traits.append(
                    wording.ready_made(design, "feature", feature.name, block.name, [block.text])
                )
            else:
                service.add_feature(design, feature.name, item.value, wording)

    def apply(
        design: CreatureDesign,
        changes: dict[str, Any],
        fit: bool,
        ready_made: Sequence[ReadyMadeInput] | None = None,
    ) -> CreatureDesign:
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
        if ready_made:
            add_ready_made(changed, ready_made)
        if fit:
            changed = registry.creatures.math.fit(changed)
        with tool_errors():
            return registry.creatures.save(changed)

    async def picture_of(image_url: str | None) -> tuple[str, bytes] | None:
        """The picture to store, fetched before anything changes so a bad address
        leaves the design as it was."""
        if not image_url or not image_url.strip():
            return None
        with tool_errors():
            return await fetch(image_url)

    def keep_picture(
        before: CreatureDesign | None,
        after: CreatureDesign,
        image_url: str | None,
        picture: tuple[str, bytes] | None,
    ) -> None:
        pictures = stores().creature_pictures
        ref = design_ref(after.name)
        if before is not None:
            pictures.move(before.world_id, design_ref(before.name), after.world_id, ref)
        if picture is not None:
            with tool_errors():
                pictures.set(after.world_id, ref, *picture)
        elif image_url is not None and not image_url.strip():
            pictures.remove(after.world_id, ref)

    @server.tool()
    def creature_guide() -> str:
        """How to design a creature for the world: read it before `create_creature`."""
        return CREATURE_GUIDE.read_text(encoding="utf-8")

    @server.tool()
    def ready_made_texts(
        world_id: str,
        kind: Annotated[
            str, Field(description="trait, action or feature; empty for all three")
        ] = "",
        query: Annotated[str, Field(description="Words its name contains: bite, pack")] = "",
    ) -> list[ReadyMadeText]:
        """The creature builder's ready-made traits, actions and monster features, in the
        world's language where the table translated them: add one by name with
        `ready_made` on `create_creature` or `update_creature` rather than writing a
        block the builder has. At most 40, so narrow with ``query``."""
        registry = stores()
        world = registry.worlds.find(world_id)
        if world is None:
            raise ToolError(f"No world with id {world_id}")
        if kind and kind not in ("trait", "action", "feature"):
            raise ToolError("kind is trait, action or feature")
        t = registry.creature_wording(world.id, world.language).t
        tables = registry.creature_tables
        words = query.lower().split()
        found: list[ReadyMadeText] = []
        rows: list[tuple[str, str, str, list[Any], bool]] = []
        if kind in ("", "feature"):
            for f in tables.features():
                block = feature_block(f)
                rows.append(("feature", f.name, block.name, [block.text], f.takes_number))
        for part, blocks in (("trait", tables.traits()), ("action", tables.actions())):
            if kind in ("", part):
                rows += [(part, b.name, b.title, list(b.entries), False) for b in blocks]
        for part, name, title, entries, number in rows:
            if not all(w in name.lower() for w in words):
                continue
            data = {"name": title, "entries": entries}
            shown = t.overlay("builder", builder_key(part, name), data, BUILDER_FIELDS)
            found.append(
                ReadyMadeText(
                    kind=part,
                    name=name,
                    title=str(shown.data["name"]),
                    text=entries_text(list(shown.data["entries"])),
                    translated=shown.coverage.any,
                    takes_number=number,
                )
            )
            if len(found) >= READY_MADE_LIMIT:
                break
        return found

    @server.tool()
    async def create_creature(
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
        speed: Annotated[
            dict[str, int] | None,
            Field(description="walk, fly, swim, … in feet, whatever the world's units"),
        ] = None,
        abilities: Annotated[dict[str, int] | None, Field(description="str … cha")] = None,
        saves: Annotated[
            list[str] | None, Field(description="Abilities it is proficient in")
        ] = None,
        skills: Annotated[list[str] | None, Field(description='"arcana", "perception", …')] = None,
        senses: Annotated[
            str | None,
            Field(description='In English and feet, as 5eTools writes them: "darkvision 60 ft."'),
        ] = None,
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
        ready_made: Annotated[list[ReadyMadeInput] | None, READY_MADE] = None,
        fit: Annotated[
            bool,
            Field(description="Set armour class and hit dice to the CR table's (after the rest)"),
        ] = False,
        image_url: Annotated[str | None, IMAGE_URL] = None,
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
        picture = await picture_of(image_url)
        with tool_errors():
            wording = registry.creature_wording(world.id, world.language)
            design = registry.creatures.create(name, world.id, cr.strip(), base, wording)
        design = design.model_copy(update={"drafted_by": found[0].id})
        saved = apply(design, changes, fit, ready_made)
        keep_picture(None, saved, image_url, picture)
        return answer(saved)

    @server.tool()
    async def update_creature(
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
        ready_made: Annotated[list[ReadyMadeInput] | None, READY_MADE] = None,
        fit: bool = False,
        image_url: Annotated[str | None, IMAGE_URL] = None,
    ) -> CreatureDraft:
        """Change a creature you drafted: each field given replaces the design's, a block
        list replaces that whole list. The gamemaster's own designs are not yours."""
        registry = stores()
        try:
            design = registry.creatures.get(design_id)
        except CreatureNotFound as exc:
            raise ToolError(f"No creature design with id {design_id}") from exc
        if not design.drafted_by:
            raise ToolError(
                "Only drafts you created can be changed; this is the gamemaster's. "
                "To change it, open a revision with `start_creature_revision`."
            )
        if design.revision_of and name is not None and name.strip() != design.name:
            raise ToolError("A revision keeps its creature's name: encounters find it by it.")
        if design.revision_of and image_url is not None:
            raise ToolError(
                "An accepted creature's picture is the gamemaster's to change in the builder."
            )
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
        picture = await picture_of(image_url)
        saved = apply(design, changes, fit, ready_made)
        keep_picture(design, saved, image_url, picture)
        return answer(saved)

    @server.tool()
    def start_creature_revision(
        ctx: Context,
        creature: Annotated[
            str, Field(description="The accepted creature's design id, or its name")
        ],
    ) -> CreatureDraft:
        """A draft copy of a creature the gamemaster accepted (or made in the builder),
        to change with `update_creature`. Answers with the copy (write to its id), or
        the copy already open. The creature stays as it is, published and in its
        encounters, until the gamemaster accepts the revision; it keeps its name and
        picture. Your own drafts need no copy."""
        registry = stores()
        found = registry.mcp_tokens.verify(bearer_of(ctx))
        if found is None:
            raise ToolError("This request carries no valid Paravent key.")
        service = registry.creatures
        try:
            design = service.get(creature)
        except CreatureNotFound:
            named = service.named(creature)
            if named is None:
                raise ToolError(f"No creature design {creature!r}") from None
            design = named
        if design.revision_of:
            return answer(design)
        if design.drafted_by:
            raise ToolError(f"{design.name} is still your draft: change it with update_creature.")
        with tool_errors():
            return answer(service.start_revision(design.id, found[0].id))


__all__ = ["register_creatures"]
