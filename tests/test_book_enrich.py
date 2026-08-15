"""Enriching a book: apply, queue, or leave alone.

The provenance rule is what makes this safe to run across a whole
library: **enrichment may overwrite a field only if its origin is the
file and it is unlocked. A value you set by hand is never overwritten
automatically.**
"""
from __future__ import annotations

import pytest

from epub_book_enrich import BookEnricher
from epub_match import Candidate
from tests.epub_fixtures import EpubBuilder


class StubProvider:
    """Returns whatever the test says, and records what it was asked."""

    name = "stub"

    def __init__(self, candidates=None, error=None):
        self.candidates = candidates or []
        self.error = error
        self.queries: list[tuple] = []

    def find_books(self, title, author=None, limit=8):
        self.queries.append((title, author))
        if self.error:
            raise self.error
        return self.candidates


@pytest.fixture
def stocked(client, library, gateway):
    for existing in library.glob("*.epub"):
        existing.unlink()
    EpubBuilder(title="Dune", author="Frank Herbert",
                first_published="1965").build(library / "dune.epub")
    gateway.reindex()
    return client


def book_id(client, title="Dune"):
    return next(b["id"] for b in client.get("/api/books").json()["books"]
                if b["title"] == title)


PERFECT = Candidate(
    title="Dune", authors=["Frank Herbert"], first_published=1965,
    language="en", isbn13="9780441013593", synopsis="A desert planet.",
    publisher="Chilton", subjects=["Science Fiction"], source="stub",
    source_id="s1",
)
MIDDLING = Candidate(title="Dune", authors=["Frank Herbert"], source="stub",
                     source_id="s2")
WRONG = Candidate(title="Pride and Prejudice", authors=["Jane Austen"],
                  source="stub", source_id="s3")


def enricher(gateway, candidates, error=None):
    return BookEnricher(gateway.database, [StubProvider(candidates, error)])


class TestApplying:
    def test_a_confident_match_is_applied(self, stocked, gateway):
        book = book_id(stocked)
        report = enricher(gateway, [PERFECT]).enrich(book)
        assert report["verdict"] == "apply"
        row = gateway.database.connection.execute(
            "SELECT synopsis, publisher, isbn13 FROM metadata WHERE book_id = ?",
            (book,)).fetchone()
        assert row["synopsis"] == "A desert planet."
        assert row["publisher"] == "Chilton"
        assert row["isbn13"] == "9780441013593"

    def test_applying_records_where_it_came_from(self, stocked, gateway):
        book = book_id(stocked)
        enricher(gateway, [PERFECT]).enrich(book)
        rows = {r["field"]: r for r in gateway.database.connection.execute(
            "SELECT field, origin, source FROM field_provenance WHERE book_id = ?",
            (book,))}
        assert rows["synopsis"]["origin"] == "provider"
        assert rows["synopsis"]["source"] == "stub"

    def test_the_search_uses_a_cleaned_query(self, stocked, gateway, library):
        provider = StubProvider([PERFECT])
        EpubBuilder(title="Herbert, Frank - Dune 01 - Dune",
                    author="Frank Herbert").build(library / "messy.epub")
        gateway.reindex()
        messy = book_id(stocked, "Herbert, Frank - Dune 01 - Dune")
        BookEnricher(gateway.database, [provider]).enrich(messy)
        assert provider.queries[0][0] == "dune"


class TestQueueing:
    def test_a_middling_match_is_queued_not_applied(self, stocked, gateway):
        book = book_id(stocked)
        before = gateway.database.connection.execute(
            "SELECT synopsis FROM metadata WHERE book_id = ?", (book,)
        ).fetchone()["synopsis"]
        report = enricher(gateway, [MIDDLING]).enrich(book)
        assert report["verdict"] == "review"
        after = gateway.database.connection.execute(
            "SELECT synopsis FROM metadata WHERE book_id = ?", (book,)
        ).fetchone()["synopsis"]
        assert after == before          # nothing written yet

    def test_a_queued_candidate_is_stored_for_review(self, stocked, gateway):
        book = book_id(stocked)
        enricher(gateway, [MIDDLING]).enrich(book)
        row = gateway.database.connection.execute(
            "SELECT status, score, provider FROM metadata_candidate"
            " WHERE book_id = ?", (book,)).fetchone()
        assert row["status"] == "pending"
        assert 0.6 <= row["score"] < 0.9

    def test_a_poor_match_is_neither_applied_nor_queued(self, stocked, gateway):
        book = book_id(stocked)
        report = enricher(gateway, [WRONG]).enrich(book)
        assert report["verdict"] == "reject"
        assert gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM metadata_candidate WHERE book_id = ?"
            " AND status = 'pending'", (book,)).fetchone()["n"] == 0

    def test_re_running_does_not_pile_up_candidates(self, stocked, gateway):
        book = book_id(stocked)
        e = enricher(gateway, [MIDDLING])
        e.enrich(book)
        e.enrich(book)
        assert gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM metadata_candidate WHERE book_id = ?",
            (book,)).fetchone()["n"] == 1


class TestProvenance:
    def test_a_field_you_set_by_hand_is_never_overwritten(self, stocked, gateway):
        book = book_id(stocked)
        db = gateway.database.connection
        db.execute("UPDATE metadata SET synopsis = ? WHERE book_id = ?",
                   ("My own words.", book))
        db.execute(
            "INSERT INTO field_provenance (book_id, field, origin, updated_at)"
            " VALUES (?, 'synopsis', 'user', 1)", (book,))
        enricher(gateway, [PERFECT]).enrich(book)
        assert db.execute(
            "SELECT synopsis FROM metadata WHERE book_id = ?", (book,)
        ).fetchone()["synopsis"] == "My own words."

    def test_a_locked_field_is_never_overwritten(self, stocked, gateway):
        book = book_id(stocked)
        db = gateway.database.connection
        db.execute("UPDATE metadata SET publisher = 'Mine' WHERE book_id = ?",
                   (book,))
        db.execute(
            "INSERT INTO field_provenance (book_id, field, origin, locked,"
            " updated_at) VALUES (?, 'publisher', 'file', 1, 1)", (book,))
        enricher(gateway, [PERFECT]).enrich(book)
        assert db.execute(
            "SELECT publisher FROM metadata WHERE book_id = ?", (book,)
        ).fetchone()["publisher"] == "Mine"

    def test_a_field_from_the_file_is_refreshed(self, stocked, gateway):
        book = book_id(stocked)
        enricher(gateway, [PERFECT]).enrich(book)
        assert gateway.database.connection.execute(
            "SELECT publisher FROM metadata WHERE book_id = ?", (book,)
        ).fetchone()["publisher"] == "Chilton"

    def test_the_title_from_the_file_is_left_alone_by_default(
        self, stocked, gateway
    ):
        # The file's title is what you see in your own folders; changing
        # it silently would be disorienting.
        book = book_id(stocked)
        enricher(gateway, [PERFECT]).enrich(book)
        assert gateway.database.connection.execute(
            "SELECT title FROM metadata WHERE book_id = ?", (book,)
        ).fetchone()["title"] == "Dune"


class TestAcceptingAReview:
    def test_accepting_writes_the_candidate(self, stocked, gateway):
        book = book_id(stocked)
        e = enricher(gateway, [MIDDLING])
        e.enrich(book)
        candidate_id = gateway.database.connection.execute(
            "SELECT id FROM metadata_candidate WHERE book_id = ?", (book,)
        ).fetchone()["id"]
        e.accept(candidate_id)
        row = gateway.database.connection.execute(
            "SELECT status FROM metadata_candidate WHERE id = ?", (candidate_id,)
        ).fetchone()
        assert row["status"] == "accepted"

    def test_rejecting_leaves_the_book_alone(self, stocked, gateway):
        book = book_id(stocked)
        e = enricher(gateway, [MIDDLING])
        e.enrich(book)
        candidate_id = gateway.database.connection.execute(
            "SELECT id FROM metadata_candidate WHERE book_id = ?", (book,)
        ).fetchone()["id"]
        e.reject(candidate_id)
        assert gateway.database.connection.execute(
            "SELECT status FROM metadata_candidate WHERE id = ?", (candidate_id,)
        ).fetchone()["status"] == "rejected"

    def test_an_unknown_candidate_is_refused(self, stocked, gateway):
        with pytest.raises(LookupError):
            enricher(gateway, []).accept(999999)


class TestWhenItCannotHelp:
    def test_no_candidates_is_recorded_not_raised(self, stocked, gateway):
        report = enricher(gateway, []).enrich(book_id(stocked))
        assert report["verdict"] == "none"

    def test_a_provider_failure_does_not_stop_the_run(self, stocked, gateway):
        from epub_providers import ProviderError
        report = enricher(gateway, [], ProviderError("down")).enrich(
            book_id(stocked))
        assert report["verdict"] == "none"
        assert "error" in report

    def test_a_second_provider_still_gets_a_turn(self, stocked, gateway):
        from epub_providers import ProviderError
        broken = StubProvider([], ProviderError("down"))
        working = StubProvider([PERFECT])
        report = BookEnricher(gateway.database, [broken, working]).enrich(
            book_id(stocked))
        assert report["verdict"] == "apply"


class TestAProviderThatIsRateLimited:
    """Google Books answers 429 to every unauthenticated request. Asking
    it 25 more times in the same run is pointless noise."""

    class Limited:
        name = "limited"

        def __init__(self):
            self.calls = 0

        def find_books(self, title, author=None, limit=8):
            from epub_providers import ProviderError
            self.calls += 1
            raise ProviderError("HTTP Error 429: Too Many Requests")

    def test_it_is_dropped_for_the_rest_of_the_run(self, stocked, gateway,
                                                   library):
        for name in ("a", "b", "c"):
            EpubBuilder(title=f"Book {name}").build(library / f"{name}.epub")
        gateway.reindex()
        limited = self.Limited()
        e = BookEnricher(gateway.database, [limited, StubProvider([PERFECT])])
        for book in [r["id"] for r in gateway.database.connection.execute(
                "SELECT id FROM book WHERE missing_since IS NULL")]:
            e.enrich(book)
        assert limited.calls == 1, "a rate-limited provider was asked again"

    def test_the_other_providers_still_work(self, stocked, gateway):
        limited = self.Limited()
        e = BookEnricher(gateway.database, [limited, StubProvider([PERFECT])])
        assert e.enrich(book_id(stocked))["verdict"] == "apply"
