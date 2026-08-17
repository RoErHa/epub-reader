"""Matching a book against a provider's candidates.

The hard part of enrichment is not calling APIs. It is that most EPUBs
have no ISBN, so matching is fuzzy against providers that confidently
return the wrong edition. This is where that judgement lives, so it is
tested harder than anything else in the project.

Scoring, per the design: ISBN exact → 1.0 and stop; otherwise title
similarity ×0.5 + author surname ×0.3 + year proximity ×0.1 + language
×0.1. Auto-apply at ≥0.90 (decision 4); 0.60–0.90 goes to review.
"""
from __future__ import annotations

import pytest

from epub_match import (
    AUTO_APPLY, REVIEW_FLOOR, Candidate, MatchScorer, normalise_title,
    search_title,
)


class TestTitleNormalisation:
    @pytest.mark.parametrize("raw,expected", [
        ("The Left Hand of Darkness", "left hand of darkness"),
        ("Dune: A Novel", "dune"),
        ("Neuromancer (Sprawl Trilogy Book 1)", "neuromancer"),
        ("Emma [Illustrated]", "emma"),
        ("The Hobbit {retail}", "hobbit"),
        ("Foundation - v1.2", "foundation"),
        ("A Wizard of Earthsea (epub)", "wizard of earthsea"),
    ])
    def test_the_junk_comes_off(self, raw, expected):
        assert normalise_title(raw) == expected

    def test_accents_and_punctuation_go(self):
        assert normalise_title("L'Étranger!") == "letranger"

    def test_a_subtitle_after_a_colon_is_dropped(self):
        assert normalise_title("Sapiens: A Brief History") == "sapiens"

    def test_a_title_that_is_only_a_subtitle_survives(self):
        # Dropping everything would make it match anything.
        assert normalise_title(": Just This") == "just this"

    def test_empty_stays_empty(self):
        assert normalise_title("") == ""
        assert normalise_title(None) == ""


class TestIsbn:
    def test_an_exact_isbn_is_certainty(self):
        book = {"title": "Anything At All", "isbn13": "9780441013593"}
        candidate = Candidate(title="Something Else", isbn13="9780441013593")
        assert MatchScorer().score(book, candidate) == pytest.approx(1.0)

    def test_a_different_isbn_does_not_short_circuit(self):
        book = {"title": "Dune", "isbn13": "9780441013593"}
        candidate = Candidate(title="Dune", isbn13="9780000000000")
        # Falls through to the fuzzy score rather than scoring 1.0.
        assert MatchScorer().score(book, candidate) < 1.0

    def test_hyphens_do_not_matter(self):
        book = {"title": "x", "isbn13": "978-0-441-01359-3"}
        candidate = Candidate(title="y", isbn13="9780441013593")
        assert MatchScorer().score(book, candidate) == pytest.approx(1.0)


class TestFuzzyScoring:
    def test_an_exact_match_on_everything_scores_top(self):
        book = {"title": "The Left Hand of Darkness",
                "authors": ["Ursula K. Le Guin"],
                "first_published": 1969, "language": "en"}
        candidate = Candidate(
            title="The Left Hand of Darkness", authors=["Ursula K. Le Guin"],
            first_published=1969, language="en",
        )
        assert MatchScorer().score(book, candidate) == pytest.approx(1.0)

    def test_title_alone_cannot_reach_auto_apply(self):
        # A title match with nothing else agreeing is exactly the case
        # that returns the wrong edition, so it must land in review.
        book = {"title": "Emma", "authors": [], "first_published": None}
        candidate = Candidate(title="Emma")
        assert MatchScorer().score(book, candidate) < AUTO_APPLY

    def test_the_wrong_author_sinks_it(self):
        book = {"title": "Dune", "authors": ["Frank Herbert"],
                "first_published": 1965, "language": "en"}
        candidate = Candidate(title="Dune", authors=["Someone Else"],
                              first_published=1965, language="en")
        assert MatchScorer().score(book, candidate) < AUTO_APPLY

    def test_a_different_title_scores_near_nothing(self):
        book = {"title": "Dune", "authors": ["Frank Herbert"]}
        candidate = Candidate(title="Pride and Prejudice",
                              authors=["Jane Austen"])
        assert MatchScorer().score(book, candidate) < 0.2

    def test_the_surname_is_what_counts_for_authors(self):
        book = {"title": "Dune", "authors": ["Frank Herbert"],
                "first_published": 1965, "language": "en"}
        # Same person, written differently.
        candidate = Candidate(title="Dune", authors=["Herbert, Frank"],
                              first_published=1965, language="en")
        assert MatchScorer().score(book, candidate) == pytest.approx(1.0)

    def test_a_near_year_still_counts(self):
        book = {"title": "Dune", "authors": ["Frank Herbert"],
                "first_published": 1965, "language": "en"}
        near = Candidate(title="Dune", authors=["Frank Herbert"],
                         first_published=1966, language="en")
        far = Candidate(title="Dune", authors=["Frank Herbert"],
                        first_published=1999, language="en")
        assert MatchScorer().score(book, near) > MatchScorer().score(book, far)

    def test_a_missing_field_neither_helps_nor_punishes(self):
        book = {"title": "Dune", "authors": ["Frank Herbert"],
                "first_published": 1965, "language": "en"}
        full = Candidate(title="Dune", authors=["Frank Herbert"],
                         first_published=1965, language="en")
        partial = Candidate(title="Dune", authors=["Frank Herbert"])
        # Unknown is not wrong: a sparse candidate still scores well.
        assert MatchScorer().score(book, partial) >= 0.75
        assert MatchScorer().score(book, partial) <= MatchScorer().score(book, full)

    def test_a_wrong_language_costs_something(self):
        book = {"title": "Dune", "authors": ["Frank Herbert"],
                "first_published": 1965, "language": "en"}
        same = Candidate(title="Dune", authors=["Frank Herbert"],
                         first_published=1965, language="en")
        other = Candidate(title="Dune", authors=["Frank Herbert"],
                          first_published=1965, language="fr")
        assert MatchScorer().score(book, other) < MatchScorer().score(book, same)


class TestVerdicts:
    def test_the_thresholds_are_the_decided_ones(self):
        assert AUTO_APPLY == pytest.approx(0.90)
        assert REVIEW_FLOOR == pytest.approx(0.60)

    @pytest.mark.parametrize("score,expected", [
        (1.0, "apply"), (0.95, "apply"), (0.90, "apply"),
        (0.89, "review"), (0.60, "review"),
        (0.59, "reject"), (0.0, "reject"),
    ])
    def test_a_score_becomes_a_verdict(self, score, expected):
        assert MatchScorer.verdict(score) == expected


class TestChoosing:
    def test_the_best_candidate_wins(self):
        book = {"title": "Dune", "authors": ["Frank Herbert"],
                "first_published": 1965, "language": "en"}
        candidates = [
            Candidate(title="Dune Messiah", authors=["Frank Herbert"]),
            Candidate(title="Dune", authors=["Frank Herbert"],
                      first_published=1965, language="en"),
            Candidate(title="Dune", authors=["Someone Else"]),
        ]
        best = MatchScorer().best(book, candidates)
        assert best.candidate.title == "Dune"
        assert best.verdict == "apply"

    def test_scored_candidates_come_back_in_order(self):
        book = {"title": "Dune", "authors": ["Frank Herbert"]}
        scored = MatchScorer().rank(book, [
            Candidate(title="Something Else"),
            Candidate(title="Dune", authors=["Frank Herbert"]),
        ])
        assert scored[0].candidate.title == "Dune"
        assert scored[0].score >= scored[1].score

    def test_no_candidates_is_not_an_error(self):
        assert MatchScorer().best({"title": "Dune"}, []) is None


class TestSearchQuery:
    """What we send to a provider is not what we compare against.

    A third of the real library returned NO candidates because its titles
    carry filename junk: "Elliott, Kate - Crown Of Stars 01 - King's
    Dragon", "05 The Wounded Land". Searching for that finds nothing.
    """

    @pytest.mark.parametrize("raw,expected", [
        ("Elliott, Kate - Crown Of Stars 01 - Kings Dragon", "kings dragon"),
        ("05 The Wounded Land", "wounded land"),
        ("# 1 The Alchemyst", "alchemyst"),
        ("Anne McCaffrey - Tsw 5 City Who Fought", "city who fought"),
        ("Hyperion Cantos [02] - The Fall of Hyperion", "fall of hyperion"),
        ("Covenant 3 - The Power that Preserves", "power that preserves"),
        ("Cato 07 - The Eagle In The Sand", "eagle in the sand"),
    ])
    def test_filename_scaffolding_is_stripped(self, raw, expected):
        assert search_title(raw) == expected

    def test_an_ordinary_title_is_left_alone(self):
        assert search_title("The Left Hand of Darkness") == "left hand of darkness"

    def test_a_title_that_is_only_scaffolding_keeps_something(self):
        # Better a poor query than an empty one.
        assert search_title("01 - 02") != ""

    def test_a_hyphen_becomes_a_space_not_nothing(self):
        # "gildenfire" is a token no provider will match; "gilden fire"
        # is a query.
        assert search_title("Gilden-Fire") == "gilden fire"


class TestTheArticleAtTheEnd:
    """Libraries file "The Left Hand of Darkness" as "Left Hand of
    Darkness, The", and both spellings turn up in one collection. They
    are the same title by any reading."""

    def test_a_trailing_article_matches_a_leading_one(self):
        assert normalise_title("Left Hand of Darkness, The") == \
            normalise_title("The Left Hand of Darkness")

    def test_it_works_for_a_and_an(self):
        assert normalise_title("Wizard of Earthsea, A") == \
            normalise_title("A Wizard of Earthsea")
        assert normalise_title("Fire Upon the Deep, An") == \
            normalise_title("An Fire Upon the Deep")

    def test_a_real_comma_is_not_an_article(self):
        # "Cussler, Clive" is a name, not a filing convention.
        assert normalise_title("Dune, Messiah of") != normalise_title("Dune")

    def test_a_title_that_is_only_an_article_survives(self):
        assert normalise_title("The") == "the"
