"""Keeping the library tidy as it grows.

Books arrive with the same defects the first 1,300 had: a name spelled
three ways, a book filed under its own title, a filename where a title
should be. `run.sh restart` scans for new books, so it is also the
natural moment to check them — but only when it has been asked to, and
never without the passes being able to say what they would do first.
"""
from __future__ import annotations

import pytest

from epub_maintenance import LibraryRepair
from tests.epub_fixtures import EpubBuilder


class StubResolver:
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.asked: list[str] = []

    def find_author(self, name):
        self.asked.append(name)
        found = self.answers.get(name)
        return {"key": found[0], "name": found[1]} if found else None


class StubEnricher:
    def __init__(self):
        self.asked: list[int] = []

    def enrich(self, author_id):
        self.asked.append(author_id)
        return {"matched": False, "works": 0}


@pytest.fixture
def messy(client, library, gateway):
    for existing in library.rglob("*.epub"):
        existing.unlink()
    # A name spelled two ways...
    EpubBuilder(title="Kesrith", author="C. J. Cherryh").build(library / "a.epub")
    EpubBuilder(title="Shonjir", author="C.J. Cherryh").build(library / "b.epub")
    # ...and a book filed under its own title.
    EpubBuilder(title="Annihilation", author="Annihilation").build(
        library / "c.epub")
    gateway.reindex()
    return client


def repairer(gateway, resolver=None, enricher=None):
    return LibraryRepair(gateway.database, resolver or StubResolver(),
                         [], enricher or StubEnricher())


def authors(gateway):
    return {r["name"] for r in gateway.database.connection.execute(
        "SELECT a.name FROM author a JOIN book_author ba ON ba.author_id = a.id")}


class TestRunningThePasses:
    def test_a_dry_run_changes_nothing(self, messy, gateway):
        before = authors(gateway)
        report = repairer(gateway).run(apply=False)
        assert authors(gateway) == before
        assert report["merged"] == 0

    def test_a_dry_run_still_says_what_it_would_do(self, messy, gateway):
        report = repairer(gateway).run(apply=False)
        assert report["would_merge"] == 1

    def test_applying_merges_the_duplicate(self, messy, gateway):
        repairer(gateway).run(apply=True)
        assert "C.J. Cherryh" not in authors(gateway)

    def test_the_passes_run_in_order(self, messy, gateway):
        # Authors are settled before titles are looked up, because a
        # title is matched against its author's bibliography.
        report = repairer(gateway).run(apply=True)
        assert list(report["passes"]) == ["authors", "books", "titles"]

    def test_the_summary_reads_as_a_sentence(self, messy, gateway):
        report = repairer(gateway).run(apply=False)
        summary = LibraryRepair.summary(report)
        assert "would merge 1 author" in summary
        assert "nothing to do" not in summary

    def test_a_tidy_library_says_so(self, client, library, gateway):
        for existing in library.rglob("*.epub"):
            existing.unlink()
        EpubBuilder(title="Dune", author="Frank Herbert").build(library / "a.epub")
        gateway.reindex()
        report = repairer(gateway).run(apply=False)
        assert "nothing to do" in LibraryRepair.summary(report)


class TestNotAskingTwice:
    """Ask Open Library once, keep the answer, and read our own database
    ever after. A restart must not re-ask about every author, or a
    library this size makes 1,200 requests every time it comes up.
    """

    def test_the_works_are_kept_in_our_own_database(self, messy, gateway):
        db = gateway.database.connection
        author_id = db.execute(
            "SELECT id FROM author WHERE name = 'C. J. Cherryh'").fetchone()["id"]
        db.execute("INSERT INTO author_work (author_id, title, work_type,"
                   " source, fetched_at) VALUES (?, 'Kesrith', 'novel',"
                   " 'openlibrary', 1)", (author_id,))
        db.execute("UPDATE author SET fetched_at = 1 WHERE id = ?", (author_id,))

        enricher = StubEnricher()
        repairer(gateway, enricher=enricher).run(apply=True)
        assert author_id not in enricher.asked
        assert db.execute(
            "SELECT COUNT(*) n FROM author_work WHERE author_id = ?",
            (author_id,)).fetchone()["n"] == 1

    def test_a_whole_second_run_asks_nothing(self, messy, gateway):
        # Everything already looked up once.
        gateway.database.connection.execute(
            "UPDATE author SET fetched_at = 1 WHERE fetched_at IS NULL")
        enricher = StubEnricher()
        repairer(gateway, enricher=enricher).run(apply=True)
        assert enricher.asked == []

    def test_an_author_already_looked_up_is_not_asked_again(self, messy,
                                                            gateway):
        enricher = StubEnricher()
        repairer(gateway, enricher=enricher).run(apply=True)
        first = len(enricher.asked)
        assert first > 0

        # Enrichment records the attempt even when it finds nothing.
        gateway.database.connection.execute(
            "UPDATE author SET fetched_at = 1 WHERE fetched_at IS NULL")
        again = StubEnricher()
        repairer(gateway, enricher=again).run(apply=True)
        assert again.asked == [], "the provider was asked all over again"

    def test_a_new_author_is_still_asked_about(self, messy, gateway, library):
        gateway.database.connection.execute(
            "UPDATE author SET fetched_at = 1 WHERE fetched_at IS NULL")
        EpubBuilder(title="Neuromancer", author="William Gibson").build(
            library / "new.epub")
        gateway.reindex()
        enricher = StubEnricher()
        repairer(gateway, enricher=enricher).run(apply=True)
        assert len(enricher.asked) == 1


class TestTheCommand:
    """`run.sh repair` is how this is reached from a terminal, and
    `--apply` is what separates looking from doing."""

    @pytest.fixture(autouse=True)
    def offline(self, monkeypatch):
        """No test talks to a provider. These build a whole gateway, so
        the refusal has to be installed where the gateway looks for it."""
        import epub_server

        def refuse(*args, **kwargs):
            def fetch(url, headers=None):
                raise OSError(f"the test suite does not use the network ({url})")
            return fetch

        monkeypatch.setattr(epub_server, "PoliteFetcher", refuse)

    @staticmethod
    def env(tmp_path, library, extra=""):
        state = tmp_path / "state"
        state.mkdir(exist_ok=True)
        path = tmp_path / ".env.repair"
        path.write_text(
            f"EPUB_PORT=8778\nEPUB_BIND=127.0.0.1\nLIBRARY_ROOT={library}\n"
            f"DB_PATH={state}/books.db\nCOVER_CACHE={state}/covers\n"
            f"LOG_LEVEL=WARNING\nLOG_FILE={state}/test.log\n{extra}"
        )
        return path

    def test_repair_reports_without_changing_anything(self, tmp_path, capsys):
        from epub_gateway import GatewayCLI
        library = tmp_path / "books"
        library.mkdir()
        EpubBuilder(title="Kesrith", author="C. J. Cherryh").build(
            library / "a.epub")
        EpubBuilder(title="Shonjir", author="C.J. Cherryh").build(
            library / "b.epub")
        env = self.env(tmp_path, library)

        assert GatewayCLI.main(["--env", str(env), "--scan"]) == 0
        assert GatewayCLI.main(["--env", str(env), "--repair"]) == 0
        out = capsys.readouterr().out
        assert "would merge 1 author" in out

    def test_apply_does_the_work(self, tmp_path, capsys):
        from epub_gateway import GatewayCLI
        library = tmp_path / "books"
        library.mkdir()
        EpubBuilder(title="Kesrith", author="C. J. Cherryh").build(
            library / "a.epub")
        EpubBuilder(title="Shonjir", author="C.J. Cherryh").build(
            library / "b.epub")
        env = self.env(tmp_path, library)

        GatewayCLI.main(["--env", str(env), "--scan"])
        assert GatewayCLI.main(["--env", str(env), "--repair", "--apply"]) == 0
        assert "merged 1 author" in capsys.readouterr().out

    def test_the_scan_says_nothing_about_repair_by_default(self, tmp_path,
                                                           capsys):
        # Reaching a provider on every restart is not something to do
        # without being asked.
        from epub_gateway import GatewayCLI
        library = tmp_path / "books"
        library.mkdir()
        EpubBuilder(title="Kesrith", author="C. J. Cherryh").build(
            library / "a.epub")
        env = self.env(tmp_path, library)
        GatewayCLI.main(["--env", str(env), "--scan"])
        assert "Library metadata" not in capsys.readouterr().out

    def test_a_scan_repairs_when_the_env_asks_it_to(self, tmp_path, capsys):
        from epub_gateway import GatewayCLI
        library = tmp_path / "books"
        library.mkdir()
        EpubBuilder(title="Kesrith", author="C. J. Cherryh").build(
            library / "a.epub")
        EpubBuilder(title="Shonjir", author="C.J. Cherryh").build(
            library / "b.epub")
        env = self.env(tmp_path, library, extra="REPAIR_ON_SCAN=true\n")
        GatewayCLI.main(["--env", str(env), "--scan"])
        assert "Library metadata" in capsys.readouterr().out


class TestTheSetting:
    def test_it_is_off_unless_asked_for(self, tmp_path):
        from epub_config import Config
        env = TestTheCommand.env(tmp_path, tmp_path)
        assert Config.from_file(env).repair_on_scan is False

    def test_true_turns_it_on(self, tmp_path):
        from epub_config import Config
        env = TestTheCommand.env(tmp_path, tmp_path, extra="REPAIR_ON_SCAN=true\n")
        assert Config.from_file(env).repair_on_scan is True

    def test_it_is_not_fooled_by_the_word_false(self, tmp_path):
        from epub_config import Config
        env = TestTheCommand.env(tmp_path, tmp_path, extra="REPAIR_ON_SCAN=false\n")
        assert Config.from_file(env).repair_on_scan is False


class TestThePace:
    """Open Library publishes its limits: 1 request/second anonymously,
    3/second for a client that identifies itself with an application
    name and a contact address. We identify ourselves, and still leave
    headroom — their APIs are explicitly not a bulk backend.
    """

    def test_the_default_leaves_headroom(self, tmp_path):
        from epub_config import Config
        env = TestTheCommand.env(tmp_path, tmp_path)
        assert Config.from_file(env).provider_max_rps == 2.0

    def test_the_pace_can_be_set(self, tmp_path):
        from epub_config import Config
        env = TestTheCommand.env(tmp_path, tmp_path,
                                 extra="PROVIDER_MAX_RPS=1\n")
        assert Config.from_file(env).provider_max_rps == 1.0

    def test_it_never_exceeds_what_they_allow_identified(self, tmp_path):
        # Three a second is their ceiling, not a target.
        from epub_config import Config
        env = TestTheCommand.env(tmp_path, tmp_path,
                                 extra="PROVIDER_MAX_RPS=50\n")
        assert Config.from_file(env).provider_max_rps == 3.0

    def test_nonsense_falls_back_to_the_default(self, tmp_path):
        from epub_config import Config
        env = TestTheCommand.env(tmp_path, tmp_path,
                                 extra="PROVIDER_MAX_RPS=banana\n")
        assert Config.from_file(env).provider_max_rps == 2.0

    def test_the_fetcher_is_built_at_that_pace(self, tmp_path):
        from epub_providers import PoliteFetcher
        fetcher = PoliteFetcher(rps=2.0)
        assert fetcher.interval == 0.5
