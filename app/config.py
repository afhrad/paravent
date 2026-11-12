"""Application configuration, sourced from environment variables.

The path settings encode the project's central structural decision
(documentation/TECHSTACK.md §3): data lives in **two roots with different rules**,
separated by lifetime rather than by category.

* **Reference** is somebody else's 5eTools corpus. It arrives as a snapshot, is replaced
  wholesale on re-import, and is **never written to**. It lives outside the project, so
  :attr:`Settings.reference_dir` has no useful default — a fresh checkout must be pointed
  at a corpus before the compendium can load.
* **Library** is everything created *with* Paravent: worlds, adventures, characters,
  combat, homebrew, media, translations. It is the user's work, the thing worth backing
  up, and it must survive a corpus swap untouched.
* **Reference cache** holds artefacts derived from the corpus. It is disposable by
  definition — deleting it costs only the time to rebuild.
"""

from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Where the code's own assets live. Not configuration: these ship with the code and
# move with it, unlike the data roots below.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"


CORPUS_FOLDER = "data"
CORPUS_MARKER = "books.json"


class Settings(BaseSettings):
    """Runtime settings. Override any field via a PARAVENT_-prefixed env var."""

    model_config = SettingsConfigDict(
        env_prefix="PARAVENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Reference: read-only, external, never written to -------------------
    # No sensible default exists: the corpus is the user's and lives outside the
    # project. The empty path means "not configured" and is reported at startup
    # rather than silently loading nothing.
    reference_dir: Path = Path()

    # The 5eTools images, shipped apart from the data (``bestiary/MM/Goblin.webp`` …).
    # Read in place like the corpus. Empty means "img next to the corpus folder".
    image_dir: Path = Path()

    # --- Library: everything authored in Paravent. Back this up. ------------
    library_dir: Path = Path("data/library")

    # --- Derived artefacts: disposable, rebuilt on demand -------------------
    reference_cache_dir: Path = Path("data/reference-cache")
    # Keep the parsed corpus there (app/store/snapshot.py), so a restart with nothing
    # changed takes about half the time; any change to the corpus or homebrew makes a new one.
    reference_snapshot: bool = True

    # --- Backups of the library (app/services/backup.py) ---------------------
    # Where the zip files go; empty means no backups. Never the reference data.
    backup_dir: Path = Path()
    # Local times of day to back up at, "03:00" or "03:00,15:00"; empty: on demand only.
    backup_at: str = ""
    # How many backups to keep; the oldest beyond this are deleted.
    backup_keep: int = 14

    default_language: str = "en"
    fallback_language: str = "en"

    # Strict mode makes the loader raise on malformed compendium entries instead
    # of collecting them as errors. Useful in tests and during import.
    strict_validation: bool = False

    # Watch JSON files and reload the in-memory stores on change.
    watch_files: bool = True

    # --- Auth -----------------------------------------------------------------
    # Signs the session JWT. Empty means "generate one on first start and keep it in
    # the library" (see app/services/auth.py), so a fresh install logs in without
    # configuration while an operator can still pin the key via PARAVENT_SECRET_KEY.
    secret_key: str = ""

    # How long a login lasts. Generous by default: this runs on a home network, and
    # being logged out mid-session at the table is the failure that matters.
    session_days: int = 30

    # Mark the session cookie Secure. Off by default because the documented
    # deployment is plain HTTP on the local network (documentation/TECHSTACK.md §7);
    # a browser silently drops a Secure cookie over HTTP, which would make login
    # appear to do nothing. Turn on when serving behind TLS.
    cookie_secure: bool = False

    # --- Machine translation (documentation/TRANSLATION.md §7) ---------------------
    # Drafts of translated corpus text from the Claude API. Off unless a key is set:
    # it sends corpus text to an external service and costs money per passage.
    translate_api_key: str = ""
    translate_model: str = "claude-opus-5-5"
    # Translating needs little reasoning; "low" keeps the cost per passage down.
    translate_effort: str = "low"

    @model_validator(mode="after")
    def _find_corpus_in_release(self) -> Self:
        """A 5eTools release is the whole site, its corpus in ``data/``: pointed at the
        release, read that folder, which every reader takes for the root."""
        inner = self.reference_dir / CORPUS_FOLDER
        if not (self.reference_dir / CORPUS_MARKER).is_file() and (inner / CORPUS_MARKER).is_file():
            self.reference_dir = inner
        return self

    @property
    def reference_configured(self) -> bool:
        """True when a reference corpus has been pointed at.

        A fresh checkout has none — Paravent ships no 5e content
        (documentation/TECHSTACK.md §8) — so this is a normal state to report, not an
        error to raise.
        """
        return str(self.reference_dir) not in ("", ".")

    @property
    def images_root(self) -> Path | None:
        """Where the corpus's images are, when that folder exists."""
        if str(self.image_dir) not in ("", "."):
            root = self.image_dir
        elif self.reference_configured:
            root = self.reference_dir.parent / "img"
        else:
            return None
        return root if root.is_dir() else None

    @property
    def backup_configured(self) -> bool:
        """True when a backup folder is set."""
        return str(self.backup_dir) not in ("", ".")

    @property
    def homebrew_dir(self) -> Path:
        """Own content in 5eTools format, loaded after the corpus so it overrides.

        Homebrew is **library** data despite sharing the reference format: it is the
        user's work and belongs on the backed-up side of the line.
        """
        return self.library_dir / "homebrew"

    @property
    def media_dir(self) -> Path:
        """Images, sounds, maps and tokens — referenced from JSON, never stored in it."""
        return self.library_dir / "media"

    @property
    def secret_key_file(self) -> Path:
        """Where a generated signing key is kept when none is configured.

        Library, not reference cache: losing it logs every user out, so it belongs on
        the backed-up side with the sessions it signs.
        """
        return self.library_dir / ".secret_key"

    @property
    def i18n_dir(self) -> Path:
        """Translation overlays and UI string catalogues (documentation/TRANSLATION.md §5)."""
        return self.library_dir / "i18n"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
