"""Series, read from the filenames.

Only 135 books of 2,323 have a series, and every one came from a
`calibre:series` tag inside the file. §6 promised series would come from
providers; that was never built, so most of the library has no thread
between its books at all.

The filenames do know, though — 597 of them carry the shape:

    Cussler, Clive - Dirk Pitt 01 - Pacific Vortex
    Dune Chronicles 1 - Hunters of Dune
    Forgotten Realms - Empires 02 - Dragonwall

and reading them needs no provider. The danger is reading them too
eagerly: "10 is Better Than 01 - Heather Jarman" is a book called *10 is
Better Than 01*, not book 1 of a series called "is Better Than".
"""
from __future__ import annotations

import pytest

from epub_series import SeriesFromNames
from tests.epub_fixtures import EpubBuilder


@pytest.fixture
def namer(gateway):
    return SeriesFromNames(gateway.database)


def clear(library):
    for old in library.rglob("*"):
        if old.is_file():
            old.unlink()


def book(library, name, title, author="Clive Cussler"):
    EpubBuilder(title=title, author=author).build(library / f"{name}.epub")


def series_of(gateway, title):
    row = gateway.database.connection.execute(
        """SELECT s.name, m.series_index FROM metadata m
             LEFT JOIN series s ON s.id = m.series_id
            WHERE m.title = ?""", (title,)).fetchone()
    return (row["name"], row["series_index"]) if row else (None, None)


class TestReadingTheName:
    def test_an_author_series_number_title_filename(self, client, library,
                                                    gateway, namer):
        clear(library)
        book(library, "Cussler, Clive - Dirk Pitt 01 - Pacific Vortex",
             "Pacific Vortex")
        book(library, "Cussler, Clive - Dirk Pitt 02 - The Mediterranean Caper",
             "The Mediterranean Caper")
        gateway.reindex()
        namer.run(apply=True)
        assert series_of(gateway, "Pacific Vortex") == ("Dirk Pitt", 1)
        assert series_of(gateway, "The Mediterranean Caper") == ("Dirk Pitt", 2)

    def test_a_series_number_title_filename(self, client, library, gateway,
                                            namer):
        clear(library)
        book(library, "Dune Chronicles 1 - Hunters of Dune", "Hunters of Dune",
             "Brian Herbert")
        book(library, "Dune Chronicles 2 - The Sandworms of Dune",
             "The Sandworms of Dune", "Brian Herbert")
        gateway.reindex()
        namer.run(apply=True)
        assert series_of(gateway, "Hunters of Dune") == ("Dune Chronicles", 1)

    def test_a_half_number_is_kept(self, client, library, gateway, namer):
        # Novellas are book 2.5, which is why series_index is REAL.
        clear(library)
        book(library, "Great Schools of Dune 2 - Mentats of Dune",
             "Mentats of Dune", "Brian Herbert")
        book(library, "Great Schools of Dune 2.5 - Red Plague", "Red Plague",
             "Brian Herbert")
        gateway.reindex()
        namer.run(apply=True)
        assert series_of(gateway, "Red Plague") == ("Great Schools of Dune", 2.5)

    def test_padding_is_not_part_of_the_number(self, client, library, gateway,
                                               namer):
        clear(library)
        book(library, "Wheel of Time 01 - The Eye of the World",
             "The Eye of the World", "Robert Jordan")
        book(library, "Wheel of Time 02 - The Great Hunt", "The Great Hunt",
             "Robert Jordan")
        gateway.reindex()
        namer.run(apply=True)
        assert series_of(gateway, "The Great Hunt") == ("Wheel of Time", 2)


class TestNotReadingTooMuchIntoIt:
    def test_one_book_is_not_a_series(self, client, library, gateway, namer):
        # "10 is Better Than 01" is a book, not book 1 of "is Better
        # Than". A series nobody else belongs to is usually a misread.
        clear(library)
        book(library, "10 is Better Than 01 - Heather Jarman",
             "10 is Better Than 01", "Heather Jarman")
        gateway.reindex()
        assert namer.run(apply=True)["named"] == 0
        assert series_of(gateway, "10 is Better Than 01") == (None, None)

    def test_two_books_agreeing_is_enough(self, client, library, gateway, namer):
        clear(library)
        book(library, "Xanth 11 - Heaven Cent", "Heaven Cent", "Piers Anthony")
        book(library, "Xanth 12 - Man From Mundania", "Man From Mundania",
             "Piers Anthony")
        gateway.reindex()
        assert namer.run(apply=True)["named"] == 2

    def test_two_authors_do_not_share_a_series_by_accident(self, client, library,
                                                           gateway, namer):
        # Agreement has to be within one author's shelf.
        clear(library)
        book(library, "Book 01 - Something", "Something", "Ann Author")
        book(library, "Book 02 - Other", "Other", "Bill Writer")
        gateway.reindex()
        assert namer.run(apply=True)["named"] == 0

    def test_the_authors_own_name_is_not_a_series(self, client, library,
                                                  gateway, namer):
        clear(library)
        book(library, "Clive Cussler 01 - Pacific Vortex", "Pacific Vortex")
        book(library, "Clive Cussler 02 - Deep Six", "Deep Six")
        gateway.reindex()
        assert namer.run(apply=True)["named"] == 0

    def test_a_year_is_not_a_series_number(self, client, library, gateway,
                                           namer):
        clear(library)
        book(library, "Asimov, Isaac - Foundation 1951 - Foundation",
             "Foundation", "Isaac Asimov")
        book(library, "Asimov, Isaac - Foundation 1952 - Foundation and Empire",
             "Foundation and Empire", "Isaac Asimov")
        gateway.reindex()
        assert namer.run(apply=True)["named"] == 0

    def test_a_series_of_one_name_only(self, client, library, gateway, namer):
        # A single letter or digit is not the name of anything.
        clear(library)
        book(library, "A 01 - Something", "Something", "Ann Author")
        book(library, "A 02 - Other", "Other", "Ann Author")
        gateway.reindex()
        assert namer.run(apply=True)["named"] == 0


class TestWhatIsAlreadyKnown:
    def test_a_book_already_filed_is_corroboration(self, client, library,
                                                   gateway, namer):
        """A sibling that is already on the shelf agrees just as well as
        one that is not.

        Found in the real library: Piers Anthony's Geodyssey 02 and 04
        were filed by an earlier run, so when 03 came along it was the
        only *unfiled* book claiming the name and was refused as a
        series of one — with two of its own siblings already filed under
        it. Agreement is about the library, not about this run.
        """
        clear(library)
        book(library, "Geodyssey 02 - Shame of Man", "Shame of Man",
             "Piers Anthony")
        book(library, "Geodyssey 04 - Muse of Art", "Muse of Art",
             "Piers Anthony")
        gateway.reindex()
        namer.run(apply=True)
        assert series_of(gateway, "Shame of Man") == ("Geodyssey", 2)

        book(library, "Geodyssey 03 - Hope of Earth", "Hope of Earth",
             "Piers Anthony")
        gateway.reindex()
        namer.run(apply=True)
        assert series_of(gateway, "Hope of Earth") == ("Geodyssey", 3)

    def test_one_filed_book_is_still_not_enough_for_a_stranger(
            self, client, library, gateway, namer):
        """Corroboration is still corroboration: one book, however it
        was filed, does not make "is Better Than" a series."""
        clear(library)
        book(library, "10 is Better Than 01 - Heather Jarman",
             "10 is Better Than 01", "Heather Jarman")
        gateway.reindex()
        assert namer.run(apply=True)["named"] == 0

    def test_a_series_from_the_file_is_not_overwritten(self, client, library,
                                                       gateway, namer):
        clear(library)
        EpubBuilder(title="Hunters of Dune", author="Brian Herbert",
                    series="Dune", series_index=7).build(
            library / "Dune Chronicles 1 - Hunters of Dune.epub")
        EpubBuilder(title="Sandworms of Dune", author="Brian Herbert",
                    series="Dune", series_index=8).build(
            library / "Dune Chronicles 2 - Sandworms of Dune.epub")
        gateway.reindex()
        namer.run(apply=True)
        assert series_of(gateway, "Hunters of Dune") == ("Dune", 7)

    def test_running_twice_changes_nothing_the_second_time(self, client,
                                                           library, gateway,
                                                           namer):
        clear(library)
        book(library, "Xanth 11 - Heaven Cent", "Heaven Cent", "Piers Anthony")
        book(library, "Xanth 12 - Man From Mundania", "Man From Mundania",
             "Piers Anthony")
        gateway.reindex()
        namer.run(apply=True)
        assert namer.run(apply=True)["named"] == 0

    def test_a_dry_run_changes_nothing(self, client, library, gateway, namer):
        clear(library)
        book(library, "Xanth 11 - Heaven Cent", "Heaven Cent", "Piers Anthony")
        book(library, "Xanth 12 - Man From Mundania", "Man From Mundania",
             "Piers Anthony")
        gateway.reindex()
        report = namer.run(apply=False)
        assert report["named"] == 0
        assert report["would_name"] == 2
        assert series_of(gateway, "Heaven Cent") == (None, None)

    def test_it_survives_a_rescan(self, client, library, gateway, namer):
        clear(library)
        book(library, "Xanth 11 - Heaven Cent", "Heaven Cent", "Piers Anthony")
        book(library, "Xanth 12 - Man From Mundania", "Man From Mundania",
             "Piers Anthony")
        gateway.reindex()
        namer.run(apply=True)
        assert gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM field_provenance WHERE field = 'series'"
        ).fetchone()["n"] == 2


class TestTheCommand:
    @pytest.fixture(autouse=True)
    def offline(self, monkeypatch):
        import epub_server

        def refuse(*args, **kwargs):
            def fetch(url, headers=None):
                raise OSError("the test suite does not use the network")
            return fetch

        monkeypatch.setattr(epub_server, "PoliteFetcher", refuse)

    def two_of_a_series(self, tmp_path):
        library = tmp_path / "books"
        library.mkdir(exist_ok=True)
        for number, title in ((11, "Heaven Cent"), (12, "Man From Mundania")):
            EpubBuilder(title=title, author="Piers Anthony").build(
                library / f"Xanth {number} - {title}.epub")
        state = tmp_path / "state"
        state.mkdir(exist_ok=True)
        env = tmp_path / ".env.series"
        env.write_text(
            f"EPUB_PORT=8775\nEPUB_BIND=127.0.0.1\nLIBRARY_ROOT={library}\n"
            f"DB_PATH={state}/books.db\nCOVER_CACHE={state}/covers\n"
            f"LOG_LEVEL=WARNING\nLOG_FILE={state}/test.log\n"
            # A scan now reads the names it finds (§8.16), which would
            # file these two before `--series` was asked to. Off here, so
            # what is under test is the command rather than the scan.
            f"TIDY_ON_SCAN=false\n")
        return env

    def test_it_reports_without_filing_anything(self, tmp_path, capsys):
        from epub_gateway import GatewayCLI
        env = self.two_of_a_series(tmp_path)
        GatewayCLI.main(["--env", str(env), "--scan"])
        assert GatewayCLI.main(["--env", str(env), "--series"]) == 0
        assert "would file 2 books" in capsys.readouterr().out

    def test_apply_files_them(self, tmp_path, capsys):
        from epub_gateway import GatewayCLI
        env = self.two_of_a_series(tmp_path)
        GatewayCLI.main(["--env", str(env), "--scan"])
        GatewayCLI.main(["--env", str(env), "--series", "--apply"])
        assert "filed 2 books" in capsys.readouterr().out
