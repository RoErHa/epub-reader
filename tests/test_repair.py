"""Books whose "author" is really their title.

The real library has 23 books where the author field holds the title
exactly, and ~190 more where it holds the title with a list-number
prefix removed ("1 - A Wizard In Mind" / "A Wizard In Mind"). The fix is
to look the title up and take the real author from a provider.

Nothing here touches a file. The library is opened read-only; only the
database changes.
"""
from __future__ import annotations

import pytest

from epub_match import Candidate
from epub_repair import TitleAsAuthorRepair
from tests.epub_fixtures import EpubBuilder


class StubProvider:
    def __init__(self, by_query=None, error=None):
        self.by_query = by_query or {}
        self.error = error
        self.queries: list[str] = []

    def find_books(self, title, author=None, limit=8):
        self.queries.append(title)
        if self.error:
            raise self.error
        return self.by_query.get(title, [])


@pytest.fixture
def broken(client, library, gateway):
    for existing in library.glob("*.epub"):
        existing.unlink()
    # The author field holds the title.
    EpubBuilder(title="Annihilation", author="Annihilation").build(
        library / "a.epub")
    # ...and with a list-number prefix on the title.
    EpubBuilder(title="1 - A Wizard In Mind", author="A Wizard In Mind").build(
        library / "b.epub")
    # A perfectly good book, which must be left alone.
    EpubBuilder(title="Dune", author="Frank Herbert").build(library / "c.epub")
    gateway.reindex()
    return client


ANSWERS = {
    "annihilation": [Candidate(title="Annihilation",
                               authors=["Jeff VanderMeer"],
                               first_published=2014, source="stub")],
    "wizard in mind": [Candidate(title="A Wizard in Mind",
                                 authors=["Christopher Stasheff"],
                                 first_published=1995, source="stub")],
}


def repairer(gateway, answers=None, error=None):
    return TitleAsAuthorRepair(
        gateway.database, [StubProvider(answers if answers is not None else ANSWERS,
                                        error)]
    )


def author_of(gateway, title):
    row = gateway.database.connection.execute(
        """SELECT a.name FROM metadata m
             JOIN book_author ba ON ba.book_id = m.book_id
             JOIN author a ON a.id = ba.author_id
            WHERE m.title = ?""", (title,)).fetchone()
    return row["name"] if row else None


class TestFinding:
    def test_an_author_equal_to_the_title_is_found(self, broken, gateway):
        found = {b["title"] for b in repairer(gateway).find()}
        assert "Annihilation" in found

    def test_a_number_prefix_does_not_hide_it(self, broken, gateway):
        found = {b["title"] for b in repairer(gateway).find()}
        assert "1 - A Wizard In Mind" in found

    def test_a_real_author_is_not_flagged(self, broken, gateway):
        found = {b["title"] for b in repairer(gateway).find()}
        assert "Dune" not in found

    def test_finding_changes_nothing(self, broken, gateway):
        repairer(gateway).find()
        assert author_of(gateway, "Annihilation") == "Annihilation"


class TestRepairing:
    def test_the_real_author_replaces_the_title(self, broken, gateway):
        repairer(gateway).run(apply=True)
        assert author_of(gateway, "Annihilation") == "Jeff VanderMeer"

    def test_the_lookup_uses_a_cleaned_title(self, broken, gateway):
        provider = StubProvider(ANSWERS)
        TitleAsAuthorRepair(gateway.database, [provider]).run(apply=True)
        assert "wizard in mind" in provider.queries

    def test_a_good_book_is_untouched(self, broken, gateway):
        repairer(gateway).run(apply=True)
        assert author_of(gateway, "Dune") == "Frank Herbert"

    def test_the_old_author_row_stops_being_credited(self, broken, gateway):
        repairer(gateway).run(apply=True)
        rows = gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM author a JOIN book_author ba"
            " ON ba.author_id = a.id WHERE a.name = 'Annihilation'"
        ).fetchone()["n"]
        assert rows == 0

    def test_where_it_came_from_is_recorded(self, broken, gateway):
        repairer(gateway).run(apply=True)
        book = gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title = 'Annihilation'"
        ).fetchone()["book_id"]
        row = gateway.database.connection.execute(
            "SELECT origin, source FROM field_provenance"
            " WHERE book_id = ? AND field = 'authors'", (book,)).fetchone()
        assert row["origin"] == "provider"

    def test_a_report_says_what_happened(self, broken, gateway):
        report = repairer(gateway).run(apply=True)
        assert report["repaired"] == 2
        assert report["found"] == 2

    def test_a_dry_run_changes_nothing(self, broken, gateway):
        report = repairer(gateway).run(apply=False)
        assert report["repaired"] == 0
        assert report["would_repair"] == 2
        assert author_of(gateway, "Annihilation") == "Annihilation"

    def test_running_twice_is_harmless(self, broken, gateway):
        repairer(gateway).run(apply=True)
        assert repairer(gateway).run(apply=True)["repaired"] == 0


class TestTheAuthorInTheTitle:
    """Most of these filenames name the author themselves —
    "Cussler, Clive - Dirk Pitt 01 - Pacific Vortex". That is far better
    evidence than a title lookup, which for "Second Chance" answers with
    a book James Patterson did not write."""

    @staticmethod
    def one(library, gateway, title, alongside=None):
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title=title, author=title.split(" - ")[-1]).build(
            library / "a.epub")
        if alongside:
            # A book by the same author that is filed properly. Written
            # given-name-first, a name only counts when the library
            # already knows the surname — otherwise "Star Trek" would
            # read as a person.
            EpubBuilder(title="Something Else", author=alongside).build(
                library / "b.epub")
        gateway.reindex()

    def test_a_candidate_that_agrees_with_the_title_is_taken(self, client,
                                                             library, gateway):
        self.one(library, gateway, "Cussler, Clive - Pacific Vortex")
        answers = {"pacific vortex": [Candidate(title="Pacific Vortex",
                                                authors=["Clive Cussler"],
                                                source="stub")]}
        repairer(gateway, answers).run(apply=True)
        assert author_of(gateway, "Cussler, Clive - Pacific Vortex") == \
            "Clive Cussler"

    def test_a_candidate_that_contradicts_the_title_is_refused(self, client,
                                                               library, gateway):
        # The real failure: "Second Chance" is a common title, and the
        # lookup answers with a book Patterson did not write. The file
        # is right, so the file wins.
        self.one(library, gateway, "James Patterson - Club 02 - Second Chance",
                 alongside="James Patterson")
        answers = {"second chance": [Candidate(title="Second Chance",
                                               authors=["Nyengi Koin"],
                                               source="stub")]}
        repairer(gateway, answers).run(apply=True)
        assert author_of(
            gateway, "James Patterson - Club 02 - Second Chance"
        ) == "James Patterson"

    def test_a_name_the_library_does_not_know_does_not_veto(self, client,
                                                             library, gateway):
        # Without a second book to vouch for it, "Deborah Chester" is
        # indistinguishable from a series name, so the lookup stands.
        self.one(library, gateway, "Deborah Chester - Reign of Shadows")
        answers = {"reign of shadows": [Candidate(title="Reign of Shadows",
                                                  authors=["Sophie Jordan"],
                                                  source="stub")]}
        repairer(gateway, answers).run(apply=True)
        assert author_of(gateway, "Deborah Chester - Reign of Shadows") == \
            "Sophie Jordan"

    def test_a_misspelled_surname_still_matches(self, client, library, gateway):
        # "Aspirin, Robert Lynn" is how the file spells Robert Asprin.
        self.one(library, gateway, "Aspirin, Robert Lynn - Hit Or Myth")  # comma form
        answers = {"hit or myth": [Candidate(title="Hit or Myth",
                                             authors=["Robert Asprin"],
                                             source="stub")]}
        repairer(gateway, answers).run(apply=True)
        assert author_of(gateway, "Aspirin, Robert Lynn - Hit Or Myth") == \
            "Robert Asprin"

    def test_the_providers_spelling_wins_over_the_files(self, client, library,
                                                        gateway):
        self.one(library, gateway, "C J Cherryh - Brothers of Earth",
                 alongside="C. J. Cherryh")
        answers = {"brothers of earth": [Candidate(title="Brothers of Earth",
                                                   authors=["C. J. Cherryh"],
                                                   source="stub")]}
        repairer(gateway, answers).run(apply=True)
        assert author_of(gateway, "C J Cherryh - Brothers of Earth") == \
            "C. J. Cherryh"

    def test_a_plain_title_is_not_read_as_a_name(self, client, library,
                                                 gateway):
        # "The Lost World" is a title, not a Mr World, and there is no
        # "Author - " in front of it to suggest otherwise.
        self.one(library, gateway, "The Lost World")
        answers = {"lost world": [Candidate(title="The Lost World",
                                            authors=["Arthur Conan Doyle"],
                                            source="stub")]}
        repairer(gateway, answers).run(apply=True)
        assert author_of(gateway, "The Lost World") == "Arthur Conan Doyle"

    def test_a_title_with_no_name_in_it_uses_the_lookup_alone(self, client,
                                                              library, gateway):
        self.one(library, gateway, "Annihilation")
        repairer(gateway).run(apply=True)
        assert author_of(gateway, "Annihilation") == "Jeff VanderMeer"

    def test_a_series_name_is_not_mistaken_for_an_author(self, client, library,
                                                         gateway):
        # "Star Trek" is not a person, so it must not veto the lookup.
        self.one(library, gateway, "Star Trek - TNG 01 - Ghost Ship")
        answers = {"ghost ship": [Candidate(title="Ghost Ship",
                                            authors=["Diane Carey"],
                                            source="stub")]}
        repairer(gateway, answers).run(apply=True)
        assert author_of(gateway, "Star Trek - TNG 01 - Ghost Ship") == \
            "Diane Carey"


class TestAFragmentOfTheTitle:
    """The same defect, one step further along: the author field holds
    part of the title rather than all of it. "Jack And Jill" is filed
    under "Jack" and "Jill", "Blue and Gold" under "Blue" and "Gold" —
    the title was split on "and" and the halves became people."""

    @staticmethod
    def build(library, gateway, title, authors, known):
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title=title, author=authors).build(library / "a.epub")
        EpubBuilder(title="Something Else", author=known).build(library / "b.epub")
        gateway.reindex()

    def test_a_title_fragment_is_found(self, client, library, gateway):
        self.build(library, gateway, "James Patterson - Jack And Jill",
                   "Jack", "James Patterson")
        found = {b["title"] for b in repairer(gateway).find()}
        assert "James Patterson - Jack And Jill" in found

    def test_it_is_repaired_from_the_title(self, client, library, gateway):
        self.build(library, gateway, "James Patterson - Jack And Jill",
                   "Jack", "James Patterson")
        repairer(gateway, {}).run(apply=True)
        assert author_of(gateway, "James Patterson - Jack And Jill") == \
            "James Patterson"

    def test_the_author_named_in_the_title_is_not_flagged(self, client,
                                                          library, gateway):
        # The one case where author text inside the title is correct.
        self.build(library, gateway, "James Patterson - Jack And Jill",
                   "James Patterson", "James Patterson")
        assert repairer(gateway).find() == []

    def test_a_co_author_is_not_flagged(self, client, library, gateway):
        # Margaret Ball really did co-write it, and her name is not in
        # the title, so nothing here touches her.
        self.build(library, gateway, "Anne McCaffrey - Acorna 2 - Quest",
                   "Margaret Ball", "Anne McCaffrey")
        assert repairer(gateway).find() == []

    def test_a_title_with_no_known_author_is_not_repaired(self, client,
                                                          library, gateway):
        # "Star Wars" is not a person, so there is nothing to correct
        # this to. It is still reported — it is plainly broken.
        self.build(library, gateway, "Star Wars - Shatterpoint",
                   "Shatterpoint", "Timothy Zahn")
        assert repairer(gateway, {}).run(apply=True)["repaired"] == 0


class TestFallingBackToTheFile:
    """When the lookup cannot confirm anything, a title that names an
    author the library already knows is still better evidence than
    nothing: "C J Cherryh - Faded Sun 1 - Kesrith" found no candidate,
    and the library holds 23 other Cherryh books."""

    @staticmethod
    def two(library, gateway, title, known):
        for existing in library.glob("*.epub"):
            existing.unlink()
        EpubBuilder(title=title, author=title.split(" - ")[-1]).build(
            library / "a.epub")
        EpubBuilder(title="Something Else", author=known).build(
            library / "b.epub")
        gateway.reindex()

    def test_the_name_in_the_title_is_used_when_the_lookup_fails(
        self, client, library, gateway
    ):
        self.two(library, gateway, "C J Cherryh - Faded Sun 1 - Kesrith",
                 "C. J. Cherryh")
        repairer(gateway, {}).run(apply=True)      # provider knows nothing
        assert author_of(gateway, "C J Cherryh - Faded Sun 1 - Kesrith") == \
            "C. J. Cherryh"

    def test_it_takes_the_spelling_the_library_already_uses(self, client,
                                                            library, gateway):
        self.two(library, gateway, "Asprin, Robert Lynn - Blood Ties",
                 "Robert Asprin")
        repairer(gateway, {}).run(apply=True)
        assert author_of(gateway, "Asprin, Robert Lynn - Blood Ties") == \
            "Robert Asprin"

    def test_a_series_name_is_not_used_as_an_author(self, client, library,
                                                    gateway):
        self.two(library, gateway, "Star Wars - Shatterpoint", "Timothy Zahn")
        assert repairer(gateway, {}).run(apply=True)["repaired"] == 0

    def test_an_unknown_name_is_not_invented(self, client, library, gateway):
        # Nothing vouches for "Deborah Chester", so nothing is written.
        self.two(library, gateway, "Deborah Chester - Reign of Shadows",
                 "Timothy Zahn")
        assert repairer(gateway, {}).run(apply=True)["repaired"] == 0

    def test_the_lookup_is_still_preferred_when_it_is_confident(self, client,
                                                                library, gateway):
        self.two(library, gateway, "Aspirin, Robert Lynn - Hit Or Myth",
                 "Robert Asprin")
        answers = {"hit or myth": [Candidate(title="Hit or Myth",
                                             authors=["Robert Lynn Asprin"],
                                             source="stub")]}
        repairer(gateway, answers).run(apply=True)
        assert author_of(gateway, "Aspirin, Robert Lynn - Hit Or Myth") == \
            "Robert Lynn Asprin"


class TestBeingCareful:
    def test_a_title_that_finds_nothing_is_left_alone(self, broken, gateway):
        assert repairer(gateway, {}).run(apply=True)["repaired"] == 0
        assert author_of(gateway, "Annihilation") == "Annihilation"

    def test_a_poor_title_match_is_refused(self, broken, gateway):
        answers = {"annihilation": [Candidate(title="Something Quite Different",
                                              authors=["A Person"],
                                              source="stub")]}
        assert repairer(gateway, answers).run(apply=True)["repaired"] == 0

    def test_a_candidate_with_no_author_is_useless(self, broken, gateway):
        answers = {"annihilation": [Candidate(title="Annihilation",
                                              authors=[], source="stub")]}
        assert repairer(gateway, answers).run(apply=True)["repaired"] == 0

    def test_a_candidate_whose_author_is_also_the_title_is_refused(
        self, broken, gateway
    ):
        # Providers repeat the same mistake; taking it would change nothing
        # while looking like a fix.
        answers = {"annihilation": [Candidate(title="Annihilation",
                                              authors=["Annihilation"],
                                              source="stub")]}
        assert repairer(gateway, answers).run(apply=True)["repaired"] == 0

    def test_a_provider_failure_leaves_everything_alone(self, broken, gateway):
        from epub_providers import ProviderError
        report = repairer(gateway, error=ProviderError("down")).run(apply=True)
        assert report["repaired"] == 0
        assert author_of(gateway, "Annihilation") == "Annihilation"


class TestAProviderThatIsRateLimited:
    """Google Books answers 429 to every unauthenticated request. Asked
    once per book across 157 books, that is 157 lines of the same
    warning."""

    class Limited:
        def __init__(self):
            self.calls = 0

        def find_books(self, title, author=None, limit=8):
            from epub_providers import ProviderError
            self.calls += 1
            raise ProviderError("HTTP Error 429: Too Many Requests")

    def test_it_is_dropped_for_the_rest_of_the_run(self, broken, gateway):
        limited = self.Limited()
        TitleAsAuthorRepair(
            gateway.database, [limited, StubProvider(ANSWERS)]
        ).run(apply=True)
        assert limited.calls == 1, "a rate-limited provider was asked again"

    def test_the_other_providers_still_work(self, broken, gateway):
        report = TitleAsAuthorRepair(
            gateway.database, [self.Limited(), StubProvider(ANSWERS)]
        ).run(apply=True)
        assert report["repaired"] == 2


class TestNothingTouchesTheFiles:
    def test_repairing_does_not_write_to_the_library(self, broken, gateway,
                                                     library):
        before = {p: p.stat().st_mtime_ns for p in library.rglob("*.epub")}
        repairer(gateway).run(apply=True)
        after = {p: p.stat().st_mtime_ns for p in library.rglob("*.epub")}
        assert before == after, "a book file was modified"

    def test_no_file_is_renamed_or_removed(self, broken, gateway, library):
        before = sorted(p.name for p in library.rglob("*.epub"))
        repairer(gateway).run(apply=True)
        assert sorted(p.name for p in library.rglob("*.epub")) == before
