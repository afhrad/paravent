"""The background builder: backgrounds of the table's own, published as homebrew.

A :class:`~app.models.gamestate.BackgroundDesign` is the record; the 5eTools background is
derived from it here, typed as the character wizard reads it (the user's decision,
2026-11-25): skills, tools and languages, fixed or so many of the player's choice
(``{"any": 1}``, ``{"anyArtisansTool": 1}``, ``{"anyStandard": 2}``), starting equipment
with its gold, a feature (2014), and under the 2024 rules the three abilities its
increases fall on (the wizard's +2/+1 or +1/+1/+1) and its origin feat. A 2024
background's equipment is option A, with 50 GP as option B, as the 2024 handbook gives
every background. The summary list at its head is written in the world's language.

Published, every design but Claude's drafts becomes a background in
``library/homebrew/backgrounds/backgrounds-paravent.json`` under the source ``Homebrew``,
and the wizard offers it beside the books' own in a world that plays its edition.
"""

from __future__ import annotations

import builtins
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.gamestate import BackgroundDesign, BackgroundItem, SourceSet
from app.services.creature_builder import entries_text, text_entries
from app.services.i18n import Translatable
from app.store.gamestate import GameStateStore
from app.store.jsonfile import write_json

HOMEBREW_SOURCE = "Homebrew"
BACKGROUND_FILE = Path("backgrounds") / "backgrounds-paravent.json"
ABILITIES = ("str", "dex", "con", "int", "wis", "cha")
OPTION_B_GOLD = 50  # every 2024 background's option B
ANY_TOOLS = {
    "any_artisan": "anyArtisansTool",
    "any_gaming": "anyGamingSet",
    "any_instrument": "anyMusicalInstrument",
}

Lookup = Callable[[str, str, str], dict[str, Any] | None]
Words = Callable[..., str]


class BackgroundError(Translatable):
    """A design the builder refuses."""


class BackgroundNotFound(LookupError):
    """No design with that id."""


@dataclass
class Published:
    count: int
    worlds: list[str] = field(default_factory=list)


def _item_entry(item: BackgroundItem) -> Any:
    if item.special.strip():
        out: dict[str, Any] = {"special": item.special.strip()}
    else:
        out = {"item": item.item.strip().lower()}
    if item.quantity > 1:
        out["quantity"] = item.quantity
    return out


def _plural(words: Words, key: str, n: int) -> str:
    """A counted phrase in the translator's plural form ("two of your choice")."""
    plural = getattr(words, "plural", None)
    return str(plural(key, n, n=n)) if plural is not None else words(key, n=n)


def _summary(design: BackgroundDesign, words: Words) -> dict[str, Any]:
    """The list at the head of a background, as the books print it."""
    t = words
    rows: list[tuple[str, str]] = []
    if design.edition == "one" and design.abilities:
        rows.append(
            (
                t("bgbuilder.line.abilities"),
                ", ".join(t(f"rules.ability.{a}") for a in design.abilities),
            )
        )
    if design.edition == "one" and design.feat.strip():
        rows.append((t("bgbuilder.line.feat"), f"{{@feat {design.feat.strip()}}}"))
    skills = [f"{{@skill {s.title()}}}" for s in design.skills]
    if design.any_skills:
        skills.append(_plural(t, "bgbuilder.any_skills", design.any_skills))
    if skills:
        rows.append((t("bgbuilder.line.skills"), ", ".join(skills)))
    tools = [s for s in design.tools]
    for key, kind in (
        ("any_artisan", "artisan"),
        ("any_gaming", "gaming"),
        ("any_instrument", "instrument"),
    ):
        if getattr(design, key):
            tools.append(_plural(t, f"bgbuilder.any_{kind}", getattr(design, key)))
    if tools:
        rows.append((t("bgbuilder.line.tools"), ", ".join(tools)))
    languages = [s.title() for s in design.languages]
    if design.any_languages:
        languages.append(_plural(t, "bgbuilder.any_languages", design.any_languages))
    if languages:
        rows.append((t("bgbuilder.line.languages"), ", ".join(languages)))
    things = [
        (f"{i.quantity} × " if i.quantity > 1 else "")
        + (i.special.strip() or f"{{@item {i.item.strip()}}}")
        for i in design.equipment
    ]
    if design.gold:
        things.append(t("bgbuilder.gold_n", gp=design.gold))
    if things:
        line = ", ".join(things)
        if design.edition == "one":
            line = t("bgbuilder.choose_ab", a=line, gp=OPTION_B_GOLD)
        rows.append((t("bgbuilder.line.equipment"), line))
    return {
        "type": "list",
        "style": "list-hang-notitle",
        "items": [{"type": "item", "name": f"{name}:", "entry": entry} for name, entry in rows],
    }


def background_of(design: BackgroundDesign, words: Words) -> dict[str, Any]:
    """The design as a 5eTools background, its summary in ``words``' language."""
    out: dict[str, Any] = {"name": design.name, "source": HOMEBREW_SOURCE}
    if design.edition == "one":
        out["edition"] = "one"
        if len(design.abilities) == 3:
            out["ability"] = [
                {"choose": {"weighted": {"from": list(design.abilities), "weights": [2, 1]}}},
                {"choose": {"weighted": {"from": list(design.abilities), "weights": [1, 1, 1]}}},
            ]
        if design.feat.strip():
            out["feats"] = [{design.feat.strip().lower(): True}]
    skills: dict[str, Any] = {s.lower(): True for s in design.skills}
    if design.any_skills:
        skills["any"] = design.any_skills
    if skills:
        out["skillProficiencies"] = [skills]
    tools: dict[str, Any] = {s.lower(): True for s in design.tools}
    for key, name in ANY_TOOLS.items():
        if getattr(design, key):
            tools[name] = getattr(design, key)
    if tools:
        out["toolProficiencies"] = [tools]
    languages: dict[str, Any] = {s.lower(): True for s in design.languages}
    if design.any_languages:
        languages["anyStandard"] = design.any_languages
    if languages:
        out["languageProficiencies"] = [languages]
    items = [_item_entry(i) for i in design.equipment if i.item.strip() or i.special.strip()]
    if design.edition == "one":
        if items or design.gold:
            option_a = [*items, *([{"value": design.gold * 100}] if design.gold else [])]
            out["startingEquipment"] = [{"A": option_a, "B": [{"value": OPTION_B_GOLD * 100}]}]
    elif items or design.gold:
        gold = [{"item": "pouch|phb", "containsValue": design.gold * 100}] if design.gold else []
        out["startingEquipment"] = [{"_": [*items, *gold]}]
    entries: list[Any] = [_summary(design, words)]
    entries += text_entries(design.text)
    if design.feature_name.strip():
        entries.append(
            {
                "type": "entries",
                "name": words("bgbuilder.feature_title", name=design.feature_name.strip()),
                "entries": text_entries(design.feature_text) or [""],
            }
        )
    out["entries"] = entries
    return out


def _true_keys(blocks: Any) -> list[str]:
    first = (blocks or [{}])[0] if isinstance(blocks, list) and blocks else {}
    return [k for k, v in first.items() if v is True] if isinstance(first, dict) else []


def _count(blocks: Any, key: str) -> int:
    first = (blocks or [{}])[0] if isinstance(blocks, list) and blocks else {}
    value = first.get(key) if isinstance(first, dict) else None
    return int(value) if isinstance(value, int) else 0


def from_background(raw: dict[str, Any]) -> dict[str, Any]:
    """A printed background's typed fields and text, as a design's."""
    fields: dict[str, Any] = {"edition": "one" if raw.get("edition") == "one" else "classic"}
    fields["skills"] = _true_keys(raw.get("skillProficiencies"))
    fields["any_skills"] = min(4, _count(raw.get("skillProficiencies"), "any"))
    fields["tools"] = _true_keys(raw.get("toolProficiencies"))
    for key, name in ANY_TOOLS.items():
        fields[key] = min(3, _count(raw.get("toolProficiencies"), name))
    fields["languages"] = _true_keys(raw.get("languageProficiencies"))
    fields["any_languages"] = min(4, _count(raw.get("languageProficiencies"), "anyStandard"))
    equipment: list[BackgroundItem] = []
    gold = 0
    groups = raw.get("startingEquipment") or []
    first = groups[0] if groups and isinstance(groups[0], dict) else {}
    for entry in first.get("_") or first.get("A") or first.get("a") or []:
        if isinstance(entry, str):
            equipment.append(BackgroundItem(item=entry))
        elif isinstance(entry, dict):
            if isinstance(entry.get("value"), int):
                gold += entry["value"] // 100
            if isinstance(entry.get("containsValue"), int):
                gold += entry["containsValue"] // 100
            quantity = int(entry.get("quantity") or 1)
            if entry.get("containsValue") and str(entry.get("item", "")).startswith("pouch"):
                continue  # the pouch the gold comes in, written anew with it
            if entry.get("item"):
                equipment.append(BackgroundItem(item=str(entry["item"]), quantity=quantity))
            elif entry.get("special"):
                equipment.append(BackgroundItem(special=str(entry["special"]), quantity=quantity))
    fields["equipment"], fields["gold"] = equipment[:30], gold
    for block in raw.get("ability") or []:
        choice = (block.get("choose") or {}).get("weighted") if isinstance(block, dict) else None
        if isinstance(choice, dict):
            fields["abilities"] = [a for a in choice.get("from") or [] if a in ABILITIES][:3]
            break
    feats = _true_keys(raw.get("feats"))
    if feats:
        fields["feat"] = feats[0]
    text: list[str] = []
    for entry in raw.get("entries") or []:
        if isinstance(entry, dict) and str(entry.get("name", "")).startswith("Feature:"):
            fields["feature_name"] = str(entry["name"]).removeprefix("Feature:").strip()[:120]
            fields["feature_text"] = entries_text(entry.get("entries") or [])[:4000]
        elif isinstance(entry, dict) and entry.get("type") == "list":
            continue  # the summary, written anew
        else:
            text.append(entries_text([entry]))
    fields["text"] = "\n\n".join(x for x in text if x)[:4000]
    return fields


class BackgroundBuilder:
    """Background designs in the library, and their homebrew backgrounds."""

    def __init__(
        self,
        store: GameStateStore,
        homebrew_dir: Path,
        lookup: Lookup,
        reload: Callable[[], object],
        *,
        words: Callable[[str | None], Words],
        newer: Callable[[str | None], bool] = lambda _w: True,
    ) -> None:
        self._store = store
        self._homebrew = homebrew_dir
        self._lookup = lookup
        self._reload = reload
        self._words = words
        self._newer = newer

    @property
    def homebrew_file(self) -> Path:
        return self._homebrew / BACKGROUND_FILE

    def list(self, world_id: str | None = None) -> builtins.list[BackgroundDesign]:
        found = self._store.background_designs.list(
            None if world_id is None else (lambda d: d.world_id == world_id)
        )
        return sorted(found, key=lambda d: d.name.casefold())

    def get(self, design_id: str) -> BackgroundDesign:
        found = self._store.background_designs.find(design_id)
        if found is None:
            raise BackgroundNotFound(design_id)
        return found

    def named(self, name: str) -> BackgroundDesign | None:
        wanted = name.strip().casefold()
        return next((d for d in self.list() if d.name.casefold() == wanted), None)

    def _clean_name(self, name: str, own_id: str | None = None) -> str:
        cleaned = " ".join(name.split())[:80]
        if not cleaned:
            raise BackgroundError("bgbuilder.error.name")
        clash = next(
            (
                d
                for d in self._store.background_designs.list()
                if d.name.casefold() == cleaned.casefold() and d.id != own_id
            ),
            None,
        )
        if clash is not None:
            raise BackgroundError("bgbuilder.error.name_taken", name=cleaned)
        return cleaned

    def create(
        self,
        name: str,
        world_id: str | None,
        based_on: str = "",
        *,
        drafted_by: str | None = None,
    ) -> BackgroundDesign:
        """A new design: from a printed background, or blank in the world's edition."""
        fields: dict[str, Any] = {"edition": "one" if self._newer(world_id) else "classic"}
        ref = based_on.strip()
        if ref:
            bg_name, _, source = ref.partition("|")
            raw = self._lookup("background", bg_name.strip(), source.strip())
            if raw is None:
                raise BackgroundError("bgbuilder.error.based_on", ref=ref)
            fields = from_background(raw)
            ref = f"{raw.get('name')}|{raw.get('source')}"
            name = name.strip() or f"{raw.get('name')} (variant)"
        design = BackgroundDesign(
            name=self._clean_name(name),
            world_id=world_id or None,
            based_on=ref or None,
            drafted_by=drafted_by,
            **fields,
        )
        return self._store.background_designs.create(design)

    def save(self, design: BackgroundDesign) -> BackgroundDesign:
        """Store a changed design (not yet published)."""
        design.name = self._clean_name(design.name, design.id)
        design.skills = [s.strip().lower() for s in dict.fromkeys(design.skills) if s.strip()][:6]
        design.tools = [s.strip().lower() for s in dict.fromkeys(design.tools) if s.strip()][:6]
        design.languages = [
            s.strip().lower() for s in dict.fromkeys(design.languages) if s.strip()
        ][:6]
        design.abilities = [a for a in dict.fromkeys(design.abilities) if a in ABILITIES]
        if design.edition == "one" and design.abilities and len(design.abilities) != 3:
            raise BackgroundError("bgbuilder.error.abilities")
        design.equipment = [i for i in design.equipment if i.item.strip() or i.special.strip()][:30]
        design.touch()
        return self._store.background_designs.replace(design)

    def delete(self, design_id: str) -> BackgroundDesign:
        design = self.get(design_id)
        self._store.background_designs.delete(design_id)
        if design.published_at is not None:
            self.publish()
        return design

    def background(self, design: BackgroundDesign) -> dict[str, Any]:
        """The design as the 5eTools background it publishes as."""
        return background_of(design, self._words(design.world_id))

    def _hash(self, design: BackgroundDesign) -> str:
        text = json.dumps(self.background(design), sort_keys=True)
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def unpublished(self, design: BackgroundDesign) -> bool:
        return design.published_hash != self._hash(design)

    def accept(self, design_id: str) -> BackgroundDesign:
        """Claude's draft taken in as the gamemaster's own, then published."""
        design = self.get(design_id)
        self._store.background_designs.update(design.id, drafted_by=None)
        self.publish()
        return self.get(design.id)

    def publish(self) -> Published:
        """Write every design but Claude's drafts to the homebrew file and reload."""
        designs = [d for d in self.list() if d.drafted_by is None]
        now = datetime.now(UTC)
        self.homebrew_file.parent.mkdir(parents=True, exist_ok=True)
        write_json(
            self.homebrew_file,
            {
                "_meta": {
                    "sources": [
                        {
                            "json": HOMEBREW_SOURCE,
                            "abbreviation": HOMEBREW_SOURCE,
                            "full": "Homebrew (Paravent)",
                        }
                    ],
                    "note": "Written by Paravent from library/background_designs.json; "
                    "edits here are replaced on the next publish.",
                },
                "background": [self.background(d) for d in designs],
            },
        )
        for design in designs:
            self._store.background_designs.update(
                design.id, published_at=now, published_hash=self._hash(design)
            )
        worlds: builtins.list[str] = []
        for world_id in {d.world_id for d in designs if d.world_id}:
            world = self._store.worlds.find(world_id)
            if world is None or world.source_set.allows(HOMEBREW_SOURCE):
                continue
            sources = [*world.source_set.sources, HOMEBREW_SOURCE]
            self._store.worlds.update(world.id, source_set=SourceSet(sources=sources))
            worlds.append(world.name)
        self._reload()
        return Published(len(designs), sorted(worlds))


__all__ = [
    "ABILITIES",
    "BackgroundBuilder",
    "BackgroundError",
    "BackgroundNotFound",
    "background_of",
    "from_background",
]
