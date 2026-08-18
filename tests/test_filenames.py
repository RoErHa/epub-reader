"""Author, series and title, read from one filename.

The real library holds 45 Piers Anthony PDFs shaped like this:

    Anthony, Piers - Xanth 08 - Crewel Lye.pdf

and every one of them browses as a single run-on title, credited to
nobody — or to `harry`, which is whoever ran the scanner. §8.4 made the
filename lead for PDFs, but only for the *title*: `epub_pdf` reads the
author from the Info dictionary alone, so 35 of the 45 have no author at
all. That is what breaks everything downstream, because §6.6's order is
authors → books → titles and the first step never happened:
`SeriesFromNames` refuses a series when there is nobody to agree with
(epub_series.py, `_plausible`), so the Xanth novels are not a series
either.

The name says all three things. Reading it needs no provider.

The danger is reading it too eagerly, so the pass is narrow by
construction: it acts only where the title the database holds *is* the
filename — the mark of a file that said nothing about itself — and it
believes a series only where `SeriesFromNames` already would.
"""
from __future__ import annotations

import pytest

from epub_filenames import CreditsFromNames
from tests.epub_fixtures import EpubBuilder
from tests.pdf_fixtures import PdfBuilder


@pytest.fixture
def namer(gateway):
    return CreditsFromNames(gateway.database)


def clear(library):
    for old in library.rglob("*"):
        if old.is_file():
            old.unlink()


def pdf(library, name, author=None, title=None):
    """A scanned PDF: the filename knows, the Info dictionary does not.

    The body carries the name, because identity is the content hash —
    two PDFs with identical bytes are one book (§10.3), which silently
    halves a fixture library that meant to hold two.
    """
    PdfBuilder(title=title, author=author, lines=[name]).build(
        library / f"{name}.pdf")


def book(gateway, stem):
    """What the library holds for the book with this filename."""
    return gateway.database.connection.execute(
        """SELECT b.id, m.title, m.file_title, m.series_index,
                  s.name AS series,
                  (SELECT GROUP_CONCAT(a.name, ' & ') FROM book_author ba
                     JOIN author a ON a.id = ba.author_id
                    WHERE ba.book_id = b.id ORDER BY ba.position) AS authors
             FROM book b JOIN metadata m ON m.book_id = b.id
             LEFT JOIN series s ON s.id = m.series_id
            WHERE b.path LIKE ?""", (f"%{stem}%",)).fetchone()


def xanth(library, *numbers):
    for n in numbers:
        pdf(library, f"Anthony, Piers - Xanth {n:02d} - Book {n}")


class TestReadingTheName:
    def test_author_series_and_title_from_one_name(self, client, library,
                                                   gateway, namer):
        clear(library)
        pdf(library, "Anthony, Piers - Xanth 08 - Crewel Lye")
        pdf(library, "Anthony, Piers - Xanth 09 - Golem in the Gears")
        gateway.reindex()
        namer.run(apply=True)

        row = book(gateway, "Crewel Lye")
        assert row["authors"] == "Piers Anthony"
        assert row["series"] == "Xanth"
        assert row["series_index"] == 8
        assert row["title"] == "Crewel Lye"

    def test_the_author_is_shown_first_name_first(self, client, library,
                                                  gateway, namer):
        # The file says "Anthony, Piers" because that is how it sorts.
        # A reader wants to see "Piers Anthony".
        clear(library)
        xanth(library, 8, 9)
        gateway.reindex()
        namer.run(apply=True)
        sort = gateway.database.connection.execute(
            "SELECT sort_name FROM author WHERE name = 'Piers Anthony'"
        ).fetchone()
        assert sort is not None
        assert sort["sort_name"].startswith("anthony")

    def test_a_book_with_no_series(self, client, library, gateway, namer):
        clear(library)
        pdf(library, "Anthony, Piers - Alien Plot")
        gateway.reindex()
        namer.run(apply=True)

        row = book(gateway, "Alien Plot")
        assert row["authors"] == "Piers Anthony"
        assert row["title"] == "Alien Plot"
        assert row["series"] is None

    def test_a_half_number_is_kept(self, client, library, gateway, namer):
        clear(library)
        pdf(library, "Anthony, Piers - Xanth 08 - Crewel Lye")
        pdf(library, "Anthony, Piers - Xanth 8.5 - A Novella")
        gateway.reindex()
        namer.run(apply=True)
        assert book(gateway, "A Novella")["series_index"] == 8.5


class TestWhatItRefuses:
    def test_a_series_of_one_is_a_misread(self, client, library, gateway,
                                          namer):
        """One book claiming a name proves nothing — but the author and
        the title it gave are still worth having."""
        clear(library)
        pdf(library, "Anthony, Piers - Aton 01 - Chthon")
        gateway.reindex()
        namer.run(apply=True)

        row = book(gateway, "Chthon")
        assert row["series"] is None
        assert row["authors"] == "Piers Anthony"
        # With no series believed, the number is not the shelf talking,
        # so the words are left whole rather than cut at a guess.
        assert row["title"] == "Aton 01 - Chthon"

    def test_a_title_the_file_gave_is_left_alone(self, client, library,
                                                 gateway, namer):
        """The mark of a file that said nothing is that its title *is*
        the filename. A book that named itself is not this pass's
        business."""
        clear(library)
        EpubBuilder(title="Chaining the Lady", author="Piers Anthony").build(
            library / "Anthony, Piers - Cluster 2 - Chaining The Lady.epub")
        EpubBuilder(title="Cluster", author="Piers Anthony").build(
            library / "Anthony, Piers - Cluster 1 - Cluster.epub")
        gateway.reindex()
        namer.run(apply=True)
        assert book(gateway, "Chaining The Lady")["title"] == "Chaining the Lady"

    def test_a_name_that_does_not_lead_with_its_author(self, client, library,
                                                       gateway, namer):
        """"10 is Better Than 01 - Heather Jarman" is a book called that,
        with its author at the END. Nothing here may touch it."""
        clear(library)
        pdf(library, "10 is Better Than 01 - Heather Jarman")
        gateway.reindex()
        before = book(gateway, "10 is Better")
        namer.run(apply=True)
        after = book(gateway, "10 is Better")
        assert after["title"] == before["title"]
        assert after["authors"] == before["authors"]

    def test_a_hand_set_author_is_never_touched(self, client, library,
                                                gateway, namer):
        clear(library)
        xanth(library, 8, 9)
        gateway.reindex()
        row = book(gateway, "Xanth 08")
        from epub_credits import BookCredits
        BookCredits(gateway.database).set(row["id"], ["Somebody Else"])
        namer.run(apply=True)
        assert book(gateway, "Xanth 08")["authors"] == "Somebody Else"

    def test_a_hand_set_title_is_never_touched(self, client, library,
                                               gateway, namer):
        clear(library)
        xanth(library, 8, 9)
        gateway.reindex()
        row = book(gateway, "Xanth 08")
        gateway.database.connection.execute(
            "UPDATE metadata SET title = 'Mine' WHERE book_id = ?", (row["id"],))
        from epub_credits import BookCredits
        BookCredits(gateway.database).record(row["id"], "title")
        namer.run(apply=True)
        assert book(gateway, "Xanth 08")["title"] == "Mine"

    def test_a_year_is_not_a_position(self, client, library, gateway, namer):
        clear(library)
        pdf(library, "Anthony, Piers - Orwell 1984 - Some Book")
        pdf(library, "Anthony, Piers - Orwell 1984 - Another Book")
        gateway.reindex()
        namer.run(apply=True)
        assert book(gateway, "Some Book")["series"] is None


class TestWritingItDown:
    def test_the_name_the_file_gave_is_kept(self, client, library, gateway,
                                            namer):
        """Every rename is reversible — §6.3's rule, and how twelve bad
        ones were undone rather than argued about."""
        clear(library)
        xanth(library, 8, 9)
        gateway.reindex()
        namer.run(apply=True)
        row = book(gateway, "Xanth 08")
        assert row["file_title"] == "Anthony, Piers - Xanth 08 - Book 8"

    def test_provenance_is_recorded(self, client, library, gateway, namer):
        """Or the next scan takes it straight back out (§6.4)."""
        clear(library)
        xanth(library, 8, 9)
        gateway.reindex()
        namer.run(apply=True)
        row = book(gateway, "Xanth 08")
        fields = {r["field"]: r["origin"] for r in
                  gateway.database.connection.execute(
                      "SELECT field, origin FROM field_provenance"
                      " WHERE book_id = ?", (row["id"],))}
        assert fields.get("authors") == "provider"
        assert fields.get("title") == "provider"

    def test_a_scanner_is_not_an_author(self, client, library, gateway, namer):
        """The Info dictionary of these scans says `harry`, `verdell`,
        `Altena`. The filename says who wrote the book."""
        clear(library)
        pdf(library, "Anthony, Piers - Xanth 08 - Crewel Lye", author="harry")
        pdf(library, "Anthony, Piers - Xanth 09 - Golem", author="harry")
        gateway.reindex()
        assert book(gateway, "Crewel Lye")["authors"] == "harry"
        namer.run(apply=True)
        assert book(gateway, "Crewel Lye")["authors"] == "Piers Anthony"

    def test_a_series_found_later_trims_the_title(self, client, library,
                                                  gateway, namer):
        """This pass owns the titles it wrote, and may refine them.

        A book alone on its shelf keeps its words whole, number and all.
        When a sibling turns up and the series becomes real, the number
        is the shelf talking after all — and without this the first
        title stood for ever, because it no longer looked like a
        filename.
        """
        clear(library)
        pdf(library, "Anthony, Piers - Aton 01 - Chthon")
        gateway.reindex()
        namer.run(apply=True)
        assert book(gateway, "Chthon")["title"] == "Aton 01 - Chthon"

        pdf(library, "Anthony, Piers - Aton 02 - Phthor")
        gateway.reindex()
        namer.run(apply=True)
        assert book(gateway, "Chthon")["series"] == "Aton"
        assert book(gateway, "Chthon")["title"] == "Chthon"
        # The file's own words are still the ones it arrived with.
        assert book(gateway, "Chthon")["file_title"] == \
            "Anthony, Piers - Aton 01 - Chthon"

    def test_the_summary_says_what_a_later_run_did(self):
        """A run that credits nobody — because everybody is credited
        already — may still file and retitle. Saying "nothing to read"
        hides work that was done, which is how a pass comes to look
        broken when it is not."""
        report = {"credited": 0, "would_credit": 0, "filed": 3,
                  "would_file": 3, "retitled": 2, "would_retitle": 2}
        line = CreditsFromNames.summary(report)
        assert "nothing" not in line
        assert "3" in line and "2" in line

    def test_a_dry_run_changes_nothing(self, client, library, gateway, namer):
        clear(library)
        xanth(library, 8, 9)
        gateway.reindex()
        before = book(gateway, "Xanth 08")
        report = namer.run()
        after = book(gateway, "Xanth 08")
        assert report["would_credit"] == 2
        assert after["title"] == before["title"]
        assert after["authors"] == before["authors"]


class TestScope:
    def test_only_the_author_asked_for(self, client, library, gateway, namer):
        clear(library)
        xanth(library, 8, 9)
        pdf(library, "Cussler, Clive - Dirk Pitt 01 - Pacific Vortex")
        pdf(library, "Cussler, Clive - Dirk Pitt 02 - The Med Caper")
        gateway.reindex()
        namer.run(apply=True, author="Piers Anthony")
        assert book(gateway, "Xanth 08")["authors"] == "Piers Anthony"
        assert book(gateway, "Pacific Vortex")["authors"] is None

    def test_only_the_format_asked_for(self, client, library, gateway, namer):
        clear(library)
        xanth(library, 8, 9)
        # An EPUB in the same shape, credited to whoever built the file.
        EpubBuilder(title="Anthony, Piers - Xanth 10 - Vale of the Vole",
                    author="harry").build(
            library / "Anthony, Piers - Xanth 10 - Vale of the Vole.epub")
        gateway.reindex()
        namer.run(apply=True, formats=["pdf"])
        assert book(gateway, "Xanth 08")["authors"] == "Piers Anthony"
        assert book(gateway, "Vale of the Vole")["authors"] == "harry"
