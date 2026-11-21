"""Reading a backup of Lion's Den's *Game Master 5e* (documentation/CURRENT-WORLD.md §9b).

The backup is one XML file, ``<data><version>…``: campaigns holding adventures, notes,
encounters, NPCs and items; pictures as Base64 JPEG referenced by ``uid``; player
characters as finished numbers; and the app's own compendium. This module only reads it
into plain values, decoding the app's numbers (a challenge rating of ``-1`` is ¼, a size
of ``1`` Small, skills and saves by position). What becomes of it is
:mod:`app.services.gm5_import`'s business.
"""

from __future__ import annotations

import base64
import binascii
import re
import xml.etree.ElementTree as ET  # noqa: S405 — a DTD is refused before parsing
from dataclasses import dataclass, field

ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
# The app numbers skills in alphabetical order of their English names.
SKILLS = (
    "acrobatics",
    "animal handling",
    "arcana",
    "athletics",
    "deception",
    "history",
    "insight",
    "intimidation",
    "investigation",
    "medicine",
    "nature",
    "perception",
    "performance",
    "persuasion",
    "religion",
    "sleight of hand",
    "stealth",
    "survival",
)
SIZES = ("T", "S", "M", "L", "H", "G")
# Its challenge ratings below 1, and an absent one, which the app means as 1.
CR_CODES = {"-4": "0", "-3": "0", "-2": "1/8", "-1": "1/4", "0": "1/2"}
# A DTD could declare entities; a backup never carries one.
_DOCTYPE = re.compile(rb"<!(DOCTYPE|ENTITY)", re.IGNORECASE)


class BackupError(Exception):
    """The file is not a Game Master 5e backup; ``key`` is the UI message."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass(frozen=True, slots=True)
class Block:
    """A trait, action, reaction or legendary action: a name and its text."""

    name: str
    text: str


@dataclass(slots=True)
class Statblock:
    """A creature as the app keeps it, decoded; empty fields were absent."""

    name: str
    label: str = ""
    enemy: bool = True
    uid: str = ""
    size: str = ""
    type: str = ""
    alignment: str = ""
    ac: int | None = None
    armor: str = ""
    abilities: dict[str, int] = field(default_factory=dict)
    hp: int | None = None
    hit_dice: str = ""
    speed: str = ""
    saves: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    resist: str = ""
    immune: str = ""
    vulnerable: str = ""
    condition_immune: str = ""
    senses: str = ""
    languages: str = ""
    cr: str = "1"
    traits: list[Block] = field(default_factory=list)
    actions: list[Block] = field(default_factory=list)
    reactions: list[Block] = field(default_factory=list)
    legendary: list[Block] = field(default_factory=list)

    @property
    def has_numbers(self) -> bool:
        """Whether there is a creature to build from it, not only a name."""
        return bool(self.traits or self.actions or self.abilities)


@dataclass(frozen=True, slots=True)
class Note:
    """A note: a title and plain text, perhaps with a picture."""

    name: str
    text: str
    image: str = ""


@dataclass(frozen=True, slots=True)
class Item:
    """An item as listed: from the app's compendium (``source``) or the gamemaster's."""

    name: str
    detail: str = ""
    text: str = ""
    quantity: int = 1
    value: str = ""
    magic: bool = False


@dataclass(slots=True)
class Combatant:
    """One creature of an encounter: a statblock, or only a ``uid`` pointing at a
    player character or an NPC."""

    statblock: Statblock | None
    uid: str = ""


@dataclass(slots=True)
class Encounter:
    """A prepared fight, its saved state left out."""

    name: str
    combatants: list[Combatant] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)


@dataclass(slots=True)
class Container:
    """What a campaign and an adventure both hold."""

    name: str
    image: str = ""
    notes: list[Note] = field(default_factory=list)
    encounters: list[Encounter] = field(default_factory=list)
    npcs: list[Statblock] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)


@dataclass(slots=True)
class Campaign(Container):
    """A campaign: its own notes and fights, and its adventures."""

    adventures: list[Container] = field(default_factory=list)
    characters: int = 0


@dataclass(slots=True)
class Backup:
    """The whole file: campaigns and the pictures they name by ``uid``."""

    campaigns: list[Campaign]
    images: dict[str, bytes]

    def npc(self, uid: str) -> Statblock | None:
        """The NPC a combatant's ``uid`` points at, in any campaign."""
        for campaign in self.campaigns:
            for npc in [*campaign.npcs, *(n for a in campaign.adventures for n in a.npcs)]:
                if npc.uid == uid:
                    return npc
        return None


def _text(el: ET.Element, tag: str) -> str:
    return (el.findtext(tag) or "").strip()


def _int(value: str) -> int | None:
    try:
        return int(float(value))
    except ValueError:
        return None


def _blocks(el: ET.Element, tag: str) -> list[Block]:
    return [
        Block(name=_text(b, "name"), text=(b.findtext("text") or "").strip())
        for b in el.findall(tag)
        if _text(b, "name") or _text(b, "text")
    ]


def statblock(el: ET.Element) -> Statblock:
    """A ``monster`` or ``npc`` element, decoded."""
    scores = [_int(s) for s in _text(el, "abilities").split(",")]
    abilities = (
        {a: s for a, s in zip(ABILITIES, scores, strict=False) if s is not None}
        if len(scores) == 6
        else {}
    )
    size = _int(_text(el, "size"))
    saves = [
        ABILITIES[i]
        for s in el.findall("savingThrow")
        if (i := _int(_text(s, "ability") or "0")) is not None and 0 <= i < 6
    ]
    skills = [
        SKILLS[i]
        for s in el.findall("skill")
        if (i := _int(_text(s, "id") or "0")) is not None and 0 <= i < len(SKILLS)
    ]
    cr = _text(el, "cr")
    return Statblock(
        name=" ".join(_text(el, "name").split()),
        label=" ".join(_text(el, "label").split()),
        enemy=_text(el, "enemy") == "1",
        uid=_text(el, "uid"),
        size=SIZES[size] if size is not None and 0 <= size < 6 else "",
        type=_text(el, "type"),
        alignment=_text(el, "alignment"),
        ac=_int(_text(el, "ac")),
        armor=_text(el, "armor"),
        abilities=abilities,
        hp=_int(_text(el, "hpMax")),
        hit_dice=_text(el, "hd"),
        speed=_text(el, "speed"),
        saves=list(dict.fromkeys(saves)),
        skills=list(dict.fromkeys(skills)),
        resist=_text(el, "resist"),
        immune=_text(el, "immune"),
        vulnerable=_text(el, "vulnerable"),
        condition_immune=_text(el, "conditionImmune"),
        senses=_text(el, "senses"),
        languages=_text(el, "languages"),
        cr=CR_CODES.get(cr, cr) if cr else "1",
        traits=_blocks(el, "trait"),
        actions=_blocks(el, "action"),
        reactions=_blocks(el, "reaction"),
        legendary=_blocks(el, "legendary"),
    )


def _image(el: ET.Element) -> str:
    image = el.find("imageData")
    return _text(image, "uid") if image is not None else ""


def _notes(el: ET.Element) -> list[Note]:
    return [
        Note(
            name=" ".join(_text(n, "name").split()),
            text=(n.findtext("text") or "").strip(),
            image=_image(n),
        )
        for n in el.findall("note")
    ]


def _items(el: ET.Element) -> list[Item]:
    return [
        Item(
            name=" ".join(_text(i, "name").split()),
            detail=_text(i, "detail"),
            text=(i.findtext("text") or "").strip(),
            quantity=max(1, _int(_text(i, "quantity") or "1") or 1),
            value=_text(i, "value"),
            magic=_text(i, "magic") == "1",
        )
        for i in el.findall("item")
        if _text(i, "name")
    ]


def _encounters(el: ET.Element) -> list[Encounter]:
    found = []
    for e in el.findall("encounter"):
        combatants = []
        for c in e.findall("combatant"):
            monster = c.find("monster")
            if monster is None:
                continue
            if _text(monster, "name"):
                combatants.append(Combatant(statblock(monster), _text(monster, "uid")))
            elif _text(monster, "uid"):
                combatants.append(Combatant(None, _text(monster, "uid")))
        found.append(
            Encounter(
                name=" ".join(_text(e, "name").split()),
                combatants=combatants,
                notes=_notes(e),
                items=_items(e),
            )
        )
    return found


def _fill(container: Container, el: ET.Element) -> None:
    container.image = _image(el)
    container.notes = _notes(el)
    container.encounters = _encounters(el)
    container.npcs = [statblock(n) for n in el.findall("npc")]
    container.items = _items(el)


def read_backup(data: bytes) -> Backup:
    """The backup's campaigns and pictures; raises :class:`BackupError` for a file that
    is none."""
    if _DOCTYPE.search(data):
        raise BackupError("gm5.error.not_backup")
    try:
        root = ET.fromstring(data)  # noqa: S314 — no DTD, so no entities to expand
    except ET.ParseError as exc:
        raise BackupError("gm5.error.not_backup") from exc
    if root.tag != "data" or root.find("version") is None:
        raise BackupError("gm5.error.not_backup")
    campaigns = []
    for el in root.findall("campaign"):
        campaign = Campaign(name=" ".join(_text(el, "name").split()) or "Campaign")
        _fill(campaign, el)
        for a in el.findall("adventure"):
            adventure = Container(name=" ".join(_text(a, "name").split()) or "Adventure")
            _fill(adventure, a)
            campaign.adventures.append(adventure)
        campaign.characters = len(el.findall("pc"))
        campaigns.append(campaign)
    images: dict[str, bytes] = {}
    for image in root.findall("imageData"):
        uid, encoded = _text(image, "uid"), image.findtext("encoded") or ""
        try:
            images[uid] = base64.b64decode("".join(encoded.split()), validate=True)
        except (binascii.Error, ValueError):
            continue
    return Backup(campaigns=campaigns, images=images)


__all__ = [
    "Backup",
    "BackupError",
    "Block",
    "Campaign",
    "Combatant",
    "Container",
    "Encounter",
    "Item",
    "Note",
    "Statblock",
    "read_backup",
    "statblock",
]
