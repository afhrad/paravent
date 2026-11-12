"""5eTools entries as plain text, for places that show rules text before the full renderer.

The real ``{@...}`` renderer — links, tooltips, roll chips — is its own piece of work
(documentation/DESIGN.md §6). Until it exists, the character sheet still has to show a
spell's description, so this reduces entries to readable paragraphs: every tag becomes its
display text, nested tags included, and nothing is ever shown as a raw brace.

Tags nest (``{@note see {@spell fireball}}``), so tags are replaced innermost-first until
none remain; a flat regex over the whole string would cut a nested tag in half.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

# The innermost tag: no braces inside its body.
_INNERMOST = re.compile(r"\{@(\w+)(?:\s([^{}]*))?\}")

# Entity tags whose third slot is display text (documentation/TRANSLATION.md §3).
# Not deity or card: {@deity Bahamut|Nonhuman|PHB} puts the pantheon in slot 2 and the
# source in slot 3 ({@card} the deck), so their display text is slot 4.
_DISPLAY_IN_SLOT_3 = frozenset(
    {
        "action",
        "background",
        "boon",
        "charoption",
        "class",
        "condition",
        "creature",
        "crochet",
        "cult",
        "deck",
        "disease",
        "facility",
        "feat",
        "hazard",
        "item",
        "itemMastery",
        "language",
        "object",
        "optfeature",
        "psionic",
        "race",
        "recipe",
        "reward",
        "sense",
        "skill",
        "spell",
        "status",
        "table",
        "trap",
        "variantrule",
        "vehicle",
        "vehupgrade",
        "classFeature",
        "subclassFeature",
    }
)

_ATTACK_KINDS = {
    "mw": "Melee Weapon Attack:",
    "rw": "Ranged Weapon Attack:",
    "mw,rw": "Melee or Ranged Weapon Attack:",
    "ms": "Melee Spell Attack:",
    "rs": "Ranged Spell Attack:",
    "ms,rs": "Melee or Ranged Spell Attack:",
    "m": "Melee Attack:",
    "r": "Ranged Attack:",
}


def _replace(match: re.Match[str]) -> str:
    tag, body = match.group(1), match.group(2) or ""
    parts = body.split("|")
    first = parts[0].strip()
    if tag == "h":
        return "Hit: "
    if tag == "m":
        return "Miss: "
    if tag in ("atk", "atkr"):
        return _ATTACK_KINDS.get(first, "Attack:")
    if tag == "hit":
        return f"+{first}" if first and not first.startswith(("+", "-")) else first
    if tag == "dc":
        return f"DC {first}"
    if tag == "recharge":
        return f"(Recharge {first}–6)" if first and first != "6" else "(Recharge 6)"
    if tag in ("scaledamage", "scaledice"):
        return parts[-1].strip() if len(parts) >= 3 else first
    if tag == "chance":
        return f"{first} percent"
    if tag in _DISPLAY_IN_SLOT_3 and len(parts) >= 3 and parts[2].strip():
        return parts[2].strip()
    if tag in ("deity", "card") and len(parts) >= 4 and parts[3].strip():
        return parts[3].strip()
    return first


def strip_tags(text: str) -> str:
    """``"Deals {@damage 8d6} {@condition prone||knocked down}"`` → display text."""
    previous = None
    while previous != text:
        previous = text
        text = _INNERMOST.sub(_replace, text)
    return text


def paragraphs(entries: Any, *, limit: int | None = None, tags: bool = False) -> list[str]:
    """Flatten 5eTools ``entries`` into plain paragraphs.

    Named sub-entries keep their name as a lead-in ("Sculpt Spells. You can…"); list
    items become their own paragraphs, prefixed with a bullet; tables are summarised by
    their caption, since a table does not survive being flattened into prose. With
    ``tags``, the ``{@...}`` tags are kept, for the tag renderer to draw as links.
    """
    out: list[str] = []
    _walk(entries, out, clean=_keep if tags else strip_tags)
    return out[:limit] if limit is not None else out


def _keep(text: str) -> str:
    return text


def _walk(node: Any, out: list[str], lead: str = "", *, clean: Callable[[str], str]) -> None:
    if node is None:
        return
    if isinstance(node, str):
        text = clean(node).strip()
        if text:
            out.append(f"{lead}{text}" if lead else text)
        return
    if isinstance(node, list):
        for i, child in enumerate(node):
            _walk(child, out, lead if i == 0 else "", clean=clean)
        return
    if not isinstance(node, dict):
        return
    kind = node.get("type")
    name = node.get("name")
    prefix = f"{clean(str(name))}. " if name else lead
    if kind == "list":
        for item in node.get("items", []):
            if isinstance(item, dict) and item.get("type") == "item":
                label = clean(str(item.get("name", ""))).strip()
                item_entries = item.get("entries") or item.get("entry")
                sub: list[str] = []
                _walk(item_entries, sub, clean=clean)
                text = " ".join(sub)
                out.append(f"• {label} {text}".strip())
            else:
                sub = []
                _walk(item, sub, clean=clean)
                out.extend(f"• {s}" for s in sub)
        return
    if kind == "table":
        caption = node.get("caption")
        if caption:
            out.append(f"[{clean(str(caption))}]")
        return
    if kind in (
        "entries",
        "section",
        "inset",
        "insetReadaloud",
        "quote",
        "variant",
        "variantSub",
        "options",
        "inline",
        "inlineBlock",
        "item",
    ):
        _walk(node.get("entries") or node.get("entry"), out, prefix, clean=clean)
        return
    if "entries" in node:
        _walk(node["entries"], out, prefix, clean=clean)


def first_dice(entries: Any, tag: str = "damage") -> str | None:
    """The first ``{@damage …}`` expression in some entries — a spell's base damage."""
    pattern = re.compile(r"\{@" + re.escape(tag) + r"\s([^|}]+)")
    for text in _strings(entries):
        found = pattern.search(text)
        if found:
            return found.group(1).strip()
    return None


def _strings(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for child in node:
            yield from _strings(child)
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)


__all__ = ["first_dice", "paragraphs", "strip_tags"]
