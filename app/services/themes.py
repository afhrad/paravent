"""Themes: the look of the whole app, exchangeable as a folder (documentation/THEMES.md).

A theme is a folder holding ``theme.json`` and at most these files, all optional but
the manifest:

* ``tokens.css`` — values for the design tokens (``static/css/tokens.css``): colours
  for light and dark, fonts, corners, shadows, textures. Loaded after Paravent's own.
* ``skin.css`` — any further rules. Classes are not a stable contract: a skin may
  break when Paravent's markup changes, and the page says which version it was made for.
* ``icons.svg`` — a sprite replacing Paravent's, used only when it holds every icon.
* ``fonts/``, ``images/`` — what the stylesheets point at, by relative ``url()``.

Paravent ships its themes in ``app/themes/``; the table adds or replaces them in
``library/themes/``, so a theme is installed by dropping its folder in. Themes carry no
JavaScript and no markup: a theme is a look, never code. The pages' Content Security
Policy holds their stylesheets to files of this server.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import PROJECT_ROOT
from app.models.gamestate import TableSettings, User
from app.store.gamestate import GameStateStore
from app.ui.icons import available as builtin_icons

SHIPPED_DIR = PROJECT_ROOT / "app" / "themes"
MANIFEST = "theme.json"
# The format of the folder itself; a theme naming a newer one is refused.
FORMAT = 1
ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
# What a theme may hand a browser, by suffix.
MEDIA_TYPES = {
    ".css": "text/css",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".gif": "image/gif",
    ".txt": "text/plain",
}
# ``url()`` and ``@import`` pointing off this server; the policy blocks them, the
# themes page says so.
_EXTERNAL = re.compile(r"""(?:url\(\s*['"]?|@import\s+['"])\s*(?:https?:)?//""", re.I)
_SYMBOL = re.compile(r'<symbol id="i-([a-z0-9-]+)"')


Mode = Literal["light", "dark"]


def _both_modes() -> list[Mode]:
    return ["light", "dark"]


class Colours(BaseModel):
    """The browser's own colour for a mode: the status bar and the app's frame."""

    model_config = ConfigDict(extra="forbid")

    light: str = "#4560DC"
    dark: str = "#171A1F"


class Manifest(BaseModel):
    """``theme.json``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1)
    version: str = "1.0"
    author: str = ""
    description: str = ""
    # The Paravent version the theme was made for, shown when a skin looks wrong.
    paravent: str = ""
    format: int = FORMAT
    # Which colour modes it draws; one alone fixes the page to it.
    modes: list[Mode] = Field(default_factory=_both_modes)
    theme_color: Colours = Field(default_factory=Colours)
    license: str = ""


@dataclass(frozen=True)
class Theme:
    """One theme folder, read and checked."""

    id: str
    root: Path
    manifest: Manifest
    shipped: bool
    stylesheets: tuple[str, ...]
    icons: bool
    # What keeps parts of it from working, as translation keys with their values.
    problems: tuple[tuple[str, dict[str, str]], ...] = ()
    # Modification times, so a changed file is fetched again.
    versions: dict[str, int] = field(default_factory=dict)

    @property
    def fixed_mode(self) -> str | None:
        """The one mode the theme draws, or None when it draws both."""
        modes = set(self.manifest.modes)
        return next(iter(modes)) if len(modes) == 1 else None

    def url(self, name: str) -> str:
        """Where the browser fetches one of the theme's files."""
        version = self.versions.get(name)
        suffix = f"?v={version:x}" if version is not None else ""
        return f"/themes/{self.id}/{name}{suffix}"

    def file(self, name: str) -> Path | None:
        """A file of the theme the browser may fetch, or None: inside the folder, of a
        kind a theme hands out, and no hidden file."""
        relative = Path(name)
        if relative.is_absolute() or any(part.startswith(".") for part in relative.parts):
            return None
        if relative.suffix.lower() not in MEDIA_TYPES:
            return None
        try:
            path = (self.root / relative).resolve(strict=True)
        except OSError:
            return None
        if not path.is_file() or not path.is_relative_to(self.root.resolve()):
            return None
        return path

    def sprite(self) -> str | None:
        """The theme's icon sprite, when it has a complete one."""
        if not self.icons:
            return None
        try:
            return (self.root / "icons.svg").read_text(encoding="utf-8")
        except OSError:
            return None


def _read(root: Path, shipped: bool) -> Theme | None:
    """The theme in ``root``, or None when it is no theme at all (no manifest, a bad
    name). A manifest that does not read leaves a theme that only reports so."""
    theme_id = root.name
    manifest_path = root / MANIFEST
    if not ID.match(theme_id) or not manifest_path.is_file():
        return None
    problems: list[tuple[str, dict[str, str]]] = []
    try:
        manifest = Manifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError) as error:
        detail = str(error).splitlines()[0] if str(error) else type(error).__name__
        return Theme(
            theme_id,
            root,
            Manifest(name=theme_id),
            shipped,
            (),
            False,
            (("themes.problem.manifest", {"error": detail}),),
        )
    if manifest.format > FORMAT:
        return Theme(
            theme_id,
            root,
            manifest,
            shipped,
            (),
            False,
            (("themes.problem.format", {"format": str(manifest.format)}),),
        )
    stylesheets = tuple(n for n in ("tokens.css", "skin.css") if (root / n).is_file())
    versions: dict[str, int] = {}
    for name in stylesheets:
        path = root / name
        versions[name] = path.stat().st_mtime_ns
        if _EXTERNAL.search(path.read_text(encoding="utf-8", errors="replace")):
            problems.append(("themes.problem.external", {"file": name}))
    icons = False
    sprite = root / "icons.svg"
    if sprite.is_file():
        text = sprite.read_text(encoding="utf-8", errors="replace")
        missing = sorted(builtin_icons() - set(_SYMBOL.findall(text)))
        if missing:
            problems.append(
                (
                    "themes.problem.icons",
                    {"count": str(len(missing)), "names": ", ".join(missing[:8])},
                )
            )
        else:
            icons = True
            versions["icons.svg"] = sprite.stat().st_mtime_ns
    if not stylesheets and not icons:
        problems.append(("themes.problem.empty", {}))
    return Theme(theme_id, root, manifest, shipped, stylesheets, icons, tuple(problems), versions)


class ThemeLibrary:
    """Every theme of the code and the library, read again when a folder changes."""

    def __init__(self, library_dir: Path, shipped_dir: Path = SHIPPED_DIR) -> None:
        self.library_dir = library_dir
        self.shipped_dir = shipped_dir
        self._signature: tuple[tuple[str, int], ...] | None = None
        self._themes: dict[str, Theme] = {}

    def _stamp(self) -> tuple[tuple[str, int], ...]:
        """What changes when a theme is added, removed or edited."""
        stamps: list[tuple[str, int]] = []
        for base in (self.shipped_dir, self.library_dir):
            if not base.is_dir():
                continue
            stamps.append((str(base), base.stat().st_mtime_ns))
            for folder in base.iterdir():
                if not folder.is_dir():
                    continue
                for name in (MANIFEST, "tokens.css", "skin.css", "icons.svg"):
                    path = folder / name
                    if path.is_file():
                        stamps.append((str(path), path.stat().st_mtime_ns))
        return tuple(stamps)

    def _themes_now(self) -> dict[str, Theme]:
        signature = self._stamp()
        if signature != self._signature:
            themes: dict[str, Theme] = {}
            for base, shipped in ((self.shipped_dir, True), (self.library_dir, False)):
                if not base.is_dir():
                    continue
                for folder in sorted(base.iterdir()):
                    theme = _read(folder, shipped) if folder.is_dir() else None
                    if theme is not None:
                        themes[theme.id] = theme  # the library's replaces the code's
            self._themes = themes
            self._signature = signature
        return self._themes

    def all(self) -> list[Theme]:
        """Every theme, by name."""
        return sorted(self._themes_now().values(), key=lambda t: t.manifest.name.lower())

    def get(self, theme_id: str | None) -> Theme | None:
        """The theme ``theme_id``, or None for Paravent's own look or one not there."""
        if not theme_id:
            return None
        return self._themes_now().get(theme_id)

    def usable(self, theme_id: str | None) -> Theme | None:
        """``theme_id`` when it can be drawn: there, and with something to draw."""
        theme = self.get(theme_id)
        if theme is None or (not theme.stylesheets and not theme.icons):
            return None
        return theme


# A reader's choice of Paravent's own look over the table's theme.
OWN = "paravent"
TABLE_ID = "table"


def choose(user_choice: str | None, table_choice: str) -> str:
    """The theme a reader sees: their own choice, else the table's."""
    if user_choice == OWN:
        return ""
    return user_choice or table_choice


class ThemeService:
    """Which theme the table and each reader use."""

    def __init__(self, gamestate: GameStateStore, library: ThemeLibrary) -> None:
        self.gamestate = gamestate
        self.library = library

    def table_choice(self) -> str:
        """The table's theme id; "" for Paravent's own."""
        record = self.gamestate.table.find(TABLE_ID)
        return record.theme if record is not None else ""

    def set_table(self, theme_id: str) -> None:
        """Make ``theme_id`` the table's theme ("" for Paravent's own)."""
        if theme_id and self.library.get(theme_id) is None:
            raise KeyError(theme_id)
        self.gamestate.table.upsert(TableSettings(id=TABLE_ID, theme=theme_id))

    def set_user(self, user_id: str, theme_id: str) -> None:
        """A reader's own choice: a theme, :data:`OWN`, or "" for the table's."""
        if theme_id not in ("", OWN) and self.library.get(theme_id) is None:
            raise KeyError(theme_id)
        self.gamestate.users.update(user_id, theme=theme_id)

    def for_user(self, user: User | None) -> Theme | None:
        """The theme ``user`` reads in; before logging in, the table's."""
        return self.library.usable(choose(user.theme if user else None, self.table_choice()))


__all__ = [
    "FORMAT",
    "MEDIA_TYPES",
    "OWN",
    "SHIPPED_DIR",
    "Manifest",
    "Theme",
    "ThemeLibrary",
    "ThemeService",
    "choose",
]
