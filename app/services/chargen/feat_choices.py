"""The choices a feat brings beyond abilities, proficiencies and spells (CHARGEN-WIZARD.md §8).

One reader for the wizard's Features step and for level-up, so a feat asks the same
whether it is taken at creation or later:

- **Options from a feature list** — ``optionalfeatureProgression``: Martial Adept's two
  maneuvers, Eldritch Adept's invocation, Fighting Initiate's style, Metamagic Adept's two
  options. Asked as ``opt.feat.<feat>.<list>``, kept with the character's other options in
  ``optional_features``.
- **Weapons** — ``weaponProficiencies`` with a ``choose``: Weapon Master's four simple or
  martial weapons. Asked as ``feat.<feat>.weapons``, kept in the character's ``weapons``.
- **Choices in prose only** — ``data/rules/feat_choices.json``: Elemental Adept's damage
  type, Strike of the Giants' giant. Asked as ``feat.<feat>.choice.<id>``; the answer stays
  in ``choices`` and the sheet names it beside the feat.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.services.chargen.blocks import Pick
from app.services.chargen.catalogue import Catalogue, Offer
from app.services.chargen.prereq import Context, Verdict, check
from app.services.chargen.rules import feat_choices

OPT_PREFIX = "opt.feat."


def _key(feat: Offer) -> str:
    return feat.reference.lower()


def choice_id(feat: Offer | str, choice: str) -> str:
    """Where a prose choice's answer is kept: ``feat.<name|source>.choice.<id>``."""
    reference = feat if isinstance(feat, str) else feat.reference
    return f"feat.{reference.lower()}.choice.{choice}"


def weapons_id(feat: Offer) -> str:
    return f"feat.{_key(feat)}.weapons"


def _count(progression: Mapping[str, Any]) -> int:
    """How many options a feat's list grants: ``{"*": 2}``, or its levels summed."""
    values = [v for v in progression.values() if isinstance(v, int)]
    return max(values) if values else 0


def repeatable(feat: Offer) -> bool:
    """A feat that may be taken more than once: its record says so (``repeatable``), or
    only its text does and the rules file records it (Elemental Adept, 2014)."""
    return bool(feat.data.get("repeatable")) or any(
        row.repeatable for row in feat_choices(feat.reference)
    )


def feat_choice_picks(
    feats: Iterable[Offer],
    catalogue: Catalogue,
    ctx: Context,
    verdicts: dict[str, Verdict],
    *,
    held_options: Iterable[str] = (),
    held_weapons: Iterable[str] = (),
    held_choices: Mapping[str, Sequence[str]] | None = None,
) -> list[Pick]:
    """The questions of these feats, each feat once; options already held are not offered
    again (a Battle Master's maneuvers, a weapon the class already trains). A repeatable
    feat listed twice asks its prose choice twice, another value each time, and never one
    ``held_choices`` already holds (the first Elemental Adept's when taking the second)."""
    had = {h.lower() for h in held_options}
    trained = {w.lower() for w in held_weapons}
    feats = list(feats)
    times = Counter(_key(f) for f in feats)
    picks: list[Pick] = []
    seen: set[str] = set()
    for feat in feats:
        if _key(feat) in seen:
            continue
        seen.add(_key(feat))
        for progression in feat.data.get("optionalfeatureProgression") or []:
            if not isinstance(progression, Mapping):
                continue
            types = tuple(progression.get("featureType") or ())
            count = _count(progression.get("progression") or {})
            name = str(progression.get("name") or "")
            offers = [o for o in catalogue.optional_features(types) if o.name.lower() not in had]
            for offer in offers:
                verdicts.setdefault(offer.reference, check(offer.data.get("prerequisite"), ctx))
            if count and offers:
                picks.append(
                    Pick(
                        f"{OPT_PREFIX}{_key(feat)}.{name}",
                        "optional",
                        feat.name,
                        count=min(count, len(offers)),
                        options=tuple(o.reference for o in offers),
                        label=f"{feat.name} · {name}",
                    )
                )
        for block in feat.data.get("weaponProficiencies") or []:
            choose = block.get("choose") if isinstance(block, Mapping) else None
            if not isinstance(choose, Mapping):
                continue
            offers = [o for o in _weapons(catalogue, str(choose.get("fromFilter") or ""))]
            offers = [o for o in offers if o.name.lower() not in trained]
            if offers:
                picks.append(
                    Pick(
                        weapons_id(feat),
                        "weapon",
                        feat.name,
                        count=min(int(choose.get("count") or 1), len(offers)),
                        options=tuple(o.reference for o in offers),
                        label=feat.name,
                    )
                )
        for row in feat_choices(feat.reference):
            taken = {v.lower() for v in (held_choices or {}).get(choice_id(feat, row.id)) or []}
            options = tuple(o for o in row.options if o.lower() not in taken)
            if options:
                picks.append(
                    Pick(
                        choice_id(feat, row.id),
                        "featureoption",
                        feat.name,
                        count=min(times[_key(feat)], len(options)),
                        options=options,
                        label=f"{feat.name} · {row.label}",
                    )
                )
    return picks


def _weapons(catalogue: Catalogue, filter_text: str) -> list[Offer]:
    """The weapons a ``fromFilter`` allows: simple and martial ones, both by default."""
    text = filter_text.lower()
    wanted = [kind for kind in ("simple", "martial") if f"{kind} weapon" in text]
    if not wanted or "mundane weapon" in text:
        wanted = ["simple", "martial"]
    offers = [o for kind in wanted for o in catalogue.equipment(f"weapon{kind.title()}")]
    return sorted({o.reference: o for o in offers}.values(), key=lambda o: o.name.lower())


def feat_options(picks: Sequence[Pick], choices: Mapping[str, Sequence[str]]) -> list[str]:
    """The options the feats' list questions were answered with."""
    return [
        value
        for pick in picks
        if pick.id.startswith(OPT_PREFIX)
        for value in choices.get(pick.id) or []
    ]


def feat_weapons(picks: Sequence[Pick], choices: Mapping[str, Sequence[str]]) -> list[str]:
    """The weapons the feats' weapon questions were answered with."""
    return [
        value for pick in picks if pick.kind == "weapon" for value in choices.get(pick.id) or []
    ]


def chosen_detail(feat_reference: str, choices: Mapping[str, Sequence[str]]) -> list[str]:
    """What a feat's prose choices were answered with, for the sheet ("Fire")."""
    return [
        value
        for row in feat_choices(feat_reference)
        for value in choices.get(choice_id(feat_reference, row.id)) or []
    ]


__all__ = [
    "OPT_PREFIX",
    "choice_id",
    "chosen_detail",
    "feat_choice_picks",
    "feat_options",
    "feat_weapons",
    "repeatable",
    "weapons_id",
]
