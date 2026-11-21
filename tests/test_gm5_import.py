"""A Game Master 5e backup made into a new world (app/services/gm5_backup.py,
app/services/gm5_import.py). The backup here is invented, in the app's own format."""

# ruff: noqa: E501 — the backup's XML keeps its lines as the app writes them

from __future__ import annotations

import base64
import io
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.models.gamestate import Role
from app.services.gm5_backup import BackupError, read_backup
from app.services.gm5_import import Gm5Import, Gm5Uploads, tag_text
from app.store.registry import StoreRegistry

PASSWORD = "correct horse"


def _jpeg() -> str:
    out = io.BytesIO()
    Image.new("RGB", (4, 4), (120, 90, 40)).save(out, "JPEG")
    return base64.b64encode(out.getvalue()).decode()


SKELETON = """<monster><enemy>1</enemy><label>Bones {n}</label><name>Skeleton</name>
<type>undead, monster manual</type><alignment>lawful evil</alignment><ac>13</ac>
<abilities>10,14,15,6,8,5</abilities><hpMax>13</hpMax><hd>2d8+4</hd><speed>30 ft.</speed>
<cr>-1</cr><action><name>Shortsword</name><text>x</text></action></monster>"""

BACKUP = f"""<?xml version="1.0" encoding="UTF-8"?>
<data><version>33</version><uniqueID>99</uniqueID>
<campaign><imageData><uid>7</uid></imageData><name>Grey Reed</name>
<pc><uid>1</uid><label>Wren</label><name>Human Cleric</name></pc>
<npc><uid>5</uid><label>Old Marla</label><name>Ferrywoman (Human)</name>
<abilities>9,10,12,13,15,11</abilities><hpMax>9</hpMax><hd>2d8</hd>
<trait><name>Riverwise</name><text>Knows every ford.</text></trait></npc>
<note><name>Background</name><text>The reeds whisper.
Nobody sleeps.</text></note>
<adventure><name>The Drowned Bell</name>
<note><name>The Ford</name><text>Cold water.</text><imageData><uid>7</uid></imageData></note>
<encounter><name>Ambush at the ford</name><state>0</state><current>0</current><round>0</round>
<combatant>{SKELETON.format(n=1)}</combatant>
<combatant>{SKELETON.format(n=2)}</combatant>
<combatant><monster><label>Klapperbein</label><name>Klapperbein</name><type>Untoter</type>
<abilities>10,14,15,6,8,5</abilities><hpMax>13</hpMax><hd>2d8+4</hd><enemy>1</enemy>
<action><name>Hieb</name><text>x</text></action></monster></combatant>
<combatant><monster><label>Bog Walker 1</label><name>Bog Walker (Zombie)</name>
<type>Untoter</type><alignment>neutral böse</alignment><ac>9</ac>
<abilities>13,6,16,3,6,5</abilities><hpMax>22</hpMax><hd>3d8+9</hd>
<speed>6 m, Schwimmen 6 m</speed><immune>Gift</immune><cr>-1</cr><enemy>1</enemy>
<action><name>Hieb</name><text>Nahkampf-Waffenangriff: +3 zum Treffen, 1,5 m. Treffer: 4 (1W6+1) Wuchtschaden.</text></action>
</monster></combatant>
<combatant><monster><label>Reed Lurker</label><name>Reed Lurker</name><size>3</size>
<type>monstrosity</type><alignment>unaligned</alignment><ac>14</ac>
<abilities>16,12,14,2,10,4</abilities><hpMax>30</hpMax><hd>4d10+8</hd><speed>20 ft., swim 40 ft.</speed>
<skill><id>16</id><modifier>3</modifier></skill><savingThrow><ability>2</ability></savingThrow>
<cr>2</cr><enemy>1</enemy><action><name>Bite</name><text>Melee Weapon Attack: +5 to hit, reach 5 ft., one target. Hit: 7 (1d8 + 3) piercing damage.</text></action>
</monster></combatant>
<combatant><monster><label>Mist</label><name>Mist</name><enemy>1</enemy></monster></combatant>
<combatant><monster><uid>1</uid></monster></combatant>
<combatant><monster><uid>5</uid></monster></combatant>
<item><source>1</source><name>Potion of Healing</name><type>12</type><quantity>2</quantity></item>
<item><source>1</source><name>Gold (gp)</name><type>15</type><quantity>40</quantity></item>
<item><name>Marla's Bell Clapper</name><detail>uncommon</detail><text>Rings under water.</text></item>
<note><name>Tactics</name><text>They rise from the mud.</text></note>
</encounter>
</adventure>
</campaign>
<campaign><name>Elsewhere</name></campaign>
<monster><name>Goblin</name></monster>
<imageData><uid>7</uid><encoded>{_jpeg()}</encoded></imageData>
</data>""".encode()


# -- reading ---------------------------------------------------------------------------


def test_the_backup_reads_with_the_apps_numbers_decoded() -> None:
    backup = read_backup(BACKUP)
    assert [c.name for c in backup.campaigns] == ["Grey Reed", "Elsewhere"]
    grey = backup.campaigns[0]
    assert grey.characters == 1 and grey.image == "7" and backup.images["7"][:2] == b"\xff\xd8"
    fight = grey.adventures[0].encounters[0]
    skeleton = fight.combatants[0].statblock
    assert skeleton is not None and skeleton.cr == "1/4" and skeleton.label == "Bones 1"
    lurker = fight.combatants[4].statblock
    assert lurker is not None
    assert (lurker.size, lurker.cr, lurker.skills, lurker.saves) == ("L", "2", ["stealth"], ["con"])
    assert fight.combatants[6].statblock is None and fight.combatants[6].uid == "1"
    assert backup.npc("5") is not None and backup.npc("1") is None  # a character, not an NPC
    assert grey.adventures[0].notes[0].image == "7"


def test_a_file_that_is_no_backup_is_refused() -> None:
    for data in (b"not xml", b"<other/>", b'<!DOCTYPE data [<!ENTITY x "y">]><data/>'):
        with pytest.raises(BackupError):
            read_backup(data)


def test_attack_lines_become_tags() -> None:
    assert tag_text("Melee Weapon Attack: +5 to hit, reach 5 ft. Hit: 7 (1d8 + 3) piercing.") == (
        "{@atk mw} {@hit 5} to hit, reach 5 ft. {@h} 7 ({@damage 1d8 + 3}) piercing."
    )
    assert tag_text("Nahkampf-Waffenangriff: +3 zum Treffen. Treffer: 4 (1W6+1), SG 12.") == (
        "{@atk mw} {@hit 3} zum Treffen. {@h} 4 ({@damage 1d6+1}), {@dc 12}."
    )
    assert tag_text("Already {@hit 3} tagged") == "Already {@hit 3} tagged"


def test_uploads_wait_by_token_and_go(tmp_path: Path) -> None:
    uploads = Gm5Uploads(tmp_path / ".imports")
    token = uploads.save(BACKUP)
    assert uploads.load(token).campaigns[0].name == "Grey Reed"
    uploads.delete(token)
    with pytest.raises(Exception, match="gm5.error.gone"):
        uploads.load(token)
    with pytest.raises(Exception, match="gm5.error.gone"):
        uploads.load("../../etc")


# -- against the books -----------------------------------------------------------------


@pytest.fixture
def registry(tmp_path: Path, corpus: Path) -> Iterator[StoreRegistry]:
    built = StoreRegistry(
        Settings(reference_dir=corpus, library_dir=tmp_path / "lib", watch_files=False)
    )
    built.load()
    built.glossary.set("de", "monster", "Skeleton", "Klapperbein")
    yield built


def test_the_plan_says_what_each_creature_and_item_becomes(registry: StoreRegistry) -> None:
    plan = Gm5Import(registry).plan(read_backup(BACKUP), [0], ["XPHB", "XMM", "XDMG"], "de")
    found = {c.name: (c.status, c.ref.partition("|")[0]) for c in plan.creatures}
    assert found["Skeleton"] == ("book", "Skeleton")
    assert found["Klapperbein"] == ("book", "Skeleton")  # by the table's own name for it
    assert found["Bog Walker (Zombie)"] == ("variant", "Zombie")
    assert found["Reed Lurker"] == ("new", "")
    assert found["Mist"] == ("skipped", "")
    assert found["Ferrywoman (Human)"][0] == "new"
    items = {i.name: (i.ref.partition("|")[0], i.coins) for i in plan.items}
    assert items["Potion of Healing"] == ("Potion of Healing", False)
    assert items["Gold (gp)"] == ("", True)
    assert items["Marla's Bell Clapper"] == ("", False)
    assert (plan.adventures, plan.chapters, plan.encounters, plan.npcs, plan.pictures) == (
        1, 3, 1, 1, 1,
    )  # fmt: skip


@pytest.fixture
async def gm(registry: StoreRegistry) -> AsyncIterator[AsyncClient]:
    registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER, language="de")
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/login", data={"username": "dm", "password": PASSWORD})
        yield client


async def test_a_campaign_becomes_a_new_world(gm: AsyncClient, registry: StoreRegistry) -> None:
    sent = await gm.post("/gm/import/gm5", files={"file": ("my.backup", BACKUP)})
    choose = sent.headers["location"]
    page = (await gm.get(choose)).text
    assert "Grey Reed" in page and "Elsewhere" in page
    # Every campaign is ticked to start.
    assert page.count('type="checkbox" name="campaigns"') == 2
    assert 'name="campaigns" value="0" checked' in page
    assert 'name="campaigns" value="1" checked' in page
    nothing = (await gm.get(choose, params=[("campaigns", "x")])).text
    assert "Hake mindestens eine Kampagne an." in nothing
    books = [("campaigns", "0"), ("sources", "XPHB"), ("sources", "XMM"), ("sources", "XDMG")]
    checked = (await gm.get(choose, params=books)).text
    assert "Reed Lurker" in checked and "Bog Walker (Zombie)" in checked
    done = await gm.post(
        choose, data={"campaigns": ["0"], "name": "", "sources": ["XPHB", "XMM", "XDMG"]}
    )
    assert done.headers["location"].startswith("/gm/adventures?transferred=gm5%3A1%3A3%3A1%3A1%3A")

    world = registry.table.world()
    assert world is not None and world.name == "Grey Reed"
    assert world.source_set.sources == ["XPHB", "XMM", "XDMG", "Homebrew"]
    # The campaign is one adventure; its Game Master 5e adventure's notes and scenes are
    # chapters of it, named after that adventure.
    (adventure,) = registry.adventures.adventures(world.id)
    assert adventure.name == "Grey Reed" and adventure.cover
    background, ford, ambush = registry.adventures.chapters(adventure.id)
    assert background.title == "Background" and "Nobody sleeps." in background.body
    assert ford.title == "The Drowned Bell: The Ford"
    assert ford.body.startswith("![The Drowned Bell: The Ford](media/")
    # An encounter with notes is a scene: a chapter with the fight and its loot as cards.
    assert ambush.title == "The Drowned Bell: Ambush at the ford"
    assert "They rise from the mud." in ambush.body
    assert "{@encounter Ambush at the ford}" in ambush.body
    assert "{@treasure Schatz: Ambush at the ford}" in ambush.body

    (fight,) = registry.encounters.list(world.id)
    assert fight.chapter_id == ambush.id and fight.adventure_id == adventure.id
    lines = {(m.name, m.source, m.label): m.count for m in fight.monsters}
    skeleton = next(k for k in lines if k[0] == "Skeleton")
    assert lines[skeleton] == 2 and skeleton[2] == "Bones"  # one group, its label unnumbered
    assert lines[(skeleton[0], skeleton[1], None)] == 1  # Klapperbein: its own name for it
    assert lines[("Bog Walker (Zombie)", "Homebrew", "Bog Walker")] == 1
    assert lines[("Reed Lurker", "Homebrew", None)] == 1
    assert lines[("Ferrywoman (Human)", "Homebrew", "Old Marla")] == 1  # the NPC joins
    assert "Mist" in fight.notes

    walker = registry.creatures.named("Bog Walker (Zombie)")
    assert walker is not None and walker.based_on and walker.based_on.startswith("Zombie|")
    assert (walker.ac, walker.hit_dice, walker.speed) == (9, 3, {"walk": 20, "swim": 20})
    assert walker.immune == ["poison"] and walker.alignment == ["N", "E"]
    assert "darkvision" in walker.senses.lower()  # left out by the backup, filled from the Zombie
    assert walker.actions[0].text.startswith("{@atk mw} {@hit 3}")
    lurker = registry.creatures.named("Reed Lurker")
    assert lurker is not None and lurker.based_on is None and lurker.size == "L"
    assert lurker.skills == ["stealth"] and lurker.saves == ["con"]
    assert [a.name for a in lurker.actions] == ["Bite"] and lurker.traits == []
    assert registry.compendium.get("monster", "Reed Lurker|Homebrew") is not None

    (marla,) = registry.npcs.list(world.id)
    assert marla.name == "Old Marla" and marla.statblock == "Ferrywoman (Human)|Homebrew"
    (loot,) = registry.treasure.list(world.id)
    assert loot.name == "Schatz: Ambush at the ford" and loot.coins.gp == 40
    assert [(i.name, i.count) for i in loot.items] == [("Potion of Healing", 2)]
    assert "**Marla's Bell Clapper** (uncommon): Rings under water." in loot.notes
    # The upload is gone once imported, and the page says what came over.
    assert (await gm.get(choose)).headers["location"].startswith("/gm/import/gm5?error=")
    notice = (await gm.get(done.headers["location"])).text
    assert "aus Game Master 5e importiert" in notice


async def test_the_world_menu_offers_it(gm: AsyncClient) -> None:
    page = (await gm.get("/gm/import/gm5")).text
    assert 'action="/gm/import/gm5"' in page and 'href="/gm/import/gm5"' in page
    refused = await gm.post("/gm/import/gm5", files={"file": ("x.backup", b"nope")})
    assert refused.headers["location"] == "/gm/import/gm5?error=gm5.error.not_backup"


async def test_every_campaign_ticked_into_one_world(
    gm: AsyncClient, registry: StoreRegistry
) -> None:
    sent = await gm.post("/gm/import/gm5", files={"file": ("my.backup", BACKUP)})
    done = await gm.post(
        sent.headers["location"],
        data={"campaigns": ["0", "1"], "name": "", "sources": ["XPHB", "XMM", "XDMG"]},
    )
    assert done.headers["location"].startswith("/gm/adventures?transferred=gm5%3A2%3A")
    world = registry.table.world()
    assert world is not None and world.name == "Game Master 5e"
    names = [a.name for a in registry.adventures.adventures(world.id)]
    assert sorted(names) == ["Elsewhere", "Grey Reed"]
