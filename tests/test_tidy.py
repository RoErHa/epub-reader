"""Dropping a book in and restarting is meant to be the whole workflow.

§6.6 said so, and it was half true: `REPAIR_ON_SCAN` runs the three
passes that reach a provider, and is off by default for exactly that
reason. Everything built since — reading a name into author, series and
title (§8.13a), freeing a co-author from a title (§8.13b), taking the
scanner's notes off (§8.13d) — is **local, free and needs no network**,
and none of it ran on a new arrival at all. A book dropped in yesterday
browsed as `Anthony, Piers - Xanth 12 - The Colour of Her Panties`,
credited to nobody, exactly as the first forty-five did.

> **What costs nothing may be done for you; what costs a request may
> not.** These passes read filenames and write to our own database, so
> they run on every scan unless told otherwise. `fragments` asks Open
> Library, so it stays where `REPAIR_ON_SCAN` is: opt-in.
"""
from __future__ import annotations

import pytest

from epub_config import Config
from tests.conftest import ENV_BODY
from tests.epub_fixtures import EpubBuilder
from tests.pdf_fixtures import PdfBuilder


def arrival(library, name):
    """A new book, as scanners leave them: the filename knows and the
    file says nothing."""
    PdfBuilder(title=None, author=None, lines=[name]).build(
        library / f"{name}.pdf")


def shelf(gateway, like):
    return gateway.database.connection.execute(
        """SELECT m.title, m.file_title, s.name AS series, m.series_index,
                  (SELECT GROUP_CONCAT(a.name, ' & ' ORDER BY ba.position)
                     FROM book_author ba JOIN author a ON a.id = ba.author_id
                    WHERE ba.book_id = b.id) AS authors
             FROM book b JOIN metadata m ON m.book_id = b.id
             LEFT JOIN series s ON s.id = m.series_id
            WHERE b.path LIKE ?""", (f"%{like}%",)).fetchone()


class TestTheSetting:
    def test_it_is_on_unless_turned_off(self, tmp_path, library):
        """The reason `REPAIR_ON_SCAN` is off does not apply: nothing
        here reaches a provider."""
        env = tmp_path / ".env"
        env.write_text(ENV_BODY.format(library=library))
        assert Config.from_file(env).tidy_on_scan is True

    @pytest.mark.parametrize("value,expected", [
        ("false", False), ("no", False), ("0", False), ("off", False),
        ("true", True), ("yes", True), ("", True),
    ])
    def test_it_can_be_turned_off(self, tmp_path, library, value, expected):
        env = tmp_path / ".env"
        env.write_text(ENV_BODY.format(library=library)
                       + f"TIDY_ON_SCAN={value}\n")
        assert Config.from_file(env).tidy_on_scan is expected


class TestANewArrival:
    def test_its_name_is_read_into_three_facts(self, client, library, gateway):
        for old in library.rglob("*"):
            if old.is_file():
                old.unlink()
        arrival(library, "Anthony, Piers - Xanth 08 - Crewel Lye")
        arrival(library, "Anthony, Piers - Xanth 09 - Golem in the Gears")
        gateway.reindex()
        gateway.tidy()

        row = shelf(gateway, "Crewel Lye")
        assert row["authors"] == "Piers Anthony"
        assert row["series"] == "Xanth"
        assert row["series_index"] == 8
        assert row["title"] == "Crewel Lye"

    def test_a_co_author_is_freed_from_the_title(self, client, library,
                                                 gateway):
        """And it must happen *after* crediting, or there is no first
        author for the second one to join (§8.13b)."""
        for old in library.rglob("*"):
            if old.is_file():
                old.unlink()
        arrival(library, "Anthony, Piers - Dead Morn (with Roberto Fuentes)")
        gateway.reindex()
        gateway.tidy()
        row = shelf(gateway, "Dead Morn")
        assert row["title"] == "Dead Morn"
        assert row["authors"] == "Piers Anthony & Roberto Fuentes"

    def test_a_scanner_note_is_taken_off(self, client, library, gateway):
        for old in library.rglob("*"):
            if old.is_file():
                old.unlink()
        EpubBuilder(title="Blue Moon (v2.1)", author="Ann Author").build(
            library / "bm.epub")
        gateway.reindex()
        gateway.tidy()
        assert shelf(gateway, "bm")["title"] == "Blue Moon"

    def test_the_file_can_still_be_undone(self, client, library, gateway):
        """Automatic is not irreversible: what the file said is kept."""
        for old in library.rglob("*"):
            if old.is_file():
                old.unlink()
        arrival(library, "Anthony, Piers - Alien Plot")
        gateway.reindex()
        gateway.tidy()
        row = shelf(gateway, "Alien Plot")
        assert row["title"] == "Alien Plot"
        assert row["file_title"] == "Anthony, Piers - Alien Plot"

    def test_running_it_twice_changes_nothing_with_a_co_author(
            self, client, library, gateway):
        """The passes must settle, not take turns.

        `names` derives a title from `file_title` — the file's own words,
        bracket and all — and `coauthors` takes the bracket off. So each
        restart put "(with Roberto Fuentes)" back and took it off again:
        thirteen books retitled and thirteen co-authors "freed" on a
        scan where nothing had changed. Both passes read the same source
        and each writes only the facts it owns.
        """
        for old in library.rglob("*"):
            if old.is_file():
                old.unlink()
        arrival(library, "Anthony, Piers - Dead Morn (with Roberto Fuentes)")
        gateway.reindex()
        gateway.tidy()
        first = shelf(gateway, "Dead Morn")
        second = gateway.tidy()
        assert second == {"credited": 0, "filed": 0, "retitled": 0,
                          "freed": 0, "cleaned": 0, "languages": 0}
        assert shelf(gateway, "Dead Morn")["title"] == first["title"]
        assert shelf(gateway, "Dead Morn")["authors"] == first["authors"]

    def test_running_it_twice_changes_nothing(self, client, library, gateway):
        for old in library.rglob("*"):
            if old.is_file():
                old.unlink()
        arrival(library, "Anthony, Piers - Alien Plot")
        gateway.reindex()
        gateway.tidy()
        second = gateway.tidy()
        assert second["credited"] == 0
        assert second["retitled"] == 0
        assert second["cleaned"] == 0

    def test_nothing_here_reaches_a_provider(self, client, library, gateway):
        """`fragments` is the one pass that asks Open Library, and it
        stays opt-in — the whole point of this being automatic is that it
        costs nobody anything. The gateway's fetcher refuses in tests, so
        a call would raise rather than pass."""
        for old in library.rglob("*"):
            if old.is_file():
                old.unlink()
        arrival(library, "Anthony, Piers - The Caterpillars Questi")
        gateway.reindex()
        gateway.tidy()          # must not raise
        assert shelf(gateway, "Caterpillars")["title"] == \
            "The Caterpillars Questi"


class TestWhatTheProviderSettled:
    """A title Open Library confirmed must outrank one read off a
    filename.

    `The Caterpillars Questi` is truncated on disk, and §8.13c asked a
    provider and got *The Caterpillar's Question*. The next scan read the
    filename again and put the fragment back — then `coauthors` saw the
    bracket it had just restored and rewrote the title a second time.

    Within `provider`, a **confirmed** answer beats a **derived** one:
    `source = 'openlibrary'` is something somebody's catalogue asserts,
    `source = 'filename'` is something we inferred from a name.
    """

    def _repaired(self, gateway, library):
        for old in library.rglob("*"):
            if old.is_file():
                old.unlink()
        arrival(library, "Anthony, Piers - The Caterpillars Questi "
                         "(with Philip Jose Farmer)")
        gateway.reindex()
        gateway.tidy()          # credits Piers Anthony, frees the co-author
        book_id = gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title LIKE '%Caterpillar%'"
        ).fetchone()["book_id"]
        # As `fragments` would leave it.
        gateway.database.connection.execute(
            "UPDATE metadata SET title = ? WHERE book_id = ?",
            ("The Caterpillar's Question", book_id))
        gateway.database.connection.execute(
            "INSERT INTO field_provenance (book_id, field, origin, source,"
            " updated_at) VALUES (?, 'title', 'provider', 'openlibrary', 1)"
            " ON CONFLICT(book_id, field) DO UPDATE SET origin='provider',"
            " source='openlibrary'", (book_id,))
        return book_id

    def test_a_confirmed_title_survives_the_next_scan(self, client, library,
                                                      gateway):
        self._repaired(gateway, library)
        gateway.tidy()
        assert shelf(gateway, "Caterpillar")["title"] == \
            "The Caterpillar's Question"

    def test_it_survives_being_scanned_again_and_again(self, client, library,
                                                       gateway):
        self._repaired(gateway, library)
        for _ in range(3):
            gateway.tidy()
        assert shelf(gateway, "Caterpillar")["title"] == \
            "The Caterpillar's Question"

    def test_the_authors_are_still_kept(self, client, library, gateway):
        """Only the title is settled. Who wrote it is a different fact,
        and the pass that credits people goes on doing so."""
        self._repaired(gateway, library)
        gateway.tidy()
        assert "Piers Anthony" in shelf(gateway, "Caterpillar")["authors"]
