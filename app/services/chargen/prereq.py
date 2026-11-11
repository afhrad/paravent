"""Prerequisites of feats and optional features (documentation/CHARGEN-WIZARD.md §5).

A ``prerequisite`` is a list of alternatives — any one satisfied is enough — and each
alternative a dict whose conditions must all hold. Typed conditions are checked; free
prose (``other``, a campaign) cannot be, and comes back to be **confirmed** by the
player rather than silently allowed or blocked (CHARGEN.md G4).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.services.i18n import Translatable


class Unmet(Translatable):
    """Why an option is not available yet, in the reader's language."""


@dataclass(frozen=True)
class Context:
    """What a prerequisite is checked against: the character as chosen so far."""

    level: int = 0
    class_levels: Mapping[str, int] = field(default_factory=dict)
    abilities: Mapping[str, int] = field(default_factory=dict)
    species: str = ""
    background: str = ""
    feats: frozenset[str] = frozenset()
    features: frozenset[str] = frozenset()
    armor: frozenset[str] = frozenset()
    weapons: frozenset[str] = frozenset()
    caster: bool = False
    spells: frozenset[str] = frozenset()
    feat_categories: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Verdict:
    """Whether an option may be chosen, why not, and what the player must confirm."""

    unmet: tuple[Unmet, ...] = ()
    confirm: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        """True when nothing typed stands in the way."""
        return not self.unmet


def _name(value: Any) -> str:
    return str(value).split("|")[0].strip().lower()


def _check(condition: str, value: Any, ctx: Context) -> tuple[list[Unmet], list[str]]:
    """One condition: (what is unmet, what needs confirming)."""
    unmet: list[Unmet] = []
    confirm: list[str] = []
    if condition == "level":
        if isinstance(value, int):
            if ctx.level < value:
                unmet.append(Unmet("chargen.prereq.level", n=value))
        elif isinstance(value, Mapping):
            needed = int(value.get("level", 0))
            cls = value.get("class")
            if isinstance(cls, Mapping):
                have = ctx.class_levels.get(str(cls.get("name", "")).lower(), 0)
                if have < needed:
                    unmet.append(
                        Unmet("chargen.prereq.class_level", cls=str(cls.get("name")), n=needed)
                    )
            elif ctx.level < needed:
                unmet.append(Unmet("chargen.prereq.level", n=needed))
    elif condition == "ability":
        options = value if isinstance(value, list) else [value]
        if not any(
            isinstance(o, Mapping) and all(ctx.abilities.get(a, 0) >= int(n) for a, n in o.items())
            for o in options
        ):
            need = " / ".join(
                " + ".join(f"{a.upper()} {n}" for a, n in o.items())
                for o in options
                if isinstance(o, Mapping)
            )
            unmet.append(Unmet("chargen.prereq.ability", need=need))
    elif condition == "race":
        names = [str(r.get("name", "")) for r in value if isinstance(r, Mapping)]
        if names and not any(n.lower() in ctx.species.lower() for n in names):
            unmet.append(Unmet("chargen.prereq.species", names=", ".join(names)))
    elif condition == "background":
        names = [str(r.get("name", "")) for r in value if isinstance(r, Mapping)]
        if names and ctx.background.lower() not in {n.lower() for n in names}:
            unmet.append(Unmet("chargen.prereq.background", names=", ".join(names)))
    elif condition == "feat":
        names = [_name(f).split(";")[0].strip() for f in value]
        if names and not any(n in ctx.feats for n in names):
            unmet.append(Unmet("chargen.prereq.feat", names=", ".join(names)))
    elif condition == "feature":
        names = [str(f) for f in value]
        if names and not any(n.lower() in ctx.features for n in names):
            unmet.append(Unmet("chargen.prereq.feature", names=", ".join(names)))
    elif condition == "proficiency":
        for entry in value if isinstance(value, list) else [value]:
            if not isinstance(entry, Mapping):
                continue
            armor = entry.get("armor")
            weapon = entry.get("weapon") or entry.get("weaponGroup")
            if armor and str(armor).lower() not in ctx.armor:
                unmet.append(Unmet("chargen.prereq.armor", armor=str(armor)))
            if weapon and str(weapon).lower() not in ctx.weapons:
                unmet.append(Unmet("chargen.prereq.weapon", weapon=str(weapon)))
    elif condition in ("spellcasting", "spellcasting2020", "spellcastingFeature"):
        if value and not ctx.caster:
            unmet.append(Unmet("chargen.prereq.caster"))
    elif condition == "spell":
        # A named spell is checked; a filter ("a Warlock cantrip that deals damage") is
        # confirmed in the corpus's own words.
        filters = [str(s.get("entry") or s.get("choose")) for s in value if isinstance(s, Mapping)]
        confirm.extend(filters)
        names = [_name(s).split("#")[0] for s in value if isinstance(s, str)]
        if names and not any(n in ctx.spells for n in names):
            unmet.append(Unmet("chargen.prereq.spell", names=", ".join(names)))
    elif condition == "exclusiveFeatCategory":
        taken = set(value) & ctx.feat_categories
        if taken:
            unmet.append(Unmet("chargen.prereq.exclusive", category=", ".join(sorted(taken))))
    elif condition in ("other", "otherSummary"):
        text = value.get("entry") if isinstance(value, Mapping) else value
        confirm.append(str(text))
    elif condition == "campaign":
        confirm.append(", ".join(str(c) for c in value))
    elif condition in ("pact", "patron"):
        confirm.append(f"{condition}: {value}")
    else:
        # Unknown upstream condition: neither allowed nor blocked silently.
        confirm.append(f"{condition}: {value}")
    return unmet, confirm


def check(prerequisite: Sequence[Any] | None, ctx: Context) -> Verdict:
    """The verdict on a ``prerequisite`` list for the character in ``ctx``."""
    if not prerequisite:
        return Verdict()
    best: Verdict | None = None
    for alternative in prerequisite:
        if not isinstance(alternative, Mapping):
            continue
        unmet: list[Unmet] = []
        confirm: list[str] = []
        for condition, value in alternative.items():
            if condition == "note":
                continue
            u, c = _check(condition, value, ctx)
            unmet.extend(u)
            confirm.extend(c)
        verdict = Verdict(tuple(unmet), tuple(confirm))
        if verdict.allowed:
            return verdict
        if best is None:
            best = verdict
    return best or Verdict()


__all__ = ["Context", "Unmet", "Verdict", "check"]
