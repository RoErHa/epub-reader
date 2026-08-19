"""Titles that were cut off, and the provider that can finish them.

    Anthony, Piers - Jason Striker 02 - Bamboo Bloodbath & Ninj.pdf
    Anthony, Piers - The Caterpillars Questi (with Philip Jose Farmer).pdf

Somebody's filesystem or unzipper truncated these years ago. §12.3's
tier 1: the damage is always at the **end**, so trimming back to the last
complete word turns a dead query live. Probed 2026-08-19 before any of
this existed:

    "Bamboo Bloodbath & Ninj"  -> nothing found
    "Bamboo Bloodbath"         -> The Bamboo Bloodbath, Piers Anthony, 1974
    "The Caterpillars Questi"  -> nothing found
    "The Caterpillars"         -> The caterpillar's question, 1992

The safety property is the whole design:

> **The answer must COMPLETE our fragment, word for word.** Everything
> but our last word must match exactly, and our last word must be the
> beginning of theirs. A rule that can only ever *lengthen* a name
> cannot swap in a different book.

Which is also why one of the two examples above is **refused**, and
should be: `Bamboo Bloodbath & Ninj` names two novels, and Open Library
holds one of them, so *The Bamboo Bloodbath* is shorter than what we
have. Completing a fragment is not the same as replacing it with half of
itself.

No test here touches the network — the provider is injected, as
everything in §10.11 is.
"""
from __future__ import annotations

import pytest

from epub_fragments import TitlesFromFragments
from epub_match import Candidate
from tests.epub_fixtures import EpubBuilder


class FakeProvider:
    """Answers only what it has been told, and remembers what it was
    asked — the asking is half of what these tests are about."""

    def __init__(self, answers: dict[str, list[Candidate]] | None = None):
        self.answers = answers or {}
        self.asked: list[tuple[str, str | None]] = []

    def find_books(self, title, author=None, limit=8):
        self.asked.append((title, author))
        return self.answers.get(title.casefold(), [])


def works(title, author="Piers Anthony", year=1974):
    return [Candidate(title=title, authors=[author], first_published=year,
                      source="openlibrary")]


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def mender(gateway, provider):
    return TitlesFromFragments(gateway.database, provider)


def clear(library):
    for old in library.rglob("*"):
        if old.is_file():
            old.unlink()


def book(library, title, author="Piers Anthony"):
    EpubBuilder(title=title, author=author).build(
        library / f"{abs(hash(title))}.epub")


def title_of(gateway, like):
    row = gateway.database.connection.execute(
        "SELECT title, file_title FROM metadata WHERE title LIKE ? OR file_title LIKE ?",
        (f"%{like}%", f"%{like}%")).fetchone()
    return row["title"] if row else None


class TestTheCompletionRule:
    """The rule on its own, with no database in the way."""

    @pytest.mark.parametrize("ours,theirs", [
        ("The Caterpillars Questi", "The caterpillar's question"),
        ("Fahrenheit 45", "Fahrenheit 451"),
        ("A Wizard of Earthse", "A Wizard of Earthsea"),
        # An article on their side only, which is how catalogues write
        # it: "Fahrenheit 45" against "The Fahrenheit 451".
        ("Fahrenheit 45", "The Fahrenheit 451"),
    ])
    def test_a_completion_is_accepted(self, ours, theirs):
        assert TitlesFromFragments.completes(ours, theirs)

    @pytest.mark.parametrize("ours,theirs,why", [
        ("Bamboo Bloodbath & Ninj", "The Bamboo Bloodbath",
         "theirs is SHORTER — it is half of what we hold, not a completion"),
        ("The Left Hand of Darkness", "The Right Hand of Darkness",
         "an earlier word differs"),
        ("Dune", "Dune Messiah",
         "our last word is complete, so nothing was cut off"),
        ("Mute", "God on Mute",
         "theirs does not begin where ours does"),
        ("Chthon", "Phthor", "a different book entirely"),
        ("The Ring", "The Rings of Saturn",
         ("one word alone corroborates nothing — 'ring' would become "
          "'rings' and carry the book off to a different author")),
        ("Dispossesse", "The Dispossessed",
         "one word again: a completion needs a whole word beside it"),
    ])
    def test_a_non_completion_is_refused(self, ours, theirs, why):
        assert not TitlesFromFragments.completes(ours, theirs), why

    def test_an_identical_title_is_not_a_completion(self):
        """Nothing was cut off, so there is nothing to repair — and
        rewriting a title with itself would burn a provenance row."""
        assert not TitlesFromFragments.completes("Dead Morn", "Dead Morn")


class TestAskingTheProvider:
    def test_the_whole_title_is_tried_first(self, client, library, gateway,
                                            mender, provider):
        clear(library)
        book(library, "Dead Morn")
        gateway.reindex()
        provider.answers["dead morn"] = works("Dead Morn")
        mender.run(apply=True)
        assert provider.asked == [("Dead Morn", "Piers Anthony")]

    def test_the_last_word_is_dropped_only_when_nothing_matched(
            self, client, library, gateway, mender, provider):
        clear(library)
        book(library, "The Caterpillars Questi")
        gateway.reindex()
        provider.answers["the caterpillars"] = works("The caterpillar's question")
        mender.run(apply=True)
        assert provider.asked == [("The Caterpillars Questi", "Piers Anthony"),
                                  ("The Caterpillars", "Piers Anthony")]
        assert title_of(gateway, "Caterpillar") == "The Caterpillar's Question"

    def test_a_book_with_no_author_is_not_asked_about(self, client, library,
                                                      gateway, mender,
                                                      provider):
        """The author is what makes a two-word query safe. Without one,
        "The Caterpillars" is a search for anything."""
        clear(library)
        from tests.pdf_fixtures import PdfBuilder
        PdfBuilder(title=None, author=None, lines=["x"]).build(
            library / "The Caterpillars Questi.pdf")
        gateway.reindex()
        mender.run(apply=True)
        assert provider.asked == []

    def test_a_one_word_title_is_never_trimmed(self, client, library, gateway,
                                               mender, provider):
        """Trimming the only word leaves nothing to ask about."""
        clear(library)
        book(library, "Chthon")
        gateway.reindex()
        mender.run(apply=True)
        assert provider.asked == [("Chthon", "Piers Anthony")]


class TestWhatItWrites:
    def test_the_provider_marketing_is_stripped(self, client, library,
                                                gateway, mender, provider):
        """Open Library really answered: "The Bamboo Bloodbath (#3 in the
        violent new series featuring Jason Striker, Master of Martial
        Arts)". A trailing bracket is the shelf talking."""
        clear(library)
        book(library, "Bamboo Bloodbat")
        gateway.reindex()
        provider.answers["bamboo bloodbat"] = []
        provider.answers["bamboo"] = works(
            "Bamboo Bloodbath (#3 in the violent new series featuring "
            "Jason Striker, Master of Martial Arts)")
        mender.run(apply=True)
        assert title_of(gateway, "Bamboo") == "Bamboo Bloodbath"

    def test_the_name_the_file_gave_is_kept(self, client, library, gateway,
                                            mender, provider):
        clear(library)
        book(library, "The Caterpillars Questi")
        gateway.reindex()
        provider.answers["the caterpillars"] = works("The caterpillar's question")
        mender.run(apply=True)
        row = gateway.database.connection.execute(
            "SELECT file_title FROM metadata WHERE title LIKE '%Caterpillar%'"
        ).fetchone()
        assert row["file_title"] == "The Caterpillars Questi"

    def test_the_series_number_is_left_alone(self, client, library, gateway,
                                             mender, provider):
        """Open Library calls The Bamboo Bloodbath Jason Striker #3; the
        file calls it 02, and both are right — the file is an omnibus of
        novels 3 and 4, and it is the second such volume. Take the title,
        leave the number."""
        clear(library)
        EpubBuilder(title="The Caterpillars Questi", author="Piers Anthony",
                    series="Jason Striker", series_index=2).build(
            library / "js.epub")
        gateway.reindex()
        provider.answers["the caterpillars"] = works("The caterpillar's question")
        mender.run(apply=True)
        row = gateway.database.connection.execute(
            "SELECT m.series_index, s.name FROM metadata m"
            " JOIN series s ON s.id = m.series_id"
            " WHERE m.title LIKE '%Caterpillar%'").fetchone()
        assert (row["name"], row["series_index"]) == ("Jason Striker", 2)

    def test_a_hand_set_title_is_never_touched(self, client, library, gateway,
                                               mender, provider):
        clear(library)
        book(library, "The Caterpillars Questi")
        gateway.reindex()
        book_id = gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title LIKE '%Caterpillar%'"
        ).fetchone()["book_id"]
        from epub_credits import BookCredits
        BookCredits(gateway.database).record(book_id, "title")
        mender.run(apply=True)
        assert provider.asked == []
        assert title_of(gateway, "Caterpillar") == "The Caterpillars Questi"

    def test_a_dry_run_asks_but_writes_nothing(self, client, library, gateway,
                                               mender, provider):
        """Asking is how it knows there is anything to report, and every
        answer is cached — so a dry run costs the same as the real one
        exactly once."""
        clear(library)
        book(library, "The Caterpillars Questi")
        gateway.reindex()
        provider.answers["the caterpillars"] = works("The caterpillar's question")
        report = mender.run()
        assert report["would_mend"] == 1
        assert title_of(gateway, "Caterpillar") == "The Caterpillars Questi"


class TestAskedOnceEver:
    def test_an_answer_is_cached(self, client, library, gateway, mender,
                                 provider):
        clear(library)
        book(library, "Dead Morn")
        gateway.reindex()
        provider.answers["dead morn"] = works("Dead Morn")
        mender.run(apply=True)
        asked = len(provider.asked)
        mender.run(apply=True)
        assert len(provider.asked) == asked, "the second run must ask nothing"

    def test_a_silence_is_cached_too(self, client, library, gateway, mender,
                                     provider):
        """§6.6's rule: a miss is an answer. Asking twice for the same
        silence is somebody's afternoon."""
        clear(library)
        book(library, "Some Unknown Thing")
        gateway.reindex()
        mender.run(apply=True)
        asked = len(provider.asked)
        assert asked > 0
        mender.run(apply=True)
        assert len(provider.asked) == asked


class TestScope:
    def test_only_the_author_asked_for(self, client, library, gateway, mender,
                                       provider):
        clear(library)
        book(library, "The Caterpillars Questi")
        book(library, "Some Other Boo", author="Andre Norton")
        gateway.reindex()
        mender.run(apply=True, author="Piers Anthony")
        assert all(a == "Piers Anthony" for _, a in provider.asked)
