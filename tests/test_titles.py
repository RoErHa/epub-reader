"""Correcting a title from its author's bibliography.

A global title search is a poor way to identify a book: "Second Chance"
matches hundreds. Once the author is known the question gets far easier
— match the messy title against that author's works and nothing else.
The real library is full of titles that are really filenames:

    Cussler, Clive - Dirk Pitt 01 - Pacific Vortex   →  Pacific Vortex
    Anthony, Piers - Novel - Ghost.doc               →  Ghost
    Mercedes Lackey - Castle of DeceptionUC - Bards Tale.txt

Nothing here touches a file; only the database changes, and the title
the file gave is kept so the change can always be undone.
"""
from __future__ import annotations

import pytest

from epub_titles import TitleFromWorks
from tests.epub_fixtures import EpubBuilder


class StubWorks:
    """Stands in for the author enricher: fills author_work from a dict
    of author name → work titles."""

    def __init__(self, by_author=None):
        self.by_author = by_author or {}
        self.asked: list[str] = []

    def enrich(self, author_id):
        self.asked.append(author_id)
        return {"matched": False, "works": 0}


@pytest.fixture
def cussler(client, library, gateway):
    for existing in library.glob("*.epub"):
        existing.unlink()
    EpubBuilder(title="Cussler, Clive - Dirk Pitt 01 - Pacific Vortex",
                author="Clive Cussler").build(library / "a.epub")
    EpubBuilder(title="Raise the Titanic", author="Clive Cussler").build(
        library / "b.epub")
    gateway.reindex()
    works(gateway, "Clive Cussler", ["Pacific Vortex!", "Raise the Titanic",
                                     "Deep Six", "Flood Tide"])
    return client


def works(gateway, author, titles):
    """Put an author's bibliography in the database, as enrichment does."""
    db = gateway.database.connection
    author_id = db.execute("SELECT id FROM author WHERE name = ?",
                           (author,)).fetchone()["id"]
    for title in titles:
        db.execute(
            "INSERT INTO author_work (author_id, title, work_type, source,"
            " fetched_at) VALUES (?, ?, 'novel', 'openlibrary', 1)",
            (author_id, title),
        )
    return author_id


def titler(gateway, enricher=None):
    return TitleFromWorks(gateway.database, enricher or StubWorks())


def title_of(gateway, book_id):
    return gateway.database.connection.execute(
        "SELECT title FROM metadata WHERE book_id = ?", (book_id,)
    ).fetchone()["title"]


def book_named(gateway, like):
    return gateway.database.connection.execute(
        "SELECT book_id FROM metadata WHERE title LIKE ?", (f"%{like}%",)
    ).fetchone()["book_id"]


class TestCorrecting:
    def test_a_filename_becomes_a_title(self, cussler, gateway):
        book = book_named(gateway, "Pacific Vortex")
        titler(gateway).run(apply=True)
        assert title_of(gateway, book) == "Pacific Vortex!"

    def test_a_title_that_is_already_right_is_left_alone(self, cussler, gateway):
        book = book_named(gateway, "Raise the Titanic")
        report = titler(gateway).run(apply=True)
        assert title_of(gateway, book) == "Raise the Titanic"
        assert report["corrected"] == 1        # only the messy one

    def test_the_original_title_is_kept(self, cussler, gateway):
        book = book_named(gateway, "Pacific Vortex")
        titler(gateway).run(apply=True)
        assert gateway.database.connection.execute(
            "SELECT file_title FROM metadata WHERE book_id = ?", (book,)
        ).fetchone()["file_title"] == \
            "Cussler, Clive - Dirk Pitt 01 - Pacific Vortex"

    def test_the_sort_key_follows_the_new_title(self, cussler, gateway):
        book = book_named(gateway, "Pacific Vortex")
        titler(gateway).run(apply=True)
        assert gateway.database.connection.execute(
            "SELECT title_sort FROM metadata WHERE book_id = ?", (book,)
        ).fetchone()["title_sort"] == "pacific vortex"

    def test_the_book_is_findable_under_its_new_title(self, cussler, gateway):
        titler(gateway).run(apply=True)
        found = gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM book_fts WHERE book_fts MATCH 'vortex'"
        ).fetchone()["n"]
        assert found == 1

    def test_where_the_title_came_from_is_recorded(self, cussler, gateway):
        book = book_named(gateway, "Pacific Vortex")
        titler(gateway).run(apply=True)
        row = gateway.database.connection.execute(
            "SELECT origin, source FROM field_provenance WHERE book_id = ?"
            " AND field = 'title'", (book,)).fetchone()
        assert row["origin"] == "provider"

    def test_a_dry_run_changes_nothing(self, cussler, gateway):
        book = book_named(gateway, "Pacific Vortex")
        report = titler(gateway).run(apply=False)
        assert report["corrected"] == 0
        assert report["would_correct"] == 1
        assert "Dirk Pitt" in title_of(gateway, book)

    def test_the_plan_says_what_would_change(self, cussler, gateway):
        plan = titler(gateway).plan()
        assert plan[0]["from"] == "Cussler, Clive - Dirk Pitt 01 - Pacific Vortex"
        assert plan[0]["to"] == "Pacific Vortex!"

    def test_running_twice_is_harmless(self, cussler, gateway):
        titler(gateway).run(apply=True)
        assert titler(gateway).run(apply=True)["corrected"] == 0


class TestBeingCareful:
    def test_a_title_you_set_by_hand_is_never_overwritten(self, cussler,
                                                          gateway):
        book = book_named(gateway, "Pacific Vortex")
        db = gateway.database.connection
        db.execute("UPDATE metadata SET title = 'My Own Title' WHERE book_id = ?",
                   (book,))
        db.execute("INSERT INTO field_provenance (book_id, field, origin,"
                   " updated_at) VALUES (?, 'title', 'user', 1)", (book,))
        titler(gateway).run(apply=True)
        assert title_of(gateway, book) == "My Own Title"

    def test_a_locked_title_is_never_overwritten(self, cussler, gateway):
        book = book_named(gateway, "Pacific Vortex")
        gateway.database.connection.execute(
            "INSERT INTO field_provenance (book_id, field, origin, locked,"
            " updated_at) VALUES (?, 'title', 'file', 1, 1)", (book,))
        titler(gateway).run(apply=True)
        assert "Dirk Pitt" in title_of(gateway, book)

    def test_an_author_with_no_bibliography_is_skipped(self, client, library,
                                                       gateway):
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="Nobody, A - Book 01 - Something",
                    author="A Nobody").build(library / "a.epub")
        gateway.reindex()
        assert titler(gateway).run(apply=True)["corrected"] == 0

    def test_a_poor_match_is_refused(self, client, library, gateway):
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="Cussler, Clive - Dirk Pitt 01 - Pacific Vortex",
                    author="Clive Cussler").build(library / "a.epub")
        gateway.reindex()
        works(gateway, "Clive Cussler", ["Something Else Entirely"])
        assert titler(gateway).run(apply=True)["corrected"] == 0

    def test_two_works_that_match_equally_well_are_refused(self, client,
                                                           library, gateway):
        # "Damia" against "Damia" and "Damia's Children" is decidable;
        # a title matching two works equally is not.
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="Herbert, Frank - Dune", author="Frank Herbert").build(
            library / "a.epub")
        gateway.reindex()
        works(gateway, "Frank Herbert", ["Dune", "Dune."])
        assert titler(gateway).run(apply=True)["corrected"] == 0

    def test_a_near_twin_is_still_told_apart(self, client, library, gateway):
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="Anne McCaffrey - Tower03 Damia's Children",
                    author="Anne McCaffrey").build(library / "a.epub")
        gateway.reindex()
        works(gateway, "Anne McCaffrey", ["Damia", "Damia's Children",
                                          "Lyon's Pride"])
        titler(gateway).run(apply=True)
        assert title_of(gateway, book_named(gateway, "Damia")) == \
            "Damia's Children"

    def test_the_longest_matching_work_wins(self, client, library, gateway):
        # "Dune" is inside "Dune Messiah", so the shorter work must not
        # win by being contained twice over.
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="Herbert, Frank - Dune 02 - Dune Messiah",
                    author="Frank Herbert").build(library / "a.epub")
        gateway.reindex()
        works(gateway, "Frank Herbert", ["Dune", "Dune Messiah",
                                         "Children of Dune"])
        titler(gateway).run(apply=True)
        assert title_of(gateway, book_named(gateway, "Dune")) == "Dune Messiah"

    def test_a_title_is_not_shortened_to_a_different_book(self, client,
                                                          library, gateway):
        # The real failure. Open Library caps a bibliography at 200
        # works, so Asimov's list has "Foundation" but not "Foundation
        # and Empire" — and the one is not the other.
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="FOUNDATION AND EMPIRE", author="Isaac Asimov").build(
            library / "a.epub")
        EpubBuilder(title="Forward the Foundation",
                    author="Isaac Asimov").build(library / "b.epub")
        gateway.reindex()
        works(gateway, "Isaac Asimov", ["Foundation", "I, Robot"])
        assert titler(gateway).run(apply=True)["corrected"] == 0

    def test_a_series_number_does_not_count_against_the_match(self, client,
                                                              library, gateway):
        # "Catteni 3 - Freedom's Challenge" is the series talking, not
        # the title.
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="Catteni 3 - Freedom's Challenge",
                    author="Anne McCaffrey").build(library / "a.epub")
        gateway.reindex()
        works(gateway, "Anne McCaffrey", ["Freedom's Challenge", "Freedom's Ransom"])
        titler(gateway).run(apply=True)
        assert title_of(gateway, book_named(gateway, "Freedom")) == \
            "Freedom's Challenge"

    def test_a_credit_after_the_title_does_not_count_against_it(self, client,
                                                                library, gateway):
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="McCaffrey, Anne - The Death of Sleep (With Nye, J.L.)",
                    author="Anne McCaffrey").build(library / "a.epub")
        gateway.reindex()
        works(gateway, "Anne McCaffrey", ["The Death of Sleep", "Sassinak"])
        titler(gateway).run(apply=True)
        assert title_of(gateway, book_named(gateway, "Death of Sleep")) == \
            "The Death of Sleep"

    def test_two_contained_works_of_the_same_length_are_refused(self, client,
                                                                library,
                                                                gateway):
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="Lackey, Mercedes - Storm Warning Storm Rising",
                    author="Mercedes Lackey").build(library / "a.epub")
        gateway.reindex()
        works(gateway, "Mercedes Lackey", ["Storm Warning", "Storm Rising"])
        assert titler(gateway).run(apply=True)["corrected"] == 0

    def test_a_bibliography_is_fetched_when_it_is_missing(self, client,
                                                          library, gateway):
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="Cussler, Clive - Dirk Pitt 01 - Pacific Vortex",
                    author="Clive Cussler").build(library / "a.epub")
        gateway.reindex()
        enricher = StubWorks()
        titler(gateway, enricher).run(apply=True)
        assert len(enricher.asked) == 1

    def test_an_author_is_only_asked_about_once(self, client, library, gateway):
        for existing in library.glob("*.epub"):
            existing.unlink()
        for i in range(3):
            EpubBuilder(title=f"Cussler, Clive - Dirk Pitt 0{i} - Book {i}",
                        author="Clive Cussler").build(library / f"a{i}.epub")
        gateway.reindex()
        enricher = StubWorks()
        titler(gateway, enricher).run(apply=True)
        assert len(enricher.asked) == 1


class TestHowTheTitleIsWritten:
    """Open Library's own capitalisation is inconsistent — "Acorna's
    search", "THE WINGS OF PEGASUS", "The tower and the hive" — and a
    library reads badly when every third title shouts or whispers."""

    @staticmethod
    def polished(gateway, library, work, plain):
        """`work` is how Open Library spells it, `plain` how the file
        does. What comes back is what the reader ends up seeing."""
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title=f"McCaffrey, Anne - Pern 01 - {plain}",
                    author="Anne McCaffrey").build(library / "a.epub")
        gateway.reindex()
        works(gateway, "Anne McCaffrey", [work])
        titler(gateway).run(apply=True)
        return title_of(gateway, book_named(gateway, plain))

    def test_shouting_is_calmed_down(self, client, library, gateway):
        assert self.polished(gateway, library, "SOMETHING WICKED",
                             "Something Wicked") == "Something Wicked"

    def test_a_sentence_case_title_is_capitalised(self, client, library,
                                                  gateway):
        assert self.polished(gateway, library, "Something wicked",
                             "Something Wicked") == "Something Wicked"

    def test_small_words_stay_small(self, client, library, gateway):
        assert self.polished(gateway, library, "the tower and the hive",
                             "The Tower And The Hive") == \
            "The Tower and the Hive"

    def test_a_title_already_written_properly_is_left_alone(self, client,
                                                            library, gateway):
        assert self.polished(gateway, library, "Something of the McGuffin",
                             "Something Of The McGuffin") == \
            "Something of the McGuffin"

    def test_a_series_tag_is_not_part_of_the_title(self, client, library,
                                                   gateway):
        assert self.polished(gateway, library, "Damia (Pern #8.5)",
                             "Damia") == "Damia"


class TestANumberInFront:
    """42 titles in the real library open with their position in a
    series: "1 - A Wizard In Mind", "01 Lord Foul's Bane",
    "11.Scion of Cyador". The number belongs to the shelf, not the book.

    Two of them are real titles though — "10 is Better Than 01" and
    "50 Philosophy Ideas You Really Need to Know" — so the number alone
    is not enough to go on. This needs no provider: it is only reading
    what is already there.
    """

    @staticmethod
    def cleaned(gateway, library, title):
        """The title as a reader ends up seeing it. Books the shared
        library fixture leaves in subfolders are still there, so this
        asks about one book rather than the first row."""
        for existing in library.rglob("*.epub"):
            existing.unlink()
        EpubBuilder(title=title, author="Somebody Else").build(library / "a.epub")
        gateway.reindex()
        titler(gateway).run(apply=True)
        return gateway.database.connection.execute(
            """SELECT m.title FROM metadata m
                 JOIN book_author ba ON ba.book_id = m.book_id
                 JOIN author a ON a.id = ba.author_id
                WHERE a.name = 'Somebody Else'""").fetchone()["title"]

    def test_a_number_and_a_dash_come_off(self, client, library, gateway):
        assert self.cleaned(gateway, library, "1 - A Wizard In Mind") == \
            "A Wizard In Mind"

    def test_a_number_and_a_full_stop_come_off(self, client, library, gateway):
        assert self.cleaned(gateway, library, "11.Scion of Cyador") == \
            "Scion of Cyador"

    def test_a_padded_number_and_a_space_come_off(self, client, library,
                                                  gateway):
        assert self.cleaned(gateway, library, "01 Lord Foul's Bane") == \
            "Lord Foul's Bane"

    def test_an_unpadded_number_and_a_space_are_left_alone(self, client,
                                                           library, gateway):
        # "10 is Better Than 01" is the title. So is "50 Philosophy
        # Ideas You Really Need to Know".
        assert self.cleaned(gateway, library, "10 is Better Than 01") == \
            "10 is Better Than 01"
        assert self.cleaned(
            gateway, library, "50 Philosophy Ideas You Really Need to Know"
        ) == "50 Philosophy Ideas You Really Need to Know"

    def test_a_title_that_is_a_number_is_left_alone(self, client, library,
                                                    gateway):
        assert self.cleaned(gateway, library, "1984") == "1984"

    def test_a_number_that_runs_into_the_title_is_left_alone(self, client,
                                                             library, gateway):
        # "20.000 Leagues Under the Sea" is not book 20 of anything.
        assert self.cleaned(
            gateway, library, "20.000 Leagues Under the Sea"
        ) == "20.000 Leagues Under the Sea"

    def test_the_original_is_still_kept(self, client, library, gateway):
        self.cleaned(gateway, library, "1 - A Wizard In Mind")
        assert gateway.database.connection.execute(
            """SELECT m.file_title FROM metadata m
                 JOIN book_author ba ON ba.book_id = m.book_id
                 JOIN author a ON a.id = ba.author_id
                WHERE a.name = 'Somebody Else'"""
        ).fetchone()["file_title"] == "1 - A Wizard In Mind"

    def test_a_work_from_the_bibliography_still_wins(self, client, library,
                                                     gateway):
        # Open Library knows the real spelling; a strip is the fallback.
        for existing in library.rglob("*.epub"):
            existing.unlink()
        EpubBuilder(title="02 The Illearth War",
                    author="Stephen R. Donaldson").build(library / "a.epub")
        gateway.reindex()
        works(gateway, "Stephen R. Donaldson", ["the illearth war"])
        titler(gateway).run(apply=True)
        assert title_of(gateway, book_named(gateway, "Illearth")) == \
            "The Illearth War"


class TestNothingTouchesTheFiles:
    def test_correcting_does_not_write_to_the_library(self, cussler, gateway,
                                                      library):
        before = {p: p.stat().st_mtime_ns for p in library.rglob("*.epub")}
        titler(gateway).run(apply=True)
        after = {p: p.stat().st_mtime_ns for p in library.rglob("*.epub")}
        assert before == after, "a book file was modified"

    def test_no_file_is_renamed(self, cussler, gateway, library):
        before = sorted(p.name for p in library.rglob("*.epub"))
        titler(gateway).run(apply=True)
        assert sorted(p.name for p in library.rglob("*.epub")) == before
