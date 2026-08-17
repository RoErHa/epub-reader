"""Indexing the library into SQLite.

Shaped by what the real library actually is: 44% of books live in
subfolders, some files are corrupt, and re-scanning 1,300 books must not
mean re-hashing 34 GB every time.
"""
from __future__ import annotations

import os

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


class TestRescanningDoesNotUndoCorrections:
    """The file is only the *first* word on a book's metadata, not the
    last. Author de-duplication and the title-as-author repair correct
    what the file says; a rescan, which happens on every restart, must
    not quietly put the wrong answer back.

    The rule is the same one enrichment follows: a field may be taken
    from the file only while the file still owns it.

    A rescan short-circuits on an unchanged file, so this only bites
    when the bytes or the timestamp move — a file touched by another
    tool, a better scan dropped in over an old one.
    """

    @staticmethod
    def touch(path):
        """Make the next scan re-read this book, as replacing the file
        with a better copy would."""
        stamp = path.stat().st_mtime + 60
        os.utime(path, (stamp, stamp))

    @staticmethod
    def owned_by(db, book_id, field, origin, locked=0):
        db.connection.execute(
            "INSERT INTO field_provenance (book_id, field, origin, locked,"
            " updated_at) VALUES (?, ?, ?, ?, 1)"
            " ON CONFLICT(book_id, field) DO UPDATE SET origin = excluded.origin,"
            " locked = excluded.locked",
            (book_id, field, origin, locked),
        )

    @staticmethod
    def author_of(db, book_id):
        row = db.connection.execute(
            "SELECT a.name FROM book_author ba JOIN author a"
            " ON a.id = ba.author_id WHERE ba.book_id = ?", (book_id,)
        ).fetchone()
        return row["name"] if row else None

    def book_id(self, db):
        return db.connection.execute("SELECT id FROM book").fetchone()["id"]

    def test_a_repaired_author_survives_a_rescan(self, indexer, library, db):
        # The file credits the book to its own title; the repair fixed it.
        add(library, "a.epub", title="Annihilation", author="Annihilation")
        indexer.run()
        book = self.book_id(db)
        db.connection.execute("DELETE FROM book_author WHERE book_id = ?", (book,))
        db.connection.execute(
            "INSERT INTO author (name, sort_name) VALUES ('Jeff VanderMeer',"
            " 'vandermeer, jeff')")
        db.connection.execute(
            "INSERT INTO book_author (book_id, author_id, role, position)"
            " SELECT ?, id, 'author', 0 FROM author WHERE name = 'Jeff VanderMeer'",
            (book,))
        self.owned_by(db, book, "authors", "provider")

        self.touch(library / "a.epub")
        indexer.run()
        assert self.author_of(db, book) == "Jeff VanderMeer"

    def test_a_merged_author_is_not_split_again(self, indexer, library, db):
        add(library, "a.epub", title="Kesrith", author="C J Cherryh")
        indexer.run()
        book = self.book_id(db)
        db.connection.execute("UPDATE author SET name = 'C. J. Cherryh',"
                              " sort_name = 'cherryh, c. j.'")
        self.owned_by(db, book, "authors", "provider")

        self.touch(library / "a.epub")
        indexer.run()
        assert self.author_of(db, book) == "C. J. Cherryh"

    def test_a_title_you_set_by_hand_survives_a_rescan(self, indexer, library,
                                                       db):
        add(library, "a.epub", title="Herbert, Frank - Dune 01 - Dune",
            author="Frank Herbert")
        indexer.run()
        book = self.book_id(db)
        db.connection.execute("UPDATE metadata SET title = 'Dune' WHERE book_id = ?",
                              (book,))
        self.owned_by(db, book, "title", "user")

        self.touch(library / "a.epub")
        indexer.run()
        assert db.connection.execute(
            "SELECT title FROM metadata WHERE book_id = ?", (book,)
        ).fetchone()["title"] == "Dune"

    def test_a_locked_field_survives_a_rescan(self, indexer, library, db):
        add(library, "a.epub", title="Dune", author="Frank Herbert",
            language="en")
        indexer.run()
        book = self.book_id(db)
        db.connection.execute(
            "UPDATE metadata SET language = 'nl' WHERE book_id = ?", (book,))
        self.owned_by(db, book, "language", "file", locked=1)

        self.touch(library / "a.epub")
        indexer.run()
        assert db.connection.execute(
            "SELECT language FROM metadata WHERE book_id = ?", (book,)
        ).fetchone()["language"] == "nl"

    def test_genres_you_set_are_kept_too(self, indexer, library, db):
        # Nothing sets genres by hand yet, but the guard covered title
        # and authors and not this — an inconsistency waiting for the
        # first person to correct a genre.
        add(library, "a.epub", title="Dune", author="Frank Herbert")
        indexer.run()
        book = self.book_id(db)
        db.connection.execute("DELETE FROM book_genre WHERE book_id = ?", (book,))
        db.connection.execute(
            "INSERT INTO genre (name, sort_name) VALUES ('Space Opera',"
            " 'space opera')")
        db.connection.execute(
            "INSERT INTO book_genre (book_id, genre_id, position)"
            " SELECT ?, id, 0 FROM genre WHERE name = 'Space Opera'", (book,))
        self.owned_by(db, book, "genres", "user")

        self.touch(library / "a.epub")
        indexer.run()
        kept = [r["name"] for r in db.connection.execute(
            "SELECT g.name FROM book_genre bg JOIN genre g ON g.id = bg.genre_id"
            " WHERE bg.book_id = ?", (book,))]
        assert kept == ["Space Opera"]

    def test_a_field_the_file_still_owns_is_refreshed(self, indexer, library, db):
        # The point of a rescan: an edited EPUB updates the library.
        path = add(library, "a.epub", title="Dune", author="Frank Herbert")
        indexer.run()
        book = self.book_id(db)
        path.unlink()
        add(library, "a.epub", title="Dune Messiah", author="Frank Herbert")

        indexer.run()
        assert db.connection.execute(
            "SELECT title FROM metadata WHERE book_id = ?", (book,)
        ).fetchone()["title"] == "Dune Messiah"

    def test_a_new_book_is_still_taken_from_its_file(self, indexer, library, db):
        add(library, "a.epub", title="Dune", author="Frank Herbert")
        indexer.run()
        assert self.author_of(db, self.book_id(db)) == "Frank Herbert"


class TestOtherFormats:
    """MOBI, AZW3 and PRC. The reader gets these free — foliate sniffs
    the container — but the library has to read their metadata out of a
    PalmDB record rather than an OPF.

    The real library holds 89 of them beside 1,353 EPUBs.
    """

    def test_a_mobi_is_indexed(self, indexer, library, db):
        from tests.mobi_fixtures import MobiBuilder
        MobiBuilder(title="Dune", authors=["Frank Herbert"]).build(
            library / "dune.mobi")
        assert indexer.run().added == 1
        row = db.connection.execute(
            "SELECT m.title, b.format FROM metadata m"
            " JOIN book b ON b.id = m.book_id").fetchone()
        assert row["title"] == "Dune"
        assert row["format"] == "mobi"

    def test_its_author_is_read_too(self, indexer, library, db):
        from tests.mobi_fixtures import MobiBuilder
        MobiBuilder(title="Dune", authors=["Herbert, Frank"]).build(
            library / "dune.mobi")
        indexer.run()
        assert db.connection.execute(
            "SELECT name FROM author").fetchone()["name"] == "Frank Herbert"

    def test_an_azw3_is_indexed(self, indexer, library, db):
        from tests.mobi_fixtures import MobiBuilder
        MobiBuilder(title="Modern", kf8=True).build(library / "modern.azw3")
        assert indexer.run().added == 1
        assert db.connection.execute(
            "SELECT format FROM book").fetchone()["format"] == "azw3"

    def test_a_prc_is_indexed(self, indexer, library, db):
        from tests.mobi_fixtures import MobiBuilder
        MobiBuilder(title="Older").build(library / "older.prc")
        assert indexer.run().added == 1

    def test_epub_and_mobi_live_side_by_side(self, indexer, library, db):
        from tests.mobi_fixtures import MobiBuilder
        add(library, "a.epub", title="An EPUB", author="Someone")
        MobiBuilder(title="A MOBI", authors=["Someone"]).build(library / "b.mobi")
        assert indexer.run().added == 2
        titles = {r["title"] for r in
                  db.connection.execute("SELECT title FROM metadata")}
        assert titles == {"An EPUB", "A MOBI"}

    def test_a_broken_mobi_does_not_stop_the_scan(self, indexer, library, db):
        # One bad file among 1,300 must never end a run.
        (library / "broken.mobi").write_bytes(b"not a palm database at all")
        add(library, "good.epub", title="Fine", author="Someone")
        report = indexer.run()
        assert report.failed == 1
        assert db.connection.execute(
            "SELECT COUNT(*) n FROM metadata").fetchone()["n"] == 1

    def test_a_mobi_cover_is_extracted(self, indexer, library, db, tmp_path):
        from tests.mobi_fixtures import MobiBuilder, jpeg
        MobiBuilder(title="Covered", cover=jpeg()).build(library / "covered.mobi")
        indexer.run()
        assert db.connection.execute(
            "SELECT cover_path FROM metadata").fetchone()["cover_path"]


class TestBooksThatWereNeverZipped:
    """265 books in the real library are directories: iTunes exports,
    and anything somebody unzipped and never put back. They were
    invisible — the scan listed files only — which is 265 books you own
    and cannot see."""

    def test_a_directory_is_indexed_as_one_book(self, indexer, library, db):
        EpubBuilder(title="Drive", author="James Sallis").build_unpacked(
            library / "Drive.epub")
        assert indexer.run().added == 1
        row = db.connection.execute(
            "SELECT m.title, b.format FROM metadata m"
            " JOIN book b ON b.id = m.book_id").fetchone()
        assert row["title"] == "Drive"
        assert row["format"] == "epub"

    def test_the_files_inside_it_are_not_books(self, indexer, library, db):
        # A book is the directory, not the hundred documents in it.
        EpubBuilder(title="Drive", chapters=4).build_unpacked(
            library / "Drive.epub")
        assert indexer.run().added == 1

    def test_it_sits_beside_zipped_books(self, indexer, library, db):
        add(library, "packed.epub", title="Packed", author="Someone")
        EpubBuilder(title="Loose", author="Someone").build_unpacked(
            library / "Loose.epub")
        assert indexer.run().added == 2

    def test_nothing_changes_on_a_second_scan(self, indexer, library, db):
        EpubBuilder(title="Drive").build_unpacked(library / "Drive.epub")
        indexer.run()
        report = indexer.run()
        assert report.added == 0
        assert report.updated == 0
        assert report.unchanged == 1

    def test_editing_a_file_inside_it_is_noticed(self, indexer, library, db):
        EpubBuilder(title="Drive").build_unpacked(library / "Drive.epub")
        indexer.run()
        page = next((library / "Drive.epub").rglob("*.xhtml"))
        page.write_text(page.read_text() + "<!-- changed -->")

        report = indexer.run()
        assert report.updated == 1

    def test_the_same_book_zipped_and_loose_is_one_book(self, indexer, library,
                                                        db):
        # Same contents, two containers. Identity is the content hash,
        # and these hash differently — a zip is not its own contents —
        # so this is two books, and the book-level dedupe is what will
        # have an opinion about that later.
        # Not the same name in two cases: macOS would call those one
        # path and the second would fail to be created at all.
        builder = EpubBuilder(title="Twice", author="Someone")
        builder.build(library / "twice-zipped.epub")
        builder.build_unpacked(library / "twice-loose.epub")
        assert indexer.run().added == 2

    def test_a_directory_that_is_not_a_book_is_skipped(self, indexer, library,
                                                       db):
        (library / "Notes.epub").mkdir()
        (library / "Notes.epub" / "readme.txt").write_text("not a book")
        report = indexer.run()
        assert report.added == 0

    def test_a_broken_one_does_not_stop_the_scan(self, indexer, library, db):
        broken = library / "Broken.epub"
        (broken / "META-INF").mkdir(parents=True)
        (broken / "META-INF" / "container.xml").write_text("<container/>")
        add(library, "good.epub", title="Fine")
        report = indexer.run()
        assert report.failed == 1
        assert db.connection.execute(
            "SELECT COUNT(*) n FROM metadata").fetchone()["n"] == 1


class TestComics:
    """309 comic archives. A CBZ is a zip of images; foliate draws it,
    and the library reads the filename, the page count and the first
    page."""

    def test_a_comic_is_indexed(self, indexer, library, db):
        from tests.cbz_fixtures import CbzBuilder
        CbzBuilder(pages=5).build(library / "Zootopia 001 (2025) (digital).cbz")
        assert indexer.run().added == 1
        row = db.connection.execute(
            "SELECT m.title, m.page_count, b.format FROM metadata m"
            " JOIN book b ON b.id = m.book_id").fetchone()
        assert row["title"] == "Zootopia 001"
        assert row["page_count"] == 5
        assert row["format"] == "cbz"

    def test_its_first_page_becomes_the_cover(self, indexer, library, db):
        from tests.cbz_fixtures import CbzBuilder
        CbzBuilder(pages=3).build(library / "Covered 001.cbz")
        indexer.run()
        assert db.connection.execute(
            "SELECT cover_path FROM metadata").fetchone()["cover_path"]

    def test_the_issue_number_gives_it_a_series(self, indexer, library, db):
        from tests.cbz_fixtures import CbzBuilder
        CbzBuilder().build(library / "Shadowman 004 (2025).cbz")
        indexer.run()
        row = db.connection.execute(
            "SELECT s.name, m.series_index FROM metadata m"
            " JOIN series s ON s.id = m.series_id").fetchone()
        assert row["name"] == "Shadowman"
        assert row["series_index"] == 4

    def test_a_cbr_is_still_left_alone(self, indexer, library, db):
        # RAR, which nothing in the stack reads.
        (library / "comic.cbr").write_bytes(b"Rar!\x1a\x07\x00")
        assert indexer.run().added == 0

    def test_a_broken_comic_does_not_stop_the_scan(self, indexer, library, db):
        (library / "broken.cbz").write_bytes(b"not a zip")
        add(library, "good.epub", title="Fine")
        report = indexer.run()
        assert report.failed == 1


class TestBooksThatCannotBeOpened:
    """Apple Books purchases: every document encrypted, the key in an
    Apple ID. 50 of them here. They are not failures — nothing is wrong
    with the file or with us — so they are skipped and said so."""

    def locked(self, library, name="drm.epub"):
        return EpubBuilder(title="Secret Europe",
                           encrypted=["OEBPS/chapter1.xhtml"]).build(
            library / name)

    def test_it_is_not_indexed(self, indexer, library, db):
        self.locked(library)
        add(library, "readable.epub", title="Plain", author="Someone")
        report = indexer.run()
        assert report.added == 1
        titles = {r["title"] for r in
                  db.connection.execute("SELECT title FROM metadata")}
        assert titles == {"Plain"}

    def test_it_is_not_counted_as_a_failure(self, indexer, library, db):
        # Nothing is broken. Calling it a failure would send somebody
        # looking for a fault that is not there.
        self.locked(library)
        report = indexer.run()
        assert report.failed == 0
        assert report.locked == 1

    def test_a_book_with_obfuscated_fonts_is_kept(self, indexer, library, db):
        EpubBuilder(title="The Tombs of Atuan", author="Ursula K. Le Guin",
                    encrypted=["OEBPS/fonts/Charter.otf"]).build(
            library / "atuan.epub")
        assert indexer.run().added == 1

    def test_one_already_indexed_is_removed(self, indexer, library, db):
        # It was listed before this was understood; excluding it means
        # taking it off the shelves, not leaving it there unreadable.
        add(library, "drm.epub", title="Secret Europe", author="Lonely Planet")
        indexer.run()
        assert db.connection.execute(
            "SELECT COUNT(*) n FROM book").fetchone()["n"] == 1

        (library / "drm.epub").unlink()
        self.locked(library)
        indexer.run()
        assert db.connection.execute(
            "SELECT COUNT(*) n FROM book").fetchone()["n"] == 0

    def test_one_indexed_before_is_dropped_on_the_next_scan(self, indexer,
                                                            library, db):
        # A scan short-circuits on an unchanged file, so a book listed
        # before this rule existed would sit there for ever. Checking
        # for a lock costs 0.1s across 1,600 books, so it is done every
        # time rather than in a command somebody has to remember.
        self.locked(library)
        db.connection.execute(
            "INSERT INTO book (content_hash, path, format, size_bytes, mtime,"
            " added_at) VALUES ('abc', 'drm.epub', 'epub', 1, 1, 1)")
        report = indexer.run()
        assert db.connection.execute(
            "SELECT COUNT(*) n FROM book WHERE path = 'drm.epub'"
        ).fetchone()["n"] == 0
        assert report.locked >= 1

    def test_a_font_obfuscated_book_survives_every_scan(self, indexer, library,
                                                        db):
        EpubBuilder(title="Tehanu", author="Ursula K. Le Guin",
                    encrypted=["OEBPS/fonts/Charter.otf"]).build(
            library / "tehanu.epub")
        indexer.run()
        indexer.run()
        assert db.connection.execute(
            "SELECT COUNT(*) n FROM book").fetchone()["n"] == 1

    def test_dropping_one_that_hides_a_duplicate_does_not_fail(self, indexer,
                                                               library, db):
        # A locked book can be the copy a duplicate points at. Deleting
        # it then breaks the foreign key and takes the whole scan down —
        # which is exactly what happened on the real library.
        # As it happened: both listed while readable, one hidden behind
        # the other, and only later replaced by a locked copy.
        add(library, "keeper.epub", title="Secret Europe", author="Lonely Planet")
        add(library, "spare.epub", title="Secret Europe", author="Lonely Planet",
            chapters=2)
        indexer.run()
        keeper = db.connection.execute(
            "SELECT id FROM book WHERE path = 'keeper.epub'").fetchone()["id"]
        spare = db.connection.execute(
            "SELECT id FROM book WHERE path = 'spare.epub'").fetchone()["id"]
        db.connection.execute("UPDATE book SET duplicate_of = ? WHERE id = ?",
                              (keeper, spare))

        (library / "keeper.epub").unlink()
        self.locked(library, "keeper.epub")

        report = indexer.run()          # must not raise
        assert db.connection.execute(
            "SELECT COUNT(*) n FROM book WHERE path = 'keeper.epub'"
        ).fetchone()["n"] == 0
        # ...and the copy it was hiding comes back into view.
        assert db.connection.execute(
            "SELECT duplicate_of FROM book WHERE id = ?", (spare,)
        ).fetchone()["duplicate_of"] is None

    def test_dropping_one_listed_in_a_bibliography_does_not_fail(self, indexer,
                                                                 library, db):
        # "Also published" links each work to the copy you own, and that
        # link is a foreign key without a cascade. Two references to a
        # book row have no cascade — this is the second, and it took the
        # scan down on the real library after the first was fixed.
        add(library, "keeper.epub", title="Secret Europe", author="Lonely Planet")
        indexer.run()
        book = db.connection.execute(
            "SELECT id FROM book WHERE path = 'keeper.epub'").fetchone()["id"]
        author = db.connection.execute(
            "SELECT id FROM author LIMIT 1").fetchone()["id"]
        db.connection.execute(
            "INSERT INTO author_work (author_id, title, work_type, source,"
            " fetched_at, book_id) VALUES (?, 'Secret Europe', 'novel',"
            " 'openlibrary', 1, ?)", (author, book))

        (library / "keeper.epub").unlink()
        self.locked(library, "keeper.epub")
        indexer.run()                      # must not raise

        assert db.connection.execute(
            "SELECT COUNT(*) n FROM book WHERE path = 'keeper.epub'"
        ).fetchone()["n"] == 0
        # The work stays on the author's shelf; we simply no longer own it.
        row = db.connection.execute(
            "SELECT book_id FROM author_work").fetchone()
        assert row["book_id"] is None

    def test_a_locked_book_is_never_hashed(self, indexer, library, db):
        # Recognised before any work is done on it: otherwise every scan
        # hashes 50 books it is about to throw away, and adds and
        # removes their rows for ever.
        self.locked(library)
        report = indexer.run()
        assert report.locked == 1
        assert report.added == 0
        assert indexer.hashed == 0

    def test_the_summary_mentions_them(self, indexer, library, db):
        self.locked(library)
        assert "locked" in indexer.run().summary()
