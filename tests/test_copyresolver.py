"""Tests for ``_copy`` inheritance resolution.

Each ``_mod`` mode is covered because a silently mis-applied patch produces a statblock
that looks plausible and is wrong — the worst possible failure for a DM mid-combat.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.store.copyresolver import CopyResolver, entry_key
from app.store.errors import CopyResolutionError


def build(*entries: dict[str, Any]) -> CopyResolver:
    """Build a resolver over an index of the given raw entries."""
    index = {entry_key(e["name"], e["source"]): e for e in entries}
    return CopyResolver(index)


PARENT: dict[str, Any] = {
    "name": "Kobold",
    "source": "MM",
    "page": 195,
    "srd": True,
    "cr": "1/4",
    "str": 7,
    "int": 8,
    "ac": [12],
    "skill": {"stealth": "+6"},
    "trait": [
        {"name": "Pack Tactics", "entries": ["The kobold has advantage."]},
        {"name": "Sunlight Sensitivity", "entries": ["The kobold has disadvantage."]},
    ],
    "action": [{"name": "Dagger", "entries": ["The kobold stabs."]}],
}


class TestBasics:
    def test_entry_without_copy_is_returned_unchanged(self) -> None:
        resolver = build(PARENT)
        assert resolver.resolve(PARENT) is PARENT

    def test_child_inherits_parent_fields(self) -> None:
        child = {"name": "Kid", "source": "X", "_copy": {"name": "Kobold", "source": "MM"}}
        result = build(PARENT, child).resolve(child)
        assert result["cr"] == "1/4"
        assert result["ac"] == [12]
        assert len(result["trait"]) == 2

    def test_child_fields_override_parent(self) -> None:
        child = {
            "name": "Kid",
            "source": "X",
            "cr": "2",
            "_copy": {"name": "Kobold", "source": "MM"},
        }
        assert build(PARENT, child).resolve(child)["cr"] == "2"

    def test_copy_directive_is_stripped_from_result(self) -> None:
        child = {"name": "Kid", "source": "X", "_copy": {"name": "Kobold", "source": "MM"}}
        assert "_copy" not in build(PARENT, child).resolve(child)

    def test_parent_is_not_mutated(self) -> None:
        """Resolution must deep-copy; otherwise one child's patch corrupts the parent."""
        child = {
            "name": "Kid",
            "source": "X",
            "_copy": {
                "name": "Kobold",
                "source": "MM",
                "_mod": {"trait": {"mode": "removeArr", "names": "Pack Tactics"}},
            },
        }
        resolver = build(PARENT, child)
        resolver.resolve(child)
        assert len(PARENT["trait"]) == 2

    def test_provenance_is_not_inherited(self) -> None:
        """A child does not appear on its parent's page or in its SRD listing."""
        child = {"name": "Kid", "source": "X", "_copy": {"name": "Kobold", "source": "MM"}}
        result = build(PARENT, child).resolve(child)
        assert "page" not in result
        assert "srd" not in result

    def test_preserve_keeps_named_provenance(self) -> None:
        child = {
            "name": "Kid",
            "source": "X",
            "_copy": {"name": "Kobold", "source": "MM", "_preserve": {"page": True}},
        }
        assert build(PARENT, child).resolve(child)["page"] == 195

    def test_missing_parent_raises(self) -> None:
        child = {"name": "Kid", "source": "X", "_copy": {"name": "Ghost", "source": "ZZ"}}
        with pytest.raises(CopyResolutionError):
            build(child).resolve(child)

    def test_chained_copies_resolve(self) -> None:
        mid = {"name": "Mid", "source": "X", "cr": "1", "_copy": {"name": "Kobold", "source": "MM"}}
        leaf = {"name": "Leaf", "source": "X", "_copy": {"name": "Mid", "source": "X"}}
        result = build(PARENT, mid, leaf).resolve(leaf)
        assert result["cr"] == "1"
        assert result["ac"] == [12]

    def test_circular_copy_raises_instead_of_recursing(self) -> None:
        a = {"name": "A", "source": "X", "_copy": {"name": "B", "source": "X"}}
        b = {"name": "B", "source": "X", "_copy": {"name": "A", "source": "X"}}
        with pytest.raises(ValueError, match="Circular"):
            build(a, b).resolve(a)


class TestModModes:
    """One test per ``_mod`` mode that occurs in the corpus."""

    def _child(self, mod: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": "Kid",
            "source": "X",
            "_copy": {"name": "Kobold", "source": "MM", "_mod": mod},
        }

    def test_replace_txt_rewrites_nested_strings(self) -> None:
        """802 uses — re-skins a generic statblock as a named NPC."""
        child = self._child(
            {"*": {"mode": "replaceTxt", "replace": "the kobold", "with": "Myla", "flags": "i"}}
        )
        result = build(PARENT, child).resolve(child)
        assert result["trait"][0]["entries"][0] == "Myla has advantage."
        assert result["action"][0]["entries"][0] == "Myla stabs."

    def test_replace_txt_is_case_insensitive_with_flag(self) -> None:
        child = self._child(
            {"*": {"mode": "replaceTxt", "replace": "THE KOBOLD", "with": "Myla", "flags": "i"}}
        )
        assert "Myla" in build(PARENT, child).resolve(child)["trait"][0]["entries"][0]

    def test_append_arr(self) -> None:
        child = self._child({"trait": {"mode": "appendArr", "items": {"name": "New"}}})
        result = build(PARENT, child).resolve(child)
        assert [t["name"] for t in result["trait"]] == [
            "Pack Tactics",
            "Sunlight Sensitivity",
            "New",
        ]

    def test_prepend_arr(self) -> None:
        child = self._child({"trait": {"mode": "prependArr", "items": {"name": "First"}}})
        assert build(PARENT, child).resolve(child)["trait"][0]["name"] == "First"

    def test_insert_arr_at_index(self) -> None:
        child = self._child({"trait": {"mode": "insertArr", "index": 1, "items": {"name": "Mid"}}})
        result = build(PARENT, child).resolve(child)
        assert [t["name"] for t in result["trait"]][1] == "Mid"

    def test_remove_arr_by_name(self) -> None:
        child = self._child({"trait": {"mode": "removeArr", "names": "Pack Tactics"}})
        result = build(PARENT, child).resolve(child)
        assert [t["name"] for t in result["trait"]] == ["Sunlight Sensitivity"]

    def test_replace_arr_swaps_named_element_in_place(self) -> None:
        child = self._child(
            {
                "action": {
                    "mode": "replaceArr",
                    "replace": "Dagger",
                    "items": {"name": "Sword", "entries": ["Slash."]},
                }
            }
        )
        result = build(PARENT, child).resolve(child)
        assert [a["name"] for a in result["action"]] == ["Sword"]

    def test_append_if_not_exists_arr_deduplicates(self) -> None:
        parent = {**PARENT, "languages": ["Common", "Draconic"]}
        child = {
            "name": "Kid",
            "source": "X",
            "_copy": {
                "name": "Kobold",
                "source": "MM",
                "_mod": {
                    "languages": {
                        "mode": "appendIfNotExistsArr",
                        "items": ["Common", "Undercommon"],
                    }
                },
            },
        }
        result = build(parent, child).resolve(child)
        assert result["languages"] == ["Common", "Draconic", "Undercommon"]

    def test_set_prop(self) -> None:
        child = self._child({"cr": {"mode": "setProp", "value": "5"}})
        assert build(PARENT, child).resolve(child)["cr"] == "5"

    def test_add_skills_computes_modifier_from_ability_and_proficiency(self) -> None:
        """A multiplier of 2 is expertise: ability modifier + 2 x proficiency bonus.

        INT 8 gives -1; CR 1/4 gives PB +2; so expertise in Investigation is +3.
        """
        child = self._child({"skill": {"mode": "addSkills", "skills": {"investigation": 2}}})
        result = build(PARENT, child).resolve(child)
        assert result["skill"]["investigation"] == "+3"
        assert result["skill"]["stealth"] == "+6"

    def test_add_skills_scales_proficiency_with_cr(self) -> None:
        """CR 12 gives PB +4; INT 20 gives +5; expertise is therefore 5 + 2x4 = +13.

        Matches the Sylvira Savikas entry in the real corpus.
        """
        parent = {**PARENT, "cr": "12", "int": 20}
        child = {
            "name": "Kid",
            "source": "X",
            "_copy": {
                "name": "Kobold",
                "source": "MM",
                "_mod": {"skill": {"mode": "addSkills", "skills": {"investigation": 2}}},
            },
        }
        assert build(parent, child).resolve(child)["skill"]["investigation"] == "+13"

    def test_add_spells_merges_into_spellcasting_block(self) -> None:
        parent = {**PARENT, "spellcasting": [{"name": "Innate", "will": ["{@spell light}"]}]}
        child = {
            "name": "Kid",
            "source": "X",
            "_copy": {
                "name": "Kobold",
                "source": "MM",
                "_mod": {"spellcasting": {"mode": "addSpells", "will": ["{@spell bless}"]}},
            },
        }
        result = build(parent, child).resolve(child)
        assert result["spellcasting"][0]["will"] == ["{@spell light}", "{@spell bless}"]

    def test_replace_spells_swaps_a_prepared_spell(self) -> None:
        parent = {
            **PARENT,
            "spellcasting": [{"spells": {"7": {"slots": 1, "spells": ["{@spell teleport}"]}}}],
        }
        child = {
            "name": "Kid",
            "source": "X",
            "_copy": {
                "name": "Kobold",
                "source": "MM",
                "_mod": {
                    "spellcasting": {
                        "mode": "replaceSpells",
                        "spells": {
                            "7": [{"replace": "{@spell teleport}", "with": "{@spell plane shift}"}]
                        },
                    }
                },
            },
        }
        result = build(parent, child).resolve(child)
        assert result["spellcasting"][0]["spells"]["7"]["spells"] == ["{@spell plane shift}"]

    def test_remove_spells(self) -> None:
        parent = {
            **PARENT,
            "spellcasting": [
                {"spells": {"3": {"slots": 3, "spells": ["{@spell fly}", "{@spell haste}"]}}}
            ],
        }
        child = {
            "name": "Kid",
            "source": "X",
            "_copy": {
                "name": "Kobold",
                "source": "MM",
                "_mod": {
                    "spellcasting": {"mode": "removeSpells", "spells": {"3": ["{@spell fly}"]}}
                },
            },
        }
        result = build(parent, child).resolve(child)
        assert result["spellcasting"][0]["spells"]["3"]["spells"] == ["{@spell haste}"]

    def test_unknown_mode_is_recorded_not_silently_ignored(self) -> None:
        """Upstream adds modes with new books; a skipped patch must be visible."""
        child = self._child({"trait": {"mode": "someFutureMode", "items": []}})
        resolver = build(PARENT, child)
        resolver.resolve(child)
        assert resolver.unknown_modes == {"someFutureMode"}

    def test_string_remove_directive_drops_the_property(self) -> None:
        child = self._child({"skill": "remove"})
        assert "skill" not in build(PARENT, child).resolve(child)


class TestTemplates:
    def test_template_mod_is_applied(self) -> None:
        template = {
            "name": "Reduced Threat",
            "source": "TftYP",
            "_mod": {"trait": {"mode": "removeArr", "names": "Pack Tactics"}},
        }
        child = {
            "name": "Kid",
            "source": "X",
            "_copy": {
                "name": "Kobold",
                "source": "MM",
                "_templates": [{"name": "Reduced Threat", "source": "TftYP"}],
            },
        }
        result = build(PARENT, template, child).resolve(child)
        assert [t["name"] for t in result["trait"]] == ["Sunlight Sensitivity"]

    def test_unknown_template_is_skipped_not_fatal(self) -> None:
        """Without template.json loaded the base statblock is still correct."""
        child = {
            "name": "Kid",
            "source": "X",
            "_copy": {
                "name": "Kobold",
                "source": "MM",
                "_templates": [{"name": "Absent", "source": "ZZ"}],
            },
        }
        assert build(PARENT, child).resolve(child)["cr"] == "1/4"

    def test_a_missing_template_is_recorded(self) -> None:
        child = {
            "name": "Kid",
            "source": "X",
            "_copy": {
                "name": "Kobold",
                "source": "MM",
                "_templates": [{"name": "Absent", "source": "ZZ"}],
            },
        }
        resolver = build(PARENT, child)
        resolver.resolve(child)
        assert resolver.missing_templates == {"Absent|ZZ"}

    def test_template_apply_root_and_mod(self) -> None:
        """template.json's shape: ``apply._root`` sets fields, ``apply._mod`` patches."""
        template = {
            "name": "Skeleton",
            "source": "DMG",
            "apply": {
                "_root": {"type": "undead", "cr": "9"},
                "_mod": {
                    "_": [{"mode": "addSenses", "senses": {"type": "darkvision", "range": 60}}],
                    "immune": {"mode": "appendIfNotExistsArr", "items": "poison"},
                },
            },
        }
        child = {
            "name": "Bony Kid",
            "source": "X",
            "cr": "1/2",
            "_copy": {
                "name": "Kobold",
                "source": "MM",
                "_templates": [{"name": "Skeleton", "source": "DMG"}],
            },
        }
        resolver = build(PARENT, template, child)
        result = resolver.resolve(child)
        assert result["type"] == "undead"
        assert result["cr"] == "1/2"  # the entry's own field beats the template's root
        assert result["senses"] == ["darkvision 60 ft."]
        assert result["immune"] == ["poison"]
        assert not resolver.unknown_modes and not resolver.missing_templates


class TestTemplateModes:
    """The modes only monster templates use, as 5eTools defines them."""

    def apply(self, target: dict[str, Any], prop: str, mod: dict[str, Any]) -> dict[str, Any]:
        resolver = build(PARENT)
        resolver._apply_mod(target, prop, mod)
        assert not resolver.unknown_modes
        return target

    def test_add_senses_lengthens_or_adds(self) -> None:
        target = {"senses": ["darkvision 60 ft.", "tremorsense 10 ft."]}
        self.apply(
            target,
            "_",
            {
                "mode": "addSenses",
                "senses": [
                    {"type": "darkvision", "range": 120},
                    {"type": "blindsight", "range": 30},
                ],
            },
        )
        assert target["senses"] == ["darkvision 120 ft.", "tremorsense 10 ft.", "blindsight 30 ft."]
        self.apply(
            target, "_", {"mode": "addSenses", "senses": {"type": "darkvision", "range": 60}}
        )
        assert target["senses"][0] == "darkvision 120 ft."

    def test_max_size_caps(self) -> None:
        target = {"size": ["H", "G"]}
        assert self.apply(target, "_", {"mode": "maxSize", "max": "L"})["size"] == ["L"]

    def test_scalar_add_prop_keeps_signed_strings(self) -> None:
        target = {
            "save": {"con": "+6", "wis": "+1"},
            "skill": {"other": [{"x": 1}], "stealth": "+3"},
        }
        self.apply(target, "save", {"mode": "scalarAddProp", "scalar": -2, "prop": "*"})
        self.apply(target, "skill", {"mode": "scalarAddProp", "scalar": -2, "prop": "*"})
        assert target["save"] == {"con": "+4", "wis": "-1"}
        assert target["skill"] == {"other": [{"x": 1}], "stealth": "+1"}

    def test_hp_halved_with_its_formula(self) -> None:
        target = {"hp": {"average": 135, "formula": "18d10 + 36"}}
        self.apply(
            target,
            "hp",
            {"mode": "scalarMultProp", "prop": "average", "scalar": 0.5, "floor": True},
        )
        self.apply(
            target,
            "hp",
            {
                "mode": "prefixSuffixStringProp",
                "prop": "formula",
                "prefix": "floor((",
                "suffix": ") ÷ 2)",
            },
        )
        assert target["hp"] == {"average": 67, "formula": "floor((18d10 + 36) ÷ 2)"}

    def test_hit_and_dc_in_text(self) -> None:
        target = {"action": [{"entries": ["{@atk mw} {@hit 7} to hit, {@dc 15|text} or {@dc 13}"]}]}
        self.apply(target, "action", {"mode": "scalarAddHit", "scalar": -2})
        self.apply(target, "action", {"mode": "scalarAddDc", "scalar": -2})
        assert (
            target["action"][0]["entries"][0]
            == "{@atk mw} {@hit 5} to hit, {@dc 13|text} or {@dc 11}"
        )

    def test_xp_halved_keeping_the_cr(self) -> None:
        target = {"cr": "10"}
        self.apply(target, "_", {"mode": "scalarMultXp", "scalar": 0.5, "floor": True})
        assert target["cr"] == {"cr": "10", "xp": 2950}


class TestProficiencyBonus:
    """The PB-by-CR table, which ``addSkills`` depends on."""

    @pytest.mark.parametrize(
        ("cr", "expected"),
        [
            ("0", 2),
            ("1/8", 2),
            ("1/4", 2),
            ("1/2", 2),
            ("4", 2),
            ("5", 3),
            ("8", 3),
            ("9", 4),
            ("12", 4),
            ("13", 5),
            ("16", 5),
            ("17", 6),
            ("20", 6),
            ("21", 7),
            ("24", 7),
            ("25", 8),
            ("28", 8),
            ("29", 9),
            ("30", 9),
        ],
    )
    def test_matches_official_table(self, cr: str, expected: int) -> None:
        from app.store.copyresolver import _proficiency_bonus

        assert _proficiency_bonus({"cr": cr}) == expected

    def test_reads_cr_from_object_form(self) -> None:
        from app.store.copyresolver import _proficiency_bonus

        assert _proficiency_bonus({"cr": {"cr": "17", "lair": "18"}}) == 6

    def test_defaults_for_missing_or_unparseable_cr(self) -> None:
        from app.store.copyresolver import _proficiency_bonus

        assert _proficiency_bonus({}) == 2
        assert _proficiency_bonus({"cr": "Unknown"}) == 2
