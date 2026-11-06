"""tools/backup_db.py and backup-db.sh — the weekly snapshot of books.db.

books.db holds what no rescan can rebuild: reading positions, annotations,
shelves and every metadata decision. What must never be wrong:

  * a snapshot includes writes still sitting in the live WAL
  * a snapshot that fails its check is never kept, and never costs an old one
  * retention prunes weekly snapshots only — never what an upgrade saved
  * a failed run tells a person, because nobody reads a log
"""

from __future__ import annotations

import os
import plistlib
import shutil
import sqlite3
import stat
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.backup_db import BackupError, BackupSettings, DatabaseBackup  # noqa: E402


class Stamps:
    """One name per call: two runs inside one second must not collide here."""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> str:
        self.n += 1
        return f"20261108-0347{self.n:02d}"


def make_db(where: Path, books: int = 3) -> Path:
    with closing(sqlite3.connect(where)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE book (id INTEGER PRIMARY KEY, path TEXT)")
        db.executemany(
            "INSERT INTO book (path) VALUES (?)",
            [(f"b{i}.epub",) for i in range(books)],
        )
        db.commit()
    return where


def backup(db: Path, dest: Path, keep: int = 8) -> DatabaseBackup:
    return DatabaseBackup(db, dest, keep, stamp=Stamps())


def count_books(where: Path) -> int:
    uri = f"{where.as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        return db.execute("SELECT count(*) FROM book").fetchone()[0]


class TestSnapshot:
    def test_includes_writes_still_in_the_live_wal(self, tmp_path):
        """A plain copy of books.db would miss exactly these rows."""
        src = make_db(tmp_path / "books.db")
        with closing(sqlite3.connect(src)) as writer:
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO book (path) VALUES ('in-the-wal.epub')")
            writer.commit()
            assert (tmp_path / "books.db-wal").stat().st_size > 0
            taken = backup(src, tmp_path / "out").run()
        assert count_books(taken) == 4

    def test_is_one_self_contained_file(self, tmp_path):
        """Opens read-only with no sidecars, which a WAL-mode copy cannot."""
        taken = backup(make_db(tmp_path / "books.db"), tmp_path / "out").run()
        assert count_books(taken) == 3
        assert [p.name for p in (tmp_path / "out").iterdir()] == [taken.name]

    def test_creates_the_destination(self, tmp_path):
        out = tmp_path / "not" / "yet"
        assert backup(make_db(tmp_path / "books.db"), out).run().parent == out

    def test_is_named_as_a_weekly_snapshot(self, tmp_path):
        taken = backup(make_db(tmp_path / "books.db"), tmp_path / "out").run()
        assert taken.name.startswith("books-weekly-")
        assert taken.suffix == ".db"


class TestRefusals:
    def test_a_missing_database_is_an_error_not_a_new_empty_file(self, tmp_path):
        src = tmp_path / "books.db"
        with pytest.raises(BackupError):
            backup(src, tmp_path / "out").run()
        assert not src.exists()

    def test_an_empty_library_is_refused(self, tmp_path):
        """It passes integrity_check, then would push a good snapshot out."""
        src = make_db(tmp_path / "books.db", books=0)
        out = tmp_path / "out"
        with pytest.raises(BackupError, match="0 books"):
            backup(src, out).run()
        assert list(out.iterdir()) == []

    def test_a_database_with_no_book_table_is_refused(self, tmp_path):
        src = tmp_path / "books.db"
        with closing(sqlite3.connect(src)) as db:
            db.execute("CREATE TABLE other (id INTEGER)")
            db.commit()
        out = tmp_path / "out"
        with pytest.raises(BackupError):
            backup(src, out).run()
        assert list(out.iterdir()) == []

    def test_a_corrupt_database_is_refused(self, tmp_path):
        src = make_db(tmp_path / "books.db", books=5000)
        with closing(sqlite3.connect(src)) as db:
            db.execute("PRAGMA journal_mode=DELETE")
        with src.open("r+b") as handle:
            handle.seek(4096 * 2)
            handle.write(b"\xff" * 4096)
        out = tmp_path / "out"
        with pytest.raises(BackupError):
            backup(src, out).run()
        assert list(out.iterdir()) == []

    def test_a_refused_run_prunes_nothing(self, tmp_path):
        out = tmp_path / "out"
        good = make_db(tmp_path / "books.db")
        job = DatabaseBackup(good, out, 1, stamp=Stamps())
        kept = job.run()
        empty = make_db(tmp_path / "empty.db", books=0)
        with pytest.raises(BackupError):
            DatabaseBackup(empty, out, 1, stamp=job.stamp).run()
        assert [p.name for p in out.iterdir()] == [kept.name]

    @pytest.mark.parametrize("keep", [0, -1])
    def test_a_retention_that_keeps_nothing_is_refused(self, tmp_path, keep):
        with pytest.raises(BackupError):
            DatabaseBackup(tmp_path / "books.db", tmp_path / "out", keep)


class TestRetention:
    def test_keeps_the_newest(self, tmp_path):
        job = backup(make_db(tmp_path / "books.db"), tmp_path / "out", keep=2)
        taken = [job.run() for _ in range(4)]
        assert sorted((tmp_path / "out").iterdir()) == taken[-2:]

    def test_never_touches_what_an_upgrade_saved(self, tmp_path):
        """tools/upgrade.py writes books-<stamp>.db and upgrade-prev/ here."""
        out = tmp_path / "out"
        (out / "upgrade-prev").mkdir(parents=True)
        pre_upgrade = out / "books-20261023-155620.db"
        pre_upgrade.write_bytes(b"pre-upgrade")
        attention = out / "upgrade-attention.json"
        attention.write_text("{}")
        job = backup(make_db(tmp_path / "books.db"), out, keep=1)
        for _ in range(3):
            job.run()
        assert pre_upgrade.read_bytes() == b"pre-upgrade"
        assert attention.exists() and (out / "upgrade-prev").is_dir()

    def test_listing_shows_only_weekly_snapshots_newest_first(self, tmp_path):
        out = tmp_path / "out"
        job = backup(make_db(tmp_path / "books.db"), out)
        first, second = job.run(), job.run()
        (out / "books-20261023-155620.db").write_bytes(b"x")
        assert job.snapshots() == [second, first]


class TestSettings:
    TEXT = (
        "DB_PATH=books.db\n"
        "UPGRADE_BACKUP_DIR=~/somewhere\n"
        "BACKUP_KEEP=5\n"
    )

    def test_reads_env(self, tmp_path):
        s = BackupSettings.from_text(self.TEXT, tmp_path)
        assert s.db == (tmp_path / "books.db").resolve()
        assert s.dest == Path("~/somewhere").expanduser()
        assert s.keep == 5

    @pytest.mark.parametrize("key", ["DB_PATH", "UPGRADE_BACKUP_DIR", "BACKUP_KEEP"])
    def test_a_missing_key_is_named(self, tmp_path, key):
        text = "".join(
            line + "\n"
            for line in self.TEXT.splitlines()
            if not line.startswith(key)
        )
        with pytest.raises(BackupError, match=key):
            BackupSettings.from_text(text, tmp_path)

    def test_a_retention_that_is_not_a_number_is_an_error(self, tmp_path):
        with pytest.raises(BackupError, match="BACKUP_KEEP"):
            BackupSettings.from_text(
                self.TEXT.replace("BACKUP_KEEP=5", "BACKUP_KEEP=lots"), tmp_path
            )

    def test_env_example_documents_the_key(self):
        assert "\nBACKUP_KEEP=" in (ROOT / ".env.example").read_text()


class TestWeeklyScript:
    """The real script, under the bash launchd runs, in a sandbox: its own
    project copy, a stub `osascript` that records, and no real notification."""

    @pytest.fixture
    def sandbox(self, tmp_path):
        project = tmp_path / "project"
        (project / "tools").mkdir(parents=True)
        shutil.copy2(ROOT / "backup-db.sh", project)
        for name in ("backup_db.py", "upgrade_checks.py"):
            shutil.copy2(ROOT / "tools" / name, project / "tools")
        (project / ".env").write_text(
            f"DB_PATH=books.db\nUPGRADE_BACKUP_DIR={tmp_path / 'out'}\nBACKUP_KEEP=2\n"
        )
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "python3").symlink_to(sys.executable)
        stub = bin_dir / "osascript"
        stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{tmp_path}/notified"\n')
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        return project

    @staticmethod
    def run(project: Path) -> subprocess.CompletedProcess:
        env = {"PATH": f"{project.parent / 'bin'}:/usr/bin:/bin", "HOME": os.environ["HOME"]}
        return subprocess.run(
            ["/bin/bash", str(project / "backup-db.sh")],
            check=False,
            cwd="/",
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_success_is_logged_and_stays_quiet(self, sandbox):
        make_db(sandbox / "books.db")
        result = self.run(sandbox)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "books-weekly-" in (sandbox / "backup-db.log").read_text()
        assert not (sandbox.parent / "notified").exists()
        assert len(list((sandbox.parent / "out").glob("books-weekly-*.db"))) == 1

    def test_failure_notifies_and_exits_non_zero(self, sandbox):
        result = self.run(sandbox)  # no books.db in this project
        assert result.returncode != 0
        assert "epub-reader" in (sandbox.parent / "notified").read_text()
        assert "FAILED" in (sandbox / "backup-db.log").read_text()


class TestPlist:
    """Shipped as a template, like the agents beside it."""

    @staticmethod
    def load(name: str) -> dict:
        with (ROOT / name).open("rb") as handle:
            return plistlib.load(handle)

    def test_it_runs_the_script(self):
        plist = self.load("com.example.epub-reader.backup.plist")
        assert plist["Label"] == "com.example.epub-reader.backup"
        assert plist["ProgramArguments"] == ["/path/to/epub-reader/backup-db.sh"]
        assert os.access(ROOT / "backup-db.sh", os.X_OK)

    def test_it_runs_before_the_weekly_refresh_rewrites_metadata(self):
        when = self.load("com.example.epub-reader.backup.plist")[
            "StartCalendarInterval"
        ]
        refresh = self.load("com.example.epub-reader.refresh.plist")[
            "StartCalendarInterval"
        ]
        assert when["Weekday"] == refresh["Weekday"]
        assert (when["Hour"], when["Minute"]) < (refresh["Hour"], refresh["Minute"])
        assert when["Minute"] != 0  # not on the hour, where every other job lives
