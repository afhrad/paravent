"""Themes (documentation/THEMES.md): read from a folder, chosen per reader and for the
table, served confined to their folder, and held to this server by the page policy."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import CONTENT_POLICY, create_app
from app.models.gamestate import Role, User
from app.services.themes import OWN, SHIPPED_DIR, ThemeLibrary, choose
from app.store.registry import StoreRegistry
from app.ui.icons import available

PASSWORD = "correct horse"


def _theme(root: Path, theme_id: str, manifest: object = None, **files: str) -> Path:
    folder = root / theme_id
    folder.mkdir(parents=True)
    body = manifest if manifest is not None else {"name": theme_id.title()}
    (folder / "theme.json").write_text(
        body if isinstance(body, str) else json.dumps(body), encoding="utf-8"
    )
    for name, text in files.items():
        path = folder / name.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return folder


def _sprite(names: frozenset[str]) -> str:
    symbols = "".join(f'<symbol id="i-{n}" viewBox="0 0 24 24"></symbol>' for n in sorted(names))
    return f'<svg xmlns="http://www.w3.org/2000/svg">{symbols}</svg>'


class TestLibrary:
    def test_a_folder_with_a_manifest_and_tokens_is_a_theme(self, tmp_path: Path) -> None:
        _theme(tmp_path, "dusk", {"name": "Dusk", "modes": ["dark"]}, **{"tokens.css": ":root{}"})
        library = ThemeLibrary(tmp_path, shipped_dir=tmp_path / "none")
        theme = library.usable("dusk")
        assert theme is not None
        assert theme.manifest.name == "Dusk"
        assert theme.stylesheets == ("tokens.css",)
        assert theme.fixed_mode == "dark"
        assert re.fullmatch(r"/themes/dusk/tokens\.css\?v=[0-9a-f]+", theme.url("tokens.css"))

    def test_the_librarys_theme_replaces_the_shipped_one(self, tmp_path: Path) -> None:
        _theme(tmp_path / "code", "tome", {"name": "Old"}, **{"tokens.css": ""})
        _theme(tmp_path / "lib", "tome", {"name": "New"}, **{"tokens.css": ""})
        library = ThemeLibrary(tmp_path / "lib", shipped_dir=tmp_path / "code")
        theme = library.get("tome")
        assert theme is not None and theme.manifest.name == "New" and not theme.shipped

    def test_problems_are_reported_not_raised(self, tmp_path: Path) -> None:
        _theme(tmp_path, "broken", "{not json")
        _theme(tmp_path, "far", **{"skin.css": "body{background:url(https://example.com/x.png)}"})
        _theme(tmp_path, "icons", **{"icons.svg": _sprite(frozenset({"sun"}))})
        _theme(tmp_path, "future", {"name": "Future", "format": 99}, **{"tokens.css": ""})
        library = ThemeLibrary(tmp_path, shipped_dir=tmp_path / "none")
        problems = {t.id: [key for key, _ in t.problems] for t in library.all()}
        assert problems["broken"] == ["themes.problem.manifest"]
        assert problems["far"] == ["themes.problem.external"]
        assert problems["icons"] == ["themes.problem.icons", "themes.problem.empty"]
        assert problems["future"] == ["themes.problem.format"]
        assert library.usable("broken") is None and library.usable("icons") is None

    def test_a_complete_sprite_replaces_the_icons(self, tmp_path: Path) -> None:
        _theme(tmp_path, "glyphs", **{"icons.svg": _sprite(available())})
        theme = ThemeLibrary(tmp_path, shipped_dir=tmp_path / "none").usable("glyphs")
        assert theme is not None and theme.icons and theme.sprite() is not None

    def test_a_change_is_read_again(self, tmp_path: Path) -> None:
        library = ThemeLibrary(tmp_path, shipped_dir=tmp_path / "none")
        assert library.get("late") is None
        _theme(tmp_path, "late", **{"tokens.css": ""})
        assert library.get("late") is not None

    def test_files_stay_inside_the_folder(self, tmp_path: Path) -> None:
        folder = _theme(
            tmp_path / "themes",
            "safe",
            **{"tokens.css": "", "fonts__a.woff2": "x", "notes.md": "", ".hidden.css": ""},
        )
        (tmp_path / "secret.css").write_text("", encoding="utf-8")
        theme = ThemeLibrary(tmp_path / "themes", shipped_dir=tmp_path / "none").get("safe")
        assert theme is not None
        assert theme.file("fonts/a.woff2") == (folder / "fonts" / "a.woff2").resolve()
        for name in ("../../secret.css", "notes.md", ".hidden.css", "theme.json", "/etc/hosts"):
            assert theme.file(name) is None, name

    def test_names_that_are_no_id_are_ignored(self, tmp_path: Path) -> None:
        _theme(tmp_path, "Bad Name", **{"tokens.css": ""})
        assert ThemeLibrary(tmp_path, shipped_dir=tmp_path / "none").all() == []


def test_the_reader_chooses_over_the_table() -> None:
    assert choose("", "tome") == "tome"
    assert choose(None, "tome") == "tome"
    assert choose("dusk", "tome") == "dusk"
    assert choose(OWN, "tome") == ""


class TestTome:
    """The theme Paravent ships."""

    def test_it_loads_without_problems(self) -> None:
        theme = ThemeLibrary(Path("/nonexistent"), shipped_dir=SHIPPED_DIR).get("tome")
        assert theme is not None
        assert theme.problems == ()
        assert theme.stylesheets == ("tokens.css", "skin.css")

    def test_every_url_it_names_is_a_file_of_its_own(self) -> None:
        theme = ThemeLibrary(Path("/nonexistent"), shipped_dir=SHIPPED_DIR).get("tome")
        assert theme is not None
        for name in theme.stylesheets:
            css = (theme.root / name).read_text(encoding="utf-8")
            for target in re.findall(r"url\(([^)#]+)\)", css):
                assert theme.file(target.strip("'\" ")) is not None, target

    @pytest.mark.parametrize("mode", ["light", "dark"])
    def test_text_is_readable(self, mode: str) -> None:
        """Every text colour clears 4.5:1 (WCAG AA) on every surface it sits on."""
        css = (SHIPPED_DIR / "tome" / "tokens.css").read_text(encoding="utf-8")
        start = css.index(':root[data-theme="dark"]{') if mode == "dark" else css.index(":root{")
        tokens = dict(
            re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", css[start : css.index("}", start)])
        )

        def luminance(colour: str) -> float:
            def channel(c: float) -> float:
                return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

            r, g, b = (channel(int(colour[i : i + 2], 16) / 255) for i in (1, 3, 5))
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        def ratio(a: str, b: str) -> float:
            high, low = sorted((luminance(tokens[a]), luminance(tokens[b])), reverse=True)
            return (high + 0.05) / (low + 0.05)

        texts = (
            "text",
            "text-2",
            "text-3",
            "accent-text",
            "dmg-text",
            "heal-text",
            "magic-text",
            "cond-text",
        )
        surfaces = ("bg", "bg-sunken", "surface", "surface-2", "surface-3")
        pairs = [(t, s) for t in texts for s in surfaces] + [
            ("on-accent", "accent-solid"),
            *((f"{k}-text", f"{k}-weak") for k in ("accent", "dmg", "heal", "magic", "cond")),
        ]
        low = [(a, b, round(ratio(a, b), 2)) for a, b in pairs if ratio(a, b) < 4.5]
        assert low == []


@pytest.fixture
def registry(tmp_path: Path) -> StoreRegistry:
    registry = StoreRegistry(Settings(library_dir=tmp_path / "library", watch_files=False))
    registry.load()
    _theme(tmp_path / "library" / "themes", "dusk", {"name": "Dusk"}, **{"tokens.css": ":root{}"})
    return registry


@pytest.fixture
def gm(registry: StoreRegistry) -> User:
    return registry.auth.create_user("dm", PASSWORD, Role.GAMEMASTER, "Dana")


@pytest.fixture
def alice(registry: StoreRegistry, gm: User) -> User:
    return registry.auth.create_user("alice", PASSWORD, Role.PLAYER, "Alice")


@pytest.fixture
async def client(registry: StoreRegistry) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.stores = registry
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def _login(client: AsyncClient, username: str) -> None:
    await client.post("/login", data={"username": username, "password": PASSWORD})


class TestRoutes:
    async def test_the_table_theme_is_what_everyone_sees(
        self, client: AsyncClient, registry: StoreRegistry, gm: User, alice: User
    ) -> None:
        await _login(client, "dm")
        assert (await client.post("/themes/table", data={"theme": "tome"})).status_code == 303
        page = await client.get("/themes")
        assert "/themes/tome/tokens.css?v=" in page.text
        assert "/themes/tome/skin.css?v=" in page.text
        await client.post("/logout")
        login = await client.get("/login")
        assert "/themes/tome/skin.css" in login.text  # before logging in too
        await _login(client, "alice")
        assert "/themes/tome/skin.css" in (await client.get("/themes")).text

    async def test_a_reader_chooses_their_own(
        self, client: AsyncClient, registry: StoreRegistry, gm: User, alice: User
    ) -> None:
        registry.themes.set_table("tome")
        await _login(client, "alice")
        await client.post("/themes", data={"theme": "dusk", "next": "/themes"})
        text = (await client.get("/themes")).text
        assert "/themes/dusk/tokens.css" in text and "/themes/tome/" not in text.split("</head>")[0]
        await client.post("/themes", data={"theme": OWN})
        head = (await client.get("/themes")).text.split("</head>")[0]
        assert "/themes/" not in head
        await client.post("/themes", data={"theme": "nonexistent"})
        assert registry.gamestate.users.get(alice.id).theme == OWN

    async def test_only_the_gamemaster_sets_the_table(
        self, client: AsyncClient, gm: User, alice: User
    ) -> None:
        await _login(client, "alice")
        response = await client.post("/themes/table", data={"theme": "tome"})
        assert response.status_code == 403

    async def test_files_are_served_with_their_type_and_kept_inside(
        self, client: AsyncClient
    ) -> None:
        css = await client.get("/themes/tome/tokens.css")
        assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")
        assert "sandbox" in css.headers["content-security-policy"]
        font = await client.get("/themes/tome/fonts/cinzel-latin-700-normal.woff2")
        assert font.headers["content-type"] == "font/woff2"
        for path in (
            "/themes/tome/theme.json",
            "/themes/tome/..%2F..%2Fthemes.py",
            "/themes/none/x.css",
        ):
            assert (await client.get(path)).status_code == 404, path

    async def test_pages_carry_the_content_policy(self, client: AsyncClient, gm: User) -> None:
        response = await client.get("/login")
        assert response.headers["content-security-policy"] == CONTENT_POLICY
        assert "font-src 'self'" in CONTENT_POLICY and "style-src 'self'" in CONTENT_POLICY
