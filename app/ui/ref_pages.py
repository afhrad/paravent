"""The reference page: one spell, creature, item, condition or rule, rendered in full.

What every ``{@…}`` link opens (documentation/DESIGN.md §6). Labels follow the reader's
language; the text itself is the English corpus text, as everywhere until the
translation overlay exists (documentation/TRANSLATION.md).

A creature becomes a **statblock** — and because its actions are rendered through the
tag renderer, every attack line carries rollable to-hit and damage chips.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import quote, urlencode

from htpy import (
    Element,
    Node,
    a,
    b,
    button,
    details,
    div,
    form,
    h1,
    h2,
    h3,
    header,
    img,
    label,
    li,
    p,
    section,
    small,
    span,
    summary,
    ul,
)
from htpy import input as input_el

from app.services.backlinks import Backlinks, Owner
from app.services.references import Printing, ReferenceEntry
from app.services.sheet import spell_view
from app.services.translation_targets import fluff_category
from app.services.translations import record_key
from app.store.projections import ITEM_KIND_NAMES as ITEM_TYPES
from app.ui import rules_text
from app.ui.auth_pages import message_panel
from app.ui.context import Page
from app.ui.encounter_pages import encounter_url
from app.ui.icons import icon
from app.ui.layout import app_shell
from app.ui.library_links import chapter_url, location_url, npc_url, treasure_url
from app.ui.names import name_text
from app.ui.player_view import view_switch
from app.ui.tags import RenderContext, card_anchor, plain, ref_url, render, render_entries
from app.ui.translate_pages import translate_url
from app.ui.translated import coverage_note, localized

SIZES = {"T": "Tiny", "S": "Small", "M": "Medium", "L": "Large", "H": "Huge", "G": "Gargantuan"}
ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
DAMAGE = {
    "A": "acid",
    "B": "bludgeoning",
    "C": "cold",
    "F": "fire",
    "O": "force",
    "L": "lightning",
    "N": "necrotic",
    "P": "piercing",
    "I": "poison",
    "Y": "psychic",
    "R": "radiant",
    "S": "slashing",
    "T": "thunder",
}
CATEGORY_KEY = {
    "spell": "ref.kind.spell",
    "monster": "ref.kind.monster",
    "item": "ref.kind.item",
    "baseitem": "ref.kind.item",
    "magicvariant": "ref.kind.variant",
    "itemGroup": "ref.kind.item_group",
    "itemProperty": "ref.kind.item_property",
    "itemType": "ref.kind.item_type",
    "table": "ref.kind.table",
    "condition": "ref.kind.condition",
    "status": "ref.kind.condition",
    "psionic": "ref.kind.psionic",
    "vehicle": "ref.kind.vehicle",
    "vehicleUpgrade": "ref.kind.vehicle_upgrade",
    "facility": "ref.kind.facility",
    "charoption": "ref.kind.charoption",
    "cult": "ref.kind.cult",
    "boon": "ref.kind.boon",
    "itemMastery": "ref.kind.item_mastery",
    "deck": "ref.kind.deck",
    "card": "ref.kind.card",
}


def _mod(score: int) -> str:
    return f"{(score - 10) // 2:+d}"


def _first_code(value: Any) -> str:
    return str(value).split("|")[0]


def _line(label: str, *content: Node) -> Element:
    return p(class_="sb-line")[b[label], " ", content]


def _joined(values: list[Any], ctx: RenderContext) -> list[Node]:
    out: list[Node] = []
    for index, value in enumerate(values):
        if index:
            out.append(", ")
        out.append(render(value, ctx) if isinstance(value, str) else _special(value, ctx))
    return out


def _special(value: Any, ctx: RenderContext) -> Node:
    """Mixed list members: {"immune": [...], "note": "..."}, {"special": "..."} …"""
    if isinstance(value, dict):
        if "special" in value:
            return render(str(value["special"]), ctx)
        inner: list[Any] = next(
            (value[k] for k in ("resist", "immune", "vulnerable", "conditionImmune") if k in value),
            [],
        )
        parts: list[Node] = []
        if value.get("preNote"):
            parts.append(f"{value['preNote']} ")
        parts.append(_joined(inner, ctx))
        if value.get("note"):
            parts.append([" ", render(str(value["note"]), ctx)])
        return parts
    return str(value)


def _item_damage(page: Page, data: dict[str, Any]) -> str:
    """A weapon's damage: "1d8 slashing" / "1d8 Hieb"."""
    word = rules_text.damage_word(page, DAMAGE.get(str(data.get("dmgType")), ""))
    return f"{rules_text.dice(page, str(data['dmg1']))} {word}".strip()


def _terms(page: Page, values: list[Any], kind: str = "damage") -> list[Any]:
    """A resistance, immunity or condition list with its words in the reader's language;
    nested ``{"immune": [...], "note": …}`` entries too. Notes stay as written."""
    if page.lang == "en":
        return values

    def word(value: str) -> str:
        if kind == "condition":
            text = page.t(f"condition.{value.lower()}")
            return value if text.startswith("condition.") else text
        return rules_text.damage_word(page, value)

    out: list[Any] = []
    for value in values:
        if isinstance(value, str):
            out.append(word(value))
        elif isinstance(value, dict):
            copy = dict(value)
            for key in ("resist", "immune", "vulnerable", "conditionImmune"):
                if isinstance(copy.get(key), list):
                    inner = "condition" if key == "conditionImmune" else kind
                    copy[key] = _terms(page, copy[key], inner)
            out.append(copy)
        else:
            out.append(value)
    return out


# -- creature -----------------------------------------------------------------------------


def _armor(page: Page, data: dict[str, Any], ctx: RenderContext) -> list[Node]:
    out: list[Node] = []
    for index, ac in enumerate(data.get("ac") or []):
        if index:
            out.append(", ")
        if isinstance(ac, dict):
            out.append(str(ac.get("ac", ac.get("special", ""))))
            if ac.get("from"):
                sources = [
                    page.t("rules.ac.natural") if str(x).lower() == "natural armor" else x
                    for x in ac["from"]
                ]
                out.append([" (", _joined(sources, ctx), ")"])
            if ac.get("condition"):
                out.append([" ", render(str(ac["condition"]), ctx)])
        else:
            out.append(str(ac))
    return out


def _hit_points(data: dict[str, Any], ctx: RenderContext) -> Node:
    hp = data.get("hp") or {}
    if "special" in hp:
        return render(str(hp["special"]), ctx)
    formula = hp.get("formula")
    return [
        str(hp.get("average", "—")),
        [" (", render(f"{{@dice {formula}|{formula}}}", ctx), ")"] if formula else None,
    ]


def _ability_chip(score: int, ctx: RenderContext) -> list[Node]:
    """``14 (+2)``, the modifier rollable as an ability check."""
    modifier = _mod(score)
    return render(f"{{@d20 {modifier}|{score} ({modifier})}}", ctx)


def _spellcasting(block: dict[str, Any], ctx: RenderContext) -> list[Node]:
    lines: list[Node] = [render_entries(block.get("headerEntries") or [], ctx)]
    if block.get("will"):
        lines.append(p(class_="sb-spells")["At will: ", _joined(block["will"], ctx)])
    for key, spells in (block.get("daily") or {}).items():
        each = " each" if key.endswith("e") else ""
        lines.append(p(class_="sb-spells")[f"{key.rstrip('e')}/day{each}: ", _joined(spells, ctx)])
    for level, info in sorted((block.get("spells") or {}).items(), key=lambda kv: int(kv[0])):
        label = "Cantrips (at will)" if level == "0" else f"Level {level}"
        slots = info.get("slots")
        if slots:
            label += f" ({slots} slots)"
        lines.append(p(class_="sb-spells")[f"{label}: ", _joined(info.get("spells", []), ctx)])
    lines.append(render_entries(block.get("footerEntries") or [], ctx))
    return lines


def _section(
    title: str, entries: list[Any] | None, ctx: RenderContext, header_text: Any = None
) -> Node:
    if not entries:
        return None
    return section(class_="sb-section")[
        h2(class_="sb-heading")[title],
        render_entries(header_text, ctx) if header_text else None,
        render_entries(entries, ctx),
    ]


def _creature(page: Page, data: dict[str, Any], ctx: RenderContext) -> list[Node]:
    t = page.t
    abilities = div(class_="sb-abilities")[
        [
            div(class_="sb-ability")[
                span(class_="k")[t(f"abbr.{key}")],
                _ability_chip(int(data.get(key, 10)), ctx),
            ]
            for key in ABILITIES
        ]
    ]
    saves = rules_text.saves(page, data)
    skills = rules_text.skills(page, data)
    senses = rules_text.senses(page, data)
    traits = [*(data.get("trait") or [])]
    spellcasting = [
        {"type": "entries", "name": block.get("name", "Spellcasting"), "entries": []}
        | {"_block": block}
        for block in data.get("spellcasting") or []
    ]
    return [
        p(class_="sb-type")[rules_text.creature_type(page, data)],
        div(class_="sb-core")[
            _line(t("ref.ac"), _armor(page, data, ctx)),
            _line(t("ref.hp"), _hit_points(data, ctx)),
            _line(t("ref.speed"), rules_text.speed(page, data)),
        ],
        abilities,
        div(class_="sb-core")[
            _line(t("ref.saves"), saves) if saves else None,
            _line(t("ref.skills"), skills) if skills else None,
            _line(t("ref.vulnerable"), _joined(_terms(page, data["vulnerable"]), ctx))
            if data.get("vulnerable")
            else None,
            _line(t("ref.resist"), _joined(_terms(page, data["resist"]), ctx))
            if data.get("resist")
            else None,
            _line(t("ref.immune"), _joined(_terms(page, data["immune"]), ctx))
            if data.get("immune")
            else None,
            _line(
                t("ref.condition_immune"),
                _joined(_terms(page, data["conditionImmune"], "condition"), ctx),
            )
            if data.get("conditionImmune")
            else None,
            _line(t("ref.senses"), _joined(senses, ctx)),
            _line(
                t("ref.languages"),
                _joined(rules_text.languages(page, data["languages"]), ctx)
                if data.get("languages")
                else "—",
            ),
            _line(t("ref.cr"), rules_text.challenge(page, data)),
        ],
        section(class_="sb-section")[
            render_entries(traits, ctx),
            [
                [p[b(class_="entry-name")[s["name"], ". "]], _spellcasting(s["_block"], ctx)]
                for s in spellcasting
            ],
        ]
        if traits or spellcasting
        else None,
        _section(t("ref.actions"), data.get("action"), ctx),
        _section(t("ref.bonus_actions"), data.get("bonus"), ctx),
        _section(t("ref.reactions"), data.get("reaction"), ctx),
        _section(
            t("ref.legendary"),
            data.get("legendary"),
            ctx,
            rules_text.legendary_header(page, data, str(data.get("name", ""))),
        ),
        _section(t("ref.mythic"), data.get("mythic"), ctx, data.get("mythicHeader")),
    ]


# -- spell, item, anything --------------------------------------------------------------


def _spell(page: Page, data: dict[str, Any], ctx: RenderContext) -> list[Node]:
    t = page.t
    spell = spell_view(data)
    level = t("sheet.cantrip") if spell.level == 0 else t("rules.spell_level", level=spell.level)
    tags = [
        x
        for x in (
            t("sheet.conc") if spell.concentration else "",
            t("sheet.ritual") if spell.ritual else "",
        )
        if x
    ]
    return [
        p(class_="sb-type")[
            f"{level} {rules_text.school(page, spell.school_code) or spell.school}".strip(),
            [" · ", ", ".join(tags)] if tags else None,
        ],
        div(class_="sb-core")[
            _line(t("ref.casting_time"), rules_text.spell_time(page, spell)),
            _line(t("ref.range"), rules_text.spell_range(page, spell)),
            _line(
                t("ref.components"),
                spell.components,
                f" ({spell.material})" if spell.material else None,
            ),
            _line(t("ref.duration"), rules_text.spell_duration(page, spell)),
        ],
        div(class_="ref-body")[render_entries(list(spell.entries), ctx)],
    ]


_TEMPLATE_GAP = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")
# Words the property templates print themselves ("Range 80/320 ft.; Arrow", "6 shots").
_TEMPLATE_WORDS = {"Range": "ref.range", "shots": "rules.shots"}


def _coded(ctx: RenderContext, category: str, raw: Any) -> Any:
    """The item property or type an item names by code (``"V|XPHB"``, a bare code being
    the PHB's), or None."""
    uid = raw.get("uid", "") if isinstance(raw, dict) else raw
    code, _, source = str(uid).partition("|")
    if ctx.resolver is None or not code:
        return None
    return ctx.resolver.resolve_parts(category, code, source or "PHB").entry


def _coded_link(ctx: RenderContext, category: str, entry: Any) -> Element:
    return a(href=ref_url(category, entry.name, entry.source, ctx.world_id), class_="ref rule")


def _property(page: Page, data: dict[str, Any], raw: Any, ctx: RenderContext) -> Node:
    """One property as the item prints it, through the property's own template:
    "Versatile (1d10)", "Ammunition (Range 80/320 ft.; Arrow)" — its name a link."""
    found = _coded(ctx, "itemProperty", raw)
    if found is None:
        return _first_code(raw.get("uid", "") if isinstance(raw, dict) else raw)
    name = rules_text.property_word(page, found.name)
    template = (found.template or "{{prop_name}}").replace("{{item.range}}\u00a0ft.", "{{range}}")
    link = _coded_link(ctx, "itemProperty", found)
    if "prop_name" not in template:
        return link[_TEMPLATE_GAP.sub("", template)]
    out: list[Node] = []
    position = 0
    for gap in _TEMPLATE_GAP.finditer(template):
        literal = template[position : gap.start()]
        for word, key in _TEMPLATE_WORDS.items():
            literal = re.sub(rf"\b{word}\b", page.t(key), literal)
        out.append(literal)
        position = gap.end()
        field = gap.group(1)
        if field == "prop_name":
            out.append(link[name])
        elif field == "prop_name_lower":
            out.append(link[name.lower() if page.lang == "en" else name])
        elif field == "range":
            out.append(page.u.text(f"{data.get('range', '')} ft."))
        elif field == "item.dmg2":
            out.append(rules_text.dice(page, str(data.get("dmg2", ""))))
        elif field == "item.ammoType" and data.get("ammoType"):
            out.append(render(f"{{@item {data['ammoType']}}}", ctx))
        else:
            out.append(str(data.get(field.removeprefix("item."), "")))
    out.append(template[position:])
    if isinstance(raw, dict) and raw.get("note"):
        out.append(f" ({raw['note']})")
    return out


def _item(page: Page, data: dict[str, Any], ctx: RenderContext) -> list[Node]:
    t = page.t
    code = _first_code(data.get("type", ""))
    kind_text = ITEM_TYPES.get(code, "Wondrous item" if data.get("wondrous") else code or "Item")
    kind_entry = _coded(ctx, "itemType", data["type"]) if data.get("type") else None
    # A type with rules of its own (Ammunition, Spellcasting Focus) links to them.
    kind: Node = (
        _coded_link(ctx, "itemType", kind_entry)[kind_text]
        if kind_entry is not None and kind_entry.entries
        else kind_text
    )
    rarity = data.get("rarity")
    attune = data.get("reqAttune")
    attune_text = (
        (" (requires attunement)" if attune is True else f" (requires attunement {attune})")
        if attune
        else ""
    )
    facts: list[Node] = []
    if data.get("dmg1"):
        damage = _item_damage(page, data)
        facts.append(_line(t("ref.damage"), render(f"{{@damage {data['dmg1']}|{damage}}}", ctx)))
    if data.get("ac") is not None:
        facts.append(_line(t("ref.ac"), str(data["ac"])))
    if data.get("weight") is not None:
        facts.append(_line(t("ref.weight"), page.u.weight(data["weight"])))
    if data.get("value") is not None:
        facts.append(_line(t("ref.value"), _coins(int(data["value"]))))
    if data.get("property"):
        shown = [_property(page, data, x, ctx) for x in data["property"]]
        facts.append(
            _line(t("ref.properties"), [[", " if i else None, x] for i, x in enumerate(shown)])
        )
    if data.get("range"):
        facts.append(_line(t("ref.range"), page.u.text(f"{data['range']} ft.")))
    shown_rarity = f", {rarity}" if rarity and rarity not in ("none", "unknown") else ""
    lead = [kind, shown_rarity, attune_text]
    return [
        p(class_="sb-type")[lead],
        div(class_="sb-core")[facts] if facts else None,
        div(class_="ref-body")[render_entries(data.get("entries") or [], ctx)],
    ]


def _item_group(page: Page, data: dict[str, Any], ctx: RenderContext) -> list[Node]:
    """An item group ("Artisan's Tools"): an item's header, then the items it stands for,
    each a link resolved in the world's books. ``itemsHidden`` groups list them in their
    own text already, as a table."""
    members = [str(x) for x in data.get("items") or [] if isinstance(x, str)]
    listed = (
        section(class_="sb-section item-group")[
            h2(class_="sb-heading")[page.t.plural("ref.group_items", len(members))],
            ul(class_="item-group-list")[[li[render(f"{{@item {m}}}", ctx)] for m in members]],
        ]
        if members and not data.get("itemsHidden")
        else None
    )
    return [*_item(page, data, ctx), listed]


def _table_entry(page: Page, data: dict[str, Any], ctx: RenderContext) -> list[Node]:
    """A table entry: the table itself, with its footnotes (it carries no ``entries``)."""
    shown = {k: data[k] for k in ("caption", "colLabels", "colStyles", "rows") if k in data}
    return [
        div(class_="ref-body")[
            render_entries([{"type": "table", **shown}], ctx),
            render_entries(data.get("footnotes") or [], ctx),
        ]
    ]


def _printed_in(page: Page, place: tuple[str, str, str] | None) -> Node:
    """Where a table is printed, as a link into the rulebook chapter."""
    if place is None:
        return None
    url, book, chapter = place
    return p(class_="ref-printed")[
        icon("book-open", "sm"),
        " ",
        a(href=url)[page.t("ref.printed_in", book=book, chapter=chapter)],
    ]


# Rows a table's preview shows before "…".
PREVIEW_ROWS = 10


def _coins(copper: int) -> str:
    if copper % 100 == 0:
        return f"{copper // 100:,} gp"
    if copper % 10 == 0:
        return f"{copper // 10:,} sp"
    return f"{copper:,} cp"


def _generic(page: Page, data: dict[str, Any], ctx: RenderContext) -> list[Node]:
    return [
        div(class_="ref-body")[render_entries(data.get("entries") or data.get("entry") or [], ctx)]
    ]


# -- the long tail: vehicles, facilities, cults, psionics, decks ------------------------

VEHICLE_KINDS = {
    "SHIP": "Ship",
    "SPELLJAMMER": "Spelljammer ship",
    "ELEMENTAL_AIRSHIP": "Elemental airship",
    "INFWAR": "Infernal war machine",
    "CREATURE": "Creature vehicle",
    "OBJECT": "Vehicle",
}
# A vehicle is its parts, each a named block with AC, HP and text, in this order.
VEHICLE_PARTS = (
    ("control", "ref.vehicle_control"),
    ("movement", "ref.vehicle_movement"),
    ("station", "ref.vehicle_stations"),
    ("actionStation", "ref.vehicle_stations"),
    ("weapon", "ref.vehicle_weapons"),
    ("other", "ref.vehicle_other"),
)
PSIONIC_TYPES = {"D": "Psionic discipline", "T": "Psionic talent"}
FOCUS_KINDS = {"arcane": "an Arcane Focus", "tool": "a tool", "artisansTool": "Artisan's Tools"}


def _sizes(value: Any) -> str:
    codes = value if isinstance(value, list) else [value] if value else []
    return " or ".join(SIZES.get(str(s), str(s)) for s in codes)


def _wrapped(value: Any) -> Any:
    """``{"entry": "..."}`` — the corpus's one-entry wrapper — to the entry itself."""
    return value.get("entry") if isinstance(value, dict) and "entry" in value else value


def _durability(page: Page, block: dict[str, Any]) -> str:
    """``AC 15 (wood), HP 300, damage threshold 20`` for a hull or a part."""
    t = page.t
    bits: list[str] = []
    ac = block.get("ac")
    if isinstance(ac, list):  # a creature's statblock form, as a creature vehicle has
        ac = _first_ac(block)
    if isinstance(ac, int | str):
        material = ", ".join(plain(str(x)) for x in block.get("acFrom") or [])
        bits.append(f"{t('ref.ac')} {ac}{f' ({material})' if material else ''}")
    hp = block.get("hp")
    if isinstance(hp, dict) and "hp" in hp:
        block = {**block, **hp}
        hp = hp["hp"]
    elif isinstance(hp, dict):
        hp = hp.get("average", hp.get("special"))
    if isinstance(hp, int | str):
        note = f" ({plain(str(block['hpNote']))})" if block.get("hpNote") else ""
        bits.append(f"{t('ref.hp')} {hp}{note}")
    if block.get("dt"):
        bits.append(f"{t('ref.damage_threshold')} {block['dt']}")
    if block.get("mt"):
        bits.append(f"{t('ref.mishap_threshold')} {block['mt']}")
    if block.get("crew"):
        bits.append(f"{t('ref.crew')} {block['crew']}")
    return ", ".join(bits)


def _vehicle_part(page: Page, part: dict[str, Any], ctx: RenderContext) -> Node:
    count = part.get("count")
    title = f"{part.get('name', '')}{f' ({count})' if count and count > 1 else ''}"
    speeds = [
        {"type": "entries", "name": f"{page.t('ref.speed')} ({s.get('mode', '')})", **s}
        for s in part.get("speed") or []
        if isinstance(s, dict)
    ]
    durability = _durability(page, part)
    return render_entries(
        {
            "type": "entries",
            "name": title,
            "entries": [
                *([f"{{@i {durability}}}"] if durability else []),
                *(part.get("entries") or []),
                *speeds,
                *(part.get("action") or []),
            ],
        },
        ctx,
    )


def _vehicle(page: Page, data: dict[str, Any], ctx: RenderContext) -> list[Node]:
    t, u = page.t, page.u
    kind = VEHICLE_KINDS.get(str(data.get("vehicleType")), "Vehicle")
    terrain = ", ".join(str(x) for x in data.get("terrain") or [])
    lead = f"{_sizes(data.get('size'))} {kind.lower() if data.get('size') else kind}".strip()
    if terrain:
        lead += f" ({terrain})"
    crew = [
        f"{data[key]} {t(label)}"
        for key, label in (
            ("capCrew", "ref.crew_members"),
            ("capPassenger", "ref.passengers"),
            ("capCreature", "ref.creatures"),
        )
        if data.get(key)
    ]
    cargo = data.get("capCargo")
    if isinstance(cargo, str):  # "crew and passengers' normal gear"
        cargo_text = plain(cargo)
    elif cargo and data.get("vehicleType") == "INFWAR":
        cargo_text = u.weight(cargo)
    elif cargo:
        cargo_text = t.plural("ref.tons", 1 if cargo == 1 else 2, amount=u.number(cargo))
    else:
        cargo_text = ""
    pace = data.get("pace")
    paces = pace.items() if isinstance(pace, dict) else [("", pace)] if pace else []
    pace_text = ", ".join(f"{mode} {u.text(f'{n} mi')}/h".strip() for mode, n in paces)
    speed = data.get("speed")
    hull = data.get("hull") or (
        {"ac": data.get("ac"), "hp": data.get("hp")} if data.get("hp") or data.get("ac") else None
    )
    scores = all(key in data for key in ABILITIES)
    return [
        p(class_="sb-type")[lead],
        div(class_="sb-core")[
            _line(t("ref.dimensions"), " × ".join(u.text(str(x)) for x in data["dimensions"]))
            if data.get("dimensions")
            else None,
            _line(t("ref.capacity"), ", ".join(crew)) if crew else None,
            _line(t("ref.cargo"), cargo_text) if cargo_text else None,
            _line(t("ref.weight"), u.weight(data["weight"])) if data.get("weight") else None,
            _line(t("ref.pace"), pace_text) if pace_text else None,
            _line(t("ref.speed"), rules_text.speed(page, data)) if speed else None,
            _line(t("ref.cost"), _coins(int(data["cost"]))) if data.get("cost") else None,
            _line(t("ref.hull"), _durability(page, hull)) if hull else None,
        ],
        div(class_="sb-abilities")[
            [
                div(class_="sb-ability")[
                    span(class_="k")[t(f"abbr.{key}")],
                    _ability_chip(int(data[key]), ctx),
                ]
                for key in ABILITIES
            ]
        ]
        if scores
        else None,
        div(class_="sb-core")[
            _line(t("ref.immune"), _joined(_terms(page, data["immune"]), ctx))
            if data.get("immune")
            else None,
            _line(
                t("ref.condition_immune"),
                _joined(_terms(page, data["conditionImmune"], "condition"), ctx),
            )
            if data.get("conditionImmune")
            else None,
        ]
        if data.get("immune") or data.get("conditionImmune")
        else None,
        div(class_="ref-body")[render_entries(data["entries"], ctx)]
        if data.get("entries")
        else None,
        section(class_="sb-section")[render_entries(data["trait"], ctx)]
        if data.get("trait")
        else None,
        _section(t("ref.actions"), data.get("action"), ctx),
        _section(t("ref.reactions"), data.get("reaction"), ctx),
        [
            section(class_="sb-section")[
                h2(class_="sb-heading")[t(label)],
                [_vehicle_part(page, part, ctx) for part in data[key] if isinstance(part, dict)],
            ]
            for key, label in VEHICLE_PARTS
            if data.get(key)
        ],
    ]


def _facility_prerequisite(prerequisite: list[Any], ctx: RenderContext) -> list[Node]:
    """A bastion facility's prerequisites, in the 2024 DMG's words."""
    out: list[Node] = []
    for condition in prerequisite:
        if not isinstance(condition, dict):
            continue
        if condition.get("membership"):
            out.append(f"Membership in {' or '.join(map(str, condition['membership']))}")
        kinds = condition.get("spellcastingFocus")
        if kinds is True:
            out.append("Ability to use a Spellcasting Focus")
        elif isinstance(kinds, list) and kinds:
            focus = " or ".join(FOCUS_KINDS.get(str(k), str(k)) for k in kinds)
            out.append(f"Ability to use {focus} as a Spellcasting Focus")
        if condition.get("expertise"):
            out.append("Expertise in a skill")
        summary = condition.get("otherSummary")
        if isinstance(summary, dict):
            out.append(render(str(summary.get("entrySummary") or summary.get("entry", "")), ctx))
        if condition.get("other"):
            out.append(render(str(condition["other"]), ctx))
    return [[", " if index else None, part] for index, part in enumerate(out)]


def _facility(page: Page, data: dict[str, Any], ctx: RenderContext) -> list[Node]:
    t = page.t
    kind = f"{str(data.get('facilityType', '')).title()} facility".strip()
    level = f", {t('ref.level')} {data['level']}" if data.get("level") else ""
    hirelings = ", ".join(
        str(h.get("exact", "")) + (f" ({h['space']})" if h.get("space") else "")
        for h in data.get("hirelings") or []
        if isinstance(h, dict)
    )
    prerequisite = _facility_prerequisite(data.get("prerequisite") or [], ctx)
    return [
        p(class_="sb-type")[kind, level],
        div(class_="sb-core")[
            _line(t("ref.prerequisite"), prerequisite) if prerequisite else None,
            _line(t("ref.space"), ", ".join(str(s).title() for s in data["space"]))
            if data.get("space")
            else None,
            _line(t("ref.hirelings"), hirelings) if hirelings else None,
            _line(t("ref.orders"), ", ".join(str(o).title() for o in data["orders"]))
            if data.get("orders")
            else None,
        ],
        div(class_="ref-body")[render_entries(data.get("entries") or [], ctx)],
    ]


def _cult(page: Page, data: dict[str, Any], ctx: RenderContext) -> list[Node]:
    t = page.t

    def shown(value: Any) -> Node:
        value = _wrapped(value)
        return render(value, ctx) if isinstance(value, str) else render_entries(value, ctx)

    facts = [
        _line(t(label), shown(data[key]))
        for key, label in (
            ("goal", "ref.goal"),
            ("cultists", "ref.cultists"),
            ("ability", "ref.ability_scores"),
            ("signatureSpells", "ref.signature_spells"),
        )
        if data.get(key)
    ]
    return [
        p(class_="sb-type")[str(data["type"])] if data.get("type") else None,
        div(class_="sb-core")[facts] if facts else None,
        div(class_="ref-body")[render_entries(data.get("entries") or [], ctx)],
    ]


def _psionic(page: Page, data: dict[str, Any], ctx: RenderContext) -> list[Node]:
    t = page.t
    kind = PSIONIC_TYPES.get(str(data.get("type")), "Psionic")
    order = f" ({data['order']} {t('ref.psionic_order')})" if data.get("order") else ""

    def cost(mode: dict[str, Any]) -> str:
        spend = mode.get("cost") or {}
        low, high = spend.get("min"), spend.get("max")
        if low is None:
            return ""
        return f" ({low}{f'–{high}' if high and high != low else ''} psi)"

    modes = [
        {**mode, "type": "entries", "name": f"{mode.get('name', '')}{cost(mode)}"}
        for mode in data.get("modes") or []
        if isinstance(mode, dict)
    ]
    return [
        p(class_="sb-type")[kind, order],
        div(class_="ref-body")[
            render_entries(data.get("entries") or [], ctx),
            render_entries(
                {"type": "entries", "name": t("ref.psychic_focus"), "entries": [data["focus"]]},
                ctx,
            )
            if data.get("focus")
            else None,
            render_entries(modes, ctx) if modes else None,
        ],
    ]


def _deck_cards(
    page: Page, cards: tuple[tuple[dict[str, Any], int], ...], ctx: RenderContext
) -> Node:
    if not cards:
        return None
    return section(class_="sb-section deck-cards")[
        h2(class_="sb-heading")[page.t.plural("ref.cards", len(cards))],
        [
            div(class_="deck-card", id=card_anchor(str(card.get("name", ""))))[
                h3(class_="entry-name")[card.get("name", ""), f" ×{count}" if count > 1 else None],
                render_entries(card.get("entries") or [], ctx),
            ]
            for card, count in cards
        ],
    ]


# -- previews (documentation/DESIGN.md §7) -------------------------------------------------

PREVIEW_ICON = {
    "spell": "sparkles",
    "monster": "skull",
    "item": "gem",
    "baseitem": "gem",
    "itemGroup": "gem",
    "table": "dices",
    "condition": "footprints",
    "status": "hourglass",
}


def _first_sentence(entries: Any) -> str:
    """The first sentence of the first paragraph, as plain text — a preview, not the entry."""
    for entry in entries if isinstance(entries, list) else [entries]:
        text = entry if isinstance(entry, str) else None
        if isinstance(entry, dict):
            inner = entry.get("entries") or []
            text = next((x for x in inner if isinstance(x, str)), None)
        if text:
            sentence = plain(text)
            cut = sentence.find(". ")
            return sentence if cut < 0 or cut > 260 else sentence[: cut + 1]
    return ""


def _first_ac(data: dict[str, Any]) -> str:
    """The first armour class a statblock lists, as a number."""
    values = data.get("ac") or []
    first = values[0] if values else None
    if isinstance(first, dict):
        return str(first.get("ac", "—"))
    return str(first) if first is not None else "—"


def _preview_body(page: Page, entry: ReferenceEntry, ctx: RenderContext) -> list[Node]:
    t = page.t
    data = entry.data
    if entry.category == "monster":
        hp = data.get("hp") or {}
        return [
            p(class_="tip-meta")[
                rules_text.creature_type(page, data),
                " · ",
                t("ref.cr"),
                " ",
                rules_text.challenge(page, data),
            ],
            div(class_="tip-stats")[
                div[
                    span(class_="k")[t("sheet.ac")],
                    span(class_="v mono")[_first_ac(data)],
                ],
                div[
                    span(class_="k")[t("ref.hp")],
                    span(class_="v mono")[str(hp.get("average", "—"))],
                ],
                div[
                    span(class_="k")[t("ref.speed")],
                    span(class_="v mono")[rules_text.speed(page, data)],
                ],
            ],
            div(class_="tip-abilities")[
                [
                    div[
                        span(class_="k")[t(f"abbr.{key}")],
                        span(class_="v mono")[
                            f"{data.get(key, 10)} ({_mod(int(data.get(key, 10)))})"
                        ],
                    ]
                    for key in ABILITIES
                ]
            ],
        ]
    if entry.category == "spell":
        spell = spell_view(data)
        level = (
            t("sheet.cantrip") if spell.level == 0 else t("rules.spell_level", level=spell.level)
        )
        conc = f" · {t('sheet.conc')}" if spell.concentration else ""
        return [
            p(class_="tip-meta")[
                f"{level} {rules_text.school(page, spell.school_code) or spell.school}".strip(),
                conc,
            ],
            p(class_="tip-meta")[
                " · ".join(
                    x
                    for x in (
                        rules_text.spell_time(page, spell),
                        rules_text.spell_range(page, spell),
                        rules_text.spell_duration(page, spell),
                    )
                    if x
                )
            ],
            p(class_="tip-text")[_first_sentence(data.get("entries"))],
        ]
    if entry.category in ("item", "baseitem", "itemGroup"):
        code = _first_code(data.get("type", ""))
        kind = ITEM_TYPES.get(code, "Wondrous item" if data.get("wondrous") else code or "Item")
        rarity = data.get("rarity")
        facts = [
            x
            for x in (
                _item_damage(page, data) if data.get("dmg1") else "",
                f"{t('sheet.ac')} {data['ac']}" if data.get("ac") is not None else "",
                page.u.weight(data["weight"]) if data.get("weight") is not None else "",
                _coins(int(data["value"])) if data.get("value") is not None else "",
            )
            if x
        ]
        return [
            p(class_="tip-meta")[
                kind, f", {rarity}" if rarity and rarity not in ("none", "unknown") else ""
            ],
            p(class_="tip-meta mono")[" · ".join(facts)] if facts else None,
            p(class_="tip-text")[_first_sentence(data.get("entries"))]
            if data.get("entries")
            else None,
        ]
    if entry.category == "table":
        rows = data.get("rows") or []
        shown = {k: data[k] for k in ("colLabels", "colStyles") if k in data}
        return [
            div(class_="tip-rules")[
                render_entries([{"type": "table", **shown, "rows": rows[:PREVIEW_ROWS]}], ctx)
            ],
            p(class_="tip-meta muted")[t("ref.more_rows", count=len(rows) - PREVIEW_ROWS)]
            if len(rows) > PREVIEW_ROWS
            else None,
        ]
    if entry.category in ("condition", "status", "card"):
        # Short, and truncating a rule is worse than useless: show it whole.
        return [div(class_="tip-rules")[render_entries(data.get("entries") or [], ctx)]]
    return [p(class_="tip-text")[_first_sentence(data.get("entries") or data.get("entry") or [])]]


def _printing_url(entry: ReferenceEntry, printing: Printing) -> str:
    return ref_url(entry.category, printing.name, printing.source, entry.world_id)


def _printings(page: Page, entry: ReferenceEntry, printings: Sequence[Printing]) -> Node:
    """The entry's other printings in the world's books, under its heading."""
    if not printings:
        return None
    t = page.t
    key = "ref.printings_used" if printings[0].used else "ref.printings_other"
    links: list[Node] = []
    for index, printing in enumerate(printings):
        if index:
            links.append(", ")
        links.append(a(href=_printing_url(entry, printing))[printing.source])
    return p(class_="ref-printings")[icon("book-open", "xs"), span[t(key)], " ", links]


def preview_card(
    page: Page,
    entry: ReferenceEntry,
    ctx: RenderContext,
    open_url: str,
    printings: Sequence[Printing] = (),
) -> Element:
    """The tooltip / bottom-sheet content for one reference — a glance, not the entry.

    ``ctx`` should be inert (no resolver, static chips): nested references in a
    preview do not chain (documentation/DESIGN.md §7). "Open" is the way deeper.
    """
    t = page.t
    entry, coverage = localized(page, entry)
    glyph = PREVIEW_ICON.get(entry.category, "book-open")
    return div(class_=f"tip-card tip-{entry.category}", data_close=t("tip.close"))[
        div(class_="tip-head")[
            icon(glyph, "sm"),
            b(class_="tip-name")[t.term(entry.category, entry.name) or entry.name],
            span(class_="tip-en muted")[entry.name] if t.term(entry.category, entry.name) else None,
            span(class_="tip-source mono")[entry.source],
        ],
        div(class_="tip-body")[_preview_body(page, entry, ctx), coverage_note(page, coverage)],
        div(class_="tip-foot")[
            a(href=open_url, class_="btn btn-sm btn-primary tip-open")[
                icon("book-open"), span[t("tip.open")]
            ],
            # A declared book supersession set aside is offered, never dropped (§6c).
            [
                a(href=_printing_url(entry, printing), class_="btn btn-sm tip-printing")[
                    t(
                        "ref.printing_used" if printing.used else "ref.printing",
                        source=printing.source,
                    )
                ]
                for printing in printings
            ],
        ],
    ]


def statblock(page: Page, entry: ReferenceEntry, ctx: RenderContext) -> Element:
    """One entry's panel: a creature's statblock, a spell, an item, or its text — in the
    page's language where its text is translated, English elsewhere."""
    t = page.t
    entry, coverage = localized(page, entry)
    data = entry.data
    builder = {
        "monster": _creature,
        "spell": _spell,
        "item": _item,
        "baseitem": _item,
        "magicvariant": _item,
        "itemGroup": _item_group,
        "table": _table_entry,
        "vehicle": _vehicle,
        "facility": _facility,
        "cult": _cult,
        "boon": _cult,
        "psionic": _psionic,
    }.get(entry.category, _generic)
    kind = t(CATEGORY_KEY.get(entry.category, "ref.kind.rule"))
    where = f"{entry.source}{' p. ' + str(data['page']) if data.get('page') else ''}"
    return div(class_=f"panel statblock ref-{entry.category}")[
        header(class_="sb-head")[
            span(class_="eyebrow")[kind],
            h1(class_="sb-name")[
                t.term(entry.category, entry.name) or entry.name,
                small(class_="sb-en")[entry.name] if t.term(entry.category, entry.name) else None,
            ],
            span(class_="sb-source mono", title=where)[where],
        ],
        coverage_note(page, coverage, untranslated=True),
        _printed_in(page, entry.printed_in),
        builder(page, data, ctx),
        _lair(page, entry.lair, ctx) if entry.lair else None,
        _deck_cards(page, entry.cards, ctx),
    ]


def _lair(page: Page, group: dict[str, Any], ctx: RenderContext) -> Node:
    """A legendary creature's lair actions and regional effects, from its group."""
    t = page.t
    return [
        _section(t("ref.lair_actions"), group.get("lairActions"), ctx),
        _section(t("ref.regional_effects"), group.get("regionalEffects"), ctx),
    ]


def reference_page(
    page: Page,
    entry: ReferenceEntry,
    ctx: RenderContext,
    backlinks: Backlinks | None = None,
    printings: Sequence[Printing] = (),
    description: tuple[list[Any], str | None] | None = None,
) -> Element:
    """One entry, in full, what the books say about it — and what links to it."""
    t = page.t
    return app_shell(
        page,
        t.term(entry.category, entry.name) or entry.name,
        div(class_="ref-page")[
            div(class_="ref-top")[
                button(type="button", class_="backlink js-back")[
                    icon("arrow-left"), span[t("ref.back")]
                ],
                view_switch(page),
            ],
            _printings(page, entry, printings),
            statblock(page, entry, ctx),
            _description(page, entry, ctx, description),
            _backlinks(page, backlinks, entry.world_id) if backlinks else None,
            _names_form(page, entry),
            p(class_="ref-roll-result", aria_live="polite", hidden=True),
        ],
        extra_scripts=("/static/js/roll.js",),
        world_id=entry.world_id,
    )


def _description(
    page: Page,
    entry: ReferenceEntry,
    ctx: RenderContext,
    description: tuple[list[Any], str | None] | None,
) -> Node:
    """What the books say about the entry, in the reader's language where translated,
    and its picture; beneath the rules, which a reader at the table came for."""
    if description is None:
        return None
    t = page.t
    entries, image = description
    shown = t.overlay(
        fluff_category(entry.category),
        record_key(entry.name, entry.source),
        {"entries": entries},
        ("entries",),
    )
    gamemaster = page.identity is not None and page.identity.is_gamemaster
    return section(class_="panel ref-fluff")[
        div(class_="panel-head")[
            h2[t("ref.description")],
            a(
                href=translate_url(
                    "fluff", entry.category, entry.source, entry.name, back=page.path
                ),
                class_="btn btn-sm",
            )[icon("languages", "sm"), span[t("translate.text")]]
            if gamemaster and entries
            else None,
        ],
        coverage_note(page, shown.coverage, untranslated=True) if entries else None,
        div(class_="ref-fluff-body")[
            img(class_="ref-fluff-image", src=f"/play/img/{quote(image)}", alt="", loading="lazy")
            if image
            else None,
            render_entries(shown.data["entries"], ctx),
        ],
    ]


# Groups longer than this fold, so a much-cited spell does not bury the page.
BACKLINKS_OPEN = 12

LIBRARY_URL = {
    "chapter": chapter_url,
    "npc": npc_url,
    "place": location_url,
    "treasure": treasure_url,
    "encounter": encounter_url,
}


def _kind(page: Page, category: str) -> str:
    label = page.t(f"glossary.category.{category}")
    return category if label == f"glossary.category.{category}" else label


def _backlinks(page: Page, links: Backlinks, world_id: str | None) -> Node:
    """What links here, one compact row per kind — the campaign's first, for the
    gamemaster: the kind, its count with a bar for its share, then its entries; past
    :data:`BACKLINKS_OPEN` the rest open from "+ N more"."""
    t = page.t
    if not links.total:
        return section(class_="panel ref-backlinks")[
            div(class_="panel-head")[h2[t("ref.backlinks")]],
            p(class_="muted")[t("ref.backlinks_none" if world_id else "ref.backlinks_none_all")],
        ]

    def owner_item(owner: Owner) -> Element:
        shown = name_text(page, owner.category, owner.name)
        href = owner.url or ref_url(owner.category, owner.name, owner.source, world_id)
        if owner.url and world_id:
            href = f"{owner.url}?{urlencode({'world': world_id})}"
        return li[
            a(href=href, class_="ref", title=owner.name if shown != owner.name else None)[shown],
            small(class_="bl-src")[" · ".join(x for x in (owner.context, owner.source) if x)],
        ]

    def library_item(link: Any) -> Element:
        return li[
            a(href=LIBRARY_URL[link.kind](link.record_id), class_="ref")[link.name],
            small(class_="bl-src")[
                " · ".join(x for x in (t(f"ref.backlinks_kind.{link.kind}"), link.context) if x)
            ],
        ]

    rows: list[tuple[str, list[Element]]] = []
    if links.library:
        rows.append((t("ref.backlinks_campaign"), [library_item(x) for x in links.library]))
    rows += [
        (_kind(page, category), [owner_item(o) for o in owners])
        for category, owners in sorted(links.groups, key=lambda g: (-len(g[1]), _kind(page, g[0])))
    ]
    most = max(len(items) for _, items in rows)

    def row(title: str, items: list[Element]) -> Element:
        shown, rest = items[:BACKLINKS_OPEN], items[BACKLINKS_OPEN:]
        return div(class_="bl-row")[
            div(class_="bl-kind")[
                span(class_="bl-name")[title],
                span(class_="bl-count mono")[str(len(items))],
                span(class_="bl-bar", aria_hidden="true")[
                    span(style=f"width:{max(4, round(100 * len(items) / most))}%")
                ],
            ],
            ul(class_="bl-list")[
                shown,
                li(class_="bl-more")[
                    details[
                        summary[t("ref.backlinks_more", count=len(rest))],
                        ul(class_="bl-list")[rest],
                    ]
                ]
                if rest
                else None,
            ],
        ]

    return section(class_="panel ref-backlinks")[
        div(class_="panel-head bl-head")[
            h2[t("ref.backlinks")],
            span(class_="bl-total")[
                b(class_="mono")[str(links.total)],
                " ",
                t.plural("ref.backlinks_entries", links.total),
                " ",
                t.plural("ref.backlinks_kinds", len(rows)),
            ],
        ],
        p(class_="muted bl-hint")[
            t("ref.backlinks_hint" if world_id else "ref.backlinks_hint_all")
        ],
        div(class_="bl-rows")[[row(title, items) for title, items in rows]],
    ]


def _names_form(page: Page, entry: ReferenceEntry) -> Node:
    """The gamemaster names the entry in each other language (TRANSLATION.md §4)."""
    t = page.t
    if page.identity is None or not page.identity.is_gamemaster:
        return None
    others = [lang for lang in t.languages if lang.code != "en"]
    if not others:
        return None
    return section(class_="panel ref-names")[
        div(class_="panel-head")[
            h2[t("glossary.names")],
            p(class_="muted")[t("glossary.names_hint")],
            a(
                href=translate_url("ref", entry.category, entry.source, entry.name, back=page.path),
                class_="btn btn-sm",
            )[icon("languages", "sm"), span[t("translate.text")]],
        ],
        [
            form(method="post", action="/glossary/term", class_="ref-name-form")[
                input_el(type="hidden", name="lang", value=lang.code),
                input_el(type="hidden", name="category", value=entry.category),
                input_el(type="hidden", name="english", value=entry.name),
                input_el(type="hidden", name="next", value=page.path),
                label(for_=f"name-{lang.code}")[lang.name],
                input_el(
                    type="text",
                    id=f"name-{lang.code}",
                    name="value",
                    value=page.t.glossary_term(lang.code, entry.category, entry.name) or "",
                    maxlength="120",
                    placeholder=entry.name,
                ),
                button(type="submit", class_="btn btn-sm")[t("glossary.save")],
            ]
            for lang in others
        ],
    ]


def reference_missing_page(page: Page) -> Element:
    """No such entry in the loaded data."""
    t = page.t
    return app_shell(
        page,
        t("notfound.page_title"),
        message_panel(
            "book-open",
            t("notfound.reference"),
            t("notfound.reference_lede"),
            a(href="/", class_="btn btn-primary")[icon("arrow-left"), span[t("forbidden.back")]],
        ),
    )


__all__ = ["preview_card", "reference_missing_page", "reference_page", "statblock"]
