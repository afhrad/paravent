"""The spell builder: spells of the table's own, published as homebrew.

A :class:`~app.models.gamestate.SpellDesign` is the record; the 5eTools spell is
derived from it here, and typed is what casting it from the sheet uses (the user's
decision, 2026-11-25): its header (level, school, casting time, range and area,
components, duration, concentration, ritual), the class lists it is on, and its effect —
a saving throw or a spell attack with damage, or healing — with what a higher slot adds,
or for a cantrip how its damage grows with the caster's level. Anything else it does is
its text.

The builder writes the effect's sentence and its "at higher levels" in the world's
language, with the tags the sheet reads (``{@damage 8d6}``, ``{@scaledamage
8d6|3-9|1d6}``, ``{@scaledice}``), so Cast rolls it, a save spell goes to the fight and
upcasting adds its dice. Healing is typed as well (``healing``: dice, whether the
caster's modifier is added, how many creatures, what a slot adds), as the sheet reads it
from English words otherwise. A design begun from a printed spell keeps that spell's text,
which already says its effect, and writes no sentence of its own unless asked.

Published, every design but Claude's drafts becomes a spell in
``library/homebrew/spells/spells-paravent.json`` under the source ``Homebrew``, and its
class lists go to ``library/homebrew/generated/gendata-spell-source-lookup.json``, which
the lookups merge with the corpus's: the wizard, level-up and the prepare page offer it
to those classes.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.gamestate import SourceSet, SpellDesign
from app.services import design_revisions
from app.services.creature_builder import entries_text, text_entries
from app.services.i18n import Translatable
from app.services.text import first_dice
from app.store.gamestate import GameStateStore
from app.store.jsonfile import write_json

HOMEBREW_SOURCE = "Homebrew"
SPELL_FILE = Path("spells") / "spells-paravent.json"
LOOKUP_FILE = Path("generated") / "gendata-spell-source-lookup.json"
SCHOOLS = ("A", "C", "D", "E", "V", "I", "N", "T")
ABILITY_WORDS = {
    "str": "strength",
    "dex": "dexterity",
    "con": "constitution",
    "int": "intelligence",
    "wis": "wisdom",
    "cha": "charisma",
}
DAMAGE_TYPES = (
    "acid", "bludgeoning", "cold", "fire", "force", "lightning", "necrotic", "piercing",
    "poison", "psychic", "radiant", "slashing", "thunder",
)  # fmt: skip
CANTRIP_STEPS = (1, 5, 11, 17)
_DICE = re.compile(r"^(\d+)d(\d+)$")
_SCALE = re.compile(r"\{@scale(?:damage|dice) ([^|}]+)\|[^|}]+\|([^|}]+)\}")

Lookup = Callable[[str, str, str], dict[str, Any] | None]  # (category, name, source)
Words = Callable[..., str]  # a translator: words(key, **params)


class SpellError(Translatable):
    """A design the builder refuses."""


class SpellNotFound(LookupError):
    """No design with that id."""


@dataclass
class Published:
    count: int
    worlds: list[str] = field(default_factory=list)


def _dice(text: str) -> tuple[int, int] | None:
    found = _DICE.match(text.replace(" ", ""))
    return (int(found[1]), int(found[2])) if found else None


def cantrip_scaling(dice: str) -> dict[str, str]:
    """A cantrip's damage at levels 1, 5, 11 and 17: one more die each step."""
    parsed = _dice(dice)
    if parsed is None:
        return {}
    count, faces = parsed
    return {str(level): f"{count * (n + 1)}d{faces}" for n, level in enumerate(CANTRIP_STEPS)}


def _range(design: SpellDesign) -> dict[str, Any]:
    if design.range_kind == "self" and design.area:
        kind = {"sphere": "sphere", "cylinder": "radius"}.get(design.area, design.area)
        return {"type": kind, "distance": {"type": "feet", "amount": design.area_size}}
    if design.range_kind in ("feet", "miles"):
        return {
            "type": "point",
            "distance": {"type": design.range_kind, "amount": design.range_amount},
        }
    return {"type": "point", "distance": {"type": design.range_kind}}


def _duration(design: SpellDesign) -> list[dict[str, Any]]:
    if design.duration_kind == "timed":
        out: dict[str, Any] = {
            "type": "timed",
            "duration": {"type": design.duration_unit, "amount": design.duration_amount},
        }
        if design.concentration:
            out["concentration"] = True
        return [out]
    if design.duration_kind == "permanent":
        return [{"type": "permanent", "ends": ["dispel"]}]
    return [{"type": design.duration_kind}]


def effect_sentence(design: SpellDesign, words: Words) -> str:
    """The effect in the world's language, with the tags the sheet reads; "" for none."""
    if design.effect == "none" or not design.dice.strip() or not design.write_effect:
        return ""
    dice = design.dice.replace(" ", "")
    kinds = " ".join(words(f"rules.damage.{x}") for x in design.damage_types)
    who = words("spellbuilder.effect.who_area" if design.area else "spellbuilder.effect.who_one")
    if design.effect == "save":
        key = "spellbuilder.effect.save_half" if design.half_on_save else "spellbuilder.effect.save"
        return words(
            key,
            who=who,
            save=words(f"rules.ability.{design.save}"),
            dice=f"{{@damage {dice}}}",
            type=kinds,
        )
    if design.effect == "attack":
        return words(
            f"spellbuilder.effect.attack_{design.attack}",
            dice=f"{{@damage {dice}}}",
            type=kinds,
        )
    key = "spellbuilder.effect.heal_mod" if design.heal_mod else "spellbuilder.effect.heal"
    many = design.heal_targets > 1
    return words(
        key + ("_many" if many else ""),
        dice=f"{{@dice {dice}}}",
        n=design.heal_targets,
    )


def higher_sentence(design: SpellDesign, words: Words) -> str:
    """What a higher slot adds (or a cantrip's growth), in the world's language; none
    where the builder writes no sentences (a printed spell's text says it)."""
    if not design.write_effect:
        return ""
    dice = design.dice.replace(" ", "")
    if design.effect in ("save", "attack") and design.level == 0 and _dice(dice):
        steps = cantrip_scaling(dice)
        return words(
            "spellbuilder.effect.cantrip",
            five=steps["5"],
            eleven=steps["11"],
            seventeen=steps["17"],
        )
    step = design.upcast.replace(" ", "")
    if not step or design.level == 0 or design.effect == "none" or not dice:
        return ""
    tag = "scaledice" if design.effect == "heal" else "scaledamage"
    return words(
        "spellbuilder.effect.upcast_heal"
        if design.effect == "heal"
        else "spellbuilder.effect.upcast",
        step=f"{{@{tag} {dice}|{design.level}-9|{step}}}",
        level=design.level,
        next=design.level + 1,
    )


def spell_of(design: SpellDesign, words: Words) -> dict[str, Any]:
    """The design as a 5eTools spell, its sentences in ``words``' language."""
    out: dict[str, Any] = {
        "name": design.name,
        "source": HOMEBREW_SOURCE,
        "level": design.level,
        "school": design.school,
        "time": [{"number": design.time_number, "unit": design.time_unit}],
        "range": _range(design),
    }
    components: dict[str, Any] = {}
    if design.verbal:
        components["v"] = True
    if design.somatic:
        components["s"] = True
    if design.material.strip():
        if design.material_cost or design.material_consumed:
            material: dict[str, Any] = {"text": design.material.strip()}
            if design.material_cost:
                material["cost"] = design.material_cost * 100
            if design.material_consumed:
                material["consume"] = True
            components["m"] = material
        else:
            components["m"] = design.material.strip()
    out["components"] = components
    out["duration"] = _duration(design)
    if design.ritual:
        out["meta"] = {"ritual": True}
    if design.effect == "save":
        out["savingThrow"] = [ABILITY_WORDS[design.save]]
    elif design.effect == "attack":
        out["spellAttack"] = [design.attack]
    if design.effect in ("save", "attack") and design.damage_types:
        out["damageInflict"] = list(design.damage_types)
    dice = design.dice.replace(" ", "")
    if design.effect in ("save", "attack") and design.level == 0 and _dice(dice):
        label = " ".join(design.damage_types) or "damage"
        out["scalingLevelDice"] = {"label": f"{label} damage", "scaling": cantrip_scaling(dice)}
    if design.effect == "heal" and dice:
        out["miscTags"] = ["HL"]
        out["healing"] = {
            "dice": dice,
            "mod": design.heal_mod,
            "targets": design.heal_targets,
            "upcast": design.upcast.replace(" ", ""),
        }
    lead = effect_sentence(design, words)
    out["entries"] = [x for x in (lead, *text_entries(design.text)) if x] or [""]
    higher = [x for x in (higher_sentence(design, words), *text_entries(design.higher_text)) if x]
    if higher:
        out["entriesHigherLevel"] = [
            {
                "type": "entries",
                "name": words(
                    "spellbuilder.cantrip_upgrade" if design.level == 0 else "spellbuilder.higher"
                ),
                "entries": higher,
            }
        ]
    return out


def from_spell(raw: dict[str, Any], classes: list[str]) -> dict[str, Any]:
    """A printed spell's typed fields and text, as a design's."""
    fields: dict[str, Any] = {"write_effect": False, "classes": classes}
    if isinstance(raw.get("level"), int):
        fields["level"] = raw["level"]
    if raw.get("school") in SCHOOLS:
        fields["school"] = raw["school"]
    for t in raw.get("time") or []:
        if t.get("unit") in ("action", "bonus", "reaction", "minute", "hour"):
            fields["time_unit"] = t["unit"]
            fields["time_number"] = int(t.get("number") or 1)
        break
    rng = raw.get("range") or {}
    distance = rng.get("distance") or {}
    if rng.get("type") == "point":
        kind = str(distance.get("type") or "")
        if kind in ("feet", "miles", "self", "touch", "sight", "unlimited"):
            fields["range_kind"] = kind
            if isinstance(distance.get("amount"), int):
                fields["range_amount"] = distance["amount"]
    elif rng.get("type") in ("cone", "cube", "line", "sphere", "radius", "emanation"):
        fields["range_kind"] = "self"
        fields["area"] = "cylinder" if rng["type"] == "radius" else rng["type"]
        if isinstance(distance.get("amount"), int):
            fields["area_size"] = distance["amount"]
    comps = raw.get("components") or {}
    fields["verbal"], fields["somatic"] = bool(comps.get("v")), bool(comps.get("s"))
    material = comps.get("m")
    if isinstance(material, str):
        fields["material"] = material[:300]
    elif isinstance(material, dict):
        fields["material"] = str(material.get("text") or "")[:300]
        cost = material.get("cost")
        if isinstance(cost, int):
            fields["material_cost"] = cost // 100
        fields["material_consumed"] = bool(material.get("consume"))
    for d in raw.get("duration") or []:
        kind = d.get("type")
        if kind in ("instant", "timed", "permanent", "special"):
            fields["duration_kind"] = kind
        if kind == "timed":
            amount = d.get("duration") or {}
            if amount.get("type") in ("round", "minute", "hour", "day"):
                fields["duration_unit"] = amount["type"]
                fields["duration_amount"] = int(amount.get("amount") or 1)
        fields["concentration"] = bool(d.get("concentration"))
        break
    fields["ritual"] = bool((raw.get("meta") or {}).get("ritual"))
    save = (raw.get("savingThrow") or [None])[0]
    if save:
        short = next((k for k, v in ABILITY_WORDS.items() if v == save), None)
        if short:
            fields["effect"], fields["save"] = "save", short
    elif raw.get("spellAttack"):
        fields["effect"] = "attack"
        fields["attack"] = "M" if raw["spellAttack"][0] == "M" else "R"
    elif "HL" in (raw.get("miscTags") or []):
        fields["effect"] = "heal"
    fields["damage_types"] = [
        x for x in raw.get("damageInflict") or [] if isinstance(x, str) and x in DAMAGE_TYPES
    ]
    tag = "dice" if fields.get("effect") == "heal" else "damage"
    found = first_dice(raw.get("entries"), tag)
    if found:
        fields["dice"] = found.replace(" ", "")[:40]
    scale = _SCALE.search(str(raw.get("entriesHigherLevel") or ""))
    if scale:
        fields["upcast"] = scale[2].replace(" ", "")[:40]
    fields["text"] = entries_text(raw.get("entries") or [])[:8000]
    higher = []
    for block in raw.get("entriesHigherLevel") or []:
        higher.append(entries_text(block.get("entries") if isinstance(block, dict) else block))
    fields["higher_text"] = "\n\n".join(x for x in higher if x)[:2000]
    return fields


class SpellBuilder:
    """Spell designs in the library, and their homebrew spells and class lists."""

    def __init__(
        self,
        store: GameStateStore,
        homebrew_dir: Path,
        lookup: Lookup,
        reload: Callable[[], object],
        *,
        words: Callable[[str | None], Words],
        class_lists: Callable[[str, str], dict[str, set[str]]] = lambda _n, _s: {},
    ) -> None:
        """``words(world)`` a translator in the world's language; ``class_lists(name,
        source)`` a printed spell's class lists, ``{class source: {class}}``."""
        self._store = store
        self._homebrew = homebrew_dir
        self._lookup = lookup
        self._reload = reload
        self._words = words
        self._class_lists = class_lists

    @property
    def spell_file(self) -> Path:
        return self._homebrew / SPELL_FILE

    @property
    def lookup_file(self) -> Path:
        return self._homebrew / LOOKUP_FILE

    def list(self, world_id: str | None = None) -> builtins.list[SpellDesign]:
        found = self._store.spell_designs.list(
            None if world_id is None else (lambda d: d.world_id == world_id)
        )
        return sorted(found, key=lambda d: d.name.casefold())

    def get(self, design_id: str) -> SpellDesign:
        found = self._store.spell_designs.find(design_id)
        if found is None:
            raise SpellNotFound(design_id)
        return found

    def named(self, name: str) -> SpellDesign | None:
        wanted = name.strip().casefold()
        return next(
            (d for d in self.list() if d.name.casefold() == wanted and not d.revision_of), None
        )

    def _clean_name(
        self, name: str, own_id: str | None = None, revision_of: str | None = None
    ) -> str:
        cleaned = " ".join(name.split())[:80]
        if not cleaned:
            raise SpellError("spellbuilder.error.name")
        if design_revisions.name_clash(self._store.spell_designs, cleaned, own_id, revision_of):
            raise SpellError("spellbuilder.error.name_taken", name=cleaned)
        return cleaned

    def create(
        self,
        name: str,
        world_id: str | None,
        based_on: str = "",
        *,
        drafted_by: str | None = None,
    ) -> SpellDesign:
        """A new design: from a printed spell (its fields, text and class lists), or
        blank (a 1st-level evocation)."""
        fields: dict[str, Any] = {}
        ref = based_on.strip()
        if ref:
            spell_name, _, source = ref.partition("|")
            raw = self._lookup("spell", spell_name.strip(), source.strip())
            if raw is None:
                raise SpellError("spellbuilder.error.based_on", ref=ref)
            lists = self._class_lists(str(raw.get("name")), str(raw.get("source")))
            classes = sorted(f"{c}|{src}" for src, names in lists.items() for c in names)
            fields = from_spell(raw, classes)
            ref = f"{raw.get('name')}|{raw.get('source')}"
            name = name.strip() or f"{raw.get('name')} (variant)"
        design = SpellDesign(
            name=self._clean_name(name),
            world_id=world_id or None,
            based_on=ref or None,
            drafted_by=drafted_by,
            **fields,
        )
        return self._store.spell_designs.create(design)

    def save(self, design: SpellDesign) -> SpellDesign:
        """Store a changed design (not yet published); a revision keeps its spell's
        name and world."""
        if design.revision_of:
            original = self.get(design.revision_of)
            design.name, design.world_id = original.name, original.world_id
        design.name = self._clean_name(design.name, design.id, design.revision_of)
        design.damage_types = [x for x in dict.fromkeys(design.damage_types) if x in DAMAGE_TYPES]
        design.classes = [c for c in dict.fromkeys(design.classes) if "|" in c][:40]
        if design.dice.strip() and _dice(design.dice) is None:
            raise SpellError("spellbuilder.error.dice", dice=design.dice)
        if design.upcast.strip() and _dice(design.upcast) is None:
            raise SpellError("spellbuilder.error.dice", dice=design.upcast)
        if design.duration_kind != "timed":
            design.concentration = False
        design.touch()
        return self._store.spell_designs.replace(design)

    def delete(self, design_id: str) -> SpellDesign:
        design = self.get(design_id)
        self._store.spell_designs.delete(design_id)
        if design.published_at is not None:
            self.publish()
        return design

    def spell(self, design: SpellDesign) -> dict[str, Any]:
        """The design as the 5eTools spell it publishes as."""
        return spell_of(design, self._words(design.world_id))

    def _hash(self, design: SpellDesign) -> str:
        text = json.dumps([self.spell(design), sorted(design.classes)], sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def unpublished(self, design: SpellDesign) -> bool:
        return design.published_hash != self._hash(design)

    def revision(self, design_id: str) -> SpellDesign | None:
        """The open revision of a design, if Claude has one."""
        return design_revisions.open_revision(self._store.spell_designs, design_id)

    def start_revision(self, design_id: str, drafted_by: str) -> SpellDesign:
        """A draft copy of an accepted design for Claude to change, or the one already
        open; the design itself stays as it is, published, until the copy is accepted."""
        original = self.get(design_id)
        if original.drafted_by:
            raise SpellError("itembuilder.error.revise_draft", name=original.name)
        return design_revisions.start(
            self._store.spell_designs, original, drafted_by, SpellDesign.model_validate
        )

    def changes(self, revision: SpellDesign) -> builtins.list[str]:
        """The fields a revision changes against its design, in the design's order."""
        return design_revisions.changes(self._store.spell_designs, revision)

    def accept_revision(self, design_id: str) -> SpellDesign:
        """Write a revision onto its spell (same id and name), drop the copy, and
        publish when the spell was published."""
        changed = design_revisions.accept(
            self._store.spell_designs,
            self.get(design_id),
            lambda design, update: design.model_copy(update=update),
        )
        if changed.published_at is not None:
            self.publish()
        return self.get(changed.id)

    def accept(self, design_id: str) -> SpellDesign:
        """Claude's draft taken in as the gamemaster's own, then published."""
        design = self.get(design_id)
        self._store.spell_designs.update(design.id, drafted_by=None)
        self.publish()
        return self.get(design.id)

    def publish(self) -> Published:
        """Write every design but Claude's drafts to the homebrew spell file and their
        class lists to the homebrew lookup, then reload the compendium."""
        designs = [d for d in self.list() if d.drafted_by is None]
        now = datetime.now(UTC)
        write_json(
            self._ensure(self.spell_file),
            {
                "_meta": {
                    "sources": [
                        {
                            "json": HOMEBREW_SOURCE,
                            "abbreviation": HOMEBREW_SOURCE,
                            "full": "Homebrew (Paravent)",
                        }
                    ],
                    "note": "Written by Paravent from library/spell_designs.json; edits here "
                    "are replaced on the next publish.",
                },
                "spell": [self.spell(d) for d in designs],
            },
        )
        lists: dict[str, Any] = {}
        for design in designs:
            by_source: dict[str, dict[str, bool]] = {}
            for ref in design.classes:
                cls, _, source = ref.partition("|")
                by_source.setdefault(source.strip(), {})[cls.strip()] = True
            if by_source:
                lists[design.name.lower()] = {"class": by_source}
        write_json(self._ensure(self.lookup_file), {HOMEBREW_SOURCE: lists})
        for design in designs:
            self._store.spell_designs.update(
                design.id, published_at=now, published_hash=self._hash(design)
            )
        worlds: builtins.list[str] = []
        for world_id in {d.world_id for d in designs if d.world_id}:
            world = self._store.worlds.find(world_id)
            if world is None or world.source_set.allows(HOMEBREW_SOURCE):
                continue
            sources = [*world.source_set.sources, HOMEBREW_SOURCE]
            self._store.worlds.update(world.id, source_set=SourceSet(sources=sources))
            worlds.append(world.name)
        self._reload()
        return Published(len(designs), sorted(worlds))

    @staticmethod
    def _ensure(path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


__all__ = [
    "ABILITY_WORDS",
    "DAMAGE_TYPES",
    "SCHOOLS",
    "SpellBuilder",
    "SpellError",
    "SpellNotFound",
    "cantrip_scaling",
    "effect_sentence",
    "from_spell",
    "higher_sentence",
    "spell_of",
]
