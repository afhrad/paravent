#!/usr/bin/env python3
"""Build paravent.org from this folder (website/README.md).

The site is static and self-contained: every file it needs lives in ``website/``, so the
folder runs as it is on any web server. This script keeps it in step with Paravent:

0. **Write** — fills the templates (``templates/index.html``, ``legal.html``) with the
   text of ``content/en.json`` and ``content/de.json`` into ``index.html``, ``legal.html``,
   ``de/index.html`` and ``de/legal.html``. The text is edited there, never in the pages.
1. **Sync** — copies the app's design tokens and components
   (``static/css/tokens.css``, ``components.css``), the Tome theme's tokens, fonts and
   textures, and the logo into ``assets/``, and writes
   the icons the pages use (``href="#i-…"``) from ``static/icons.svg`` into each page.
2. **Update** — writes "What's new" from the git history (each commit subject is
   already written for a reader) and the numbers in the facts strip (version, tests)
   into ``index.html``, between ``<!-- name:start -->`` / ``<!-- name:end -->``.
3. **Pack** — with ``--dist``, copies the site without this script and its notes into
   ``website/dist/site/``, checks that copy as a visitor would meet it (links, anchors,
   pictures, alt texts, sizes, balanced HTML) and, when nothing is broken, zips it as
   ``website/dist/paravent-website-<version>.zip``.
4. **Deploy** — with ``--deploy``, sends that checked copy to the web server over SFTP
   (``PARAVENT_SITE_TARGET``, from the environment or the project's ``.env``): only files
   that changed, and removes only files an earlier upload put there; ``--preview`` lists
   what would change and changes nothing.

    python website/build.py            # sync + update, in place
    python website/build.py --dist     # … and the zip to upload
    python website/build.py --check    # exit 1 if the pages are not up to date
    python website/build.py --preview  # … what an upload would change on the server
    python website/build.py --deploy   # build, check, zip and upload

Standard library only; run from anywhere.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
# (language, template, page written): every page is written from a template and a text.
PAGES = (
    ("en", "index.html", "index.html"),
    ("en", "legal.html", "legal.html"),
    ("de", "index.html", "de/index.html"),
    ("de", "legal.html", "de/legal.html"),
)
SITE_URL = "https://paravent.org/"
# German month names, for the German pages' dates.
MONTHS_DE = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)
# What the zip leaves out: the build itself and its notes.
NOT_SHIPPED = {
    "build.py",
    "shots.py",
    "README.md",
    "content",
    "templates",
    "dist",
    ".DS_Store",
    "__pycache__",
    ".gitkeep",
    ".gitignore",
}
SYNCED = {
    ROOT / "static/css/tokens.css": SITE / "assets/css/paravent/tokens.css",
    ROOT / "static/css/components.css": SITE / "assets/css/paravent/components.css",
    # The Tome theme the site wears: its tokens, fonts (with their OFL licences) and textures.
    ROOT / "app/themes/tome/tokens.css": SITE / "assets/css/tome/tokens.css",
    ROOT / "app/themes/tome/fonts": SITE / "assets/css/tome/fonts",
    ROOT / "app/themes/tome/images": SITE / "assets/css/tome/images",
    ROOT / "static/icons/svg/paravent-mark.svg": SITE / "assets/img/paravent-mark.svg",
    ROOT / "static/icons/svg/paravent-icon.svg": SITE / "assets/img/paravent-icon.svg",
    ROOT / "static/icons/favicon/favicon.svg": SITE / "favicon.svg",
    ROOT / "static/icons/favicon/favicon.ico": SITE / "favicon.ico",
    ROOT / "static/icons/app-icon/apple-touch-icon.png": SITE / "apple-touch-icon.png",
    # The licences of what the site carries from others (icons, fonts, SRD material).
    ROOT / "NOTICE.md": SITE / "notice.txt",
}
# "What's new" is opt-in: a commit appears when its message carries a ``News:`` trailer,
# the line the site shows, and ``News-de:`` for the German page (English when missing):
#
#     Claude can revise an accepted creature: a draft copy under the same name
#
#     News: Claude can suggest changes to a creature you accepted.
#     News-de: Claude kann Änderungen an einer angenommenen Kreatur vorschlagen.
#
# Commits up to and including LEGACY_UNTIL came before the trailer, so their subject is the
# line, unless QUIET (or a "Website:" subject) says it tells a reader nothing. The release
# commit that started the public history ("Paravent 0.1.x") says nothing either; the
# content files' pinned item tells that story instead.
LEGACY_UNTIL = "dc4fba640fbadd1c74326a53fb3e8b9ee81b75f2"
QUIET = re.compile(
    r"^(?:(?:merge|bump|wip|fixup|squash|revert|format|lint|typo)\b|paravent \d|website:)",
    re.IGNORECASE,
)
NEWS_LOG = (
    "--pretty=format:%H%x1f%ad%x1f%s"
    "%x1f%(trailers:key=News,valueonly,separator=%x1d)"
    "%x1f%(trailers:key=News-de,valueonly,separator=%x1d)%x1e"
)
UPDATES = 14
SYMBOL = re.compile(r'<symbol id="(i-[\w-]+)".*?</symbol>', re.DOTALL)


def git(*args: str) -> str:
    """A git command's output in the repository, empty when git is not there."""
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, check=True, text=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return ""


def version() -> str:
    found = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    return found.group(1) if found else "0"


def replace_block(text: str, name: str, body: str) -> str:
    """Put ``body`` between the page's ``<!-- name:start -->`` and ``<!-- name:end -->``."""
    pattern = re.compile(rf"(<!-- {name}:start -->)(.*?)(<!-- {name}:end -->)", re.DOTALL)
    if not pattern.search(text):
        return text
    return pattern.sub(lambda m: f"{m.group(1)}\n{body}\n{m.group(3)}", text, count=1)


# -- 0. write: the text into the templates ----------------------------------------------


class ContentError(Exception):
    """A text file or template that cannot be read: what and where."""


# A text: a string, a list of strings (paragraphs, or lines), or a list of entries.
Value = str | list[str] | list[dict[str, Any]]
Content = dict[str, Value]


def read_content(path: Path) -> Content:
    """A text file's fields: ``{"section": {"field": value}}`` → ``"section.field"``.

    A value is a string, a list of strings (paragraphs where the page shows paragraphs,
    lines in a title or the address) or a list of entries (``{"title", "text", …}``).
    Keys starting with ``_`` are notes for the person editing, and skipped."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ContentError(f"{path.name}:{exc.lineno}: not valid JSON ({exc.msg})") from exc
    if not isinstance(data, dict):
        raise ContentError(f"{path.name}: the file holds an object of sections")
    fields: Content = {}
    for section, body in data.items():
        if section.startswith("_"):
            continue
        if not isinstance(body, dict):
            raise ContentError(f"{path.name}: section {section!r} is not an object")
        for name, value in body.items():
            if name.startswith("_"):
                continue
            if isinstance(value, list) and value and isinstance(value[0], dict):
                value = [{k: v for k, v in e.items() if not k.startswith("_")} for e in value]
            elif not isinstance(value, (str, list)):
                raise ContentError(f"{path.name}: {section}.{name} is neither text nor a list")
            fields[f"{section}.{name}"] = value
    return fields


INLINE = [
    (re.compile(r"\[\[(.+?)\]\]"), r'<span class="todo">\1</span>'),
    (re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)"), r'<a href="\2">\1</a>'),
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"), r"<em>\1</em>"),
    (re.compile(r"==(.+?)=="), r'<span class="ink">\1</span>'),
    (re.compile(r"`(.+?)`"), r"<code>\1</code>"),
]


def inline(text: str) -> str:
    """One run of text: escaped, its marks made HTML, its line breaks kept."""
    out = html.escape(text.strip(), quote=False)
    for pattern, replacement in INLINE:
        out = pattern.sub(replacement, out)
    return "<br>\n".join(line.strip() for line in out.splitlines())


def paragraphs(text: str, indent: str = "        ") -> str:
    """Paragraphs, split at blank lines; a paragraph's lines run on."""
    blocks = [" ".join(b.split()) for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    return "\n".join(f"{indent}<p>{inline(block)}</p>" for block in blocks)


def plain(text: str) -> str:
    """Text for an attribute: the marks removed, escaped with quotes."""
    out = re.sub(r"\[\[(.+?)\]\]|\[([^\]]+)\]\([^)]*\)", lambda m: m.group(1) or m.group(2), text)
    out = re.sub(r"\*\*|\*|==|`", "", out)
    return html.escape(" ".join(out.split()), quote=True)


SECTION = re.compile(r"\{\{([#?^])([\w.=-]+)\}\}\n?(.*?)\{\{/\2\}\}\n?", re.S)
VALUE = re.compile(r"\{\{([\w.-]+)(?:\|(\w+))?\}\}")


def fill(template: str, scope: list[dict[str, Any]], where: str) -> str:
    """The template with its sections and values from the innermost scope outwards."""

    def look(name: str) -> Any:
        for layer in reversed(scope):
            if name in layer:
                return layer[name]
        raise ContentError(f"{where}: no text {name!r} (add a field for it)")

    def section(match: re.Match[str]) -> str:
        kind, name, body = match.groups()
        if kind == "#":
            entries = look(name)
            if not isinstance(entries, list) or (entries and not isinstance(entries[0], dict)):
                raise ContentError(f"{where}: {name!r} is not a list of entries")
            return "".join(
                fill(body, [*scope, {**entry, "n": f"{i:02d}"}], where)
                for i, entry in enumerate(entries, 1)
            )
        key, _, wanted = name.partition("=")
        try:
            value = look(key)
        except ContentError:
            value = ""
        hit = str(value) == wanted if wanted else bool(value)
        return fill(body, scope, where) if hit == (kind == "?") else ""

    def value(match: re.Match[str]) -> str:
        name, mode = match.group(1), match.group(2) or "text"
        found = look(name)
        if isinstance(found, list) and found and isinstance(found[0], dict):
            raise ContentError(f"{where}: {name!r} is a list of entries; use {{{{#{name}}}}}")
        # A list of strings: its entries; a string: its lines.
        lines = (
            [str(x).strip() for x in found if str(x).strip()]
            if isinstance(found, list)
            else [line.strip() for line in str(found).splitlines() if line.strip()]
        )
        text = "\n".join(lines) if isinstance(found, list) else str(found)
        if mode == "raw":
            return text
        if mode == "attr":
            return plain(text)
        if mode == "p":  # a list: one paragraph each; a string: split at blank lines
            if isinstance(found, list):
                return "\n".join(f"        <p>{inline(' '.join(x.split()))}</p>" for x in lines)
            return paragraphs(text)
        if mode == "lines":  # one value per entry, as JSON for an attribute
            return html.escape(
                json.dumps([html.unescape(plain(line)) for line in lines], ensure_ascii=False),
                quote=True,
            )
        if mode == "last":  # the last entry, where a rotation stops
            return inline(lines[-1]) if lines else ""
        return inline(text)

    return VALUE.sub(value, SECTION.sub(section, template))


def write(lang: str, template: str, page: str) -> str:
    """A page written from its template and its language's text."""
    content = read_content(SITE / "content" / f"{lang}.json")
    root = "../" * page.count("/")
    name = Path(page).name
    english = "" if name == "index.html" else name
    pages = {"en": english, "de": "de/" + english}
    own = english or "./"  # the same page in the language it is in
    computed = {
        "root": root,
        "url_en": SITE_URL + pages["en"],
        "url_de": SITE_URL + pages["de"],
        "url_here": SITE_URL + pages[lang],
        "href_en": own if lang == "en" else (root + pages["en"]) or "./",
        "href_de": own if lang == "de" else root + pages["de"],
        "current_en": ' aria-current="page"' if lang == "en" else "",
        "current_de": ' aria-current="page"' if lang == "de" else "",
    }
    source = (SITE / "templates" / template).read_text(encoding="utf-8")
    return fill(source, [computed, content], f"templates/{template} ({lang}.json)")


# -- 1. sync ----------------------------------------------------------------------------


def sync() -> None:
    for source, target in SYNCED.items():
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        elif source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def icons(text: str) -> str:
    """The sprite of the icons the page names, from the app's own."""
    sprite = ROOT / "static/icons.svg"
    if not sprite.exists():
        return text
    wanted = set(re.findall(r'href="#(i-[\w-]+)"', text))
    symbols = [m.group(0) for m in SYMBOL.finditer(sprite.read_text()) if m.group(1) in wanted]
    body = (
        '<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">'
        + "".join(symbols)
        + "</svg>"
    )
    return replace_block(text, "icons", body)


# -- 2. update --------------------------------------------------------------------------


def news(log: str, lang: str = "en", limit: int = UPDATES) -> list[tuple[str, str]]:
    """``(day, line)`` of the commits a reader cares about, newest first, from ``git log``
    in :data:`NEWS_LOG`'s format: a commit's ``News:`` (``News-de:`` on the German page),
    or before :data:`LEGACY_UNTIL` its subject unless :data:`QUIET`."""
    rows: list[tuple[str, str]] = []
    legacy = False
    for record in log.split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) != 5:
            continue
        commit, day, subject, english, german = parts
        legacy = legacy or commit == LEGACY_UNTIL
        first = [x.strip() for x in (german if lang == "de" else "").split("\x1d") if x.strip()]
        first = first or [x.strip() for x in english.split("\x1d") if x.strip()]
        if first:
            rows.append((day, first[0]))
        elif legacy and subject and not QUIET.match(subject):
            rows.append((day, subject))
        if len(rows) == limit:
            break
    return rows


def updates(lang: str = "en") -> str:
    """The newest commits a reader cares about, as list items, newest first."""
    rows = news(git("log", "--no-merges", "--date=short", NEWS_LOG), lang)
    items = [
        f'      <li class="update reveal"><time datetime="{day}">{_day(day, lang)}</time>'
        f"<span>{html.escape(subject, quote=False)}</span></li>"
        for day, subject in rows
    ]
    return "\n".join(items)


def _day(day: str | date, lang: str = "en") -> str:
    try:
        when = day if isinstance(day, date) else date.fromisoformat(day)
    except ValueError:
        return str(day)
    if lang == "de":
        return f"{when.day}. {MONTHS_DE[when.month - 1]} {when.year}"
    return when.strftime("%d %b %Y")


def facts(lang: str = "en") -> dict[str, str]:
    tests = sum(
        len(re.findall(r"^\s*(?:async )?def test_", path.read_text(errors="ignore"), re.M))
        for path in (ROOT / "tests").glob("test_*.py")
    )

    def number(n: int) -> str:
        return f"{n:,}".replace(",", ".") if lang == "de" else f"{n:,}"

    return {
        "version": version(),
        "tests": number(tests),
        "updated": _day(date.today(), lang),
    }


def update(text: str, lang: str = "en") -> str:
    text = replace_block(text, "updates", updates(lang))
    for name, value in facts(lang).items():
        text = replace_block(text, f"fact-{name}", value)
    return text


# -- 3. pack ----------------------------------------------------------------------------

# Elements that never close; every other one must.
VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source",
    "track", "wbr",
}  # fmt: skip
# Elements a browser closes by itself where the next one starts.
SELF_CLOSING = {"p", "li", "dt", "dd", "option", "tr", "td", "th"}
# Larger than this, a picture or file is worth a second look before it goes up.
HEAVY = {".webp": 400_000, ".jpg": 400_000, ".png": 400_000, ".svg": 200_000}
HEAVY_ANY = 1_500_000
CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")


class Page(HTMLParser):
    """What a page links to, names and nests, read once."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: list[str] = []
        self.links: list[tuple[str, str]] = []  # (attribute, value)
        self.images: list[tuple[str, bool]] = []  # (src, has an alt attribute)
        self.unbalanced: list[str] = []
        self._open: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v or "" for k, v in attrs}
        if "id" in values:
            self.ids.append(values["id"])
        for name in ("href", "src", "data-dark"):
            if name in values and not (tag == "link" and values.get("rel") in ("alternate",)):
                self.links.append((name, values[name]))
        if tag == "img":
            # An empty alt is right for a decorative picture; a missing one never is.
            self.images.append((values.get("src", ""), "alt" in values))
        if tag not in VOID:
            self._open.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self._open and self._open[-1] == tag:
            self._open.pop()

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID:
            return
        while self._open and self._open[-1] != tag and self._open[-1] in SELF_CLOSING:
            self._open.pop()
        if self._open and self._open[-1] == tag:
            self._open.pop()
        else:
            self.unbalanced.append(f"</{tag}> closes <{self._open[-1] if self._open else '-'}>")

    def close(self) -> None:
        super().close()
        left = [t for t in self._open if t not in SELF_CLOSING]
        if left:
            self.unbalanced.append(f"never closed: <{'>, <'.join(left)}>")


def _read(path: Path) -> Page:
    page = Page()
    page.feed(path.read_text(encoding="utf-8"))
    page.close()
    return page


def verify(site: Path) -> tuple[list[str], list[str]]:
    """The site as it will be served: ``(errors, warnings)``. Errors are what a visitor
    would meet as broken (a dead link or anchor, a missing picture or file, a picture
    without alt text, a duplicated id, tags that do not close); warnings are files heavy
    enough to slow a phone down."""
    errors: list[str] = []
    warnings: list[str] = []
    pages = {path.resolve(): _read(path) for path in sorted(site.rglob("*.html"))}

    def where(path: Path) -> str:
        return str(path.relative_to(site.resolve()))

    for path, page in pages.items():
        name = where(path)
        for problem in page.unbalanced:
            errors.append(f"{name}: {problem}")
        seen: set[str] = set()
        for ident in page.ids:
            if ident in seen:
                errors.append(f"{name}: id {ident!r} twice")
            seen.add(ident)
        for src, has_alt in page.images:
            if not has_alt:
                errors.append(f"{name}: picture without alt text: {src}")
        for attribute, value in page.links:
            parts = urlsplit(value)
            if parts.scheme or value.startswith(("//", "mailto:", "tel:", "data:")):
                continue
            target = path if not parts.path else (path.parent / unquote(parts.path)).resolve()

            if parts.path.endswith("/") or target.is_dir():
                target = target / "index.html"
            if not target.is_file() or site.resolve() not in target.parents:
                errors.append(f"{name}: {attribute}={value!r} leads to no file")
                continue
            if parts.fragment and target.suffix == ".html":
                linked = pages.get(target.resolve())
                if linked is not None and parts.fragment not in linked.ids:
                    errors.append(f"{name}: {value!r} names no element on that page")

    for css in sorted(site.rglob("*.css")):
        for value in CSS_URL.findall(css.read_text(encoding="utf-8")):
            if value.startswith(("data:", "http:", "https:", "#")):
                continue
            if not (css.parent / unquote(urlsplit(value).path)).is_file():
                errors.append(f"{where(css.resolve())}: url({value}) leads to no file")

    for path in sorted(site.rglob("*")):
        if path.is_file():
            size = path.stat().st_size
            limit = HEAVY.get(path.suffix.lower(), HEAVY_ANY)
            if size > limit:
                warnings.append(f"{where(path.resolve())}: {size // 1024} KB")
    return errors, warnings


def pack() -> Path | None:
    """The site copied as it ships, checked, and zipped; None when the check found
    something broken (the copy stays in ``dist/site`` to look at)."""
    out = SITE / "dist"
    site = out / "site"
    if site.exists():
        shutil.rmtree(site)
    shutil.copytree(SITE, site, ignore=lambda _d, names: [n for n in names if n in NOT_SHIPPED])
    errors, warnings = verify(site)
    for warning in warnings:
        print("Heavy:", warning)
    if errors:
        print(f"Not packed, {len(errors)} problem(s) in the site:")
        for error in errors:
            print("  ", error)
        return None
    archive = out / f"paravent-website-{version()}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(site.rglob("*")):
            if path.is_file():
                zipped.write(path, path.relative_to(site))
    return archive


def render(lang: str, template: str, page: str) -> str:
    """A page written from its text, with its icons, and a start page with its news and
    numbers, in its language."""
    text = icons(write(lang, template, page))
    if template == "index.html":
        text = update(text, lang)
    return text


def sitemap() -> str:
    """Every page with its other language, dated by the last change to the texts."""
    day = git("log", "-1", "--format=%cs", "--", "website/content", "website/templates").strip()
    pairs = {
        template: {lang: page for lang, t, page in PAGES if t == template}
        for _l, template, _p in PAGES
    }
    rows = []
    for _lang, template, page in PAGES:
        url = SITE_URL + page.removesuffix("index.html")
        others = "".join(
            f'\n    <xhtml:link rel="alternate" hreflang="{other}" '
            f'href="{SITE_URL + path.removesuffix("index.html")}"/>'
            for other, path in pairs[template].items()
        )
        dated = f"\n    <lastmod>{day}</lastmod>" if day else ""
        rows.append(f"  <url>\n    <loc>{url}</loc>{dated}{others}\n  </url>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n' + "\n".join(rows) + "\n</urlset>\n"
    )


def extras() -> dict[str, str]:
    """The files beside the pages that the build writes too."""
    return {
        "sitemap.xml": sitemap(),
        "robots.txt": f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}sitemap.xml\n",
    }


def _undated(text: str) -> str:
    return re.sub(r"<!-- fact-updated:start -->.*?<!-- fact-updated:end -->", "", text, flags=re.S)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", action="store_true", help="also build the zip to upload")
    parser.add_argument("--check", action="store_true", help="fail if the pages are stale")
    parser.add_argument(
        "--deploy", action="store_true", help="build, check, zip and upload over SFTP"
    )
    parser.add_argument(
        "--preview", action="store_true", help="list what an upload would change, change nothing"
    )
    args = parser.parse_args()
    if args.deploy or args.preview:
        args.dist = True
    if not args.check:
        sync()
    stale = []
    for lang, template, name in PAGES:
        page = SITE / name
        before = page.read_text(encoding="utf-8") if page.exists() else ""
        try:
            after = render(lang, template, name)
        except ContentError as exc:
            print("Cannot build:", exc)
            return 1
        # The date always moves; a page is stale only when something else changed.
        if _undated(after) != _undated(before) or (after != before and not args.check):
            stale.append(name)
            if not args.check:
                page.parent.mkdir(parents=True, exist_ok=True)
                page.write_text(after, encoding="utf-8")
    for name, after in extras().items():
        page = SITE / name
        if not page.exists() or page.read_text(encoding="utf-8") != after:
            stale.append(name)
            if not args.check:
                page.write_text(after, encoding="utf-8")
    if args.check:
        if stale:
            print("Out of date:", ", ".join(stale), "— run python website/build.py")
            return 1
        return 0
    print("Updated:", ", ".join(stale) if stale else "nothing to change")
    if args.dist:
        archive = pack()
        if archive is None:
            return 1
        print("Zip:", archive.relative_to(ROOT))
        if args.deploy or args.preview:
            return deploy(archive.parent / "site", preview=args.preview)
    return 0


# -- 4. deploy --------------------------------------------------------------------------

# On the server, beside the site: every file an upload put there, with its fingerprint. An
# upload sends what changed and removes only files listed here, so what the host keeps in
# the folder (cgi-bin, .well-known, its quota file) is never touched.
MANIFEST = ".paravent-files.json"
# More than this many files to delete means something is off; nothing is deleted then.
MAX_DELETE = 200
# user@host:/path or host:path (SFTP), or a local folder for trying it out.
REMOTE = re.compile(r"^(?P<host>(?:[\w.+-]+@)?[\w.-]+):(?P<path>.+)$")


def setting(name: str) -> str:
    """A setting from the environment, else from the project's ``.env`` (never printed)."""
    if os.environ.get(name):
        return os.environ[name].strip()
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key.strip() == name and not line.lstrip().startswith("#"):
                return value.strip().strip("\"'")
    return ""


def target_problem(target: str) -> str | None:
    """Why ``target`` is not a folder to upload into, or None. It must name the site's own
    folder, ending in ``/``, never a root or home folder."""
    if not target:
        return "PARAVENT_SITE_TARGET is not set (user@host:/path/to/paravent.org/)"
    found = REMOTE.match(target)
    path = found.group("path") if found else target
    if not found and not Path(path).is_absolute():
        return "PARAVENT_SITE_TARGET is neither user@host:/path/ nor an absolute folder"
    if not path.endswith("/"):
        return "PARAVENT_SITE_TARGET must end with / (the folder itself, not one inside it)"
    if path.strip("/~. ") == "":
        return "PARAVENT_SITE_TARGET names a root or home folder, not the site's own folder"
    return None


def fingerprints(site: Path) -> dict[str, str]:
    """Every file of the site by its path, with a hash of its content."""
    return {
        path.relative_to(site).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(site.rglob("*"))
        if path.is_file() and path.name != ".DS_Store"
    }


@dataclass(frozen=True)
class Plan:
    """What an upload does: files to send (new or changed), files to remove (sent by an
    earlier upload, no longer in the site), and the folders the sent files need."""

    send: tuple[str, ...]
    remove: tuple[str, ...]
    folders: tuple[str, ...]


def plan(now: Mapping[str, str], before: Mapping[str, str]) -> Plan:
    send = tuple(path for path, digest in now.items() if before.get(path) != digest)
    remove = tuple(sorted(path for path in before if path not in now))
    folders = sorted({str(parent) for path in send for parent in PurePosixPath(path).parents})
    return Plan(send, remove, tuple(f for f in folders if f != "."))


def _quoted(path: str) -> str:
    return '"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"'


def sftp_batch(site: Path, base: str, steps: Plan, manifest: Path) -> list[str]:
    """The SFTP commands for ``steps``: folders made (an existing one is fine), files sent
    with readable modes, old files removed, and the new list of files last."""
    lines = []
    for folder in steps.folders:
        lines += [f"-mkdir {_quoted(base + folder)}", f"-chmod 755 {_quoted(base + folder)}"]
    for path in steps.send:
        lines += [
            f"put {_quoted(str(site / path))} {_quoted(base + path)}",
            f"chmod 644 {_quoted(base + path)}",
        ]
    lines += [f"-rm {_quoted(base + path)}" for path in steps.remove]
    lines += [f"put {_quoted(str(manifest))} {_quoted(base + MANIFEST)}"]
    return lines


def _sftp(host: str, lines: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run SFTP commands non-interactively; the login is the one ~/.ssh gives."""
    with tempfile.NamedTemporaryFile("w", suffix=".sftp", delete=False, encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    try:
        return subprocess.run(
            ["sftp", "-b", f.name, "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", host],
            capture_output=True,
            text=True,
        )
    finally:
        Path(f.name).unlink(missing_ok=True)


def _uploaded_before(target: str) -> dict[str, str]:
    """The list of files the last upload left on the server; empty before the first."""
    found = REMOTE.match(target)
    if found is None:
        listed = Path(target) / MANIFEST
        raw = listed.read_text(encoding="utf-8") if listed.is_file() else ""
    else:
        with tempfile.TemporaryDirectory() as folder:
            local = Path(folder) / MANIFEST
            _sftp(
                found.group("host"),
                [f"-get {_quoted(found.group('path') + MANIFEST)} {_quoted(str(local))}"],
            )
            raw = local.read_text(encoding="utf-8") if local.is_file() else ""
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    files = data.get("files") if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in files.items()} if isinstance(files, dict) else {}


def _apply_locally(site: Path, folder: Path, steps: Plan, manifest: Path) -> None:
    """The same upload into a local folder (to try the build out, and for its tests)."""
    for path in steps.send:
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(site / path, folder / path)
    for path in steps.remove:
        (folder / path).unlink(missing_ok=True)
    shutil.copyfile(manifest, folder / MANIFEST)


def deploy(site: Path, *, preview: bool) -> int:
    """Send the checked copy in ``dist/site`` to the server, or say what that would do."""
    target = setting("PARAVENT_SITE_TARGET")
    problem = target_problem(target)
    if problem:
        print("Not uploaded:", problem)
        return 1
    found = REMOTE.match(target)
    if found is not None and shutil.which("sftp") is None:
        print("Not uploaded: sftp is not installed")
        return 1
    now, before = fingerprints(site), _uploaded_before(target)
    steps = plan(now, before)
    if len(steps.remove) > MAX_DELETE:
        print(f"Not uploaded: {len(steps.remove)} files to delete, more than {MAX_DELETE}.")
        return 1
    print(f"{len(steps.send)} to send, {len(steps.remove)} to delete, of {len(now)} files.")
    if not before:
        print("First upload: nothing on the server is deleted; files already there are replaced.")
    if preview:
        for path in steps.send:
            print("  send  ", path)
        for path in steps.remove:
            print("  delete", path)
        print("Nothing was changed (preview).")
        return 0
    with tempfile.TemporaryDirectory() as folder:
        manifest = Path(folder) / MANIFEST
        manifest.write_text(json.dumps({"files": now}, indent=1, sort_keys=True), encoding="utf-8")
        if found is None:
            _apply_locally(site, Path(target), steps, manifest)
        else:
            done = _sftp(
                found.group("host"), sftp_batch(site, found.group("path"), steps, manifest)
            )
            if done.returncode:
                print(done.stderr.strip()[-600:])
                print(
                    "sftp stopped; part of the site may be new. Run --deploy again: "
                    "the list of files is written last, so nothing sent is lost track of."
                )
                return done.returncode
    print("The site is live.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
