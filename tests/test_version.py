"""The server's version: set once in pyproject.toml, shown on screen (app/__init__.py)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from app import __version__

ROOT = Path(__file__).resolve().parent.parent


def test_read_from_the_project_file() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]
    assert __version__ == project["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__)  # major.minor.patch


def test_the_image_carries_the_project_file() -> None:
    """The Docker image runs the app from source: pyproject.toml must be copied in."""
    assert "COPY pyproject.toml pyproject.toml" in (ROOT / "Dockerfile").read_text("utf-8")
    assert "!pyproject.toml" in (ROOT / ".dockerignore").read_text("utf-8")
