# Contributing to Paravent

Thank you for looking. Paravent is a game master's screen for Dungeons & Dragons 5e at a
real table: it keeps the numbers, notes and handouts in order so the people at the table
get more attention. It never plays the game for anyone. A change fits when it supports the
evening at the table; it does not fit when it takes a decision away from the game master or
the players.

## Run it without any rules data

Paravent ships no rulebook content, so a fresh checkout runs empty, and that is fine for
most work:

```bash
uv venv
uv pip install -e ".[dev]"
cp .env.example .env              # then empty PARAVENT_REFERENCE_DIR= and PARAVENT_IMAGE_DIR=
git config core.hooksPath scripts/hooks
./run.sh                          # http://localhost:8000, first visit opens /setup
```

To see monsters, spells and books, point `PARAVENT_REFERENCE_DIR` at a corpus in the 5eTools
format that you have the right to use (README, "Data"). It is read in place and never
copied into the repository or written to.

## Tests and checks

```bash
pytest                        # tests needing a corpus are skipped without one
PARAVENT_TEST_CORPUS=/path/to/5etools/data pytest   # the whole suite against your data
ruff check <files you changed>
ruff format <files you changed>   # not the whole tree: it is not formatted throughout
mypy app/
python website/build.py --check   # when you touched the website or its texts
```

A change comes with tests for the rules it adds, and passing tests before and after. Bugs
in the data layer have hidden behind clean-looking loads before, so a test that checks the
result against the raw files is worth more than one that checks the code ran.

## Before you write code

The documentation lives in `documentation/`, one file per area (combat, characters, names,
translations, themes …); `CLAUDE.md` lists which to read before touching what. A few rules
hold everywhere:

- **Two data roots.** The corpus (`PARAVENT_REFERENCE_DIR`) is read-only and lossless; the
  library (`data/library/`) is the user's own work and strict. Never write under the corpus.
- **References resolve through the world's declared books**, never by first match or newest
  source (TECHSTACK.md §6c). "Not in your books" is a correct answer, not an error.
- **Every user-visible text goes through the translator**, with its key in both
  `app/i18n/en.json` and `app/i18n/de.json`; a test enforces it.
- **The stack stays small:** FastAPI, htpy and HTMX, JSON files with atomic writes, no build
  step, no database, and no requests to the internet unless an optional Claude feature
  is switched on with its key.

## What may not go into the repository

Paravent is MIT licensed, and everything in the repository has to be passable under terms
anyone can follow. So no rulebook prose, tables or pictures from published books: not in
code, tests, fixtures, documentation, sample pages or screenshots. Material from the System
Reference Documents (CC BY 4.0) is fine and is listed in [`NOTICE.md`](NOTICE.md); add an
entry there when you add more. When a test needs rules text, write a few sentences of your
own or read it from the corpus at test time. Examples use Paravent's own setting, the
Sunken Marches.

## Commits and pull requests

- One commit per change, its subject one plain sentence saying what changed, written for a
  reader of the project: the website's "What's new" is built from the subjects.
- The body says why, in a few lines, when that is not obvious.
- Commit messages name no tool, model or assistant (`scripts/hooks/commit-msg` removes such
  lines). Don't change the version by hand; the pre-commit hook raises it.
- A feature that adds a new area of Paravent updates its entry in `website/content/en.json`
  and `de.json` and runs `python website/build.py` (website/README.md).

## Licence

By contributing you agree that your contribution is released under the project's
[MIT License](LICENSE).
