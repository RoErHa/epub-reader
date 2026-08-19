"""A co-author written into the title, because a file has one name.

    Anthony, Piers - Dead Morn (with Roberto Fuentes).pdf
    Asprin, Robert Lynn - Catwoman - Tiger Hunt (With Lynn Abbey).epub

*Dead Morn* is by Piers Anthony **and Roberto Fuentes**. Whoever filed
it had one filename and two authors, so they put the second one in
brackets — and every pass since has read it as part of the book's name.

This is §12.3's tier 0: a rule, not a lookup. It needs no provider, no
key and no network, and it is built first because it costs nothing and
because it shortens the strings the later tiers have to work with.

**13 books on the shelves carry it**, measured 2026-08-19 — nine of Piers
Anthony's, two of P. N. Elrod's, and one each from Robert Asprin and
Andre Norton. Every other parenthetical in the library is the scanner
talking: `(not ocr)`, `(v2.1)`, `(txt)`, `(9781982156176)`, `(1946)`.
The word `with` is what separates the two, and it does most of the work.
"""
from __future__ import annotations

import pytest

from epub_coauthors import CoAuthorsInTitles
from tests.epub_fixtures import EpubBuilder
from tests.pdf_fixtures import PdfBuilder


@pytest.fixture
def namer(gateway):
    return CoAuthorsInTitles(gateway.database)


def clear(library):
    for old in library.rglob("*"):
        if old.is_file():
            old.unlink()


def book(library, title, author="Piers Anthony", name=None):
    EpubBuilder(title=title, author=author).build(
        library / f"{name or title.replace('/', '-')}.epub")


def shelf(gateway, like):
    return gateway.database.connection.execute(
        """SELECT b.id, m.title, m.file_title,
                  (SELECT GROUP_CONCAT(a.name, ' & ') FROM book_author ba
                     JOIN author a ON a.id = ba.author_id
                    WHERE ba.book_id = b.id ORDER BY ba.position) AS authors
             FROM book b JOIN metadata m ON m.book_id = b.id
            WHERE m.title LIKE ?""", (f"%{like}%",)).fetchone()


class TestReadingTheParenthetical:
    def test_a_co_author_becomes_a_second_author(self, client, library,
                                                 gateway, namer):
        clear(library)
        book(library, "Dead Morn (with Roberto Fuentes)")
        gateway.reindex()
        namer.run(apply=True)
        row = shelf(gateway, "Dead Morn")
        assert row["title"] == "Dead Morn"
        assert row["authors"] == "Piers Anthony & Roberto Fuentes"

    def test_the_credit_order_is_kept(self, client, library, gateway, namer):
        """Whoever the file named is the first author. §5 says credit
        order matters, which is why `book_author` carries a position."""
        clear(library)
        book(library, "Dead Morn (with Roberto Fuentes)")
        gateway.reindex()
        namer.run(apply=True)
        order = [r["name"] for r in gateway.database.connection.execute(
            """SELECT a.name FROM book_author ba JOIN author a ON a.id = ba.author_id
                WHERE ba.book_id = ? ORDER BY ba.position""",
            (shelf(gateway, "Dead Morn")["id"],))]
        assert order == ["Piers Anthony", "Roberto Fuentes"]

    def test_two_people_in_one_bracket(self, client, library, gateway, namer):
        clear(library)
        book(library, "Quest for the Fallen Star "
                      "(with James Richey and Alan Riggs)")
        gateway.reindex()
        namer.run(apply=True)
        row = shelf(gateway, "Quest for the Fallen Star")
        assert row["title"] == "Quest for the Fallen Star"
        assert row["authors"] == "Piers Anthony & James Richey & Alan Riggs"

    def test_a_capital_w_is_not_a_different_word(self, client, library,
                                                 gateway, namer):
        clear(library)
        book(library, "Tiger Hunt (With Lynn Abbey)", author="Robert Asprin")
        gateway.reindex()
        namer.run(apply=True)
        assert shelf(gateway, "Tiger Hunt")["title"] == "Tiger Hunt"

    def test_only_the_with_bracket_goes(self, client, library, gateway, namer):
        """`Elvenblood (With Mercedes Lackey) (v1.0)` — the version tag is
        somebody else's problem. A pass that tidies more than it was
        asked to is a pass nobody can predict."""
        clear(library)
        book(library, "Elvenblood (With Mercedes Lackey) (v1.0)",
             author="Andre Norton")
        gateway.reindex()
        namer.run(apply=True)
        row = shelf(gateway, "Elvenblood")
        assert row["title"] == "Elvenblood (v1.0)"
        assert row["authors"] == "Andre Norton & Mercedes Lackey"


class TestWhatItRefuses:
    @pytest.mark.parametrize("title", [
        "Some Book (not ocr)", "Some Book (v2.1)", "Some Book (2013)",
        "Some Book (txt)", "Some Book (9781982156176)",
        "Some Book (The Murderbot Diaries)", "Some Book (Book 1)",
    ])
    def test_a_scanners_note_is_not_a_person(self, client, library, gateway,
                                             namer, title):
        clear(library)
        book(library, title)
        gateway.reindex()
        namer.run(apply=True)
        assert shelf(gateway, "Some Book")["title"] == title

    def test_a_book_with_no_author_is_left_alone(self, client, library,
                                                 gateway, namer):
        """A co-author is a *second* author, so there has to be a first.

        Two P. N. Elrod PDFs credit nobody at all — their filenames name
        him and `epub_pdf` never reads it. Crediting Nigel Bennett alone
        would make the collaborator the author, which is worse than the
        bracket. §6.6's order again: authors first, then everything asked
        of them.
        """
        clear(library)
        # A PDF, because that is where authorless books come from: the
        # filename names Elrod and `epub_pdf` reads only the Info dict.
        PdfBuilder(title=None, author=None,
                   lines=["Keeper of the King"]).build(
            library / "Keeper of the King (with Nigel Bennett).pdf")
        gateway.reindex()
        row = shelf(gateway, "Keeper of the King")
        assert row["authors"] is None, "fixture should credit nobody"
        namer.run(apply=True)
        after = shelf(gateway, "Keeper of the King")
        assert after["title"] == "Keeper of the King (with Nigel Bennett)"
        assert after["authors"] is None

    def test_a_name_already_credited_is_not_added_twice(self, client, library,
                                                        gateway, namer):
        clear(library)
        EpubBuilder(title="The Ring (with Robert E. Margroff)",
                    author="Piers Anthony & Robert E. Margroff").build(
            library / "ring.epub")
        gateway.reindex()
        namer.run(apply=True)
        row = shelf(gateway, "The Ring")
        assert row["title"] == "The Ring"
        assert row["authors"].count("Margroff") == 1

    def test_something_that_is_not_a_name(self, client, library, gateway,
                                          namer):
        clear(library)
        book(library, "Some Book (with 2)")
        gateway.reindex()
        namer.run(apply=True)
        assert shelf(gateway, "Some Book")["title"] == "Some Book (with 2)"

    def test_a_hand_set_author_is_never_touched(self, client, library,
                                                gateway, namer):
        clear(library)
        book(library, "Dead Morn (with Roberto Fuentes)")
        gateway.reindex()
        from epub_credits import BookCredits
        BookCredits(gateway.database).set(
            shelf(gateway, "Dead Morn")["id"], ["Somebody Else"])
        namer.run(apply=True)
        row = shelf(gateway, "Dead Morn")
        assert row["authors"] == "Somebody Else"
        assert row["title"] == "Dead Morn (with Roberto Fuentes)"


class TestWritingItDown:
    def test_the_name_the_file_gave_is_kept(self, client, library, gateway,
                                            namer):
        clear(library)
        book(library, "Dead Morn (with Roberto Fuentes)")
        gateway.reindex()
        namer.run(apply=True)
        assert shelf(gateway, "Dead Morn")["file_title"] == \
            "Dead Morn (with Roberto Fuentes)"

    def test_provenance_is_recorded(self, client, library, gateway, namer):
        """Or the next scan puts the bracket straight back (§6.4)."""
        clear(library)
        book(library, "Dead Morn (with Roberto Fuentes)")
        gateway.reindex()
        namer.run(apply=True)
        fields = {r["field"]: (r["origin"], r["source"]) for r in
                  gateway.database.connection.execute(
                      "SELECT field, origin, source FROM field_provenance"
                      " WHERE book_id = ?", (shelf(gateway, "Dead Morn")["id"],))}
        assert fields.get("title") == ("provider", "title")
        assert fields.get("authors") == ("provider", "title")

    def test_a_dry_run_changes_nothing(self, client, library, gateway, namer):
        clear(library)
        book(library, "Dead Morn (with Roberto Fuentes)")
        gateway.reindex()
        report = namer.run()
        assert report["would_credit"] == 1
        assert shelf(gateway, "Dead Morn")["title"] == \
            "Dead Morn (with Roberto Fuentes)"

    def test_running_twice_changes_nothing_the_second_time(self, client,
                                                           library, gateway,
                                                           namer):
        clear(library)
        book(library, "Dead Morn (with Roberto Fuentes)")
        gateway.reindex()
        namer.run(apply=True)
        assert namer.run(apply=True)["credited"] == 0
