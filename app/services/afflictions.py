"""Diseases, poisons and curses carried by characters, run by the world's clock.

A disease's or poison's text says in prose what it does. The reader takes what is
mechanics, as the trap reader does (app/services/traps.py): the saving throw that
catches it ("must succeed on a {@dc 11} Constitution saving throw or become infected"),
a poison's damage and the conditions it puts on, for how long ("{@condition poisoned}
for 24 hours"); a disease's incubation ("{@dice 1d4} days after infection"), its
symptoms (a level of exhaustion, a condition), and its course: a save at the end of
each long rest or each dawn, and what the saves do — a level of exhaustion more or less
until none is left (sewer plague), three successes (the 2024 cackle fever), a DC that
falls by a roll until it reaches 0 (the 2014 cackle fever).

Caught, the affliction is kept on the character (``PlayState.afflictions``) with what
was read, so the clock and rests carry it out without reading the books again: its
symptoms show when the clock passes its onset (until then only the gamemaster knows),
a poison wears off at its end, and each long rest or dawn rolls its save on the sheet.
The exhaustion it holds stays through rests until it ends. Whatever else the text says
(disadvantage on checks, a fit of laughter, a cure by an herb) is the table's, shown as
the books' text. A curse is the gamemaster's own: a name and a note, ended by hand.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.models.pc import Affliction, PlayerCharacter
from app.models.protocol import EntryKind, ProtocolEntry, Scope, Visibility
from app.services.dice import Dice, DiceError
from app.services.traps import (
    _CONDITION,
    _DAMAGE,
    _SAVE,
    _SENTENCE,
    ABILITIES,
    _strings,
)
from app.store.gamestate import GameStateStore

DAY = 24 * 60
DAWN = 6 * 60  # the hour a "daily at dawn" save is rolled
KINDS = ("disease", "poison", "curse")
_UNIT = {"minute": 1, "hour": 60, "day": DAY}
_WORDS = {"one": 1, "a": 1, "an": 1, "two": 2, "three": 3}
_COURSE = re.compile(
    r"(at the end of each long rest|daily at dawn|each day at dawn|at dawn each day)"
    r"[^.]*?\{@dc (\d+)\}\s+(\w+)\s+saving throw",
    re.IGNORECASE,
)
_ONSET = re.compile(
    r"(?:\{@dice ([^}|]+)[^}]*\}|\b(one|an?|\d+))\s+(hour|day)s?\s+after infection"
    r"|it takes \{@dice ([^}|]+)[^}]*\}\s+(hour|day)s? for",
    re.IGNORECASE,
)
_LASTS = re.compile(
    r"\bfor (?:\{@dice ([^}|]+)[^}]*\}|(\d+|one|an?|two|three)) (minute|hour|day)s?\b",
    re.IGNORECASE,
)
_EXHAUSTION = re.compile(
    r"\b(?:gains?|suffers?)\s+(?:one|1)\s+(?:level of \{@condition exhaustion[^}]*\}"
    r"|\{@condition exhaustion[^}]*\} level)",
    re.IGNORECASE,
)
# A sentence that catches it: "… or become infected", "… or have the Blinded condition".
_CATCHES = re.compile(
    r"\bor\b[^.]*\b(?:infect|contract|infest)|\bor (?:have|be|become|fall)\b", re.IGNORECASE
)
# A sentence that tells what happens only sometimes: not a symptom.
_SOMETIMES = re.compile(r"^(?:on a|if|when|while|once|after|any)\b|\b(?:whenever|when|if)\b", re.I)
_HALF = re.compile(r"\bhalf (?:as much )?damage", re.IGNORECASE)
_THREE = re.compile(r"succe\w* on three|three (?:successful|successes)", re.IGNORECASE)
_DROP = re.compile(r"DC[^.]*?drops by \{@dice ([^}|]+)", re.IGNORECASE)
_DECREASES = re.compile(r"exhaustion[^.]*?(?:decreases|reduced)", re.IGNORECASE)


@dataclass(frozen=True)
class Catch:
    """The saving throw that catches it, and what failing it does at once."""

    dc: int
    ability: str
    damage: str = ""
    damage_type: str = ""
    half: bool = False
    conditions: tuple[str, ...] = ()


@dataclass(frozen=True)
class AfflictionEffect:
    """What a disease or poison does, as far as its text says it in numbers."""

    kind: str
    catch: Catch | None = None
    lasts: str = ""  # how many units its conditions last ("1", "4d6"); "" until cured
    lasts_unit: int = 60
    onset: str = ""  # dice of units before the symptoms show ("1d4")
    onset_unit: int = DAY
    exhaustion: int = 0  # levels the symptoms bring
    conditions: tuple[str, ...] = ()  # conditions the symptoms bring
    course: str = ""  # "long_rest" or "dawn"
    dc: int | None = None
    ability: str = ""
    rule: str = ""  # "exhaustion", "three", "dc_drop"
    drop: str = ""

    @property
    def lasting(self) -> bool:
        """It stays on the character, rather than doing its harm and going."""
        return self.kind == "disease" or bool(
            self.catch and self.catch.conditions or self.conditions
        )


def _conditions(text: str) -> tuple[str, ...]:
    found = (c.strip().lower() for c in _CONDITION.findall(text))
    return tuple(dict.fromkeys(c for c in found if c != "exhaustion"))


def _amount(word: str) -> int:
    return int(word) if word.isdigit() else _WORDS.get(word.lower(), 1)


def read_affliction(data: dict[str, Any], kind: str) -> AfflictionEffect:
    """What a disease (``kind="disease"``) or poison does, from its text (see the
    module's note)."""
    paragraphs = _strings(data.get("entries"), ("spreading the contagion",))
    sentences = [s for p in paragraphs for s in _SENTENCE.split(p) if s.strip()]
    course_at = next((n for n, s in enumerate(sentences) if _COURSE.search(s)), None)
    course_text = " ".join(sentences[course_at : course_at + 4]) if course_at is not None else ""
    catch: Catch | None = None
    caught_at: int | None = None
    for n, sentence in enumerate(sentences):
        if n == course_at:
            continue
        save = _SAVE.search(sentence)
        if save is None or save[2].lower() not in ABILITIES:
            continue
        if kind == "disease" and (not _CATCHES.search(sentence) or " also " in sentence):
            continue
        after = sentence[save.end() :] + " " + " ".join(sentences[n + 1 : n + 3])
        follow = sentences[n + 1] if n + 1 < len(sentences) else ""
        failing = follow if follow.lower().startswith(("on a failed", "on a failure")) else ""
        damage = _DAMAGE.search(sentence[save.end() :]) or (
            _DAMAGE.search(failing) if failing else None
        )
        catch = Catch(
            dc=int(save[1]),
            ability=ABILITIES[save[2].lower()],
            damage=damage[1].strip() if damage else "",
            damage_type=damage[2].lower() if damage else "",
            half=bool(_HALF.search(after)),
            conditions=_conditions(sentence + " " + failing),
        )
        caught_at = n
        break
    lasts, lasts_unit = "", 60
    if catch is not None and catch.conditions and caught_at is not None:
        near = " ".join(sentences[caught_at : caught_at + 2])
        if "until the contagion ends" not in near.lower():
            found = _LASTS.search(near)
            if found is not None:
                lasts = found[1].strip() if found[1] else str(_amount(found[2]))
                lasts_unit = _UNIT[found[3].lower()]
    onset, unit = "", DAY
    found = _ONSET.search(" ".join(paragraphs)) if kind == "disease" else None
    if found is not None:
        dice = found[1] or found[4] or ""
        onset = dice.strip() if dice else str(_amount(found[2] or "1"))
        unit = _UNIT[(found[3] or found[5]).lower()]
    exhaustion = 0
    symptoms: list[str] = []
    if kind == "disease":
        for n, sentence in enumerate(sentences):
            if n == caught_at or (course_at is not None and course_at <= n < course_at + 4):
                continue
            if _SOMETIMES.search(sentence.strip()) or _SAVE.search(sentence):
                continue
            if _EXHAUSTION.search(sentence):
                exhaustion = 1
            symptoms += _conditions(sentence)
    course, dc, ability, rule, drop = "", None, "", "", ""
    if course_at is not None:
        found = _COURSE.search(sentences[course_at])
        assert found is not None
        if found[3].lower() in ABILITIES:
            course = "long_rest" if "long rest" in found[1].lower() else "dawn"
            dc, ability = int(found[2]), ABILITIES[found[3].lower()]
            dropped = _DROP.search(course_text)
            if dropped is not None:
                rule, drop = "dc_drop", dropped[1].strip()
            elif _THREE.search(course_text):
                rule = "three"
            elif _DECREASES.search(course_text):
                rule = "exhaustion"
    caught = set(catch.conditions) if catch else set()
    return AfflictionEffect(
        kind=kind,
        catch=catch,
        lasts=lasts,
        lasts_unit=lasts_unit,
        onset=onset,
        onset_unit=unit,
        exhaustion=exhaustion,
        conditions=tuple(c for c in dict.fromkeys(symptoms) if c not in caught),
        course=course,
        dc=dc,
        ability=ability,
        rule=rule,
        drop=drop,
    )


@dataclass
class Outcome:
    """What catching it did to one character."""

    character_id: str
    name: str
    roll: int | None = None
    dc: int | None = None
    caught: bool = True
    damage: int = 0
    conditions: tuple[str, ...] = ()
    lasting: bool = False


@dataclass(frozen=True)
class Victim:
    """A character it may catch, with the defences its sheet gives."""

    character_id: str
    name: str
    resist: tuple[str, ...] = ()
    immune: tuple[str, ...] = ()
    vulnerable: tuple[str, ...] = ()


class AfflictionService:
    """Puts diseases, poisons and curses on characters and carries them out."""

    def __init__(
        self,
        store: GameStateStore,
        protocol: Any | None,
        *,
        now: Callable[[str], int],
        save: Callable[[str, str], int],
        hurt: Callable[[str, int], None],
        conditions: frozenset[str],
        changed: Callable[[str], None] | None = None,
        dice: Dice | None = None,
    ) -> None:
        """``now(world)`` is the world clock's minute; ``save(character, ability)`` rolls
        a saving throw on the sheet and gives the total; ``hurt(character, amount)``
        deals damage; ``conditions`` are those a sheet knows; ``changed(character)``
        tells its open screens."""
        self._store = store
        self._protocol = protocol
        self._now = now
        self._save = save
        self._hurt = hurt
        self._known = conditions
        self._changed = changed
        self._dice = dice or Dice()

    # -- catching -------------------------------------------------------------------

    def afflict(
        self,
        world_id: str,
        kind: str,
        name: str,
        ref: str,
        effect: AfflictionEffect,
        victims: Sequence[Victim],
        *,
        with_save: bool,
        by: str,
        by_name: str,
        totals: Mapping[str, int] | None = None,
    ) -> list[Outcome]:
        """Expose ``victims`` to it: each rolls the catching save on its sheet (with
        ``with_save`` and a save to roll), and on a failure — or at once without one —
        takes its damage and conditions, and carries it on if it lasts. ``totals`` are
        saves the players rolled themselves, by character; the rest are rolled here."""
        out: list[Outcome] = []
        now = self._now(world_id)
        catch = effect.catch if with_save else None
        dealt = self._roll(effect.catch.damage) if effect.catch and effect.catch.damage else 0
        for victim in victims:
            outcome = Outcome(victim.character_id, victim.name)
            if catch is not None:
                rolled = (totals or {}).get(victim.character_id)
                outcome.roll = (
                    rolled if rolled is not None else self._save(victim.character_id, catch.ability)
                )
                outcome.dc = catch.dc
                outcome.caught = outcome.roll < catch.dc
            harm = effect.catch.damage_type if effect.catch else ""
            if dealt and (outcome.caught or (effect.catch and effect.catch.half)):
                amount = dealt if outcome.caught else dealt // 2
                outcome.damage = _taken(amount, harm, victim)
            if outcome.caught and effect.lasting:
                outcome.lasting = True
                outcome.conditions = self._carry(
                    world_id, victim.character_id, kind, name, ref, effect, now
                )
            out.append(outcome)
            if outcome.damage:
                self._hurt(victim.character_id, outcome.damage)
            hidden = kind == "disease" and outcome.caught and bool(effect.onset)
            self._log(
                world_id,
                by,
                by_name,
                victim.character_id,
                victim.name,
                f"affliction:caught:{kind}:{_safe(name)}:{_n(outcome.roll)}:{_n(outcome.dc)}:"
                f"{int(outcome.caught)}:{outcome.damage}",
                Visibility.GM if hidden else Visibility.PUBLIC,
            )
        return out

    def _carry(
        self,
        world_id: str,
        character_id: str,
        kind: str,
        name: str,
        ref: str,
        effect: AfflictionEffect,
        now: int,
    ) -> tuple[str, ...]:
        """Keep it on the character: what shows at once is put on now, the symptoms at
        its onset. The conditions put on now."""
        at_once = effect.catch.conditions if effect.catch else ()
        onset = None
        if effect.onset:
            onset = now + max(1, self._roll(effect.onset)) * effect.onset_unit
        affliction = Affliction(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            name=name[:200],
            ref=ref,
            since=now,
            onset=onset,
            ends=now + max(1, self._roll(effect.lasts)) * effect.lasts_unit
            if effect.lasts
            else None,
            pending_conditions=list(effect.conditions),
            pending_exhaustion=effect.exhaustion,
            course=effect.course,
            dc=effect.dc,
            ability=effect.ability,
            rule=effect.rule,
            drop=effect.drop,
        )
        put: list[str] = []

        def change(pc: PlayerCharacter) -> None:
            put.clear()
            held = affliction.model_copy(deep=True)
            for condition in at_once:
                if condition in self._known:
                    held.conditions.append(condition)
                    if condition not in pc.state.conditions:
                        pc.state.conditions = [*pc.state.conditions, condition]
                        put.append(condition)
            if held.onset is None:
                put.extend(_show(pc, held, self._known))
            pc.state.afflictions = [*pc.state.afflictions, held]

        self._mutate(character_id, change)
        return tuple(put)

    def curse(
        self, world_id: str, character_id: str, name: str, note: str, by: str, by_name: str
    ) -> Affliction | None:
        """A curse of the gamemaster's own on a character; None without a name."""
        name = " ".join(name.split())[:200]
        if not name:
            return None
        curse = Affliction(
            id=uuid.uuid4().hex[:12],
            kind="curse",
            name=name,
            note=note.strip()[:2000],
            since=self._now(world_id),
        )
        pc = self._mutate(
            character_id,
            lambda pc: setattr(pc.state, "afflictions", [*pc.state.afflictions, curse]),
        )
        if pc is not None:
            self._log(
                world_id,
                by,
                by_name,
                character_id,
                pc.name or "?",
                f"affliction:curse:{_safe(name)}",
                Visibility.PUBLIC,
            )
        return curse if pc is not None else None

    # -- ending ---------------------------------------------------------------------

    def end(
        self, character_id: str, affliction_id: str, by: str, by_name: str, why: str = "cured"
    ) -> bool:
        """It ends on the character (cured, or worn off): the conditions it put on go,
        unless another affliction holds them too; the exhaustion it held stays, now
        taken by rests as any other. False when it was not there."""
        ended: list[Affliction] = []

        def change(pc: PlayerCharacter) -> None:
            ended.clear()
            ended.extend(a for a in pc.state.afflictions if a.id == affliction_id)
            if ended:
                _drop(pc, ended[0])

        pc = self._mutate(character_id, change)
        if pc is None or not ended:
            return False
        gone = ended[0]
        self._log(
            pc.world_id or "",
            by,
            by_name,
            character_id,
            pc.name or "?",
            f"affliction:ended:{gone.kind}:{_safe(gone.name)}:{why}",
            Visibility.PUBLIC if gone.showing else Visibility.GM,
        )
        return True

    # -- the clock and rests ----------------------------------------------------------

    def passed(self, world_id: str, before: int, now: int, by: str, by_name: str) -> None:
        """The clock moved from ``before`` to ``now``: symptoms that fall due show,
        what wears off goes, and each dawn passed rolls the saves made at dawn."""
        dawns = _dawns(before, now)
        for pc in self._store.characters.list():
            if pc.world_id != world_id or not pc.state.afflictions:
                continue
            for held in list(pc.state.afflictions):
                if held.onset is not None and held.onset <= now:
                    self._onset(world_id, pc.id, held.id, by, by_name)
                if held.ends is not None and held.ends <= now:
                    self.end(pc.id, held.id, by, by_name, "worn_off")
                    continue
                if held.course != "dawn":
                    continue
                # Each dawn after its symptoms showed, until a save cures it.
                for _dawn in (d for d in dawns if held.onset is None or held.onset <= d):
                    if not self._course(world_id, pc.id, held.id, by, by_name):
                        break

    def rested(self, character_id: str, by: str, by_name: str) -> None:
        """A long rest ended: each affliction saved against at its end rolls its save."""
        pc = self._store.characters.find(character_id)
        if pc is None:
            return
        for held in pc.state.afflictions:
            if held.course == "long_rest" and held.showing:
                self._course(pc.world_id or "", character_id, held.id, by, by_name)

    def _onset(
        self, world_id: str, character_id: str, affliction_id: str, by: str, by_name: str
    ) -> None:
        shown: list[Affliction] = []

        def change(pc: PlayerCharacter) -> None:
            shown.clear()
            for held in pc.state.afflictions:
                if held.id == affliction_id and held.onset is not None:
                    _show(pc, held, self._known)
                    shown.append(held)

        pc = self._mutate(character_id, change)
        if pc is not None and shown:
            self._log(
                world_id,
                by,
                by_name,
                character_id,
                pc.name or "?",
                f"affliction:onset:{shown[0].kind}:{_safe(shown[0].name)}",
                Visibility.PUBLIC,
            )

    def _course(
        self, world_id: str, character_id: str, affliction_id: str, by: str, by_name: str
    ) -> bool:
        """One save against it, and what the save does; False once it has ended."""
        pc = self._store.characters.find(character_id)
        held = (
            next((a for a in pc.state.afflictions if a.id == affliction_id), None) if pc else None
        )
        if pc is None or held is None or held.dc is None or not held.ability:
            return False
        roll = self._save(character_id, held.ability)
        ok = roll >= held.dc
        outcome: list[str] = [""]
        drop = self._roll(held.drop) if ok and held.rule == "dc_drop" and held.drop else 0

        def change(pc: PlayerCharacter) -> None:
            mine = next((a for a in pc.state.afflictions if a.id == affliction_id), None)
            if mine is None:
                return
            outcome[0] = ""
            mine.successes += int(ok)
            mine.failures += int(not ok)
            state = pc.state
            if held.rule == "exhaustion":
                if ok:
                    mine.exhaustion = max(0, mine.exhaustion - 1)
                    state.exhaustion = max(0, state.exhaustion - 1)
                    outcome[0] = "better"
                    if mine.exhaustion == 0:
                        outcome[0] = "cured"
                else:
                    mine.exhaustion = min(6, mine.exhaustion + 1)
                    state.exhaustion = min(6, state.exhaustion + 1)
                    outcome[0] = "worse"
            elif held.rule == "three" and mine.successes >= 3:
                outcome[0] = "cured"
            elif held.rule == "dc_drop" and ok:
                mine.dc = max(0, (mine.dc or 0) - drop)
                outcome[0] = "cured" if mine.dc <= 0 else "better"
            if outcome[0] == "cured":
                _drop(pc, mine)

        pc = self._mutate(character_id, change)
        if pc is None:
            return False
        self._log(
            world_id,
            by,
            by_name,
            character_id,
            pc.name or "?",
            f"affliction:save:{_safe(held.name)}:{roll}:{held.dc}:{int(ok)}:{outcome[0]}",
            Visibility.PUBLIC,
        )
        return outcome[0] != "cured"

    # -- helpers --------------------------------------------------------------------

    def _roll(self, dice: str) -> int:
        try:
            return self._dice.roll(dice).total
        except DiceError:
            return 0

    def _mutate(
        self, character_id: str, change: Callable[[PlayerCharacter], None]
    ) -> PlayerCharacter | None:
        if self._store.characters.find(character_id) is None:
            return None
        pc = self._store.characters.mutate(character_id, change)
        if self._changed is not None:
            self._changed(character_id)
        return pc

    def _log(
        self,
        world_id: str,
        by: str,
        by_name: str,
        character_id: str,
        name: str,
        what: str,
        visibility: Visibility,
    ) -> None:
        if self._protocol is None:
            return
        self._protocol.append(
            [
                ProtocolEntry(
                    actor=by,
                    actor_name=by_name,
                    subject=f"character:{character_id}",
                    subject_name=name,
                    kind=EntryKind.CUSTOM,
                    what=what,
                    visibility=visibility,
                    scope=Scope(world=world_id),
                )
            ]
        )


def _show(pc: PlayerCharacter, held: Affliction, known: frozenset[str]) -> list[str]:
    """Its symptoms show: their exhaustion and conditions put on. The conditions put
    on (held is changed in place)."""
    put = []
    for condition in held.pending_conditions:
        if condition in known and condition not in held.conditions:
            held.conditions.append(condition)
            if condition not in pc.state.conditions:
                pc.state.conditions = [*pc.state.conditions, condition]
                put.append(condition)
    if held.pending_exhaustion:
        pc.state.exhaustion = min(6, pc.state.exhaustion + held.pending_exhaustion)
        held.exhaustion = min(6, held.exhaustion + held.pending_exhaustion)
    held.pending_conditions, held.pending_exhaustion, held.onset = [], 0, None
    return put


def _drop(pc: PlayerCharacter, gone: Affliction) -> None:
    """Take it off the character, with the conditions no other affliction holds."""
    rest = [a for a in pc.state.afflictions if a.id != gone.id]
    still = {c for a in rest for c in a.conditions}
    pc.state.conditions = [c for c in pc.state.conditions if c not in gone.conditions or c in still]
    pc.state.afflictions = rest


def _dawns(before: int, now: int) -> list[int]:
    """Each dawn after ``before`` up to ``now``."""
    first = (before - DAWN) // DAY + 1
    return [d * DAY + DAWN for d in range(first, (now - DAWN) // DAY + 1)]


def _taken(amount: int, damage_type: str, victim: Victim) -> int:
    if damage_type in victim.immune:
        return 0
    if damage_type in victim.resist:
        amount //= 2
    if damage_type in victim.vulnerable:
        amount *= 2
    return amount


def _n(value: int | None) -> str:
    return "" if value is None else str(value)


def _safe(text: str) -> str:
    return text.replace(":", " ")


def is_poison(data: dict[str, Any]) -> bool:
    """An item that is a poison."""
    return bool(data.get("poison"))


__all__ = [
    "KINDS",
    "AfflictionEffect",
    "AfflictionService",
    "Catch",
    "Outcome",
    "Victim",
    "is_poison",
    "read_affliction",
]
