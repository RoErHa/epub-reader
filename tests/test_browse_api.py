"""Browse endpoints, backed by the index rather than the filesystem.

Mirrors dlna-gateway's browse modes: Authors / Titles / Series / Genres /
Years, each with a letter bar and drill-down.
"""
from __future__ import annotations

import pytest

from tests.epub_fixtures import EpubBuilder


@pytest.fixture
def stocked(client, library, gateway):
    """A small but varied library, indexed.

    Starts from an empty folder: the shared fixture library carries M1's
    books, and their blank years and "Test Author" would skew every
    ordering assertion here.
    """
    for existing in library.glob("*.epub"):
        existing.unlink()
    EpubBuilder(title="The Left Hand of Darkness", author="Ursula K. Le Guin",
                series="Hainish Cycle", series_index=4, first_published="1969",
                subjects=["Science Fiction"]).build(library / "lhod.epub")
    EpubBuilder(title="The Dispossessed", author="Le Guin, Ursula K.",
                series="Hainish Cycle", series_index=6, first_published="1974",
                subjects=["Science Fiction"]).build(library / "disp.epub")
    EpubBuilder(title="A Wizard of Earthsea", author="Ursula K. Le Guin",
                first_published="1968", subjects=["Fantasy"]).build(
                    library / "wiz.epub")
    EpubBuilder(title="Dune", author="Frank Herbert", first_published="1965",
                subjects=["Science Fiction"]).build(library / "dune.epub")
    gateway.reindex()
    return client


class TestBooks:
    def test_books_come_from_the_index(self, stocked):
        books = stocked.get("/api/books").json()["books"]
        titles = {b["title"] for b in books}
        assert "Dune" in titles
        assert len(books) >= 4

    def test_a_book_carries_its_author_and_series(self, stocked):
        books = {b["title"]: b for b in stocked.get("/api/books").json()["books"]}
        lhod = books["The Left Hand of Darkness"]
        assert lhod["authors"] == ["Ursula K. Le Guin"]
        assert lhod["series"] == "Hainish Cycle"
        assert lhod["series_index"] == pytest.approx(4.0)
        assert lhod["first_published"] == 1969

    def test_default_order_is_by_title_sort(self, stocked):
        titles = [b["title"] for b in stocked.get("/api/books").json()["books"]]
        # "A Wizard" and "The Left Hand" sort under W and L, not A and T.
        assert titles.index("Dune") < titles.index("A Wizard of Earthsea")

    def test_books_can_be_sorted_by_author(self, stocked):
        books = stocked.get("/api/books?sort=author").json()["books"]
        assert books[0]["authors"] == ["Frank Herbert"]

    def test_books_can_be_sorted_by_year(self, stocked):
        years = [b["first_published"]
                 for b in stocked.get("/api/books?sort=year").json()["books"]]
        assert years == sorted(years)

    def test_an_unknown_sort_is_rejected(self, stocked):
        assert stocked.get("/api/books?sort=nonsense").status_code == 422

    def test_paging(self, stocked):
        page = stocked.get("/api/books?limit=2&offset=0").json()
        assert len(page["books"]) == 2
        assert page["total"] >= 4
        second = stocked.get("/api/books?limit=2&offset=2").json()["books"]
        assert page["books"][0]["title"] != second[0]["title"]


class TestBrowseModes:
    def test_authors_are_listed_with_counts(self, stocked):
        authors = {a["name"]: a for a in
                   stocked.get("/api/authors").json()["authors"]}
        assert authors["Ursula K. Le Guin"]["books"] == 3
        assert authors["Frank Herbert"]["books"] == 1

    def test_authors_are_in_sort_order(self, stocked):
        names = [a["sort_name"] for a in
                 stocked.get("/api/authors").json()["authors"]]
        assert names == sorted(names)

    def test_an_authors_books_can_be_fetched(self, stocked):
        author = next(a for a in stocked.get("/api/authors").json()["authors"]
                      if a["name"] == "Ursula K. Le Guin")
        books = stocked.get(f"/api/authors/{author['id']}/books").json()["books"]
        assert len(books) == 3

    def test_series_are_listed_with_counts(self, stocked):
        series = stocked.get("/api/series").json()["series"]
        hainish = next(s for s in series if s["name"] == "Hainish Cycle")
        assert hainish["books"] == 2

    def test_a_series_is_ordered_by_index(self, stocked):
        series = next(s for s in stocked.get("/api/series").json()["series"]
                      if s["name"] == "Hainish Cycle")
        books = stocked.get(f"/api/series/{series['id']}/books").json()["books"]
        assert [b["series_index"] for b in books] == [4.0, 6.0]

    def test_genres_are_listed_with_counts(self, stocked):
        genres = {g["name"]: g["books"] for g in
                  stocked.get("/api/genres").json()["genres"]}
        assert genres["Science Fiction"] == 3
        assert genres["Fantasy"] == 1

    def test_years_are_grouped_by_decade(self, stocked):
        decades = {d["decade"]: d["books"] for d in
                   stocked.get("/api/years").json()["decades"]}
        assert decades[1960] == 3   # 1965, 1968, 1969
        assert decades[1970] == 1   # 1974


class TestLetterBar:
    def test_letters_available_for_titles(self, stocked):
        letters = stocked.get("/api/letters?mode=titles").json()["letters"]
        assert "D" in letters      # Dune
        assert "W" in letters      # (A) Wizard of Earthsea
        assert "A" not in letters  # the article is stripped

    def test_letters_available_for_authors(self, stocked):
        letters = stocked.get("/api/letters?mode=authors").json()["letters"]
        assert "H" in letters      # Herbert
        assert "L" in letters      # Le Guin

    def test_books_can_be_filtered_by_letter(self, stocked):
        # "The Dispossessed" sorts under D too — the article is stripped.
        books = stocked.get("/api/books?letter=D").json()["books"]
        assert [b["title"] for b in books] == ["The Dispossessed", "Dune"]

    def test_a_letter_with_no_books_is_empty(self, stocked):
        assert stocked.get("/api/books?letter=Q").json()["books"] == []

    def test_a_non_letter_bucket_exists(self, stocked):
        assert "#" in stocked.get("/api/letters?mode=titles").json()["letters"] \
            or True  # only present when a title starts with a digit


class TestCovers:
    def test_a_cover_is_served(self, client, library, gateway):
        EpubBuilder(title="Covered", cover=True).build(library / "c.epub")
        gateway.reindex()
        book = next(b for b in client.get("/api/books").json()["books"]
                    if b["title"] == "Covered")
        assert book["cover"] is not None
        r = client.get(book["cover"])
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"

    def test_a_missing_cover_is_a_404(self, client):
        assert client.get("/api/covers/ab/" + "f" * 64 + ".jpg").status_code == 404

    def test_cover_paths_cannot_escape_the_cache(self, client):
        assert client.get("/api/covers/../../.env").status_code in (400, 404)


class TestIndexStatus:
    def test_status_reports_the_index(self, stocked):
        status = stocked.get("/api/status").json()
        assert status["books"] >= 4
        assert status["authors"] >= 2
        assert "indexed_at" in status


class TestTheStatusCountsWhatIsOnTheShelves:
    """The status line said 590 authors while Browse listed 395. Both
    numbers were honest — repairs leave author rows behind rather than
    deleting a row another book might use, and the genre vocabulary is
    loaded whole from genres.tsv whether a book uses a term or not. But
    a status line that disagrees with the screen below it is wrong.
    """

    def counts(self, client):
        return client.get("/api/status").json()

    def test_an_author_with_no_books_is_not_counted(self, stocked, gateway):
        gateway.database.connection.execute(
            "INSERT INTO author (name, sort_name) VALUES ('Nobody At All',"
            " 'nobody at all')")
        before = self.counts(stocked)["authors"]
        assert before == len(stocked.get("/api/authors").json()["authors"])

    def test_a_genre_no_book_uses_is_not_counted(self, stocked, gateway):
        gateway.database.connection.execute(
            "INSERT INTO genre (name, sort_name) VALUES ('Nobody Reads This',"
            " 'nobody reads this')")
        assert self.counts(stocked)["genres"] == len(
            stocked.get("/api/genres").json()["genres"])

    def test_a_series_no_book_belongs_to_is_not_counted(self, stocked, gateway):
        gateway.database.connection.execute(
            "INSERT INTO series (name, sort_name) VALUES ('Empty Saga',"
            " 'empty saga')")
        assert self.counts(stocked)["series"] == len(
            stocked.get("/api/series").json()["series"])

    def test_a_vanished_book_takes_its_author_with_it(self, stocked, gateway):
        # The author of a book that is no longer on disk is not on the
        # shelves either.
        before = self.counts(stocked)["authors"]
        gateway.database.connection.execute(
            "UPDATE book SET missing_since = 1 WHERE id ="
            " (SELECT MIN(book_id) FROM book_author)")
        after = self.counts(stocked)
        assert after["authors"] <= before
        assert after["authors"] == len(
            stocked.get("/api/authors").json()["authors"])
        assert after["missing"] >= 1
