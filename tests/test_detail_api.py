"""Everything the book-detail screen needs, in one request.

The screen shows a book the way you would decide whether to read it:
cover, series and number, author, genre, when it was first published,
then the synopsis, then who wrote it, then what else they wrote.
"""
from __future__ import annotations

import pytest

from tests.epub_fixtures import EpubBuilder

CFI = "epubcfi(/6/4[chap01]!/4/2/2[p1]:0)"


@pytest.fixture
def stocked(client, library, gateway):
    for existing in library.glob("*.epub"):
        existing.unlink()
    EpubBuilder(
        title="The Left Hand of Darkness", author="Ursula K. Le Guin",
        series="Hainish Cycle", series_index=4, first_published="1969",
        subjects=["Science Fiction", "Classics"], cover=True, chapters=3,
    ).build(library / "lhod.epub")
    EpubBuilder(
        title="A Wizard of Earthsea", author="Le Guin, Ursula K.",
        first_published="1968", subjects=["Fantasy"], chapters=2,
    ).build(library / "wiz.epub")
    EpubBuilder(title="Dune", author="Frank Herbert").build(library / "dune.epub")
    gateway.reindex()
    return client


def book_id(client, title):
    return next(b["id"] for b in client.get("/api/books").json()["books"]
                if b["title"] == title)


def detail(client, title):
    return client.get(f"/api/books/{book_id(client, title)}/detail").json()


class TestTheBookItself:
    def test_the_headline_facts_are_there(self, stocked):
        book = detail(stocked, "The Left Hand of Darkness")["book"]
        assert book["title"] == "The Left Hand of Darkness"
        assert book["authors"] == ["Ursula K. Le Guin"]
        assert book["series"] == "Hainish Cycle"
        assert book["series_index"] == pytest.approx(4.0)
        assert book["first_published"] == 1969
        assert book["cover"].startswith("/api/covers/")

    def test_the_synopsis_is_there(self, client, library, gateway):
        # It was selected and then dropped on the way to JSON, so every
        # book said it carried no description — 356 of them did.
        EpubBuilder(title="Blurbed", description="Genly Ai is an envoy."
                    ).build(library / "blurbed.epub")
        gateway.reindex()
        assert detail(client, "Blurbed")["book"]["synopsis"] == (
            "Genly Ai is an envoy.")

    def test_genres_come_through_in_order(self, stocked):
        assert detail(stocked, "The Left Hand of Darkness")["genres"] == [
            "Science Fiction", "Classics"
        ]

    def test_progress_is_included(self, stocked):
        book = book_id(stocked, "Dune")
        stocked.put(f"/api/books/{book}/position", json={"cfi": CFI, "fraction": 0.4})
        assert detail(stocked, "Dune")["book"]["percent"] == 40

    def test_the_shelves_it_sits_on_are_included(self, stocked):
        shelf = stocked.post("/api/shelves", json={"name": "Detail shelf"}).json()
        stocked.put(
            f"/api/shelves/{shelf['shelf']['id']}/books/{book_id(stocked, 'Dune')}"
        )
        names = [s["name"] for s in detail(stocked, "Dune")["book"]["shelves"]]
        assert names == ["Detail shelf"]

    def test_reading_details_are_there_to_show(self, stocked):
        book = detail(stocked, "The Left Hand of Darkness")["book"]
        assert book["format"] == "epub"
        assert book["language"] == "en"
        assert book["size"] > 0

    def test_an_unknown_book_is_a_404(self, stocked):
        assert stocked.get("/api/books/999999/detail").status_code == 404


class TestTheAuthor:
    def test_the_author_is_returned_with_their_id(self, stocked):
        author = detail(stocked, "The Left Hand of Darkness")["author"]
        assert author["name"] == "Ursula K. Le Guin"
        assert isinstance(author["id"], int)

    def test_a_book_with_no_author_still_works(self, stocked, library, gateway):
        EpubBuilder(title="Nameless", author="Unknown").build(library / "n.epub")
        gateway.reindex()
        assert detail(stocked, "Nameless")["author"] is None

    def test_the_bio_is_empty_until_it_is_fetched(self, stocked):
        # About the author is filled by enrichment; until then the screen
        # must have something honest to show rather than a broken panel.
        author = detail(stocked, "The Left Hand of Darkness")["author"]
        assert author["bio"] is None
        assert author["enriched"] is False


class TestAlsoPublished:
    def test_other_books_by_the_same_author_that_you_own(self, stocked):
        also = detail(stocked, "The Left Hand of Darkness")["also_published"]
        titles = [w["title"] for w in also]
        assert "A Wizard of Earthsea" in titles
        assert "The Left Hand of Darkness" not in titles  # not itself

    def test_owned_books_are_marked_as_owned(self, stocked):
        also = detail(stocked, "The Left Hand of Darkness")["also_published"]
        wizard = next(w for w in also if w["title"] == "A Wizard of Earthsea")
        assert wizard["owned"] is True
        assert isinstance(wizard["book_id"], int)

    def test_they_are_grouped_by_series(self, stocked):
        also = detail(stocked, "A Wizard of Earthsea")["also_published"]
        lhod = next(w for w in also if w["title"] == "The Left Hand of Darkness")
        assert lhod["series"] == "Hainish Cycle"
        assert lhod["series_index"] == pytest.approx(4.0)

    def test_an_author_with_one_book_has_an_empty_list(self, stocked):
        assert detail(stocked, "Dune")["also_published"] == []

    def test_a_missing_book_does_not_appear(self, stocked, library, gateway):
        (library / "wiz.epub").unlink()
        gateway.reindex()
        assert detail(stocked, "The Left Hand of Darkness")["also_published"] == []
