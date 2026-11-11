"""The website's build (website/README.md): the text files, the template language, the
files written beside the pages, and the site as committed."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SITE = Path(__file__).resolve().parent.parent / "website"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("website_build", SITE / "build.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build = _load()


def fill(template: str, **fields: Any) -> str:
    return str(build.fill(template, [fields], "test"))


def content(lang: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((SITE / "content" / f"{lang}.json").read_text("utf-8"))
    return data


# -- the text files ------------------------------------------------------------------------


def test_a_text_file_skips_notes_and_flattens_its_sections(tmp_path: Path) -> None:
    path = tmp_path / "en.json"
    path.write_text(
        json.dumps(
            {
                "_comment": "for the editor",
                "hero": {"_note": "skipped", "title": "Hi", "lede": ["One", "Two"]},
                "features": {"items": [{"title": "A", "_hint": "gone", "text": "B"}]},
            }
        ),
        encoding="utf-8",
    )
    assert build.read_content(path) == {
        "hero.title": "Hi",
        "hero.lede": ["One", "Two"],
        "features.items": [{"title": "A", "text": "B"}],
    }


def test_broken_json_names_the_file_and_line(tmp_path: Path) -> None:
    path = tmp_path / "de.json"
    path.write_text('{\n  "hero": {\n    "title": "Hallo",\n  }\n}\n', encoding="utf-8")
    with pytest.raises(build.ContentError, match=r"de\.json:4: not valid JSON"):
        build.read_content(path)


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([1, 2], "an object of sections"),
        ({"hero": "text"}, "section 'hero' is not an object"),
        ({"hero": {"count": 3}}, "hero.count is neither text nor a list"),
    ],
)
def test_a_text_file_of_the_wrong_shape_says_what(
    tmp_path: Path, data: object, message: str
) -> None:
    path = tmp_path / "en.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(build.ContentError, match=re.escape(message)):
        build.read_content(path)


# -- the template language -------------------------------------------------------------------


def test_inline_marks_become_html_and_the_rest_is_escaped() -> None:
    text = "*a* **b** ==c== `d` [e](https://x.org) [[f]] <g> & h"
    assert build.inline(text) == (
        '<em>a</em> <strong>b</strong> <span class="ink">c</span> <code>d</code> '
        '<a href="https://x.org">e</a> <span class="todo">f</span> &lt;g&gt; &amp; h'
    )


def test_inline_keeps_line_breaks_and_leaves_lone_stars() -> None:
    assert build.inline("one\ntwo") == "one<br>\ntwo"
    assert build.inline("2 * 3 * 4") == "2 * 3 * 4"


def test_values_by_mode() -> None:
    assert fill("{{t}}", t="*x*") == "<em>x</em>"
    assert fill("{{t|raw}}", t="*x*") == "*x*"
    assert fill("{{t|attr}}", t='Say "*hi*" [there](u)') == "Say &quot;hi&quot; there"
    assert fill("{{t|last}}", t=["one", "two", "three"]) == "three"
    assert json.loads(build.html.unescape(fill("{{t|lines}}", t=["a *b*", "c"]))) == [
        "a b",
        "c",
    ]


def test_paragraphs_from_a_list_or_from_blank_lines() -> None:
    assert fill("{{t|p}}", t=["One\n run on", "Two"]) == (
        "        <p>One run on</p>\n        <p>Two</p>"
    )
    assert fill("{{t|p}}", t="One\n\nTwo") == "        <p>One</p>\n        <p>Two</p>"


def test_a_list_of_entries_repeats_with_numbers() -> None:
    template = "{{#items}}{{n}} {{title}}{{?kind=wide}} wide{{/kind=wide}}; {{/items}}"
    items = [{"title": "A", "kind": "wide"}, {"title": "B", "kind": "phone"}]
    assert fill(template, items=items) == "01 A wide; 02 B; "


def test_conditions_on_a_value_or_its_absence() -> None:
    assert fill("{{?lang=en}}E{{/lang=en}}{{^lang=en}}D{{/lang=en}}", lang="de") == "D"
    assert fill("{{?lang=en}}E{{/lang=en}}{{^lang=en}}D{{/lang=en}}", lang="en") == "E"
    assert fill("{{?missing}}never{{/missing}}") == ""
    assert fill("{{^missing}}shown{{/missing}}") == "shown"


def test_an_entry_sees_the_fields_around_it() -> None:
    assert fill("{{#items}}{{lang}}:{{title}} {{/items}}", lang="de", items=[{"title": "A"}]) == (
        "de:A "
    )


def test_a_missing_text_names_itself_and_where() -> None:
    with pytest.raises(build.ContentError, match=r"test: no text 'hero\.mini'"):
        fill("{{hero.mini}}")


def test_a_list_of_entries_is_not_a_value() -> None:
    with pytest.raises(build.ContentError, match="use {{#items}}"):
        fill("{{items}}", items=[{"title": "A"}])
    with pytest.raises(build.ContentError, match="not a list of entries"):
        fill("{{#items}}{{/items}}", items="text")


def test_a_marked_block_is_replaced_and_a_missing_one_left_alone() -> None:
    page = "a <!-- news:start -->old<!-- news:end --> b"
    assert build.replace_block(page, "news", "new") == (
        "a <!-- news:start -->\nnew\n<!-- news:end --> b"
    )
    assert build.replace_block("plain", "news", "new") == "plain"


def test_dates_in_each_language() -> None:
    assert build._day("2026-03-07", "en") == "07 Mar 2026"
    assert build._day("2026-03-07", "de") == "7. März 2026"
    assert build._day("someday", "de") == "someday"


# -- beside the pages ------------------------------------------------------------------------


def test_the_sitemap_lists_every_page_with_its_other_language() -> None:
    root = ET.fromstring(build.sitemap())
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9", "x": "http://www.w3.org/1999/xhtml"}
    urls = root.findall("s:url", ns)
    locations = [u.findtext("s:loc", namespaces=ns) for u in urls]
    assert locations == [
        "https://paravent.org/",
        "https://paravent.org/legal.html",
        "https://paravent.org/de/",
        "https://paravent.org/de/legal.html",
    ]
    for url in urls:
        langs = {link.get("hreflang") for link in url.findall("x:link", ns)}
        assert langs == {"en", "de"}


def test_robots_names_the_sitemap() -> None:
    assert "Sitemap: https://paravent.org/sitemap.xml" in build.extras()["robots.txt"]


# -- the site as committed -------------------------------------------------------------------


def _shape(value: Any) -> Any:
    """A text's structure without its words: keys, list lengths, and the fields of an entry
    that are not language (pictures, icons, kinds)."""
    if isinstance(value, dict):
        return {
            k: v if k in {"file", "icon", "kind"} else _shape(v)
            for k, v in value.items()
            if not k.startswith("_") and k not in {"lang", "locale"}
        }
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            return [_shape(v) for v in value]
        return "list"
    return "text"


def test_both_languages_have_the_same_texts_and_pictures() -> None:
    hero = ("hero", "title_words")  # each language rotates its own words
    en, de = content("en"), content("de")
    for data in (en, de):
        data[hero[0]].pop(hero[1], None)
    assert _shape(en) == _shape(de)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() if not k.startswith("_") for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


@pytest.mark.parametrize("lang", ["en", "de"])
def test_the_texts_use_no_dashes(lang: str) -> None:
    """The site's voice: commas and full stops, never an em or en dash."""
    found = [s for s in _strings(content(lang)) if re.search(r"[—–]| - ", s)]
    assert found == []


@pytest.mark.parametrize("lang", ["en", "de"])
def test_every_screenshot_exists_in_light_and_dark(lang: str) -> None:
    folder = SITE / "assets/img/screens" / lang
    texts = content(lang)
    for item in texts["screens"]["items"] + texts["evening"]["steps"]:
        assert (folder / item["file"]).is_file(), item["file"]
        assert (folder / "dark" / item["file"]).is_file(), f"dark/{item['file']}"
    assert (SITE / f"assets/img/share-{lang}.jpg").is_file()


@pytest.mark.parametrize(("lang", "template", "page"), build.PAGES)
def test_every_page_renders_with_every_icon(lang: str, template: str, page: str) -> None:
    text = build.render(lang, template, page)
    used = set(re.findall(r'href="#(i-[\w-]+)"', text))
    defined = set(re.findall(r'<symbol id="(i-[\w-]+)"', text))
    assert used and used <= defined, sorted(used - defined)
    assert "{{" not in text


def _settled(text: str) -> str:
    """A page without what moves with every commit: the news and the numbers."""
    return re.sub(r"<!-- (updates|fact-[\w]+):start -->.*?<!-- \1:end -->", "", text, flags=re.S)


@pytest.mark.parametrize(("lang", "template", "page"), build.PAGES)
def test_the_committed_pages_are_built_from_their_text(lang: str, template: str, page: str) -> None:
    """A change to a template or a text file came with ``python website/build.py``."""
    committed = (SITE / page).read_text(encoding="utf-8")
    assert _settled(build.render(lang, template, page)) == _settled(committed)


@pytest.mark.parametrize("name", ["sitemap.xml", "robots.txt"])
def test_the_files_beside_the_pages_are_current(name: str) -> None:
    assert (SITE / name).read_text(encoding="utf-8") == build.extras()[name]


# -- the check before packing ----------------------------------------------------------------


def _site(tmp_path: Path, pages: dict[str, str], files: dict[str, bytes] | None = None) -> Path:
    for name, text in pages.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    for name, data in (files or {}).items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(data)
    return tmp_path


def test_a_sound_site_passes(tmp_path: Path) -> None:
    site = _site(
        tmp_path,
        {
            "index.html": '<html><body><p>Hi<p id="a">See <a href="de/#b">this</a>'
            '<img src="x.webp" alt=""></body></html>',
            "de/index.html": '<html><body><section id="b"><a href="../#a">a</a>'
            '<a href="https://example.org">out</a></section></body></html>',
            "assets/site.css": "body{ background:url(img/p.svg) }",
        },
        {"x.webp": b"x", "assets/img/p.svg": b"<svg/>"},
    )
    assert build.verify(site) == ([], [])


def test_the_check_names_what_a_visitor_would_meet_broken(tmp_path: Path) -> None:
    site = _site(
        tmp_path,
        {
            "index.html": '<html><body><div id="a"></div><div id="a"></div>'
            '<a href="gone.html">x</a><a href="#nowhere">y</a>'
            '<img src="pic.webp"><img src="missing.webp" alt="m">'
            "<section><div></section></body></html>",
            "assets/site.css": "body{ background:url('img/none.svg') }",
        },
        {"pic.webp": b"x"},
    )
    errors, _warnings = build.verify(site)
    joined = "\n".join(errors)
    assert "id 'a' twice" in joined
    assert "href='gone.html' leads to no file" in joined
    assert "'#nowhere' names no element" in joined
    assert "picture without alt text: pic.webp" in joined
    assert "src='missing.webp' leads to no file" in joined
    assert "</section> closes <div>" in joined
    assert "url(img/none.svg) leads to no file" in joined


def test_heavy_files_are_warned_about_not_refused(tmp_path: Path) -> None:
    site = _site(tmp_path, {"index.html": "<p>x</p>"}, {"big.webp": b"0" * 500_000})
    assert build.verify(site) == ([], ["big.webp: 488 KB"])


def test_the_site_as_it_ships_passes_the_check(tmp_path: Path) -> None:
    shipped = tmp_path / "site"
    shutil.copytree(
        SITE, shipped, ignore=lambda _d, names: [n for n in names if n in build.NOT_SHIPPED]
    )
    errors, _warnings = build.verify(shipped)
    assert errors == []
