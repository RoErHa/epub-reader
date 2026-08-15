"""Storing what a provider found.

Enrichment must be safe to re-run across the whole library, which is what
the provenance rule buys: a value the reader set by hand is never
overwritten automatically.
"""
from __future__ import annotations

import pytest

from epub_enrich import AuthorEnricher
from epub_providers import OpenLibraryProvider
from tests.epub_fixtures import EpubBuilder
from tests.test_providers import AUTHOR_DETAILS, AUTHOR_SEARCH, WORKS, FakeFetcher


@pytest.fixture
def library_with_le_guin(client, library, gateway):
    for existing in library.glob("*.epub"):
        existing.unlink()
    EpubBuilder(title="The Left Hand of Darkness",
                author="Ursula K. Le Guin").build(library / "lhod.epub")
    EpubBuilder(title="A Wizard of Earthsea",
                author="Le Guin, Ursula K.").build(library / "wiz.epub")
    gateway.reindex()
    return client


@pytest.fixture
def fetcher():
    return FakeFetcher({
        "search/authors.json": AUTHOR_SEARCH,
        "/authors/OL26320A.json": AUTHOR_DETAILS,
        "/authors/OL26320A/works.json": WORKS,
    })


@pytest.fixture
def enricher(gateway, fetcher):
    return AuthorEnricher(
        gateway.database, OpenLibraryProvider(fetcher), cover_root=None
    )


def author_id(client, name="Ursula K. Le Guin"):
    return next(a["id"] for a in client.get("/api/authors").json()["authors"]
                if a["name"] == name)


class TestEnrichingAnAuthor:
    def test_the_bio_is_stored(self, library_with_le_guin, enricher, gateway):
        # Scoped by id: the shared fixture library leaves other author
        # rows behind, so a bare SELECT picks up the wrong person.
        author = author_id(library_with_le_guin)
        enricher.enrich(author)
        row = gateway.database.connection.execute(
            "SELECT bio, born, died, bio_source, fetched_at FROM author"
            " WHERE id = ?", (author,)
        ).fetchone()
        assert "speculative fiction" in row["bio"]
        assert row["born"] == "21 October 1929"
        assert row["bio_source"] == "openlibrary"
        assert row["fetched_at"] > 0

    def test_the_works_are_stored(self, library_with_le_guin, enricher, gateway):
        enricher.enrich(author_id(library_with_le_guin))
        titles = {r["title"] for r in gateway.database.connection.execute(
            "SELECT title FROM author_work")}
        assert "The Left Hand of Darkness" in titles
        # Everything fetched is kept — filtering happens on display.
        assert "The Wind's Twelve Quarters" in titles

    def test_works_are_linked_to_books_we_own(
        self, library_with_le_guin, enricher, gateway
    ):
        enricher.enrich(author_id(library_with_le_guin))
        row = gateway.database.connection.execute(
            "SELECT book_id FROM author_work WHERE title = ?",
            ("A Wizard of Earthsea",),
        ).fetchone()
        assert row["book_id"] is not None

    def test_works_we_do_not_own_have_no_book(
        self, library_with_le_guin, enricher, gateway
    ):
        enricher.enrich(author_id(library_with_le_guin))
        row = gateway.database.connection.execute(
            "SELECT book_id FROM author_work WHERE title = ?",
            ("The Wind's Twelve Quarters",),
        ).fetchone()
        assert row["book_id"] is None

    def test_a_report_says_what_happened(self, library_with_le_guin, enricher):
        report = enricher.enrich(author_id(library_with_le_guin))
        assert report["matched"] is True
        assert report["works"] == len(WORKS["entries"])

    def test_re_running_does_not_duplicate_works(
        self, library_with_le_guin, enricher, gateway
    ):
        author = author_id(library_with_le_guin)
        enricher.enrich(author)
        enricher.enrich(author)
        count = gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM author_work").fetchone()["n"]
        assert count == len(WORKS["entries"])


class TestWhenItCannotHelp:
    def test_an_unmatched_author_is_recorded_as_attempted(
        self, library_with_le_guin, gateway, fetcher
    ):
        fetcher.responses["search/authors.json"] = {"docs": []}
        enricher = AuthorEnricher(
            gateway.database, OpenLibraryProvider(fetcher), cover_root=None
        )
        author = author_id(library_with_le_guin)
        report = enricher.enrich(author)
        assert report["matched"] is False
        # Marked as looked-at, so the UI does not keep promising a bio.
        row = gateway.database.connection.execute(
            "SELECT fetched_at, bio FROM author WHERE id = ?", (author,)
        ).fetchone()
        assert row["fetched_at"] > 0
        assert row["bio"] is None

    def test_a_provider_failure_does_not_raise(
        self, library_with_le_guin, gateway, fetcher
    ):
        fetcher.responses["search/authors.json"] = OSError("network down")
        enricher = AuthorEnricher(
            gateway.database, OpenLibraryProvider(fetcher), cover_root=None
        )
        report = enricher.enrich(author_id(library_with_le_guin))
        assert report["matched"] is False
        assert "error" in report

    def test_an_unknown_author_is_refused(self, library_with_le_guin, enricher):
        with pytest.raises(LookupError):
            enricher.enrich(999999)


class TestProvenance:
    def test_a_hand_written_bio_is_never_overwritten(
        self, library_with_le_guin, enricher, gateway
    ):
        author = author_id(library_with_le_guin)
        gateway.database.connection.execute(
            "UPDATE author SET bio = ?, bio_source = 'user' WHERE id = ?",
            ("My own words about her.", author),
        )
        enricher.enrich(author)
        row = gateway.database.connection.execute(
            "SELECT bio, bio_source FROM author WHERE id = ?", (author,)
        ).fetchone()
        assert row["bio"] == "My own words about her."
        assert row["bio_source"] == "user"

    def test_a_provider_bio_is_refreshed(
        self, library_with_le_guin, enricher, gateway
    ):
        author = author_id(library_with_le_guin)
        gateway.database.connection.execute(
            "UPDATE author SET bio = 'stale', bio_source = 'openlibrary'"
            " WHERE id = ?", (author,),
        )
        enricher.enrich(author)
        row = gateway.database.connection.execute(
            "SELECT bio FROM author WHERE id = ?", (author,)
        ).fetchone()
        assert "speculative fiction" in row["bio"]
