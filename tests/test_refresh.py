"""Keeping author data from going stale.

A bibliography is not static: living authors publish. But re-asking
Open Library about 395 authors is a bulk sweep, which is exactly what
they ask people not to do, so the refresh is a slow round-robin —
the oldest handful each week, ten at a time.

Oldest-first rather than alphabetical, because surnames are not evenly
spread: this library has 49 authors under S and one under U, so a
letter-per-fortnight would do 49 authors one week and one the next,
while several weeks did nothing at all.
"""
from __future__ import annotations

import time

import pytest

from epub_refresh import AuthorRefresh
from tests.epub_fixtures import EpubBuilder

DAY = 86400


class StubEnricher:
    def __init__(self):
        self.asked: list[int] = []

    def enrich(self, author_id):
        self.asked.append(author_id)
        return {"matched": True, "works": 3}


THREE = ("Ursula K. Le Guin", "Frank Herbert", "Isaac Asimov")


@pytest.fixture
def shelf(client, library, gateway):
    for existing in library.rglob("*.epub"):
        existing.unlink()
    for name in THREE:
        EpubBuilder(title=f"Book by {name}", author=name).build(
            library / f"{name.split()[-1]}.epub")
    gateway.reindex()
    return gateway


@pytest.fixture
def settled(shelf):
    """All three looked up yesterday, so a test can age exactly one and
    know that anything picked up was picked up for that reason."""
    for name in THREE:
        age(shelf, name, 1)
    return shelf


def age(gateway, name, days, works=1, died=None):
    """Put an author's last lookup that many days in the past."""
    db = gateway.database.connection
    author_id = db.execute("SELECT id FROM author WHERE name = ?",
                           (name,)).fetchone()["id"]
    db.execute("UPDATE author SET fetched_at = ?, died = ? WHERE id = ?",
               (int(time.time()) - days * DAY, died, author_id))
    db.execute("DELETE FROM author_work WHERE author_id = ?", (author_id,))
    for i in range(works):
        db.execute("INSERT INTO author_work (author_id, title, work_type,"
                   " source, fetched_at) VALUES (?, ?, 'novel', 'openlibrary',"
                   " 1)", (author_id, f"Work {i} of {name}"))
    return author_id


def refresher(gateway, enricher):
    return AuthorRefresh(gateway.database, enricher)


class TestChoosingWho:
    def test_the_oldest_goes_first(self, shelf, gateway):
        old = age(gateway, "Frank Herbert", 400)
        age(gateway, "Isaac Asimov", 200)
        age(gateway, "Ursula K. Le Guin", 190)
        enricher = StubEnricher()
        refresher(gateway, enricher).run(limit=1)
        assert enricher.asked == [old]

    def test_an_author_never_asked_about_comes_first_of_all(self, shelf,
                                                            gateway):
        age(gateway, "Frank Herbert", 400)
        age(gateway, "Isaac Asimov", 900)
        never = gateway.database.connection.execute(
            "SELECT id FROM author WHERE name = 'Ursula K. Le Guin'"
        ).fetchone()["id"]
        enricher = StubEnricher()
        refresher(gateway, enricher).run(limit=1)
        assert enricher.asked == [never]

    def test_only_so_many_at_a_time(self, shelf, gateway):
        for name in ("Frank Herbert", "Isaac Asimov", "Ursula K. Le Guin"):
            age(gateway, name, 400)
        enricher = StubEnricher()
        report = refresher(gateway, enricher).run(limit=2)
        assert len(enricher.asked) == 2
        assert report["refreshed"] == 2

    def test_a_recent_lookup_is_left_alone(self, shelf, gateway):
        for name in ("Frank Herbert", "Isaac Asimov", "Ursula K. Le Guin"):
            age(gateway, name, 10)
        enricher = StubEnricher()
        assert refresher(gateway, enricher).run(limit=10)["refreshed"] == 0
        assert enricher.asked == []


class TestHowOften:
    """The same weekly budget, spent where books actually appear."""

    def test_a_living_author_comes_round_twice_a_year(self, settled, gateway):
        age(gateway, "Frank Herbert", 200)
        enricher = StubEnricher()
        assert refresher(gateway, enricher).run(limit=10)["refreshed"] == 1

    def test_a_dead_author_waits_far_longer(self, settled, gateway):
        # Nothing new is coming, so a yearly ask is wasted.
        age(gateway, "Frank Herbert", 400, died="1986-02-11")
        enricher = StubEnricher()
        assert refresher(gateway, enricher).run(limit=10)["refreshed"] == 0

    def test_a_dead_author_is_not_forgotten_entirely(self, settled, gateway):
        # New editions and scholarship do appear, just slowly.
        age(gateway, "Frank Herbert", 1200, died="1986-02-11")
        enricher = StubEnricher()
        assert refresher(gateway, enricher).run(limit=10)["refreshed"] == 1

    def test_someone_open_library_never_knew_is_asked_yearly(self, settled,
                                                             gateway):
        # A few hundred of these: series names, scanner handles. Asking
        # every six months is asking twice for the same silence.
        age(gateway, "Frank Herbert", 200, works=0)
        enricher = StubEnricher()
        assert refresher(gateway, enricher).run(limit=10)["refreshed"] == 0

        age(gateway, "Frank Herbert", 400, works=0)
        assert refresher(gateway, StubEnricher()).run(limit=10)["refreshed"] == 1


class TestSayingWhatHappened:
    def test_the_summary_names_the_authors(self, settled, gateway):
        age(gateway, "Frank Herbert", 400)
        report = refresher(gateway, StubEnricher()).run(limit=10)
        assert "Frank Herbert" in AuthorRefresh.summary(report)

    def test_nothing_due_says_so(self, shelf, gateway):
        for name in ("Frank Herbert", "Isaac Asimov", "Ursula K. Le Guin"):
            age(gateway, name, 1)
        report = refresher(gateway, StubEnricher()).run(limit=10)
        assert "nothing due" in AuthorRefresh.summary(report)

    def test_it_says_how_long_the_round_takes(self, shelf, gateway):
        for name in ("Frank Herbert", "Isaac Asimov", "Ursula K. Le Guin"):
            age(gateway, name, 400)
        report = refresher(gateway, StubEnricher()).run(limit=2)
        assert report["due"] == 3
        assert "1 still due" in AuthorRefresh.summary(report)

    def test_a_provider_failure_is_not_fatal(self, settled, gateway):
        class Broken:
            def enrich(self, author_id):
                from epub_providers import ProviderError
                raise ProviderError("down")

        age(gateway, "Frank Herbert", 400)
        report = refresher(gateway, Broken()).run(limit=10)
        assert report["refreshed"] == 0
        assert report["failed"] == 1


class TestTheCommand:
    """`run.sh refresh` is what the weekly launchd job runs."""

    @pytest.fixture(autouse=True)
    def offline(self, monkeypatch):
        import epub_server

        def refuse(*args, **kwargs):
            def fetch(url, headers=None):
                raise OSError(f"the test suite does not use the network ({url})")
            return fetch

        monkeypatch.setattr(epub_server, "PoliteFetcher", refuse)

    @staticmethod
    def env(tmp_path, library):
        state = tmp_path / "state"
        state.mkdir(exist_ok=True)
        path = tmp_path / ".env.refresh"
        path.write_text(
            f"EPUB_PORT=8777\nEPUB_BIND=127.0.0.1\nLIBRARY_ROOT={library}\n"
            f"DB_PATH={state}/books.db\nCOVER_CACHE={state}/covers\n"
            f"LOG_LEVEL=WARNING\nLOG_FILE={state}/test.log\n"
        )
        return path

    def test_it_reports_what_it_did(self, tmp_path, capsys):
        from epub_gateway import GatewayCLI
        library = tmp_path / "books"
        library.mkdir()
        EpubBuilder(title="Dune", author="Frank Herbert").build(library / "a.epub")
        env = self.env(tmp_path, library)
        GatewayCLI.main(["--env", str(env), "--scan"])
        assert GatewayCLI.main(["--env", str(env), "--refresh"]) == 0
        assert "Author data" in capsys.readouterr().out

    def test_an_empty_library_has_nothing_due(self, tmp_path, capsys):
        from epub_gateway import GatewayCLI
        library = tmp_path / "empty"
        library.mkdir()
        env = self.env(tmp_path, library)
        GatewayCLI.main(["--env", str(env), "--scan"])
        GatewayCLI.main(["--env", str(env), "--refresh"])
        assert "nothing due" in capsys.readouterr().out

    def test_the_batch_size_can_be_set(self, tmp_path):
        from epub_gateway import GatewayCLI
        args = GatewayCLI.parse_args(["--refresh", "--max", "3"])
        assert args.max == 3

    def test_the_batch_size_has_a_sensible_default(self):
        from epub_gateway import GatewayCLI
        from epub_refresh import BATCH
        assert GatewayCLI.parse_args(["--refresh"]).max == BATCH
