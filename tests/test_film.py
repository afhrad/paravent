"""The tutorial videos' tools (website/film): their words and the cut, without a browser."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

FILM = Path(__file__).resolve().parent.parent / "website/film"
sys.path.insert(0, str(FILM))

import engine  # noqa: E402
import texts  # noqa: E402


def test_every_language_has_every_word() -> None:
    for slug, by_language in texts.EPISODES.items():
        keys = {lang: set(words) for lang, words in by_language.items()}
        assert set(keys) == set(texts.LANGUAGES), slug
        assert len({frozenset(k) for k in keys.values()}) == 1, slug
        assert {"title", "subtitle", "end_title"} <= keys["en"], slug
    assert {frozenset(v) for v in texts.LABELS.values()} == {frozenset(texts.LABELS["en"])}
    adventure = texts.ADVENTURE
    assert set(adventure["en"]) == set(adventure["de"])
    assert len(adventure["en"]["chapters"]) == len(adventure["de"]["chapters"])  # type: ignore[arg-type]


def test_the_adventure_keeps_to_the_srd() -> None:
    """The demo adventure names no creature but the System Reference Document's."""
    allowed = {"goblin", "wolf", "skeleton"}
    for words in texts.ADVENTURE.values():
        for chapter in words["chapters"]:  # type: ignore[attr-defined]
            body = chapter["body"].lower()
            tags = [t.split("}")[0] for t in body.split("{@creature ")[1:]]
            assert all(t.split("|")[0].strip() in allowed for t in tags)


def test_loops_come_from_their_marks() -> None:
    marks = {"hit:start": 3.0, "hit:end": 7.5, "poster": 4.0, "roll:start": 9.0}
    assert engine.loops(marks) == [("hit", 3.0, 7.5)]


def test_a_caption_stays_long_enough_to_read() -> None:
    assert engine.reading_time("Short.") == 2.0
    assert engine.reading_time(" ".join(["word"] * 28)) > 10


def _take(folder: Path) -> Path:
    """A take of two seconds: a desk window that changes once, a caption, a card."""
    desk = folder / "gm"
    desk.mkdir(parents=True)
    for n, colour in enumerate(("white", "red")):
        Image.new("RGB", (1280, 800), colour).save(desk / f"{n:06d}.jpg")
    data = {
        "start": 100.0,
        "end": 103.0,
        "events": [
            [100.0, "card", ["Title", "Sub"]],
            [100.5, "card", None],
            [100.5, "layout", ["desk", ["gm"]]],
            [100.6, "caption", "Hello"],
            [101.5, "mark", "poster"],
            [102.0, "caption", ""],
        ],
        "actors": {
            "gm": {
                "phone": False,
                "label": "Game master",
                "frames": [[100.0, "gm/000000.jpg"], [101.0, "gm/000001.jpg"]],
            }
        },
    }
    path = folder / "take.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_the_cut_follows_the_timeline(tmp_path: Path) -> None:
    cut = engine.Cut(_take(tmp_path), "Title")
    assert cut.duration == pytest.approx(3.0)
    card, _ = cut.frame_at(0.2)
    assert card[0] == "card"
    early, _ = cut.frame_at(0.8)
    late, _ = cut.frame_at(1.2)
    assert early != late  # the window changed at 1 s
    assert early[2] == "Hello"
    assert cut.captions() == [(pytest.approx(0.6), pytest.approx(2.0), "Hello")]
    assert cut.marks() == {"poster": pytest.approx(1.5)}
    vtt = tmp_path / "out.vtt"
    engine.write_vtt(cut, vtt)
    assert vtt.read_text(encoding="utf-8").startswith(
        "WEBVTT\n\n00:00:00.600 --> 00:00:02.000\nHello"
    )


def test_a_frame_is_drawn_whole(tmp_path: Path) -> None:
    """Drawing needs fontTools, which ``make.py`` runs with (``uv run --with``)."""
    pytest.importorskip("fontTools")
    pytest.importorskip("brotli")
    cut = engine.Cut(_take(tmp_path), "Title")
    _, draw = cut.frame_at(0.8)
    assert draw().size == (engine.WIDTH, engine.HEIGHT)
    _, card = cut.frame_at(0.2)
    assert card().size == (engine.WIDTH, engine.HEIGHT)
