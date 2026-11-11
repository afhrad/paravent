"""Suggested names for the glossary's open entries (documentation/TRANSLATION.md §4).

The names the glossary still marks ``"-"`` are sent, a category at a time, to the
Claude API with the machine translation's key (``PARAVENT_TRANSLATE_API_KEY``). What
comes back is a **suggestion**, kept in
``library/i18n/<lang>/_glossary_suggestions.json`` beside the glossary, never in it:
an entry stays ``"-"`` — missing everywhere — until the gamemaster takes or types a
name on the "Names to give" page.

The answer is held to a JSON schema; only entry names, their book and a sample of the
table's own names for the same kind go out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import anthropic
from anthropic.types.beta import BetaMessageParam, BetaOutputConfigParam, BetaTextBlockParam

from app.services.glossary import MAX_TERM, Glossary, is_name
from app.store.jsonfile import read_json, write_json

logger = logging.getLogger(__name__)

SUGGESTIONS_FILE = "_glossary_suggestions.json"
# Names per request, and requests in flight at once.
BATCH = 150
CONCURRENCY = 3
# How many of the table's own names of a kind go along as examples.
EXAMPLES = 40

SYSTEM_PROMPT = """You suggest names for entries of the Dungeons & Dragons 5th edition \
books in the language a request names, for a gaming group's glossary.

Where the official books of that language print a name for the entry, give exactly \
that name. Where they do not, make the name a translator of those books would choose: \
translate descriptive words, keep proper names of people, places, gods and unique \
creatures as they are (in the language's spelling and capitalisation conventions), and \
follow the examples of the table's own names when the request lists them.

Give a name only, never an explanation, a note or alternatives. If the name should \
stay as it is in English, return it unchanged. Return every entry you were given, once, \
with its id."""

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "names": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
                "required": ["id", "name"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["names"],
    "additionalProperties": False,
}

# Asks for an answer to ``prompt`` under ``system``, held to ``schema``; returns the
# structured answer, or None when there was none.
Runner = Callable[[str, str, Mapping[str, Any]], Awaitable[Mapping[str, Any] | None]]


class SuggestError(Exception):
    """The service failed, refused, or gave no usable answer."""


class ApiRunner:
    """The Claude API as a :data:`Runner`, with the machine translation's key."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        effort: str = "low",
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        """Bind to the API with the key and model the passage drafts use."""
        self._model = model
        self._effort = effort
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key)

    async def __call__(
        self, system: str, prompt: str, schema: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        """One request; service errors become :class:`SuggestError`."""
        output_config: BetaOutputConfigParam = {
            "effort": cast(Any, self._effort),
            "format": {"type": "json_schema", "schema": dict(schema)},
        }
        # The fixed instructions first and cached; the batch's names after them.
        blocks: list[BetaTextBlockParam] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
        messages: list[BetaMessageParam] = [{"role": "user", "content": prompt}]
        try:
            # Streamed, as the passage drafts: only the final message is read.
            async with self._client.beta.messages.stream(
                model=self._model,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                # A declined batch is re-run on Anthropic's recommended fallback model.
                fallbacks="default",
                output_config=output_config,
                system=blocks,
                messages=messages,
            ) as stream:
                response = await stream.get_final_message()
        except anthropic.APIStatusError as exc:
            raise SuggestError(f"the service answered {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise SuggestError("the service could not be reached") from exc
        if response.stop_reason in ("refusal", "max_tokens"):
            raise SuggestError(f"no usable answer ({response.stop_reason})")
        body = next((b.text for b in response.content if b.type == "text"), "")
        try:
            answer = json.loads(body)
        except ValueError as exc:
            raise SuggestError("the answer was not JSON") from exc
        return answer if isinstance(answer, Mapping) else None


@dataclass
class Progress:
    """A run of suggestions for one language."""

    lang: str
    total: int
    done: int = 0
    suggested: int = 0
    failed: int = 0
    finished: bool = False
    error: str = ""
    _task: asyncio.Task[None] | None = field(default=None, repr=False)


class NameSuggestions:
    """Suggested names per language, and the background runs that make them."""

    def __init__(
        self,
        i18n_dir: Path,
        glossary: Glossary,
        runner: Runner | None,
        *,
        language_name: Callable[[str], str],
        kind_name: Callable[[str], str],
        source_of: Callable[[str, str], str | None],
    ) -> None:
        """``runner`` asks Claude (None: no API key set); ``language_name`` names a
        language code in English; ``kind_name`` a category ("Creature");
        ``source_of(category, name)`` the book an entry is printed in."""
        self._dir = i18n_dir
        self._glossary = glossary
        self._runner = runner
        self._language_name = language_name
        self._kind_name = kind_name
        self._source_of = source_of
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict[str, dict[str, str]]]] = {}
        self._runs: dict[str, Progress] = {}

    @property
    def available(self) -> bool:
        """True when suggestions can be asked for."""
        return self._runner is not None

    # -- the file -----------------------------------------------------------------------

    def path(self, lang: str) -> Path:
        """The language's suggestions file."""
        return self._dir / lang / SUGGESTIONS_FILE

    def all(self, lang: str) -> dict[str, dict[str, str]]:
        """Category → English name → suggestion."""
        path = self.path(lang)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        cached = self._cache.get(lang)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        raw = read_json(path, {}) or {}
        table = {
            str(category): {str(k): str(v) for k, v in names.items() if isinstance(v, str)}
            for category, names in (raw.items() if isinstance(raw, dict) else ())
            if isinstance(names, dict)
        }
        self._cache[lang] = (mtime, table)
        return table

    def of(self, lang: str, category: str, english: str) -> str | None:
        """The suggestion for one entry, or None."""
        return self.all(lang).get(category, {}).get(english)

    def _change(self, lang: str, change: Callable[[dict[str, dict[str, str]]], None]) -> None:
        with self._lock:
            table = {category: dict(names) for category, names in self.all(lang).items()}
            change(table)
            path = self.path(lang)
            path.parent.mkdir(parents=True, exist_ok=True)
            write_json(
                path,
                {
                    category: dict(sorted(names.items(), key=lambda kv: kv[0].lower()))
                    for category, names in sorted(table.items())
                    if names
                },
            )
            self._cache.pop(lang, None)

    def drop(self, lang: str, category: str, names: Sequence[str]) -> None:
        """Forget the suggestions of entries that now have a name."""
        if not any(name in self.all(lang).get(category, {}) for name in names):
            return

        def change(table: dict[str, dict[str, str]]) -> None:
            for name in names:
                table.get(category, {}).pop(name, None)

        self._change(lang, change)

    # -- asking -------------------------------------------------------------------------

    def progress(self, lang: str) -> Progress | None:
        """The language's latest run, finished or not."""
        return self._runs.get(lang)

    def start(self, lang: str, entries: Mapping[str, Sequence[str]]) -> Progress | None:
        """Ask for suggestions for ``entries`` (category → English names) that have none
        yet, in the background. None when there is nothing to ask, no API key, or a
        run for the language is still going."""
        runner = self._runner
        running = self._runs.get(lang)
        if runner is None or (running is not None and not running.finished):
            return None
        have = self.all(lang)
        batches = [
            (category, list(names[i : i + BATCH]))
            for category, wanted in entries.items()
            for names in [[n for n in wanted if n not in have.get(category, {})]]
            for i in range(0, len(names), BATCH)
        ]
        if not batches:
            return None
        run = Progress(lang, sum(len(names) for _, names in batches))
        self._runs[lang] = run
        gate = asyncio.Semaphore(CONCURRENCY)

        async def one(category: str, names: list[str]) -> None:
            async with gate:
                try:
                    found = await self._ask(runner, lang, category, names)
                except SuggestError as exc:
                    logger.warning("Name suggestions failed: %s", exc)
                    run.failed += len(names)
                    run.error = str(exc)
                else:
                    if found:
                        self._change(lang, lambda t: t.setdefault(category, {}).update(found))
                    run.suggested += len(found)
                    run.failed += len(names) - len(found)
                run.done += len(names)

        async def all_batches() -> None:
            try:
                await asyncio.gather(*(one(category, names) for category, names in batches))
            finally:
                run.finished = True

        run._task = asyncio.get_running_loop().create_task(all_batches())
        return run

    async def _ask(
        self, runner: Runner, lang: str, category: str, names: list[str]
    ) -> dict[str, str]:
        """One batch: the names it got back, held to a usable name."""
        given = list(self._glossary.table(lang).get(category, {}).values())
        step = max(1, len(given) // EXAMPLES)
        examples = given[::step][:EXAMPLES]
        lines = [
            f"Language: {self._language_name(lang)}",
            f"Kind of entry: {self._kind_name(category)} (5eTools category {category})",
        ]
        if examples:
            lines.append("The table already uses these names for this kind:")
            lines += [f"- {english} = {localized}" for english, localized in examples]
        lines.append("Entries (id: English name, book):")
        for index, english in enumerate(names):
            book = self._source_of(category, english)
            lines.append(f"{index}: {english}" + (f" ({book})" if book else ""))
        answer = await runner(SYSTEM_PROMPT, "\n".join(lines), OUTPUT_SCHEMA)
        found: dict[str, str] = {}
        for item in (answer or {}).get("names") or []:
            if not isinstance(item, Mapping):
                continue
            at, name = item.get("id"), str(item.get("name") or "").strip()
            if (
                isinstance(at, int)
                and 0 <= at < len(names)
                and is_name(name)
                and len(name) <= MAX_TERM
                and "{@" not in name
            ):
                found[names[at]] = name
        return found


__all__ = [
    "SUGGESTIONS_FILE",
    "ApiRunner",
    "NameSuggestions",
    "Progress",
    "Runner",
    "SuggestError",
]
