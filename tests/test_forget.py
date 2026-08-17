"""Forgetting a book whose file has gone.

A scan notices a deletion, but it does not act on one: the row is marked
`missing_since` and kept, which is why a vanished book leaves the shelves
at once while your place in it survives. §5 says so in as many words —
"vanished; row never auto-deleted".

That is right for a file that is temporarily unplugged and wrong for one
you deleted on purpose, so forgetting is a separate act, asked for
explicitly.
"""
from __future__ import annotations

import pytest

from epub_forget import ForgetMissing, UnpluggedError
from tests.epub_fixtures import EpubBuilder


@pytest.fixture
def forgetter(gateway):
    return ForgetMissing(gateway.database)


def shelf(library, gateway, count=4):
    """A library of exactly `count` books.

    The shared fixture brings its own, and deleting their files would
    leave them behind as missing rows — so the database is emptied too,
    and every count in these tests is then the whole truth.
    """
    for old in library.rglob("*"):
        if old.is_file():
            old.unlink()
    gateway.database.connection.execute("DELETE FROM book")
    for i in range(count):
        EpubBuilder(title=f"Book {i}", author="A Reader",
                    chapters=1 + i).build(library / f"b{i}.epub")
    gateway.reindex()


def lose(library, gateway, name):
    (library / name).unlink()
    gateway.reindex()


def rows(gateway):
    return gateway.database.connection.execute(
        "SELECT COUNT(*) n FROM book").fetchone()["n"]


class TestWhatAScanDoes:
    def test_a_deleted_book_is_marked_not_removed(self, client, library,
                                                  gateway):
        shelf(library, gateway)
        lose(library, gateway, "b0.epub")
        assert gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM book WHERE missing_since IS NOT NULL"
        ).fetchone()["n"] == 1
        assert rows(gateway) == 4

    def test_it_leaves_the_shelves_at_once(self, client, library, gateway):
        shelf(library, gateway)
        lose(library, gateway, "b0.epub")
        titles = [b["title"] for b in client.get("/api/books").json()["books"]]
        assert "Book 0" not in titles


class TestForgetting:
    def test_a_dry_run_changes_nothing(self, client, library, gateway,
                                       forgetter):
        shelf(library, gateway)
        lose(library, gateway, "b0.epub")
        report = forgetter.run(apply=False)
        assert report["forgotten"] == 0
        assert report["would_forget"] == 1
        assert rows(gateway) == 4

    def test_applying_removes_the_row(self, client, library, gateway,
                                      forgetter):
        shelf(library, gateway)
        lose(library, gateway, "b0.epub")
        assert forgetter.run(apply=True)["forgotten"] == 1
        assert rows(gateway) == 3

    def test_the_books_that_are_there_are_untouched(self, client, library,
                                                    gateway, forgetter):
        shelf(library, gateway)
        lose(library, gateway, "b0.epub")
        forgetter.run(apply=True)
        titles = {b["title"] for b in client.get("/api/books").json()["books"]}
        assert titles == {"Book 1", "Book 2", "Book 3"}

    def test_its_highlights_go_with_it(self, client, library, gateway,
                                       forgetter):
        shelf(library, gateway)
        book = next(b for b in client.get("/api/books").json()["books"]
                    if b["title"] == "Book 0")
        client.post(f"/api/books/{book['id']}/annotations",
                    json={"cfi": "epubcfi(/6/4)", "text": "A line.",
                          "fraction": 0.1})
        lose(library, gateway, "b0.epub")
        forgetter.run(apply=True)
        assert gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM annotation").fetchone()["n"] == 0

    def test_its_place_goes_with_it(self, client, library, gateway, forgetter):
        shelf(library, gateway)
        book = next(b for b in client.get("/api/books").json()["books"]
                    if b["title"] == "Book 0")
        client.put(f"/api/books/{book['id']}/position",
                   json={"cfi": "epubcfi(/6/4)", "fraction": 0.5})
        lose(library, gateway, "b0.epub")
        forgetter.run(apply=True)
        assert gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM reading_state").fetchone()["n"] == 0

    def test_it_says_what_would_be_lost(self, client, library, gateway,
                                        forgetter):
        shelf(library, gateway)
        book = next(b for b in client.get("/api/books").json()["books"]
                    if b["title"] == "Book 0")
        client.post(f"/api/books/{book['id']}/annotations",
                    json={"cfi": "epubcfi(/6/4)", "text": "A line.",
                          "fraction": 0.1})
        lose(library, gateway, "b0.epub")
        plan = forgetter.plan()
        assert plan[0]["title"] == "Book 0"
        assert plan[0]["marks"] == 1

    def test_a_library_with_nothing_missing_says_so(self, client, library,
                                                    gateway, forgetter):
        shelf(library, gateway)
        assert forgetter.run(apply=True)["forgotten"] == 0


class TestWhenTheDriveIsUnplugged:
    """A whole library that has 'vanished' has not been deleted; it is
    on a disk that is not mounted. Forgetting it would throw away every
    highlight and every reading position in one go."""

    def test_it_refuses_when_most_of_the_library_is_missing(self, client,
                                                            library, gateway,
                                                            forgetter):
        shelf(library, gateway)
        for old in library.rglob("*.epub"):
            old.unlink()
        gateway.reindex()
        with pytest.raises(UnpluggedError):
            forgetter.run(apply=True)
        assert rows(gateway) == 4

    def test_it_can_be_insisted_upon(self, client, library, gateway,
                                     forgetter):
        shelf(library, gateway)
        for old in library.rglob("*.epub"):
            old.unlink()
        gateway.reindex()
        assert forgetter.run(apply=True, force=True)["forgotten"] == 4

    def test_a_few_missing_is_not_an_unplugged_drive(self, client, library,
                                                     gateway, forgetter):
        shelf(library, gateway, count=10)
        lose(library, gateway, "b0.epub")
        assert forgetter.run(apply=True)["forgotten"] == 1
