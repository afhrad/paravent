"""A *Game Master 5e* campaign made into a new world (documentation/CURRENT-WORLD.md §9b).

The gamemaster picks a campaign of the backup and the books to match against, which
become the new world's books. Then:

* every creature the backup names is looked for in those books, by its English name or
  the name the table's glossary gives it ("Irrlicht" is a Will-o'-Wisp); a match is the
  book's creature, the backup's label ("Skelett 1") its "Shown as";
* a creature the books lack becomes a design in the creature builder, published as
  ``Homebrew``. When its name contains a book creature's ("Moorleiche (Zombie)",
  "Catelyn, Drow Elite Warrior") the design begins from that creature, so what the backup
  leaves out — skills, saves, senses, a trait — comes from the book; whatever the backup
  states wins;
* items are matched the same way into treasure, coins counted, the rest written into the
  treasure's notes; notes become chapters, encounters encounters, NPCs plain NPCs (the
  book's creature they are or name as statblock, never a design of their own), pictures
  covers. Player characters and the app's own compendium stay behind.

:meth:`Gm5Import.plan` says all of that before anything is written.
"""

from __future__ import annotations

import re
import secrets
import time
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from app.models.gamestate import (
    Coins,
    CreatureBlock,
    CreatureDesign,
    EncounterMonster,
    Npc,
    Scope,
    SourceSet,
    Treasure,
    TreasureItem,
)
from app.services import media
from app.services.creature_builder import (
    ALIGNMENTS,
    CONDITIONS,
    DAMAGE_TYPES,
    HOMEBREW_SOURCE,
    SIZES,
    TYPES,
    CreatureError,
)
from app.services.gm5_backup import (
    Backup,
    BackupError,
    Block,
    Campaign,
    Container,
    Encounter,
    Item,
    Statblock,
    read_backup,
)
from app.services.units import text_in_feet
from app.services.worlds import WorldInput

if TYPE_CHECKING:
    from app.models.gamestate import World
    from app.services.i18n import Translator
    from app.store.registry import StoreRegistry

# An upload waits this long for its import, then goes.
UPLOAD_LIFETIME = 24 * 3600
TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
# The longest run of words a book creature's name is looked for within a name.
LONGEST_NAME = 6
_WORD = re.compile(r"[\w'’-]+", re.UNICODE)
_COINS = re.compile(r"\((pp|gp|ep|sp|cp|pm|gm|em|sm|km)\)\s*$", re.IGNORECASE)
COIN_CODES = {"pm": "pp", "gm": "gp", "em": "ep", "sm": "sp", "km": "cp"}
_HIT_DICE = re.compile(r"(\d+)\s*[dw](\d+)", re.IGNORECASE)
_DIE_SIZES = {die: size for size, die in SIZES.items()}
_NUMBERED = re.compile(r"\s*\d+\s*$")
_SPEED = re.compile(r"(?P<mode>[^\d,()]*?)\s*(?P<n>\d+(?:[.,]\d+)?)\s*(?P<u>ft\.?|feet|m)\b", re.I)
SPEED_WORDS = {"walk": "walk", "fly": "fly", "swim": "swim", "climb": "climb", "burrow": "burrow"}
SENSES = ("blindsight", "darkvision", "tremorsense", "truesight")
_AXES = {"lawful": "L", "chaotic": "C", "neutral": "N", "good": "G", "evil": "E"}

Status = Literal["book", "variant", "new", "skipped"]
# An encounter's plan line: name, source, on the characters' side, "Shown as".
Line = tuple[str, str, bool, str]


class Gm5ImportError(Exception):
    """A UI message key: the upload is gone, or the campaign is not in the file."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


# -- uploads ---------------------------------------------------------------------------


class Gm5Uploads:
    """Backups uploaded and waiting for their import, in the library's ``.imports``
    folder, gone after the import or a day."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def _path(self, token: str) -> Path:
        if not TOKEN.match(token):
            raise Gm5ImportError("gm5.error.gone")
        return self._folder / f"{token}.gm5.xml"

    def save(self, data: bytes) -> str:
        """Keep a backup that reads as one; its token. Raises :class:`BackupError`."""
        read_backup(data)
        self.clear_old()
        self._folder.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(12)
        path = self._path(token)
        path.write_bytes(data)
        path.chmod(0o600)
        return token

    def load(self, token: str) -> Backup:
        """The backup uploaded under ``token``."""
        path = self._path(token)
        if not path.is_file():
            raise Gm5ImportError("gm5.error.gone")
        try:
            return read_backup(path.read_bytes())
        except BackupError as exc:
            raise Gm5ImportError(exc.key) from exc

    def delete(self, token: str) -> None:
        """Forget an upload once imported."""
        self._path(token).unlink(missing_ok=True)

    def clear_old(self) -> None:
        """Remove uploads older than a day."""
        if not self._folder.is_dir():
            return
        cutoff = time.time() - UPLOAD_LIFETIME
        for path in self._folder.glob("*.gm5.xml"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)


# -- the plan --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CreatureMatch:
    """What becomes of one creature name of the backup."""

    name: str
    count: int
    status: Status
    ref: str = ""  # the book's creature, or the one a design begins from


@dataclass(frozen=True, slots=True)
class ItemMatch:
    """What becomes of one item name: a book's item, coins, or a line in the notes."""

    name: str
    count: int
    ref: str = ""
    coins: bool = False


@dataclass(slots=True)
class Plan:
    """What an import of the chosen campaigns would make, counted."""

    campaigns: list[Campaign]
    creatures: list[CreatureMatch] = field(default_factory=list)
    items: list[ItemMatch] = field(default_factory=list)
    adventures: int = 0
    chapters: int = 0
    encounters: int = 0
    npcs: int = 0
    pictures: int = 0

    def count(self, status: Status) -> int:
        """How many creature names end up as ``status``."""
        return sum(1 for c in self.creatures if c.status == status)


@dataclass(frozen=True, slots=True)
class Imported:
    """What an import made."""

    world: World
    adventures: int
    chapters: int
    encounters: int
    npcs: int
    treasures: int
    designs: int
    pictures: int


def _words(text: str) -> list[str]:
    return [w.casefold() for w in _WORD.findall(text)]


class _Names:
    """Names of one kind in the world's books, looked up whole or within a longer name,
    in English and in the table's glossary language."""

    def __init__(self, rows: Iterable[tuple[str, str]], localized: dict[str, str]) -> None:
        self._refs: dict[str, str] = {}
        for name, source in rows:
            self._refs.setdefault(" ".join(_words(name)), f"{name}|{source}")
        for local, english in localized.items():
            ref = self._refs.get(" ".join(_words(english)))
            if ref is not None:
                self._refs.setdefault(" ".join(_words(local)), ref)

    def exact(self, name: str) -> str:
        """The book entry of exactly this name; "" for none."""
        return self._refs.get(" ".join(_words(name)), "")

    def within(self, name: str) -> str:
        """The longest book name that stands within ``name`` as whole words."""
        words = _words(name)
        for size in range(min(LONGEST_NAME, len(words)), 0, -1):
            for start in range(len(words) - size + 1):
                found = self._refs.get(" ".join(words[start : start + size]))
                if found:
                    return found
        return ""


def _containers(campaign: Campaign) -> Iterator[Container]:
    yield campaign
    yield from campaign.adventures


def _statblocks(backup: Backup, campaign: Campaign) -> Iterator[Statblock]:
    """Every creature of the campaign: its encounters' and its NPCs."""
    for container in _containers(campaign):
        for encounter in container.encounters:
            for combatant in encounter.combatants:
                if combatant.statblock is not None:
                    yield combatant.statblock
                elif (npc := backup.npc(combatant.uid)) is not None:
                    yield npc
        yield from container.npcs


def _items(campaign: Campaign) -> Iterator[Item]:
    for container in _containers(campaign):
        yield from container.items
        for encounter in container.encounters:
            yield from encounter.items


def has_chapters(container: Container) -> bool:
    """Whether a campaign has notes or scenes of its own, which chapters of an adventure
    must hold."""
    return bool(container.notes) or any(e.notes for e in container.encounters)


def unique(name: str, taken: set[str]) -> str:
    """``name``, or ``name (2)`` and on when the world has one already."""
    candidate, number = name, 2
    while candidate.casefold() in taken:
        candidate = f"{name} ({number})"
        number += 1
    taken.add(candidate.casefold())
    return candidate


# -- reading a statblock's words -------------------------------------------------------


class _Words:
    """The backup's rules words, English or in the table's language, to the builder's."""

    def __init__(self, t: Translator) -> None:
        def known(prefix: str, keys: Iterable[str]) -> dict[str, str]:
            found = {k.casefold(): k for k in keys}
            for k in keys:
                found.setdefault(t(f"{prefix}{k}").casefold(), k)
            return found

        self.types = known("rules.type.", TYPES)
        self.damage = known("rules.damage.", DAMAGE_TYPES)
        self.conditions = known("condition.", CONDITIONS)
        self.speeds = known("rules.speed.", SPEED_WORDS)
        self.senses = known("rules.sense.", SENSES)
        self.axes = dict(_AXES)
        for code in ("L", "C", "G", "E"):
            self.axes.setdefault(t(f"rules.align.{code}").casefold(), code)
        self.unaligned = {"unaligned", t("rules.align.U").casefold()}
        self.any = {"any", t("rules.align.A").split()[0].casefold()}

    def type(self, text: str) -> tuple[str, list[str]] | None:
        kind = text.split(",")[0].strip()
        tags = re.findall(r"\(([^)]*)\)", kind)
        word = re.sub(r"\(.*?\)", "", kind).strip().casefold()
        found = self.types.get(word) or next(
            (v for k, v in self.types.items() if k and word.startswith(k)), None
        )
        if found is None:
            return None
        return found, [tag.strip() for t in tags for tag in t.split(",") if tag.strip()]

    def alignment(self, text: str) -> list[str] | None:
        words = _words(text)
        if not words:
            return None
        if set(words) & self.unaligned:
            return ["U"]
        if set(words) & self.any:
            return ["A"]
        codes = [self.axes[w] for w in words if w in self.axes]
        joined = " ".join(codes)
        if joined == "N N":
            joined = "N"
        return joined.split() if joined in ALIGNMENTS else None

    def listed(self, text: str, table: dict[str, str]) -> list[str]:
        words = _words(text)
        return list(dict.fromkeys(table[w] for w in words if w in table))

    def speed(self, text: str) -> dict[str, int]:
        found: dict[str, int] = {}
        for match in _SPEED.finditer(text):
            mode_words = _words(match["mode"])
            mode = next((self.speeds[w] for w in mode_words if w in self.speeds), "walk")
            amount = float(match["n"].replace(",", "."))
            feet = round(amount / 0.3 / 5) * 5 if match["u"].lower() == "m" else int(amount)
            found.setdefault(mode, feet)
        return found

    def senses_text(self, text: str) -> str:
        parts = []
        for part in text.split(","):
            words = _words(part)
            if not words or words[0] in ("passive", "passiv"):
                continue
            converted = text_in_feet(part.strip())
            for local, sense in self.senses.items():
                converted = re.sub(rf"\b{re.escape(local)}\b", sense, converted, flags=re.I)
            parts.append(converted)
        return ", ".join(parts)


# Attack lines as the app writes them, in English and German, into the corpus's tags so
# the tracker rolls them.
_ATTACKS = (
    (re.compile(r"Melee or Ranged Weapon Attack:", re.I), "{@atk mw,rw}"),
    (re.compile(r"Melee Weapon Attack:", re.I), "{@atk mw}"),
    (re.compile(r"Ranged Weapon Attack:", re.I), "{@atk rw}"),
    (re.compile(r"Melee Spell Attack:", re.I), "{@atk ms}"),
    (re.compile(r"Ranged Spell Attack:", re.I), "{@atk rs}"),
    (re.compile(r"Nah- oder Fernkampf-Waffenangriff:", re.I), "{@atk mw,rw}"),
    (re.compile(r"Nahkampf-Waffenangriff:", re.I), "{@atk mw}"),
    (re.compile(r"Fernkampf-Waffenangriff:", re.I), "{@atk rw}"),
    (re.compile(r"Nahkampf-Zauberangriff:", re.I), "{@atk ms}"),
    (re.compile(r"Fernkampf-Zauberangriff:", re.I), "{@atk rs}"),
)
_TO_HIT = re.compile(r"\+(\d+) (to hit|zum Treffen|auf Treffer)")
_HIT = re.compile(r"\b(Hit|Treffer):")
_DAMAGE = re.compile(r"\((\d+\s*[dDwW]\d+(?:\s*[+-−]\s*\d+)?)\)")
_DC = re.compile(r"\b(DC|SG) (\d+)\b")


def tag_text(text: str) -> str:
    """A block's text with its attack, hit, damage and DC written as the corpus's tags;
    text that already has tags stays as it is."""
    if "{@" in text:
        return text
    for pattern, tag in _ATTACKS:
        text = pattern.sub(tag, text)
    text = _TO_HIT.sub(lambda m: f"{{@hit {m[1]}}} {m[2]}", text)
    text = _HIT.sub("{@h}", text)
    text = _DAMAGE.sub(
        lambda m: "({@damage " + re.sub(r"[wW]", "d", m[1]).replace("−", "-") + "})", text
    )
    return _DC.sub(lambda m: f"{{@dc {m[2]}}}", text)


def _blocks(blocks: list[Block]) -> list[CreatureBlock]:
    return [CreatureBlock(name=b.name or "—", text=tag_text(b.text)) for b in blocks]


# -- the import ------------------------------------------------------------------------


class Gm5Import:
    """Plans and runs the import of one campaign of a backup."""

    def __init__(self, registry: StoreRegistry) -> None:
        self._registry = registry

    def _localized(self, lang: str, category: str) -> dict[str, str]:
        if lang == "en":
            return {}
        table = self._registry.glossary.table(lang).get(category, {})
        return {
            local: english for english, local in table.values() if any(ch.isalnum() for ch in local)
        }

    def creature_names(self, source_set: SourceSet, lang: str) -> _Names:
        """The world's creatures by name."""
        rows = (
            (m.name, m.source) for m in self._registry.compendium.monsters().in_books(source_set)
        )
        return _Names(rows, self._localized(lang, "monster"))

    def item_names(self, source_set: SourceSet, lang: str) -> _Names:
        """The world's items by name."""
        rows = ((i.name, i.source) for i in self._registry.compendium.items().in_books(source_set))
        return _Names(rows, self._localized(lang, "item"))

    @staticmethod
    def _campaigns(backup: Backup, indexes: Iterable[int]) -> list[Campaign]:
        chosen = sorted(set(indexes))
        if not chosen or not all(0 <= i < len(backup.campaigns) for i in chosen):
            raise Gm5ImportError("gm5.error.campaign")
        return [backup.campaigns[i] for i in chosen]

    def plan(
        self, backup: Backup, indexes: Iterable[int], sources: Iterable[str], lang: str
    ) -> Plan:
        """What importing the campaigns ``indexes`` against ``sources`` would make."""
        campaigns = self._campaigns(backup, indexes)
        source_set = SourceSet(sources=list(sources))
        creatures = self.creature_names(source_set, lang)
        items = self.item_names(source_set, lang)
        plan = Plan(campaigns=campaigns)
        blocks = [s for c in campaigns for s in _statblocks(backup, c)]
        counts = Counter(s.name for s in blocks)
        numbers = {s.name: s.has_numbers for s in blocks}
        for name, count in sorted(counts.items(), key=lambda kv: kv[0].casefold()):
            if ref := creatures.exact(name):
                plan.creatures.append(CreatureMatch(name, count, "book", ref))
            elif not numbers[name]:
                plan.creatures.append(CreatureMatch(name, count, "skipped"))
            elif base := creatures.within(name):
                plan.creatures.append(CreatureMatch(name, count, "variant", base))
            else:
                plan.creatures.append(CreatureMatch(name, count, "new"))
        item_counts = Counter(i.name for c in campaigns for i in _items(c))
        for name, count in sorted(item_counts.items(), key=lambda kv: kv[0].casefold()):
            if _COINS.search(name):
                plan.items.append(ItemMatch(name, count, coins=True))
            else:
                plan.items.append(ItemMatch(name, count, items.exact(name)))
        containers = [c for campaign in campaigns for c in _containers(campaign)]
        plan.adventures = len(campaigns)
        plan.chapters = sum(
            len(c.notes) + sum(1 for e in c.encounters if e.notes) for c in containers
        ) + sum(  # an adventure of fights alone is gathered into a chapter of its name
            1
            for campaign in campaigns
            for part in campaign.adventures
            if not has_chapters(part) and (part.encounters or part.items or part.npcs)
        )
        plan.encounters = sum(
            1
            for c in containers
            for e in c.encounters
            if any(x.statblock or backup.npc(x.uid) for x in e.combatants)
        )
        plan.npcs = sum(len(c.npcs) for c in containers)
        uids = {c.image for c in containers} | {n.image for c in containers for n in c.notes}
        plan.pictures = len({u for u in uids if u and u in backup.images})
        return plan

    def run(
        self,
        backup: Backup,
        indexes: Iterable[int],
        *,
        name: str,
        sources: Iterable[str],
        t: Translator,
    ) -> Imported:
        """Make the campaigns ``indexes`` into one new world named ``name`` (one campaign's
        own name when empty, else "Game Master 5e"), each campaign an adventure of it,
        with ``sources`` as its books; texts it writes itself in ``t``'s language."""
        campaigns = self._campaigns(backup, indexes)
        return _Run(self, backup, campaigns, name, list(sources), t).go()


class _Run:
    """One import, start to end."""

    def __init__(
        self,
        importer: Gm5Import,
        backup: Backup,
        campaigns: list[Campaign],
        name: str,
        sources: list[str],
        t: Translator,
    ) -> None:
        self.registry = importer._registry
        self.backup = backup
        self.campaigns = campaigns
        self.t = t
        self.words = _Words(t)
        default = campaigns[0].name if len(campaigns) == 1 else t("gm5.world_default")
        self.world = self.registry.worlds.create(
            WorldInput(name=name.strip() or default, sources=tuple(sources), language=t.code)
        )
        # A picture a Game Master 5e adventure had, for the first chapter made of it.
        self.pending_image = ""
        self.creatures = importer.creature_names(self.world.source_set, t.code)
        self.items = importer.item_names(self.world.source_set, t.code)
        self.designs: dict[str, str] = {}  # a backup name → its design's reference
        self.design_count = 0
        self.plain_npcs: list[tuple[str, str]] = []  # (NPC id, backup name) without a statblock
        self.taken: dict[str, set[str]] = {k: set() for k in ("adv", "enc", "npc", "tre")}
        self.titles: dict[str, set[str]] = {}  # chapter titles per adventure
        self.counts = Counter[str]()

    # -- creatures ---------------------------------------------------------------------

    def ref_of(self, block: Statblock) -> str:
        """The creature a statblock is: the book's, or a design made for it; "" when it
        is neither (a bare name)."""
        if ref := self.creatures.exact(block.name):
            return ref
        if block.name in self.designs:
            return self.designs[block.name]
        if not block.has_numbers:
            return ""
        ref = self._design(block)
        self.designs[block.name] = ref
        return ref

    def _design(self, block: Statblock) -> str:
        registry = self.registry
        base = self.creatures.within(block.name)
        cr = block.cr if registry.creatures.known_cr(block.cr) else "1"
        wording = registry.creature_wording(self.world.id, self.t.code)
        taken = {d.name.casefold() for d in registry.creatures.list()}
        name = block.name
        if name.casefold() in taken:
            name = unique(f"{block.name} ({self.world.name})", taken)
        try:
            design = registry.creatures.create(name, self.world.id, cr, base, wording)
        except CreatureError:  # its base is gone after all: begin blank
            base = ""
            design = registry.creatures.create(name, self.world.id, cr, "", wording)
        design = design.model_copy(update=self._fields(block, design, bool(base)))
        saved = registry.creatures.save(design)
        if base:
            registry.copy_base_picture(saved)
        self.design_count += 1
        return f"{saved.name}|{HOMEBREW_SOURCE}"

    def _fields(self, block: Statblock, design: CreatureDesign, based: bool) -> dict[str, Any]:
        """What the backup states, over the design's start; without a base, what it
        leaves out stays empty rather than the builder's starting slam."""
        w = self.words
        fields: dict[str, Any] = {}
        dice = _HIT_DICE.search(block.hit_dice)
        if dice:
            fields["hit_dice"] = max(1, min(200, int(dice[1])))
        if block.size:
            fields["size"] = block.size
        elif dice and int(dice[2]) in _DIE_SIZES:
            fields["size"] = _DIE_SIZES[int(dice[2])]
        if (kind := w.type(block.type)) is not None:
            fields["type"], fields["type_tags"] = kind
        if (alignment := w.alignment(block.alignment)) is not None:
            fields["alignment"] = alignment
        if block.ac is not None:
            fields["ac"] = max(0, min(40, block.ac))
            fields["ac_from"] = block.armor
        if block.speed and (speed := w.speed(block.speed)):
            fields["speed"] = speed
        if len(block.abilities) == 6:
            fields["abilities"] = {k: max(1, min(30, v)) for k, v in block.abilities.items()}
        for key, value in (("saves", block.saves), ("skills", block.skills)):
            if value or not based:
                fields[key] = value
        if block.senses:
            fields["senses"] = w.senses_text(block.senses)
        if block.languages:
            fields["languages"] = block.languages
        for key, text, table in (
            ("resist", block.resist, w.damage),
            ("immune", block.immune, w.damage),
            ("vulnerable", block.vulnerable, w.damage),
            ("condition_immune", block.condition_immune, w.conditions),
        ):
            if text or not based:
                fields[key] = w.listed(text, table)
        for key, blocks in (
            ("traits", block.traits),
            ("actions", block.actions),
            ("reactions", block.reactions),
            ("legendary", block.legendary),
        ):
            if blocks or not based:
                fields[key] = _blocks(blocks)
        if not based:
            fields["bonus"] = []
        return fields

    # -- writing -----------------------------------------------------------------------

    def picture(self, record_id: str, uid: str, stem: str) -> str | None:
        """A picture of the backup saved with a record; its path there."""
        data = self.backup.images.get(uid) if uid else None
        if not data:
            return None
        try:
            path = media.store_upload(self.registry.gamestate.prose, record_id, f"{stem}.jpg", data)
        except media.MediaError:
            return None
        self.counts["pictures"] += 1
        return path

    def chapter(self, adventure_id: str, title: str, body: str, image: str = "") -> str:
        """A chapter at the end of the adventure; its id."""
        adventures = self.registry.adventures
        title = unique(title or self.t("gm5.untitled"), self.titles.setdefault(adventure_id, set()))
        chapter = adventures.create_chapter(adventure_id, title)
        image, self.pending_image = image or self.pending_image, ""
        if path := self.picture(chapter.id, image, title):
            body = f"![{title}]({path})\n\n{body}"
        adventures.update_chapter(
            chapter.id, title=chapter.title, status="planned", body=body.strip() + "\n"
        )
        self.counts["chapters"] += 1
        return chapter.id

    def monster_line(self, block: Statblock, ref: str, ally: bool) -> Line:
        name, _, source = ref.partition("|")
        label = _NUMBERED.sub("", block.label).strip()
        if label.casefold() in (name.casefold(), block.name.casefold()):
            label = ""
        return name, source, ally, label

    def fighters(self, encounter: Encounter) -> tuple[Counter[Line], list[str]]:
        """The encounter's creatures as plan lines, and the names left out."""
        lines: Counter[Line] = Counter()
        missing: list[str] = []
        for combatant in encounter.combatants:
            block = combatant.statblock
            if block is None:
                npc = self.backup.npc(combatant.uid)
                if npc is None:  # a player character
                    continue
                block = replace(npc, label=npc.label or npc.name)
            ref = self.ref_of(block)
            if not ref:
                missing.append(block.label or block.name)
                continue
            lines[self.monster_line(block, ref, not block.enemy)] += 1
        return lines, missing

    def encounter(
        self, encounter: Encounter, where: Scope, adventure_id: str | None, prefix: str = ""
    ) -> None:
        """An encounter of the backup. One with notes is a scene: a chapter of the
        adventure with the notes as its text and the fight and its loot placed in it as
        cards, so the adventure reads in the backup's order."""
        lines, missing = self.fighters(encounter)
        notes = "\n\n".join(
            f"**{n.name}**\n\n{n.text}" if n.name else n.text for n in encounter.notes
        )
        left_out = self.t("gm5.not_imported", names=", ".join(missing)) if missing else ""
        name = encounter.name or self.t("gm5.untitled")
        if encounter.notes and adventure_id is not None:
            chapter_id = self.chapter(adventure_id, f"{prefix}{name}", notes)
            here = Scope(world_id=self.world.id, adventure_id=adventure_id, chapter_id=chapter_id)
            cards = []
            if lines or missing:
                cards.append(f"{{@encounter {self.fight(name, lines, left_out, here)}}}")
            if encounter.items:
                loot = self.t("gm5.treasure_of", name=name)
                cards.append(f"{{@treasure {self.treasure(loot, encounter.items, here)}}}")
            if cards:
                chapter = self.registry.adventures.get_chapter(chapter_id)
                self.registry.adventures.update_chapter(
                    chapter_id,
                    title=chapter.title,
                    status="planned",
                    body=chapter.body.rstrip() + "\n\n" + "\n\n".join(cards) + "\n",
                )
            return
        if lines or missing or notes:
            text = "\n\n".join(p for p in (notes, left_out) if p)
            name = self.fight(name, lines, text, where)
        if encounter.items:
            self.treasure(self.t("gm5.treasure_of", name=name), encounter.items, where)

    def fight(self, name: str, lines: Counter[Line], notes: str, where: Scope) -> str:
        """An encounter with these creatures; its name."""
        registry = self.registry
        made = registry.encounters.create(unique(name, self.taken["enc"]), self.world.id, where)
        registry.encounters.update(
            made.id, name=made.name, scope=where, notes=notes, rules=None, party=None
        )
        registry.encounters.set_monsters(
            made.id,
            [
                EncounterMonster(
                    name=n, source=s, count=min(99, count), ally=ally, label=label or None
                )
                for (n, s, ally, label), count in lines.items()
            ],
        )
        self.counts["encounters"] += 1
        return made.name

    def npc(self, block: Statblock, where: Scope) -> None:
        """A townsperson as a plain NPC (the user's decision, 2026-11-25): the book's
        creature it is or names ("Bürgermeister (Noble)"), never a design of its own; a
        statblock the books do not hold keeps its numbers in the NPC's notes, unless the
        townsperson also fights, whose design the fight made it statblock."""
        ref = (
            self.creatures.exact(block.name)
            or self.creatures.within(block.name)
            or self.designs.get(block.name, "")
        )
        name = unique(block.label or block.name, self.taken["npc"])
        lines = [block.name] if block.name.casefold() != name.casefold() else []
        if not ref and block.has_numbers:
            stated = [
                f"{label} {value}"
                for label, value in (("AC", block.ac), ("HP", block.hp), ("CR", block.cr))
                if value not in (None, "")
            ]
            if stated:
                lines.append(", ".join(stated))
        notes = "\n\n".join(lines)
        made = self.registry.gamestate.npcs.create(
            Npc(scope=where, name=name, statblock=ref or None, notes=notes)
        )
        if not ref:
            self.plain_npcs.append((made.id, block.name))
        self.counts["npcs"] += 1

    def treasure(self, name: str, items: list[Item], where: Scope) -> str:
        found: list[TreasureItem] = []
        coins: dict[str, int] = {}
        notes: list[str] = []
        for item in items:
            if (coin := _COINS.search(item.name)) is not None:
                code = coin[1].lower()
                code = COIN_CODES.get(code, code)
                coins[code] = coins.get(code, 0) + item.quantity
            elif ref := self.items.exact(item.name):
                item_name, _, source = ref.partition("|")
                found.append(
                    TreasureItem(name=item_name, source=source, count=min(999, item.quantity))
                )
            else:
                head = f"**{item.name}**" + (f" ×{item.quantity}" if item.quantity > 1 else "")
                detail = ", ".join(p for p in (item.detail, item.value and f"{item.value} gp") if p)
                notes.append(
                    f"- {head}"
                    + (f" ({detail})" if detail else "")
                    + (f": {' '.join(item.text.split())}" if item.text else "")
                )
        name = unique(name, self.taken["tre"])
        self.registry.gamestate.treasures.create(
            Treasure(
                scope=where,
                name=name,
                items=found,
                coins=Coins(**coins),
                notes="\n".join(notes),
            )
        )
        self.counts["treasures"] += 1
        return name

    def container(
        self, container: Container, where: Scope, adventure_id: str | None, prefix: str = ""
    ) -> None:
        """A campaign's or Game Master 5e adventure's notes and scenes as chapters (titled
        with ``prefix``), its fights, NPCs and items in ``where``."""
        for note in container.notes:
            if adventure_id is not None:
                self.chapter(adventure_id, f"{prefix}{note.name}", note.text, note.image)
        for encounter in container.encounters:
            self.encounter(encounter, where, adventure_id, prefix)
        for npc in container.npcs:
            self.npc(npc, where)
        if container.items:
            self.treasure(self.t("gm5.treasure_of", name=container.name), container.items, where)

    def gathered(self, part: Container, adventure_id: str) -> None:
        """A Game Master 5e adventure of fights, NPCs and items but no notes: one chapter
        of its name, what it holds placed in it and its fights and treasure as cards."""
        chapter_id = self.chapter(adventure_id, part.name, "")
        here = Scope(world_id=self.world.id, adventure_id=adventure_id, chapter_id=chapter_id)
        self.container(part, here, None)
        registry = self.registry
        fights = reversed(registry.encounters.within(here))  # in the order they were made
        cards = [f"{{@encounter {e.name}}}" for e in fights]
        cards += [f"{{@treasure {x.name}}}" for x in registry.treasure.list(self.world.id, here)]
        chapter = registry.adventures.get_chapter(chapter_id)
        registry.adventures.update_chapter(
            chapter_id,
            title=chapter.title,
            status="planned",
            body=(chapter.body.rstrip() + "\n\n" + "\n\n".join(cards)).strip() + "\n",
        )

    def adventure(self, name: str, image: str) -> str:
        made = self.registry.adventures.create(self.world.id, unique(name, self.taken["adv"]))
        if path := self.picture(made.id, image, "cover"):
            self.registry.adventures.set_cover(made.id, path)
        self.counts["adventures"] += 1
        return made.id

    def go(self) -> Imported:
        """Each campaign one adventure: its own notes and scenes, then each of its Game
        Master 5e adventures' as chapters named after that adventure, in the backup's
        order; what any of them holds in that adventure."""
        world = self.world
        for campaign in self.campaigns:
            adventure_id = self.adventure(campaign.name, campaign.image)
            where = Scope(world_id=world.id, adventure_id=adventure_id)
            self.container(campaign, where, adventure_id)
            for part in campaign.adventures:
                # A campaign of one adventure by its own name reads without the name.
                alone = len(campaign.adventures) == 1 and not has_chapters(campaign)
                same = part.name.casefold() == campaign.name.casefold()
                prefix = "" if alone or same else f"{part.name}: "
                self.pending_image = part.image
                if has_chapters(part) or not (part.encounters or part.items or part.npcs):
                    self.container(part, where, adventure_id, prefix)
                else:  # fights alone: a chapter of its name holds them as cards
                    self.gathered(part, adventure_id)
                self.pending_image = ""
        # A townsperson who also fights has the design its fight made as statblock.
        for npc_id, name in self.plain_npcs:
            if name in self.designs:
                self.registry.gamestate.npcs.update(npc_id, statblock=self.designs[name])
        if self.design_count:
            self.registry.creatures.publish()
        c = self.counts
        return Imported(
            world=self.registry.worlds.get(world.id),
            adventures=c["adventures"],
            chapters=c["chapters"],
            encounters=c["encounters"],
            npcs=c["npcs"],
            treasures=c["treasures"],
            designs=self.design_count,
            pictures=c["pictures"],
        )


__all__ = [
    "CreatureMatch",
    "Gm5Import",
    "Gm5ImportError",
    "Gm5Uploads",
    "Imported",
    "ItemMatch",
    "Plan",
    "tag_text",
    "unique",
]
