"""The naming workshop (documentation/NAMES.md §5): the table's own styles, written in a
form or as raw JSON, copied from a starter, and which of them each world uses."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import ValidationError

from app.models.gamestate import GivenName, Npc
from app.models.naming import (
    SUBJECTS,
    ByGenderPart,
    ElementsPart,
    KinPart,
    LearnedPart,
    ListPart,
    NamePart,
    NamingStyle,
    SoundsPart,
    Subject,
    pattern_tokens,
)
from app.models.pc import PlayerCharacter
from app.services.i18n import FALLBACK, Translatable
from app.services.naming import (
    OWN_PREFIX,
    PERSON,
    FoundStyle,
    Naming,
    StyleEntry,
    language_code,
)
from app.store.gamestate import GameStateStore

_PART_KEY = re.compile(r"\w+")
_SPLIT = re.compile(r"[\n,]")
_SPACED = re.compile(r"[\s,]+")
_GROUP = re.compile(r"[A-Z]")
# Not part of what a style says: the store's own bookkeeping.
_BOOKKEEPING = {"id", "created_at", "updated_at", "draft", "for_world"}
KINDS = ("list", "elements", "by_gender", "sounds", "learned", "kin")
# Fewer examples than this and a chain only hands them back.
MIN_LEARNED = 10


class StyleError(Translatable):
    """A style that cannot be saved."""


@dataclass
class Unserved:
    """A people of a world no style serves: the name it goes by in the world, its other
    names, how the world's people spell it, and a style of the table's that would serve
    it given ``alias`` as another name."""

    race: str
    aliases: list[str] = field(default_factory=list)
    met: list[str] = field(default_factory=list)
    alias_style: tuple[str, str] | None = None
    alias: str = ""


@dataclass
class SubjectGroup:
    """A kind of place in the workshop's index."""

    subject: str
    styles: int = 0
    cultures: list[str] = field(default_factory=list)


@dataclass
class RaceGroup:
    """A race in the workshop's index."""

    race: str
    styles: int = 0
    cultures: list[str] = field(default_factory=list)


@dataclass
class PartFields:
    """One part as the form holds it: lists as text, one entry per line."""

    key: str = ""
    kind: str = "list"
    names: str = ""
    female: str = ""
    male: str = ""
    first: str = ""
    second: str = ""
    second_female: str = ""
    second_male: str = ""
    fixed_female: str = ""
    fixed_male: str = ""
    groups: str = ""
    syllables: str = ""
    min_syllables: str = ""
    max_syllables: str = ""
    ending_female: str = ""
    ending_male: str = ""
    order: str = ""
    kin_part: str = ""
    fallback: str = ""

    @property
    def blank(self) -> bool:
        """True for the empty row the form always ends with."""
        return not any(v.strip() for k, v in vars(self).items() if k != "kind")


@dataclass
class StyleFields:
    """A style as the form holds it."""

    name: str = ""
    summary: str = ""
    pattern: str = ""
    rewrites: str = ""
    parts: list[PartFields] = field(default_factory=list)
    banned: str = ""
    max_cluster: str = ""
    race: str = ""
    culture: str = ""
    sources: str = ""
    aliases: str = ""
    subject: str = "person"
    language: str = ""


def _lines(values: list[str]) -> str:
    return "\n".join(values)


def _words(text: str) -> list[str]:
    return [word.strip() for word in _SPLIT.split(text) if word.strip()]


def fields_of(style: NamingStyle) -> StyleFields:
    """A style as the form shows it."""
    parts: list[PartFields] = []
    for key, part in style.parts.items():
        row = PartFields(key=key, kind=part.kind)
        if isinstance(part, ListPart | LearnedPart):
            row.names, row.female = _lines(part.names), _lines(part.female)
            row.male = _lines(part.male)
            row.order = str(part.order) if isinstance(part, LearnedPart) else ""
        elif isinstance(part, KinPart):
            row.kin_part, row.fallback = part.part, part.fallback
        elif isinstance(part, ElementsPart):
            row.first, row.second = _lines(part.first), _lines(part.second)
            row.second_female = _lines(part.second_female)
            row.second_male = _lines(part.second_male)
        elif isinstance(part, SoundsPart):
            row.groups = "\n".join(f"{k} = {' '.join(v)}" for k, v in part.groups.items())
            row.syllables = " ".join(part.syllables)
            row.min_syllables, row.max_syllables = str(part.min_syllables), str(part.max_syllables)
            row.ending_female = _lines(part.ending_female)
            row.ending_male = _lines(part.ending_male)
        else:
            row.fixed_female, row.fixed_male = part.female, part.male
        parts.append(row)
    rewrites = "\n".join(f"{old} = {new}" for old, new in style.rewrites.items())
    return StyleFields(
        style.name,
        style.summary,
        style.pattern,
        rewrites,
        parts,
        _lines(style.banned),
        str(style.max_cluster) if style.max_cluster else "",
        style.race,
        style.culture,
        _lines(style.sources),
        ", ".join(style.aliases),
        style.subject,
        style.language,
    )


def _number(text: str, default: int, low: int, high: int) -> int:
    if not text.strip():
        return default
    if not text.strip().isdecimal() or not low <= int(text) <= high:
        raise StyleError("naming.error.number", value=text.strip(), low=low, high=high)
    return int(text)


def _groups(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        mark, equals, sounds = line.partition("=")
        if not equals or not _GROUP.fullmatch(mark.strip()):
            raise StyleError("naming.error.group", line=line.strip())
        out[mark.strip()] = [s for s in _SPACED.split(sounds.strip()) if s]
    return out


def _part(row: PartFields) -> NamePart:
    if row.kind == "elements":
        return ElementsPart(
            first=_words(row.first),
            second=_words(row.second),
            second_female=_words(row.second_female),
            second_male=_words(row.second_male),
        )
    if row.kind == "learned":
        return LearnedPart(
            names=_words(row.names),
            female=_words(row.female),
            male=_words(row.male),
            order=_number(row.order, 2, 1, 4),
        )
    if row.kind == "kin":
        return KinPart(part=row.kin_part.strip(), fallback=row.fallback.strip())
    if row.kind == "sounds":
        return SoundsPart(
            groups=_groups(row.groups),
            syllables=[s for s in _SPACED.split(row.syllables.strip()) if s] or [""],
            min_syllables=_number(row.min_syllables, 2, 1, 8),
            max_syllables=_number(row.max_syllables, 3, 1, 8),
            ending_female=_words(row.ending_female),
            ending_male=_words(row.ending_male),
        )
    if row.kind == "by_gender":
        return ByGenderPart(female=row.fixed_female.strip(), male=row.fixed_male.strip())
    return ListPart(names=_words(row.names), female=_words(row.female), male=_words(row.male))


def _rewrites(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        old, equals, new = line.partition("=")
        if not equals or not old.strip():
            raise StyleError("naming.error.rewrite", line=line.strip())
        out[old.strip().lower()] = new.strip()
    return out


def _empty(part: NamePart) -> bool:
    if isinstance(part, ListPart | LearnedPart):
        return not (part.names or part.female or part.male)
    if isinstance(part, KinPart):
        return not part.part
    if isinstance(part, ElementsPart):
        return not (part.second or part.second_female or part.second_male)
    if isinstance(part, SoundsPart):
        return not (any(part.syllables) and any(part.groups.values()))
    return not (part.female or part.male)


def _check_sounds(key: str, part: SoundsPart) -> None:
    """A capital in a template with no group is a typo, not a letter to print."""
    if part.min_syllables > part.max_syllables:
        raise StyleError("naming.error.syllables", part=key)
    for name, sounds in part.groups.items():
        if not sounds:
            raise StyleError("naming.error.empty_group", part=key, group=name)
    for template in part.syllables:
        for mark in _GROUP.findall(template):
            if mark not in part.groups:
                raise StyleError("naming.error.no_group", part=key, group=mark)


def _check(name: str, pattern: str, parts: dict[str, NamePart]) -> None:
    """What the model cannot say in the reader's language, and what would make no name."""
    if not name.strip():
        raise StyleError("naming.error.name")
    tokens = pattern_tokens(pattern)
    if not tokens:
        raise StyleError("naming.error.pattern")
    for token, modifier in tokens:
        if token not in parts:
            raise StyleError("naming.error.unknown_part", part=token)
        if modifier and modifier != "initial":
            raise StyleError("naming.error.modifier", modifier=modifier)
    for key, part in parts.items():
        if _empty(part):
            raise StyleError("naming.error.empty_part", part=key)
        if isinstance(part, SoundsPart):
            _check_sounds(key, part)
        if isinstance(part, KinPart) and (
            part.fallback not in parts or isinstance(parts[part.fallback], KinPart)
        ):
            raise StyleError("naming.error.fallback", part=key)
        if isinstance(part, LearnedPart):
            own = [p for p in (part.names, part.female, part.male) if p]
            if any(len(pool) < MIN_LEARNED for pool in own):
                raise StyleError("naming.error.learned_few", part=key, count=MIN_LEARNED)


def style_from(fields: StyleFields, *, base: NamingStyle | None = None) -> NamingStyle:
    """The style a form describes; ``base`` keeps its identity and rejected names."""
    parts: dict[str, NamePart] = {}
    for row in fields.parts:
        if row.blank:
            continue
        key = row.key.strip()
        if not _PART_KEY.fullmatch(key):
            raise StyleError("naming.error.part_key", part=key)
        if key in parts:
            raise StyleError("naming.error.part_twice", part=key)
        if row.kind == "elements" and not _words(row.first):
            raise StyleError("naming.error.empty_part", part=key)
        parts[key] = _part(row)
    pattern = fields.pattern.strip()
    _check(fields.name, pattern, parts)
    subject = fields.subject if fields.subject in SUBJECTS else "person"
    made = NamingStyle(
        name=fields.name.strip(),
        subject=cast(Subject, subject),
        language=language_code(fields.language),
        race=fields.race.strip(),
        culture=fields.culture.strip(),
        aliases=_words(fields.aliases),
        sources=[line.strip() for line in fields.sources.splitlines() if line.strip()],
        summary=fields.summary.strip(),
        pattern=pattern,
        parts=parts,
        rewrites=_rewrites(fields.rewrites),
        banned=_words(fields.banned),
        max_cluster=_number(fields.max_cluster, 0, 0, 8),
    )
    if base is None:
        return made
    return made.model_copy(
        update={
            "id": base.id,
            "created_at": base.created_at,
            "rejected": base.rejected,
            "draft": base.draft,
            "for_world": base.for_world,
        }
    )


def raw_text(style: NamingStyle) -> str:
    """A style as JSON to read and edit: what it says, without the bookkeeping."""
    return json.dumps(
        style.model_dump(mode="json", exclude=_BOOKKEEPING), ensure_ascii=False, indent=2
    )


def style_from_raw(text: str, *, base: NamingStyle | None = None) -> NamingStyle:
    """The style a raw text describes. An ``id`` in it is ignored: ``base`` keeps its own."""
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StyleError("naming.error.json", line=exc.lineno, detail=exc.msg) from exc
    if not isinstance(data, dict):
        raise StyleError("naming.error.format", detail="an object {…}")
    data = {k: v for k, v in data.items() if k not in _BOOKKEEPING}
    if base is not None:
        data = {
            **data,
            "id": base.id,
            "created_at": base.created_at,
            "draft": base.draft,
            "for_world": base.for_world,
        }
    try:
        style = NamingStyle.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(x) for x in first["loc"])
        raise StyleError("naming.error.format", detail=f"{where}: {first['msg']}") from exc
    _check(style.name, style.pattern, style.parts)
    return style


def _bears(name: str, given: GivenName | None) -> bool:
    return given is not None and given.text == name


class NamingWorkshop:
    """The table's own styles, and which style each world uses."""

    def __init__(self, store: GameStateStore, naming: Naming) -> None:
        """Bind to the library and to every style there is."""
        self._store = store
        self._naming = naming

    def styles(self) -> list[NamingStyle]:
        """The table's styles, by name."""
        return sorted(self._store.naming_styles.list(), key=lambda s: s.name.casefold())

    def get(self, style_id: str) -> NamingStyle:
        """One of the table's styles; raises EntryNotFound (a 404) when it is gone."""
        return self._store.naming_styles.get(style_id)

    def create(self, style: NamingStyle) -> NamingStyle:
        """Keep a new style."""
        return self._store.naming_styles.create(style)

    def save(self, style: NamingStyle) -> NamingStyle:
        """Keep a changed style."""
        return self._store.naming_styles.replace(style)

    def copy_starter(self, starter_id: str, name: str, summary: str) -> NamingStyle:
        """A starter as the table's own, under the name the reader knows it by."""
        found = next((s for s in self._naming.starters if s.id == starter_id), None)
        if found is None:
            raise StyleError("naming.error.no_starter")
        data = found.model_dump(exclude=_BOOKKEEPING, round_trip=True)
        return self.create(NamingStyle(**{**data, "name": name, "summary": summary}))

    def reject(self, style_id: str, name: str) -> NamingStyle:
        """Never offer ``name`` again in this style."""

        def add(style: NamingStyle) -> None:
            if name and name not in style.rejected:
                style.rejected.append(name)

        return self._store.naming_styles.mutate(style_id, add)

    def restore(self, style_id: str, name: str) -> NamingStyle:
        """Offer a rejected name again."""

        def drop(style: NamingStyle) -> None:
            style.rejected = [r for r in style.rejected if r != name]

        return self._store.naming_styles.mutate(style_id, drop)

    def propose(
        self, style: NamingStyle, replacing: str | None = None, world_id: str | None = None
    ) -> NamingStyle:
        """Claude's style: a draft offered nowhere; ``replacing`` is a draft of its own,
        ``world_id`` the world it is for (kept from the draft it replaces when omitted)."""
        if world_id is not None:
            self._store.worlds.get(world_id)
        if replacing is None:
            return self.create(style.model_copy(update={"draft": True, "for_world": world_id}))
        base = self.get(replacing)
        if not base.draft:
            raise StyleError("naming.error.not_draft")
        kept = style_from_raw(raw_text(style), base=base)
        if world_id is not None:
            kept = kept.model_copy(update={"for_world": world_id})
        return self.save(kept)

    def accept(self, style_id: str) -> NamingStyle:
        """Make a draft one of the table's styles. A draft made for a world is ticked
        there, unless the world uses every style anyway (or is gone)."""
        world = self._store.worlds.find(self.get(style_id).for_world)
        style = self._store.naming_styles.update(style_id, draft=False, for_world=None)
        key = f"{OWN_PREFIX}{style.id}"
        if world is not None and world.naming_styles and key not in world.naming_styles:
            self._store.worlds.update(world.id, naming_styles=[*world.naming_styles, key])
        return style

    def relatives(self, world_id: str | None) -> list[tuple[str, str]]:
        """Who a name can descend from: ``(ref, name)`` of the world's people whose names
        Paravent made and who still bear them."""
        npcs = [n for n in self._store.npcs.list() if n.scope.world_id == world_id]
        pcs = [c for c in self._store.characters.list() if c.world_id == world_id]
        rows = [
            *((f"npc:{n.id}", n.name) for n in npcs if _bears(n.name, n.given_name)),
            *((f"pc:{c.id}", c.name) for c in pcs if _bears(c.name, c.given_name)),
        ]
        return sorted(rows, key=lambda row: row[1].casefold())

    def unserved(
        self,
        world_id: str,
        naming: Naming,
        edition: str = "2014",
        race_names: Mapping[str, str] | None = None,
    ) -> list[Unserved]:
        """The peoples of a world's NPCs and characters that no style of ``naming``
        serves: their names come from a fallback until one is made. ``race_names`` (the
        glossary's English → the world's language) joins a people met under both names
        into one, named in the world's language with the English as an alias, and finds
        a style of the table's that serves it under its other name, to give it that alias."""
        world = self._store.worlds.get(world_id)
        races = [n.race for n in self._store.npcs.list() if n.scope.world_id == world_id]
        races += [
            c.species.name
            for c in self._store.characters.list()
            if c.world_id == world_id and c.species
        ]
        spelled: dict[str, str] = {}
        for race in races:
            if race.strip():
                spelled.setdefault(race.strip().casefold(), race.strip())
        local = {e.casefold(): (e, n) for e, n in (race_names or {}).items() if e and n}
        back = {n.casefold(): (e, n) for e, n in local.values()}

        def find(race: str) -> FoundStyle:
            return naming.find(race, edition=edition, language=world.language)

        found: dict[str, Unserved] = {}
        for key, race in spelled.items():
            if not find(race).fallback:
                continue
            pair = local.get(key) or back.get(key)
            name, other = (pair[1], pair[0]) if pair else (race, "")
            if other.casefold() == name.casefold():
                other = ""
            people = found.setdefault(name.casefold(), Unserved(name))
            people.met.append(race)
            if other and other not in people.aliases:
                people.aliases.append(other)
        own = [e for e in naming.entries() if e.kind == "own" and e.style is not None]
        for people in found.values():
            for other in [people.race, *people.aliases]:
                serving = next((e.style for e in own if e.of_race(other.casefold())), None)
                if serving is not None:
                    people.alias_style = (serving.id, serving.name)
                    people.alias = people.met[0]
                    break
        return sorted(found.values(), key=lambda p: p.race.casefold())

    def add_alias(self, style_id: str, alias: str) -> NamingStyle:
        """Let one of the table's styles serve a race under another name too."""

        def add(style: NamingStyle) -> None:
            known = {a.casefold() for a in style.aliases} | {style.race.casefold()}
            if alias.strip() and alias.strip().casefold() not in known:
                style.aliases.append(alias.strip())

        return self._store.naming_styles.mutate(style_id, add)

    def kin_parts(self, ref: str) -> dict[str, str]:
        """The name parts of a person named by :meth:`relatives`; empty when they are gone."""
        kind, _, person_id = ref.partition(":")
        person: Npc | PlayerCharacter | None = None
        if kind == "npc":
            person = self._store.npcs.find(person_id)
        elif kind == "pc":
            person = self._store.characters.find(person_id)
        if person is None or person.given_name is None:
            return {}
        return dict(person.given_name.parts) if _bears(person.name, person.given_name) else {}

    def accept_all(self) -> int:
        """Accept every draft; how many there were."""
        drafts = [s for s in self.styles() if s.draft]
        for style in drafts:
            self.accept(style.id)
        return len(drafts)

    def delete(self, style_id: str) -> None:
        """Remove a style."""
        self._store.naming_styles.delete(style_id)


def races(entries: Iterable[StyleEntry]) -> list[RaceGroup]:
    """Every race of people with its styles' cultures, by name; styles of no race come
    last. Styles of places are :func:`subjects`."""
    found: dict[str, RaceGroup] = {}
    for entry in entries:
        if entry.style is None or entry.subject != PERSON:
            continue
        group = found.setdefault(entry.race.casefold(), RaceGroup(entry.race))
        group.styles += 1
        if entry.culture and entry.culture not in group.cultures:
            group.cultures.append(entry.culture)
    return sorted(found.values(), key=lambda g: (not g.race, g.race.casefold()))


def subjects(entries: Iterable[StyleEntry]) -> list[SubjectGroup]:
    """Every kind of place with styles, in the order of :data:`SUBJECTS`."""
    found: dict[str, SubjectGroup] = {}
    for entry in entries:
        if entry.style is None or entry.subject == PERSON:
            continue
        group = found.setdefault(entry.subject, SubjectGroup(entry.subject))
        group.styles += 1
        if entry.culture and entry.culture not in group.cultures:
            group.cultures.append(entry.culture)
    return [found[s] for s in SUBJECTS if s in found]


def search(
    entries: Iterable[StyleEntry],
    query: str = "",
    race: str | None = None,
    subject: str | None = None,
) -> list[StyleEntry]:
    """The styles of a race (``""`` is no race) or of a kind of place, those matching
    ``query``, or both: words of the query may be in the name, race, culture or
    summary, in any order. A race lists people only."""
    words = query.casefold().split()

    def text(entry: StyleEntry) -> str:
        style = entry.style
        extra = f"{style.summary} {' '.join(style.aliases)}" if style else ""
        return f"{entry.label} {entry.race} {entry.culture} {extra}".casefold()

    return [
        e
        for e in entries
        if e.style is not None
        and (subject is None or e.subject == subject)
        and (race is None or e.subject == PERSON)
        and (
            race is None
            or e.race.casefold() == race.casefold()
            or (race and e.of_race(race.casefold()))
        )
        and all(word in text(e) for word in words)
    ]


def race_names(glossary: Any, language: str) -> dict[str, str]:
    """The glossary's races, English → ``language``'s name: what joins a people met under
    both names (Dwarf, Zwerg) in :meth:`NamingWorkshop.unserved`. English has none."""
    if language == FALLBACK:
        return {}
    return {e: n for e, n in glossary.table(language).get("race", {}).values()}


__all__ = [
    "race_names",
    "KINDS",
    "NamingWorkshop",
    "PartFields",
    "RaceGroup",
    "StyleError",
    "Unserved",
    "StyleFields",
    "SubjectGroup",
    "fields_of",
    "raw_text",
    "races",
    "search",
    "subjects",
    "style_from",
    "style_from_raw",
]
