"""What the scanner wrote into the title, and the book underneath it.

    Blue Moon (v2.2)
    Enemies of the Roman Order (not ocr)
    Rome and Her Enemies (Osprey)(not ocr)

A version stamp, a note that a scan was never put through OCR, a bare
format — none of these is part of a book's name. 17 titles carry one.

**A closed vocabulary, never "strip any bracket".** The same 66 titles
also hold `(The Murderbot Diaries)`, `(Osprey, Men at Arms #121)`,
`(PhD, 1998)`, `(Collins Shorts, Book 1)` and `(Mysterious Creatures)` —
every one part of the book or its shelf. Guessing at brackets would cost
more than it cleared, which is the rule §6.1 already follows for genres
and §8.13a for file extensions: known values only, and log the rest.
"""
from __future__ import annotations

import pytest

from epub_tags import ScannerTags
from tests.epub_fixtures import EpubBuilder


@pytest.fixture
def tagger(gateway):
    return ScannerTags(gateway.database)


def clear(library):
    for old in library.rglob("*"):
        if old.is_file():
            old.unlink()


def book(library, title, author="Ann Author", name="b"):
    EpubBuilder(title=title, author=author).build(library / f"{name}.epub")


def shelf(gateway, like):
    return gateway.database.connection.execute(
        "SELECT title, file_title FROM metadata WHERE title LIKE ? OR file_title LIKE ?",
        (f"%{like}%", f"%{like}%")).fetchone()


class TestTheVocabulary:
    """The rule alone, with no database in the way."""

    @pytest.mark.parametrize("title,expected", [
        ("Blue Moon (v2.2)", "Blue Moon"),
        ("Angel Fire East (v1.0)", "Angel Fire East"),
        ("Burning Water (v 2.0)", "Burning Water"),
        ("Myth-Nomers (uc)", "Myth-Nomers"),
        ("Enemies of the Roman Order (not ocr)", "Enemies of the Roman Order"),
        ("Krondor The Assassins (txt)", "Krondor The Assassins"),
        # Two brackets, no space between them: only the scanner's goes.
        ("Rome and Her Enemies (Osprey)(not ocr)",
         "Rome and Her Enemies (Osprey)"),
        ("Roman Battle Tactics (Osprey, Elite #155)(not ocr)",
         "Roman Battle Tactics (Osprey, Elite #155)"),
    ])
    def test_a_scanners_note_comes_off(self, title, expected):
        assert ScannerTags.strip(title) == expected

    @pytest.mark.parametrize("title", [
        "All Systems Red (The Murderbot Diaries)",
        "Armies of the Carthaginian Wars (Osprey, Men at Arms #121)",
        "Double Diffraction Dissociation (PhD, 1998)",
        "Cryptozoology, A Guide to (Mysterious Creatures)",
        "How to Write (Before You Waste Three Years)",
        "The Hobbit (1946)",
        "Something (Collins Shorts, Book 1)",
        "Dead Morn (with Roberto Fuentes)",
    ])
    def test_everything_else_is_the_book(self, title):
        assert ScannerTags.strip(title) == title

    def test_a_title_is_never_emptied(self):
        """A book called "(v1.0)" is absurd, but a book called nothing is
        worse — it falls out of every list that sorts by title."""
        assert ScannerTags.strip("(v1.0)") == "(v1.0)"


class TestOnTheShelves:
    def test_the_tag_is_taken_off(self, client, library, gateway, tagger):
        clear(library)
        book(library, "Blue Moon (v2.2)")
        gateway.reindex()
        tagger.run(apply=True)
        assert shelf(gateway, "Blue Moon")["title"] == "Blue Moon"

    def test_the_name_the_file_gave_is_kept(self, client, library, gateway,
                                            tagger):
        clear(library)
        book(library, "Blue Moon (v2.2)")
        gateway.reindex()
        tagger.run(apply=True)
        assert shelf(gateway, "Blue Moon")["file_title"] == "Blue Moon (v2.2)"

    def test_provenance_is_recorded(self, client, library, gateway, tagger):
        clear(library)
        book(library, "Blue Moon (v2.2)")
        gateway.reindex()
        tagger.run(apply=True)
        row = gateway.database.connection.execute(
            "SELECT f.origin, f.source FROM field_provenance f"
            " JOIN metadata m ON m.book_id = f.book_id"
            " WHERE f.field = 'title' AND m.title = 'Blue Moon'").fetchone()
        assert (row["origin"], row["source"]) == ("provider", "title")

    def test_a_hand_set_title_is_never_touched(self, client, library, gateway,
                                               tagger):
        clear(library)
        book(library, "Blue Moon (v2.2)")
        gateway.reindex()
        from epub_credits import BookCredits
        book_id = gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title LIKE '%Blue Moon%'"
        ).fetchone()["book_id"]
        BookCredits(gateway.database).record(book_id, "title")
        tagger.run(apply=True)
        assert shelf(gateway, "Blue Moon")["title"] == "Blue Moon (v2.2)"

    def test_a_dry_run_changes_nothing(self, client, library, gateway, tagger):
        clear(library)
        book(library, "Blue Moon (v2.2)")
        gateway.reindex()
        report = tagger.run()
        assert report["would_clean"] == 1
        assert shelf(gateway, "Blue Moon")["title"] == "Blue Moon (v2.2)"

    def test_running_twice_changes_nothing_the_second_time(self, client,
                                                           library, gateway,
                                                           tagger):
        clear(library)
        book(library, "Blue Moon (v2.2)")
        gateway.reindex()
        tagger.run(apply=True)
        assert tagger.run(apply=True)["cleaned"] == 0


class TestUnderscores:
    """A title the filesystem wrote, with every space replaced.

    `Spotlight_Blues_Guitar_LessonBook` names a book nobody wrote that
    way. It is the same family as `(txt)`: something the *file* carries
    that the *book* does not.
    """

    @pytest.mark.parametrize("title,expected", [
        ("Spotlight_Blues_Guitar_LessonBook", "Spotlight Blues Guitar LessonBook"),
        ("Book_of_Ruby", "Book of Ruby"),
        ("lood_en_whisky__rosan_hollak", "lood en whisky rosan hollak"),
    ])
    def test_underscores_stand_in_for_spaces(self, title, expected):
        assert ScannerTags.strip(title) == expected

    @pytest.mark.parametrize("title", [
        "Chthon_Phthor Omnibus",          # one, among real spaces
        "Blue Moon",                       # none at all
        "Fifty_Fifty",                     # one, and no spaces to compare with
    ])
    def test_a_title_that_merely_contains_one_is_left_alone(self, title):
        assert ScannerTags.strip(title) == title

    def test_a_title_with_no_bracket_at_all_is_still_reached(
            self, client, library, gateway, tagger):
        """The candidate query looked for a bracket, which is half the
        rule. An underscored title has none — and `_` is a LIKE wildcard,
        so asking for one needs an ESCAPE or it matches every title in
        the library."""
        clear(library)
        book(library, "Spotlight_Blues_Guitar_LessonBook")
        gateway.reindex()
        assert tagger.run(apply=True)["cleaned"] == 1
        assert shelf(gateway, "Spotlight")["title"] == \
            "Spotlight Blues Guitar LessonBook"

    def test_both_rules_at_once(self):
        assert ScannerTags.strip(
            "Hamilton,_Laurell_K._-_The_Lunatic_Cafe(v2.1)"
        ) == "Hamilton, Laurell K. - The Lunatic Cafe"
