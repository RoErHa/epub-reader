"""Schema and migrations.

Migrations are forward-only and numbered. They must be safe to run on
every startup, because that is exactly when they run.
"""
from __future__ import annotations

import sqlite3

import pytest

from epub_db import Database, DatabaseError, Migration, MigrationRunner


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "books.db")
    database.migrate()
    yield database
    database.close()


class TestConnection:
    def test_database_file_is_created(self, tmp_path):
        path = tmp_path / "nested" / "books.db"
        database = Database(path)
        database.migrate()
        assert path.is_file()
        database.close()

    def test_foreign_keys_are_enforced(self, db):
        assert db.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    def test_write_ahead_logging_is_on(self, db):
        mode = db.connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"

    def test_rows_come_back_addressable_by_name(self, db):
        row = db.connection.execute("SELECT 1 AS answer").fetchone()
        assert row["answer"] == 1


class TestMigrations:
    def test_reading_state_table_exists(self, db):
        tables = {
            r["name"]
            for r in db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "reading_state" in tables

    def test_schema_version_is_recorded(self, db):
        assert db.schema_version() == MigrationRunner.LATEST

    def test_migrating_twice_is_harmless(self, tmp_path):
        database = Database(tmp_path / "books.db")
        database.migrate()
        first = database.schema_version()
        database.migrate()
        assert database.schema_version() == first
        database.close()

    def test_migrations_are_numbered_without_gaps(self):
        versions = [m.version for m in MigrationRunner.MIGRATIONS]
        assert versions == list(range(1, len(versions) + 1))

    def test_migrations_run_in_order(self, tmp_path):
        # The second migration alters what the first creates, so it can
        # only succeed if they are applied in order.
        runner = MigrationRunner(
            [
                Migration(2, "adds a column", "ALTER TABLE a ADD COLUMN y TEXT"),
                Migration(1, "creates the table", "CREATE TABLE a (x INTEGER)"),
            ]
        )
        database = Database(tmp_path / "books.db")
        assert runner.run(database.connection) == 2
        columns = {
            r["name"] for r in database.connection.execute("PRAGMA table_info(a)")
        }
        assert columns == {"x", "y"}
        database.close()

    def test_already_applied_versions_are_skipped(self, tmp_path):
        database = Database(tmp_path / "books.db")
        MigrationRunner([Migration(1, "first", "CREATE TABLE a (x INTEGER)")]).run(
            database.connection
        )
        # Re-running version 1 unchanged would fail with "table a already
        # exists" if the version check were not honoured.
        again = MigrationRunner(
            [
                Migration(1, "first", "CREATE TABLE a (x INTEGER)"),
                Migration(2, "second", "CREATE TABLE b (x INTEGER)"),
            ]
        )
        assert again.run(database.connection) == 2
        database.close()

    def test_an_already_migrated_database_skips_applied_steps(self, tmp_path):
        database = Database(tmp_path / "books.db")
        database.migrate()
        runner = MigrationRunner(MigrationRunner.MIGRATIONS)
        # Re-running must not raise "table already exists".
        runner.run(database.connection)
        database.close()

    def test_a_failing_migration_leaves_the_version_untouched(self, tmp_path):
        database = Database(tmp_path / "books.db")
        runner = MigrationRunner([Migration(1, "broken", "NOT VALID SQL")])
        with pytest.raises(sqlite3.Error):
            runner.run(database.connection)
        assert runner.current_version(database.connection) == 0
        database.close()


class TestTwoWriters:
    """The cover sweep runs for hours beside the live service, so a
    write that arrives mid-transaction must wait rather than fail.

    Python's sqlite3 gives five seconds by default and always has; this
    pins it, because a background pass now depends on it.
    """

    def test_a_busy_database_is_waited_for(self, tmp_path):
        from epub_db import Database

        db = Database(tmp_path / "books.db")
        waited = db.connection.execute("PRAGMA busy_timeout").fetchone()[0]
        assert waited >= 5000


class TestSqliteVersion:
    """The schema and the queries assume a current SQLite.

    `GROUP_CONCAT(x, sep ORDER BY y)` — which is what keeps a book's
    authors in credit order — arrived in **3.44.0** (November 2023). On
    anything older the parser rejects it, and the failure would arrive
    as a query error in the middle of browsing rather than as something
    anyone could act on.

    So it is checked once, on the way in, and says what to do.
    """

    def test_a_current_version_is_accepted(self):
        assert Database.supported("3.44.0")
        assert Database.supported("3.53.4")
        assert Database.supported("4.0.1")

    def test_an_old_version_is_refused(self):
        assert not Database.supported("3.43.2")
        assert not Database.supported("3.9.0")
        assert not Database.supported("2.8.17")

    def test_opening_on_an_old_sqlite_says_what_is_wrong(self, tmp_path,
                                                          monkeypatch):
        monkeypatch.setattr("epub_db.sqlite3.sqlite_version", "3.43.2")
        with pytest.raises(DatabaseError) as exc:
            Database(tmp_path / "old.db").connection
        message = str(exc.value)
        assert "3.43.2" in message and "3.44" in message

    def test_the_running_interpreter_is_current_enough(self):
        """Not a unit test — a check on the machine this is built on."""
        assert Database.supported(sqlite3.sqlite_version), (
            f"SQLite {sqlite3.sqlite_version} is too old for this project")
