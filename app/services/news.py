"""What changed in Paravent lately, for the gamemaster (the commits' ``News:`` lines).

The website build writes ``app/news.json`` from git with every rebuild: the newest lines a
gamemaster or player would notice, each with its time and its English and German text. The
app reads it from there (no git needed where it runs) and shows the lines newer than the
newest the gamemaster has dismissed; the first time, those of the last ``FIRST_DAYS`` days.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

NEWS_FILE = Path(__file__).resolve().parent.parent / "news.json"
FIRST_DAYS = 7
SHOWN = 8


@dataclass(frozen=True)
class NewsLine:
    """One line of what's new: when, and its words in each language."""

    at: str  # ISO time of the commit
    en: str
    de: str

    def text(self, lang: str) -> str:
        """The line in ``lang``, English when there is no other."""
        return self.de if lang == "de" else self.en


class NewsFeed:
    """Reads the news file, again whenever it changes."""

    def __init__(self, path: Path = NEWS_FILE) -> None:
        self._path = path
        self._stamp: float | None = None
        self._lines: list[NewsLine] = []

    def lines(self) -> list[NewsLine]:
        """Every line, newest first; none when the file is missing or unreadable."""
        try:
            stamp = self._path.stat().st_mtime
        except OSError:
            return []
        if stamp != self._stamp:
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = []
            self._lines = [
                NewsLine(str(r["at"]), str(r["en"]), str(r.get("de") or r["en"]))
                for r in raw
                if isinstance(r, dict) and r.get("at") and r.get("en")
            ]
            self._stamp = stamp
        return self._lines

    def unseen(self, seen: str) -> list[NewsLine]:
        """The lines newer than ``seen`` (an ISO time), at most ``SHOWN``; with nothing seen
        yet, those of the last ``FIRST_DAYS`` days."""
        lines = self.lines()
        if not seen and lines:
            newest = datetime.fromisoformat(lines[0].at)
            seen = (newest - timedelta(days=FIRST_DAYS)).isoformat()
        return [line for line in lines if _later(line.at, seen)][:SHOWN]


def _later(at: str, seen: str) -> bool:
    try:
        return datetime.fromisoformat(at) > datetime.fromisoformat(seen)
    except ValueError:
        return False


__all__ = ["FIRST_DAYS", "NEWS_FILE", "NewsFeed", "NewsLine"]
