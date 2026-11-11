"""Claude drafts a naming style from the workshop (documentation/NAMES.md §5b).

For a people of a world that no style serves, the gamemaster asks Claude through the
Claude API, with the machine translation's key (``PARAVENT_TRANSLATE_API_KEY``). Claude
researches with web search and hands the style to a ``draft_naming_style`` tool, the
same shape as the MCP tool of that name; every call is answered with samples and what
they show is wrong, so Claude may revise it. The result is an ordinary draft for that
world: offered nowhere until accepted, then one of the world's styles.

Runs in the background, one per world and people, kept in memory.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any, cast

import anthropic
from anthropic.types.beta import (
    BetaMessageParam,
    BetaOutputConfigParam,
    BetaTextBlockParam,
    BetaToolUnionParam,
)
from pydantic import TypeAdapter

from app.models.naming import NamePart
from app.services.i18n import english
from app.services.naming import Naming, sample_notes
from app.services.naming_workshop import NamingWorkshop, StyleError, style_from_raw

logger = logging.getLogger(__name__)

# The research guide Claude reads over MCP, read here too.
NAMING_RULES = files("app.mcp").joinpath("naming_rules.md")
# Rounds of the conversation, and drafts Claude may hand in, before the run stops.
MAX_TURNS = 16
MAX_DRAFTS = 4
# How many peoples Claude drafts for at the same time; the rest wait their turn.
CONCURRENT = 3
SAMPLES = 20
EFFORT = "medium"

SYSTEM_PROMPT = """You research and draft naming styles for Paravent, a tool a \
gamemaster runs their tabletop game with. There is no conversation: the gamemaster \
asked once, and reads only the style you hand in and its summary.

Follow the guide below, with these differences: there is no `list_naming_styles` (the \
request says which styles the world has), you hand the style to the \
`draft_naming_style` tool (each call replaces your previous draft), and what you would \
tell the gamemaster goes into the style's `summary`. Search the web with `web_search`; \
cite in `sources` only pages it returned. When the samples read well, stop.

"""

PART_KINDS = """
## The parts, as JSON

Each part of `parts` is an object with a `kind`:

- `{"kind": "list", "names": [...], "female": [...], "male": [...]}`
- `{"kind": "elements", "first": [...], "second": [...], "second_female": [...], \
"second_male": [...]}`
- `{"kind": "by_gender", "female": "dottir", "male": "son"}`
- `{"kind": "sounds", "groups": {"C": ["k", "th"], "V": ["a", "i"]}, "syllables": \
["CV", "CVn"], "min_syllables": 2, "max_syllables": 3, "ending_female": [...], \
"ending_male": [...]}`
- `{"kind": "learned", "names": [...], "female": [...], "male": [...], "order": 2}` \
(at least 10 in each list given)
- `{"kind": "kin", "part": "given", "fallback": "father"}`

The pattern names each part as `{part}`, or `{part:initial}` for its first letter.
"""


def _tool_schema() -> dict[str, Any]:
    parts = TypeAdapter(dict[str, NamePart]).json_schema()
    words = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The style's name"},
            "pattern": {"type": "string", "description": 'The full name: "{given} {family}"'},
            "parts": parts,
            "race": {"type": "string", "description": "The people, as the setting names them"},
            "culture": {"type": "string", "description": "A group within them"},
            "aliases": {**words, "description": "Other names of the race"},
            "sources": {**words, "description": "Pages you read"},
            "summary": {"type": "string"},
            "rewrites": {"type": "object", "additionalProperties": {"type": "string"}},
            "banned": words,
            "max_cluster": {"type": "integer", "minimum": 0, "maximum": 8},
            "subject": {
                "type": "string",
                "enum": ["person", "settlement", "water", "landscape", "building"],
            },
            "language": {"type": "string", "description": 'A language code, or ""'},
        },
        "required": ["name", "pattern", "parts"],
        "$defs": parts.pop("$defs", {}),
    }


DRAFT_TOOL: dict[str, Any] = {
    "name": "draft_naming_style",
    "description": "Hand in the naming style. Answers with samples and what they show is "
    "wrong; call it again to replace the draft, or stop when it reads well.",
    "input_schema": _tool_schema(),
}
WEB_SEARCH: dict[str, Any] = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}

# Hands Claude's draft in: ``(answer, is_error)``.
Submit = Callable[[Mapping[str, Any]], tuple[str, bool]]
# Runs the conversation: ``(system, prompt, submit)``.
Agent = Callable[[str, str, Submit], Awaitable[None]]


class DraftError(Exception):
    """The service failed, refused, or handed in no usable style."""


class ApiAgent:
    """The Claude API as an :data:`Agent`: web search and the draft tool, in a loop."""

    def __init__(
        self, *, api_key: str, model: str, client: anthropic.AsyncAnthropic | None = None
    ) -> None:
        """Bind to the API with the machine translation's key and model."""
        self._model = model
        self._client = client or anthropic.AsyncAnthropic(api_key=api_key)

    async def __call__(self, system: str, prompt: str, submit: Submit) -> None:
        """Talk until Claude stops; service errors become :class:`DraftError`."""
        blocks: list[BetaTextBlockParam] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
        ]
        tools = cast(list[BetaToolUnionParam], [WEB_SEARCH, DRAFT_TOOL])
        messages: list[BetaMessageParam] = [{"role": "user", "content": prompt}]
        for _ in range(MAX_TURNS):
            try:
                async with self._client.beta.messages.stream(
                    model=self._model,
                    max_tokens=64000,
                    betas=["server-side-fallback-2026-07-01"],
                    # A declined request is re-run on Anthropic's recommended fallback.
                    fallbacks="default",
                    output_config=cast(BetaOutputConfigParam, {"effort": EFFORT}),
                    system=blocks,
                    tools=tools,
                    messages=messages,
                ) as stream:
                    response = await stream.get_final_message()
            except anthropic.APIStatusError as exc:
                raise DraftError(f"the service answered {exc.status_code}") from exc
            except anthropic.APIConnectionError as exc:
                raise DraftError("the service could not be reached") from exc
            if response.stop_reason in ("refusal", "max_tokens"):
                raise DraftError(f"no usable answer ({response.stop_reason})")
            # Everything goes back as it came, thinking and search results included.
            messages.append(
                cast(BetaMessageParam, {"role": "assistant", "content": response.content})
            )
            if response.stop_reason == "pause_turn":
                continue  # the server's search loop paused; sending it back resumes it
            if response.stop_reason != "tool_use":
                return
            results: list[dict[str, Any]] = []
            for block in response.content:
                if block.type == "tool_use":
                    given = block.input if isinstance(block.input, Mapping) else {}
                    answer, failed = submit(given)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": answer,
                            "is_error": failed,
                        }
                    )
            messages.append(cast(BetaMessageParam, {"role": "user", "content": results}))
        raise DraftError("it went on too long")


@dataclass
class DraftRun:
    """One people's draft in one world."""

    world_id: str
    race: str
    style_id: str | None = None
    drafts: int = 0
    # False while it waits for one of the slots (CONCURRENT), True once Claude is asked.
    started: bool = False
    finished: bool = False
    error: str = ""
    _task: asyncio.Task[None] | None = field(default=None, repr=False)

    @property
    def running(self) -> bool:
        """True until the run ends."""
        return not self.finished


@dataclass(frozen=True)
class DraftRequest:
    """What Claude is asked: the people, and what the world says about itself."""

    world_id: str
    world_name: str
    race: str
    aliases: Sequence[str] = ()
    language: str = ""
    setting: str = ""
    # Names of the people already at the table, and the styles the world uses.
    known: Sequence[str] = ()
    styles: Sequence[str] = ()

    def prompt(self) -> str:
        """The request as Claude reads it."""
        lines = [
            f"Draft a naming style for the people {self.race!r} of the world "
            f"{self.world_name!r}: `race` {self.race!r}"
            + (f", `aliases` {list(self.aliases)}" if self.aliases else "")
            + ".",
            f"The table reads the language {self.language!r}; give `language` only if "
            "the names are made for it (as a translation names them).",
        ]
        if self.setting.strip():
            lines += ["", "What the gamemaster says about the setting:", self.setting.strip()]
        if self.styles:
            lines += ["", "Styles the world uses: " + "; ".join(self.styles)]
        if self.known:
            lines += ["", "Names of this people already in the game: " + ", ".join(self.known)]
        return "\n".join(lines)


def system_prompt() -> str:
    """The instructions, the research guide and the parts' JSON."""
    return SYSTEM_PROMPT + NAMING_RULES.read_text(encoding="utf-8") + PART_KINDS


class StyleDrafts:
    """Claude's drafts of naming styles for a world's peoples, run in the background."""

    def __init__(self, workshop: NamingWorkshop, naming: Naming, agent: Agent | None) -> None:
        """``agent`` asks Claude (None: no API key set)."""
        self._workshop = workshop
        self._naming = naming
        self._agent = agent
        self._runs: dict[tuple[str, str], DraftRun] = {}
        # Many peoples drafted at once queue here, so the API is not asked for all at once.
        self._slots = asyncio.Semaphore(CONCURRENT)

    @property
    def available(self) -> bool:
        """True when Claude can be asked."""
        return self._agent is not None

    def runs(self, world_id: str) -> dict[str, DraftRun]:
        """The world's runs, by lower-case people."""
        return {race: run for (wid, race), run in self._runs.items() if wid == world_id}

    def running(self, world_id: str) -> bool:
        """True while any run of the world goes on."""
        return any(run.running for run in self.runs(world_id).values())

    def start(self, request: DraftRequest) -> DraftRun | None:
        """Ask for a style in the background; None without an API key or while the same
        people's run goes on."""
        agent = self._agent
        key = (request.world_id, request.race.casefold())
        current = self._runs.get(key)
        if agent is None or (current is not None and current.running):
            return None
        run = DraftRun(request.world_id, request.race)
        self._runs[key] = run

        async def go() -> None:
            try:
                async with self._slots:
                    run.started = True
                    await agent(
                        system_prompt(), request.prompt(), lambda given: self._take(run, given)
                    )
                if run.style_id is None:
                    raise DraftError("no style was handed in")
            except DraftError as exc:
                logger.warning("Drafting a naming style for %s failed: %s", request.race, exc)
                run.error = str(exc)
            finally:
                run.finished = True

        run._task = asyncio.get_running_loop().create_task(go())
        return run

    def _take(self, run: DraftRun, given: Mapping[str, Any]) -> tuple[str, bool]:
        """Keep a handed-in style as the run's draft; answer with samples or the fault."""
        if run.drafts >= MAX_DRAFTS:
            return "That was the last draft; stop now.", True
        try:
            style = self._workshop.propose(
                style_from_raw(json.dumps(dict(given))), run.style_id, run.world_id
            )
        except StyleError as exc:
            return f"Not saved: {exc}", True
        run.style_id = style.id
        run.drafts += 1
        samples = self._naming.sample(style, count=SAMPLES)
        say = english()
        problems = [say(key, **params) for key, params in sample_notes(samples, SAMPLES)]
        if not style.sources:
            problems.append("No sources: name what you read, or say it was not researched.")
        return (
            "Saved. Samples: "
            + ", ".join(samples)
            + ("\nProblems: " + "; ".join(problems) if problems else "\nNo problems found.")
        ), False


__all__ = [
    "ApiAgent",
    "DraftError",
    "DraftRequest",
    "DraftRun",
    "StyleDrafts",
    "system_prompt",
]
