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
