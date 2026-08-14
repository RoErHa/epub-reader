"""Indexing the library into SQLite.

Shaped by what the real library actually is: 44% of books live in
subfolders, some files are corrupt, and re-scanning 1,300 books must not
mean re-hashing 34 GB every time.
"""
from __future__ import annotations

import pytest

from epub_db import Database
from epub_index import LibraryIndexer
from epub_server import BookLibrary
from tests.epub_fixtures import EpubBuilder


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "books.db")
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    return root


@pytest.fixture
def indexer(db, library, tmp_path):
    return LibraryIndexer(db, BookLibrary(library), tmp_path / "covers")


def add(root, name, **kwargs):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return EpubBuilder(**kwargs).build(path)


class TestFirstIndex:
    def test_books_are_stored(self, indexer, library, db):
        add(library, "a.epub", title="Dune", author="Frank Herbert")
        report = indexer.run()
        assert report.added == 1
        row = db.connection.execute(
            "SELECT * FROM book JOIN metadata USING (book_id)"
        ).fetchone() if False else db.connection.execute(
            "SELECT b.content_hash, m.title FROM book b "
            "JOIN metadata m ON m.book_id = b.id"
        ).fetchone()
        assert row["title"] == "Dune"
        assert len(row["content_hash"]) == 64

    def test_subfolders_are_indexed(self, indexer, library):
        # 44% of the real library lives in subfolders.
        add(library, "a.epub", title="One")
        add(library, "Series Folder/b.epub", title="Two")
        add(library, "Deep/Deeper/c.epub", title="Three")
        assert indexer.run().added == 3

    def test_identical_copies_are_one_book(self, indexer, library, db):
        # Identity is the content hash, so the same book filed twice is
        # one row — which a real library needs, being full of duplicates.
        add(library, "a.epub", title="Dune")
        add(library, "backup/a.epub", title="Dune")
        indexer.run()
        assert db.connection.execute(
            "SELECT COUNT(*) c FROM book").fetchone()["c"] == 1

    def test_the_relative_path_is_stored(self, indexer, library, db):
        add(library, "Series Folder/b.epub")
        indexer.run()
        path = db.connection.execute("SELECT path FROM book").fetchone()["path"]
        assert path == "Series Folder/b.epub"

    def test_non_epub_files_are_skipped(self, indexer, library):
        add(library, "a.epub")
        (library / "notes.txt").write_text("x")
        (library / "manual.pdf").write_bytes(b"%PDF")
        assert indexer.run().added == 1

    def test_authors_are_stored_with_sort_names(self, indexer, library, db):
        add(library, "a.epub", author="Ludlum, Robert")
        indexer.run()
        row = db.connection.execute("SELECT * FROM author").fetchone()
        assert row["name"] == "Robert Ludlum"
        assert row["sort_name"] == "ludlum, robert"

    def test_an_author_is_shared_between_books(self, indexer, library, db):
        add(library, "a.epub", author="Robin Hobb", title="A")
        add(library, "b.epub", author="Hobb, Robin", title="B")
        indexer.run()
        assert db.connection.execute("SELECT COUNT(*) c FROM author").fetchone()["c"] == 1
        assert db.connection.execute(
            "SELECT COUNT(*) c FROM book_author").fetchone()["c"] == 2

    def test_series_is_stored(self, indexer, library, db):
        add(library, "a.epub", series="Hainish Cycle", series_index=4)
        indexer.run()
        row = db.connection.execute(
            "SELECT s.name, m.series_index FROM metadata m "
            "JOIN series s ON s.id = m.series_id"
        ).fetchone()
        assert row["name"] == "Hainish Cycle"
        assert row["series_index"] == pytest.approx(4.0)

    def test_subjects_become_genres(self, indexer, library, db):
        add(library, "a.epub", subjects=["Science Fiction", "Adventure"])
        indexer.run()
        names = [r["name"] for r in db.connection.execute(
            "SELECT g.name FROM genre g JOIN book_genre bg ON bg.genre_id = g.id "
            "ORDER BY bg.position")]
        assert names == ["Science Fiction", "Adventure"]


class TestIncremental:
    def test_a_second_run_changes_nothing(self, indexer, library):
        add(library, "a.epub")
        indexer.run()
        report = indexer.run()
        assert (report.added, report.updated, report.unchanged) == (0, 0, 1)

    def test_an_unchanged_book_is_not_rehashed(self, indexer, library):
        add(library, "a.epub")
        indexer.run()
        indexer.hashed = 0
        indexer.run()
        assert indexer.hashed == 0, "unchanged books must not be re-hashed"

    def test_a_new_book_is_added(self, indexer, library):
        add(library, "a.epub")
        indexer.run()
        add(library, "b.epub", title="Second")
        report = indexer.run()
        assert (report.added, report.unchanged) == (1, 1)

    def test_an_edited_book_is_updated(self, indexer, library, db):
        add(library, "a.epub", title="Before")
        indexer.run()
        add(library, "a.epub", title="After", paragraphs_per_chapter=9)
        import os, time
        os.utime(library / "a.epub", (time.time() + 5, time.time() + 5))
        report = indexer.run()
        assert report.updated == 1
        assert db.connection.execute(
            "SELECT title FROM metadata").fetchone()["title"] == "After"

    def test_a_moved_book_keeps_its_row(self, indexer, library, db):
        # Identity is the content hash, so moving a file updates the path
        # rather than creating a duplicate.
        add(library, "a.epub", title="Dune")
        indexer.run()
        (library / "sub").mkdir()
        (library / "a.epub").rename(library / "sub" / "a.epub")
        report = indexer.run()
        assert db.connection.execute("SELECT COUNT(*) c FROM book").fetchone()["c"] == 1
        assert db.connection.execute(
            "SELECT path FROM book").fetchone()["path"] == "sub/a.epub"
        assert report.moved == 1

    def test_duplicate_copies_do_not_fight_over_the_path(self, indexer, library):
        # Two identical files means one row, but each run must not keep
        # reassigning its path — the real library churned on this.
        add(library, "a.epub", title="Dune")
        add(library, "backup/a.epub", title="Dune")
        indexer.run()
        report = indexer.run()
        assert report.moved == 0
        assert not report.has_changes

    def test_a_deleted_book_is_marked_missing_not_removed(self, indexer, library, db):
        add(library, "a.epub")
        indexer.run()
        (library / "a.epub").unlink()
        report = indexer.run()
        assert report.missing == 1
        row = db.connection.execute("SELECT missing_since FROM book").fetchone()
        assert row["missing_since"] is not None

    def test_a_returning_book_is_unmarked(self, indexer, library, db):
        add(library, "a.epub")
        indexer.run()
        (library / "a.epub").unlink()
        indexer.run()
        add(library, "a.epub")
        indexer.run()
        assert db.connection.execute(
            "SELECT missing_since FROM book").fetchone()["missing_since"] is None


class TestBrokenBooks:
    def test_a_corrupt_book_does_not_stop_the_run(self, indexer, library, db):
        (library / "broken.epub").write_bytes(b"not a zip")
        add(library, "good.epub", title="Fine")
        report = indexer.run()
        assert report.failed == 1
        assert report.added == 1
        assert db.connection.execute(
            "SELECT COUNT(*) c FROM metadata").fetchone()["c"] == 1

    def test_the_failure_is_recorded_against_the_book(self, indexer, library, db):
        (library / "broken.epub").write_bytes(b"not a zip")
        indexer.run()
        row = db.connection.execute(
            "SELECT path, index_error FROM book WHERE index_error IS NOT NULL"
        ).fetchone()
        assert row["path"] == "broken.epub"
        assert "not a valid" in row["index_error"]

    def test_a_book_that_starts_failing_keeps_its_old_metadata(
        self, indexer, library, db
    ):
        add(library, "a.epub", title="Good Title")
        indexer.run()
        (library / "a.epub").write_bytes(b"corrupted now")
        indexer.run()
        assert db.connection.execute(
            "SELECT title FROM metadata").fetchone()["title"] == "Good Title"


class TestCovers:
    def test_a_cover_is_written_to_the_cache(self, indexer, library, tmp_path, db):
        add(library, "a.epub", cover=True)
        indexer.run()
        stored = db.connection.execute(
            "SELECT cover_path FROM metadata").fetchone()["cover_path"]
        assert stored
        assert (tmp_path / "covers" / stored).is_file()

    def test_a_book_without_a_cover_stores_none(self, indexer, library, db):
        add(library, "a.epub")
        indexer.run()
        assert db.connection.execute(
            "SELECT cover_path FROM metadata").fetchone()["cover_path"] is None


class TestReport:
    def test_the_summary_reads_plainly(self, indexer, library):
        add(library, "a.epub")
        assert "1 book" in indexer.run().summary()

    def test_an_unchanged_run_says_so(self, indexer, library):
        add(library, "a.epub")
        indexer.run()
        assert "no changes" in indexer.run().summary().lower()
