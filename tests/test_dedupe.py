"""Merging authors that are really the same person.

The real library holds "Stephen R. Donaldson" (18 books), "Stephen
Donaldson" (3), "Stephan Donaldson" (1) and "Stephan R. Donaldson" (1) —
two spellings crossed with a middle initial. Also "Ursula K. Le Guin"
and "Ursula K. LeGuin".

Merging is destructive, so the bar is high: either Open Library resolves
both spellings to the *same* author, or the names differ only by spacing
and punctuation. Anything else is reported for a human and left alone —
fusing two real people is much worse than leaving a duplicate.
"""
from __future__ import annotations

import pytest

from epub_dedupe import AuthorDeduplicator
from tests.epub_fixtures import EpubBuilder


class StubResolver:
    """Stands in for Open Library. Maps a name to (key, canonical name)."""

    def __init__(self, answers: dict, error=None):
        self.answers = answers
        self.error = error
        self.asked: list[str] = []

    def find_author(self, name):
        self.asked.append(name)
        if self.error:
            raise self.error
        found = self.answers.get(name)
        return {"key": found[0], "name": found[1]} if found else None


@pytest.fixture
def donaldsons(client, library, gateway):
    for existing in library.glob("*.epub"):
        existing.unlink()
    for i, name in enumerate([
        "Stephen R. Donaldson", "Stephen R. Donaldson", "Stephen R. Donaldson",
        "Stephen Donaldson", "Stephan Donaldson",
    ]):
        EpubBuilder(title=f"Book {i}", author=name).build(library / f"b{i}.epub")
    EpubBuilder(title="Elsewhere", author="Jane Austen").build(library / "j.epub")
    gateway.reindex()
    return client


OPEN_LIBRARY = {
    "Stephen R. Donaldson": ("OL227779A", "Stephen R. Donaldson"),
    "Stephen Donaldson": ("OL227779A", "Stephen R. Donaldson"),
    "Stephan Donaldson": ("OL227779A", "Stephen R. Donaldson"),
    "Jane Austen": ("OL21594A", "Jane Austen"),
}


def deduper(gateway, answers=None, error=None):
    return AuthorDeduplicator(
        gateway.database, StubResolver(answers or OPEN_LIBRARY, error)
    )


def authors(gateway):
    return {r["name"]: r["id"] for r in gateway.database.connection.execute(
        "SELECT a.id, a.name FROM author a"
        " JOIN book_author ba ON ba.author_id = a.id GROUP BY a.id")}


class TestFindingDuplicates:
    def test_similar_names_are_grouped(self, donaldsons, gateway):
        plan, _ = deduper(gateway).plan()
        group = next(g for g in plan if "Donaldson" in g["canonical"])
        assert len(group["merge"]) >= 2

    def test_the_canonical_spelling_comes_from_open_library(
        self, donaldsons, gateway
    ):
        plan, _ = deduper(gateway).plan()
        group = next(g for g in plan if "Donaldson" in g["canonical"])
        assert group["canonical"] == "Stephen R. Donaldson"
        assert group["key"] == "OL227779A"

    def test_an_author_with_no_twin_is_left_out_of_the_plan(
        self, donaldsons, gateway
    ):
        plan, _ = deduper(gateway).plan()
        assert not any("Austen" in g["canonical"] for g in plan)

    def test_planning_changes_nothing(self, donaldsons, gateway):
        before = authors(gateway)
        deduper(gateway).plan()
        assert authors(gateway) == before


class TestMerging:
    def test_the_duplicates_disappear(self, donaldsons, gateway):
        deduper(gateway).run(apply=True)
        names = [n for n in authors(gateway) if "Donaldson" in n]
        assert names == ["Stephen R. Donaldson"]

    def test_every_book_follows_the_surviving_author(self, donaldsons, gateway):
        deduper(gateway).run(apply=True)
        count = gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM book_author ba JOIN author a"
            " ON a.id = ba.author_id WHERE a.name = 'Stephen R. Donaldson'"
        ).fetchone()["n"]
        assert count == 5

    def test_no_book_is_left_pointing_at_a_deleted_author(
        self, donaldsons, gateway
    ):
        deduper(gateway).run(apply=True)
        orphans = gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM book_author ba"
            " LEFT JOIN author a ON a.id = ba.author_id WHERE a.id IS NULL"
        ).fetchone()["n"]
        assert orphans == 0

    def test_the_sort_name_is_recomputed(self, donaldsons, gateway):
        deduper(gateway).run(apply=True)
        row = gateway.database.connection.execute(
            "SELECT sort_name FROM author WHERE name = 'Stephen R. Donaldson'"
        ).fetchone()
        assert row["sort_name"] == "donaldson, stephen r."

    def test_a_report_says_what_happened(self, donaldsons, gateway):
        report = deduper(gateway).run(apply=True)
        assert report["merged"] >= 2
        assert report["groups"] >= 1

    def test_running_twice_is_harmless(self, donaldsons, gateway):
        deduper(gateway).run(apply=True)
        second = deduper(gateway).run(apply=True)
        assert second["merged"] == 0

    def test_a_dry_run_changes_nothing(self, donaldsons, gateway):
        before = authors(gateway)
        report = deduper(gateway).run(apply=False)
        assert authors(gateway) == before
        assert report["merged"] == 0
        assert report["would_merge"] >= 2


class TestChoosingTheSpelling:
    def test_proper_case_beats_shouting_even_with_fewer_books(
        self, client, library, gateway
    ):
        # "DIANE CAREY" had more books than "Diane Carey" and was winning
        # on count alone, which is not a spelling anyone wants.
        for existing in library.glob("*.epub"):
            existing.unlink()
        for i in range(3):
            EpubBuilder(title=f"Loud {i}", author="DIANE CAREY").build(
                library / f"loud{i}.epub")
        EpubBuilder(title="Quiet", author="Diane Carey").build(
            library / "quiet.epub")
        gateway.reindex()
        deduper(gateway, {}).run(apply=True)
        assert "Diane Carey" in authors(gateway)
        assert "DIANE CAREY" not in authors(gateway)

    def test_all_lower_case_loses_too(self, client, library, gateway):
        for existing in library.glob("*.epub"):
            existing.unlink()
        for i in range(3):
            EpubBuilder(title=f"Low {i}", author="jane austen").build(
                library / f"low{i}.epub")
        EpubBuilder(title="Proper", author="Jane Austen").build(
            library / "proper.epub")
        gateway.reindex()
        deduper(gateway, {}).run(apply=True)
        assert "Jane Austen" in authors(gateway)

    def test_punctuation_comes_from_open_library(self, client, library, gateway):
        # "Raymond E Feist" had more books than "Raymond E. Feist" and so
        # won on count. Open Library knows where the full stops go.
        for existing in library.glob("*.epub"):
            existing.unlink()
        for i in range(3):
            EpubBuilder(title=f"Riftwar {i}", author="Raymond E Feist").build(
                library / f"r{i}.epub")
        EpubBuilder(title="Magician", author="Raymond E. Feist").build(
            library / "m.epub")
        gateway.reindex()
        # Open Library does not hold "Raymond E Feist" at all, so the
        # spelling has to come from the other row in the group.
        answers = {"Raymond E. Feist": ("OL22105A", "Raymond E. Feist")}
        deduper(gateway, answers).run(apply=True)
        assert "Raymond E. Feist" in authors(gateway)

    def test_a_worse_spelling_from_open_library_is_ignored(self, client,
                                                            library, gateway):
        # Open Library really does hold "J.k. Rowling". Ours is better.
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="A", author="J.K. Rowling").build(library / "a.epub")
        EpubBuilder(title="B", author="J. K. Rowling").build(library / "b.epub")
        gateway.reindex()
        answers = {"J.K. Rowling": ("OL23919A", "J.k. Rowling"),
                   "J. K. Rowling": ("OL23919A", "J.k. Rowling")}
        deduper(gateway, answers).run(apply=True)
        assert "J.k. Rowling" not in authors(gateway)

    def test_a_different_name_from_open_library_is_ignored(self, client,
                                                           library, gateway):
        # Only spelling may come from the provider. If it answers with
        # somebody else, the local name stands.
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="A", author="Ursula K. Le Guin").build(library / "a.epub")
        EpubBuilder(title="B", author="Ursula K. LeGuin").build(library / "b.epub")
        gateway.reindex()
        answers = {"Ursula K. Le Guin": ("OL1A", "Someone Else Entirely"),
                   "Ursula K. LeGuin": ("OL1A", "Someone Else Entirely")}
        deduper(gateway, answers).run(apply=True)
        assert "Someone Else Entirely" not in authors(gateway)

    def test_a_provider_failure_still_merges_on_spacing(self, client, library,
                                                        gateway):
        from epub_providers import ProviderError
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="A", author="Ursula K. Le Guin").build(library / "a.epub")
        EpubBuilder(title="B", author="Ursula K. LeGuin").build(library / "b.epub")
        gateway.reindex()
        report = deduper(gateway, error=ProviderError("down")).run(apply=True)
        assert report["merged"] == 1

    def test_open_library_still_wins_when_it_knows(self, donaldsons, gateway):
        # A provider answer beats a spelling heuristic.
        plan, _ = deduper(gateway).plan()
        group = next(g for g in plan if "Donaldson" in g["canonical"])
        assert group["canonical"] == "Stephen R. Donaldson"


class TestNotMerging:
    def test_different_people_are_never_fused(self, client, library, gateway):
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="A", author="John Williams").build(library / "a.epub")
        EpubBuilder(title="B", author="John Wyndham").build(library / "b.epub")
        gateway.reindex()
        answers = {"John Williams": ("OL1A", "John Williams"),
                   "John Wyndham": ("OL2A", "John Wyndham")}
        report = deduper(gateway, answers).run(apply=True)
        assert report["merged"] == 0

    def test_open_library_disagreeing_stops_a_merge(self, client, library,
                                                    gateway):
        # Names look alike, but they are two different authors.
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="A", author="David Eddings").build(library / "a.epub")
        EpubBuilder(title="B", author="David Edding").build(library / "b.epub")
        gateway.reindex()
        answers = {"David Eddings": ("OL1A", "David Eddings"),
                   "David Edding": ("OL2A", "David Edding")}
        assert deduper(gateway, answers).run(apply=True)["merged"] == 0

    def test_spacing_alone_merges_without_asking_anyone(self, client, library,
                                                       gateway):
        # "LeGuin" and "Le Guin" are the same string to a reader, so this
        # needs no provider at all.
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="A", author="Ursula K. Le Guin").build(library / "a.epub")
        EpubBuilder(title="B", author="Ursula K. LeGuin").build(library / "b.epub")
        gateway.reindex()
        report = deduper(gateway, {}).run(apply=True)   # provider knows nothing
        assert report["merged"] == 1

    def test_a_provider_failure_leaves_everything_alone(self, donaldsons,
                                                       gateway):
        from epub_providers import ProviderError
        before = authors(gateway)
        report = deduper(gateway, error=ProviderError("down")).run(apply=True)
        assert authors(gateway) == before
        assert report["merged"] == 0


class TestTypos:
    """A misspelling has a signature: Open Library knows the real name
    and has never heard of the other one. "Issac Asimov", "Peirs
    Anthony", "Neal Gaiman" and "J.R.R. Tolkein" are all in the real
    library, and none of them exist as authors anywhere."""

    @staticmethod
    def two(library, gateway, right, wrong, copies=2):
        for existing in library.glob("*.epub"):
            existing.unlink()
        for i in range(copies):
            EpubBuilder(title=f"Real {i}", author=right).build(
                library / f"r{i}.epub")
        EpubBuilder(title="Typo", author=wrong).build(library / "w.epub")
        gateway.reindex()

    def test_a_name_open_library_never_heard_of_is_a_typo(self, client,
                                                          library, gateway):
        self.two(library, gateway, "Isaac Asimov", "Issac Asimov")
        answers = {"Isaac Asimov": ("OL34221A", "Isaac Asimov")}  # typo: absent
        report = deduper(gateway, answers).run(apply=True)
        assert report["merged"] == 1
        assert "Issac Asimov" not in authors(gateway)

    def test_the_misspelling_can_be_the_one_with_more_books(self, client,
                                                             library, gateway):
        # The real library holds three "Robert Aspirin" and one "Robert
        # Asprin". Book count picks the survivor, but a spelling Open
        # Library has never heard of is not the one to keep.
        self.two(library, gateway, "Robert Aspirin", "Robert Asprin", copies=3)
        answers = {"Robert Asprin": ("OL347792A", "Robert Asprin")}
        report = deduper(gateway, answers).run(apply=True)
        assert report["merged"] == 1
        assert "Robert Aspirin" not in authors(gateway)
        assert "Robert Asprin" in authors(gateway)

    def test_every_book_follows_the_swapped_keeper(self, client, library,
                                                   gateway):
        self.two(library, gateway, "Robert Aspirin", "Robert Asprin", copies=3)
        answers = {"Robert Asprin": ("OL347792A", "Robert Asprin")}
        deduper(gateway, answers).run(apply=True)
        count = gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM book_author ba JOIN author a"
            " ON a.id = ba.author_id WHERE a.name = 'Robert Asprin'"
        ).fetchone()["n"]
        assert count == 4

    def test_two_names_it_knows_are_never_fused(self, client, library, gateway):
        # Both real people, and similar enough to be asked about.
        self.two(library, gateway, "Stephen King", "Stephen Hawking")
        answers = {"Stephen King": ("OL19981A", "Stephen King"),
                   "Stephen Hawking": ("OL2675573A", "Stephen Hawking")}
        assert deduper(gateway, answers).run(apply=True)["merged"] == 0

    def test_an_unknown_name_that_is_not_close_enough_is_left(self, client,
                                                              library, gateway):
        # "L. E. Modesitt Jr." is unknown to Open Library, but a suffix
        # is not a spelling mistake and merging it would lose the "Jr.".
        self.two(library, gateway, "L. E. Modesitt", "L. E. Modesitt Jr.")
        answers = {"L. E. Modesitt": ("OL14735273A", "L. E. Modesitt")}
        assert deduper(gateway, answers).run(apply=True)["merged"] == 0

    def test_a_typo_is_not_merged_when_the_real_name_is_unknown_too(
        self, client, library, gateway
    ):
        # With no provider answer at all there is nothing to be sure of.
        self.two(library, gateway, "Ferdinand Quimby", "Ferdinand Quinby")
        assert deduper(gateway, {}).run(apply=True)["merged"] == 0

    def test_the_books_follow_the_correct_spelling(self, client, library,
                                                   gateway):
        self.two(library, gateway, "Neil Gaiman", "Neal Gaiman")
        answers = {"Neil Gaiman": ("OL53305A", "Neil Gaiman")}
        deduper(gateway, answers).run(apply=True)
        assert authors(gateway)["Neil Gaiman"]
        count = gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM book_author ba JOIN author a"
            " ON a.id = ba.author_id WHERE a.name = 'Neil Gaiman'"
        ).fetchone()["n"]
        assert count == 3


class TestSurvivingARescan:
    """A merge is a correction, and the indexer only keeps a correction
    when provenance says the file no longer owns the field. Without this
    the next time a file is touched it is filed under the old spelling
    again."""

    def test_a_merged_book_records_where_its_author_came_from(self, donaldsons,
                                                              gateway):
        deduper(gateway).run(apply=True)
        rows = gateway.database.connection.execute(
            "SELECT origin, source FROM field_provenance WHERE field = 'authors'"
        ).fetchall()
        assert rows, "nothing would stop a rescan undoing the merge"
        assert {r["origin"] for r in rows} == {"provider"}

    def test_a_book_that_did_not_move_is_left_alone(self, donaldsons, gateway):
        # Jane Austen was never part of a merge.
        deduper(gateway).run(apply=True)
        austen = gateway.database.connection.execute(
            """SELECT COUNT(*) n FROM field_provenance f
                 JOIN book_author ba ON ba.book_id = f.book_id
                 JOIN author a ON a.id = ba.author_id
                WHERE f.field = 'authors' AND a.name = 'Jane Austen'"""
        ).fetchone()["n"]
        assert austen == 0

    def test_a_dry_run_records_nothing(self, donaldsons, gateway):
        deduper(gateway).run(apply=False)
        assert gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM field_provenance WHERE field = 'authors'"
        ).fetchone()["n"] == 0


class TestReportingWhatWasNotMerged:
    """The bar for merging is high, so what it refuses has to be visible
    or it is simply lost: the real library still holds "Issac Asimov"
    beside "Isaac Asimov"."""

    def test_a_refused_look_alike_is_reported(self, client, library, gateway):
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title="A", author="Isaac Asimov").build(library / "a.epub")
        EpubBuilder(title="B", author="Issac Asimov").build(library / "b.epub")
        gateway.reindex()
        report = deduper(gateway, {}).run(apply=True)
        assert report["merged"] == 0
        names = {n for group in report["unresolved"] for n in group}
        assert {"Isaac Asimov", "Issac Asimov"} <= names

    def test_a_merged_group_is_not_also_reported(self, donaldsons, gateway):
        report = deduper(gateway).run(apply=True)
        assert not any("Donaldson" in n
                       for group in report["unresolved"] for n in group)


class TestTheEndpoints:
    def test_dedupe_defaults_to_a_dry_run(self, donaldsons):
        # It changes the index, so seeing the plan first is the point.
        before = donaldsons.get("/api/authors").json()["authors"]
        report = donaldsons.post("/api/authors/dedupe").json()
        assert report["merged"] == 0
        assert donaldsons.get("/api/authors").json()["authors"] == before

    def test_repair_defaults_to_a_dry_run(self, donaldsons):
        assert donaldsons.post("/api/books/repair-authors").json()["repaired"] == 0
