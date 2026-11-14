"""Every package the app imports is installed in the image (Dockerfile: ``uv sync --no-dev``).

The image installs the project's runtime dependencies from ``uv.lock`` and what they need,
never the test extras. A module imported by ``app/`` from a package that only the tests
pull in works on a developer's machine and fails on the server at start; this test makes
that fail here instead.
"""

from __future__ import annotations

import ast
import importlib.metadata
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _normal(name: str) -> str:
    """A distribution name as the lock file writes it (PEP 503)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _imported_modules() -> dict[str, set[str]]:
    """Every third-party top-level module ``app/`` imports, with the files importing it."""
    found: dict[str, set[str]] = {}
    for path in (ROOT / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top in sys.stdlib_module_names or top in ("app", "__future__"):
                    continue
                found.setdefault(top, set()).add(str(path.relative_to(ROOT)))
    return found


def _runtime_packages() -> set[str]:
    """What ``uv sync --no-dev`` installs: the project's dependencies and theirs, each
    with the extras asked for, as the lock file resolves them (markers taken as met)."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    packages = {_normal(p["name"]): p for p in lock["package"]}
    project = next(p for p in lock["package"] if p.get("source", {}).get("editable") == ".")
    visited: set[tuple[str, tuple[str, ...]]] = set()
    todo = [(_normal(d["name"]), tuple(d.get("extra", ()))) for d in project["dependencies"]]
    while todo:
        name, extras = todo.pop()
        if (name, extras) in visited:
            continue
        visited.add((name, extras))
        package = packages[name]
        wanted = list(package.get("dependencies", ()))
        for extra in extras:
            wanted += package.get("optional-dependencies", {}).get(extra, ())
        todo += [(_normal(d["name"]), tuple(d.get("extra", ()))) for d in wanted]
    return {name for name, _ in visited}


def test_every_imported_package_is_installed_in_the_image() -> None:
    providers = importlib.metadata.packages_distributions()
    runtime = _runtime_packages()
    missing = {
        module: sorted(files)
        for module, files in _imported_modules().items()
        if not any(_normal(d) in runtime for d in providers.get(module, [module]))
    }
    assert not missing, (
        "app/ imports packages the image does not install; add them to "
        f"[project] dependencies in pyproject.toml: {missing}"
    )


def test_the_runtime_set_leaves_out_the_test_extras() -> None:
    runtime = _runtime_packages()
    assert "fastapi" in runtime and "starlette" in runtime  # a dependency's dependency
    assert "pytest" not in runtime and "ruff" not in runtime
