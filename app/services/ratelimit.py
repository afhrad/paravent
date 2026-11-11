"""Failed logins slow down (PROGRESS.md "Login rate limiting").

Two counters are kept for every attempt: one for the username tried, one for the client
address it came from. The first :data:`FREE` failures within :data:`WINDOW` cost nothing
— people mistype. After that each further failure doubles the wait before the next
attempt is even looked at (2 s, 4 s, 8 s …, at most :data:`MAX_WAIT`). A successful
login clears the username's counter; the address's counter runs out with the window.

The username counter is kept whether or not the account exists, so the answer never
tells an attacker which names are real. The address is the connection's own — a
forwarded-for header is not trusted, as anyone can send one. Counters live in memory:
a restart clears them, which is acceptable for a server at home.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

FREE = 5
WINDOW = 15 * 60.0
MAX_WAIT = 15 * 60.0
MAX_KEYS = 10_000


@dataclass
class _Counter:
    failures: int
    last: float
    until: float


class LoginThrottle:
    """Counts failed attempts per key and says how long the next one has to wait."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """``clock`` is seconds, monotonic; tests pass their own."""
        self.clock = clock
        self._counters: dict[str, _Counter] = {}
        self._lock = threading.Lock()

    def _live(self, key: str, now: float) -> _Counter | None:
        counter = self._counters.get(key)
        if counter is not None and now - counter.last > WINDOW and now >= counter.until:
            del self._counters[key]
            return None
        return counter

    def wait(self, keys: Iterable[str]) -> int:
        """Whole seconds until any of ``keys`` may try again; 0 when they may now."""
        now = self.clock()
        with self._lock:
            waits = [
                c.until - now
                for key in keys
                if (c := self._live(key, now)) is not None and c.until > now
            ]
        return math.ceil(max(waits, default=0.0))

    def failed(self, keys: Iterable[str]) -> None:
        """Count a failure against each key."""
        now = self.clock()
        with self._lock:
            if len(self._counters) > MAX_KEYS:
                self._counters = {
                    k: c for k, c in self._counters.items() if self._live(k, now) is not None
                }
            for key in keys:
                counter = self._live(key, now) or _Counter(0, now, now)
                counter.failures += 1
                counter.last = now
                over = counter.failures - FREE
                if over > 0:
                    counter.until = now + min(MAX_WAIT, 2.0**over)
                self._counters[key] = counter

    def succeeded(self, keys: Iterable[str]) -> None:
        """Forget these keys' failures."""
        with self._lock:
            for key in keys:
                self._counters.pop(key, None)


def login_keys(username: str, address: str | None) -> list[str]:
    """The counters a login attempt counts against."""
    keys = [f"user:{username.strip().lower()}"]
    if address:
        keys.append(f"addr:{address}")
    return keys


__all__ = ["FREE", "MAX_WAIT", "WINDOW", "LoginThrottle", "login_keys"]
