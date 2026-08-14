"""SQLite access.

All database work happens server side; the frontend only ever sees JSON.
Migrations are forward-only and numbered, and run on every startup, so
each one must be safe to re-apply.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


class MigrationRunner:
    """Applies numbered migrations in order, once each."""

    MIGRATIONS: list[Migration] = [
        Migration(
            1,
            "reading_state",
            """
            -- Server-side and shared across all devices (decision 7).
            -- Keyed by content hash, not filename, so renaming a book
            -- does not lose your place. M2 introduces book.id and can
            -- add the foreign key.
            CREATE TABLE IF NOT EXISTS reading_state (
              content_hash TEXT PRIMARY KEY,
              cfi          TEXT,
              fraction     REAL NOT NULL DEFAULT 0,
              status       TEXT NOT NULL DEFAULT 'unread',
              opened_at    INTEGER,
              finished_at  INTEGER
            );
            CREATE INDEX IF NOT EXISTS reading_state_status
              ON reading_state(status);
            """,
        ),
    ]

    LATEST = MIGRATIONS[-1].version

    def __init__(self, migrations: list[Migration] | None = None) -> None:
        self._migrations = sorted(
            migrations if migrations is not None else self.MIGRATIONS,
            key=lambda m: m.version,
        )

    def current_version(self, connection: sqlite3.Connection) -> int:
        return connection.execute("PRAGMA user_version").fetchone()[0]

    def run(self, connection: sqlite3.Connection) -> int:
        version = self.current_version(connection)
        for migration in self._migrations:
            if migration.version <= version:
                continue
            with connection:
                connection.executescript(migration.sql)
                # PRAGMA cannot be parameterised.
                connection.execute(f"PRAGMA user_version = {migration.version}")
            version = migration.version
        return version


class Database:
    """Owns the connection and its pragmas."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._connection: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self._path, check_same_thread=False, isolation_level=None
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = NORMAL")
            self._connection = connection
        return self._connection

    def migrate(self) -> int:
        return MigrationRunner().run(self.connection)

    def schema_version(self) -> int:
        return MigrationRunner().current_version(self.connection)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
