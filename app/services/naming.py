"""Making names from naming styles (documentation/NAMES.md).

Styles come from the books (built here, in memory: the corpus is never copied), from the
starters Paravent ships, and from the table's own file. Each has a key the forms and the
MCP tools pass around; the books' keys are the ones the NPC generator has always sent.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Literal

from app.models.naming import (
    ByGenderPart,
    ElementsPart,
    KinPart,
    LearnedPart,
    ListPart,
    NamePart,
    NamingStyle,
    SoundsPart,
)
from app.services.dice import Dice
from app.store.npc_tables import SURNAME_TABLES, Ancestry, NpcTables, Table

STARTERS_FILE = Path(__file__).resolve().parents[2] / "data" / "rules" / "naming_styles.json"
STYLE_PREFIX = "style:"
ANY_STYLE = "style:"
STARTER_PREFIX = "starter:"
OWN_PREFIX = "own:"
RACE_PREFIX = "race:"
SUBJECT_PREFIX = "subject:"
PERSON = "person"
# The books' ancestries under the other names creatures go by: an orc finds the
# half-orc's tables, a githyanki the gith's.
BOOK_ALIASES: dict[str, tuple[str, ...]] = {
    "Half-Orc": ("Orc",),
    "Gith": ("Githyanki", "Githzerai"),
}
GENDERS = ("any", "female", "male")
# How many tries per name before a style counts as run out.
ATTEMPTS = 20
# How often a learned word is tried for one the examples do not already hold.
LEARNED_TRIES = 20
_START, _END = "\x02", "\x03"

_TOKEN = re.compile(r"\{(\w+)(?::(\w+))?\}")
_WORDS = re.compile(r"[a-z]+")
_VOWELS = frozenset("aeiouyæøœ")
# A name repeating itself this often in thirty samples is a style to look at.
ALIKE_SHARE = 0.4

Kind = Literal["ancestry", "style", "starter", "own", "race", "subject"]


class NamingError(LookupError):
    """No style has that key."""


@dataclass(frozen=True)
class StyleEntry:
    """A style as offered: its key, label, origin and, but for "any style" and "any of
    this race", the style."""

    key: str
    label: str
    kind: Kind
    style: NamingStyle | None
    race: str = ""
    culture: str = ""
    subject: str = PERSON

    @property
    def language(self) -> str:
        """The language the style's names are made for; empty for every language."""
        return self.style.language if self.style else ""

    def rank(self, language: str) -> int:
        """How well it suits a reader of ``language``: 0 made for it, 1 for every
        language, 2 made for another (never picked by itself)."""
        own = self.language
        if not own:
            return 1
        return 0 if own == language else 2

    def of_race(self, folded: str) -> bool:
        """True when a case-folded race is this style's, or another name of it."""
        aliases = self.style.aliases if self.style else ()
        return folded in (self.race.casefold(), *(a.casefold() for a in aliases))


@dataclass(frozen=True)
class FoundStyle:
    """Which style serves a race and culture; ``fallback`` when none was made for them."""

    key: str
    fallback: bool = False


@dataclass(frozen=True)
class MadeName:
    """A name and the value of each part in it, kept on the person (NAMES.md §3)."""

    text: str
    parts: dict[str, str] = field(default_factory=dict)


def language_code(language: str) -> str:
    """``"de-DE"`` → ``"de"``; empty stays empty."""
    return language.strip().lower().replace("_", "-").split("-")[0]


def pool_key(prefix: str, value: str, subject: str = PERSON) -> str:
    """The key of a pool that rolls among several styles: ``race:Dwarf``,
    ``race:Dwarf|settlement``, ``subject:water``."""
    if prefix == RACE_PREFIX and subject != PERSON:
        return f"{prefix}{value}|{subject}"
    return f"{prefix}{value}"


def creature_races(name: str, tags: Iterable[str] = (), label: str = "") -> list[str]:
    """What a creature may be called as a race, most telling first: the name it is shown
    under, its subtype tags ("orc" of "humanoid (orc)"), its own name, each word of it."""
    out: list[str] = []
    for candidate in (label, *tags, name, *name.split()):
        word = candidate.strip()
        if word and word.casefold() not in (c.casefold() for c in out):
            out.append(word)
    return out


def _words(text: str) -> set[str]:
    return set(_WORDS.findall(text.lower()))


def _expanded(table: Table, cell: int = 0) -> list[str]:
    """A table's names, each as often as the rolls its row covers."""
    return [
        row.cells[cell]
        for row in table.rows
        if len(row.cells) > cell
        for _ in range(row.high - row.low + 1)
    ]


def _ancestry_style(ancestry: Ancestry, culture: str) -> NamingStyle:
    """An ancestry's tables as a style: a given name, and the clan or family it may have."""
    surnames = [t for t in ancestry.tables if t.caption.lower() in SURNAME_TABLES]
    given = [t for t in ancestry.tables if t.caption.lower() not in SURNAME_TABLES]
    given = [t for t in given if "clan" not in _words(t.caption)] or given
    if culture:
        given = [t for t in given if t.caption.startswith(f"{culture},")] or given
    given = [t for t in given if "child" not in _words(t.caption)] or given
    part = ListPart()
    for table in given:
        words = _words(table.caption)
        pool = "female" if "female" in words else "male" if "male" in words else "names"
        getattr(part, pool).extend(_expanded(table))
    parts: dict[str, NamePart] = {"given": part}
    pattern = "{given}"
    if surnames:
        parts["family"] = ListPart(names=[n for t in surnames for n in _expanded(t)])
        pattern = "{given} {family}"
    return NamingStyle(
        name=ancestry.name,
        race=ancestry.name,
        culture=culture,
        aliases=list(BOOK_ALIASES.get(ancestry.name, ())),
        pattern=pattern,
        parts=parts,
    )


def _book_style(table: Table) -> NamingStyle:
    """A 2024 guide's name table: a given name and a surname, in two columns."""
    return NamingStyle(
        name=style_label(table.caption),
        culture=style_label(table.caption),
        pattern="{given} {surname}",
        parts={
            "given": ListPart(names=_expanded(table)),
            "surname": ListPart(names=_expanded(table, 1)),
        },
    )


def style_label(caption: str) -> str:
    """``"1: Common Names"`` → ``"Common"``."""
    return caption.split(": ", 1)[-1].removesuffix(" Names")


def _plain(letter: str) -> str:
    """A letter without its accents: "á" → "a"."""
    return unicodedata.normalize("NFKD", letter)[0].casefold() if letter else ""


def cluster(name: str) -> int:
    """The most consonants in a row in ``name``; three of one letter count as too many
    for any limit. An apostrophe, hyphen or space ends a run."""
    longest = run = 0
    repeated = 1
    previous = ""
    for letter in name:
        plain = _plain(letter)
        run = run + 1 if plain.isalpha() and plain not in _VOWELS else 0
        repeated = repeated + 1 if plain == previous and plain.isalpha() else 1
        previous = plain
        longest = max(longest, run, 9 if repeated >= 3 else 0)
    return longest


def refused(style: NamingStyle, name: str) -> bool:
    """True for a name the style's checks or rejections turn down."""
    folded = name.casefold()
    if any(r.casefold() == folded for r in style.rejected):
        return True
    if any(word.casefold() in folded for word in style.banned if word):
        return True
    return bool(style.max_cluster) and cluster(name) > style.max_cluster


def sample_notes(names: Sequence[str], wanted: int) -> list[tuple[str, dict[str, str]]]:
    """What samples say about a style, as ``(catalogue key, params)``."""
    notes: list[tuple[str, dict[str, str]]] = []
    if len(names) < wanted:
        notes.append(("naming.check.runs_out", {"count": str(len(names))}))
    starts = Counter(_plain(n[0]) for n in names if n)
    if len(names) >= 10 and starts:
        letter, seen = starts.most_common(1)[0]
        if seen / len(names) >= ALIKE_SHARE:
            notes.append(("naming.check.alike", {"letter": letter.upper(), "count": str(seen)}))
    return notes


def _splits_gender(part: NamePart) -> bool:
    if isinstance(part, ByGenderPart):
        return True
    if isinstance(part, SoundsPart):
        return bool(part.ending_female and part.ending_male)
    if isinstance(part, KinPart):
        return False
    if isinstance(part, LearnedPart):
        return bool(part.female and part.male)
    if isinstance(part, ListPart):
        return bool(part.female and part.male)
    return bool(part.second_female and part.second_male)


def _chain(names: Iterable[str], order: int) -> dict[str, list[str]]:
    """For each ``order`` letters, the letters that follow them in ``names``."""
    chain: dict[str, list[str]] = {}
    for name in names:
        state = _START * order
        for letter in [*name.lower(), _END]:
            chain.setdefault(state, []).append(letter)
            state = (state + letter)[-order:]
    return chain


def _repeats(style: NamingStyle, parts: Mapping[str, str]) -> bool:
    """True when two parts came out the same: Gudvald Gudvaldsson."""
    seen = [
        value.casefold()
        for key, value in parts.items()
        if value and not isinstance(style.parts[key], ByGenderPart)
    ]
    return len(seen) != len(set(seen))


class NameMaker:
    """Makes one name at a time from a style."""

    def __init__(self, dice: Dice) -> None:
        """Roll with ``dice``."""
        self._dice = dice
        self._chains: dict[tuple[int, str], dict[str, list[str]]] = {}

    def _pick(self, options: Sequence[str]) -> str:
        return options[self._dice.die(len(options)) - 1] if options else ""

    def _value(self, part: NamePart, gender: str) -> str:
        if isinstance(part, ByGenderPart):
            return part.female if gender == "female" else part.male
        if isinstance(part, ListPart):
            own = {"female": part.female, "male": part.male}.get(gender, [])
            return self._pick(
                [*own, *part.names] if own or part.names else [*part.female, *part.male]
            )
        if isinstance(part, SoundsPart):
            return self._sounds(part, gender)
        if isinstance(part, LearnedPart):
            return self._learned(part, gender)
        if isinstance(part, ElementsPart):
            return self._elements(part, gender)
        return ""  # a kin part is read in make(), which has the style

    def _learned(self, part: LearnedPart, gender: str) -> str:
        own = {"female": part.female, "male": part.male}.get(gender, [])
        pool = own or part.names or [*part.female, *part.male]
        if not pool:
            return ""
        chain = self._chains.setdefault((id(part), gender), _chain(pool, part.order))
        known = {n.casefold() for n in pool}
        low, high = min(map(len, pool)), max(map(len, pool)) + 2
        word = ""
        for _ in range(LEARNED_TRIES):
            word = self._walk(chain, part.order, high)
            if low <= len(word) and word.casefold() not in known:
                break
        return word[:1].upper() + word[1:]

    def _walk(self, chain: dict[str, list[str]], order: int, limit: int) -> str:
        state, word = _START * order, ""
        while len(word) < limit:
            letter = self._pick(chain.get(state, [_END]))
            if letter == _END:
                break
            word += letter
            state = (state + letter)[-order:]
        return word

    def _sounds(self, part: SoundsPart, gender: str) -> str:
        span = max(part.max_syllables - part.min_syllables, 0) + 1
        count = part.min_syllables + self._dice.die(span) - 1
        word = "".join(
            self._pick(part.groups[mark]) if mark in part.groups else mark
            for _ in range(count)
            for mark in self._pick(part.syllables)
        )
        endings = {"female": part.ending_female, "male": part.ending_male}.get(gender, [])
        word += self._pick(endings)
        return word[:1].upper() + word[1:]

    def _elements(self, part: ElementsPart, gender: str) -> str:
        own = {"female": part.second_female, "male": part.second_male}.get(gender, [])
        seconds = own or part.second or [*part.second_female, *part.second_male]
        first = self._pick(part.first)
        return first + self._pick([s for s in seconds if s.lower() != first.lower()])

    def make(self, style: NamingStyle, gender: str = "any") -> str:
        """A name; gender is settled first, so every part agrees."""
        return self.build(style, gender).text

    def build(
        self, style: NamingStyle, gender: str = "any", kin: Mapping[str, str] | None = None
    ) -> MadeName:
        """A name with its parts; ``kin`` holds a parent's, which kin parts read."""
        if gender not in ("female", "male") and any(
            _splits_gender(p) for p in style.parts.values()
        ):
            gender = "female" if self._dice.die(2) == 1 else "male"
        values: dict[str, str] = {}

        def fill(found: re.Match[str]) -> str:
            part = found[1]
            if part not in values:
                values[part] = self._part(style, part, gender, kin or {})
            value = values[part]
            return value[:1].upper() if found[2] == "initial" else value

        name = " ".join(_TOKEN.sub(fill, style.pattern).split())
        for broken, mended in style.rewrites.items():
            name = re.sub(re.escape(broken), mended, name, flags=re.IGNORECASE)
        return MadeName(name, values)

    def _part(self, style: NamingStyle, key: str, gender: str, kin: Mapping[str, str]) -> str:
        part = style.parts[key]
        if isinstance(part, KinPart):
            return kin.get(part.part) or self._value(style.parts[part.fallback], gender)
        return self._value(part, gender)


class Naming:
    """Every style there is, and names from them."""

    def __init__(
        self,
        tables: NpcTables,
        own: Callable[[], Iterable[NamingStyle]] = tuple,
        starters: Path = STARTERS_FILE,
        allowed: frozenset[str] | None = None,
    ) -> None:
        """``own`` lists the table's styles; ``starters`` is the shipped file;
        ``allowed`` keeps only those styles (:meth:`within`)."""
        self._tables = tables
        self._own = own
        self._starters = starters
        self._allowed = allowed

    def within(self, keys: Iterable[str]) -> Naming:
        """Only the styles with these keys, as a world chose them (NAMES.md §5b): every
        lookup, pool and menu of the view sees no other. Keys of styles that are gone
        are ignored, and none left — or none given — means every style."""
        wanted = frozenset(keys)
        if not wanted:
            return self
        existing = {e.key for e in self._made()}
        chosen = wanted & existing
        if not chosen:
            return self
        view = Naming(self._tables, self._own, self._starters, allowed=chosen)
        view.__dict__["starters"] = self.starters
        return view

    @property
    def restricted(self) -> bool:
        """True for a view :meth:`within` a world's choice."""
        return self._allowed is not None

    @cached_property
    def starters(self) -> list[NamingStyle]:
        """The styles Paravent ships."""
        raw = json.loads(self._starters.read_text(encoding="utf-8"))
        return [NamingStyle.model_validate(item) for item in raw["styles"]]

    def entries(self) -> list[StyleEntry]:
        """Every style: ancestries, the 2024 styles, starters, the table's own; then
        "any of this race" and "any of this kind of place" over them."""
        out = self._made()
        if self._allowed is not None:
            out = [e for e in out if e.style is None or e.key in self._allowed]
            if not any(e.kind == "style" and e.style is not None for e in out):
                out = [e for e in out if e.key != ANY_STYLE]
        races = {(e.subject, e.race.casefold()): e.race for e in out if e.race}
        out.extend(
            StyleEntry(pool_key(RACE_PREFIX, race, subject), race, "race", None, race, "", subject)
            for (subject, _), race in sorted(races.items(), key=lambda i: i[1].casefold())
        )
        subjects = dict.fromkeys(e.subject for e in out if e.subject != PERSON)
        out.extend(
            StyleEntry(pool_key(SUBJECT_PREFIX, subject), "", "subject", None, subject=subject)
            for subject in subjects
        )
        return out

    def _made(self) -> list[StyleEntry]:
        """The styles themselves, without the pools over them."""
        out: list[StyleEntry] = []
        for ancestry in self._tables.ancestries():
            out.append(
                StyleEntry(
                    ancestry.key,
                    f"{ancestry.name} ({ancestry.source})",
                    "ancestry",
                    _ancestry_style(ancestry, ""),
                    ancestry.name,
                )
            )
            for culture in ancestry.cultures:
                out.append(
                    StyleEntry(
                        f"{ancestry.key}|{culture}",
                        f"{ancestry.name}: {culture}",
                        "ancestry",
                        _ancestry_style(ancestry, culture),
                        ancestry.name,
                        culture,
                    )
                )
        books = self._tables.name_styles()
        if books:
            out.append(StyleEntry(ANY_STYLE, "", "style", None))
            for table in books:
                out.append(
                    StyleEntry(
                        f"{STYLE_PREFIX}{table.caption}",
                        style_label(table.caption),
                        "style",
                        _book_style(table),
                        culture=style_label(table.caption),
                    )
                )
        for style in self.starters:
            out.append(
                StyleEntry(
                    f"{STARTER_PREFIX}{style.id}",
                    style.id,
                    "starter",
                    style,
                    style.race,
                    style.culture,
                    style.subject,
                )
            )
        own = [s for s in self._own() if not s.draft]
        for style in sorted(own, key=lambda s: s.name.casefold()):
            out.append(
                StyleEntry(
                    f"{OWN_PREFIX}{style.id}",
                    style.name,
                    "own",
                    style,
                    style.race,
                    style.culture,
                    style.subject,
                )
            )
        return out

    def default_key(self, edition: str, subject: str = PERSON) -> str:
        """The 2024 styles for a 2024 world, else human names, else whatever exists; for
        a place, any style of its kind."""
        if subject != PERSON:
            return pool_key(SUBJECT_PREFIX, subject)
        entries = [e for e in self.entries() if e.subject == PERSON]
        keys = [e.key for e in entries]
        if edition == "2024" and ANY_STYLE in keys:
            return ANY_STYLE
        human = next((k for k in keys if k.lower().startswith("human|")), None)
        if human is None:
            # A world's own humans ("Mensch" with the alias Human) before anything else.
            race = next((e.race for e in entries if e.style and e.of_race("human")), None)
            human = pool_key(RACE_PREFIX, race) if race else None
        return human or (keys[0] if keys else "")

    def entry(self, key: str) -> StyleEntry:
        """The style with ``key``; raises :class:`NamingError` when there is none."""
        found = next((e for e in self.entries() if e.key == key), None)
        if found is None:
            raise NamingError(key)
        return found

    def find(
        self,
        race: str = "",
        culture: str = "",
        edition: str = "2014",
        language: str = "",
        subject: str = PERSON,
    ) -> FoundStyle:
        """The style for a race and culture: both, the race (any of its cultures), the
        culture (any race), else the books' default, marked as a fallback. A race may be
        another name of a style's (``aliases``), and the table's own come first, so its
        Zwerg with the alias Dwarf serves a Dwarf before the books' tables do. Styles
        made for the reader's ``language`` come before those for every language, and
        those made for another language are never picked (NAMES.md §5); a race's key
        rolls among them as :meth:`made` is told the same language."""
        wanted_race, wanted_culture = race.strip().casefold(), culture.strip().casefold()
        code = language_code(language)
        made = sorted(
            (
                e
                for e in self.entries()
                if e.style is not None and e.subject == subject and e.rank(code) < 2
            ),
            key=lambda e: (e.rank(code), e.kind != "own"),
        )

        def same(entry: StyleEntry, *, of_race: bool, of_culture: bool) -> bool:
            return (not of_race or entry.of_race(wanted_race)) and (
                not of_culture or entry.culture.casefold() == wanted_culture
            )

        if wanted_race and wanted_culture:
            for entry in made:
                if same(entry, of_race=True, of_culture=True):
                    return FoundStyle(entry.key)
        if wanted_race and any(same(e, of_race=True, of_culture=False) for e in made):
            exact = not wanted_culture
            race_name = next(e.race for e in made if same(e, of_race=True, of_culture=False))
            return FoundStyle(pool_key(RACE_PREFIX, race_name, subject), fallback=not exact)
        if wanted_culture:
            for entry in made:
                if same(entry, of_race=False, of_culture=True):
                    return FoundStyle(entry.key, fallback=bool(wanted_race))
        return FoundStyle(
            self.default_key(edition, subject), fallback=bool(wanted_race or wanted_culture)
        )

    def for_creature(self, races: Sequence[str], edition: str, language: str = "") -> FoundStyle:
        """The style for a creature: the first of its race names (:func:`creature_races`)
        a style serves, else the default, marked as a fallback."""
        for race in races:
            found = self.find(race, "", edition, language)
            if not found.fallback and found.key.startswith(RACE_PREFIX):
                return found
        return FoundStyle(self.default_key(edition), fallback=True)

    def _style(
        self, key: str, entries: Sequence[StyleEntry], dice: Dice, language: str = ""
    ) -> NamingStyle:
        found = next((e for e in entries if e.key == key), None)
        if found is None:
            raise NamingError(key)
        if found.style is not None:
            return found.style
        if found.kind in ("race", "subject"):
            return self._pooled(found, entries, dice, language)
        books = [e.style for e in entries if e.kind == "style" and e.style is not None]
        return books[dice.die(len(books)) - 1]

    def _pooled(
        self, pool_entry: StyleEntry, entries: Sequence[StyleEntry], dice: Dice, language: str
    ) -> NamingStyle:
        """One style of a race or a kind of place: those made for ``language`` if there
        are any, else those for every language, else the rest."""
        folded = pool_entry.race.casefold()
        pool = [
            e
            for e in entries
            if e.style is not None
            and e.subject == pool_entry.subject
            and (pool_entry.kind == "subject" or e.race.casefold() == folded)
        ]
        if not pool:
            raise NamingError(pool_entry.key)
        code = language_code(language)
        best = min(e.rank(code) for e in pool)
        tier = [e.style for e in pool if e.rank(code) == best and e.style is not None]
        return tier[dice.die(len(tier)) - 1]

    def name(self, key: str, gender: str = "any", dice: Dice | None = None) -> str:
        """One name in a style; raises :class:`NamingError` for an unknown key."""
        return next(iter(self.names(key, gender, dice=dice)), "")

    def sample(
        self, style: NamingStyle, gender: str = "any", count: int = 30, dice: Dice | None = None
    ) -> list[str]:
        """Up to ``count`` different names from a style, saved or not."""
        made = self._distinct(lambda _: style, gender, count, (), dice or Dice())
        return [m.text for m in made]

    def names(
        self,
        key: str,
        gender: str = "any",
        count: int = 1,
        avoid: Collection[str] = (),
        dice: Dice | None = None,
        spread: bool = False,
        language: str = "",
    ) -> list[str]:
        """Up to ``count`` different names, none of them in ``avoid`` (by any case);
        fewer when the style runs out. ``spread`` starts them with different letters
        while it can: a cast reads better that way. A race's or a kind of place's key
        rolls among the styles made for ``language`` first."""
        made = self.made(key, gender, count, avoid, dice, spread, language=language)
        return [m.text for m in made]

    def made(
        self,
        key: str,
        gender: str = "any",
        count: int = 1,
        avoid: Collection[str] = (),
        dice: Dice | None = None,
        spread: bool = False,
        kin: Mapping[str, str] | None = None,
        language: str = "",
    ) -> list[MadeName]:
        """As :meth:`names`, with each name's parts; ``kin`` holds a parent's parts."""
        dice = dice or Dice()
        entries = self.entries()
        return self._distinct(
            lambda d: self._style(key, entries, d, language),
            gender,
            count,
            avoid,
            dice,
            spread,
            kin,
        )

    def _distinct(
        self,
        style_of: Callable[[Dice], NamingStyle],
        gender: str,
        count: int,
        avoid: Collection[str],
        dice: Dice,
        spread: bool = False,
        kin: Mapping[str, str] | None = None,
    ) -> list[MadeName]:
        maker = NameMaker(dice)
        taken = {n.casefold() for n in avoid}
        starts: set[str] = set()
        out: list[MadeName] = []
        attempts = count * ATTEMPTS
        for attempt in range(attempts):
            if len(out) >= count:
                break
            style = style_of(dice)
            made = maker.build(style, gender, kin)
            name = made.text
            if not name or name.casefold() in taken or refused(style, name):
                continue
            if _repeats(style, made.parts):
                continue
            if spread and attempt < attempts // 2 and _plain(name[0]) in starts:
                continue
            taken.add(name.casefold())
            starts.add(_plain(name[0]))
            out.append(made)
        return out


__all__ = [
    "ANY_STYLE",
    "FoundStyle",
    "GENDERS",
    "MadeName",
    "OWN_PREFIX",
    "PERSON",
    "RACE_PREFIX",
    "SUBJECT_PREFIX",
    "STARTER_PREFIX",
    "Naming",
    "NameMaker",
    "NamingError",
    "StyleEntry",
    "cluster",
    "creature_races",
    "language_code",
    "pool_key",
    "refused",
    "sample_notes",
    "style_label",
]
