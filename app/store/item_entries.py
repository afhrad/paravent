"""Shared item text: ``{#itemEntry Name|SOURCE}`` in an item's entries.

122 items do not carry their own description. Their ``entries`` hold the single string
``"{#itemEntry Absorbing Tattoo|TCE}"``, naming a template in ``items-base.json``'s
``itemEntry`` array whose ``entriesTemplate`` has gaps the item fills:

    "…features designs that emphasize one color ({{item.detail1}})."
    "…you have resistance to {{getFullImmRes item.resist}} damage."

The reference is replaced at load by the template's entries, gaps filled from the item
(or, for a generic magic variant, from what it hands its items in ``inherits``). It is
structure, not markup — the renderer never sees ``{#…}`` — and a reference without a
source means the DMG, as everywhere in 5eTools's items.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.store.copyresolver import entry_key
from app.store.jsonfile import read_json

logger = logging.getLogger(__name__)

TEMPLATE_FILE = "items-base.json"
TEMPLATE_KEY = "itemEntry"
DEFAULT_SOURCE = "DMG"

_REFERENCE = re.compile(r"^\{#itemEntry ([^}]+)\}$")
_GAP = re.compile(r"\{\{\s*(getFullImmRes\s+)?item\.(\w+)\s*\}\}")


def read_templates(roots: Iterable[Path]) -> dict[str, list[Any]]:
    """Every ``itemEntry`` template under ``roots``, by ``name|source``."""
    templates: dict[str, list[Any]] = {}
    for root in roots:
        path = root / TEMPLATE_FILE
        if not path.is_file():
            continue
        try:
            data = read_json(path)
        except json.JSONDecodeError:
            continue  # reported by the compendium load, which reads the same file
        for raw in (data or {}).get(TEMPLATE_KEY) or [] if isinstance(data, dict) else []:
            if isinstance(raw, dict) and raw.get("name") and raw.get("source"):
                templates[entry_key(raw["name"], raw["source"])] = list(
                    raw.get("entriesTemplate") or []
                )
    return templates


class ItemEntries:
    """Expands ``{#itemEntry}`` references against a set of templates."""

    def __init__(self, templates: dict[str, list[Any]]) -> None:
        """``templates`` as :func:`read_templates` returns them."""
        self._templates = templates
        # ``name|source`` of references to no template, and ``item: gap`` of gaps the
        # item had nothing for — both left visible rather than guessed.
        self.missing: set[str] = set()
        self.unfilled: set[str] = set()

    def expand(self, item: dict[str, Any]) -> dict[str, Any]:
        """``item`` with every reference in its ``entries`` replaced — and in a generic
        variant's ``inherits``, what it hands its items; unchanged (the same dict) when it
        has none."""
        inherits = item.get("inherits")
        if isinstance(inherits, dict):
            expanded = self._expand_entries(inherits)
            if expanded is not inherits:
                item = {**item, "inherits": expanded}
        return self._expand_entries(item)

    def _expand_entries(self, item: dict[str, Any]) -> dict[str, Any]:
        entries = item.get("entries")
        if not isinstance(entries, list) or not any(
            isinstance(e, str) and e.startswith("{#itemEntry") for e in entries
        ):
            return item
        out: list[Any] = []
        for entry in entries:
            found = _REFERENCE.match(entry) if isinstance(entry, str) else None
            if found is None:
                out.append(entry)
                continue
            name, _, source = found.group(1).partition("|")
            template = self._templates.get(entry_key(name, source or DEFAULT_SOURCE))
            if template is None:
                self.missing.add(f"{name}|{source or DEFAULT_SOURCE}")
                out.append(entry)
                continue
            out.extend(self._fill(template, item))
        return {**item, "entries": out}

    def _fill(self, node: Any, item: dict[str, Any]) -> Any:
        if isinstance(node, str):
            return _GAP.sub(lambda m: self._value(item, m.group(2), bool(m.group(1)), m), node)
        if isinstance(node, list):
            return [self._fill(x, item) for x in node]
        if isinstance(node, dict):
            return {k: self._fill(v, item) for k, v in node.items()}
        return node

    def _value(self, item: dict[str, Any], field: str, full: bool, gap: re.Match[str]) -> str:
        inherits = item.get("inherits")
        value = item.get(field, inherits.get(field) if isinstance(inherits, dict) else None)
        if isinstance(value, list):
            words = [str(x) for x in value if isinstance(x, str)]
            if words:
                return _joined(words) if full else ", ".join(words)
        elif isinstance(value, str | int | float) and not isinstance(value, bool):
            return str(value)
        self.unfilled.add(f"{item.get('name', '?')}: {field}")
        return gap.group(0)


def _joined(words: list[str]) -> str:
    """ "acid", "acid and cold", "acid, cold, and fire" — as 5eTools's getFullImmRes."""
    if len(words) < 3:
        return " and ".join(words)
    return f"{', '.join(words[:-1])}, and {words[-1]}"


__all__ = ["ItemEntries", "read_templates"]
