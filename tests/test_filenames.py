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


class TestUnderscores:
    """A filesystem that never learned about spaces.

    `Hamilton,_Laurell_K._-_Anita_Blake_04_-_The_Lunatic_Cafe` is a
    perfectly ordinary name with every space replaced. Read as it stands
    it parses as nothing, so the book kept a scanner called "Lee Hughes"
    as its author and its whole filename as its title.
    """

    def test_a_name_written_with_underscores_is_still_a_name(
            self, client, library, gateway, namer):
        clear(library)
        for n, name in ((4, "The_Lunatic_Cafe"), (5, "Bloody_Bones")):
            stem = f"Hamilton,_Laurell_K._-_Anita_Blake_{n:02d}_-_{name}"
            # The real file carries the scanner's name after it, which is
            # why the title and the filename differ.
            EpubBuilder(title=stem, author="Lee Hughes").build(
                library / f"{stem} - Lee Hughes.epub")
        gateway.reindex()
        namer.run(apply=True)
        row = book(gateway, "Lunatic")
        assert row["authors"] == "Laurell K. Hamilton"
        assert row["series"] == "Anita Blake"
        assert row["series_index"] == 4
        assert row["title"] == "The Lunatic Cafe"

    def test_a_name_that_merely_contains_one_is_left_alone(self, client,
                                                           library, gateway,
                                                           namer):
        """One underscore among words is somebody's punctuation, not a
        filesystem standing in for every space."""
        clear(library)
        EpubBuilder(title="Anthony, Piers - Chthon_Phthor Omnibus",
                    author="Piers Anthony").build(library / "omni.epub")
        gateway.reindex()
        namer.run(apply=True)
        assert book(gateway, "omni")["title"] == "Chthon_Phthor Omnibus"


class TestTheRulesCompose:
    """`names` derives a title from `file_title`, which is the file's
    own words — scanner tags and all.

    So the two passes fought: `tags` took `(v2.1)` off, and the next
    `names` run put it straight back, because that is what the file said.
    A title this pass writes goes through §8.13d on the way out.
    """

    def test_a_scanner_tag_does_not_come_back(self, client, library, gateway,
                                              namer):
        clear(library)
        EpubBuilder(title="Hamilton, Laurell K. - The Lunatic Cafe(v2.1)",
                    author="Lee Hughes").build(library / "cafe.epub")
        gateway.reindex()
        namer.run(apply=True)
        assert book(gateway, "cafe")["title"] == "The Lunatic Cafe"
        namer.run(apply=True)
        assert book(gateway, "cafe")["title"] == "The Lunatic Cafe"


class TestNamesThatAreNotNames:
    """What a library-wide dry run caught before it wrote anything.

    268 books, 61 of them replacing an author rather than filling a
    blank — and reading those 61 found four filenames the parser had
    misread. Each is a different way for a comma to appear in something
    that is not a name.
    """

    def test_a_junk_prefix_is_not_a_surname(self, client, library, gateway,
                                            namer):
        """`e-Book - Feist, Raymond E. - Krondor The Assassins` gave
        "Raymond E. e-Book - Feist". A surname may be hyphenated, but a
        *spaced* dash is a separator, not a name."""
        clear(library)
        EpubBuilder(title="e-Book - Feist, Raymond E. - Krondor The Assassins",
                    author="Somebody Real").build(library / "krondor.epub")
        gateway.reindex()
        namer.run(apply=True)
        assert book(gateway, "krondor")["authors"] == "Somebody Real"

    def test_a_title_with_a_comma_is_not_a_name(self, client, library,
                                                gateway, namer):
        """`Cryptozoology, A Guide to (Mysterious Creatures) - George M.
        Eberhart` put the *title* where the author goes and would have
        credited the book to "A Guide to (Mysterious Creatures)
        Cryptozoology". Given names are short, capitalised, and have no
        brackets in them."""
        clear(library)
        EpubBuilder(
            title="Cryptozoology, A Guide to (Mysterious Creatures) - "
                  "George M. Eberhart",
            author="George M. Eberhart").build(library / "crypto.epub")
        gateway.reindex()
        namer.run(apply=True)
        assert book(gateway, "crypto")["authors"] == "George M. Eberhart"

    def test_an_editor_is_not_claimed_as_the_author(self, client, library,
                                                    gateway, namer):
        """`Jakubowski, Maxim (ed.) - New Crimes` is an anthology, and
        `(ed.)` says so. §5 has a `role` column for editors; until
        something fills it, saying nothing beats saying the wrong
        thing."""
        clear(library)
        EpubBuilder(title="Jakubowski, Maxim (ed.) - New Crimes",
                    author="John Le Carre").build(library / "crimes.epub")
        gateway.reindex()
        namer.run(apply=True)
        assert book(gateway, "crimes")["authors"] == "John Le Carre"

    def test_two_authors_written_surname_first(self, client, library, gateway,
                                               namer):
        """`Pratchett, Terry & Gaiman, Neil - Good Omens` is two people,
        and reading it as one gave "Terry & Gaiman, Neil Pratchett"."""
        clear(library)
        EpubBuilder(title="Pratchett, Terry & Gaiman, Neil - Good Omens",
                    author="harry").build(library / "omens.epub")
        gateway.reindex()
        namer.run(apply=True)
        row = book(gateway, "omens")
        assert row["authors"] == "Terry Pratchett & Neil Gaiman"
        assert row["title"] == "Good Omens"


class TestTheNameIsNotAFile:
    """A title never ends in `.txt`.

    12 books carry one: `Anthony, Piers - Volk.doc`, `Brooks,Terry -
    Word01 - Running with the Demon.txt`, `Nanny Ogg's Cookbook.htm` —
    the mark of a library converted from one format to another by
    somebody who kept the old name. Writing `Volk.doc` as a title, having
    just decided what the title is, would be knowingly writing something
    wrong.
    """

    @pytest.mark.parametrize("stem,expected", [
        ("Anthony, Piers - Volk.doc", "Volk"),
        ("Goodkind, Terry - Wizard's First Rule.jpg", "Wizard's First Rule"),
        ("Anthony, Piers - Something.jpeg", "Something"),
        ("Anthony, Piers - Novel - Ghost.doc", "Novel - Ghost"),
        ("Anthony, Piers - Something.TXT", "Something"),
        ("Anthony, Piers - Krondor The Assassins.htm", "Krondor The Assassins"),
    ])
    def test_a_file_extension_is_not_part_of_the_title(self, client, library,
                                                       gateway, namer,
                                                       stem, expected):
        clear(library)
        EpubBuilder(title=stem, author="Piers Anthony").build(
            library / f"{stem}.epub")
        gateway.reindex()
        namer.run(apply=True)
        assert book(gateway, expected.split()[0])["title"] == expected

    def test_a_title_that_merely_has_a_dot_is_kept(self, client, library,
                                                   gateway, namer):
        """`2001` and `Mr. Norrell` have dots in them and are not files."""
        clear(library)
        EpubBuilder(title="Anthony, Piers - Jonathan Strange & Mr. Norrell",
                    author="Piers Anthony").build(library / "js.epub")
        gateway.reindex()
        namer.run(apply=True)
        assert book(gateway, "js")["title"] == "Jonathan Strange & Mr. Norrell"


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

    def test_a_title_that_is_itself_a_filename(self, client, library,
                                               gateway, namer):
        """The guard is "the file said nothing about itself", and
        `title == filename` was only ever a proxy for it.

        Four Bio of a Space Tyrant EPUBs carry `Anthony, Piers - Tyrant 1
        - Refugee` in their own OPF, while the file on disk has the
        author appended a second time — so the two are not equal and the
        pass walked past them. The file did speak; what it said was a
        filename. A stored title that leads with `Surname, First - ` came
        from somebody's folder wherever it now sits.
        """
        clear(library)
        for n, name in ((1, "Refugee"), (2, "Mercenary")):
            EpubBuilder(title=f"Anthony, Piers - Tyrant {n} - {name}",
                        author="Piers Anthony").build(
                library / f"Anthony, Piers - Tyrant {n} - {name} - Anthony, Piers.epub")
        gateway.reindex()
        namer.run(apply=True)
        row = book(gateway, "Refugee")
        assert row["title"] == "Refugee"
        assert row["series"] == "Tyrant"
        assert row["series_index"] == 1

    def test_a_shelf_label_is_not_a_name(self, client, library, gateway,
                                         namer):
        """When the file's own name gives only a shelf — a series and a
        number and no book — the path is the better name.

        `Lackey, Mercedes - Elemental Masters 02` is what one EPUB calls
        itself, and it names no book at all; the file on disk is
        `… - The Gates Of Sleep.epub`. Preferring the file's word left
        the title as `Elemental Masters 02`, and since a sibling was
        *also* filed as Elemental Masters 02, dedupe took them for one
        book and offered to hide *The Serpent's Shadow*. A wrong title
        hides a book as surely as a wrong position does (§8.10).
        """
        clear(library)
        for n, name in ((1, "The Serpents Shadow"), (2, "The Gates Of Sleep")):
            EpubBuilder(title=f"Lackey, Mercedes - Elemental Masters {n:02d}",
                        author="Mercedes Lackey").build(
                library / f"Lackey, Mercedes - Elemental Masters {n:02d} - {name}.epub")
        gateway.reindex()
        namer.run(apply=True)
        assert book(gateway, "Gates Of Sleep")["title"] == "The Gates Of Sleep"
        assert book(gateway, "Serpents Shadow")["title"] == "The Serpents Shadow"

    def test_a_file_that_named_a_book_is_still_believed(self, client, library,
                                                        gateway, namer):
        """The other half of the same rule, and the case that made
        `file_title` the source in the first place: where the file names
        a *book*, the path's extra words are somebody's signature, not a
        title."""
        clear(library)
        EpubBuilder(title="Roberts, Nora - A World Apart",
                    author="Emily").build(
            library / "Roberts, Nora - A World Apart - Emily & Jane.epub")
        gateway.reindex()
        namer.run(apply=True)
        assert book(gateway, "World Apart")["title"] == "A World Apart"

    def test_a_real_title_is_still_not_a_filename(self, client, library,
                                                  gateway, namer):
        """The widening must not reach a book that named itself
        properly, however its file is called."""
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

    def test_a_hand_filed_series_trims_a_misspelt_shelf(self, client, library,
                                                        gateway, namer):
        """Once a person has said what the series is, the words in front
        of its number are the shelf — however the file spells them.

        Found in the real library: "Geodessey 01 - Isle of Women" is a
        misspelling of Geodyssey, so nothing corroborated it and it kept
        its whole name. Filing it by hand settled the series and left
        the title still reading "Geodessey 01", which is the shelf
        talking twice and disagreeing with itself.
        """
        clear(library)
        pdf(library, "Anthony, Piers - Geodessey 01 - Isle of Women")
        gateway.reindex()
        namer.run(apply=True)
        assert book(gateway, "Isle of Women")["title"] == \
            "Geodessey 01 - Isle of Women"

        from epub_credits import BookCredits
        BookCredits(gateway.database).set_series(
            book(gateway, "Isle of Women")["id"], "Geodyssey", 1)
        namer.run(apply=True)
        assert book(gateway, "Isle of Women")["title"] == "Isle of Women"
        assert book(gateway, "Isle of Women")["series"] == "Geodyssey"

    def test_a_number_that_disagrees_is_not_the_shelf(self, client, library,
                                                      gateway, namer):
        """The number is what carries the misspelt name. Without it
        agreeing there is nothing tying the words to the shelf, so they
        stay part of the book."""
        clear(library)
        pdf(library, "Anthony, Piers - Geodessey 01 - Isle of Women")
        gateway.reindex()
        namer.run(apply=True)
        from epub_credits import BookCredits
        BookCredits(gateway.database).set_series(
            book(gateway, "Isle of Women")["id"], "Geodyssey", 4)
        namer.run(apply=True)
        assert book(gateway, "Isle of Women")["title"] == \
            "Geodessey 01 - Isle of Women"

    def test_running_again_does_not_undo_the_first_answer(self, client,
                                                          library, gateway,
                                                          namer):
        """A second run must read the name the FILE gave, not the title
        this pass wrote and not the path.

        `Roberts, Nora - A World Apart - Emily & Jane.epub` holds
        `Roberts, Nora - A World Apart` in its OPF — the file is
        *shorter* than its own filename, because a scanner appended their
        name. Once the title is `A World Apart` it no longer looks like a
        filename, so a second pass fell back to the path and produced
        `A World Apart - Emily & Jane`. `file_title` is what the file
        said, and it is the only stable source.
        """
        clear(library)
        EpubBuilder(title="Roberts, Nora - A World Apart",
                    author="Emily").build(
            library / "Roberts, Nora - A World Apart - Emily & Jane.epub")
        gateway.reindex()
        namer.run(apply=True)
        first = book(gateway, "World Apart")["title"]
        assert first == "A World Apart"
        namer.run(apply=True)
        assert book(gateway, "World Apart")["title"] == first
        namer.run(apply=True)
        assert book(gateway, "World Apart")["title"] == first

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
