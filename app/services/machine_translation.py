"""Machine-made drafts of translated corpus text (documentation/TRANSLATION.md §7).

The Claude API translates the passages nobody has translated yet. Its output is a
**draft**: stored with ``draft: true`` and shown marked until a person confirms it,
because auto-translated text nobody has read must never pass for a reviewed one.

Three rules keep a draft safe to show:

* **Tags stay as they are.** The model is told to copy every ``{@...}`` tag verbatim,
  and a translation whose tags differ from the English (one lost, one invented, one
  changed) is thrown away and the passage stays English — §3: tags come from the
  source, never from the translated words. The creature builder's placeholders
  (``<$to_hit__str$>``) are held the same way.
* **The glossary's names are used.** The terms a batch mentions are sent with it, so
  "charmed" comes back as the table's "bezaubert" everywhere.
* **Structure never travels.** Only the passages' strings go out and only strings come
  back; the English record keeps its shape (§2).

Passages go in batches by size, a few batches at a time; a batch that fails or is
refused leaves its passages untranslated without stopping the rest.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

import anthropic
from anthropic.types.beta import BetaMessageParam, BetaOutputConfigParam, BetaTextBlockParam

from app.services.i18n import Translatable
from app.services.translation_targets import Target
from app.services.translations import TranslationStore, marks_in, passages

logger = logging.getLogger(__name__)

# A batch: at most this many passages, and about this many characters of English.
BATCH_PASSAGES = 40
BATCH_CHARS = 12000
# Batches in flight at once.
CONCURRENCY = 4

SYSTEM_PROMPT = """You translate text from the Dungeons & Dragons 5th edition rules and \
adventures from English into the target language named in each request, for a group \
playing at the table.

Write the way the official translated books of that language do: their terms for game \
mechanics, their tone, their conventions for numbers and dice. Translate prose fully; \
keep proper names of people and places unless the request's glossary names them.

The text contains markup tags in curly braces that start with an at sign, for example \
{@spell fireball}, {@damage 8d6}, {@dc 15}, {@creature goblin|MM|goblins}, \
{@b bold words}. Copy every tag exactly as it is, character for character, including \
its contents, and place it where it belongs in the translated sentence. Never \
translate, add, drop, merge or reorder the text inside a tag's braces; the software \
shows the tag's label in the reader's language itself.

Some text holds placeholders between <$ and $>, for example <$to_hit__str$> or \
<$title_short_name$>, or between {= and }, for example {=amount1/v}. Copy each exactly \
as it is, too. <$title_short_name$> and \
<$short_name$> stand for the creature's name (the software puts in its name, without an \
article), so build the sentence around them as around a name.

When the request lists glossary terms, use exactly those names for those terms.

Each passage is translated on its own and keeps its own id. Return every passage you \
were given, once, with its id."""

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "translations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["id", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["translations"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class DraftResult:
    """What a run of drafts did."""

    translated: int = 0
    rejected: int = 0
    failed: int = 0


class MachineTranslator:
    """Drafts untranslated passages through the Claude API and stores them as drafts."""

    def __init__(
        self,
        store: TranslationStore,
        terms: Callable[[str], Mapping[str, str]],
        language_name: Callable[[str], str],
        *,
        api_key: str,
        model: str,
        effort: str = "low",
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        """``terms(lang)`` is the glossary as English → localized name; ``language_name``
        names a language code in English ("German")."""
        self._store = store
        self._terms = terms
        self._language_name = language_name
        self._model = model
        self._effort = effort
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key)

    async def draft(self, lang: str, target: Target, *, by: str) -> int:
        """Draft every untranslated passage of one record. Returns how many were stored.

        Raises :class:`Translatable` when the service failed for every batch.
        """
        result = await self.draft_record(lang, target, by=by)
        if result.failed and not result.translated:
            raise Translatable("translate.error.service")
        return result.translated

    async def draft_record(self, lang: str, target: Target, *, by: str) -> DraftResult:
        """Draft one record's untranslated passages, reporting what happened."""
        record = self._store.record(lang, target.category, target.key)
        done = set(record.passages) if record else set()
        todo = [(p, text) for p, text in passages(target.data, target.fields) if p not in done]
        if not todo:
            return DraftResult()
        translated, rejected, failed = await self._translate(lang, todo)
        if translated:
            self._store.save(
                lang,
                target.category,
                target.key,
                target.data,
                translated,
                by=by,
                draft=True,
                fields=target.fields,
            )
        return DraftResult(len(translated), rejected, failed)

    # -- the API -----------------------------------------------------------------------

    async def _translate(
        self, lang: str, todo: Sequence[tuple[str, str]]
    ) -> tuple[dict[str, str], int, int]:
        glossary = self._terms(lang)
        semaphore = asyncio.Semaphore(CONCURRENCY)

        async def one(batch: list[tuple[str, str]]) -> tuple[dict[str, str], int, int]:
            async with semaphore:
                return await self._batch(lang, batch, glossary)

        results = await asyncio.gather(*(one(b) for b in _batches(todo)))
        translated: dict[str, str] = {}
        rejected = failed = 0
        for done, bad, lost in results:
            translated.update(done)
            rejected += bad
            failed += lost
        return translated, rejected, failed

    async def _batch(
        self, lang: str, batch: list[tuple[str, str]], glossary: Mapping[str, str]
    ) -> tuple[dict[str, str], int, int]:
        """Translate one batch: (path → text accepted, rejected count, failed count)."""
        text = " ".join(english for _, english in batch).lower()
        terms = {en: local for en, local in glossary.items() if en.lower() in text}
        request = {
            "target_language": f"{self._language_name(lang)} ({lang})",
            "glossary": terms,
            "passages": [{"id": i, "text": english} for i, (_, english) in enumerate(batch)],
        }
        output_config: BetaOutputConfigParam = {
            "effort": cast(Any, self._effort),
            "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA},
        }
        # The fixed instructions first and cached; the batch's own text after them.
        system: list[BetaTextBlockParam] = [
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ]
        messages: list[BetaMessageParam] = [
            {"role": "user", "content": json.dumps(request, ensure_ascii=False)}
        ]
        try:
            # Streamed: the SDK refuses a request whose max_tokens could outlast its
            # ten-minute timeout unless it streams; only the final message is read.
            async with self._client.beta.messages.stream(
                model=self._model,
                max_tokens=32000,
                betas=["server-side-fallback-2026-07-01"],
                # A declined batch is re-run on Anthropic's recommended fallback model.
                fallbacks="default",
                output_config=output_config,
                system=system,
                messages=messages,
            ) as stream:
                response = await stream.get_final_message()
        except anthropic.RateLimitError as exc:
            logger.warning("Machine translation rate-limited: %s", exc)
            return {}, 0, len(batch)
        except anthropic.APIStatusError as exc:
            logger.warning("Machine translation failed (%s): %s", exc.status_code, exc)
            return {}, 0, len(batch)
        except anthropic.APIConnectionError as exc:
            logger.warning("Machine translation unreachable: %s", exc)
            return {}, 0, len(batch)
        if response.stop_reason == "refusal":
            logger.warning("Machine translation declined a batch of %d passages", len(batch))
            return {}, 0, len(batch)
        if response.stop_reason == "max_tokens":
            logger.warning("Machine translation cut off a batch of %d passages", len(batch))
            return {}, 0, len(batch)
        body = next((b.text for b in response.content if b.type == "text"), "")
        try:
            items = json.loads(body).get("translations") or []
        except (ValueError, AttributeError):
            return {}, 0, len(batch)
        return _accepted(batch, items)


def _batches(todo: Sequence[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Passages grouped into batches by count and size, in order."""
    out: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    size = 0
    for item in todo:
        if current and (len(current) >= BATCH_PASSAGES or size + len(item[1]) > BATCH_CHARS):
            out.append(current)
            current, size = [], 0
        current.append(item)
        size += len(item[1])
    if current:
        out.append(current)
    return out


def _accepted(
    batch: Sequence[tuple[str, str]], items: Sequence[Any]
) -> tuple[dict[str, str], int, int]:
    """The returned translations that keep their passage's tags exactly."""
    by_id: dict[int, str] = {}
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("id"), int):
            value = item.get("text")
            if isinstance(value, str) and value.strip():
                by_id[item["id"]] = value.strip()
    translated: dict[str, str] = {}
    rejected = 0
    for index, (path, english) in enumerate(batch):
        text = by_id.get(index)
        if text is None:
            rejected += 1
        elif sorted(marks_in(text)) != sorted(marks_in(english)):
            rejected += 1  # a tag lost, made up or changed: the passage stays English
        else:
            translated[path] = text
    return translated, rejected, 0


__all__ = ["BATCH_CHARS", "BATCH_PASSAGES", "DraftResult", "MachineTranslator"]
