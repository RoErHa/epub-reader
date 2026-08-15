"""Author enrichment from Open Library.

Open Library is the primary source because it models authors and works
as first-class entities — no other free provider gives you a
bibliography, which is what "Also published" needs.

No test here touches the network: the provider takes its fetcher as an
argument, and every response below is canned.
"""
from __future__ import annotations

import pytest

from epub_providers import (
    OpenLibraryProvider,
    ProviderError,
    WorkClassifier,
)

AUTHOR_SEARCH = {
    "docs": [
        {"key": "OL26320A", "name": "Ursula K. Le Guin", "work_count": 300,
         "birth_date": "21 October 1929", "death_date": "22 January 2018"},
        {"key": "OL99999A", "name": "Ursula Le Guin (someone else)",
         "work_count": 1},
    ]
}

AUTHOR_DETAILS = {
    "key": "/authors/OL26320A",
    "name": "Ursula K. Le Guin",
    "bio": {"value": "American author of speculative fiction."},
    "birth_date": "21 October 1929",
    "death_date": "22 January 2018",
    "photos": [6631669],
}

WORKS = {
    "entries": [
        {"title": "The Left Hand of Darkness", "first_publish_date": "1969",
         "subjects": ["Science fiction"]},
        {"title": "A Wizard of Earthsea", "first_publish_date": "1968",
         "subjects": ["Fantasy fiction"]},
        {"title": "The Wind's Twelve Quarters",
         "subjects": ["Short stories", "Science fiction"]},
        {"title": "Dancing at the Edge of the World: Thoughts on Words",
         "subjects": ["Essays", "Literary criticism"]},
        {"title": "The Complete Orsinia", "subjects": ["Fiction"]},
    ]
}


class FakeFetcher:
    """Stands in for the network. Records what was asked for."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[str] = []
        self.headers: dict = {}

    def __call__(self, url: str, headers: dict | None = None):
        self.calls.append(url)
        self.headers = headers or {}
        for fragment, payload in self.responses.items():
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise AssertionError(f"unexpected request: {url}")


@pytest.fixture
def fetcher():
    return FakeFetcher({
        "search/authors.json": AUTHOR_SEARCH,
        "/authors/OL26320A.json": AUTHOR_DETAILS,
        "/authors/OL26320A/works.json": WORKS,
    })


@pytest.fixture
def provider(fetcher):
    return OpenLibraryProvider(fetcher, contact="reader@example.com")


class TestFindingAnAuthor:
    def test_a_matching_author_is_found(self, provider):
        found = provider.find_author("Ursula K. Le Guin")
        assert found["key"] == "OL26320A"

    def test_the_match_must_be_the_same_name(self, provider):
        # Nothing is guessed: a near-miss is left alone for a human.
        assert provider.find_author("Someone Entirely Different") is None

    def test_matching_ignores_case_and_accents(self, fetcher):
        fetcher.responses["search/authors.json"] = {
            "docs": [{"key": "OL1A", "name": "John le Carré", "work_count": 40}]
        }
        provider = OpenLibraryProvider(fetcher)
        assert provider.find_author("john le carre")["key"] == "OL1A"

    def test_an_inverted_name_still_matches(self, provider):
        assert provider.find_author("Le Guin, Ursula K.")["key"] == "OL26320A"

    def test_no_results_is_not_an_error(self, fetcher):
        fetcher.responses["search/authors.json"] = {"docs": []}
        assert OpenLibraryProvider(fetcher).find_author("Nobody") is None

    def test_we_identify_ourselves(self, provider, fetcher):
        provider.find_author("Ursula K. Le Guin")
        # Open Library asks that clients say who they are.
        assert "reader@example.com" in fetcher.headers.get("User-Agent", "")


class TestAuthorDetails:
    def test_the_bio_is_unwrapped(self, provider):
        details = provider.author_details("OL26320A")
        assert details["bio"] == "American author of speculative fiction."

    def test_a_plain_string_bio_also_works(self, fetcher):
        fetcher.responses["/authors/OL26320A.json"] = {
            "name": "X", "bio": "A plain string bio."
        }
        details = OpenLibraryProvider(fetcher).author_details("OL26320A")
        assert details["bio"] == "A plain string bio."

    def test_dates_come_through(self, provider):
        details = provider.author_details("OL26320A")
        assert details["born"] == "21 October 1929"
        assert details["died"] == "22 January 2018"

    def test_a_photo_url_is_built_when_there_is_a_photo(self, provider):
        assert "6631669" in provider.author_details("OL26320A")["photo_url"]

    def test_no_photo_is_not_an_error(self, fetcher):
        fetcher.responses["/authors/OL26320A.json"] = {"name": "X"}
        assert OpenLibraryProvider(fetcher).author_details("OL26320A")[
            "photo_url"] is None

    def test_a_failing_request_raises_a_provider_error(self, fetcher):
        fetcher.responses["/authors/OL26320A.json"] = OSError("network down")
        with pytest.raises(ProviderError):
            OpenLibraryProvider(fetcher).author_details("OL26320A")


class TestWorks:
    def test_duplicate_titles_are_collapsed(self, fetcher):
        # Open Library lists the same work repeatedly (editions and
        # translations under one title), which filled the screen with
        # repeats.
        fetcher.responses["/authors/OL26320A/works.json"] = {"entries": [
            {"title": "The Dispossessed"},
            {"title": "The  Dispossessed"},
            {"title": "the dispossessed"},
        ]}
        works = OpenLibraryProvider(fetcher).author_works("OL26320A")
        assert len(works) == 1


    def test_works_are_returned_with_years(self, provider):
        works = provider.author_works("OL26320A")
        titles = {w["title"]: w for w in works}
        assert titles["The Left Hand of Darkness"]["first_published"] == 1969

    def test_every_work_is_kept_and_classified(self, provider):
        # Everything fetched is stored; filtering happens on display, so
        # reclassifying later never needs a re-fetch.
        works = {w["title"]: w["work_type"] for w in provider.author_works("OL26320A")}
        assert works["The Left Hand of Darkness"] == "novel"
        assert works["The Wind's Twelve Quarters"] == "collection"
        assert works["The Complete Orsinia"] == "omnibus"
        assert works["Dancing at the Edge of the World: Thoughts on Words"] == \
            "nonfiction"


class TestWorkClassifier:
    @pytest.mark.parametrize("title,expected", [
        ("The Complete Works", "omnibus"),
        ("The Collected Stories", "collection"),
        ("Selected Poems", "collection"),
        ("The Earthsea Omnibus", "omnibus"),
        ("Nine Lives and Other Stories", "collection"),
        ("The Earthsea Trilogy", "omnibus"),
    ])
    def test_titles_that_give_themselves_away(self, title, expected):
        assert WorkClassifier.classify(title, []) == expected

    def test_subjects_reveal_non_fiction(self):
        assert WorkClassifier.classify("A Book", ["Essays"]) == "nonfiction"
        assert WorkClassifier.classify("A Book", ["Biography"]) == "nonfiction"

    def test_short_story_subjects_mean_a_collection(self):
        assert WorkClassifier.classify("A Book", ["Short stories"]) == "collection"

    def test_anything_else_is_assumed_to_be_a_novel(self):
        assert WorkClassifier.classify("Some Novel", ["Fiction"]) == "novel"

    @pytest.mark.parametrize("title", [
        "A Wizard of Earthsea/The Tombs of Atuan",
        "A Wizard of Earthsea/The Tombs of Atuan/The Farthest Shore",
    ])
    def test_slash_joined_titles_are_omnibuses(self, title):
        # Open Library lists bind-ups this way, and they were being
        # counted as novels and cluttering Also published.
        assert WorkClassifier.classify(title, []) == "omnibus"

    def test_a_title_with_a_date_slash_is_not_an_omnibus(self):
        assert WorkClassifier.classify("Summer 1914/1918", []) == "novel"

    def test_classification_is_case_insensitive(self):
        assert WorkClassifier.classify("the COLLECTED stories", []) == "collection"
