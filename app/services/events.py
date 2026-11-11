"""Live updates: telling every open screen that a character changed.

A change can come from anywhere — a button on the sheet, the gamemaster's screen, the
CLI, or someone saving the JSON file in an editor. All of them end in the
:class:`~app.store.characters.CharacterStore`, which reports ``(id, revision)``; this hub
fans that out to every browser that has the character open, over Server-Sent Events.

SSE rather than WebSockets: the traffic is one-way (server → browser), the browser
reconnects on its own, and it is plain HTTP that needs no extra dependency. The message
is only the new revision — the browser compares it with what it shows and fetches the
fresh sheet when they differ, so a screen that made the change itself does not reload
twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import threading
from collections.abc import AsyncGenerator, Awaitable, Callable
from types import FrameType
from typing import Any

logger = logging.getLogger(__name__)

# Put into a queue to end its stream, e.g. on server shutdown.
_CLOSED = object()

HEARTBEAT_SECONDS = 15.0

# The key every character change is also published under, as "<id> <revision>": the
# gamemaster's party overview watches all characters through one stream.
PARTY = "*party"

# Every world, adventure and chapter change, as "<kind> <id> <revision>": the lists of
# them watch this one key.
PROSE = "*prose"

# Every combat change, as "<world id> <combat id> <revision>": the combat list, and the
# strip that tells players a fight is on, watch this one key.
COMBATS = "*combats"

# Every entry appended to the log, as "<log key> <entry id>": the log button and panel
# of every signed-in page watch this one key, and ask for what their reader may see.
PROTOCOL = "*protocol"


def combat_key(character_id: str) -> str:
    """The key a character's combats publish under, as their revision: the strip on the
    character's sheet watches it, so it learns of a fight without polling."""
    return f"*combat {character_id}"


class ChangeHub:
    """Per-key subscriptions; ``publish`` reaches every current subscriber of a key."""

    def __init__(self) -> None:
        """Start with no subscribers."""
        self._subscribers: dict[str, set[asyncio.Queue[object]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def subscribe(self, key: str) -> asyncio.Queue[object]:
        """A queue that receives every revision published for ``key``."""
        self._loop = asyncio.get_running_loop()
        queue: asyncio.Queue[object] = asyncio.Queue(maxsize=64)
        self._subscribers.setdefault(key, set()).add(queue)
        return queue

    def unsubscribe(self, key: str, queue: asyncio.Queue[object]) -> None:
        """Stop delivering to ``queue``."""
        queues = self._subscribers.get(key)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                del self._subscribers[key]

    def subscriber_count(self, key: str) -> int:
        """How many screens are watching ``key``."""
        return len(self._subscribers.get(key, ()))

    def publish(self, key: str, revision: str | None) -> None:
        """Tell every subscriber of ``key`` its new revision ("" when it was deleted).

        Safe to call from any thread: off the event loop, delivery is scheduled onto it.
        """
        if not self._subscribers.get(key):
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self._loop is not None and running is not self._loop:
            self._loop.call_soon_threadsafe(self._deliver, key, revision or "")
        else:
            self._deliver(key, revision or "")

    def _deliver(self, key: str, message: object) -> None:
        for queue in list(self._subscribers.get(key, ())):
            # A slow screen loses intermediate revisions, never the latest one: only
            # the newest matters, because the screen re-fetches the whole sheet.
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(message)

    def close(self) -> None:
        """End every open stream, so a shutdown does not wait on idle browsers."""
        for key in list(self._subscribers):
            self._deliver(key, _CLOSED)


def close_on_shutdown_signals(hub: ChangeHub) -> Callable[[], None]:
    """End every open stream as soon as the server is told to stop.

    Uvicorn, on Ctrl+C or SIGTERM (which ``--reload`` also sends), first waits for open
    connections to finish and only *then* runs the app's shutdown. An open sheet's event
    stream never finishes by itself, so shutdown — and every reload — would hang for as
    long as any browser had a sheet open.

    This chains a handler in front of whatever is installed (uvicorn's own, at the
    point the app starts): it closes the streams, then hands the signal on unchanged.
    Returns a function that restores the previous handlers. Outside the main thread,
    where signal handlers cannot be set, it does nothing.
    """
    if threading.current_thread() is not threading.main_thread():
        return lambda: None
    loop = asyncio.get_running_loop()
    previous: dict[int, Any] = {}

    def make(then: Any) -> Callable[[int, FrameType | None], None]:
        def handler(signum: int, frame: FrameType | None) -> None:
            # A signal handler may interrupt the loop mid-step; schedule, don't touch.
            loop.call_soon_threadsafe(hub.close)
            if callable(then):
                then(signum, frame)
            elif then == signal.SIG_DFL:
                # Nothing else was listening: let the signal do what it does by default.
                signal.signal(signum, signal.SIG_DFL)
                signal.raise_signal(signum)

        return handler

    for sig in (signal.SIGINT, signal.SIGTERM):
        current = signal.getsignal(sig)
        previous[sig] = current
        signal.signal(sig, make(current))

    def restore() -> None:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    return restore


async def revision_stream(
    hub: ChangeHub,
    key: str,
    current: str | None,
    is_disconnected: Callable[[], Awaitable[bool]],
    *,
    heartbeat: float = HEARTBEAT_SECONDS,
) -> AsyncGenerator[str, None]:
    """The SSE body for one screen watching ``key``.

    Sends the current revision first, so a screen that reconnects after a gap notices
    what it missed; then every new one; and a comment line as a heartbeat so proxies and
    the browser keep the connection open.
    """
    queue = hub.subscribe(key)
    try:
        yield "retry: 2000\n\n"
        yield f"event: revision\ndata: {current or ''}\n\n"
        while True:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except TimeoutError:
                if await is_disconnected():
                    break
                yield ": ping\n\n"
                continue
            if message is _CLOSED:
                break
            yield f"event: revision\ndata: {message}\n\n"
    finally:
        hub.unsubscribe(key, queue)


__all__ = [
    "HEARTBEAT_SECONDS",
    "PARTY",
    "PROTOCOL",
    "ChangeHub",
    "close_on_shutdown_signals",
    "revision_stream",
]
