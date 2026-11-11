"""Paravent — a D&D 5e tool for the gamemaster's table and the players' phones."""

import tomllib
from pathlib import Path



def _version() -> str:
    """The version as pyproject.toml defines it — the one place it is set. Read from the
    file rather than the installed package: the Docker image installs dependencies only
    (``--no-install-project``) and runs the app from its source, pyproject.toml beside it."""
    try:
        project = tomllib.loads(
            (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text("utf-8")
        )
        return str(project["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


# Shown in the user menu and on the login page.
__version__ = _version()
