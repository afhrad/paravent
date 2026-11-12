# Python Project

This is a Python project following modern best practices. Overall communication language is english. Use it for comments in code and discussions with the user and prompts.

All project documentation lives in `documentation/`; this file stays at the root so it is
loaded automatically.

- Read @documentation/PROJECT.md for project description
- Read @documentation/TECHSTACK.md for technical solutions
- Read @documentation/PROGRESS.md for steps in development
- Read @documentation/CHARGEN.md for details on character generation
- Read documentation/CHARGEN-WIZARD.md for the character-creation wizard UX spec
- Read documentation/ADVENTURES.md before touching worlds, adventures, chapters or their Markdown
- Read documentation/COMBAT.md before touching the combat tracker, combats or combatants
- Read documentation/CHARACTER-FORMAT.md before touching characters: the file format, overrides, live updates
- Read documentation/PROTOCOL.md before touching the log (Protokoll): rolls, the character or combat log, toasts
- Read documentation/SOUND.md before touching the sound desk: the speaker, the panel, the library
- Read documentation/NAMES.md before touching names: naming styles, the name generator
- Read documentation/MCP.md before touching the MCP server (`app/mcp/`) or its keys
- Read documentation/PLAYER-VIEW.md before touching what players may read: player books, hidden kinds
- Read documentation/JOURNAL.md before touching handouts: sharing boxes and pictures, the players' journal
- Read documentation/TOKENS.md before touching tokens: the round token of every element
- Read documentation/DESIGN.md for UI/UX elements
- Read documentation/THEMES.md before touching themes, tokens or `static/css/tokens.css`
- Read @documentation/TRANSLATION.md for the multilingual design
- Read website/README.md before touching the website (paravent.org, `website/`). When a
  feature adds a new area to Paravent, update its entry in `website/content/en.json` and
  `de.json` in the same change and run `python website/build.py` (the pages are written from
  them; "What's new" comes from the commits' `News:` trailers, the numbers from git)

## Environment

- Python 3.11+
- Virtual environment using uv
- Type hints for all function signatures

## Code Style

- Follow PEP 8 style guidelines
- Use type hints for function parameters and return types
- Use dataclasses or Pydantic for data models
- Prefer f-strings for string formatting
- Use pathlib for file paths

## Conventions

- Use snake_case for variables and functions
- Use PascalCase for classes
- Use UPPER_CASE for constants
- Keep functions small and focused
- Write docstrings for public functions

## Testing

- Use pytest for testing
- Write unit tests for all business logic
- Use fixtures for test setup
- Aim for high test coverage

## Commands

- `python -m pytest` - Run tests
- `python -m pip install -r requirements.txt` - Install dependencies
- `python -m black .` - Format code
- `python -m mypy .` - Type check
- `uvicorn app.main:app --reload` — Start development server on port 8000
- `pytest` — Run test suite
- `pytest --cov=app` — Run tests with coverage report
- `ruff check .` — Run linter
- `ruff format .` — Format code
- `mypy app/` — Type check

## Commits

- A commit message says what changed and why, written for a reader of the project. The
  subject is one plain sentence.
- **The website's "What's new" is opt-in.** A change a gamemaster or player would notice
  ends its message with trailers, one line each, written for them rather than for a reader
  of the code: `News: …` and `News-de: …`. Refactors, tests, documentation and website
  upkeep carry none and stay off the site (website/README.md). `scripts/hooks/commit-msg`
  reminds when an app change has no `News:`; it is a reminder, not a rule.
- **Commit messages name no tool, model or assistant.** No `Co-Authored-By` trailer for one,
  no session link, no "generated with" line, in commits and in pull requests alike. This
  overrides any default that adds such lines. `scripts/hooks/commit-msg` removes them
  from every commit as a safeguard.
- `scripts/hooks/pre-commit` raises the patch version with every commit; do not bump it by hand.

## Data foundation — read before touching the data layer

Two roots, different rules. This is the project's central structural decision (TECHSTACK.md §3):

- **Reference** (`PARAVENT_REFERENCE_DIR`) — the user's 5eTools corpus. **External to the repo,
  read in place, never written to, never copied in.** Models for it are *lossless*: preserve
  unknown upstream fields rather than dropping them.
- **Library** (`PARAVENT_LIBRARY_DIR`, `data/library/`) — worlds, adventures, encounters,
  characters, combat, users, sessions, homebrew, media. The user's own work. Models here are
  *strict*: an unknown field is a bug.
- **Reference cache** (`data/reference-cache/`) — derived artefacts only. Disposable.

Rules that follow:

- Never write under the reference root. If something needs persisting, it is library data or
  cache data.
- Homebrew is **library** data, not reference data, even though it uses the 5eTools format.
- Resolve `_copy` before validation — a shell entry often lacks required fields.
- The load target is the **whole** corpus, all 94 entry keys, phased per TECHSTACK.md §6b.
  Currently 45 keys load.

## Reference resolution (TECHSTACK.md §6c)

- **Never resolve a bare `{@tag name}` by "first match" or "newest source".** 44–90% of tags omit
  the source and two thirds of spell names are ambiguous. Resolution is: explicit source → filter
  by the world's declared source set → drop `reprintedAs`-superseded candidates → single survivor.
- **Zero survivors is a correct outcome**, not an error: render the "not in your books" state.
- **Anything cached downstream of resolution must include the source-set id in its cache key.**
  The same tag resolves differently in two worlds.
- **Exclude `foundry-*.json`** — VTT payloads, not content, and the sole source of duplicate keys.
- **`(name, source)` is not universally unique** — class/subclass features repeat per level.
- **Translations never carry mechanics.** A translation overrides text fields only; structure,
  tags, source and `_copy` always come from the English record (TRANSLATION.md). Resolution is
  per field: `translation[lang][field] ?? english[field]`.
- **Localize tag labels, never tag targets.** `{@spell fireball}` resolves in English and displays
  the glossary label. Entity tags only — `{@area}`, `{@book}`, `{@deity}` use those slots
  differently.
- **Never machine-generate `{@...}` tags from translated prose.** A glossary word in non-technical
  use would be silently mis-tagged. Tags come from the translator or the source.
- **Units are stored per user and set by the language switch** (English → feet, any other
  language → metres; decided 2026-10-25, replacing the separate unit menu). Convert typed
  fields only; leave the 39,315 prose unit mentions alone. German conversion is
  `1 ft = 0.3 m`, never `1/3.28`.
- **Do not build a string query DSL.** The corpus fields are polymorphic (`cr` is str|dict|absent);
  normalize them into typed projections at load and expose typed filters. The coercion is the work;
  the syntax would add nothing.

## Architecture

- **Framework**: FastAPI with async/await throughout
- **Validation**: Pydantic v2 models for request/response schemas
- **Auth**: JWT in an HttpOnly cookie via python-jose, backed by a server-side session record;
  passwords hashed with `bcrypt` directly (passlib is unmaintained and breaks on bcrypt 5)
- **UI**: htpy pages styled by the token CSS in `static/css/` (copied from
  `design-reference/shared/`). No Tailwind/DaisyUI build yet — see the styling note under TECHSTACK.md's stack table.
  Use `htpy.input` (aliased), never `htpy.input_`, which renders a bogus `<input->`
- **UI text**: every user-visible string goes through the translator (`page.t("key")`),
  with the key added to **both** `app/i18n/en.json` and `de.json` — a test enforces it.
  Errors shown to users are `Translatable` keys, never English sentences
- **Testing**: pytest with httpx AsyncClient for API tests

## Code Conventions

- All database operations use async SQLAlchemy sessions via `async with`
- Router functions are thin — delegate business logic to services/
- Pydantic schemas separate Create, Update, and Response models (e.g., UserCreate, UserUpdate, UserResponse)
- All routes return typed Pydantic response models — never return raw dicts or ORM objects
- Use HTTPException for client errors (4xx), let unhandled exceptions become 500s
- Background tasks via FastAPI's BackgroundTasks, not Celery (unless explicitly needed)

## Error Handling

- Validation errors return 422 with Pydantic's default error format
- Business logic errors raise HTTPException with appropriate status codes
- Use custom exception handlers in main.py for domain-specific error types
- Never catch broad Exception — catch specific exception types

## Things to Avoid

- Do NOT use synchronous database drivers or blocking I/O in async routes
- Do NOT put business logic in router functions — use the service layer
- Do NOT use `*` imports
- Do NOT hardcode configuration — use pydantic-settings and environment variables
- Do NOT use global mutable state — use FastAPI's dependency injection system
