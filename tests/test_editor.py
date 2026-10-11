"""Editing a book's whole record by hand (§12.3).

Hand-editing was authors and series only (§8.10). What no pass can
reach — `Bamboo Bloodbath & Ninj`, a series nobody else has heard of, a
synopsis no file carried — only a person can, so the rest of the record
opens up: title, synopsis, genres, first published.

Every field a person sets is `origin = 'user'`, locked, and no scan or
pass touches it again. That is §6.4's rule, used deliberately.

And underneath it, a fault the editor could not be built on top of: a
title has three copies — the column, its sort key and the search row —
and six passes rewrote the first and forgot the other two.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from epub_browse import BrowseQueries
from epub_editor import BookEditor, TitleWriter
from tests.epub_fixtures import EpubBuilder

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def book(client, library, gateway):
    for old in library.rglob("*.epub"):
        old.unlink()
    EpubBuilder(title="Bamboo Bloodbath & Ninj", author="Piers Anthony",
                description="What the file says.").build(
        library / "bamboo.epub")
    gateway.reindex()
    return next(b["id"] for b in client.get("/api/books").json()["books"]
                if b["title"] == "Bamboo Bloodbath & Ninj")


@pytest.fixture
def editor(gateway):
    return BookEditor(gateway.database)


def browse(gateway):
    return BrowseQueries(gateway.database)


def meta(gateway, book_id):
    return gateway.database.connection.execute(
        "SELECT * FROM metadata WHERE book_id = ?", (book_id,)).fetchone()


def provenance(gateway, book_id, field):
    return gateway.database.connection.execute(
        "SELECT origin, locked FROM field_provenance"
        " WHERE book_id = ? AND field = ?", (book_id, field)).fetchone()


def found(client, query):
    return [b["id"] for b in client.get(f"/api/search?q={query}").json()["books"]]


def touch(path: Path) -> None:
    stamp = path.stat().st_mtime + 60
    os.utime(path, (stamp, stamp))


class TestATitleHasThreeCopies:
    """The column, the sort key, and the search row. Writing one and not
    the others is how 368 books came to sort under the filename they no
    longer carry, and 386 to be found only by it."""

    def test_the_sort_key_follows_the_title(self, gateway, book):
        TitleWriter(gateway.database).write(book, "The Bamboo Bloodbath")
        assert meta(gateway, book)["title_sort"] == "bamboo bloodbath"

    def test_search_finds_the_new_title(self, gateway, client, book):
        TitleWriter(gateway.database).write(book, "Ninja's Revenge")
        assert book in found(client, "revenge")

    def test_search_forgets_the_old_one(self, gateway, client, book):
        TitleWriter(gateway.database).write(book, "Ninja's Revenge")
        assert book not in found(client, "bloodbath")

    def test_what_the_file_said_is_kept(self, gateway, book):
        writer = TitleWriter(gateway.database)
        writer.write(book, "First")
        writer.write(book, "Second")
        # Every rename reversible: the file's own words, not the last
        # thing somebody wrote over them.
        assert meta(gateway, book)["file_title"] == "Bamboo Bloodbath & Ninj"

    def test_no_pass_writes_a_title_any_other_way(self):
        # A rule that must be copied into N places will be in N−1 (§10.14).
        # Six passes wrote `title = ?` themselves and five forgot the sort
        # key; now only the writer may.
        offenders = []
        for module in ROOT.glob("epub_*.py"):
            if module.name in {"epub_editor.py", "epub_db.py", "epub_index.py"}:
                continue
            if "SET file_title" in module.read_text():
                offenders.append(module.name)
        assert offenders == []


class TestAScanHealsWhatWasAlreadyWrong:
    def test_a_stale_sort_key_is_put_right(self, gateway, book):
        gateway.database.connection.execute(
            "UPDATE metadata SET title = 'Refugee' WHERE book_id = ?", (book,))
        gateway.reindex()
        assert meta(gateway, book)["title_sort"] == "refugee"

    def test_a_stale_search_row_is_put_right(self, gateway, client, book):
        gateway.database.connection.execute(
            "UPDATE metadata SET title = 'Refugee' WHERE book_id = ?", (book,))
        gateway.reindex()
        assert book in found(client, "refugee")

    def test_a_stale_author_in_search_is_put_right(self, gateway, client, book):
        # Passes credit authors without telling search either.
        gateway.database.connection.execute(
            "UPDATE author SET name = 'Roberto Fuentes'"
            " WHERE name = 'Piers Anthony'")
        gateway.reindex()
        assert book in found(client, "fuentes")


class TestEditingTheRecord:
    def test_the_title(self, editor, gateway, book):
        editor.edit(book, {"title": "The Bamboo Bloodbath"})
        assert meta(gateway, book)["title"] == "The Bamboo Bloodbath"

    def test_the_synopsis(self, editor, gateway, book):
        editor.edit(book, {"synopsis": "Jason Striker, again."})
        assert meta(gateway, book)["synopsis"] == "Jason Striker, again."

    def test_emptying_the_synopsis_clears_it(self, editor, gateway, book):
        editor.edit(book, {"synopsis": "  "})
        assert meta(gateway, book)["synopsis"] is None

    def test_first_published(self, editor, gateway, book):
        editor.edit(book, {"first_published": 1975})
        assert meta(gateway, book)["first_published"] == 1975

    def test_genres_in_order(self, editor, gateway, book):
        editor.edit(book, {"genres": ["Martial Arts", "Thriller"]})
        assert browse(gateway).genres_of(book) == [
            "Martial Arts", "Thriller"]

    def test_a_genre_already_held_is_reused(self, editor, gateway, book):
        gateway.database.connection.execute(
            "INSERT OR IGNORE INTO genre (name, sort_name)"
            " VALUES ('Science Fiction', 'science fiction')")
        editor.edit(book, {"genres": ["science fiction"]})
        # One spelling per genre, or the Genres browse splits in two.
        assert browse(gateway).genres_of(book) == ["Science Fiction"]

    def test_authors(self, editor, gateway, book):
        editor.edit(book, {"authors": ["Piers Anthony", "Roberto Fuentes"]})
        assert browse(gateway).book(book)["authors"] == [
            "Piers Anthony", "Roberto Fuentes"]

    def test_series_and_number(self, editor, gateway, book):
        editor.edit(book, {"series": "Jason Striker", "series_index": 3})
        shown = browse(gateway).book(book)
        assert (shown["series"], shown["series_index"]) == ("Jason Striker", 3)

    def test_search_sees_every_edited_field(self, editor, client, book):
        editor.edit(book, {"synopsis": "Karate in Kyoto",
                           "series": "Jason Striker",
                           "authors": ["Roberto Fuentes"]})
        for word in ("kyoto", "striker", "fuentes"):
            assert book in found(client, word), word


class TestWhatYouSayIsKept:
    @pytest.mark.parametrize("field, value", [
        ("title", "The Bamboo Bloodbath"),
        ("synopsis", "Mine."),
        ("first_published", 1975),
        ("genres", ["Thriller"]),
    ])
    def test_it_is_yours_and_locked(self, editor, gateway, book, field, value):
        editor.edit(book, {field: value})
        row = provenance(gateway, book, field)
        assert (row["origin"], row["locked"]) == ("user", 1)

    def test_a_rescan_does_not_put_the_file_back(self, editor, gateway, book,
                                                 library):
        editor.edit(book, {"title": "The Bamboo Bloodbath",
                           "synopsis": "Mine.", "genres": ["Thriller"],
                           "first_published": 1975})
        touch(library / "bamboo.epub")
        gateway.reindex()
        row = meta(gateway, book)
        assert row["title"] == "The Bamboo Bloodbath"
        assert row["synopsis"] == "Mine."
        assert row["first_published"] == 1975
        assert browse(gateway).genres_of(book) == ["Thriller"]

    def test_a_field_not_mentioned_is_not_touched(self, editor, gateway, book):
        # Saying nothing about a field is not saying it is wrong.
        editor.edit(book, {"title": "The Bamboo Bloodbath"})
        assert provenance(gateway, book, "synopsis") is None
        assert meta(gateway, book)["synopsis"] == "What the file says."

    def test_saving_the_same_title_claims_nothing(self, editor, gateway, book):
        # An editor sends the whole form; an unchanged title is not a
        # decision, and locking it would freeze the file's typo for ever.
        editor.edit(book, {"title": "Bamboo Bloodbath & Ninj"})
        assert provenance(gateway, book, "title") is None


class TestBeingCareful:
    def test_a_book_that_does_not_exist(self, editor):
        with pytest.raises(LookupError):
            editor.edit(999999, {"title": "Anything"})

    def test_an_empty_title_is_refused(self, editor, book):
        # A book called nothing falls out of every list sorted by title.
        with pytest.raises(ValueError):
            editor.edit(book, {"title": "   "})

    @pytest.mark.parametrize("year", [0, 99999, -5000])
    def test_a_year_that_is_not_one(self, editor, book, year):
        with pytest.raises(ValueError):
            editor.edit(book, {"first_published": year})

    def test_a_field_nobody_can_edit(self, editor, book):
        with pytest.raises(ValueError):
            editor.edit(book, {"isbn13": "9780000000000"})

    def test_one_bad_field_writes_nothing(self, editor, gateway, book):
        # All or nothing: half a save is a record nobody asked for.
        with pytest.raises(ValueError):
            editor.edit(book, {"title": "Good", "first_published": 99999})
        assert meta(gateway, book)["title"] == "Bamboo Bloodbath & Ninj"
        assert provenance(gateway, book, "title") is None

    def test_a_bad_author_list_writes_nothing(self, editor, gateway, book):
        with pytest.raises(ValueError):
            editor.edit(book, {"title": "Good", "authors": []})
        assert meta(gateway, book)["title"] == "Bamboo Bloodbath & Ninj"


class TestTheEndpoint:
    def test_it_saves_and_answers_with_the_book(self, client, book):
        res = client.patch(f"/api/books/{book}/metadata",
                           json={"title": "The Bamboo Bloodbath",
                                 "genres": ["Thriller"]})
        assert res.status_code == 200
        body = res.json()
        assert body["book"]["title"] == "The Bamboo Bloodbath"
        assert body["genres"] == ["Thriller"]

    def test_it_says_which_fields_are_yours(self, client, book):
        client.patch(f"/api/books/{book}/metadata", json={"synopsis": "Mine."})
        detail = client.get(f"/api/books/{book}/detail").json()
        assert detail["book"]["edited"] == ["synopsis"]

    def test_null_clears_and_absence_does_not(self, client, gateway, book):
        client.patch(f"/api/books/{book}/metadata", json={"synopsis": None})
        assert meta(gateway, book)["synopsis"] is None
        client.patch(f"/api/books/{book}/metadata", json={"synopsis": "Back."})
        client.patch(f"/api/books/{book}/metadata", json={})
        assert meta(gateway, book)["synopsis"] == "Back."

    def test_an_unknown_book_is_a_404(self, client):
        assert client.patch("/api/books/999999/metadata",
                            json={"title": "x"}).status_code == 404

    def test_a_bad_value_is_a_422(self, client, book):
        assert client.patch(f"/api/books/{book}/metadata",
                            json={"title": ""}).status_code == 422

    def test_an_unknown_field_is_a_422(self, client, book):
        assert client.patch(f"/api/books/{book}/metadata",
                            json={"isbn13": "x"}).status_code == 422
