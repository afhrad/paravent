"""What anyone sees of a creature by looking (documentation/COMBAT.md §5)."""

from __future__ import annotations

from pathlib import Path

from app.models.compendium import Fluff
from app.services.combat import SeenCombatant, SeenDetails
from app.services.creature_sight import Sight, image_file, sight
from app.services.i18n import UiCatalogue
from app.services.units import UnitFormat
from app.ui.context import Page
from app.ui.play_combat_pages import member_details
from app.ui.tags import RenderContext

ITEMS = {"leather armor": "Leather Armor", "shield": "Shield", "scimitar": "Scimitar"}


def item_name(name: str) -> str | None:
    return ITEMS.get(name.lower())


GOBLIN_2014 = {
    "size": ["S"],
    "type": {"type": "humanoid", "tags": ["goblinoid"]},
    "ac": [{"ac": 15, "from": ["{@item leather armor|phb}", "{@item shield|phb}"]}],
    "hp": {"average": 7, "formula": "2d6"},
    "speed": {"walk": 30},
    "action": [
        {"name": "Scimitar", "entries": ["{@atk mw} {@hit 4} to hit, reach 5 ft."]},
        {"name": "Bite", "entries": ["{@atk mw} {@hit 4} to hit, reach 5 ft."]},
        {"name": "Shield", "entries": ["Not an attack, though named like an item."]},
    ],
}


def fluff(**extra: object) -> Fluff:
    return Fluff.model_validate(
        {
            "name": "Goblin",
            "source": "MM",
            "entries": [
                {"type": "entries", "entries": ["Goblins are {@b small} and spiteful.", "More."]}
            ],
            **extra,
        }
    )


class TestSight:
    def test_a_2014_statblock_shows_its_armour_and_weapons_not_its_numbers(self) -> None:
        seen = sight(GOBLIN_2014, fluff(), item_name, None)
        assert seen.sizes == ("S",) and seen.kind == GOBLIN_2014["type"]
        assert dict(seen.speed) == {"walk": 30}
        # armour from the AC, weapons from attacks named after an item; a Bite is no gear
        assert seen.gear == ("Leather Armor", "Shield", "Scimitar")
        # tags kept, for the renderer to draw as links
        assert seen.description == "Goblins are {@b small} and spiteful."
        assert seen.image is None
        assert "15" not in repr(seen) and "2d6" not in repr(seen)  # no AC, no hit points

    def test_a_2024_statblock_lists_its_gear(self) -> None:
        data = {
            "size": ["S"],
            "speed": {"walk": 30},
            "gear": ["leather armor|xphb", {"item": "scimitar|xphb", "quantity": 1}, "rope|xphb"],
            "action": [{"name": "Scimitar", "entries": ["{@atkr m} {@hit 4}, reach 5 ft."]}],
        }
        assert sight(data, None, item_name, None).gear == ("Leather Armor", "Scimitar")

    def test_a_picture_shows_only_when_its_file_is_there(self, tmp_path: Path) -> None:
        pictures = {
            "images": [
                {"type": "image", "href": {"type": "internal", "path": "bestiary/MM/Goblin.webp"}}
            ]
        }
        assert sight(GOBLIN_2014, fluff(**pictures), item_name, tmp_path).image is None
        (tmp_path / "bestiary" / "MM").mkdir(parents=True)
        (tmp_path / "bestiary" / "MM" / "Goblin.webp").write_bytes(b"RIFF")
        seen = sight(GOBLIN_2014, fluff(**pictures), item_name, tmp_path)
        assert seen.image == "bestiary/MM/Goblin.webp"

    def test_an_image_path_never_leaves_its_folder(self, tmp_path: Path) -> None:
        (tmp_path / "secret.txt").write_text("no")
        images = tmp_path / "img"
        images.mkdir()
        assert image_file(images, "../secret.txt") is None
        assert image_file(images, "missing.webp") is None


class TestDetails:
    """The players' Details draw the books' description through the tag renderer: its
    references links, its units hinted for a reader in metres."""

    def _shown(self, ctx: RenderContext | None) -> str:
        member = SeenCombatant("Shrub", "unhurt", (), None)
        description = "Woken by the {@spell awaken} spell, it moves 20 feet a round."
        details = SeenDetails(member, Sight(description=description))
        return str(member_details(Page(UiCatalogue().translator("de")), details, ctx=ctx))

    def test_with_the_renderer(self) -> None:
        shown = self._shown(RenderContext(units=UnitFormat("metres", "de")))
        assert "{@" not in shown and "awaken" in shown
        assert '20 feet<span class="unit-hint"> (6 m)</span> a round' in shown

    def test_plain_without_it(self) -> None:
        shown = self._shown(None)
        assert "Woken by the awaken spell, it moves 20 feet a round." in shown
