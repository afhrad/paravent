"""One log layout for the whole server: Paravent's own loggers, uvicorn's server and
request lines, and the libraries'.

    16:37:57  INFO     store.compendium    Compendium loaded: 21016 entries …
    16:37:58  INFO     server              Application startup complete.
    16:38:02  WARNING  http                127.0.0.1:51034 - "GET /x HTTP/1.1" 404

The time is dimmed, the level coloured and the source cyan when the output is a terminal;
``NO_COLOR`` turns colours off, ``FORCE_COLOR`` on (https://no-color.org). The setup lives
in ``app/log_config.json``, which uvicorn reads with ``--log-config`` (so its reloader
process logs the same way) and :func:`configure_logging` applies when the app starts.
"""

from __future__ import annotations

import json
import logging
import logging.config
import os
import sys
from pathlib import Path

LOG_CONFIG = Path(__file__).with_name("log_config.json")
# The source column: uvicorn's loggers by what they report.
NAMES = {"uvicorn": "server", "uvicorn.error": "server", "uvicorn.access": "http"}
NAME_WIDTH = 18
COLOURS = {
    logging.DEBUG: "2",
    logging.INFO: "32",
    logging.WARNING: "33",
    logging.ERROR: "31",
    logging.CRITICAL: "1;31",
}


def use_colour(stream: object = None) -> bool:
    """Colours for a terminal, unless ``NO_COLOR`` says otherwise or ``FORCE_COLOR`` asks."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    target = stream if stream is not None else sys.stderr
    isatty = getattr(target, "isatty", None)
    return bool(isatty and isatty())


def _paint(text: str, code: str, colour: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if colour and code else text


class Formatter(logging.Formatter):
    """Time, level and source in fixed columns, then the message."""

    def __init__(self, *args: object, colour: bool | None = None, **kwargs: object) -> None:
        super().__init__()
        self.colour = use_colour() if colour is None else colour

    def format(self, record: logging.LogRecord) -> str:
        # Paravent's own by their module, another library's by its package ("mcp").
        name = NAMES.get(record.name) or (
            record.name.removeprefix("app.")
            if record.name.startswith("app.")
            else record.name.split(".")[0]
        )
        if len(name) > NAME_WIDTH:
            name = "…" + name[-(NAME_WIDTH - 1) :]
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            message += "\n" + self.formatStack(record.stack_info)
        when = self.formatTime(record, "%H:%M:%S")
        level = f"{record.levelname:<8}"
        return (
            f"{_paint(when, '2', self.colour)}  "
            f"{_paint(level, COLOURS.get(record.levelno, ''), self.colour)} "
            f"{_paint(f'{name:<{NAME_WIDTH}}', '36', self.colour)}  {message}"
        )


def configure_logging() -> None:
    """Apply ``app/log_config.json`` to every logger, uvicorn's included."""
    logging.config.dictConfig(json.loads(LOG_CONFIG.read_text(encoding="utf-8")))


__all__ = ["LOG_CONFIG", "Formatter", "configure_logging", "use_colour"]
