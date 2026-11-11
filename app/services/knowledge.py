"""What a world's party has learned about kinds of creatures (documentation/COMBAT.md §5,
"What players know about a creature").

Filled in from the log as the fight goes: every entry the players may read is looked at
once, as it is appended. What a creature was seen to use (an attack, an ability with a
save, a spell), a condition it shrugged off, and what each damage type that landed on it
did, read from its statblock at that moment (half, nothing, double, as dealt). Kept per
world and per kind of creature (``Goblin|MM``) in ``library/knowledge.json``, so the
next goblin fight starts with it.

Only what the table saw counts: an entry the players may not read (a hidden creature,
the gamemaster's own rolls) teaches nothing. The gamemaster adds the rest by hand — a
lore check, Identify, telling the table: any action, trait, defence, condition immunity,
an AC and CR band, a note — and can make the party forget any fact again.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.models.gamestate import Combat, Combatant, Knowledge, KnownFact
from app.models.protocol import EntryKind, ProtocolEntry, Visibility
from app.services.monster_attacks import damage_list, defence, spells_of
from app.services.protocol import attack_entry_id
from app.services.text import paragraphs, strip_tags
from app.store.gamestate import GameStateStore
from app.store.protocol import log_key

# "Fire Breath {@recharge 5}": the recharge is not part of the name.
_RECHARGE = re.compile(r"\{@recharge[^}]*\}")
# What a statblock calls an action, by kind of entry.
_ACTIONS = ("action", "bonus", "reaction", "legendary")
_AC_BANDS = ((12, "low"), (15, "medium"), (18, "high"))
_CR_BANDS = ((0.5, "minor"), (4, "moderate"), (10, "dangerous"), (16, "deadly"))


def action_name(raw: str) -> str:
    """An action's name as the log and the facts know it: tags made plain, no recharge."""
    return " ".join(strip_tags(_RECHARGE.sub("", raw)).split())


@dataclass(frozen=True)
class Revealable:
    """A fact the gamemaster can tell the table: its id, what kind (``action``,
    ``trait``, ``spell``, ``damage``, ``immune``, ``ac``, ``cr``), the English name or type it is
    about, and what telling it stores."""

    id: str
    kind: str
    name: str
    fact: KnownFact


def revealable(data: Mapping[str, Any]) -> list[Revealable]:
    """Everything a statblock offers to reveal, in its order."""
    out: list[Revealable] = []
    for key in _ACTIONS:
        for entry in data.get(key) or []:
            if isinstance(entry, Mapping) and entry.get("name"):
                name = action_name(str(entry["name"]))
                out.append(Revealable(f"action:{name}", "action", name, _told()))
    for entry in data.get("trait") or []:
        if isinstance(entry, Mapping) and entry.get("name"):
            name = action_name(str(entry["name"]))
            text = " ".join(paragraphs(entry.get("entries") or []))
            out.append(Revealable(f"trait:{name}", "trait", name, _told(text=text)))
    for key in ("immune", "resist", "vulnerable"):
        for kind in damage_list(data, key):
            out.append(
                Revealable(f"damage:{kind}", "damage", kind, _told(effect=defence(kind, data)))
            )
    for spell in spells_of(data):
        out.append(Revealable(f"spell:{spell}", "spell", spell, _told()))
    for condition in data.get("conditionImmune") or []:
        if isinstance(condition, str):
            out.append(Revealable(f"immune:{condition}", "immune", condition, _told()))
    ac = _first_number(data.get("ac"), "ac")
    if ac is not None:
        band = next((word for top, word in _AC_BANDS if ac <= top), "very_high")
        out.append(Revealable("ac", "ac", band, _told(text=band)))
    cr = _challenge(data.get("cr"))
    if cr is not None:
        band = next((word for top, word in _CR_BANDS if cr <= top), "legendary")
        out.append(Revealable("cr", "cr", band, _told(text=band)))
    return out


def _told(**fields: Any) -> KnownFact:
    return KnownFact(how="revealed", **fields)


def _first_number(values: Any, key: str) -> int | None:
    for value in values if isinstance(values, list) else [values]:
        if isinstance(value, int):
            return value
        if isinstance(value, Mapping) and isinstance(value.get(key), int):
            return int(value[key])
    return None


def _challenge(value: Any) -> float | None:
    raw = value.get("cr") if isinstance(value, Mapping) else value
    if not isinstance(raw, str):
        return None
    top, _, bottom = raw.partition("/")
    try:
        return int(top) / int(bottom) if bottom else float(top)
    except ValueError:
        return None


class KnowledgeService:
    """Reads and adds to each world's :class:`Knowledge`."""

    def __init__(
        self,
        store: GameStateStore,
        combat: Callable[[str], Combat | None],
        statblock: Callable[[Combatant], Mapping[str, Any] | None],
        entry: Callable[[str | None, str], ProtocolEntry | None],
    ) -> None:
        """``combat(id)`` finds a fight; ``statblock(combatant)`` its creature's data;
        ``entry(world, id)`` an entry of the world's log (what a save refers to)."""
        self._store = store
        self._combat = combat
        self._statblock = statblock
        self._entry = entry

    def of(self, world_id: str | None, ref: str) -> dict[str, KnownFact]:
        """What the world's party knows of a kind of creature."""
        found = self._store.knowledge.find(log_key(world_id))
        return dict(found.creatures.get(ref, {})) if found is not None else {}

    def reveal(
        self, world_id: str | None, ref: str, facts: list[Revealable], note: str | None = None
    ) -> None:
        """The gamemaster tells the table these facts; ``note`` (when given) replaces the
        free note, an empty one removes it."""
        learned = [(ref, r.id, r.fact) for r in facts]
        self._learn(world_id, learned, revealed=True)
        if note is not None:
            self._change(world_id, ref, "note", _told(text=note.strip()) if note.strip() else None)

    def forget(self, world_id: str | None, ref: str, fact_id: str) -> None:
        """The party no longer knows this fact, however it was learned."""
        self._change(world_id, ref, fact_id, None)

    def _change(self, world_id: str | None, ref: str, fact_id: str, fact: KnownFact | None) -> None:
        key = log_key(world_id)
        record = self._store.knowledge.find(key) or Knowledge(id=key)
        facts = dict(record.creatures.get(ref, {}))
        if fact is None:
            if fact_id not in facts:
                return
            del facts[fact_id]
        else:
            facts[fact_id] = fact
        creatures = {**record.creatures, ref: facts}
        if not facts:
            del creatures[ref]
        record = record.model_copy(update={"creatures": creatures})
        record.touch()
        self._store.knowledge.upsert(record)

    def observe(self, entry: ProtocolEntry) -> None:
        """Learn what ``entry`` showed the table, if anything."""
        if entry.visibility is not Visibility.PUBLIC or not entry.scope.combat:
            return
        combat = self._combat(entry.scope.combat)
        if combat is None:
            return
        learned: list[tuple[str, str, KnownFact]] = []
        actor = _member(combat, entry.subject)
        kind = actor.ref if actor is not None else None
        # A spell rolled as an attack or a save effect is learned as the spell it is.
        spells = spells_of(self._statblock(actor) or {}) if actor is not None and kind else []
        if entry.kind is EntryKind.ATTACK and entry.roll is not None and kind is not None:
            fact = KnownFact(types=sorted(entry.types))
            learned.append(_action(kind, entry.what, fact, spells))
        elif entry.kind is EntryKind.EFFECT and kind is not None:
            effect = next(
                (
                    e
                    for e in combat.pending
                    if e.kind == "save_effect" and attack_entry_id(combat.id, e.at) == entry.id
                ),
                None,
            )
            save = effect.save_ability if effect is not None else ""
            fact = KnownFact(types=sorted(entry.types), save=save)
            learned.append(_action(kind, entry.what, fact, spells))
        elif entry.kind is EntryKind.CAST and kind is not None and entry.what.startswith("cast:"):
            spell = entry.what.removeprefix("cast:").partition(":")[0]
            learned.append((kind, f"spell:{spell}", KnownFact()))
        elif entry.kind is EntryKind.CONDITION and kind is not None:
            head, _, condition = entry.what.partition(":")
            if head == "immune" and condition and actor is not None and not actor.hidden:
                learned.append((kind, f"immune:{condition}", KnownFact()))
        elif entry.kind is EntryKind.ATTACK and entry.outcome == "hit":
            target = _member(combat, entry.target or "")
            learned += self._damage(target, entry.types)
        elif entry.kind is EntryKind.SAVE and entry.what.startswith("save:"):
            # A save effect's damage on one target, the types the effect's: seen on a
            # failure, or when a success still took some (an immune one takes none).
            if entry.outcome != "failure" and not entry.amount:
                return
            effect_entry = self._entry(entry.scope.world, entry.refers_to or "")
            if effect_entry is not None:
                learned += self._damage(actor, effect_entry.types)
        if learned:
            self._learn(entry.scope.world, learned)

    def _damage(
        self, target: Combatant | None, types: Mapping[str, int]
    ) -> list[tuple[str, str, KnownFact]]:
        if target is None or target.ref is None or target.hidden:
            return []
        data = self._statblock(target)
        return [
            (target.ref, f"damage:{kind}", KnownFact(effect=defence(kind, data)))
            for kind in sorted(types)
            if kind
        ]

    def _learn(
        self,
        world_id: str | None,
        learned: list[tuple[str, str, KnownFact]],
        *,
        revealed: bool = False,
    ) -> None:
        key = log_key(world_id)
        record = self._store.knowledge.find(key) or Knowledge(id=key)
        creatures = {ref: dict(facts) for ref, facts in record.creatures.items()}
        changed = False
        for ref, fact_id, fact in learned:
            facts = creatures.setdefault(ref, {})
            known = facts.get(fact_id)
            if known is not None and known.how == "revealed" and not revealed:
                continue  # the gamemaster's word stands
            if known is not None:
                fact = known.model_copy(
                    update={
                        "how": "revealed" if revealed else known.how,
                        "types": sorted({*known.types, *fact.types}),
                        "save": fact.save or known.save,
                        "effect": fact.effect or known.effect,
                        "text": fact.text or known.text,
                    }
                )
                if fact == known:
                    continue
            facts[fact_id] = fact
            changed = True
        if changed:
            record = record.model_copy(update={"creatures": creatures})
            record.touch()
            self._store.knowledge.upsert(record)


def _member(combat: Combat, ref: str) -> Combatant | None:
    """The combatant an entry names (``combatant:<combat>/<id>``)."""
    prefix = f"combatant:{combat.id}/"
    if not ref.startswith(prefix):
        return None
    return combat.combatant(ref.removeprefix(prefix))


def _action(kind: str, what: str, fact: KnownFact, spells: list[str]) -> tuple[str, str, KnownFact]:
    name = action_name(what)
    return kind, f"{'spell' if name in spells else 'action'}:{name}", fact


__all__ = ["KnowledgeService", "Revealable", "action_name", "revealable"]
