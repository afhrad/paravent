"""The item builder: magic items of the table's own, published as homebrew.

An :class:`~app.models.gamestate.ItemDesign` is the record; the 5eTools item is derived
from it here, and only what the character sheet applies is typed (the user's decision,
2026-11-25): a weapon's attack and damage bonus, an armour's, shield's or worn item's
AC, the saving throw, spell attack, spell save DC and ability check bonuses, damage
resistances, immunities and vulnerabilities, ability scores set ("Strength 19") or
raised (attuned only), charges with what restores them, and the spells it casts at will
or for charges. Anything else it does is its text, which the table applies.

A weapon, armour, shield, ammunition or staff is made on a base item of the world's
books and takes its numbers (damage, properties, AC, weight) from it, as the books'
own magic weapons name their ``baseItem``. A design starts blank or from a printed magic
item, whose typed fields and text it copies. Its price is its rarity's value by the
world's guide unless one is set (a consumable at half, as the guide prices them).

Published, every design but Claude's drafts becomes an item in
``library/homebrew/items/items-paravent.json`` under the source ``Homebrew`` and loads
with the corpus: search, reference pages, treasure, shops and the sheet find it like any
other. The file is rewritten whole from the designs; edits made to it by hand are lost.
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
from typing import Any, get_args

from app.models.gamestate import ItemDesign, ItemKind, ItemSpell, Rarity, SourceSet
from app.services import design_revisions
from app.services.creature_builder import entries_text, text_entries
from app.services.i18n import Translatable
from app.store.gamestate import GameStateStore
from app.store.jsonfile import write_json

HOMEBREW_SOURCE = "Homebrew"
HOMEBREW_FILE = Path("items") / "items-paravent.json"
KINDS: tuple[str, ...] = get_args(ItemKind)
RARITIES: tuple[str, ...] = get_args(Rarity)
ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
DAMAGE_TYPES = (
    "acid", "bludgeoning", "cold", "fire", "force", "lightning", "necrotic", "piercing",
    "poison", "psychic", "radiant", "slashing", "thunder",
)  # fmt: skip
RECHARGES = ("dawn", "dusk", "midnight", "restLong")
# The kinds made on a base item, and the base item types each takes.
BASED: dict[str, tuple[str, ...]] = {
    "weapon": ("M", "R"),
    "armor": ("LA", "MA", "HA"),
    "shield": ("S",),
    "ammunition": ("A", "AF"),
    "staff": ("M",),
}
# The item type a kind is filed under, by the guide the world plays (2014, 2024).
TYPES: dict[str, tuple[str, str]] = {
    "ring": ("RG|DMG", "RG|XDMG"),
    "rod": ("RD|DMG", "RD|XDMG"),
    "wand": ("WD|DMG", "WD|XDMG"),
    "potion": ("P|PHB", "P|XPHB"),
    "scroll": ("SC|DMG", "SC|XPHB"),
}
CONSUMABLE = ("potion", "scroll", "ammunition")
# What a base item lends a magic item made on it.
BASE_FIELDS = (
    "type", "weaponCategory", "property", "mastery", "range", "dmg1", "dmg2", "dmgType",
    "ac", "strength", "stealth", "weight", "weapon", "armor", "ammoType", "packContents",
)  # fmt: skip
_DICE_TAG = re.compile(r"\{@dice ([^}|]+)[^}]*\}")
_BONUS = re.compile(r"^[+]?(\d+)$")

Lookup = Callable[[str, str, str], dict[str, Any] | None]  # (category, name, source)


class ItemError(Translatable):
    """A design the builder refuses."""


class ItemNotFound(LookupError):
    """No design with that id."""


@dataclass
class Published:
    """What a publish did."""

    count: int
    worlds: list[str] = field(default_factory=list)  # names of worlds given Homebrew


def _ref(name: str, source: str) -> str:
    return f"{name}|{source}"


def _split(ref: str) -> tuple[str, str]:
    name, _, source = ref.partition("|")
    return name.strip(), source.strip()


def _plus(value: int) -> str:
    return f"+{value}"


def _bonus(raw: Any) -> int:
    found = _BONUS.match(str(raw or "").strip())
    return min(3, int(found[1])) if found else 0


def kind_of(raw: dict[str, Any], base: dict[str, Any] | None) -> str:
    """Which of the builder's kinds a printed item is."""
    code = str(raw.get("type") or (base or {}).get("type") or "").partition("|")[0]
    if raw.get("staff"):
        return "staff"
    for kind, codes in BASED.items():
        if kind != "staff" and code in codes:
            return kind
    for kind, codes in TYPES.items():
        if code in {c.partition("|")[0] for c in codes}:
            return kind
    return "wondrous"


def item_of(
    design: ItemDesign, base: dict[str, Any] | None, *, newer: bool, value: int | None
) -> dict[str, Any]:
    """The design as a 5eTools item; ``base`` its base item's record, ``newer`` the
    2024 guide's type codes, ``value`` its price in copper (None for none)."""
    out: dict[str, Any] = {"name": design.name, "source": HOMEBREW_SOURCE}
    if base is not None and design.kind in BASED:
        out.update({k: base[k] for k in BASE_FIELDS if k in base})
        out["baseItem"] = _ref(str(base.get("name")), str(base.get("source"))).lower()
    if design.kind == "staff":
        out["staff"] = True
    elif design.kind == "wondrous":
        out["wondrous"] = True
    elif design.kind in TYPES:
        out["type"] = TYPES[design.kind][1 if newer else 0]
    out["rarity"] = design.rarity
    if design.attunement:
        out["reqAttune"] = design.attune_by.strip() or True
    for key, amount in (
        ("bonusWeapon", design.bonus_weapon),
        ("bonusAc", design.bonus_ac),
        ("bonusSavingThrow", design.bonus_saving_throw),
        ("bonusSpellAttack", design.bonus_spell_attack),
        ("bonusSpellSaveDc", design.bonus_spell_save_dc),
        ("bonusAbilityCheck", design.bonus_ability_check),
    ):
        if amount:
            out[key] = _plus(amount)
    for key, values in (
        ("resist", design.resist),
        ("immune", design.immune),
        ("vulnerable", design.vulnerable),
    ):
        if values:
            out[key] = list(values)
    ability: dict[str, Any] = {k: v for k, v in design.ability_raise.items() if v}
    if design.ability_set:
        ability["static"] = dict(design.ability_set)
    if ability:
        out["ability"] = ability
    if design.charges:
        out["charges"] = design.charges
        if design.recharge:
            out["recharge"] = design.recharge
            restore = design.recharge_amount.strip()
            if restore.isdigit():
                out["rechargeAmount"] = int(restore)
            elif restore:
                out["rechargeAmount"] = f"{{@dice {restore}}}"
    attached: dict[str, Any] = {}
    for spell in design.spells:
        ref = spell.spell.strip().lower()
        if not ref:
            continue
        if spell.charges:
            attached.setdefault("charges", {}).setdefault(str(spell.charges), []).append(ref)
        else:
            attached.setdefault("will", []).append(ref)
    if attached:
        out["attachedSpells"] = attached
    if design.weight is not None:
        out["weight"] = design.weight
    if value:
        out["value"] = value
    out["entries"] = text_entries(design.text) or [""]
    return out


def from_item(raw: dict[str, Any], base: dict[str, Any] | None) -> dict[str, Any]:
    """A printed magic item's typed fields and text, as a design's."""
    fields: dict[str, Any] = {"kind": kind_of(raw, base)}
    if base is not None:
        fields["base_item"] = _ref(str(base.get("name")), str(base.get("source")))
    rarity = str(raw.get("rarity") or "")
    if rarity in RARITIES:
        fields["rarity"] = rarity
    attune = raw.get("reqAttune")
    if attune:
        fields["attunement"] = True
        if isinstance(attune, str):
            fields["attune_by"] = attune[:120]
    for key, name in (
        ("bonusWeapon", "bonus_weapon"),
        ("bonusAc", "bonus_ac"),
        ("bonusSavingThrow", "bonus_saving_throw"),
        ("bonusSpellAttack", "bonus_spell_attack"),
        ("bonusSpellSaveDc", "bonus_spell_save_dc"),
        ("bonusAbilityCheck", "bonus_ability_check"),
    ):
        if _bonus(raw.get(key)):
            fields[name] = _bonus(raw.get(key))
    for key in ("resist", "immune", "vulnerable"):
        found = [x for x in raw.get(key) or [] if isinstance(x, str) and x in DAMAGE_TYPES]
        if found:
            fields[key] = found
    ability = raw.get("ability")
    if isinstance(ability, dict):
        static = ability.get("static")
        if isinstance(static, dict):
            fields["ability_set"] = {
                k: v for k, v in static.items() if k in ABILITIES and isinstance(v, int)
            }
        fields["ability_raise"] = {
            k: v for k, v in ability.items() if k in ABILITIES and isinstance(v, int)
        }
    charges = raw.get("charges")
    if isinstance(charges, int) and charges > 0:
        fields["charges"] = min(100, charges)
        if str(raw.get("recharge") or "") in RECHARGES:
            fields["recharge"] = raw["recharge"]
            amount = raw.get("rechargeAmount")
            if isinstance(amount, int):
                fields["recharge_amount"] = str(amount)
            elif isinstance(amount, str):
                dice = _DICE_TAG.search(amount)
                fields["recharge_amount"] = (dice[1] if dice else amount).replace(" ", "")[:40]
    attached = raw.get("attachedSpells")
    spells: list[ItemSpell] = []
    if isinstance(attached, dict):
        spells += [ItemSpell(spell=str(r)) for r in attached.get("will") or []]
        for cost, refs in (attached.get("charges") or {}).items():
            if str(cost).isdigit():
                spells += [ItemSpell(spell=str(r), charges=int(cost)) for r in refs or []]
    elif isinstance(attached, list):
        spells += [ItemSpell(spell=str(r)) for r in attached if isinstance(r, str)]
    if spells:
        fields["spells"] = spells[:20]
    fields["text"] = entries_text(raw.get("entries") or [])[:8000]
    weight = raw.get("weight")
    if isinstance(weight, int | float) and (base is None or weight != base.get("weight")):
        fields["weight"] = float(weight)
    return fields


class ItemBuilder:
    """Item designs in the library, and their homebrew items."""

    def __init__(
        self,
        store: GameStateStore,
        homebrew_dir: Path,
        lookup: Lookup,
        reload: Callable[[], object],
        *,
        newer: Callable[[str | None], bool] = lambda _w: False,
        rarity_values: Callable[[str | None], dict[str, int]] = lambda _w: {},
    ) -> None:
        """Bind to the library, the homebrew folder and a corpus lookup
        (``lookup(category, name, source)``); ``reload`` re-reads the compendium once
        the file is written; ``newer(world)`` whether the world plays the 2024 guide;
        ``rarity_values(world)`` copper by rarity, by its guide."""
        self._store = store
        self._homebrew = homebrew_dir
        self._lookup = lookup
        self._reload = reload
        self._newer = newer
        self._values = rarity_values

    @property
    def homebrew_file(self) -> Path:
        return self._homebrew / HOMEBREW_FILE

    def list(self, world_id: str | None = None) -> builtins.list[ItemDesign]:
        """Every design, or a world's, by name."""
        found = self._store.item_designs.list(
            None if world_id is None else (lambda d: d.world_id == world_id)
        )
        return sorted(found, key=lambda d: d.name.casefold())

    def get(self, design_id: str) -> ItemDesign:
        found = self._store.item_designs.find(design_id)
        if found is None:
            raise ItemNotFound(design_id)
        return found

    def named(self, name: str, world_id: str | None = None) -> ItemDesign | None:
        """A design by name (never a revision, which shares its item's name)."""
        wanted = name.strip().casefold()
        return next(
            (d for d in self.list(world_id) if d.name.casefold() == wanted and not d.revision_of),
            None,
        )

    def _clean_name(
        self, name: str, own_id: str | None = None, revision_of: str | None = None
    ) -> str:
        cleaned = " ".join(name.split())[:80]
        if not cleaned:
            raise ItemError("itembuilder.error.name")
        if design_revisions.name_clash(self._store.item_designs, cleaned, own_id, revision_of):
            raise ItemError("itembuilder.error.name_taken", name=cleaned)
        return cleaned

    def base(self, ref: str) -> dict[str, Any] | None:
        """A base weapon, armour or shield of the books by ``Name|SOURCE``."""
        name, source = _split(ref)
        if not name:
            return None
        return self._lookup("baseitem", name, source) or self._lookup("item", name, source)

    def create(
        self,
        name: str,
        world_id: str | None,
        kind: str = "wondrous",
        based_on: str = "",
        base_item: str = "",
        *,
        drafted_by: str | None = None,
    ) -> ItemDesign:
        """A new design: from a printed magic item (its fields and text copied), or
        blank of a kind, a weapon or armour on its base item."""
        fields: dict[str, Any] = {"kind": kind if kind in KINDS else "wondrous"}
        ref = based_on.strip()
        if ref:
            item_name, source = _split(ref)
            raw = self._lookup("item", item_name, source)
            if raw is None:
                raise ItemError("itembuilder.error.based_on", ref=ref)
            base = self.base(str(raw.get("baseItem") or ""))
            fields = from_item(raw, base)
            ref = _ref(str(raw.get("name")), str(raw.get("source")))
            name = name.strip() or f"{raw.get('name')} (variant)"
        if base_item.strip():
            fields["base_item"] = base_item.strip()
        design = ItemDesign(
            name=self._clean_name(name),
            world_id=world_id or None,
            based_on=ref or None,
            drafted_by=drafted_by,
            **fields,
        )
        self._check_base(design)
        return self._store.item_designs.create(design)

    def _check_base(self, design: ItemDesign) -> None:
        """A weapon, armour, shield, ammunition or staff needs a base item of a fitting
        type; a staff without one is made on the quarterstaff."""
        if design.kind not in BASED:
            design.base_item = ""
            return
        if design.kind == "staff" and not design.base_item:
            design.base_item = "Quarterstaff|" + ("XPHB" if self._newer(design.world_id) else "PHB")
        base = self.base(design.base_item)
        code = str((base or {}).get("type") or "").partition("|")[0]
        if base is None or code not in BASED[design.kind]:
            raise ItemError(f"itembuilder.error.base_{design.kind}", ref=design.base_item)
        design.base_item = _ref(str(base.get("name")), str(base.get("source")))

    def save(self, design: ItemDesign) -> ItemDesign:
        """Store a changed design (not yet published); a revision keeps its item's name
        and world."""
        if design.revision_of:
            original = self.get(design.revision_of)
            design.name, design.world_id = original.name, original.world_id
        design.name = self._clean_name(design.name, design.id, design.revision_of)
        self._check_base(design)
        design.resist = [x for x in dict.fromkeys(design.resist) if x in DAMAGE_TYPES]
        design.immune = [x for x in dict.fromkeys(design.immune) if x in DAMAGE_TYPES]
        design.vulnerable = [x for x in dict.fromkeys(design.vulnerable) if x in DAMAGE_TYPES]
        design.ability_set = {
            k: max(1, min(30, v)) for k, v in design.ability_set.items() if k in ABILITIES and v
        }
        design.ability_raise = {
            k: max(1, min(4, v)) for k, v in design.ability_raise.items() if k in ABILITIES and v
        }
        if not design.charges:
            design.recharge, design.recharge_amount = "", ""
        design.spells = [s for s in design.spells if s.spell.strip()][:20]
        design.touch()
        return self._store.item_designs.replace(design)

    def delete(self, design_id: str) -> ItemDesign:
        design = self.get(design_id)
        self._store.item_designs.delete(design_id)
        if design.published_at is not None:
            self.publish()
        return design

    def value(self, design: ItemDesign) -> int | None:
        """Its price in copper: as set, else its rarity's by the world's guide (a
        consumable at half); None where the guide prices nothing (an artifact)."""
        if design.value is not None:
            return design.value
        found = self._values(design.world_id).get(design.rarity)
        if not found:
            return None
        return found // 2 if design.kind in CONSUMABLE else found

    def item(self, design: ItemDesign) -> dict[str, Any]:
        """The design as the 5eTools item it publishes as."""
        base = self.base(design.base_item) if design.kind in BASED else None
        return item_of(design, base, newer=self._newer(design.world_id), value=self.value(design))

    def _hash(self, design: ItemDesign) -> str:
        text = json.dumps(self.item(design), sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def unpublished(self, design: ItemDesign) -> bool:
        """Whether the design differs from what was last published."""
        return design.published_hash != self._hash(design)

    def revision(self, design_id: str) -> ItemDesign | None:
        """The open revision of a design, if Claude has one."""
        return design_revisions.open_revision(self._store.item_designs, design_id)

    def start_revision(self, design_id: str, drafted_by: str) -> ItemDesign:
        """A draft copy of an accepted design for Claude to change, or the one already
        open; the design itself stays as it is, published, until the copy is accepted."""
        original = self.get(design_id)
        if original.drafted_by:
            raise ItemError("itembuilder.error.revise_draft", name=original.name)
        return design_revisions.start(
            self._store.item_designs, original, drafted_by, ItemDesign.model_validate
        )

    def changes(self, revision: ItemDesign) -> builtins.list[str]:
        """The fields a revision changes against its item, in the design's order."""
        return design_revisions.changes(self._store.item_designs, revision)

    def accept_revision(self, design_id: str) -> ItemDesign:
        """Write a revision onto its item (same id and name, so characters carrying it
        keep it), drop the copy, and publish when the item was published."""
        changed = design_revisions.accept(
            self._store.item_designs,
            self.get(design_id),
            lambda design, update: design.model_copy(update=update),
        )
        if changed.published_at is not None:
            self.publish()
        return self.get(changed.id)

    def accept(self, design_id: str) -> ItemDesign:
        """Claude's draft taken in as the gamemaster's own, then published."""
        design = self.get(design_id)
        self._store.item_designs.update(design.id, drafted_by=None)
        self.publish()
        return self.get(design.id)

    def publish(self) -> Published:
        """Write every design but Claude's drafts to the homebrew file and reload the
        compendium; a world with a design that declares its books gets ``Homebrew``."""
        designs = [d for d in self.list() if d.drafted_by is None]
        now = datetime.now(UTC)
        data = {
            "_meta": {
                "sources": [
                    {
                        "json": HOMEBREW_SOURCE,
                        "abbreviation": HOMEBREW_SOURCE,
                        "full": "Homebrew (Paravent)",
                    }
                ],
                "note": "Written by Paravent from library/item_designs.json; edits here "
                "are replaced on the next publish.",
            },
            "item": [self.item(d) for d in designs],
        }
        self.homebrew_file.parent.mkdir(parents=True, exist_ok=True)
        write_json(self.homebrew_file, data)
        for design in designs:
            self._store.item_designs.update(
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


__all__ = [
    "BASED",
    "DAMAGE_TYPES",
    "HOMEBREW_FILE",
    "HOMEBREW_SOURCE",
    "KINDS",
    "RARITIES",
    "RECHARGES",
    "ItemBuilder",
    "ItemError",
    "ItemNotFound",
    "Published",
    "from_item",
    "item_of",
    "kind_of",
]
