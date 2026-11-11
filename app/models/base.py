"""Shared base classes and primitives for the 5eTools data model.

Two rules govern everything in this package:

1. **Lossless.** The 5eTools corpus is wide and sparse — monsters carry 93 distinct
   fields, items 104, most of them present on well under a third of entries. We type
   the fields we consume and keep the rest verbatim in ``extra``, so an import round
   trip never destroys data we have not modelled yet.
2. **Permissive on read, strict on our own writes.** Hand-edited homebrew is validated
   against the same models, but a malformed upstream entry must not take down the load
   of the other 4,457 monsters.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 5eTools abbreviates sizes; expanded for display.
SIZE_NAMES: dict[str, str] = {
    "T": "Tiny",
    "S": "Small",
    "M": "Medium",
    "L": "Large",
    "H": "Huge",
    "G": "Gargantuan",
    "V": "Varies",
}

# Alignment abbreviations as they appear in the corpus.
ALIGNMENT_NAMES: dict[str, str] = {
    "L": "lawful",
    "N": "neutral",
    "C": "chaotic",
    "G": "good",
    "E": "evil",
    "U": "unaligned",
    "A": "any alignment",
    "NX": "neutral (law/chaos axis)",
    "NY": "neutral (good/evil axis)",
}

ABILITIES: tuple[str, ...] = ("str", "dex", "con", "int", "wis", "cha")

SCHOOL_NAMES: dict[str, str] = {
    "A": "Abjuration",
    "C": "Conjuration",
    "D": "Divination",
    "E": "Enchantment",
    "V": "Evocation",
    "I": "Illusion",
    "N": "Necromancy",
    "T": "Transmutation",
    "P": "Psionic",
}


class FiveToolsModel(BaseModel):
    """Base for every model mapped from 5eTools JSON.

    Unknown keys are preserved rather than rejected: the upstream schema grows with
    every published book, and dropping a field we have not seen yet would silently
    corrupt a re-export.
    """

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        str_strip_whitespace=True,
        # Serialise back to the upstream JSON key names. Several fields are aliased
        # because their 5eTools names collide with Python builtins (``str`` and ``int``
        # are ability scores, ``property`` is a weapon property list). Without this a
        # dump emits ``str_``/``int_``/``properties``, which no 5eTools consumer — our
        # own renderer included — knows how to read.
        serialize_by_alias=True,
        # Corpus entries are treated as immutable once loaded; the store hands out
        # shared references and mutation would leak across requests.
        frozen=False,
        validate_assignment=False,
    )

    @model_validator(mode="before")
    @classmethod
    def _drop_explicit_nulls(cls, data: Any) -> Any:
        """Strip keys whose value is an explicit ``null``.

        5eTools uses ``null`` as a *deletion* marker: an entry that inherits via
        ``_copy`` writes ``"trait": null`` to remove the parent's traits. Around 46
        bestiary entries do this on ``action``, ``trait``, ``spellcasting``,
        ``languages`` and friends. Passing the ``None`` through would fail every
        ``list[...]`` annotation, so the key is removed and the field falls back to
        its declared default.

        This is safe because no field in this package distinguishes "absent" from
        "explicitly null" — both mean *not present*.
        """
        if not isinstance(data, dict):
            return data
        return {k: v for k, v in data.items() if v is not None}

    def extra_fields(self) -> dict[str, Any]:
        """Return the upstream fields that this model does not explicitly declare."""
        return dict(self.__pydantic_extra__ or {})


class Entry(FiveToolsModel):
    """A structured text node.

    5eTools text is a recursive tree: an entry is either a bare string or an object
    with a ``type`` discriminator (``entries``, ``list``, ``table``, ``inset`` …) whose
    children are themselves entries. We keep it untyped-but-preserved and let the
    renderer walk it, because typing all ~40 node types buys nothing until each has a
    renderer.
    """

    type: str = "entries"
    name: str | None = None
    entries: list[Any] = Field(default_factory=list)


class SourceRef(FiveToolsModel):
    """A pointer to a book/page where content was reprinted."""

    source: str
    page: int | None = None


class Copy(FiveToolsModel):
    """The ``_copy`` directive: inherit another entry, then patch it.

    Used by 1,141 monsters (25% of the bestiary) and 53 items. An entry with ``_copy``
    is a near-empty shell until resolved — the loader MUST resolve it before the entry
    is usable, or a quarter of the bestiary renders as blank statblocks.
    """

    name: str
    source: str
    mod: dict[str, Any] | None = Field(default=None, alias="_mod")
    templates: list[dict[str, Any]] | None = Field(default=None, alias="_templates")
    preserve: dict[str, Any] | None = Field(default=None, alias="_preserve")


class Copyable(FiveToolsModel):
    """Mixin for entry types that support ``_copy`` inheritance.

    The attribute is ``copy_from`` rather than ``copy`` because ``copy`` is a
    :class:`~pydantic.BaseModel` method; the JSON key ``_copy`` is preserved as an alias.
    """

    copy_from: Copy | None = Field(default=None, alias="_copy")

    @property
    def is_unresolved_copy(self) -> bool:
        """True while this entry still needs its ``_copy`` parent merged in."""
        return self.copy_from is not None


class Named(FiveToolsModel):
    """Every compendium entry is identified by (name, source)."""

    name: str
    source: str
    page: int | None = None

    @property
    def key(self) -> str:
        """Stable cross-reference key, e.g. ``goblin|mm``.

        Lowercased because 5eTools tag references are case-insensitive:
        ``{@creature Goblin|MM}`` and ``{@creature goblin|mm}`` are the same entry.
        """
        return f"{self.name.lower()}|{self.source.lower()}"

    @property
    def ref_name(self) -> str:
        """What a tag names the entry by — its name, except where the corpus addresses
        it by a code (an item property: ``{@itemProperty V|XPHB}``)."""
        return self.name


class Meta(FiveToolsModel):
    """The ``_meta`` block that heads most corpus files."""

    sources: list[dict[str, Any]] = Field(default_factory=list)
    dateAdded: int | None = None
    dateLastModified: int | None = None


LoadMode = Literal["strict", "lenient"]
