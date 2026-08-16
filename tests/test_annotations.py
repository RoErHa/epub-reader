"""Highlights and notes.

Server-side and shared across devices, like reading position (decision
7): a passage you marked on the iPad is marked on the Mac. What is
per-device is how the page looks, not what is on it.

`annotation.text` deliberately duplicates what the CFI points at. A CFI
can stop resolving — a book replaced by a better scan, a file edited —
and a highlight whose words survive is recoverable, while a dangling
pointer is not.
"""
from __future__ import annotations

import pytest

from tests.epub_fixtures import EpubBuilder


@pytest.fixture
def book(client, library, gateway):
    for existing in library.rglob("*.epub"):
        existing.unlink()
    EpubBuilder(title="Marked Up", author="A Reader").build(library / "a.epub")
    EpubBuilder(title="Untouched", author="A Reader").build(library / "b.epub")
    gateway.reindex()
    return next(b["id"] for b in client.get("/api/books").json()["books"]
                if b["title"] == "Marked Up")


def make(client, book_id, **kwargs):
    body = {"cfi": "epubcfi(/6/4!/4/2,/1:0,/1:24)",
            "text": "It was a bright cold day in April.",
            "fraction": 0.12}
    body.update(kwargs)
    return client.post(f"/api/books/{book_id}/annotations", json=body)


def listing(client, book_id):
    return client.get(f"/api/books/{book_id}/annotations").json()["annotations"]


class TestMakingOne:
    def test_a_highlight_is_kept(self, client, book):
        assert make(client, book).status_code == 201
        marks = listing(client, book)
        assert len(marks) == 1
        assert marks[0]["text"] == "It was a bright cold day in April."

    def test_it_comes_back_with_an_id(self, client, book):
        assert make(client, book).json()["id"] > 0

    def test_the_quoted_words_are_required(self, client, book):
        # Without them a highlight cannot survive a broken CFI.
        assert make(client, book, text="").status_code == 422

    def test_a_position_is_required(self, client, book):
        assert make(client, book, cfi="").status_code == 422

    def test_a_note_can_be_written_with_it(self, client, book):
        make(client, book, note="Orwell opens on a clock.")
        assert listing(client, book)[0]["note"] == "Orwell opens on a clock."

    def test_a_colour_is_kept(self, client, book):
        make(client, book, color="green")
        assert listing(client, book)[0]["color"] == "green"

    def test_a_colour_nobody_can_see_is_refused(self, client, book):
        assert make(client, book, color="chartreuse").status_code == 422

    def test_an_unknown_book_is_a_404(self, client):
        assert make(client, 999999).status_code == 404


class TestReadingThemBack:
    def test_they_come_in_book_order(self, client, book):
        make(client, book, fraction=0.80, text="Later", cfi="epubcfi(/6/14!/4)")
        make(client, book, fraction=0.10, text="Earlier", cfi="epubcfi(/6/4!/4)")
        assert [m["text"] for m in listing(client, book)] == ["Earlier", "Later"]

    def test_another_book_has_its_own(self, client, book, gateway):
        other = next(b["id"] for b in client.get("/api/books").json()["books"]
                     if b["title"] == "Untouched")
        make(client, book)
        assert listing(client, other) == []

    def test_a_book_with_none_says_so_rather_than_failing(self, client, book):
        assert listing(client, book) == []

    def test_the_count_travels_with_the_book(self, client, book):
        make(client, book)
        make(client, book, cfi="epubcfi(/6/8!/4)", text="Another", fraction=0.5)
        row = next(b for b in client.get("/api/books").json()["books"]
                   if b["id"] == book)
        assert row["annotations"] == 2


class TestChangingOne:
    def test_a_note_can_be_added_afterwards(self, client, book):
        mark = make(client, book).json()["id"]
        assert client.patch(f"/api/annotations/{mark}",
                            json={"note": "Second thoughts."}).status_code == 200
        assert listing(client, book)[0]["note"] == "Second thoughts."

    def test_the_colour_can_change(self, client, book):
        mark = make(client, book).json()["id"]
        client.patch(f"/api/annotations/{mark}", json={"color": "blue"})
        assert listing(client, book)[0]["color"] == "blue"

    def test_the_words_and_the_place_are_not_editable(self, client, book):
        # They are what the highlight *is*. Changing them would make it
        # a different highlight wearing the same id.
        mark = make(client, book).json()["id"]
        client.patch(f"/api/annotations/{mark}",
                     json={"text": "Something else", "cfi": "epubcfi(/6/99)"})
        kept = listing(client, book)[0]
        assert kept["text"] == "It was a bright cold day in April."
        assert kept["cfi"] == "epubcfi(/6/4!/4/2,/1:0,/1:24)"

    def test_changing_one_that_is_gone_is_a_404(self, client, book):
        assert client.patch("/api/annotations/999999",
                            json={"note": "x"}).status_code == 404


class TestRemovingOne:
    def test_it_goes(self, client, book):
        mark = make(client, book).json()["id"]
        assert client.delete(f"/api/annotations/{mark}").status_code == 204
        assert listing(client, book) == []

    def test_removing_it_twice_is_not_an_error_the_second_time(self, client,
                                                               book):
        mark = make(client, book).json()["id"]
        client.delete(f"/api/annotations/{mark}")
        assert client.delete(f"/api/annotations/{mark}").status_code == 404

    def test_the_others_stay(self, client, book):
        first = make(client, book).json()["id"]
        make(client, book, cfi="epubcfi(/6/8!/4)", text="Keep me", fraction=0.4)
        client.delete(f"/api/annotations/{first}")
        assert [m["text"] for m in listing(client, book)] == ["Keep me"]


class TestWhenTheBookGoes:
    def test_annotations_go_with_it(self, client, book, gateway):
        make(client, book)
        gateway.database.connection.execute("PRAGMA foreign_keys = ON")
        gateway.database.connection.execute("DELETE FROM book WHERE id = ?",
                                            (book,))
        assert gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM annotation WHERE book_id = ?", (book,)
        ).fetchone()["n"] == 0

    def test_a_book_that_merely_vanished_keeps_them(self, client, book, gateway):
        # The file is off the disk, not deleted from the library — the
        # row survives, and so should what you wrote on it.
        make(client, book)
        gateway.database.connection.execute(
            "UPDATE book SET missing_since = 1 WHERE id = ?", (book,))
        assert len(listing(client, book)) == 1
