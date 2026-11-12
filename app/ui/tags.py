"""The ``{@tag}`` renderer: 5eTools text and entries to HTML (documentation/DESIGN.md §6).

Two layers:

* **Inline** — a string such as ``"{@atk mw} {@hit 5} to hit. {@h}{@damage 1d8+3}"``
  becomes text with links and rollable chips. Tags **nest** (``{@note … {@spell
  fireball} …}``), so the string is parsed into a tree first; a flat regex would cut a
  nested tag in half.
* **Block** — an ``entries`` structure (paragraphs, named traits, lists, tables, insets)
  becomes the elements around that text.

What each tag does is decided by the catalogue in ``data/library/tags/5etools_tags.json``
(110 tags, measured on the corpus): its category says whether it is an entity link, a
roll, a shorthand, formatting or navigation.

* **Entity tags** resolve through the source set (TECHSTACK.md §6c). A resolved one is a
  link to its entry with the category's icon; an unresolved one is grey text with a
  dotted underline whose title says *why* — not in the imported data, or not among this
  world's books (DESIGN.md §6). Never a dead link, never a raw brace.
* **Roll tags** are chips. Given a roll form (on a character sheet) a chip rolls into
  that character's log, without JavaScript too; otherwise it carries ``data-roll`` for a
  page script to roll locally.
* Text inside a tag is always rendered, so a nested tag in a display slot still works.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from htpy import (
    Element,
    Node,
    a,
    aside,
    blockquote,
    button,
    caption,
    div,
    em,
    h4,
    kbd,
    li,
    mark,
    p,
    s,
    span,
    strong,
    sub,
    sup,
    table,
    tbody,
    td,
    th,
    thead,
    tr,
    u,
    ul,
)
from htpy import code as code_el

from app.models.gamestate import SourceSet
from app.store.reference import Outcome, parse_reference
from app.ui.icons import available, icon

CATALOGUE = Path(__file__).resolve().parents[2] / "data" / "library" / "tags" / "5etools_tags.json"

# Entity tag → the compendium category it resolves in. Tags naming something the corpus
# load does not include (books, adventures…) render as plain text. ``{@card}`` is not
# here: a card is reached through its deck (:func:`_card`).
ENTITY_CATEGORY: dict[str, tuple[str, ...]] = {
    "creature": ("monster",),
    "spell": ("spell",),
    "item": ("item", "baseitem", "magicvariant", "itemGroup"),
    "condition": ("condition",),
    "status": ("status",),
    "skill": ("skill",),
    "sense": ("sense",),
    "action": ("action",),
    "variantrule": ("variantrule",),
    "background": ("background",),
    "feat": ("feat",),
    "race": ("race", "subrace"),
    "class": ("class",),
    "deity": ("deity",),
    "disease": ("disease",),
    "hazard": ("hazard",),
    "trap": ("trap",),
    "object": ("object",),
    "language": ("language",),
    "table": ("table", "tableGroup"),
    "reward": ("reward",),
    "optfeature": ("optionalfeature",),
    "legroup": ("legendaryGroup",),
    "legendaryGroup": ("legendaryGroup",),
    "psionic": ("psionic",),
    "vehicle": ("vehicle",),
    "vehupgrade": ("vehicleUpgrade",),
    "facility": ("facility",),
    "charoption": ("charoption",),
    "cult": ("cult",),
    "boon": ("boon",),
    "recipe": ("recipe",),
    "crochet": ("crochetPattern",),
    "itemMastery": ("itemMastery",),
    "itemProperty": ("itemProperty",),
    "deck": ("deck",),
}

# The CSS class a link takes, so spells read violet, creatures red… (DESIGN.md §6).
KIND_CLASS = {
    "creature": "creature",
    "spell": "spell",
    "item": "item",
    "condition": "cond",
    "status": "cond",
}

ATTACK_KINDS = {
    "mw": "Melee Weapon Attack:",
    "rw": "Ranged Weapon Attack:",
    "mw,rw": "Melee or Ranged Weapon Attack:",
    "rw,mw": "Melee or Ranged Weapon Attack:",
    "ms": "Melee Spell Attack:",
    "rs": "Ranged Spell Attack:",
    "ms,rs": "Melee or Ranged Spell Attack:",
    "m": "Melee Attack Roll:",
    "r": "Ranged Attack Roll:",
    "m,r": "Melee or Ranged Attack Roll:",
    "r,m": "Melee or Ranged Attack Roll:",
}

ABILITY_FULL = {
    "str": "Strength",
    "dex": "Dexterity",
    "con": "Constitution",
    "int": "Intelligence",
    "wis": "Wisdom",
    "cha": "Charisma",
}

SHORTHAND: dict[str, str] = {
    "h": "Hit:",
    "m": "Miss:",
    "hom": "Hit or Miss:",
    "actSaveFail": "Failure:",
    "actSaveSuccess": "Success:",
    "actSaveSuccessOrFail": "Failure or Success:",
    "actTrigger": "Trigger:",
    "actResponse": "Response:",
}

# {@color text|#hex} hardcodes a colour that breaks in dark mode; map it onto the
# nearest semantic token instead (design-reference/HANDOFF.md).
_COLOR_TOKENS = (
    ("dmg", (210, 40, 40)),
    ("heal", (40, 150, 80)),
    ("magic", (130, 70, 190)),
    ("cond", (200, 130, 20)),
    ("accent", (60, 100, 230)),
)

_DICE = re.compile(r"^\s*[+-]?\s*(\d*d\d+|\d+)(\s*[+-]\s*(\d*d\d+|\d+))*\s*$", re.I)


@lru_cache(maxsize=1)
def catalogue() -> dict[str, dict[str, Any]]:
    """The tag catalogue, by tag name."""
    try:
        data = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {entry["tag"]: entry for entry in data.get("tags", []) if "tag" in entry}


# -- parsing --------------------------------------------------------------------------


@dataclass(frozen=True)
class Tag:
    """One parsed ``{@name part|part|…}``; parts are raw and may hold nested tags."""

    name: str
    parts: tuple[str, ...]

    def part(self, index: int) -> str:
        """A slot, stripped, or "" when absent."""
        return self.parts[index].strip() if index < len(self.parts) else ""


def _split_top_level(body: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for index, char in enumerate(body):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == "|" and depth == 0:
            parts.append(body[start:index])
            start = index + 1
    parts.append(body[start:])
    return parts


def parse(text: str) -> list[str | Tag]:
    """Split ``text`` into plain strings and :class:`Tag` objects, outermost first."""
    out: list[str | Tag] = []
    index, plain_start = 0, 0
    while True:
        start = text.find("{@", index)
        if start < 0:
            break
        depth, cursor = 0, start
        while cursor < len(text):
            if text[cursor] == "{":
                depth += 1
            elif text[cursor] == "}":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        if cursor >= len(text):  # unterminated: leave the rest as text
            break
        if start > plain_start:
            out.append(text[plain_start:start])
        inner = text[start + 2 : cursor]
        name, _, body = inner.partition(" ")
        out.append(Tag(name, tuple(_split_top_level(body)) if body else ()))
        index = plain_start = cursor + 1
    if plain_start < len(text):
        out.append(text[plain_start:])
    return out


def plain(text: str) -> str:
    """The display text of ``text`` with every tag reduced to what it shows."""
    nodes = parse(text)
    out = []
    for index, node in enumerate(nodes):
        out.append(_plain_node(node))
        if isinstance(node, Tag) and node.name in LABEL_TAGS:
            following = nodes[index + 1] if index + 1 < len(nodes) else None
            if not (isinstance(following, str) and following[:1].isspace()):
                out.append(" ")
    return "".join(out)


def _plain_node(node: str | Tag) -> str:
    if isinstance(node, str):
        return node
    if node.name in SHORTHAND:
        return SHORTHAND[node.name]
    if node.name in ("atk", "atkr"):
        return ATTACK_KINDS.get(node.part(0).replace(" ", ""), "Attack:")
    if node.name == "actSave":
        return f"{ABILITY_FULL.get(node.part(0).lower(), node.part(0).title())} Saving Throw:"
    if node.name == "actSaveFailBy":
        return f"Failure by {node.part(0)} or More:"
    if node.name == "hit":
        return _signed(node.part(0))
    if node.name == "dc":
        return f"DC {node.part(0)}"
    display = _display_slot(node)
    return plain(display)


def _display_slot(tag: Tag) -> str:
    if tag.name in ("deity", "card"):
        return tag.part(3) or tag.part(0)
    if tag.name in ENTITY_CATEGORY and tag.part(2):
        return tag.part(2)
    if tag.name in ("dice", "damage", "autodice", "d20", "chance") and tag.part(1):
        return tag.part(1)
    if tag.name in ("scaledamage", "scaledice"):
        return tag.part(2) or tag.part(0)
    if tag.name in ("filter", "link", "5etools", "book", "adventure", "area"):
        return tag.part(0)
    return tag.part(0)


def _signed(value: str) -> str:
    value = value.strip()
    return value if value.startswith(("+", "-")) else f"+{value}"


# -- rendering ------------------------------------------------------------------------


@dataclass(frozen=True)
class RenderContext:
    """What rendering needs to know about where the text is shown.

    ``resolver``/``source_set`` decide which entry a reference means. ``world_id`` rides
    along on links so the reference page resolves in the same world. ``roll_form`` is
    the id of a form that rolls a dice expression (a character's roll action); without
    one, chips carry ``data-roll`` for a page script. ``missing_label`` phrases the two
    unresolved cases in the reader's language.
    """

    resolver: Any | None = None
    source_set: SourceSet | None = None
    world_id: str | None = None
    roll_form: str | None = None
    # Chips that show their dice but do not roll — inside a preview, which is a glance.
    static_rolls: bool = False
    # Consulted before any entry block is rendered: ``hook(entry, default)`` returns the
    # nodes to show instead, or None to render as usual. ``default()`` renders it as
    # usual, so a hook can wrap an entry or add after it (a book chapter's notes).
    block_hook: Callable[[dict[str, Any], Callable[[], Node]], Node | None] | None = None
    # A resolved entry's name in the reader's language (category, English name), from the
    # term glossary; the link still points at the English entry (TRANSLATION.md §3).
    term: Callable[[str, str], str | None] | None = None
    # The letter dice are written with: "d" in English, "W" in German ("2W6"). Only the
    # text shown changes; a chip still rolls its formula as written.
    dice_letter: str = "d"
    # Whether the reader may open an entry (category, entry), for a player
    # (documentation/PLAYER-VIEW.md): one they may not is plain text, no link. None: all.
    readable: Callable[[str, Any], bool] | None = None
    # Labels the renderer prints itself ("Melee Weapon Attack:", "Hit:", "DC 15") in the
    # reader's language: ``label(key, **params)`` from the UI catalogue, or None for
    # the English defaults. Translations keep these tags verbatim, so the label is what
    # makes a translated statblock read in one language.
    label: Callable[..., str] | None = None
    missing_label: Callable[[str, str], str] = field(
        default=lambda case, book: (
            f"{book} is not among this world's books."
            if case == "excluded"
            else f"“{book}” is not in your imported data."
        )
    )


def localized_context(
    t: Callable[..., str],
    *,
    resolver: Any,
    source_set: SourceSet | None,
    world_id: str | None = None,
    roll_form: str | None = None,
    readable: Callable[[str, Any], bool] | None = None,
) -> RenderContext:
    """A context whose "not in your books" messages are in the reader's language;
    ``readable`` (a page's) keeps a player's links to what they may read."""

    def missing(case: str, book: str) -> str:
        return t("ref.missing_excluded" if case == "excluded" else "ref.missing_absent", book=book)

    return RenderContext(
        resolver=resolver,
        source_set=source_set,
        world_id=world_id,
        roll_form=roll_form,
        readable=readable,
        term=getattr(t, "term", None),
        dice_letter=dice_letter(t),
        label=t,
        missing_label=missing,
    )


_DICE_TEXT = re.compile(r"(?<![A-Za-z])(\d*)d(\d+)")


def dice_letter(t: Callable[..., str]) -> str:
    """The reader's letter for dice, from the catalogue ("d" when it has none)."""
    letter = t("rules.dice_letter")
    return letter if letter and letter != "rules.dice_letter" else "d"


def dice_text(text: str, letter: str) -> str:
    """``2d6 + 3`` written with ``letter``: ``2W6 + 3``. Words with a "d" are left alone."""
    if letter == "d":
        return text
    return _DICE_TEXT.sub(lambda m: f"{m.group(1)}{letter}{m.group(2)}", text)


def ref_url(category: str, name: str, source: str, world_id: str | None = None) -> str:
    """The reference page of an entry."""
    url = f"/ref/{quote(category)}/{quote(source or '-')}/{quote(name, safe='')}"
    return f"{url}?{urlencode({'world': world_id})}" if world_id else url


def card_anchor(name: str) -> str:
    """The anchor of a card on its deck's page: ``"The Void"`` → ``"card-the-void"``."""
    return "card-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def card_url(deck_url: str, card: str) -> str:
    """A card's place on its deck's page; ``card=`` makes the preview show the card."""
    joiner = "&" if "?" in deck_url else "?"
    return f"{deck_url}{joiner}{urlencode({'card': card})}#{card_anchor(card)}"


def _icon_for(tag_name: str) -> Node:
    name = (catalogue().get(tag_name) or {}).get("icon")
    if name and name in available():
        return icon(name, "xs")
    return None


# Tags that print a leading label ("Hit:", "Melee Weapon Attack:"). The corpus is
# inconsistent about the space after them — "{@atk mw} {@hit 4}" but "{@h}5 (" — so a
# space is added only when the following text does not already start with one.
LABEL_TAGS = frozenset({*SHORTHAND, "atk", "atkr", "actSave", "actSaveFailBy"})


def render(text: str, ctx: RenderContext | None = None) -> list[Node]:
    """Render one string of 5eTools text to inline nodes."""
    ctx = ctx or RenderContext()
    nodes = parse(text)
    out: list[Node] = []
    for index, node in enumerate(nodes):
        out.append(_render_node(node, ctx))
        if isinstance(node, Tag) and node.name in LABEL_TAGS:
            following = nodes[index + 1] if index + 1 < len(nodes) else None
            if not (isinstance(following, str) and following[:1].isspace()):
                out.append(" ")
    return out


def _inline(text: str, ctx: RenderContext) -> list[Node]:
    return render(text, ctx)


def _render_node(node: str | Tag, ctx: RenderContext) -> Node:
    if isinstance(node, str):
        return node
    name = node.name
    if name in ENTITY_CATEGORY:
        return _entity(node, ctx)
    if name == "card":
        return _card(node, ctx)
    handler = _HANDLERS.get(name)
    if handler is not None:
        return handler(node, ctx)
    # Unknown or not-yet-meaningful tags (books, filters, comics…): their text.
    return _inline(_display_slot(node), ctx)


def _entity(tag: Tag, ctx: RenderContext) -> Node:
    payload = "|".join(tag.parts)
    name, source, display = parse_reference(payload, tag.name)
    label = _inline(display or name, ctx)
    kind = KIND_CLASS.get(tag.name, "rule")
    if ctx.resolver is None:
        return span(class_=f"ref {kind} inert")[_icon_for(tag.name), label]
    outcome = None
    for category in ENTITY_CATEGORY[tag.name]:
        result = ctx.resolver.resolve_parts(category, name, source, ctx.source_set)
        if result.entry is not None:
            entry = result.entry
            entry_source = str(getattr(entry, "source", "") or "")
            localized = ctx.term(category, str(getattr(entry, "name", name))) if ctx.term else None
            # {@itemProperty F} names a code; with no display text, show the entry's name.
            coded = not display and getattr(entry, "ref_name", name) != getattr(entry, "name", name)
            shown: Node = localized or (str(entry.name) if coded else label)
            if ctx.readable is not None and not ctx.readable(category, entry):
                return span(class_="ref-plain")[shown]
            return a(
                href=ref_url(
                    category, str(getattr(entry, "name", name)), entry_source, ctx.world_id
                ),
                class_=f"ref {kind}",
                data_ref=f"{category}:{entry_source}",
            )[_icon_for(tag.name), shown]
        outcome = outcome or result.outcome
    case = "excluded" if outcome is Outcome.NOT_IN_SOURCE_SET else "absent"
    book = source.upper() if source else name
    return span(class_="ref missing", title=ctx.missing_label(case, book))[label]


def _card(tag: Tag, ctx: RenderContext) -> Node:
    """``{@card Sun|Deck of Many Things|DMG}`` — a link to the card on its deck's page.

    A card is not addressable by ``(name, source)`` alone, so the deck resolves through
    the world's books like any entity, and the card is found on its page.
    """
    name, deck, source = tag.part(0), tag.part(1), tag.part(2) or None
    label = _inline(_display_slot(tag), ctx)
    if ctx.resolver is None or not deck:
        return span(class_="ref rule inert")[_icon_for("card"), label]
    result = ctx.resolver.resolve_parts("deck", deck, source, ctx.source_set)
    if result.entry is None and result.outcome is Outcome.NOT_IMPORTED and source:
        # A card's source may name a printing of the deck the corpus files elsewhere.
        result = ctx.resolver.resolve_parts("deck", deck, None, ctx.source_set)
    entry = result.entry
    if entry is None:
        case = "excluded" if result.outcome is Outcome.NOT_IN_SOURCE_SET else "absent"
        book = source.upper() if source else deck
        return span(class_="ref missing", title=ctx.missing_label(case, book))[label]
    if ctx.readable is not None and not ctx.readable("deck", entry):
        return span(class_="ref-plain")[label]
    entry_source = str(getattr(entry, "source", "") or "")
    deck_url = ref_url("deck", str(getattr(entry, "name", deck)), entry_source, ctx.world_id)
    return a(href=card_url(deck_url, name), class_="ref rule", data_ref=f"deck:{entry_source}")[
        _icon_for("card"), label
    ]


def _chip(
    expr: str,
    label: Node,
    ctx: RenderContext,
    *,
    icon_name: str = "dices",
    title: str | None = None,
) -> Element:
    expr = expr.replace(" ", "")
    glyph = icon(icon_name, "xs") if icon_name in available() else None
    if not _DICE.match(expr) or ctx.static_rolls:
        return span(class_="ref dice static", title=title)[glyph, label]
    if ctx.roll_form:
        return button(
            type="submit",
            form=ctx.roll_form,
            name="expr",
            value=expr,
            class_="ref dice",
            title=title or expr,
        )[glyph, label]
    return button(type="button", class_="ref dice", data_roll=expr, title=title or expr)[
        glyph, label
    ]


def _dice(tag: Tag, ctx: RenderContext) -> Node:
    expr = tag.part(0)
    shown = _inline(dice_text(_display_slot(tag), ctx.dice_letter), ctx)
    return _chip(expr, shown, ctx)


def _hit(tag: Tag, ctx: RenderContext) -> Node:
    bonus = _signed(tag.part(0))
    return _chip(f"1d20{bonus}", bonus, ctx, icon_name="target")


def _d20(tag: Tag, ctx: RenderContext) -> Node:
    bonus = _signed(tag.part(0))
    return _chip(f"1d20{bonus}", tag.part(1) or bonus, ctx, icon_name="target")


def _dc(tag: Tag, ctx: RenderContext) -> Node:
    return span(class_="ref dice static")[
        _icon_for("dc"), _label(ctx, "rules.dc", "DC {value}", value=tag.part(0))
    ]


def _chance(tag: Tag, ctx: RenderContext) -> Node:
    shown = tag.part(1) or f"{tag.part(0)} percent"
    return _chip("1d100", shown, ctx, icon_name="dices", title=f"{tag.part(0)}% — roll d100")


def _recharge(tag: Tag, ctx: RenderContext) -> Node:
    low = tag.part(0) or "6"
    if low == "6":
        text = _label(ctx, "rules.recharge_six", "(Recharge 6)")
    else:
        text = _label(ctx, "rules.recharge", "(Recharge {low}–6)", low=low)
    return _chip("1d6", text, ctx, icon_name="refresh-cw")


def _scale(tag: Tag, ctx: RenderContext) -> Node:
    step = tag.part(2) or tag.part(0)
    return _chip(step, dice_text(step, ctx.dice_letter), ctx)


def _check(tag: Tag, ctx: RenderContext) -> Node:
    what, _, bonus = tag.part(0).rpartition(" ")
    bonus = _signed(bonus)
    return _chip(f"1d20{bonus}", tag.part(1) or bonus, ctx, icon_name="target", title=what.title())


def _ability(tag: Tag, ctx: RenderContext) -> Node:
    score, _, _ = tag.part(0).partition(" ")
    return span(class_="ref dice static")[tag.part(1) or score]


def _shorthand(tag: Tag, ctx: RenderContext) -> Node:
    return em[_label(ctx, f"rules.label.{tag.name}", SHORTHAND[tag.name])]


def _label(ctx: RenderContext, key: str, english: str, **params: Any) -> str:
    """A label in the reader's language, or ``english`` (filled in) without one."""
    if ctx.label is not None:
        text = ctx.label(key, **params)
        if text != key:
            return text
    return english.format(**params)


def _attack(tag: Tag, ctx: RenderContext) -> Node:
    kind = tag.part(0).replace(" ", "")
    english = ATTACK_KINDS.get(kind, "Attack:")
    code = (
        "".join(sorted(kind.split(","), key=lambda k: ("m" not in k, k)))
        if kind in ATTACK_KINDS
        else "any"
    )
    return em[_label(ctx, f"rules.atk.{code}", english)]


def _act_save(tag: Tag, ctx: RenderContext) -> Node:
    code = tag.part(0).lower()
    ability = _label(ctx, f"rules.ability.{code}", ABILITY_FULL.get(code, tag.part(0).title()))
    return em[_label(ctx, "rules.save_label", "{ability} Saving Throw:", ability=ability)]


def _act_fail_by(tag: Tag, ctx: RenderContext) -> Node:
    return em[_label(ctx, "rules.fail_by", "Failure by {amount} or More:", amount=tag.part(0))]


def _wrap(element: Callable[..., Element]) -> Callable[[Tag, RenderContext], Node]:
    def handler(tag: Tag, ctx: RenderContext) -> Node:
        return element()[_inline(tag.part(0), ctx)]

    return handler


def _color(tag: Tag, ctx: RenderContext) -> Node:
    hex_value = tag.part(1).lstrip("#")
    token = ""
    if re.fullmatch(r"[0-9a-fA-F]{6}", hex_value):
        rgb = tuple(int(hex_value[i : i + 2], 16) for i in (0, 2, 4))
        token = min(
            _COLOR_TOKENS, key=lambda t: sum((x - y) ** 2 for x, y in zip(t[1], rgb, strict=True))
        )[0]
    return span(class_=f"tag-color {token}".strip())[_inline(tag.part(0), ctx)]


def _note(tag: Tag, ctx: RenderContext) -> Node:
    return span(class_="tag-note")[_inline("|".join(tag.parts), ctx)]


def _footnote(tag: Tag, ctx: RenderContext) -> Node:
    return span(class_="tag-note")[" (", _inline(tag.part(0), ctx), ")"]


def _link(tag: Tag, ctx: RenderContext) -> Node:
    target = tag.part(1)
    if target.startswith(("http://", "https://")):
        return a(href=target, target="_blank", rel="noopener noreferrer", class_="ext")[
            _inline(tag.part(0), ctx)
        ]
    return _inline(tag.part(0), ctx)


def _style(tag: Tag, ctx: RenderContext) -> Node:
    styles = {s.strip() for s in tag.part(1).split(",")}
    return span(class_=" ".join(f"tag-{s}" for s in styles if re.fullmatch(r"[a-z-]+", s)))[
        _inline(tag.part(0), ctx)
    ]


def _plain_text(text: str) -> Callable[[Tag, RenderContext], Node]:
    def handler(tag: Tag, ctx: RenderContext) -> Node:
        return text

    return handler


_HANDLERS: dict[str, Callable[[Tag, RenderContext], Node]] = {
    "dice": _dice,
    "damage": _dice,
    "autodice": _dice,
    "hit": _hit,
    "d20": _d20,
    "dc": _dc,
    "chance": _chance,
    "recharge": _recharge,
    "scaledamage": _scale,
    "scaledice": _scale,
    "skillCheck": _check,
    "savingThrow": _check,
    "ability": _ability,
    "hitYourSpellAttack": _plain_text("your spell attack modifier"),
    "dcYourSpellSave": _plain_text("your spell save DC"),
    "atk": _attack,
    "atkr": _attack,
    "actSave": _act_save,
    "actSaveFailBy": _act_fail_by,
    "b": _wrap(strong),
    "bold": _wrap(strong),
    "i": _wrap(em),
    "italic": _wrap(em),
    "u": _wrap(u),
    "underline": _wrap(u),
    "u2": _wrap(u),
    "underlineDouble": _wrap(u),
    "s": _wrap(s),
    "strike": _wrap(s),
    "s2": _wrap(s),
    "strikeDouble": _wrap(s),
    "sup": _wrap(sup),
    "sub": _wrap(sub),
    "kbd": _wrap(kbd),
    "code": _wrap(code_el),
    "highlight": _wrap(mark),
    "color": _color,
    "note": _note,
    "footnote": _footnote,
    "tip": _wrap(span),
    "link": _link,
    "style": _style,
}
for _name in SHORTHAND:
    _HANDLERS[_name] = _shorthand


# -- entries (block level) -----------------------------------------------------------


def render_entries(entries: Any, ctx: RenderContext | None = None) -> list[Node]:
    """Render a 5eTools ``entries`` value — a string, a list, or typed objects."""
    ctx = ctx or RenderContext()
    out: list[Node] = []
    for entry in entries if isinstance(entries, list) else [entries]:
        out.append(_block(entry, ctx))
    return out


def _block(entry: Any, ctx: RenderContext) -> Node:
    if ctx.block_hook is not None and isinstance(entry, dict):
        hooked = ctx.block_hook(entry, lambda: _block_default(entry, ctx))
        if hooked is not None:
            return hooked
    return _block_default(entry, ctx)


def _block_default(entry: Any, ctx: RenderContext) -> Node:
    if entry is None:
        return None
    if isinstance(entry, str):
        return p[render(entry, ctx)]
    if isinstance(entry, list):
        return render_entries(entry, ctx)
    if not isinstance(entry, dict):
        return None
    kind = entry.get("type", "entries")
    name = entry.get("name")
    inner = entry.get("entries") or entry.get("entry") or []
    if kind in (
        "entries",
        "section",
        "item",
        "itemSub",
        "inline",
        "inlineBlock",
        "options",
        "homebrew",
        "wrapper",
        "itemSpell",
    ):
        return _named(name, inner, ctx)
    if kind in ("inset", "insetReadaloud", "variant", "variantSub", "variantInner"):
        cls = {"insetReadaloud": "entry-inset readaloud", "variant": "entry-inset variant"}
        title: Node = render(name, ctx) if name else None
        if kind == "variant" and name:  # printed "Variant: Encumbrance"
            title = [_label(ctx, "md.variant", "Variant"), ": ", title]
        return aside(class_=cls.get(kind, "entry-inset"))[
            h4[title] if title else None, render_entries(inner, ctx)
        ]
    if kind == "list":
        return ul(class_="entry-list")[[_list_item(item, ctx) for item in entry.get("items", [])]]
    if kind == "table":
        return _table(entry, ctx)
    if kind == "quote":
        by = entry.get("by")
        pull = " pull" if entry.get("style") == "quote-pull" else ""
        return blockquote(class_=f"entry-quote{pull}")[
            render_entries(inner, ctx), p(class_="entry-by")["— ", render(by, ctx)] if by else None
        ]
    if kind == "hr":
        return div(class_="entry-hr", role="separator")
    if kind in ("abilityDc", "abilityAttackMod"):
        attr = ", ".join(ABILITY_FULL.get(x, x) for x in entry.get("attributes", []))
        what = "save DC" if kind == "abilityDc" else "attack modifier"
        label = entry.get("name", "Spell")
        return p(class_="entry-formula")[
            strong[f"{label} {what}"],
            f" = 8 + your proficiency bonus + your {attr} modifier"
            if kind == "abilityDc"
            else f" = your proficiency bonus + your {attr} modifier",
        ]
    if kind in ("refClassFeature", "refSubclassFeature", "refOptionalfeature"):
        ref = (
            entry.get("classFeature")
            or entry.get("subclassFeature")
            or entry.get("optionalfeature")
            or ""
        )
        return p(class_="entry-ref")[em[str(ref).split("|")[0]]]
    if kind == "dice":
        rolls = entry.get("toRoll") or []
        expr = "+".join(
            f"{d.get('number', 1)}d{d.get('faces', 6)}" for d in rolls if isinstance(d, dict)
        )
        return _chip(expr, expr, ctx) if expr else None
    if kind in ("bonus", "bonusSpeed"):
        value = entry.get("value", 0)
        return f"{value:+d}" + (" ft." if kind == "bonusSpeed" else "")
    if kind == "flowchart":  # the books' steps, each a box with an arrow to the next
        return div(class_="entry-flowchart")[
            [_block(block, ctx) for block in entry.get("blocks", [])]
        ]
    if kind == "flowBlock":
        return div(class_="entry-flowblock")[
            h4[render(name, ctx)] if name else None, render_entries(inner, ctx)
        ]
    if kind in ("image", "gallery", "statblock", "link", "spellcasting"):
        return _fallback(entry, ctx)
    return _named(name, inner, ctx)


def _named(name: Any, inner: Any, ctx: RenderContext) -> Node:
    """A named block: the name leads the first paragraph, as 5e traits are printed."""
    blocks = inner if isinstance(inner, list) else [inner]
    if not name:
        return render_entries(blocks, ctx)
    lead = strong(class_="entry-name")[em[render(str(name), ctx), "."], " "]
    if blocks and isinstance(blocks[0], str):
        return [p[lead, render(blocks[0], ctx)], render_entries(blocks[1:], ctx)]
    return [p[lead], render_entries(blocks, ctx)]


def _list_item(item: Any, ctx: RenderContext) -> Element:
    if isinstance(item, str):
        return li[render(item, ctx)]
    if isinstance(item, dict) and item.get("type") in ("item", "itemSub", "itemSpell"):
        inner = item.get("entries") or item.get("entry") or []
        blocks = inner if isinstance(inner, list) else [inner]
        lead = strong[render(str(item.get("name", "")), ctx), " "] if item.get("name") else None
        first = render(blocks[0], ctx) if blocks and isinstance(blocks[0], str) else None
        rest = blocks[1:] if first is not None else blocks
        return li[lead, first, render_entries(rest, ctx) if rest else None]
    return li[_block(item, ctx)]


def _cell(value: Any, ctx: RenderContext) -> Node:
    if isinstance(value, dict) and value.get("type") == "cell":
        roll = value.get("roll") or {}
        if "exact" in roll:
            return str(roll["exact"])
        if "min" in roll and "max" in roll:
            return f"{roll['min']}–{roll['max']}"
        return render_entries(value.get("entry") or value.get("entries") or "", ctx)
    if isinstance(value, str):
        return render(value, ctx)
    if isinstance(value, int | float):
        return str(value)
    return _block(value, ctx)


def _table(entry: dict[str, Any], ctx: RenderContext) -> Element:
    labels = entry.get("colLabels") or []
    rows = entry.get("rows") or []
    title = entry.get("caption")
    return div(class_="entry-table")[
        table[
            caption[render(title, ctx)] if title else None,
            thead[tr[[th[render(str(label), ctx)] for label in labels]]] if labels else None,
            tbody[
                [
                    tr[
                        [
                            td[_cell(cell, ctx)]
                            for cell in (row.get("row", []) if isinstance(row, dict) else row)
                        ]
                    ]
                    for row in rows
                ]
            ],
        ]
    ]


def _fallback(entry: dict[str, Any], ctx: RenderContext) -> Node:
    """Types that are pictures or layout upstream: keep whatever text they carry."""
    texts: list[str] = []
    for key in ("name", "title", "text"):
        if isinstance(entry.get(key), str):
            texts.append(entry[key])
    inner = entry.get("entries") or entry.get("headerEntries")
    return [
        p(class_="entry-aside")[render(" — ".join(texts), ctx)] if texts else None,
        render_entries(inner, ctx) if inner else None,
    ]


def strip_all(entries: Iterable[Any]) -> str:
    """Plain text of entries, for tooltips and titles."""
    out: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            out.append(plain(entry))
        elif isinstance(entry, dict):
            out.append(strip_all(entry.get("entries") or entry.get("items") or []))
    return " ".join(x for x in out if x)


__all__ = [
    "ENTITY_CATEGORY",
    "card_anchor",
    "card_url",
    "RenderContext",
    "Tag",
    "catalogue",
    "dice_letter",
    "dice_text",
    "localized_context",
    "parse",
    "plain",
    "ref_url",
    "render",
    "render_entries",
    "strip_all",
]
