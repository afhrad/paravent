"""Paravent Markdown: CommonMark plus what an adventure needs (ADVENTURES.md §5).

Every text the gamemaster writes is rendered in it — chapters, adventures, worlds, and
the notes of NPCs, treasure, encounters and sessions (``app/routes/paravent_markdown.py``
renders a text where its object is placed).

``render_markdown(text, ctx)`` turns a chapter body into HTML and its table of contents.
The dialect is CommonMark with GFM tables and strikethrough, raw HTML **off** (escaped,
never passed through), and four additions:

* **5eTools tags** — ``{@creature goblin}``, ``{@dice 2d6}`` — rendered by
  :mod:`app.ui.tags` exactly as reference text is, against the adventure's books. The tag
  rule runs before emphasis, so an underscore or asterisk inside a tag is never read as
  Markdown; ``\\{@`` writes a literal brace.
* **Paravent's own tags** — ``{@npc Maren Holt|Maren}``, ``{@encounter …}``,
  ``{@treasure …}``, ``{@character …}``, ``{@chapter …}``, ``{@place …}``. Slot 2 is the
  *label*: these
  have no source, unlike 5eTools tags. ``{@adventure}`` is not one of them — 5eTools
  already uses that name for a link into a published adventure, with the source in slot
  2, and keeps that meaning.
* **A tag alone on its line is the object itself** — a paragraph that is nothing but
  ``{@encounter Goblin Ambush}`` shows the live encounter card, ``{@npc …}`` the NPC's,
  ``{@treasure …}`` the treasure, ``{@character …}`` the party card, and any entity tag
  (``{@spell Fireball}``) folds its entry into the text, closed until opened. Inside a
  sentence, a list item or a table cell a tag stays a link.
* **Callouts** — ``> [!read]`` read-aloud, ``> [!sidebar] Title``, ``> [!secret]``, and
  what else the books print: ``> [!variant] Title`` (a variant rule), ``> [!pullquote]``,
  ``> [!flowchart] Title`` (its list items as steps), ``> [!gallery]`` (its images side by
  side); Paravent's ``> [!handout] Title``, a letter or note the party finds. A quote's
  last line starting with ``—`` (or ``--``) is its attribution. Then Obsidian's:
  ``> [!info]``, ``[!abstract]``, ``[!danger]``, ``[!quote]`` and the rest, with their
  aliases (``[!tldr]`` is an abstract). ``> [!abstract]- Title`` folds the callout
  closed, ``+`` open; an unknown kind shows as a note, as in Obsidian.
* **Links to headings** — ``[[#Heading]]``, ``[[#Heading|label]]`` in this text,
  ``[[Chapter]]`` and ``[[Chapter#Heading]]`` to another chapter of the world.
* **Obsidian's image embeds** — ``![[map.png]]``, ``![[map.png|300]]`` (width),
  ``![[map.png|The map|300]]`` (alt text), found by file name in the world.
* **A line break is a break**, as Obsidian writes it: ``**AC** 17`` and ``**HP** 82`` on
  two lines stay two lines.
* **Blocks** — ``:::: at 00e`` anchors notes to a published adventure's entry; an outer
  block needs a longer fence than the blocks inside it. The older ``::: encounter …``
  embeds still render (text inside is a caption); :func:`embeds_to_tags` rewrites them
  as tags on their own line.

What a name *means* — which NPC, which encounter, where an image is served — is not
decided here: the page passes callbacks in :class:`MarkdownContext`, so this module
renders and the services resolve.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from htpy import Element, Node, a, button, div, fragment, img, span
from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.rules_core import StateCore
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict
from markupsafe import Markup, escape
from mdit_py_plugins.container import container_plugin

from app.services import image_effects
from app.services.i18n import english
from app.ui import tags
from app.ui.icons import icon

# Paravent's own entity tags: ``{@kind name|label}``.
LIBRARY_TAGS = frozenset({"npc", "encounter", "treasure", "character", "chapter", "place", "sound"})

# The campaign's own objects that show as a card when their tag stands alone.
LIBRARY_EMBEDS = frozenset({"npc", "encounter", "treasure", "character", "place"})

# What an embed may be: a campaign object, or any entity tag's name, whose entry folds
# into the text. ``statblock`` is the creature's older ``:::`` name. ``at`` anchors to a
# published adventure's entry id.
EMBEDS = frozenset({*LIBRARY_EMBEDS, "statblock", *tags.ENTITY_CATEGORY})
ANCHOR = "at"

# Paravent's callouts, then Obsidian's; a callout's kind is one of these after aliases.
CALLOUTS = frozenset(
    {
        "read", "sidebar", "secret", "variant", "pullquote", "flowchart", "gallery",
        "handout",
        "note", "abstract", "info", "todo", "tip", "success", "question",
        "warning", "failure", "danger", "bug", "example", "quote",
    }
)  # fmt: skip
CALLOUT_ALIASES = {
    "summary": "abstract",
    "tldr": "abstract",
    "hint": "tip",
    "important": "tip",
    "check": "success",
    "done": "success",
    "help": "question",
    "faq": "question",
    "caution": "warning",
    "attention": "warning",
    "fail": "failure",
    "missing": "failure",
    "error": "danger",
    "cite": "quote",
    "flow": "flowchart",
    "letter": "handout",
}
# Paravent's own callouts, named in ``md.<kind>``; Obsidian's in ``md.callout.<kind>``.
OWN_CALLOUTS = (
    "read",
    "secret",
    "sidebar",
    "variant",
    "pullquote",
    "flowchart",
    "gallery",
    "handout",
)
# Callouts with no title of their own unless one is written; a quote is an epigraph,
# as the books print it.
UNTITLED = frozenset({"sidebar", "quote", "pullquote", "flowchart", "gallery", "handout"})
# Callouts whose last line may name who said it.
QUOTES = frozenset({"quote", "pullquote"})
_ATTRIBUTION = re.compile(r"^(?:—|–|--)\s*")
_CALLOUT = re.compile(r"^\[!([A-Za-z][\w-]*)\]([+-]?)[ \t]*([^\n]*)(?:\n|$)")
_WIKILINK = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
# What ``![[…]]`` shows as an image rather than links to; SVG is named, and refused.
_IMAGE_FILE = re.compile(r"\.(png|jpe?g|webp|gif|avif|svg|bmp)$", re.IGNORECASE)
_SIZE = re.compile(r"^(\d+)(?:x(\d+))?$")

LIBRARY_ICON = {
    "npc": "circle-user",
    "encounter": "swords",
    "treasure": "gem",
    "character": "shield",
    "chapter": "book-open",
    "place": "map",
    "sound": "volume-2",
}
CALLOUT_ICON = {
    "read": "scroll",
    "secret": "vault",
    "variant": "refresh-cw",
    "pullquote": "quote",
    "flowchart": "list",
    "gallery": "eye",
    "handout": "scroll",
    "note": "pencil",
    "abstract": "clipboard-list",
    "info": "info",
    "todo": "circle-check",
    "tip": "flame",
    "success": "check",
    "question": "circle-help",
    "warning": "triangle-alert",
    "failure": "x",
    "danger": "zap",
    "bug": "bug",
    "example": "list",
    "quote": "quote",
}


def callout_kind(written: str) -> str | None:
    """The callout ``> [!written]`` is, aliases followed, or None for an unknown kind."""
    kind = written.lower()
    kind = CALLOUT_ALIASES.get(kind, kind)
    return kind if kind in CALLOUTS else None


def callout_label(kind: str) -> str:
    """The catalogue key of a callout's name, its title when none is written."""
    return f"md.{kind}" if kind in OWN_CALLOUTS else f"md.callout.{kind}"


ImageSpec = tuple[str, str, int | None, int | None, tuple[str, ...]]


def image_parts(parts: Sequence[str]) -> tuple[str, int | None, int | None, tuple[str, ...]]:
    """``(alt, width, height, effects)`` from what follows an image's name: a size
    (``300``, ``300x200``), effect words (``torn sepia``, image_effects.py), alt text."""
    alt, width, height = "", None, None
    effects: tuple[str, ...] = ()
    for part in parts:
        size = _SIZE.match(part)
        named = image_effects.parse(part) if part else None
        if size:
            width, height = int(size.group(1)), int(size.group(2)) if size.group(2) else None
        elif named:
            effects = named
        elif part:
            alt = part
    return alt, width, height, effects


def embedded_image(inner: str) -> ImageSpec | None:
    """``(file, alt, width, height, effects)`` when ``![[inner]]`` is an image, else None."""
    file, *rest = [part.strip() for part in inner.replace("\\|", "|").split("|")]
    if not _IMAGE_FILE.search(file):
        return None
    return (file, *image_parts(rest))


def callout_key(lines: Sequence[str]) -> str:
    """What names a box for sharing: its own lines, so the same box in a chapter's text
    and in a woven book section is one (documentation/JOURNAL.md)."""
    return hashlib.sha256("\n".join(lines).strip().encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Callout:
    """A box of a text, as written: its kind, title and own lines."""

    key: str
    kind: str
    title: str
    text: str


def callouts(text: str) -> list[Callout]:
    """Every box of ``text`` but secrets, in order, each with its lines — a nested
    secret taken out — as the share buttons name them."""
    source = protect_pipes(text or "")
    lines = source.split("\n")
    tokens = _PARSER.parse(source, {})
    found: list[Callout] = []
    for index, token in enumerate(tokens):
        meta = token.meta or {}
        kind = meta.get("callout") if token.type == "blockquote_open" else None
        if not kind or kind == "secret" or not token.map:
            continue
        start, end = token.map
        drop: set[int] = set()
        close = _matching_close(tokens, index)
        stop = tokens.index(close) if close is not None else len(tokens)
        for inner in tokens[index + 1 : stop]:
            secret = (inner.meta or {}).get("callout") == "secret"
            if inner.type == "blockquote_open" and secret and inner.map:
                drop.update(range(*inner.map))
        own = lines[start:end]
        kept = [line for n, line in enumerate(own, start) if n not in drop]
        found.append(Callout(callout_key(own), kind, meta.get("title", ""), "\n".join(kept)))
    return found


def share_link(url: str, label: str) -> Element:
    """The gamemaster's share button on a box or picture."""
    return a(href=url, class_="md-share", title=label, aria_label=label)[_icon("send")]


def _image(url: str, alt: str, effects: tuple[str, ...], **attrs: Any) -> str:
    """An image, asking for its effects; one with effects carries ``fx`` so the page
    leaves its edges alone."""
    return _html(
        img(
            src=image_effects.with_effects(url, effects),
            alt=alt,
            class_=" ".join(["fx", *(f"fx-{e}" for e in effects)]) if effects else None,
            **attrs,
            loading="lazy",
        )
    )


def image_url(ctx: MarkdownContext, file: str) -> str | None:
    """Where ``![[file]]`` is served from: as written, in ``media/``, or found by name."""
    if ctx.find_media is not None:
        return ctx.find_media(file)
    if ctx.media is None:
        return None
    return ctx.media(file) or ctx.media(f"media/{file}")


def heading_anchor(text: str) -> str:
    """The anchor a heading reading ``text`` gets (the first of that name)."""
    return re.sub(r"[^\w]+", "-", text.lower()).strip("-") or "section"


def wikilink_parts(inner: str) -> tuple[str, str, str]:
    """``(chapter, heading, label)`` of ``[[inner]]``; an empty chapter is this text."""
    target, _, label = inner.replace("\\|", "|").partition("|")
    chapter, _, heading = target.partition("#")
    return chapter.strip(), heading.strip(), label.strip()


# Media paths are relative, inside the world; anything with a scheme is not ours.
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


@dataclass(frozen=True)
class LibraryLink:
    """Where a ``{@npc …}``-style tag leads, once resolved."""

    url: str
    title: str


@dataclass(frozen=True)
class Heading:
    """One entry in a chapter's table of contents."""

    level: int
    text: str
    anchor: str


@dataclass(frozen=True)
class Rendered:
    """A rendered body: the HTML and the headings it holds."""

    html: Markup
    toc: tuple[Heading, ...]


def _english(key: str, **params: Any) -> str:
    return english()(key, **params)


@dataclass(frozen=True)
class MarkdownContext:
    """Everything the page knows that rendering needs.

    * ``tags`` — the context 5eTools tags resolve in (the adventure's books).
    * ``link(kind, name)`` — a Paravent tag's target, or None for the inert state.
    * ``embed(kind, argument)`` — the live object for a tag alone on its line (or a
      ``:::`` block), or None for the inert state.
    * ``media(path)`` — the URL an image is served from, or None when it may not be.
    * ``find_media(name)`` — the URL of the image ``![[name]]`` means, found by its file
      name where ``media`` only takes a path.
    * ``t`` — the reader's translator, for the few words the dialect prints itself.
    * ``breaks`` — a newline is a line break, as Obsidian writes; off for text the
      project ships hard-wrapped (the help pages).
    """

    tags: tags.RenderContext = field(default_factory=tags.RenderContext)
    link: Callable[[str, str], LibraryLink | None] | None = None
    embed: Callable[[str, str], Node] | None = None
    media: Callable[[str], str | None] | None = None
    find_media: Callable[[str], str | None] | None = None
    t: Callable[..., str] = _english
    breaks: bool = True
    # The gamemaster's share button (documentation/JOURNAL.md): ``share(kind, key)`` is
    # where a box (kind "box", its content key) or a picture ("image", its spec) is
    # shared from, or None for none.
    share: Callable[[str, str], str | None] | None = None
    # For players: a campaign link that leads nowhere they may go is its words alone,
    # never the gamemaster's "not found" mark.
    plain: bool = False


# -- parsing ---------------------------------------------------------------------------

_FENCE_LINE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def protect_pipes(text: str) -> str:
    """Escape the ``|`` inside ``{@…}`` tags and ``[[…]]`` links, so a table row does
    not split them.

    GFM tables cut a row into cells at every unescaped pipe *before* inline parsing, and
    5eTools tags separate their parts with pipes: ``| {@creature wolf||Three wolves} |``
    would become three cells. The tag rule undoes the escape. Code — fenced blocks and
    backtick spans — is left exactly as written.
    """
    out: list[str] = []
    fence: str | None = None
    for line in text.split("\n"):
        match = _FENCE_LINE.match(line)
        if fence is not None:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence):
                fence = None
            out.append(line)
            continue
        if match:
            fence = match.group(1)
            out.append(line)
            continue
        out.append(_protect_line(line) if "{@" in line or "[[" in line else line)
    return "\n".join(out)


def _protect_line(line: str) -> str:
    out: list[str] = []
    index, depth = 0, 0
    while index < len(line):
        char = line[index]
        if char == "`" and depth == 0:
            run = len(line[index:]) - len(line[index:].lstrip("`"))
            # A code span: copy up to the closing run of the same length, untouched.
            close = line.find("`" * run, index + run)
            end = close + run if close >= 0 else index + run
            out.append(line[index:end])
            index = end
            continue
        if depth == 0 and line.startswith("[[", index):
            close = line.find("]]", index + 2)
            if close >= 0:
                out.append(re.sub(r"(?<!\\)\|", r"\\|", line[index : close + 2]))
                index = close + 2
                continue
        if line.startswith("{@", index) or char == "{" and depth:
            depth += 1
        elif char == "}" and depth:
            depth -= 1
        elif char == "|" and depth and (index == 0 or line[index - 1] != "\\"):
            out.append("\\|")
            index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def _wikilink_rule(state: StateInline, silent: bool) -> bool:
    """``[[#Heading|label]]``, ``[[Chapter#Heading]]`` or ``![[map.png]]`` as one token."""
    if not state.src.startswith(("[[", "![["), state.pos):
        return False
    match = _WIKILINK.match(state.src, state.pos)
    if match is None or match.end() > state.posMax:
        return False
    if not silent:
        image = embedded_image(match.group(2)) if match.group(1) else None
        token = state.push("paravent_wikilink", "", 0)
        token.meta = {"parts": wikilink_parts(match.group(2)), "image": image}
    state.pos = match.end()
    return True


def _wikilink_text(chapter: str, heading: str, label: str) -> str:
    if label:
        return label
    return f"{chapter} › {heading}" if chapter and heading else heading or chapter


def _tag_rule(state: StateInline, silent: bool) -> bool:
    """``{@…}``, braces balanced, as one token; anything unterminated stays text."""
    src, start = state.src, state.pos
    if not src.startswith("{@", start):
        return False
    depth, cursor = 0, start
    while cursor < state.posMax:
        char = src[cursor]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                break
        cursor += 1
    if depth != 0 or cursor >= state.posMax:
        return False
    if not silent:
        token = state.push("paravent_tag", "", 0)
        token.content = src[start : cursor + 1].replace("\\|", "|")
    state.pos = cursor + 1
    return True


def _callouts(state: StateCore) -> None:
    """Turn ``> [!read]`` blockquotes into callouts, before inline parsing runs."""
    tokens = state.tokens
    quotes: list[tuple[Token, Token]] = []
    for index, token in enumerate(tokens):
        if token.type != "blockquote_open" or index + 2 >= len(tokens):
            continue
        para, inline = tokens[index + 1], tokens[index + 2]
        if para.type != "paragraph_open" or inline.type != "inline":
            continue
        match = _CALLOUT.match(inline.content)
        if match is None:
            continue
        written, fold, title = match.group(1), match.group(2), match.group(3).strip()
        kind = callout_kind(written)
        if kind is None:  # as Obsidian: a note, titled with what was written
            kind, title = "note", title or written.capitalize()
        token.meta = {"callout": kind, "title": title, "fold": fold}
        inline.content = inline.content[match.end() :]
        if not inline.content.strip():
            for hidden in tokens[index + 1 : index + 4]:
                hidden.hidden = True
                hidden.type = "paravent_hidden"
        close = _matching_close(tokens, index)
        if close is not None:
            close.meta = {"callout": kind, "fold": fold}
            if kind in QUOTES:
                quotes.append((token, close))
    for opening, close in reversed(quotes):
        _attribution(tokens, tokens.index(opening), tokens.index(close))


def _attribution(tokens: list[Token], start: int, end: int) -> None:
    """Mark a quote's last line starting with a dash as who said it, as the books print
    "— a sage of old": its own paragraph, or the last line of the last one, split off."""
    last = next(
        (
            i
            for i in range(end - 1, start, -1)
            if tokens[i].type == "inline" and not tokens[i].hidden
        ),
        None,
    )
    if last is None or tokens[last - 1].type != "paragraph_open":
        return
    inline = tokens[last]
    before, _, line = inline.content.rpartition("\n")
    if not _ATTRIBUTION.match(line):
        return
    line = _ATTRIBUTION.sub("— ", line, count=1)
    if not before.strip():
        inline.content = line
        tokens[last - 1].attrSet("class", "md-quote-by")
        return
    inline.content = before
    level, block_map = tokens[last - 1].level, tokens[last - 1].map
    opening = Token("paragraph_open", "p", 1, level=level, map=block_map, block=True)
    opening.attrSet("class", "md-quote-by")
    text = Token("inline", "", 0, content=line, level=level + 1, map=block_map, block=True)
    text.children = []
    closing = Token("paragraph_close", "p", -1, level=level, block=True)
    tokens[last + 2 : last + 2] = [opening, text, closing]


def _matching_close(tokens: Sequence[Token], index: int) -> Token | None:
    depth = 0
    for token in tokens[index:]:
        if token.type == "blockquote_open":
            depth += 1
        elif token.type == "blockquote_close":
            depth -= 1
            if depth == 0:
                return token
    return None


def _standalone(state: StateCore) -> None:
    """Mark paragraphs that are one embeddable tag and nothing else — the object itself,
    not a link to it. List items keep their tags as links: ``- {@creature Goblin}`` is
    a list."""
    tokens = state.tokens
    in_list = 0
    for index, token in enumerate(tokens):
        if token.type == "list_item_open":
            in_list += 1
        elif token.type == "list_item_close":
            in_list -= 1
        elif token.type == "paragraph_open" and not in_list and index + 2 < len(tokens):
            inline = tokens[index + 1]
            children = [
                c
                for c in inline.children or []
                if not (c.type == "text" and not c.content.strip()) and c.type != "softbreak"
            ]
            if len(children) != 1 or children[0].type != "paravent_tag":
                continue
            found = embed_of(children[0].content)
            if found is None:
                continue
            token.type = "paravent_embed"
            token.meta = {"kind": found[0], "argument": found[1], "raw": children[0].content}
            inline.type = "paravent_hidden"
            tokens[index + 2].type = "paravent_hidden"


def embed_of(raw: str) -> tuple[str, str] | None:
    """``(kind, argument)`` when the tag ``raw`` shows an object standing alone: the
    campaign's own by name, a reference with its book and label as written."""
    parsed = tags.parse(raw)
    node = parsed[0] if len(parsed) == 1 else None
    if not isinstance(node, tags.Tag) or not node.parts or not node.part(0).strip():
        return None
    if node.name in LIBRARY_EMBEDS:
        return node.name, node.part(0).strip()
    if node.name in tags.ENTITY_CATEGORY:
        return node.name, "|".join(node.parts)
    return None


def _headings(state: StateCore) -> None:
    """Shift headings one level down — the title is the page's ``h1`` — and anchor them."""
    used: dict[str, int] = {}
    toc: list[Heading] = []
    tokens = state.tokens
    for index, token in enumerate(tokens):
        if token.type != "heading_open":
            continue
        level = int(token.tag[1])
        token.tag = f"h{min(level + 1, 6)}"
        text = _inline_text(tokens[index + 1]).strip()
        slug = heading_anchor(text)
        used[slug] = used.get(slug, 0) + 1
        anchor = slug if used[slug] == 1 else f"{slug}-{used[slug]}"
        token.attrSet("id", anchor)
        close = tokens[index + 2]
        close.tag = token.tag
        toc.append(Heading(level, text, anchor))
    state.env["toc"] = toc


def _inline_text(inline: Token) -> str:
    """What a heading reads as: Markdown marks dropped, tags reduced to their text."""
    out = []
    for child in inline.children or []:
        if child.type == "paravent_tag":
            out.append(tags.plain(child.content))
        elif child.type in ("text", "code_inline"):
            out.append(child.content)
        elif child.type == "paravent_wikilink":
            out.append(_wikilink_text(*child.meta["parts"]))
        elif child.type in ("softbreak", "hardbreak"):
            out.append(" ")
    return "".join(out)


def build_parser() -> MarkdownIt:
    """A parser for the dialect, rules registered; renderers bound per call."""
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    md.enable(["table", "strikethrough"])
    md.inline.ruler.before("emphasis", "paravent_tag", _tag_rule)
    md.inline.ruler.before("link", "paravent_wikilink", _wikilink_rule)
    md.core.ruler.after("block", "paravent_callouts", _callouts)
    md.core.ruler.push("paravent_headings", _headings)
    md.core.ruler.push("paravent_standalone", _standalone)
    md.use(container_plugin, "block", validate=lambda _params, *_: True)
    return md


_PARSER = build_parser()


# -- rendering -------------------------------------------------------------------------


def _html(node: Node) -> str:
    # A plain str, not Markup: markdown-it concatenates its own (already safe) HTML
    # onto this, and ``str + Markup`` would escape the str side.
    return "".join([str(fragment[node])])


def _missing(label: Node, title: str) -> Node:
    return span(class_="ref missing", title=title)[label]


def _icon(name: str | None) -> Node:
    return icon(name, "xs") if name else None


def render_tag(raw: str, ctx: MarkdownContext) -> Node:
    """One ``{@…}`` from Markdown: Paravent's own kinds, else the 5eTools renderer."""
    parsed = tags.parse(raw)
    node = parsed[0] if len(parsed) == 1 else None
    if not isinstance(node, tags.Tag) or node.name not in LIBRARY_TAGS:
        return tags.render(raw, ctx.tags)
    name = node.part(0)
    label = tags.render(node.part(1) or name, ctx.tags)
    found = ctx.link(node.name, name) if ctx.link is not None and name else None
    if found is None:
        return label if ctx.plain else _missing(label, ctx.t("md.missing_library", name=name))
    if node.name == "sound":
        # Not a link: a press plays it on the speaker (sound-panel.js).
        return button(
            type="button", class_="ref lib sound", data_sound_trigger=name, title=found.title
        )[_icon(LIBRARY_ICON["sound"]), label]
    return a(href=found.url, class_=f"ref lib {node.name}", title=found.title)[
        _icon(LIBRARY_ICON[node.name]), label
    ]


class _Renderer(RendererHTML):
    """markdown-it's HTML renderer with the dialect's tokens added."""

    def __init__(self, ctx: MarkdownContext) -> None:
        super().__init__()
        self.ctx = ctx

    def paravent_tag(
        self, tokens: Sequence[Token], idx: int, _options: OptionsDict, _env: EnvType
    ) -> str:
        return _html(render_tag(tokens[idx].content, self.ctx))

    def paravent_hidden(self, *_args: Any) -> str:
        return ""

    def softbreak(self, *_args: Any) -> str:
        return "<br />\n" if self.ctx.breaks else "\n"

    def paravent_wikilink(
        self, tokens: Sequence[Token], idx: int, _options: OptionsDict, env: EnvType
    ) -> str:
        if tokens[idx].meta.get("image"):
            return self._embedded_image(*tokens[idx].meta["image"])
        chapter, heading, label = tokens[idx].meta["parts"]
        text = _wikilink_text(chapter, heading, label)
        anchor = heading_anchor(heading) if heading else ""
        if not chapter:
            if anchor in {h.anchor for h in env.get("toc", ())}:
                return _html(a(href=f"#{anchor}", class_="md-wikilink")[text])
            return _html(_missing(text, self.ctx.t("md.missing_heading", name=heading)))
        found = self.ctx.link("chapter", chapter) if self.ctx.link is not None else None
        if found is None:
            if self.ctx.plain:
                return _html(text)
            return _html(_missing(text, self.ctx.t("md.missing_library", name=chapter)))
        href = f"{found.url}#{anchor}" if anchor else found.url
        return _html(
            a(href=href, class_="ref lib chapter", title=found.title)[
                _icon(LIBRARY_ICON["chapter"]), text
            ]
        )

    def blockquote_open(
        self, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
    ) -> str:
        meta = tokens[idx].meta or {}
        kind = meta.get("callout")
        if not kind:
            return self.renderToken(tokens, idx, options, env)
        fold = meta.get("fold")
        written = meta.get("title")
        # These are untitled unless they fold: a summary needs a word.
        title: Node = (
            Markup(
                # Its own env: parsing runs the core rules, which would reset the toc.
                self.renderInline(_PARSER.parseInline(written, {})[0].children or [], options, env)
            )
            if written
            else self.ctx.t(callout_label(kind))
            if kind not in UNTITLED or fold
            else None
        )
        if kind == "variant" and written:
            title = [self.ctx.t("md.variant"), ": ", title]
        share = self._share_box(tokens[idx], kind, env)
        if fold:
            head = _html(span(class_="md-callout-title")[_icon(CALLOUT_ICON.get(kind)), title])
            opened = " open" if fold == "+" else ""
            return (
                f'<details class="md-callout md-{kind}"{opened}>'
                f'<summary class="md-callout-summary">{head}</summary>{share}'
            )
        head = (
            _html(div(class_="md-callout-title")[_icon(CALLOUT_ICON.get(kind)), title])
            if title is not None
            else ""
        )
        return f'<aside class="md-callout md-{kind}">{share}{head}'

    def _share_box(self, token: Token, kind: str, env: EnvType) -> str:
        """The share button of a box; never on a secret."""
        if self.ctx.share is None or kind == "secret" or not token.map:
            return ""
        lines = env.get("lines") or []
        url = self.ctx.share("box", callout_key(lines[token.map[0] : token.map[1]]))
        return _html(share_link(url, self.ctx.t("journal.share"))) if url else ""

    def blockquote_close(
        self, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
    ) -> str:
        meta = tokens[idx].meta or {}
        if meta.get("callout"):
            return "</details>\n" if meta.get("fold") else "</aside>\n"
        return self.renderToken(tokens, idx, options, env)

    def _embedded_image(
        self,
        file: str,
        alt: str,
        width: int | None,
        height: int | None,
        effects: tuple[str, ...] = (),
    ) -> str:
        url = image_url(self.ctx, file)
        if url is None:
            if self.ctx.plain:
                return ""
            missing = self.ctx.t("md.missing_image", path=file)
            return _html(span(class_="md-image missing")[missing])
        picture = _image(url, alt, effects, width=width, height=height)
        shared = (
            self.ctx.share("image", image_effects.join(file, effects)) if self.ctx.share else None
        )
        if not shared:
            return picture
        return _html(
            span(class_="md-shareable")[
                Markup(picture), share_link(shared, self.ctx.t("journal.share"))
            ]
        )

    def image(self, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType) -> str:
        token = tokens[idx]
        src = str(token.attrGet("src") or "")
        # Obsidian's ``![alt|300](file)``: size and effects follow the alt text.
        written = self.renderInlineAsText(token.children or [], options, env)
        first, *rest = [part.strip() for part in written.split("|")]
        alt, width, height, effects = image_parts([first, *rest] if rest else [first])
        url = None
        if src and not _SCHEME.match(src) and not src.startswith(("/", "//")):
            url = self.ctx.media(src) if self.ctx.media is not None else None
        if url is None:
            return _html(span(class_="md-image missing")[self.ctx.t("md.missing_image", path=src)])
        title = token.attrGet("title")
        return _image(
            url, alt, effects, width=width, height=height, title=str(title) if title else None
        )

    def paravent_embed(
        self, tokens: Sequence[Token], idx: int, _options: OptionsDict, _env: EnvType
    ) -> str:
        meta = tokens[idx].meta
        ctx = self.ctx
        card = ctx.embed(meta["kind"], meta["argument"]) if ctx.embed is not None else None
        if card is None:
            # Nothing to show: the tag as a link would be, in its inert state.
            return f"<p>{_html(render_tag(meta['raw'], ctx))}</p>\n"
        return f'<div class="md-embed md-embed-{meta["kind"]}">{_html(card)}</div>\n'

    def container_block_open(
        self, tokens: Sequence[Token], idx: int, _options: OptionsDict, _env: EnvType
    ) -> str:
        kind, _, argument = tokens[idx].info.strip().partition(" ")
        kind, argument = kind.lower(), argument.strip()
        ctx = self.ctx
        if kind == ANCHOR:
            label = ctx.t("md.anchor", anchor=argument)
            return (
                f'<div class="md-anchor" data-anchor="{escape(argument)}">'
                f'<div class="md-anchor-label">{escape(label)}</div>'
            )
        if kind not in EMBEDS:
            notice = ctx.t("md.unknown_block", kind=kind or ":::")
            head = f'<div class="md-embed-head">{escape(notice)}</div>'
            return f'<div class="md-embed md-unknown">{head}'
        card = ctx.embed(kind, argument) if ctx.embed is not None and argument else None
        if card is None and ctx.plain:
            return f'<div class="md-embed md-embed-{kind}"><div class="md-embed-caption">'
        if card is None:
            library = kind in LIBRARY_EMBEDS
            card = div(class_="md-embed-head")[
                span(class_="md-embed-kind")[ctx.t(f"md.embed.{kind}") if library else kind],
                " ",
                _missing(
                    argument or "—",
                    ctx.t(
                        "md.missing_library" if library else "md.missing_reference",
                        name=argument,
                    ),
                ),
            ]
        return f'<div class="md-embed md-embed-{kind}">{_html(card)}<div class="md-embed-caption">'

    def container_block_close(self, tokens: Sequence[Token], idx: int, *_args: Any) -> str:
        # Every opening above leaves one or two divs open; find which by its partner.
        depth = 0
        for token in reversed(tokens[:idx]):
            if token.type == "container_block_close":
                depth += 1
            elif token.type == "container_block_open":
                if depth == 0:
                    kind = token.info.strip().partition(" ")[0].lower()
                    return "</div>\n" if kind == ANCHOR or kind not in EMBEDS else "</div></div>\n"
                depth -= 1
        return "</div>\n"


def split_anchors(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Take the top-level ``:::: at <id>`` blocks out of ``text``.

    Returns the rest of the text and, in order, each anchor's id with the Markdown inside
    its block. A book chapter shows the rest at the top and each block after its entry
    (documentation/ADVENTURES.md §7). Nested ``at`` blocks stay where they are.
    """
    lines = (text or "").split("\n")
    tokens = _PARSER.parse(protect_pipes(text or ""), {})
    anchored: list[tuple[str, str]] = []
    cut: list[tuple[int, int]] = []
    depth = 0
    for token in tokens:
        if token.type == "container_block_open":
            kind, _, argument = token.info.strip().partition(" ")
            if depth == 0 and kind.lower() == ANCHOR and token.map and argument.strip():
                start, end = token.map
                inner = lines[start + 1 : end]
                # The map stops before the closing fence; take that line out too.
                if end < len(lines) and re.match(r"^\s*:{3,}\s*$", lines[end]):
                    end += 1
                elif inner and re.match(r"^\s*:{3,}\s*$", inner[-1]):
                    inner = inner[:-1]
                anchored.append((argument.strip(), "\n".join(inner)))
                cut.append((start, end))
            depth += 1
        elif token.type == "container_block_close":
            depth -= 1
    keep = [line for i, line in enumerate(lines) if not any(a <= i < b for a, b in cut)]
    return "\n".join(keep), anchored


_EMBED_OPEN = re.compile(r"^( {0,3})(:{3,})[ \t]*([A-Za-z]+)[ \t]+(\S.*?)[ \t]*$")
_BLOCK_OPEN = re.compile(r"^ {0,3}:{3,}[ \t]*\S")
_BLOCK_CLOSE = re.compile(r"^ {0,3}(:{3,})[ \t]*$")


def embeds_to_tags(text: str) -> str:
    """Rewrite the older ``::: kind argument`` … ``:::`` embeds as a tag on its own line.

    ``::: encounter Goblin Ambush`` becomes ``{@encounter Goblin Ambush}``, ``::: statblock
    Goblin|MM`` becomes ``{@creature Goblin|MM}``, a fold's ``open`` is dropped, and a
    caption written inside the block follows as its own paragraph. ``:::: at`` anchors,
    blocks of unknown kinds and fenced code stay exactly as written.
    """
    lines = text.split("\n")
    out: list[str] = []
    fence: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        code = _FENCE_LINE.match(line)
        if fence is None and code:
            fence = code.group(1)
            out.append(line)
            index += 1
            continue
        if fence is not None:
            if code and code.group(1)[0] == fence[0] and len(code.group(1)) >= len(fence):
                fence = None
            out.append(line)
            index += 1
            continue
        opened = _EMBED_OPEN.match(line)
        kind = opened.group(3).lower() if opened else ""
        if opened is None or kind == ANCHOR or kind not in EMBEDS:
            out.append(line)
            index += 1
            continue
        argument = opened.group(4)
        if argument.lower().endswith(" open"):
            argument = argument[: -len(" open")].rstrip()
        # The caption: the lines up to this block's closing fence (or the end of the text).
        depth, end = 0, index + 1
        while end < len(lines):
            if _BLOCK_CLOSE.match(lines[end]):
                if depth == 0:
                    break
                depth -= 1
            elif _BLOCK_OPEN.match(lines[end]):
                depth += 1
            end += 1
        caption = "\n".join(lines[index + 1 : end]).strip("\n")
        if out and out[-1].strip():
            out.append("")
        out.append(f"{{@{'creature' if kind == 'statblock' else kind} {argument}}}")
        if caption.strip():
            out.extend(["", caption])
        index = end + 1
        if index < len(lines) and lines[index].strip():
            out.append("")
    return "\n".join(out)


def headings_of(text: str) -> tuple[Heading, ...]:
    """The headings ``text`` holds, anchored as rendering anchors them."""
    env: dict[str, Any] = {}
    _PARSER.parse(protect_pipes(text or ""), env)
    return tuple(env.get("toc", ()))


def render_markdown(text: str, ctx: MarkdownContext | None = None) -> Rendered:
    """Render a body of the dialect to HTML, with its table of contents."""
    ctx = ctx or MarkdownContext()
    source = protect_pipes(text or "")
    env: dict[str, Any] = {"lines": source.split("\n")}
    tokens = _PARSER.parse(source, env)
    html = _Renderer(ctx).render(tokens, _PARSER.options, env)
    return Rendered(Markup(html), tuple(env.get("toc", ())))


__all__ = [
    "CALLOUTS",
    "CALLOUT_ALIASES",
    "EMBEDS",
    "LIBRARY_EMBEDS",
    "LIBRARY_TAGS",
    "Heading",
    "LibraryLink",
    "MarkdownContext",
    "Rendered",
    "build_parser",
    "callout_kind",
    "callout_label",
    "embed_of",
    "embedded_image",
    "embeds_to_tags",
    "heading_anchor",
    "headings_of",
    "image_url",
    "render_markdown",
    "render_tag",
    "split_anchors",
    "wikilink_parts",
]
