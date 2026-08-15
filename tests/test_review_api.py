"""The review queue.

Matches in the middle band wait for a human. The screen has to show WHY
a candidate scored what it did — a bare number is not reviewable — and
accepting must obey the same provenance rule as automatic enrichment.
"""
from __future__ import annotations

import json
import time

import pytest

from tests.epub_fixtures import EpubBuilder


@pytest.fixture
def queued(client, library, gateway):
    for existing in library.glob("*.epub"):
        existing.unlink()
    EpubBuilder(title="Dune", author="Frank Herbert").build(library / "dune.epub")
    EpubBuilder(title="Emma", author="Jane Austen").build(library / "emma.epub")
    gateway.reindex()
    db = gateway.database.connection
    for book in client.get("/api/books").json()["books"]:
        db.execute(
            """INSERT INTO metadata_candidate
               (book_id, provider, source_id, payload, parts, score, status,
                fetched_at)
               VALUES (?, 'stub', 's1', ?, ?, 0.72, 'pending', ?)""",
            (book["id"],
             json.dumps({"title": book["title"], "authors": ["Someone"],
                         "first_published": 1999, "synopsis": "From a provider.",
                         "publisher": "A Publisher", "source": "stub"}),
             json.dumps({"title": 1.0, "author": 0.2, "year": None}),
             int(time.time())))
    return client


class TestTheQueue:
    def test_pending_candidates_are_listed(self, queued):
        items = queued.get("/api/review").json()["items"]
        assert len(items) == 2

    def test_each_item_shows_the_book_and_the_candidate(self, queued):
        item = queued.get("/api/review").json()["items"][0]
        assert item["book"]["title"]
        assert item["candidate"]["title"]
        assert item["score"] == pytest.approx(0.72)

    def test_each_item_shows_why_it_scored_that(self, queued):
        # A number without a reason is not reviewable.
        item = queued.get("/api/review").json()["items"][0]
        assert item["parts"]["title"] == 1.0
        assert item["parts"]["author"] == pytest.approx(0.2)

    def test_it_shows_what_would_change(self, queued):
        item = queued.get("/api/review").json()["items"][0]
        changes = {c["field"]: c for c in item["changes"]}
        assert changes["synopsis"]["to"] == "From a provider."
        assert changes["synopsis"]["from"] in (None, "")

    def test_unchanged_fields_are_not_offered_as_changes(self, queued):
        item = queued.get("/api/review").json()["items"][0]
        assert all(c["from"] != c["to"] for c in item["changes"])

    def test_the_queue_is_counted_in_the_status(self, queued):
        assert queued.get("/api/status").json()["pending_review"] == 2


class TestDeciding:
    def test_accepting_writes_the_metadata(self, queued, gateway):
        item = queued.get("/api/review").json()["items"][0]
        assert queued.post(f"/api/review/{item['id']}/accept").status_code == 200
        row = gateway.database.connection.execute(
            "SELECT synopsis FROM metadata WHERE book_id = ?", (item["book"]["id"],)
        ).fetchone()
        assert row["synopsis"] == "From a provider."

    def test_accepting_takes_it_off_the_queue(self, queued):
        item = queued.get("/api/review").json()["items"][0]
        queued.post(f"/api/review/{item['id']}/accept")
        assert len(queued.get("/api/review").json()["items"]) == 1

    def test_rejecting_changes_nothing_about_the_book(self, queued, gateway):
        item = queued.get("/api/review").json()["items"][0]
        queued.post(f"/api/review/{item['id']}/reject")
        row = gateway.database.connection.execute(
            "SELECT synopsis FROM metadata WHERE book_id = ?", (item["book"]["id"],)
        ).fetchone()
        assert row["synopsis"] in (None, "")

    def test_rejecting_takes_it_off_the_queue(self, queued):
        item = queued.get("/api/review").json()["items"][0]
        queued.post(f"/api/review/{item['id']}/reject")
        assert len(queued.get("/api/review").json()["items"]) == 1

    def test_a_decision_on_an_unknown_candidate_is_a_404(self, queued):
        assert queued.post("/api/review/999999/accept").status_code == 404

    def test_accepting_still_obeys_provenance(self, queued, gateway):
        item = queued.get("/api/review").json()["items"][0]
        db = gateway.database.connection
        db.execute("UPDATE metadata SET synopsis = 'Mine' WHERE book_id = ?",
                   (item["book"]["id"],))
        db.execute("INSERT INTO field_provenance (book_id, field, origin,"
                   " updated_at) VALUES (?, 'synopsis', 'user', 1)",
                   (item["book"]["id"],))
        queued.post(f"/api/review/{item['id']}/accept")
        assert db.execute(
            "SELECT synopsis FROM metadata WHERE book_id = ?",
            (item["book"]["id"],)).fetchone()["synopsis"] == "Mine"


class TestBulk:
    def test_everything_can_be_accepted_at_once(self, queued):
        r = queued.post("/api/review/accept-all")
        assert r.status_code == 200
        assert r.json()["accepted"] == 2
        assert queued.get("/api/review").json()["items"] == []
