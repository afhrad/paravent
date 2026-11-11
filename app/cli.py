"""Command-line account management, until the gamemaster screen can do it.

    python -m app.cli user add alice                    # a player
    python -m app.cli user add bob --role gamemaster
    python -m app.cli user list
    python -m app.cli user passwd alice
    python -m app.cli schema                            # character-file JSON Schema
    python -m app.cli rules                             # encounter tables from the books
    python -m app.cli glossary fill de                  # list every name to give, as "-"
    python -m app.cli corpus check                      # is the 5eTools corpus readable?
    python -m app.cli backup run                        # back the library up now
    python -m app.cli backup list                       # the backups kept

Passwords are prompted for, never taken as an argument — an argument ends up in shell
history and in the process list. Writes go to ``library/users.json`` through the same
store the server uses, so a running server sees the change through its file watcher.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT, get_settings
from app.models.gamestate import Role
from app.models.pc import character_json_schema
from app.services.auth import AuthError, AuthService
from app.services.backup import BackupError
from app.store import rulestables
from app.store.gamestate import GameStateStore


def _service() -> AuthService:
    settings = get_settings()
    return AuthService(GameStateStore(settings.library_dir), settings)


def _prompt_password() -> str:
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Confirm password: "):
        raise AuthError("error.password_mismatch")
    return password


def _add(args: argparse.Namespace) -> int:
    auth = _service()
    user = auth.create_user(
        args.username, _prompt_password(), Role(args.role), args.display_name or ""
    )
    print(f"Created {user.role.value} {user.username!r} ({user.id}).")
    return 0


def _list(_args: argparse.Namespace) -> int:
    auth = _service()
    users = sorted(auth.users(), key=lambda u: (u.role.value, u.username.casefold()))
    if not users:
        print("No users yet. Open the app to create the gamemaster, or run `user add`.")
        return 0
    for user in users:
        state = "" if user.active else "  (inactive)"
        print(f"{user.role.value:<11} {user.username:<20} {user.display_name}{state}")
    return 0


def _passwd(args: argparse.Namespace) -> int:
    auth = _service()
    user = auth.find_user(args.username)
    if user is None:
        raise AuthError("error.user_unknown", username=args.username)
    auth.set_password(user.id, _prompt_password())
    print(f"Password changed for {user.username!r}; their sessions were ended.")
    return 0


SCHEMA_PATH = PROJECT_ROOT / "documentation" / "character.schema.json"


def _schema(args: argparse.Namespace) -> int:
    """Write the character-file JSON Schema (documentation/CHARACTER-FORMAT.md)."""
    target = Path(args.output) if args.output else SCHEMA_PATH
    target.write_text(json.dumps(character_json_schema(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {target}")
    return 0


def _rules(args: argparse.Namespace) -> int:
    """Extract the encounter-difficulty tables from the corpus's rulebooks, and write the
    ones Paravent ships: those the System Reference Documents hold too. The 2014 guide's
    are read from the corpus when a fight is rated (app/store/rulestables.py)."""
    settings = get_settings()
    if not settings.reference_configured:
        print("error: PARAVENT_REFERENCE_DIR is not set", file=sys.stderr)
        return 1
    try:
        rulestables.write(
            settings.reference_dir, Path(args.output) if args.output else rulestables.RULES_FILE
        )
    except rulestables.RulesTableMissing as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {args.output or rulestables.RULES_FILE}")
    return 0


def _translations_import(args: argparse.Namespace) -> int:
    """A translated 5eTools corpus as translations over the English one: matched per
    record, kept per passage where the links are the same, drafts unless ``--reviewed``,
    the table's own translations kept unless ``--replace``."""
    from app.services.corpus_import import CorpusImport
    from app.services.i18n import is_language_code
    from app.store.registry import StoreRegistry

    settings = get_settings()
    folder = Path(args.folder).expanduser()
    if not settings.reference_configured:
        print("error: PARAVENT_REFERENCE_DIR is not set", file=sys.stderr)
        return 1
    if not folder.is_dir():
        print(f"error: {folder} is not a folder", file=sys.stderr)
        return 1
    if not is_language_code(args.lang) or args.lang == "en":
        print(f"error: {args.lang!r} is not a language to translate into", file=sys.stderr)
        return 1
    if folder.resolve() == settings.reference_dir.resolve():
        print("error: that is the English corpus itself", file=sys.stderr)
        return 1
    registry = StoreRegistry(settings)
    registry.load()
    report = CorpusImport(
        compendium=registry.compendium,
        fluff=registry.fluff,
        books=registry.rulebooks,
        adventures=registry.published,
        store=registry.translations,
        glossary=registry.glossary,
    ).run(folder, args.lang, reviewed=args.reviewed, replace=args.replace, dry_run=args.dry_run)
    print(f"{'kind':<22}{'records':>9}{'passages':>10}{'kept':>7}{'links':>7}{'unmatched':>11}")
    for kind in report.kinds:
        print(
            f"{kind:<22}{report.records[kind]:>9}{report.passages[kind]:>10}"
            f"{report.kept[kind]:>7}{report.links[kind]:>7}{report.unmatched[kind]:>11}"
        )
    print(f"Names for the glossary: {sum(report.names.values())}")
    if report.misaligned:
        print("Chapters not lined up with the English, left out: " + ", ".join(report.misaligned))
    print(
        "Nothing written (dry run)."
        if args.dry_run
        else "Imported"
        + (" as reviewed." if args.reviewed else " as drafts: confirm them under Translations.")
    )
    return 0


def _glossary_fill(args: argparse.Namespace) -> int:
    """Every corpus entry the language's glossary lacks, added as ``"-"`` to name by hand.

    Magic items made from a generic variant ("+1 Longsword") are left out: their names
    are composed from the base item's and the variant's.
    """
    from app.store.registry import StoreRegistry

    settings = get_settings()
    if not settings.reference_configured:
        print("error: PARAVENT_REFERENCE_DIR is not set", file=sys.stderr)
        return 1
    registry = StoreRegistry(settings)
    registry.load()
    compendium = registry.compendium
    entries = {
        category: sorted(
            {
                str(getattr(entry, "name", ""))
                for entry in compendium.all(category)
                if not (category == "item" and getattr(entry, "genericVariant", None))
            }
        )
        for category in compendium.categories()
    }
    added = registry.glossary.fill(args.lang, entries)
    for category, count in sorted(added.items()):
        print(f"{category:<20} {count:>6}")
    print(f'Added {sum(added.values())} names as "-" to {registry.glossary.path(args.lang)}')
    return 0


def _corpus_check(args: argparse.Namespace) -> int:
    """Check every corpus file against what Paravent reads; exit 1 on an error (on a
    warning too with ``--strict``)."""
    from app.services.corpus_check import REPORT_FILE, CorpusCheck, load_report, save_report
    from app.services.library_check import LibraryCheck
    from app.store.registry import StoreRegistry

    settings = get_settings()
    if not settings.reference_configured:
        print("error: PARAVENT_REFERENCE_DIR is not set", file=sys.stderr)
        return 1
    registry = StoreRegistry(settings)
    registry.load()
    path = settings.reference_cache_dir / REPORT_FILE
    check = CorpusCheck(
        settings.reference_dir,
        settings.homebrew_dir,
        registry.compendium,
        registry.fluff,
        registry.lookups,
        LibraryCheck(registry),
    )
    report = check.run(references=args.references, previous=load_report(path))
    if not args.no_save:
        save_report(path, report)
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print_report(report, verbose=args.verbose)
    failed = report.errors or (args.strict and report.warnings)
    return 1 if failed else 0


def print_report(report: Any, *, verbose: bool = False) -> None:
    """The corpus check as text: what was read, what is wrong, what changed."""
    version = report.version
    print(
        f"5eTools {version.version} ({version.date})" if version else "5eTools, version unknown",
        f"at {report.root}",
    )
    unread = len(report.unread)
    print(
        f"{report.files} files: {report.files_read} read, {unread} not read, "
        f"{report.vtt_files} Foundry payloads skipped"
    )
    print(
        f"{report.entries:,} entries, {report.fluff:,} fluff records, "
        f"checked in {report.seconds:.1f} s"
    )
    for group in report.groups:
        print(f"\n{group.severity.value.upper()} {group.kind.value} ({group.count})")
        shown = group.items if verbose else group.items[:10]
        for finding in shown:
            where = " · ".join(x for x in (finding.file, finding.subject) if x)
            print(f"  {where}" + (f" — {finding.detail}" if finding.detail else ""))
        if group.count > len(shown):
            print(f"  … and {group.count - len(shown)} more")
    odd = [c for c in report.counts if verbose or not c.matches or c.repeated or c.invalid]
    if odd:
        print("\nCounts (rows in the files → entries loaded)")
        for c in odd:
            mark = "ok" if c.matches else "MISMATCH"
            print(
                f"  {c.category:<18} {c.rows:>6} rows − {c.invalid} invalid − {c.unresolved}"
                f" unresolved − {c.repeated} repeated + {c.made} made = {c.expected:>6}"
                f" · loaded {c.loaded:>6}  {mark}"
            )
    planned = [k for k in report.keys if k.status == "planned"]
    if planned:
        print("\nNot loaded yet: " + ", ".join(f"{k.key} ({k.rows})" for k in planned))
    if verbose:
        for category, fields in report.fields.items():
            print(f"  {category}: kept, not read: {', '.join(sorted(fields))}")
    changes = report.changes
    if changes is not None:
        print(f"\nSince the check of {changes.since}:", "nothing changed" if changes.empty else "")
        if changes.version_before and version and changes.version_before != version.version:
            print(f"  version {changes.version_before} → {version.version}")
        for key in changes.new_keys:
            print(f"  new key {key}")
        for key in changes.gone_keys:
            print(f"  key gone {key}")
        for category, (before, after) in changes.rows.items():
            print(f"  {category}: {before} → {after} rows")
        for category, fields in changes.new_fields.items():
            print(f"  {category}: new fields {', '.join(fields)}")
        for kind, (before, after) in changes.library.items():
            print(f"  {kind}: {before} → {after}")
    verdict = "OK" if report.ok else f"FAILED: {report.errors} error(s)"
    print(f"\n{verdict}, {report.warnings} warning(s)")


def _backup_service() -> Any:
    from app.services.backup import BackupService

    settings = get_settings()
    if not settings.backup_configured:
        raise BackupError("PARAVENT_BACKUP_DIR is not set")
    return BackupService(
        settings.library_dir, settings.backup_dir, settings.backup_keep, settings.backup_at
    )


def _backup_run(args: argparse.Namespace) -> int:
    """Back the library up now (only the library, never the reference data)."""
    service = _backup_service()
    backup = service.run(force=not args.if_changed)
    if backup is None:
        print("Nothing changed since the last backup; none made.")
    else:
        print(f"Wrote {backup.path} ({backup.size:,} bytes)")
    return 0


def _sound_import(args: argparse.Namespace) -> int:
    """Add every audio file of a folder to the sound library."""
    from app.services.sound import AUDIO_TYPES, SoundError, SoundLibrary
    from app.store.gamestate import GameStateStore

    settings = get_settings()
    store = GameStateStore(settings.library_dir)
    library = SoundLibrary(store.sounds, store.sound_scenes, settings.library_dir / "sounds")
    added = skipped = 0
    for path in sorted(p for p in args.folder.rglob("*") if p.is_file()):
        if path.suffix.lower() not in AUDIO_TYPES:
            continue
        with path.open("rb") as data:
            try:
                library.add(path.name, data, args.kind, credit=args.credit)
                added += 1
            except SoundError as exc:
                print(exc, file=sys.stderr)
                skipped += 1
    print(f"{added} added, {skipped} skipped")
    return 0


def _backup_list(_args: argparse.Namespace) -> int:
    """The backups kept, newest first."""
    service = _backup_service()
    found = service.backups()
    if not found:
        print(f"No backups in {service.target}.")
    for backup in found:
        print(f"{backup.made:%Y-%m-%d %H:%M:%S}  {backup.size:>12,}  {backup.path.name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The ``python -m app.cli`` argument parser."""
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n")[0])
    groups = parser.add_subparsers(dest="group", required=True)

    user = groups.add_parser("user", help="manage accounts").add_subparsers(
        dest="command", required=True
    )
    add = user.add_parser("add", help="create an account")
    add.add_argument("username")
    add.add_argument("--role", choices=[r.value for r in Role], default=Role.PLAYER.value)
    add.add_argument("--display-name")
    add.set_defaults(handler=_add)

    user.add_parser("list", help="list accounts").set_defaults(handler=_list)

    passwd = user.add_parser("passwd", help="change a password")
    passwd.add_argument("username")
    passwd.set_defaults(handler=_passwd)

    schema = groups.add_parser("schema", help="write the character-file JSON Schema")
    schema.add_argument("--output", help="where to write it (default: documentation/)")
    schema.set_defaults(handler=_schema)

    rules = groups.add_parser("rules", help="extract encounter-difficulty tables from the books")
    rules.add_argument("--output", help="where to write it (default: data/rules/)")
    rules.set_defaults(handler=_rules)

    glossary = groups.add_parser("glossary", help="the term glossary").add_subparsers(
        dest="command", required=True
    )
    fill = glossary.add_parser("fill", help='add every missing entry name as "-"')
    fill.add_argument("lang", help="the language code, e.g. de")
    fill.set_defaults(handler=_glossary_fill)

    translations = groups.add_parser(
        "translations", help="translated text over the English corpus"
    ).add_subparsers(dest="command", required=True)
    importing = translations.add_parser(
        "import", help="import a translated 5eTools corpus (TRANSLATION.md §7)"
    )
    importing.add_argument("folder", help="the translated corpus, laid out as 5eTools")
    importing.add_argument("lang", help="the language code it is in, e.g. de")
    importing.add_argument(
        "--dry-run", action="store_true", help="say what would be imported, write nothing"
    )
    importing.add_argument(
        "--reviewed", action="store_true", help="take the passages as reviewed, not as drafts"
    )
    importing.add_argument(
        "--replace", action="store_true", help="also replace passages already translated here"
    )
    importing.set_defaults(handler=_translations_import)

    corpus = groups.add_parser("corpus", help="the 5eTools corpus").add_subparsers(
        dest="command", required=True
    )
    check = corpus.add_parser("check", help="check every file is in a shape Paravent reads")
    check.add_argument(
        "--references", action="store_true", help="also check every tag (a few seconds more)"
    )
    check.add_argument("--strict", action="store_true", help="fail on warnings too")
    check.add_argument("--verbose", action="store_true", help="every finding and count")
    check.add_argument("--json", action="store_true", help="the report as JSON")
    check.add_argument(
        "--no-save", action="store_true", help="do not keep it as the report to compare with"
    )
    check.set_defaults(handler=_corpus_check)

    sound = groups.add_parser("sound", help="the sound desk's library").add_subparsers(
        dest="action", required=True
    )
    import_sounds = sound.add_parser("import", help="add every audio file of a folder")
    import_sounds.add_argument("folder", type=Path)
    import_sounds.add_argument("--kind", choices=("effect", "ambience", "music"), default="effect")
    import_sounds.add_argument("--credit", default="", help="who made them, and the licence")
    import_sounds.set_defaults(handler=_sound_import)

    backup = groups.add_parser("backup", help="backups of the library").add_subparsers(
        dest="command", required=True
    )
    run = backup.add_parser("run", help="back the library up now")
    run.add_argument(
        "--if-changed", action="store_true", help="only when something changed since the last"
    )
    run.set_defaults(handler=_backup_run)
    backup.add_parser("list", help="the backups kept").set_defaults(handler=_backup_list)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI, returning the process exit code."""
    args = build_parser().parse_args(argv)
    try:
        code: int = args.handler(args)
    except (AuthError, BackupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
