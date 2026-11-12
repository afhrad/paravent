"""Resolution of the 5eTools ``_copy`` inheritance directive.

An entry with ``_copy`` is a *patch*, not a record: it names a parent by
``(name, source)`` and carries a ``_mod`` block describing edits. 1,141 monsters (25%
of the bestiary) and 53 items use it. Left unresolved they render as blank statblocks,
so this runs during load, before anything is handed to the rest of the application.

The twelve ``_mod`` modes implemented here are exactly those that occur in the corpus,
with their measured frequencies:

===================== ===== ============================================
mode                  uses  effect
===================== ===== ============================================
``replaceTxt``          802  regex-replace a phrase throughout the entry
``replaceArr``          179  swap one named array element
``appendArr``           115  append to an array
``prependArr``           51  prepend to an array
``removeArr``            38  drop named array elements
``replaceSpells``        14  swap spells inside a spellcasting block
``addSkills``             9  add skill proficiencies
``appendIfNotExistsArr``  7  append only when absent
``addSpells``             6  add spells to a spellcasting block
``setProp``               2  set an arbitrary property
``removeSpells``          1  drop spells from a spellcasting block
``insertArr``             1  insert at an index
===================== ===== ============================================

Monster templates (``bestiary/template.json``, applied through ``_copy._templates``) use
eight more, as 5eTools defines them: ``addSenses`` (44 — a drow's darkvision),
``scalarAddHit`` and ``scalarAddDc`` (6 each), ``scalarAddProp`` (2), ``maxSize``,
``scalarMultProp``, ``prefixSuffixStringProp`` and ``scalarMultXp`` (1 each, all
*Reduced Threat*).

An unknown mode is reported rather than silently ignored: upstream adds modes with new
books, and a silently skipped patch produces a statblock that is subtly wrong, which is
far worse than one that is loudly missing.
"""

from __future__ import annotations

import copy as copy_module
import json
import math
import re
from functools import lru_cache
from typing import Any

# Keys that describe the copy itself and must never survive into the resolved entry.
_COPY_META_KEYS = frozenset({"_copy", "_mod", "_templates", "_preserve"})

# Parent fields that are dropped unless the child names them in ``_preserve``.
# These are provenance, not content: inheriting them would claim the child appeared on
# the parent's page, in the parent's SRD listing, with the parent's reprint history.
_UNPRESERVED_KEYS = frozenset(
    {
        "page",
        "otherSources",
        "srd",
        "srd52",
        "basicRules",
        "basicRules2024",
        "reprintedAs",
        "hasFluff",
        "hasFluffImages",
        "hasToken",
        "isReprinted",
    }
)


def entry_key(name: str, source: str) -> str:
    """Build the case-insensitive index key for an entry."""
    return f"{name.lower()}|{source.lower()}"


# Fields that discriminate entries sharing a ``(name, source)``. Subclasses and their
# features repeat names across classes and levels, so a two-part key makes a copy of a
# *different* record look like a self-reference. Appended in a fixed order so the index
# and the lookup always agree.
_DISCRIMINATORS: tuple[str, ...] = (
    "className",
    "classSource",
    "subclassShortName",
    "subclassSource",
    "level",
)


def composite_key(entry: dict[str, Any]) -> str:
    """The index key for a raw entry, including any discriminators it carries.

    Degrades to :func:`entry_key` for the categories that need nothing more, so the
    bestiary and item indexes are unaffected.
    """
    parts = [
        str(entry.get("name", "")).lower(),
        str(entry.get("source", "")).lower(),
    ]
    parts += [str(entry[field]).lower() for field in _DISCRIMINATORS if field in entry]
    return "|".join(parts)


class CopyResolver:
    """Resolves ``_copy`` directives against an index of raw entries.

    Operates on plain dicts rather than models: resolution has to happen before
    validation, since an unresolved shell often lacks fields the model requires.
    """

    def __init__(self, index: dict[str, dict[str, Any]]) -> None:
        """``index`` maps :func:`entry_key` to the raw entry dict."""
        self._index = index
        self.unknown_modes: set[str] = set()
        # ``name|source`` of every ``_templates`` reference not found in the index.
        self.missing_templates: set[str] = set()

    def resolve(self, entry: dict[str, Any], _seen: frozenset[str] | None = None) -> dict[str, Any]:
        """Return ``entry`` with its ``_copy`` chain applied.

        Entries without ``_copy`` are returned unchanged. Chains are followed
        recursively — a copy of a copy is common — and cycles raise rather than
        recursing until the stack dies.
        """
        directive = entry.get("_copy")
        if not directive:
            return entry

        # Keys must carry the same discriminators on both sides, or a subclass copying
        # from its namesake in another class reads as a cycle. The Artificer's Alchemist
        # (classSource EFA) copies the Alchemist with classSource TCE — identical name
        # and source, different record.
        parent_key = composite_key(directive)
        self_key = composite_key(entry)

        seen = _seen or frozenset()
        if parent_key in seen:
            raise ValueError(f"Circular _copy chain at {self_key!r} -> {parent_key!r}")

        parent = self._index.get(parent_key)
        if parent is None:
            from app.store.errors import CopyResolutionError

            raise CopyResolutionError(self_key, parent_key)

        # The parent may itself be a copy; resolve it first.
        parent = self.resolve(parent, seen | {parent_key})
        result = copy_module.deepcopy(parent)

        # Provenance belongs to the parent, not the child, unless explicitly preserved.
        preserve = directive.get("_preserve") or {}
        for key in _UNPRESERVED_KEYS:
            if key in result and not preserve.get(key):
                del result[key]

        # The child's own fields win over the inherited ones.
        for key, value in entry.items():
            if key not in _COPY_META_KEYS:
                result[key] = value

        # Templates are shared patch bundles, applied before the entry's own _mod.
        own = {key for key in entry if key not in _COPY_META_KEYS}
        for template in directive.get("_templates") or []:
            self._apply_template(result, template, own)

        for prop, instructions in (directive.get("_mod") or {}).items():
            for instruction in _as_list(instructions):
                self._apply_mod(result, prop, instruction)

        result.pop("_copy", None)
        return result

    # -- mod application ---------------------------------------------------------

    def _apply_template(
        self, target: dict[str, Any], template: dict[str, Any], own: set[str]
    ) -> None:
        """Apply a named template ("Skeleton", "Reduced Threat") to a resolved entry.

        Templates live in ``bestiary/template.json`` as ``{"apply": {"_root": …,
        "_mod": …}}``: ``_root`` sets fields (a skeleton's type is undead) unless the
        entry sets them itself, ``_mod`` patches like an entry's own. A template may
        ``_copy`` another. One not in the index is skipped rather than failing the entry
        — the base statblock is still correct, only the variant trimmings are missing —
        and recorded in :attr:`missing_templates`.
        """
        key = entry_key(template.get("name", ""), template.get("source", ""))
        definition = self._index.get(key)
        if definition is None:
            self.missing_templates.add(f"{template.get('name', '')}|{template.get('source', '')}")
            return
        definition = self.resolve(definition)
        apply = definition.get("apply") or {}
        for prop, value in (apply.get("_root") or {}).items():
            if prop not in own:
                replaced = target.get(prop)
                target[prop] = copy_module.deepcopy(value)
                # A legendary group replaced by the template's is kept beside it: the
                # template's group may only add to it (``legendaryGroupTemplate``).
                if (
                    prop == "legendaryGroup"
                    and isinstance(replaced, dict)
                    and isinstance(target[prop], dict)
                ):
                    target[prop]["_base"] = copy_module.deepcopy(replaced)
        for prop, instructions in (apply.get("_mod") or definition.get("_mod") or {}).items():
            for instruction in _as_list(instructions):
                self._apply_mod(target, prop, instruction)

    def apply_mods(self, target: dict[str, Any], mods: dict[str, Any]) -> None:
        """Apply a ``_mod`` block (``{prop: instruction or [instructions]}``) to
        ``target`` in place."""
        for prop, instructions in mods.items():
            for instruction in _as_list(instructions):
                self._apply_mod(target, prop, instruction)

    def _apply_mod(self, target: dict[str, Any], prop: str, mod: Any) -> None:
        """Apply one ``_mod`` instruction to ``target``.

        ``prop`` is the field being patched; the wildcard ``"*"`` means "every field",
        and ``"_"`` means "the entry as a whole" — both are used by ``replaceTxt``.
        """
        if mod == "remove":
            target.pop(prop, None)
            return
        if not isinstance(mod, dict):
            return

        mode = mod.get("mode")
        handler = getattr(self, f"_mod_{_snake(mode)}", None) if mode else None
        if handler is None:
            self.unknown_modes.add(str(mode))
            return
        handler(target, prop, mod)

    def _mod_replace_txt(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Regex-replace a phrase throughout the entry (802 uses — the most common).

        Used to re-skin a generic statblock as a named NPC: *"the kobold"* becomes
        *"Myla"* everywhere the text mentions it.
        """
        pattern = re.compile(
            re.escape(mod["replace"]) if not mod.get("regex") else mod["replace"],
            re.IGNORECASE if "i" in (mod.get("flags") or "") else 0,
        )
        replacement = mod["with"]
        # "*" and "_" both mean "not one named field": "*" walks the standard
        # text-bearing arrays, "_" walks the whole entry.
        if prop == "*":
            fields = mod.get("props") or [
                "action",
                "bonus",
                "reaction",
                "trait",
                "legendary",
                "mythic",
                "variant",
                "spellcasting",
                "entries",
            ]
        elif prop == "_":
            fields = list(target)
        else:
            fields = [prop]

        for field in fields:
            if field in target:
                target[field] = _replace_in(target[field], pattern, replacement)

    def _mod_append_arr(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Append items to an array, creating it when absent (115 uses)."""
        target.setdefault(prop, [])
        target[prop] = list(target[prop]) + _as_list(mod.get("items"))

    def _mod_prepend_arr(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Prepend items to an array (51 uses)."""
        target.setdefault(prop, [])
        target[prop] = _as_list(mod.get("items")) + list(target[prop])

    def _mod_insert_arr(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Insert items at an index (1 use)."""
        target.setdefault(prop, [])
        index = mod.get("index", 0)
        items = _as_list(mod.get("items"))
        existing = list(target[prop])
        target[prop] = existing[:index] + items + existing[index:]

    def _mod_append_if_not_exists_arr(
        self, target: dict[str, Any], prop: str, mod: dict[str, Any]
    ) -> None:
        """Append only the items not already present (7 uses)."""
        existing = list(target.get(prop) or [])
        for item in _as_list(mod.get("items")):
            if item not in existing:
                existing.append(item)
        target[prop] = existing

    def _mod_remove_arr(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Drop array elements by name, or by exact value (38 uses)."""
        if prop not in target:
            return
        if "names" in mod:
            names = {n.lower() for n in _as_list(mod["names"])}
            target[prop] = [
                item
                for item in target[prop]
                if not (isinstance(item, dict) and str(item.get("name", "")).lower() in names)
            ]
        elif "items" in mod:
            drop = _as_list(mod["items"])
            target[prop] = [item for item in target[prop] if item not in drop]

    def _mod_replace_arr(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Swap one named array element for replacement items (179 uses)."""
        if prop not in target:
            return
        needle = mod.get("replace")
        items = _as_list(mod.get("items"))
        name = needle.get("name") if isinstance(needle, dict) else needle

        out: list[Any] = []
        replaced = False
        for item in target[prop]:
            matches = (
                isinstance(item, dict)
                and isinstance(name, str)
                and str(item.get("name", "")).lower() == name.lower()
            ) or item == needle
            if matches and not replaced:
                out.extend(items)
                replaced = True
            else:
                out.append(item)
        # An unmatched replace still contributes its items; upstream treats the
        # instruction as authoritative about what the final entry should contain.
        if not replaced:
            out.extend(items)
        target[prop] = out

    def _mod_set_prop(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Set a property outright (2 uses)."""
        target[prop] = mod.get("value")

    def _mod_add_skills(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Add skill proficiencies, expressed as multiples of the proficiency bonus.

        9 uses. The value is a multiplier (1 = proficient, 2 = expertise), so the
        resulting modifier depends on the creature's ability score and PB. Computing it
        needs the resolved statblock, which is exactly what we have here.
        """
        skills = dict(target.get("skill") or {})
        for skill, multiplier in (mod.get("skills") or {}).items():
            ability = _SKILL_ABILITY.get(skill.lower(), "int")
            score = target.get(ability)
            bonus = _proficiency_bonus(target)
            modifier = _ability_modifier(score) + bonus * int(multiplier)
            skills[skill] = f"{modifier:+d}"
        target["skill"] = skills

    def _mod_add_spells(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Merge spells into the first spellcasting block (6 uses)."""
        block = _first_spellcasting(target)
        if block is None:
            return
        for slot in ("will", "daily", "rest", "weekly", "spells"):
            addition = mod.get(slot)
            if addition is None:
                continue
            if isinstance(addition, list):
                block[slot] = list(block.get(slot) or []) + addition
            elif isinstance(addition, dict):
                merged = dict(block.get(slot) or {})
                for key, value in addition.items():
                    if isinstance(value, list):
                        merged[key] = list(merged.get(key) or []) + value
                    else:
                        merged[key] = value
                block[slot] = merged

    def _mod_replace_spells(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Swap individual spells inside a spellcasting block (14 uses)."""
        block = _first_spellcasting(target)
        if block is None:
            return
        for slot, instructions in (mod.get("spells") or {}).items():
            container = block.get("spells", {}).get(slot)
            if not isinstance(container, dict):
                continue
            spells = list(container.get("spells") or [])
            for instruction in _as_list(instructions):
                needle, replacement = instruction.get("replace"), instruction.get("with")
                spells = [replacement if s == needle else s for s in spells]
            container["spells"] = spells
        for slot in ("will", "daily"):
            instructions = mod.get(slot)
            if not instructions:
                continue
            if slot == "will":
                spells = list(block.get("will") or [])
                for instruction in _as_list(instructions):
                    spells = [
                        instruction.get("with") if s == instruction.get("replace") else s
                        for s in spells
                    ]
                block["will"] = spells

    def _mod_add_senses(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Add a sense, or lengthen one the creature has ("darkvision 60 ft." → 120)."""
        senses = [str(x) for x in target.get("senses") or []]
        for sense in _as_list(mod.get("senses")):
            if not isinstance(sense, dict) or not sense.get("type"):
                continue
            kind, reach = str(sense["type"]), int(sense.get("range", 0))
            pattern = re.compile(rf"{re.escape(kind)} (\d+)", re.IGNORECASE)
            for index, text in enumerate(senses):
                found = pattern.search(text)
                if found:
                    if int(found.group(1)) < reach:
                        senses[index] = f"{kind} {reach} ft."
                    break
            else:
                senses.append(f"{kind} {reach} ft.")
        target["senses"] = senses

    def _mod_max_size(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Cap the creature's size: anything larger than ``max`` becomes ``max``."""
        largest = str(mod.get("max", ""))
        if largest not in _SIZES:
            return
        cap = _SIZES.index(largest)
        sizes = [
            s if s not in _SIZES or _SIZES.index(s) <= cap else largest
            for s in _as_list(target.get("size"))
        ]
        target["size"] = list(dict.fromkeys(sizes))

    def _mod_scalar_add_prop(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Add to numbers in an object (``save``, ``skill``); ``prop`` ``*`` is every key.

        Bonuses written as strings stay strings ("+5" − 2 → "+3"); non-numbers are left.
        """
        values = target.get(prop)
        if not isinstance(values, dict):
            return
        keys = list(values) if mod.get("prop") == "*" else [str(mod.get("prop"))]
        for key in keys:
            if key in values:
                values[key] = _add_scalar(values[key], mod.get("scalar", 0))

    def _mod_scalar_mult_prop(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Multiply a number in an object (``hp.average`` × 0.5), floored if asked."""
        values = target.get(prop)
        if not isinstance(values, dict):
            return
        keys = list(values) if mod.get("prop") == "*" else [str(mod.get("prop"))]
        for key in keys:
            value = values.get(key)
            if isinstance(value, int | float) and not isinstance(value, bool):
                result = value * float(mod.get("scalar", 1))
                values[key] = math.floor(result) if mod.get("floor") else result

    def _mod_prefix_suffix_string_prop(
        self, target: dict[str, Any], prop: str, mod: dict[str, Any]
    ) -> None:
        """Wrap a string in an object (``hp.formula`` → "floor((4d8) ÷ 2)")."""
        values = target.get(prop)
        key = str(mod.get("prop"))
        if isinstance(values, dict) and isinstance(values.get(key), str):
            values[key] = f"{mod.get('prefix', '')}{values[key]}{mod.get('suffix', '')}"

    def _mod_scalar_add_hit(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Add to every ``{@hit N}`` in a field (attacks of a weaker variant)."""
        scalar = int(mod.get("scalar", 0))
        self._retag(target, prop, _HIT, lambda m: f"{{@hit {int(m.group(1)) + scalar}}}")

    def _mod_scalar_add_dc(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Add to every ``{@dc N}`` in a field, keeping any display text after it."""
        scalar = int(mod.get("scalar", 0))
        self._retag(
            target,
            prop,
            _DC,
            lambda m: f"{{@dc {int(m.group(1)) + scalar}{m.group(2) or ''}}}",
        )

    @staticmethod
    def _retag(target: dict[str, Any], prop: str, pattern: re.Pattern[str], replace: Any) -> None:
        if prop in target:
            text = json.dumps(target[prop], ensure_ascii=False)
            target[prop] = json.loads(pattern.sub(replace, text))

    def _mod_scalar_mult_xp(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Scale the XP a creature is worth, keeping its CR: ``cr`` becomes
        ``{"cr": "5", "xp": 900}``."""
        cr = target.get("cr")
        current = cr.get("xp") if isinstance(cr, dict) else None
        rating = cr.get("cr") if isinstance(cr, dict) else cr
        if current is None:
            current = _xp_by_cr().get(str(rating))
        if current is None:
            return
        xp = float(current) * float(mod.get("scalar", 1))
        scaled = math.floor(xp) if mod.get("floor") else xp
        target["cr"] = {**(cr if isinstance(cr, dict) else {"cr": rating}), "xp": scaled}

    def _mod_remove_spells(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> None:
        """Drop spells from a spellcasting block (1 use)."""
        block = _first_spellcasting(target)
        if block is None:
            return
        for slot, to_remove in (mod.get("spells") or {}).items():
            container = block.get("spells", {}).get(slot)
            if isinstance(container, dict):
                drop = set(_as_list(to_remove))
                container["spells"] = [s for s in container.get("spells") or [] if s not in drop]


# -- helpers ---------------------------------------------------------------------

_SIZES = ("T", "S", "M", "L", "H", "G")
_HIT = re.compile(r"\{@hit ([-+]?\d+)\}")
_DC = re.compile(r"\{@dc (\d+)(\|[^}]*)?\}")


def _add_scalar(value: Any, scalar: Any) -> Any:
    """``value`` + ``scalar``, keeping a signed string a signed string."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value + scalar
    if isinstance(value, str):
        try:
            total = int(value.strip()) + int(scalar)
        except ValueError:
            return value
        return f"{total:+d}"
    return value


@lru_cache(maxsize=1)
def _xp_by_cr() -> dict[str, int]:
    """XP per CR, as extracted from the DMG (``data/rules/encounterdifficulty.json``)."""
    from app.store.rulestables import RULES_FILE

    try:
        data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    table = data.get("xp_by_cr") if isinstance(data, dict) else None
    return {str(k): int(v) for k, v in (table or {}).items()}


_SKILL_ABILITY: dict[str, str] = {
    "athletics": "str",
    "acrobatics": "dex",
    "sleight of hand": "dex",
    "stealth": "dex",
    "arcana": "int",
    "history": "int",
    "investigation": "int",
    "nature": "int",
    "religion": "int",
    "animal handling": "wis",
    "insight": "wis",
    "medicine": "wis",
    "perception": "wis",
    "survival": "wis",
    "deception": "cha",
    "intimidation": "cha",
    "performance": "cha",
    "persuasion": "cha",
}


def _as_list(value: Any) -> list[Any]:
    """Normalise a value that may arrive as a single item or a list."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _snake(mode: str) -> str:
    """``replaceTxt`` -> ``replace_txt``, to map a mode onto its handler method."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", mode).lower()


def _replace_in(value: Any, pattern: re.Pattern[str], replacement: str) -> Any:
    """Recursively regex-replace inside any nested string."""
    if isinstance(value, str):
        return pattern.sub(replacement.replace("\\", "\\\\"), value)
    if isinstance(value, list):
        return [_replace_in(item, pattern, replacement) for item in value]
    if isinstance(value, dict):
        return {k: _replace_in(v, pattern, replacement) for k, v in value.items()}
    return value


def _ability_modifier(score: Any) -> int:
    """The 5e ability modifier for a score: ``(score - 10) // 2``."""
    if not isinstance(score, int):
        return 0
    return (score - 10) // 2


def _proficiency_bonus(entry: dict[str, Any]) -> int:
    """Derive the proficiency bonus from challenge rating.

    5e ties PB to CR: +2 up to CR 4, then +1 every four CRs.
    """
    cr = entry.get("cr")
    if isinstance(cr, dict):
        cr = cr.get("cr")
    if not isinstance(cr, str):
        return 2
    if "/" in cr:
        return 2
    try:
        value = int(cr)
    except ValueError:
        return 2
    return max(2, 2 + (value - 1) // 4)


def _first_spellcasting(entry: dict[str, Any]) -> dict[str, Any] | None:
    """Return the entry's first spellcasting block, or None when it has none."""
    blocks = entry.get("spellcasting")
    if isinstance(blocks, list) and blocks and isinstance(blocks[0], dict):
        return blocks[0]
    return None
