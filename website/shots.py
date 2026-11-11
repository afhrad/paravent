#!/usr/bin/env python3
"""Take the website's screenshots from a demo table (website/README.md, "Screenshots").

Builds a throwaway library in a temporary folder for each language: a world, an
adventure with a chapter, an encounter, three example characters and a fight in its
second round, every word of the table's own in that language (``TEXTS``). It starts
Paravent on it in the Tome theme and photographs ten screens, each in the
light and the dark mode, into ``website/assets/img/screens/<lang>/`` (light) and
``<lang>/dark/``:

- ``combat.webp``       the tracker, the troll's statblock beside the order (wide)
- ``chapter.webp``      a chapter with its read-aloud box and the encounter's card
- ``encounter.webp``    the encounter builder rating the fight
- ``sheet.webp``        a player's character sheet on a phone
- ``fight-phone.webp``  the same player's view of the fight, on their turn
- ``journal.webp``      a handout in that player's journal, on a phone
- ``wizard.webp``       the character wizard at its class step (without the books' line
                        about each class)
- ``names.webp``        the names workshop
- ``sound.webp``        the sound desk open beside the chapter (wide)
- ``recap.webp``        the fight's recap (for "How an evening goes")

and the picture a link to the site shows when shared, ``website/assets/img/share-<lang>.jpg``
(the tracker, 1200 × 630, light).

The creatures are from the System Reference Document 5.1 (Goblin, Bugbear, Wolf, Troll) and
every text in the world is our own, because the site is public. For the same reason the
wizard's class cards are photographed without the sentence each quotes from its book
(``HIDDEN``): that prose is the publisher's and not part of the SRD. The user's library is
never written, and read for two things only: the glossary's German names, and the German
text of those four SRD creatures, so the German shots name and describe them in German.
The corpus is read from ``PARAVENT_REFERENCE_DIR`` as the app reads it (``.env``).

    uv run --with playwright python website/shots.py          # both languages
    uv run --with playwright python website/shots.py --lang de
    uv run --with playwright python website/shots.py --mode dark

Uses the installed Chrome (``playwright install chromium`` otherwise), and Pillow, which
the app already needs, for WebP. Then run ``python website/build.py``.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
sys.path.insert(0, str(ROOT))

PASSWORD = "demo-password-" + secrets.token_hex(4)
GM, PLAYER = "demo", "mara"
OUT = SITE / "assets/img/screens"
# The game master's translation prompts (most of the books' text has no German yet): a
# working tool, not what the pictures are about.
# The sound desk's "no speaker connected" too: the demo has no loudspeaker.
# And the line a wizard card quotes from the books about its class: rulebook prose that is
# not in the SRD, which a public picture must not show.
HIDDEN = ".cb-translate, .tr-note, .sd-status, .cg-card-about{ display:none !important; }"
# Viewport (CSS pixels) and pixel ratio per kind of shot, matching the boxes on the page.
WIDE = ({"width": 1440, "height": 630}, 1.5)
WINDOW = ({"width": 1280, "height": 800}, 1.5)
PHONE = ({"width": 390, "height": 737}, 2)
SHARE = ({"width": 1600, "height": 840}, 0.75)  # 1200 × 630 pixels, more of the screen
MODES = ("light", "dark")
SOUND_KINDS = ("ambience",) * 3 + ("music",) * 2 + ("effect",) * 4

# The demo table's own words, one set per language: the German shots show a German
# table (world, chapter, names, handout, sounds, attacks), the English ones an English.
TEXTS: dict[str, dict[str, Any]] = {
    "en": {
        "gm": "Game master",
        "world": "The Sunken Marches",
        "world_text": "River country where the old dam broke; the towns live on ferries.",
        "adventure": "The Drowned Bell",
        "chapter": "The Ferry at Grey Reed",
        "encounter": "Ambush at the Ferry",
        "goblins": ("Snik", "Grub", "Vexa"),
        "boss": "Old Gnasher",
        "troll": "Mudmaw",
        "handout": "The bell in the fog",
        "sounds": (
            "Rain on the reeds",
            "River at night",
            "Tavern crowd",
            "The quiet ferry",
            "Drums of battle",
            "Drowned bell",
            "Wolf howl",
            "Steel on steel",
            "Troll roar",
        ),
        "attacks": {
            "longsword": "Longsword",
            "fire_bolt": "Fire Bolt",
            "morningstar": "Morningstar",
            "claw": "Claw",
            "bite": "Bite",
        },
        "body": """\
The ferry at Grey Reed has not crossed in three days. The ferryman's lantern still
hangs on its post, unlit, and the rope across the river is cut clean through on this side.

> [!read]
> Fog lies on the river like wet wool. Somewhere out on the water a bell tolls, slow,
> drowned and wrong, and the reeds along the bank go quiet all at once. On the landing a
> lantern swings, though there is no wind.

## The landing

Goblins of the Rotfang band took the hut two nights ago and keep watch from the reeds.
Their leader, a bugbear they call Old Gnasher, wants the toll box and anyone who might
pay to have the ferry running again. Under the landing sleeps Mudmaw, a river troll the
goblins feed; the noise of a fight wakes it in the second round.

{@encounter Ambush at the Ferry}

> [!secret]
> The bell is real: the old chapel tower lies under the river since the dam broke, and
> something down there rings it whenever the moon is new.

## If the party wins

The ferryman, Hob Wade, is tied up behind the hut. He knows the way to the drowned
chapel and will take them across at dawn, for free, this once.
""",
    },
    "de": {
        "gm": "Spielleitung",
        "world": "Die Versunkenen Marschen",
        "world_text": "Flussland, in dem der alte Damm brach; die Städte leben von ihren Fähren.",
        "adventure": "Die ertrunkene Glocke",
        "chapter": "Die Fähre bei Grauried",
        "encounter": "Hinterhalt an der Fähre",
        "goblins": ("Snik", "Grub", "Vexa"),
        "boss": "Der alte Knirscher",
        "troll": "Schlammschlund",
        "handout": "Die Glocke im Nebel",
        "sounds": (
            "Regen im Schilf",
            "Fluss bei Nacht",
            "Stimmen in der Taverne",
            "Die stille Fähre",
            "Kriegstrommeln",
            "Ertrunkene Glocke",
            "Wolfsgeheul",
            "Stahl auf Stahl",
            "Trollgebrüll",
        ),
        "attacks": {
            "longsword": "Langschwert",
            "fire_bolt": "Feuerpfeil",
            "morningstar": "Morgenstern",
            "claw": "Klaue",
            "bite": "Biss",
        },
        "body": """\
Die Fähre bei Grauried hat seit drei Tagen nicht übergesetzt. Die Laterne des Fährmanns
hängt noch an ihrem Pfahl, kalt, und das Seil über den Fluss ist auf dieser Seite glatt
durchtrennt.

> [!read]
> Nebel liegt auf dem Fluss wie nasse Wolle. Irgendwo draußen auf dem Wasser schlägt eine
> Glocke, langsam, ertrunken und falsch, und das Schilf am Ufer verstummt auf einen Schlag.
> Am Anleger schwingt eine Laterne, obwohl kein Wind geht.

## Der Anleger

Goblins der Faulzahn-Bande haben die Hütte vor zwei Nächten besetzt und halten aus dem
Schilf Wache. Ihr Anführer, ein Grottenschrat, den sie den alten Knirscher nennen, will die
Mautkiste und jeden, der dafür zahlen würde, dass die Fähre wieder fährt. Unter dem Anleger
schläft Schlammschlund, ein Flusstroll, den die Goblins füttern; der Lärm eines Kampfes
weckt ihn in der zweiten Runde.

{@encounter Hinterhalt an der Fähre}

> [!secret]
> Die Glocke ist echt: Der alte Kapellturm liegt unter dem Fluss, seit der Damm brach, und
> etwas dort unten läutet sie bei jedem Neumond.

## Wenn die Gruppe siegt

Der Fährmann, Hob Wade, liegt gefesselt hinter der Hütte. Er kennt den Weg zur versunkenen
Kapelle und bringt die Gruppe im Morgengrauen hinüber, umsonst, dieses eine Mal.
""",
    },
}
# The creatures whose German statblock text the demo borrows from the user's
# translations: the System Reference Document's, nothing else.
SRD_CREATURES = ("Goblin|MM", "Bugbear|MM", "Wolf|MM", "Troll|MM")


def unwrap(text: str) -> str:
    """Join the lines of each paragraph: Paravent reads a newline as a line break."""
    blocks = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        if lines[0].startswith("> "):
            head, rest = lines[0], [line.removeprefix(">").strip() for line in lines[1:]]
            blocks.append(head + ("\n> " + " ".join(rest) if rest else ""))
        else:
            blocks.append(" ".join(lines))
    return "\n\n".join(blocks) + "\n"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def overrides(library: Path, scratch: Path) -> dict[str, str]:
    """Settings that keep the demo away from everything of the user's."""
    return {
        "PARAVENT_LIBRARY_DIR": str(library),
        "PARAVENT_BACKUP_DIR": str(scratch / "backups"),
        "PARAVENT_BACKUP_AT": "",
        "PARAVENT_TRANSLATE_API_KEY": "",
        "PARAVENT_WATCH_FILES": "false",
        "PARAVENT_SECRET_KEY": secrets.token_hex(32),
    }


# (attacker, target, attack, to hit, damage, type): the fight so far, for the recap.
# Who is named by role (the labels differ by language), the attack by its key in TEXTS.
BLOWS = (
    ("varis", "snik", "longsword", 7, "1d8+4", "slashing"),
    ("lyra", "boss", "fire_bolt", 7, "2d10", "fire"),
    ("boss", "varis", "morningstar", 4, "2d8+2", "piercing"),
    ("brunhild", "troll", "longsword", 8, "1d8+4", "slashing"),
    ("troll", "brunhild", "claw", 7, "2d6+4", "slashing"),
    ("brunhild", "troll", "longsword", 8, "1d8+4", "slashing"),
    ("varis", "troll", "longsword", 7, "1d8+4", "slashing"),
    ("wolf", "lyra", "bite", 4, "2d4+2", "piercing"),
)


def quiet_wav() -> io.BytesIO:
    """A second of silence as a WAV file, for the sound desk's pads."""
    import wave

    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\0\0" * 8000)
    out.seek(0)
    return out


def borrow_translations(users_library: Path, target: Path) -> None:
    """The user's German names, and their German text of the demo's SRD creatures, so
    the German shots read German. Nothing else of the user's library is read."""
    source = users_library / "i18n/de"
    target.mkdir(parents=True, exist_ok=True)
    if (source / "_glossary.json").exists():
        shutil.copyfile(source / "_glossary.json", target / "_glossary.json")
    for category in ("monster", "monsterFluff"):
        path = source / f"{category}.json"
        if path.exists():
            records = json.loads(path.read_text(encoding="utf-8"))
            kept = {k: v for k, v in records.items() if k in SRD_CREATURES}
            (target / f"{category}.json").write_text(
                json.dumps(kept, ensure_ascii=False), encoding="utf-8"
            )


def users_library() -> Path:
    """Where the user's own library is, read before any demo points the settings away."""
    from app.config import Settings

    found = Settings(_env_file=ROOT / ".env").library_dir  # type: ignore[call-arg]
    return found if found.is_absolute() else ROOT / found


def seed(library: Path, scratch: Path, lang: str, users: Path) -> dict[str, str]:
    """The demo table in ``lang``; returns the ids the shots need."""
    text = TEXTS[lang]
    body = unwrap(text["body"])
    from app.config import Settings
    from app.models.gamestate import Role, Scope
    from app.models.pc import PlayerCharacter
    from app.services.combat import pending_attacks
    from app.services.monster_attacks import Damage
    from app.services.worlds import WorldInput
    from app.store.registry import StoreRegistry

    os.environ.update(overrides(library, scratch))
    registry = StoreRegistry(Settings(_env_file=ROOT / ".env"))  # type: ignore[call-arg]
    registry.load()
    borrow_translations(users, registry.settings.i18n_dir / "de")

    registry.auth.create_user(GM, PASSWORD, Role.GAMEMASTER, display_name=text["gm"])
    registry.auth.create_user(PLAYER, PASSWORD, Role.PLAYER, display_name="Mara")
    registry.themes.set_table("tome")
    world = registry.worlds.create(
        WorldInput(
            name=text["world"],
            description=text["world_text"],
            sources=("PHB", "MM", "DMG"),
            language=lang,
        )
    )

    characters: dict[str, str] = {}
    for name, owner in (
        ("lyra-mooncaller", PLAYER),
        ("brunhild-ironvow", GM),
        ("varis-thornwood", GM),
    ):
        data: dict[str, Any] = json.loads(
            (ROOT / f"documentation/examples/{name}.json").read_text(encoding="utf-8")
        )
        data.pop("$schema", None)
        data.update(world_id=world.id, player=owner)
        made = registry.gamestate.characters.create(PlayerCharacter.model_validate(data))
        characters[name] = made.id

    adventures = registry.adventures
    adventure = adventures.create(world.id, text["adventure"])
    chapter = adventures.create_chapter(adventure.id, text["chapter"])
    encounters = registry.encounters
    here = Scope(world_id=world.id, adventure_id=adventure.id, chapter_id=chapter.id)
    encounter = encounters.create(text["encounter"], world.id, scope=here)
    for ref, count in (("Goblin|MM", 3), ("Bugbear|MM", 1), ("Wolf|MM", 2), ("Troll|MM", 1)):
        encounters.add_monster(encounter.id, ref, count)
    encounters.set_label(encounter.id, 0, ", ".join(text["goblins"]))
    encounters.set_label(encounter.id, 1, text["boss"])
    encounters.set_label(encounter.id, 3, text["troll"])
    adventures.update_chapter(chapter.id, title=text["chapter"], status="running", body=body)

    combats = registry.combats
    combat = combats.start(registry.gamestate.encounters.get(encounter.id), GM)
    rolls = {
        characters["lyra-mooncaller"]: 17,
        characters["brunhild-ironvow"]: 9,
        characters["varis-thornwood"]: 21,
    }
    for member in combat.combatants:
        if member.character_id in rolls:
            combats.set_initiative(combat.id, member.id, rolls[member.character_id], GM)
    combat = combats.begin(combat.id, GM)
    by_label = {m.label: m for m in combat.combatants}
    snik, grub, vexa = text["goblins"]
    roles = {
        "varis": "Varis Thornwood",
        "lyra": "Lyra Mooncaller",
        "brunhild": "Brunhild Ironvow",
        "snik": snik,
        "boss": text["boss"],
        "troll": text["troll"],
        "wolf": "Wolf 1",
    }
    # Blows with an attacker, rolled and resolved as the tracker does, so the recap
    # can say who dealt what.
    for who, whom, weapon, hit, dice, kind in BLOWS:
        attacker, target = roles[who], roles[whom]
        if attacker not in by_label or target not in by_label:
            continue
        name = text["attacks"][weapon]
        rolled = combats.attack(
            combat.id, by_label[attacker].id, name, hit, [Damage(dice, kind)], GM
        )
        waiting = next(
            (e for e in pending_attacks(rolled) if e.actor == by_label[attacker].id), None
        )
        if waiting is not None:  # a natural 1 rolls no damage and waits for nothing
            combats.resolve_attack(combat.id, waiting.at.isoformat(), by_label[target].id, GM)
    for label, hurt in ((grub, 3), ("Wolf 1", 11)):
        if label in by_label:
            combats.damage(combat.id, by_label[label].id, hurt, GM)
    if vexa in by_label:
        combats.toggle_condition(combat.id, by_label[vexa].id, "prone", GM, rounds=1)
    lyra = next(m for m in combat.combatants if m.character_id == characters["lyra-mooncaller"])
    for _ in range(3 * len(combat.combatants)):  # on to round 2 or 3, Lyra's turn
        combat = combats.next_turn(combat.id, GM)
        if combat.round >= 2 and combat.turn == lyra.group:
            break
    auth = registry.auth
    gm_user, player_user = auth.find_user(GM), auth.find_user(PLAYER)
    assert gm_user is not None and player_user is not None
    gm_id = auth.identify(auth.start_session(gm_user))
    player_id = auth.identify(auth.start_session(player_user))
    assert gm_id is not None and player_id is not None

    from app.ui.markdown import callouts

    read = next(c for c in callouts(body) if c.kind == "read")
    handout = registry.handouts.share_box(
        chapter.id, read.key, [characters["lyra-mooncaller"]], text["handout"], gm_id
    )
    draft = registry.drafts.start(player_id, world.id)
    for name, kind in zip(text["sounds"], SOUND_KINDS, strict=True):
        registry.sounds.add(f"{name}.wav", quiet_wav(), kind, name=name)  # type: ignore[arg-type]

    troll = by_label.get(text["troll"])
    return {
        "world": world.id,
        "chapter": chapter.id,
        "encounter": encounter.id,
        "combat": combat.id,
        "lyra": characters["lyra-mooncaller"],
        "troll": troll.id if troll else "",
        "handout": handout.id,
        "draft": draft.id,
    }


@contextmanager
def server(library: Path, scratch: Path) -> Iterator[str]:
    port = free_port()
    env = {**os.environ, **overrides(library, scratch)}
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(base + "/login", timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise RuntimeError("Paravent did not start")
        yield base
    finally:
        process.terminate()
        process.wait(timeout=10)


def save(png: bytes, path: Path) -> None:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    picture = Image.open(io.BytesIO(png)).convert("RGB")
    if path.suffix == ".jpg":
        picture.save(path, "JPEG", quality=85, optimize=True, progressive=True)
    else:
        picture.save(path, "WEBP", quality=82, method=6)
    print("  ", path.relative_to(ROOT), f"{path.stat().st_size // 1024} KB")


def shoot(base: str, ids: dict[str, str], langs: list[str], modes: list[str]) -> None:
    from playwright.sync_api import Browser, Page, sync_playwright

    def session(
        browser: Browser, user: str, lang: str, size: tuple[dict[str, int], float], mode: str
    ) -> Page:
        viewport, ratio = size
        page = browser.new_page(
            viewport=viewport,  # type: ignore[arg-type]
            device_scale_factor=ratio,
            color_scheme=mode,  # type: ignore[arg-type]
        )
        page.goto(base + "/login")
        page.fill("input[name=username]", user)
        page.fill("input[name=password]", PASSWORD)
        page.press("input[name=password]", "Enter")
        page.wait_for_load_state()
        page.request.post(base + "/language", form={"lang": lang})
        return page

    def snap(page: Page, url: str, path: Path, scroll: str = "", then: str = "") -> None:
        page.goto(base + url)
        page.wait_for_load_state("load")
        page.add_style_tag(content=HIDDEN)
        if scroll:
            page.locator(scroll).first.scroll_into_view_if_needed()
            page.evaluate("window.scrollBy(0, -90)")
        if then:  # something to open first, such as the sound desk
            page.request.post(base + "/gm/sound/stop")  # its state outlives the page
            page.locator(then).first.click()
            page.wait_for_selector(".sd-pad", timeout=10000)
            for pad in (".sd-ambience", ".sd-music"):  # a mix playing: rain and a tune
                page.locator(pad).first.click()
                page.wait_for_timeout(400)
        page.wait_for_timeout(1200)
        save(page.screenshot(), path)

    with sync_playwright() as play:
        try:
            browser = play.chromium.launch(channel="chrome")
        except Exception:  # noqa: BLE001 - no Chrome installed: Playwright's own
            browser = play.chromium.launch()
        combat = f"/gm/combat/{ids['combat']}?sel={ids['troll']}"
        chapter = f"/gm/chapters/{ids['chapter']}"
        for lang in langs:
            if "light" in modes:
                print(lang, "share")
                gm = session(browser, GM, lang, SHARE, "light")
                gm.goto(base + combat)
                gm.add_style_tag(content=HIDDEN)
                gm.wait_for_timeout(1200)
                save(gm.screenshot(type="png"), SITE / f"assets/img/share-{lang}.jpg")
                gm.close()
            for mode in modes:
                print(lang, mode)
                out = OUT / lang / ("dark" if mode == "dark" else "")
                gm = session(browser, GM, lang, WIDE, mode)
                snap(gm, combat, out / "combat.webp")
                snap(gm, chapter, out / "sound.webp", then=".shell-sound")
                gm.close()
                gm = session(browser, GM, lang, WINDOW, mode)
                snap(gm, chapter, out / "chapter.webp")
                snap(gm, f"/gm/encounters/{ids['encounter']}", out / "encounter.webp")
                snap(gm, "/gm/names", out / "names.webp")
                snap(gm, f"/gm/combat/{ids['combat']}/recap", out / "recap.webp")
                gm.close()
                player = session(browser, PLAYER, lang, WINDOW, mode)
                snap(player, f"/chargen/{ids['draft']}/class", out / "wizard.webp")
                player.close()
                player = session(browser, PLAYER, lang, PHONE, mode)
                snap(player, f"/characters/{ids['lyra']}", out / "sheet.webp")
                snap(player, f"/play/combat/{ids['combat']}", out / "fight-phone.webp")
                snap(player, f"/journal/{ids['handout']}", out / "journal.webp")
                player.close()
        browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lang", choices=("en", "de"), action="append")
    parser.add_argument("--mode", choices=MODES, action="append")
    args = parser.parse_args()
    users = users_library()
    # A table of its own per language: its world, chapter and names in that language.
    for lang in args.lang or ["en", "de"]:
        scratch = Path(tempfile.mkdtemp(prefix=f"paravent-shots-{lang}-"))
        library = scratch / "library"
        try:
            ids = seed(library, scratch, lang, users)
            with server(library, scratch) as base:
                shoot(base, ids, [lang], args.mode or list(MODES))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    print("Now: python website/build.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
