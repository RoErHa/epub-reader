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
        Migration(
            2,
            "library_index",
            """
            -- Identity is the content hash, so moving or renaming a file
            -- updates a row instead of creating a duplicate. Rows are
            -- never deleted: a vanished book is marked missing_since so
            -- reading state and future annotations survive.
            CREATE TABLE IF NOT EXISTS book (
              id            INTEGER PRIMARY KEY,
              content_hash  TEXT    NOT NULL UNIQUE,
              path          TEXT    NOT NULL,   -- relative to LIBRARY_ROOT
              format        TEXT    NOT NULL,
              size_bytes    INTEGER NOT NULL,
              mtime         INTEGER NOT NULL,
              added_at      INTEGER NOT NULL,
              indexed_at    INTEGER,
              missing_since INTEGER,
              index_error   TEXT
            );
            CREATE INDEX IF NOT EXISTS book_path ON book(path);
            CREATE INDEX IF NOT EXISTS book_missing ON book(missing_since);

            CREATE TABLE IF NOT EXISTS series (
              id        INTEGER PRIMARY KEY,
              name      TEXT NOT NULL UNIQUE,
              sort_name TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS metadata (
              book_id         INTEGER PRIMARY KEY
                              REFERENCES book(id) ON DELETE CASCADE,
              title           TEXT,
              title_sort      TEXT,
              subtitle        TEXT,
              synopsis        TEXT,
              -- These two are NOT the same thing; conflating them is the
              -- commonest metadata error in e-book libraries.
              first_published INTEGER,
              edition_date    TEXT,
              publisher       TEXT,
              language        TEXT,
              isbn13          TEXT,
              page_count      INTEGER,
              spine_length    INTEGER,
              cover_path      TEXT,
              series_id       INTEGER REFERENCES series(id),
              series_index    REAL
            );
            CREATE INDEX IF NOT EXISTS metadata_title_sort ON metadata(title_sort);
            CREATE INDEX IF NOT EXISTS metadata_year ON metadata(first_published);
            CREATE INDEX IF NOT EXISTS metadata_series ON metadata(series_id);

            CREATE TABLE IF NOT EXISTS author (
              id        INTEGER PRIMARY KEY,
              name      TEXT NOT NULL UNIQUE,
              sort_name TEXT NOT NULL,
              bio         TEXT,
              bio_source  TEXT,
              photo_path  TEXT,
              born        TEXT,
              died        TEXT,
              nationality TEXT,
              website     TEXT,
              fetched_at  INTEGER
            );
            CREATE INDEX IF NOT EXISTS author_sort ON author(sort_name);

            CREATE TABLE IF NOT EXISTS book_author (
              book_id   INTEGER NOT NULL REFERENCES book(id) ON DELETE CASCADE,
              author_id INTEGER NOT NULL REFERENCES author(id),
              role      TEXT    NOT NULL DEFAULT 'author',
              position  INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (book_id, author_id, role)
            );
            CREATE INDEX IF NOT EXISTS book_author_author ON book_author(author_id);

            CREATE TABLE IF NOT EXISTS genre (
              id         INTEGER PRIMARY KEY,
              bisac_code TEXT UNIQUE,
              name       TEXT NOT NULL UNIQUE,
              sort_name  TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS book_genre (
              book_id  INTEGER NOT NULL REFERENCES book(id) ON DELETE CASCADE,
              genre_id INTEGER NOT NULL REFERENCES genre(id),
              position INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (book_id, genre_id)
            );
            CREATE INDEX IF NOT EXISTS book_genre_genre ON book_genre(genre_id);

            -- Enrichment may overwrite a field only when its origin is
            -- 'file' and it is unlocked. A user value is never
            -- overwritten automatically.
            CREATE TABLE IF NOT EXISTS field_provenance (
              book_id    INTEGER NOT NULL REFERENCES book(id) ON DELETE CASCADE,
              field      TEXT    NOT NULL,
              origin     TEXT    NOT NULL,
              source     TEXT,
              locked     INTEGER NOT NULL DEFAULT 0,
              updated_at INTEGER NOT NULL,
              PRIMARY KEY (book_id, field)
            );
            """,
        ),
        Migration(
            3,
            "search",
            """
            -- Same tokenizer dlna-gateway already uses, so "le carre"
            -- finds "le Carre" and nobody has to type the accent.
            --
            -- A plain FTS5 table, NOT contentless: the source columns
            -- span four tables, and a contentless table cannot be
            -- deleted from without replaying the original values —
            -- getting that wrong corrupts the index outright
            -- ("database disk image is malformed"). Storing a copy of a
            -- few short fields is cheap; correctness is not.
            CREATE VIRTUAL TABLE IF NOT EXISTS book_fts USING fts5(
              title, authors, series, genres, synopsis,
              tokenize='unicode61 remove_diacritics 1'
            );
            """,
        ),
        Migration(
            4,
            "shelves",
            """
            -- Shelves the reader makes. Smart shelves ("Reading now",
            -- "Finished") are NOT stored: they are derived from reading
            -- state, because they are a view of a fact rather than a
            -- list someone maintains.
            CREATE TABLE IF NOT EXISTS shelf (
              id         INTEGER PRIMARY KEY,
              name       TEXT NOT NULL,
              sort_name  TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            -- Case-insensitive: "Holiday" and "holiday" are one shelf.
            CREATE UNIQUE INDEX IF NOT EXISTS shelf_name
              ON shelf(lower(name));

            CREATE TABLE IF NOT EXISTS shelf_book (
              shelf_id INTEGER NOT NULL REFERENCES shelf(id) ON DELETE CASCADE,
              book_id  INTEGER NOT NULL REFERENCES book(id) ON DELETE CASCADE,
              added_at INTEGER NOT NULL,
              PRIMARY KEY (shelf_id, book_id)
            );
            CREATE INDEX IF NOT EXISTS shelf_book_book ON shelf_book(book_id);
            """,
        ),
        Migration(
            5,
            "author_works",
            """
            -- "Also published": an author's bibliography, INCLUDING works
            -- we do not own. book_id is non-null only when a work
            -- resolves to a book in the library, which is what lets the
            -- screen double as a gap-finder.
            --
            -- Everything fetched is stored; work_type is a DISPLAY
            -- filter (decision 6), so reclassifying never needs a
            -- re-fetch.
            CREATE TABLE IF NOT EXISTS author_work (
              id              INTEGER PRIMARY KEY,
              author_id       INTEGER NOT NULL
                              REFERENCES author(id) ON DELETE CASCADE,
              title           TEXT    NOT NULL,
              first_published INTEGER,
              series_name     TEXT,
              series_index    REAL,
              isbn13          TEXT,
              work_type       TEXT    NOT NULL DEFAULT 'unknown',
              book_id         INTEGER REFERENCES book(id),
              source          TEXT    NOT NULL,
              fetched_at      INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS author_work_author
              ON author_work(author_id);
            CREATE INDEX IF NOT EXISTS author_work_book
              ON author_work(book_id);
            """,
        ),
        Migration(
            6,
            "candidates",
            """
            -- Candidates in the middle band (0.60-0.90) wait here for a
            -- human. Everything fetched is kept with its score and the
            -- parts that produced it, so the review screen can show WHY
            -- rather than an unexplained number.
            CREATE TABLE IF NOT EXISTS metadata_candidate (
              id         INTEGER PRIMARY KEY,
              book_id    INTEGER NOT NULL REFERENCES book(id) ON DELETE CASCADE,
              provider   TEXT    NOT NULL,
              source_id  TEXT,
              payload    TEXT    NOT NULL,   -- the candidate, as JSON
              parts      TEXT,               -- score breakdown, as JSON
              score      REAL    NOT NULL,
              status     TEXT    NOT NULL DEFAULT 'pending',
              fetched_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS candidate_book
              ON metadata_candidate(book_id);
            CREATE INDEX IF NOT EXISTS candidate_status
              ON metadata_candidate(status);
            """,
        ),
        Migration(
            7,
            "file_title",
            """
            -- The title as the file gave it. A title corrected from its
            -- author's bibliography replaces what you see, and this is
            -- what it replaced: the change stays reversible, and the
            -- name you know from your own folders is not lost.
            ALTER TABLE metadata ADD COLUMN file_title TEXT;
            """,
        ),
        Migration(
            8,
            "annotations",
            """
            -- Highlights and notes. Server-side and shared across every
            -- device, like reading position: a passage marked on the
            -- iPad is marked on the Mac.
            --
            -- `text` deliberately duplicates what the CFI points at. A
            -- CFI can stop resolving — a book replaced by a better scan
            -- — and a highlight whose words survive is recoverable,
            -- while a dangling pointer is not.
            --
            -- `fraction` is how far into the book it sits, because CFIs
            -- do not sort as strings and the highlights panel has to
            -- list them in reading order.
            CREATE TABLE IF NOT EXISTS annotation (
              id         INTEGER PRIMARY KEY,
              book_id    INTEGER NOT NULL REFERENCES book(id) ON DELETE CASCADE,
              cfi        TEXT    NOT NULL,
              text       TEXT    NOT NULL,
              note       TEXT,
              color      TEXT,
              fraction   REAL    NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS annotation_book
              ON annotation(book_id, fraction);
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
