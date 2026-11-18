#!/usr/bin/env python3
"""Record the tutorial videos and cut them (website/README.md, "Videos").

    uv run --with playwright --with fonttools --with brotli python website/film/make.py
    … make.py fight                 # one episode
    … make.py fight --lang de       # one language
    … make.py fight --keep          # keep the recorded frames (website/film/takes/)
    … make.py fight --recut         # cut a kept take again, without recording

For each episode and language it builds a demo table in a temporary folder
(``stage.py``), starts Paravent on it with a fixed dice seed, records the script
(``episodes.py``) and writes ``website/assets/video/<lang>/<episode>.mp4`` with its
captions (``.vtt``), a poster (``.jpg``) and its GIF loops. The videos are not committed:
``build.py --deploy`` takes them to the server with the site.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

FILM = Path(__file__).resolve().parent
sys.path.insert(0, str(FILM))
sys.path.insert(0, str(FILM.parent))
sys.path.insert(0, str(FILM.parent.parent))

import engine  # noqa: E402
import shots  # noqa: E402 - on the path through stage
from episodes import BY_SLUG, EPISODE_LIST, texts  # noqa: E402
from stage import PASSWORD  # noqa: E402
from texts import LANGUAGES  # noqa: E402

OUT = engine.SITE / "assets/video"
TAKES = FILM / "takes"
SEED = "1871"


def record(slug: str, lang: str, keep: bool) -> Path:
    """Record one episode in one language; the take's folder."""
    from playwright.sync_api import sync_playwright

    episode = BY_SLUG[slug]
    words, labels = texts(slug, lang)
    scratch = Path(tempfile.mkdtemp(prefix=f"paravent-film-{slug}-{lang}-"))
    library = scratch / "library"
    folder = (TAKES if keep else scratch) / f"{slug}-{lang}"
    shutil.rmtree(folder, ignore_errors=True)
    os.environ["PARAVENT_DICE_SEED"] = SEED
    try:
        table = episode.stage(library, scratch, lang, shots.users_library())
        with shots.server(library, scratch) as base, sync_playwright() as play:
            try:
                browser = play.chromium.launch(channel="chrome")
            except Exception:  # noqa: BLE001 - no Chrome installed: Playwright's own
                browser = play.chromium.launch()
            take = engine.Take(browser, base, folder, PASSWORD, lang)
            try:
                episode.play(take, table, words, labels)
            except Exception:
                # Where it stopped, for whoever mends the script.
                for name, actor in take.actors.items():
                    shot = TAKES / f"failed-{slug}-{lang}-{name}.png"
                    shot.parent.mkdir(parents=True, exist_ok=True)
                    actor.page.screenshot(path=str(shot))
                    print("   failed; see", shot.relative_to(engine.ROOT), actor.page.url)
                raise
            take.save()
            browser.close()
        made = engine.publish(folder / "take.json", OUT / lang, slug, words["title"])
        for path in made:
            print("  ", path.relative_to(engine.ROOT), f"{path.stat().st_size // 1024} KB")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        os.environ.pop("PARAVENT_DICE_SEED", None)
    return folder


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("episodes", nargs="*", help=f"any of {', '.join(BY_SLUG)}")
    parser.add_argument("--lang", choices=LANGUAGES, action="append")
    parser.add_argument("--keep", action="store_true", help="keep the recorded frames")
    parser.add_argument("--recut", action="store_true", help="cut a kept take again")
    args = parser.parse_args()
    slugs = args.episodes or [e.slug for e in EPISODE_LIST]
    unknown = [s for s in slugs if s not in BY_SLUG]
    if unknown:
        parser.error(f"no such episode: {', '.join(unknown)}")
    failed = []
    for slug in slugs:
        for lang in args.lang or LANGUAGES:
            began = time.time()
            print(slug, lang)
            try:
                run(slug, lang, args)
            except Exception as error:  # noqa: BLE001 - on to the next; named at the end
                failed.append(
                    f"{slug} {lang}: {type(error).__name__}: {str(error).splitlines()[0]}"
                )
            print(f"   {time.time() - began:.0f} s")
    for line in failed:
        print("FAILED", line)
    return 1 if failed else 0


def run(slug: str, lang: str, args: argparse.Namespace) -> None:
    """One episode in one language: recorded, or a kept take cut again."""
    if not args.recut:
        record(slug, lang, args.keep)
        return
    take = TAKES / f"{slug}-{lang}" / "take.json"
    words, _ = texts(slug, lang)
    for path in engine.publish(take, OUT / lang, slug, words["title"]):
        print("  ", path.relative_to(engine.ROOT), f"{path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    sys.exit(main())
