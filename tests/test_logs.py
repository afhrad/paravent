"""One log layout for Paravent, uvicorn and the libraries (app/logs.py)."""

from __future__ import annotations

import json
import logging

import pytest

from app.logs import LOG_CONFIG, Formatter, use_colour


def _line(
    name: str, level: int = logging.INFO, message: str = "hello", colour: bool = False
) -> str:
    record = logging.LogRecord(name, level, __file__, 1, message, None, None)
    return Formatter(colour=colour).format(record)


def test_every_source_gets_the_same_columns() -> None:
    own = _line("app.store.compendium", message="Compendium loaded")
    server = _line("uvicorn.error", message="Application startup complete.")
    http = _line("uvicorn.access", logging.WARNING, '127.0.0.1:1 - "GET /x HTTP/1.1" 404')
    library = _line("mcp.server.streamable_http_manager", message="started")
    for line, source in (
        (own, "store.compendium"),
        (server, "server"),
        (http, "http"),
        (library, "mcp"),
    ):
        time, rest = line.split("  ", 1)
        assert len(time) == 8 and time.count(":") == 2
        assert rest[:9].strip() in ("INFO", "WARNING") and rest[9:27].strip() == source
    assert own.index("Compendium") == server.index("Application") == library.index("started")


def test_colours_mark_time_level_and_source() -> None:
    plain, painted = _line("app.main", logging.ERROR), _line("app.main", logging.ERROR, colour=True)
    assert "\033[" not in plain
    assert "\033[2m" in painted and "\033[31mERROR" in painted and "\033[36mmain" in painted


def test_a_long_name_is_cut_from_the_front() -> None:
    line = _line("app.services.a_very_long_module_name")
    assert "…" in line and "module_name" in line and "app." not in line


def test_no_color_and_force_color(monkeypatch: pytest.MonkeyPatch) -> None:
    class Terminal:
        def isatty(self) -> bool:
            return True

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert use_colour(Terminal()) and not use_colour(object())
    monkeypatch.setenv("NO_COLOR", "1")
    assert not use_colour(Terminal())
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert use_colour(object())


def test_uvicorn_logs_through_the_same_handler() -> None:
    config = json.loads(LOG_CONFIG.read_text(encoding="utf-8"))
    assert config["formatters"]["paravent"]["()"] == "app.logs.Formatter"
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert config["loggers"][name] == {"level": "INFO", "handlers": [], "propagate": True}
