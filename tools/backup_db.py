"""The weekly snapshot of books.db (CLAUDE.md §10.19).

books.db holds what no rescan can rebuild: reading positions, annotations,
shelves and every metadata decision. It is gitignored, so git is no safety
net for it, and until this job the only copies were the ones an upgrade
happened to take.

SQLite's online backup, not a file copy: the service writes while this
runs, and a copy of a WAL database can miss what is still in the WAL. A
snapshot is checked before it counts, and retention runs only after one
has passed, so a failing run never leaves fewer backups than it found.

Weekly snapshots are `books-weekly-<stamp>.db` beside what tools/upgrade.py
saves, and retention matches that prefix only: a pre-upgrade `books-<stamp>.db`
is a rollback point and is never this job's to delete.

    python3 tools/backup_db.py          # take a snapshot now
    python3 tools/backup_db.py --list   # what exists, newest first

STANDARD LIBRARY ONLY: it must still work when the venv does not.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.upgrade_checks import EnvFile  # noqa: E402


class BackupError(Exception):
    """A snapshot could not be taken, or failed its check."""


class BackupSettings:
    """Everything configurable comes from `.env`, the one configuration file."""

    KEYS = ("DB_PATH", "UPGRADE_BACKUP_DIR", "BACKUP_KEEP")

    def __init__(self, db: Path, dest: Path, keep: int) -> None:
        self.db, self.dest, self.keep = db, dest, keep

    @classmethod
    def from_text(cls, text: str, project: Path) -> BackupSettings:
        env = EnvFile.parse(text)
        missing = [key for key in cls.KEYS if not env.get(key)]
        if missing:
            raise BackupError(f".env is missing {missing} — see .env.example")
        try:
            keep = int(env["BACKUP_KEEP"])
        except ValueError as exc:
            raise BackupError(
                f"BACKUP_KEEP must be a number, not {env['BACKUP_KEEP']!r}"
            ) from exc
        return cls(
            (project / env["DB_PATH"]).resolve(),
            Path(env["UPGRADE_BACKUP_DIR"]).expanduser(),
            keep,
        )


class DatabaseBackup:
    PREFIX = "books-weekly-"

    def __init__(
        self,
        db: Path,
        dest: Path,
        keep: int,
        stamp: Callable[[], str] | None = None,
    ) -> None:
        if keep < 1:
            # Zero would delete the snapshot it had just taken.
            raise BackupError(f"BACKUP_KEEP must be at least 1, not {keep}")
        self.db, self.dest, self.keep = Path(db), Path(dest), keep
        self.stamp = stamp or (lambda: time.strftime("%Y%m%d-%H%M%S"))

    def run(self) -> Path:
        """Take, check and keep one snapshot, then apply retention."""
        self.dest.mkdir(parents=True, exist_ok=True)
        final = self.dest / f"{self.PREFIX}{self.stamp()}.db"
        # Not *.db until it has passed, so an interrupted run can never leave
        # something that looks like a backup, or that retention would count.
        part = final.with_suffix(".part")
        try:
            self._copy(part)
            self._check(part)
            part.replace(final)
        except sqlite3.Error as exc:
            raise BackupError(f"{self.db.name}: {exc}") from exc
        finally:
            for leftover in (part, Path(f"{part}-wal"), Path(f"{part}-shm")):
                leftover.unlink(missing_ok=True)
        self._prune()
        return final

    def snapshots(self) -> list[Path]:
        """Newest first. The stamp sorts by time, and unlike an mtime it
        cannot be changed by something merely touching the file."""
        return sorted(self.dest.glob(f"{self.PREFIX}*.db"), reverse=True)

    def _copy(self, part: Path) -> None:
        if not self.db.is_file():
            # mode=rw below refuses too, but says less.
            raise BackupError(f"no database at {self.db}")
        source = f"{self.db.as_uri()}?mode=rw"
        with (
            closing(sqlite3.connect(source, uri=True)) as live,
            closing(sqlite3.connect(part)) as out,
        ):
            live.backup(out)
            # One self-contained file. A WAL-mode snapshot cannot be opened
            # read-only without sidecars; the service puts WAL back on open.
            out.execute("PRAGMA journal_mode=DELETE")

    def _check(self, part: Path) -> None:
        with closing(sqlite3.connect(part)) as db:
            verdict = db.execute("PRAGMA integrity_check").fetchone()[0]
            if verdict != "ok":
                raise BackupError(f"integrity_check said: {verdict}")
            # An empty library passes integrity_check, and keeping it would
            # push a good snapshot out of retention.
            books = db.execute("SELECT count(*) FROM book").fetchone()[0]
        if books < 1:
            raise BackupError("snapshot holds 0 books — refusing to keep it")

    def _prune(self) -> None:
        for old in self.snapshots()[self.keep :]:
            old.unlink()
            print(f"pruned {old.name}")


class BackupCommand:
    @staticmethod
    def main(argv: list[str]) -> int:
        parser = argparse.ArgumentParser(description="Snapshot books.db.")
        parser.add_argument("--list", action="store_true", help="show what exists")
        args = parser.parse_args(argv)
        try:
            settings = BackupSettings.from_text((ROOT / ".env").read_text(), ROOT)
            job = DatabaseBackup(settings.db, settings.dest, settings.keep)
            if args.list:
                for snapshot in job.snapshots():
                    print(snapshot)
                return 0
            taken = job.run()
        except (BackupError, OSError) as exc:
            print(f"FAILED: {exc}")
            return 1
        print(f"snapshot {taken}  {taken.stat().st_size // 1024} KB  integrity ok")
        print(f"{len(job.snapshots())} kept in {settings.dest} (keep={settings.keep})")
        return 0


if __name__ == "__main__":
    sys.exit(BackupCommand.main(sys.argv[1:]))
