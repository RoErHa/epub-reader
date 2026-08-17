"""Correcting who wrote a book.

Some files simply lie. "Star Wars - Refugee" carries `Dave Taylor` as
its `dc:creator`, and "Edge of Victory - Rebirth" carries `David
Taylor`; the books are by Sean Williams & Shane Dix and by Greg Keyes.
Nothing automatic can fix that — the file is confident and wrong, and
the two names look like an author who needs merging rather than a
mistake.

So a reader can say who wrote it, and what they say is kept: `origin =
'user'`, locked, and never touched again by a scan or a pass.
"""
from __future__ import annotations

import pytest

from epub_credits import BookCredits
from tests.epub_fixtures import EpubBuilder


@pytest.fixture
def credits(gateway):
    return BookCredits(gateway.database)


@pytest.fixture
def book(client, library, gateway):
    for old in library.rglob("*.epub"):
        old.unlink()
    EpubBuilder(title="Star Wars - Refugee", author="Dave Taylor").build(
        library / "refugee.epub")
    gateway.reindex()
    return next(b["id"] for b in client.get("/api/books").json()["books"]
                if b["title"] == "Star Wars - Refugee")


def authors_of(gateway, book_id):
    return [r["name"] for r in gateway.database.connection.execute(
        """SELECT a.name FROM book_author ba JOIN author a ON a.id = ba.author_id
            WHERE ba.book_id = ? ORDER BY ba.position""", (book_id,))]


class TestSayingWhoWroteIt:
    def test_the_author_is_replaced(self, credits, gateway, book):
        credits.set(book, ["Sean Williams", "Shane Dix"])
        assert authors_of(gateway, book) == ["Sean Williams", "Shane Dix"]

    def test_the_wrong_one_no_longer_has_the_book(self, credits, gateway, book):
        credits.set(book, ["Sean Williams"])
        assert "Dave Taylor" not in authors_of(gateway, book)

    def test_credit_order_is_kept(self, credits, gateway, book):
        # "Williams & Dix" is not "Dix & Williams".
        credits.set(book, ["Sean Williams", "Shane Dix"])
        assert authors_of(gateway, book)[0] == "Sean Williams"

    def test_a_name_written_backwards_is_understood(self, credits, gateway,
                                                    book):
        credits.set(book, ["Keyes, Greg"])
        assert authors_of(gateway, book) == ["Greg Keyes"]

    def test_an_author_already_in_the_library_is_reused(self, credits, gateway,
                                                        book, library):
        EpubBuilder(title="Another", author="Greg Keyes").build(
            library / "another.epub")
        gateway.reindex()
        before = gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM author WHERE name = 'Greg Keyes'"
        ).fetchone()["n"]
        credits.set(book, ["Greg Keyes"])
        assert gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM author WHERE name = 'Greg Keyes'"
        ).fetchone()["n"] == before

    def test_it_is_recorded_as_yours(self, credits, gateway, book):
        credits.set(book, ["Sean Williams"])
        row = gateway.database.connection.execute(
            "SELECT origin, locked FROM field_provenance"
            " WHERE book_id = ? AND field = 'authors'", (book,)).fetchone()
        assert row["origin"] == "user"
        assert row["locked"] == 1

    def test_a_rescan_does_not_put_the_file_back(self, credits, gateway, book,
                                                 library):
        # The file still says Dave Taylor, and will say so for ever.
        credits.set(book, ["Sean Williams"])
        import os
        path = library / "refugee.epub"
        stamp = path.stat().st_mtime + 60
        os.utime(path, (stamp, stamp))
        gateway.reindex()
        assert authors_of(gateway, book) == ["Sean Williams"]


class TestBeingCareful:
    def test_a_book_that_does_not_exist(self, credits):
        with pytest.raises(LookupError):
            credits.set(999999, ["Somebody"])

    def test_no_authors_at_all_is_refused(self, credits, book):
        # A book with nobody is worse than a book with the wrong person.
        with pytest.raises(ValueError):
            credits.set(book, [])

    def test_a_name_that_is_not_a_name_is_refused(self, credits, book):
        with pytest.raises(ValueError):
            credits.set(book, ["   "])


class TestTheEndpoint:
    def test_it_sets_the_authors(self, client, book):
        res = client.put(f"/api/books/{book}/authors",
                         json={"authors": ["Sean Williams", "Shane Dix"]})
        assert res.status_code == 200
        assert res.json()["authors"] == ["Sean Williams", "Shane Dix"]

    def test_an_unknown_book_is_a_404(self, client):
        assert client.put("/api/books/999999/authors",
                          json={"authors": ["Somebody"]}).status_code == 404

    def test_an_empty_list_is_refused(self, client, book):
        assert client.put(f"/api/books/{book}/authors",
                          json={"authors": []}).status_code == 422


class TestSayingWhatSeriesItIsIn:
    """M9 reads series from filenames, and refuses to invent one from a
    single book. "The Light of All That Falls" is book three of the
    Licanius Trilogy, its file says nothing, and its name carries no
    number — so nothing automatic will ever file it. A reader can."""

    def test_the_book_joins_the_series(self, credits, gateway, book):
        credits.set_series(book, "The Licanius Trilogy", 3)
        row = gateway.database.connection.execute(
            """SELECT s.name, m.series_index FROM metadata m
                 JOIN series s ON s.id = m.series_id WHERE m.book_id = ?""",
            (book,)).fetchone()
        assert row["name"] == "The Licanius Trilogy"
        assert row["series_index"] == 3

    def test_a_series_that_exists_is_reused(self, credits, gateway, book,
                                            library):
        from tests.epub_fixtures import EpubBuilder
        EpubBuilder(title="Book One", author="James Islington",
                    series="The Licanius Trilogy", series_index=1).build(
            library / "one.epub")
        gateway.reindex()
        before = gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM series WHERE name = 'The Licanius Trilogy'"
        ).fetchone()["n"]
        credits.set_series(book, "The Licanius Trilogy", 3)
        assert gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM series WHERE name = 'The Licanius Trilogy'"
        ).fetchone()["n"] == before

    def test_half_numbers_are_allowed(self, credits, gateway, book):
        # Novellas are book 2.5; series_index is REAL for this reason.
        credits.set_series(book, "Hainish Cycle", 2.5)
        assert gateway.database.connection.execute(
            "SELECT series_index FROM metadata WHERE book_id = ?", (book,)
        ).fetchone()["series_index"] == 2.5

    def test_a_book_can_have_a_series_without_a_number(self, credits, gateway,
                                                       book):
        credits.set_series(book, "Discworld", None)
        row = gateway.database.connection.execute(
            """SELECT s.name, m.series_index FROM metadata m
                 JOIN series s ON s.id = m.series_id WHERE m.book_id = ?""",
            (book,)).fetchone()
        assert row["name"] == "Discworld"
        assert row["series_index"] is None

    def test_it_can_be_taken_out_of_a_series(self, credits, gateway, book):
        credits.set_series(book, "Wrong Series", 1)
        credits.set_series(book, None, None)
        assert gateway.database.connection.execute(
            "SELECT series_id FROM metadata WHERE book_id = ?", (book,)
        ).fetchone()["series_id"] is None

    def test_it_is_recorded_as_yours_and_locked(self, credits, gateway, book):
        credits.set_series(book, "The Licanius Trilogy", 3)
        row = gateway.database.connection.execute(
            "SELECT origin, locked FROM field_provenance"
            " WHERE book_id = ? AND field = 'series'", (book,)).fetchone()
        assert row["origin"] == "user"
        assert row["locked"] == 1

    def test_a_rescan_does_not_undo_it(self, credits, gateway, book, library):
        import os
        credits.set_series(book, "The Licanius Trilogy", 3)
        path = library / "refugee.epub"
        stamp = path.stat().st_mtime + 60
        os.utime(path, (stamp, stamp))
        gateway.reindex()
        assert gateway.database.connection.execute(
            """SELECT s.name FROM metadata m JOIN series s ON s.id = m.series_id
                WHERE m.book_id = ?""", (book,)).fetchone()["name"] == \
            "The Licanius Trilogy"

    def test_an_unknown_book(self, credits):
        with pytest.raises(LookupError):
            credits.set_series(999999, "Anything", 1)

    def test_a_blank_series_name_is_refused(self, credits, book):
        with pytest.raises(ValueError):
            credits.set_series(book, "   ", 1)


class TestTheSeriesEndpoint:
    def test_it_files_the_book(self, client, book):
        res = client.put(f"/api/books/{book}/series",
                         json={"series": "The Licanius Trilogy", "index": 3})
        assert res.status_code == 200
        assert res.json()["series"] == "The Licanius Trilogy"
        assert res.json()["index"] == 3

    def test_it_can_clear_the_series(self, client, book):
        client.put(f"/api/books/{book}/series",
                   json={"series": "Something", "index": 1})
        res = client.put(f"/api/books/{book}/series",
                         json={"series": None, "index": None})
        assert res.status_code == 200
        assert res.json()["series"] is None

    def test_an_unknown_book_is_a_404(self, client):
        assert client.put("/api/books/999999/series",
                          json={"series": "X", "index": 1}).status_code == 404
