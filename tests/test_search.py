"""Full-text search over the index.

FTS5 with the same tokenizer dlna-gateway already uses
(unicode61 remove_diacritics 1), so "le carre" finds "le Carré".

Results group by type — Authors, Series, Books — because at 1,300 books
a query like "le guin" should offer the author, not forty loose rows.
"""
from __future__ import annotations

import pytest

from tests.epub_fixtures import EpubBuilder


@pytest.fixture
def stocked(client, library, gateway):
    for existing in library.glob("*.epub"):
        existing.unlink()
    EpubBuilder(title="The Left Hand of Darkness", author="Ursula K. Le Guin",
                series="Hainish Cycle", series_index=4, first_published="1969",
                subjects=["Science Fiction"]).build(library / "lhod.epub")
    EpubBuilder(title="The Dispossessed", author="Le Guin, Ursula K.",
                series="Hainish Cycle", series_index=6,
                subjects=["Science Fiction"]).build(library / "disp.epub")
    EpubBuilder(title="Tinker Tailor Soldier Spy", author="John le Carré",
                subjects=["Thriller"]).build(library / "ttss.epub")
    EpubBuilder(title="Dune", author="Frank Herbert",
                subjects=["Science Fiction"]).build(library / "dune.epub")
    gateway.reindex()
    return client


def search(client, q):
    return client.get(f"/api/search?q={q}").json()


class TestBookSearch:
    def test_a_title_word_finds_the_book(self, stocked):
        titles = [b["title"] for b in search(stocked, "dispossessed")["books"]]
        assert titles == ["The Dispossessed"]

    def test_search_is_case_insensitive(self, stocked):
        assert search(stocked, "DUNE")["books"][0]["title"] == "Dune"

    def test_a_partial_word_matches_as_a_prefix(self, stocked):
        titles = [b["title"] for b in search(stocked, "dispos")["books"]]
        assert "The Dispossessed" in titles

    def test_two_words_both_have_to_match(self, stocked):
        assert search(stocked, "left darkness")["books"][0]["title"] == \
            "The Left Hand of Darkness"
        assert search(stocked, "left dune")["books"] == []

    def test_an_author_name_finds_their_books(self, stocked):
        titles = {b["title"] for b in search(stocked, "herbert")["books"]}
        assert titles == {"Dune"}

    def test_a_series_name_finds_its_books(self, stocked):
        titles = {b["title"] for b in search(stocked, "hainish")["books"]}
        assert len(titles) == 2

    def test_a_genre_finds_its_books(self, stocked):
        assert len(search(stocked, "thriller")["books"]) == 1


class TestDiacritics:
    def test_an_unaccented_query_finds_an_accented_name(self, stocked):
        # The whole point of remove_diacritics: nobody types "Carré".
        titles = [b["title"] for b in search(stocked, "carre")["books"]]
        assert titles == ["Tinker Tailor Soldier Spy"]

    def test_an_unaccented_query_finds_an_accented_author(self, stocked):
        # Author matching is LIKE, which does not fold accents — but the
        # stored sort_name is already folded, so it matches there.
        names = [a["name"] for a in search(stocked, "carre")["authors"]]
        assert names == ["John le Carré"]

    def test_an_accented_query_works_too(self, stocked):
        assert len(search(stocked, "carr%C3%A9")["books"]) == 1


class TestGrouping:
    def test_matching_authors_are_offered(self, stocked):
        names = [a["name"] for a in search(stocked, "le guin")["authors"]]
        assert names == ["Ursula K. Le Guin"]

    def test_matching_series_are_offered(self, stocked):
        names = [s["name"] for s in search(stocked, "hainish")["series"]]
        assert names == ["Hainish Cycle"]

    def test_an_author_query_returns_the_author_and_their_books(self, stocked):
        result = search(stocked, "le guin")
        assert len(result["authors"]) == 1
        assert len(result["books"]) == 2

    def test_a_query_matching_nothing_is_empty_not_an_error(self, stocked):
        result = search(stocked, "zzzznothing")
        assert result["books"] == []
        assert result["authors"] == []
        assert result["series"] == []


class TestAwkwardQueries:
    def test_an_empty_query_returns_nothing(self, stocked):
        assert search(stocked, "")["books"] == []

    @pytest.mark.parametrize("q", ['"', "AND", "*", "NEAR(", "a OR", "^", "()"])
    def test_fts_syntax_cannot_break_the_query(self, stocked, q):
        # Users type punctuation; FTS5 treats much of it as syntax.
        from urllib.parse import quote
        r = stocked.get(f"/api/search?q={quote(q)}")
        assert r.status_code == 200

    def test_an_apostrophe_is_handled(self, stocked):
        from urllib.parse import quote

        query = quote("hitchhiker's")
        assert stocked.get(f"/api/search?q={query}").status_code == 200

    def test_results_are_capped(self, stocked):
        result = stocked.get("/api/search?q=the&limit=1").json()
        assert len(result["books"]) <= 1


class TestIndexMaintenance:
    def test_a_new_book_becomes_searchable(self, stocked, library, gateway):
        assert search(stocked, "neuromancer")["books"] == []
        EpubBuilder(title="Neuromancer", author="William Gibson").build(
            library / "neuro.epub"
        )
        gateway.reindex()
        assert search(stocked, "neuromancer")["books"][0]["title"] == "Neuromancer"

    def test_a_retitled_book_is_findable_by_its_new_title(
        self, stocked, library, gateway
    ):
        import os, time
        EpubBuilder(title="Renamed Entirely", author="Frank Herbert").build(
            library / "dune.epub"
        )
        os.utime(library / "dune.epub", (time.time() + 5, time.time() + 5))
        gateway.reindex()
        assert search(stocked, "renamed")["books"][0]["title"] == "Renamed Entirely"
        assert search(stocked, "dune")["books"] == []

    def test_books_indexed_before_search_existed_are_backfilled(
        self, stocked, gateway
    ):
        # Adding the FTS table does not re-index anything: on the next
        # start every book is "unchanged", so nothing repopulates it.
        # The real library hit exactly this — search found authors but
        # zero books.
        gateway.database.connection.execute("DELETE FROM book_fts")
        assert search(stocked, "dune")["books"] == []
        gateway.reindex()
        assert search(stocked, "dune")["books"][0]["title"] == "Dune"

    def test_a_missing_book_drops_out_of_results(self, stocked, library, gateway):
        (library / "dune.epub").unlink()
        gateway.reindex()
        assert search(stocked, "dune")["books"] == []
