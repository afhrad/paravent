"""The demo tables the videos are recorded on (website/README.md, "Videos").

Each episode gets a throwaway library in a temporary folder, as the screenshots do
(``shots.py``): the world of the website's pictures in the take's language, the three
example characters, and whatever the episode starts from. Only creatures of the System
Reference Document appear, and every word of the world is our own: the videos are
public. The user's library is read for the glossary's names and the German text of the
SRD creatures shown, as the screenshots read it, and never written.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

FILM = Path(__file__).resolve().parent
SITE = FILM.parent
ROOT = SITE.parent
sys.path.insert(0, str(SITE))
sys.path.insert(0, str(ROOT))

import shots  # noqa: E402 - the screenshot script's demo table, shared
from texts import ADVENTURE  # noqa: E402

GM, PLAYER, PLAYER2, PASSWORD = shots.GM, shots.PLAYER, "jonas", shots.PASSWORD
# The fight's creatures: the System Reference Document 5.1's goblins and bugbear.
FOES = (("Goblin|MM", 3), ("Bugbear|MM", 1))


@dataclass
class Table:
    """The ids a script needs."""

    lang: str
    world: str = ""
    adventure: str = ""
    chapter: str = ""
    encounter: str = ""
    combat: str = ""
    lyra: str = ""
    brunhild: str = ""
    varis: str = ""
    extra: dict[str, str] = field(default_factory=dict)


def _registry(library: Path, scratch: Path, users: Path) -> Any:
    import os

    from app.config import Settings
    from app.store.registry import StoreRegistry

    os.environ.update(shots.overrides(library, scratch))
    registry = StoreRegistry(Settings(_env_file=ROOT / ".env"))  # type: ignore[call-arg]
    registry.load()
    shots.borrow_translations(users, registry.settings.i18n_dir / "de")
    return registry


def table(
    library: Path, scratch: Path, lang: str, users: Path, *, with_fight: bool = True
) -> tuple[Table, Any]:
    """The demo world: its users, characters, the ferry chapter and its ambush."""
    from app.models.gamestate import Role, Scope
    from app.models.pc import PlayerCharacter
    from app.services.worlds import WorldInput

    text = shots.TEXTS[lang]
    registry = _registry(library, scratch, users)
    auth = registry.auth
    auth.create_user(GM, PASSWORD, Role.GAMEMASTER, display_name=text["gm"])
    auth.create_user(PLAYER, PASSWORD, Role.PLAYER, display_name="Mara")
    auth.create_user(PLAYER2, PASSWORD, Role.PLAYER, display_name="Jonas")
    registry.themes.set_table("tome")
    world = registry.worlds.create(
        WorldInput(
            name=text["world"],
            description=text["world_text"],
            sources=("PHB", "MM", "DMG"),
            language=lang,
        )
    )
    found = Table(lang=lang, world=world.id)
    for name, owner, slot in (
        ("lyra-mooncaller", PLAYER, "lyra"),
        ("brunhild-ironvow", PLAYER2, "brunhild"),
        ("varis-thornwood", GM, "varis"),
    ):
        data: dict[str, Any] = json.loads(
            (ROOT / f"documentation/examples/{name}.json").read_text(encoding="utf-8")
        )
        data.pop("$schema", None)
        data.update(world_id=world.id, player=owner)
        setattr(
            found,
            slot,
            registry.gamestate.characters.create(PlayerCharacter.model_validate(data)).id,
        )

    # A few days' food each, so a day on the road is eaten, not gone hungry.
    from app.services.play import Actor

    for character_id in (found.lyra, found.brunhild, found.varis):
        registry.characters.receive(
            character_id, [("Rations (1 day)|PHB", 5)], {}, Actor(text["gm"], username=GM)
        )
    adventures = registry.adventures
    adventure = adventures.create(world.id, text["adventure"])
    chapter = adventures.create_chapter(adventure.id, text["chapter"])
    found.adventure, found.chapter = adventure.id, chapter.id
    if with_fight:
        here = Scope(world_id=world.id, adventure_id=adventure.id, chapter_id=chapter.id)
        encounter = registry.encounters.create(text["encounter"], world.id, scope=here)
        for ref, count in FOES:
            registry.encounters.add_monster(encounter.id, ref, count)
        registry.encounters.set_label(encounter.id, 0, ", ".join(text["goblins"]))
        registry.encounters.set_label(encounter.id, 1, text["boss"])
        found.encounter = encounter.id
        # The pictures' chapter, without the troll this smaller ambush leaves out.
        body = re.sub(
            r" (Under the landing|Unter dem Anleger)[^.]*\.[^.]*\.", "", shots.unwrap(text["body"])
        )
        adventures.update_chapter(chapter.id, title=text["chapter"], status="running", body=body)
    return found, registry


def fight(library: Path, scratch: Path, lang: str, users: Path) -> Table:
    """The table with the ambush run: the fight waits for initiative, Brunhild's and
    Varis's rolled at the table, Lyra's to come from her phone."""
    found, registry = table(library, scratch, lang, users)
    combat = registry.combats.start(registry.gamestate.encounters.get(found.encounter), GM)
    for member in combat.combatants:
        if member.character_id == found.brunhild:
            registry.combats.set_initiative(combat.id, member.id, 9, GM)
        elif member.character_id == found.varis:
            registry.combats.set_initiative(combat.id, member.id, 21, GM)
    found.combat = combat.id
    return found


def session(library: Path, scratch: Path, lang: str, users: Path) -> Table:
    """The table for the demo session: the adventure written for the videos (its two
    chapters, the ferryman, the goblins in the reeds, the toll box), nothing played yet."""
    from app.models.gamestate import Coins, Scope

    found, registry = table(library, scratch, lang, users, with_fight=False)
    words: dict[str, Any] = ADVENTURE[lang]  # type: ignore[assignment]
    adventures = registry.adventures
    adventure = adventures.create(found.world, words["title"])
    found.adventure = adventure.id
    chapters = [adventures.create_chapter(adventure.id, c["title"]).id for c in words["chapters"]]
    first = Scope(world_id=found.world, adventure_id=adventure.id, chapter_id=chapters[0])
    encounter = registry.encounters.create(words["encounter"], found.world, scope=first)
    registry.encounters.add_monster(encounter.id, "Goblin|MM", 3)
    registry.encounters.add_monster(encounter.id, "Wolf|MM", 1)
    registry.encounters.set_label(encounter.id, 0, ", ".join(words["goblins"]))
    registry.encounters.set_label(encounter.id, 1, words["wolf"])
    found.encounter = encounter.id
    npc = registry.npcs.create(words["npc"], f"c:{chapters[0]}", found.world)
    registry.npcs.update(
        npc.id,
        name=npc.name,
        scope_value=f"c:{chapters[0]}",
        roles=[],
        statblock="Commoner|MM",
        notes=words["npc_notes"],
    )
    chest = registry.treasure.create(words["treasure"], f"c:{chapters[0]}", found.world)
    registry.treasure.add_item(chest.id, "Potion of Healing|DMG")
    registry.treasure.update(
        chest.id,
        name=chest.name,
        scope_value=f"c:{chapters[0]}",
        coins=Coins(gp=60, sp=40),
        notes="",
    )
    for n, (chapter_id, chapter) in enumerate(zip(chapters, words["chapters"], strict=True)):
        adventures.update_chapter(
            chapter_id,
            title=chapter["title"],
            status="running" if n == 0 else "planned",
            body=shots.unwrap(chapter["body"]),
        )
    found.chapter = chapters[0]
    found.extra.update(npc=npc.id, treasure=chest.id, chapter2=chapters[1])
    return found


__all__ = ["GM", "PASSWORD", "PLAYER", "PLAYER2", "Table", "fight", "session", "table"]
