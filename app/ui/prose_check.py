"""Checking a chapter before play: what in its Markdown leads nowhere (ADVENTURES.md §5.7).

The text is read line by line — code left out — and every reference judged by the same
callbacks the page renders with (:class:`~app.ui.markdown.MarkdownContext`), so the check
and the page cannot disagree:

* a ``{@…}`` tag the world's books do not resolve — not imported, or in a book the world
  leaves out — with the nearest names the books do have;
* ``{@npc …}``, ``{@encounter …}`` and the like naming nothing in the world — offered to
  create where the chapter is;
* an encounter shown alone on its line with no creatures, an image that is not there, and
  in older texts a ``:::`` block of a kind the dialect does not know or never closed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

from app.store.reference import Outcome, parse_reference
from app.ui import tags
from app.ui.markdown import (
    ANCHOR,
    EMBEDS,
    LIBRARY_TAGS,
    MarkdownContext,
    callout_kind,
    embed_of,
    embedded_image,
    heading_anchor,
    headings_of,
    image_url,
    wikilink_parts,
)

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")
_BLOCK = re.compile(r"^ {0,3}(:{3,})\s*(\S*)\s*(.*)$")
_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_CALLOUT = re.compile(r"^ {0,3}(?:>\s*)+\[!([A-Za-z][\w-]*)\]")
_WIKILINK = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
_CREATABLE = frozenset({"npc", "encounter", "treasure", "place"})


@dataclass(frozen=True)
class Problem:
    """One thing that leads nowhere, where it is and what would mend it."""

    line: int  # from 1
    kind: str  # reference, excluded, library, empty, block, image, unclosed, callout, heading
    text: str  # what was written: "{@spell Firebal}", "::: npc Garaele"
    name: str = ""
    # Replacements for ``text`` on the line ("{@spell Fireball}"), best first.
    instead: tuple[str, ...] = ()
    # ``(kind, name)`` of an NPC, encounter or treasure to create where the chapter is.
    create: tuple[str, str] | None = None


@dataclass
class Checks:
    """What the page knows beyond the Markdown context."""

    # An encounter's name → True when it has no creatures, None when there is none.
    empty_encounter: Callable[[str], bool | None] = lambda _name: None
    # ``(tag, name)`` → tags the world's books do have, nearest first.
    nearest: Callable[[str, str], Sequence[str]] = lambda _tag, _name: ()


def check(text: str, ctx: MarkdownContext, checks: Checks | None = None) -> list[Problem]:
    """Every problem in ``text``, in reading order."""
    checks = checks or Checks()
    out: list[Problem] = []
    opened: list[tuple[int, int, str]] = []  # (fence length, line, what) of open blocks
    anchors: set[str] | None = None  # the text's headings, read once a link asks
    for number, line in _lines(text):
        block = _BLOCK.match(line)
        if block:
            fence, kind, argument = len(block.group(1)), block.group(2).lower(), block.group(3)
            if not kind:  # a closing fence: closes the innermost block it is long enough for
                while opened and opened[-1][0] > fence:
                    opened.pop()
                if opened:
                    opened.pop()
                continue
            opened.append((fence, number, line.strip()))
            out.extend(_embed(number, line.strip(), kind, argument.strip(), ctx, checks))
            continue
        bare = _CODE_SPAN.sub("", line)
        found_here = [p for p in (_tag(number, tag, ctx, checks) for tag in _tags(bare)) if p]
        out.extend(found_here)
        # An encounter shown alone on its line: its card, which has nothing to run.
        alone = embed_of(line.strip()) if line.strip().startswith("{@") else None
        empty = alone is not None and alone[0] == "encounter" and not found_here
        if alone is not None and empty and checks.empty_encounter(alone[1]):
            out.append(Problem(number, "empty", line.strip(), alone[1]))
        callout = _CALLOUT.match(bare)
        if callout and callout_kind(callout.group(1)) is None:
            out.append(Problem(number, "callout", f"[!{callout.group(1)}]", callout.group(1)))
        for link in _WIKILINK.finditer(bare):
            image = embedded_image(link.group(2)) if link.group(1) else None
            if image is not None:
                if image_url(ctx, image[0]) is None:
                    out.append(Problem(number, "image", image[0], image[0]))
                continue
            chapter, heading, _label = wikilink_parts(link.group(2))
            if chapter:
                if ctx.link is None or ctx.link("chapter", chapter) is None:
                    out.append(Problem(number, "library", link.group(0), chapter))
                continue
            if anchors is None:
                anchors = {h.anchor for h in headings_of(text)}
            if heading and heading_anchor(heading) not in anchors:
                out.append(Problem(number, "heading", link.group(0), heading))
        for path in _IMAGE.findall(bare):
            if _SCHEME.match(path) or path.startswith(("/", "//")):
                continue
            if ctx.media is None or ctx.media(path) is None:
                out.append(Problem(number, "image", path, path))
    out.extend(Problem(n, "unclosed", what) for _fence, n, what in opened)
    return sorted(out, key=lambda p: p.line)


def _lines(text: str) -> Iterator[tuple[int, str]]:
    """The lines outside fenced code, numbered from 1."""
    fence: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        marker = _FENCE.match(line)
        if fence is None and marker:
            fence = marker.group(1)[0] * 3
            continue
        if fence is not None:
            if line.strip().startswith(fence):
                fence = None
            continue
        yield number, line


def _tags(text: str) -> Iterator[tags.Tag]:
    """Every tag in ``text``, nested ones included."""
    for node in tags.parse(text):
        if isinstance(node, tags.Tag):
            yield node
            for part in node.parts:
                yield from _tags(part)


def _written(tag: tags.Tag) -> str:
    return "{@" + tag.name + (" " + "|".join(tag.parts) if tag.parts else "") + "}"


def _tag(number: int, tag: tags.Tag, ctx: MarkdownContext, checks: Checks) -> Problem | None:
    if tag.name in LIBRARY_TAGS:
        name = tag.part(0)
        if ctx.link is not None and ctx.link(tag.name, name) is not None:
            return None
        create = (tag.name, name) if tag.name in _CREATABLE and name else None
        return Problem(number, "library", _written(tag), name, create=create)
    if tag.name not in tags.ENTITY_CATEGORY:
        return None
    return _reference(number, tag.name, "|".join(tag.parts), _written(tag), ctx, checks)


def _reference(
    number: int, tag: str, payload: str, written: str, ctx: MarkdownContext, checks: Checks
) -> Problem | None:
    """A reference the world's books do not resolve, or None."""
    resolver = ctx.tags.resolver
    if resolver is None:
        return None
    name, source, _display = parse_reference(payload, tag)
    outcome = None
    for category in tags.ENTITY_CATEGORY[tag]:
        result = resolver.resolve_parts(category, name, source, ctx.tags.source_set)
        if result.entry is not None:
            return None
        outcome = outcome or result.outcome
    kind = "excluded" if outcome is Outcome.NOT_IN_SOURCE_SET else "reference"
    near = tuple(
        written.replace(name, found, 1).replace(f"|{source}", "", 1)
        if source
        else written.replace(name, found, 1)
        for found in checks.nearest(tag, name)[:3]
        if found.lower() != name.lower()
    )
    return Problem(number, kind, written, name, instead=near)


def _embed(
    number: int, line: str, kind: str, argument: str, ctx: MarkdownContext, checks: Checks
) -> list[Problem]:
    if kind == ANCHOR:
        return []
    if kind not in EMBEDS:
        return [Problem(number, "block", line, kind)]
    if not argument:
        return [Problem(number, "library" if kind in LIBRARY_TAGS else "reference", line)]
    if kind in ("encounter", "npc", "treasure"):
        if ctx.embed is not None and ctx.embed(kind, argument) is not None:
            if kind == "encounter" and checks.empty_encounter(argument):
                return [Problem(number, "empty", line, argument)]
            return []
        return [Problem(number, "library", line, argument, create=(kind, argument))]
    payload = argument[: -len(" open")].rstrip() if argument.lower().endswith(" open") else argument
    tag = "creature" if kind == "statblock" else kind
    found = _reference(number, tag, payload, line, ctx, checks)
    return [found] if found is not None else []


__all__ = ["Checks", "Problem", "check"]
