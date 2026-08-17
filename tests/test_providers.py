"""Author enrichment from Open Library.

Open Library is the primary source because it models authors and works
as first-class entities — no other free provider gives you a
bibliography, which is what "Also published" needs.

No test here touches the network: the provider takes its fetcher as an
argument, and every response below is canned.
"""
from __future__ import annotations

import urllib.error

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


BOOK_SEARCH = {
    "docs": [
        {"title": "The Left Hand of Darkness",
         "author_name": ["Ursula K. Le Guin"],
         "first_publish_year": 1969, "language": ["eng"],
         "isbn": ["9780441478125"], "cover_i": 8231856,
         "subject": ["Science fiction", "Gender identity"],
         "key": "/works/OL453936W"},
        {"title": "The Left Hand of Darkness: A Study Guide",
         "author_name": ["Someone Else"], "first_publish_year": 2005},
    ]
}

GOOGLE_BOOKS = {
    "items": [
        {"id": "gb1", "volumeInfo": {
            "title": "Dune", "subtitle": "A Novel",
            "authors": ["Frank Herbert"], "publishedDate": "1965-08-01",
            "language": "en", "publisher": "Chilton",
            "description": "A desert planet.",
            "categories": ["Fiction / Science Fiction"],
            "industryIdentifiers": [{"type": "ISBN_13",
                                     "identifier": "9780441013593"}],
            "imageLinks": {"thumbnail": "https://example/cover.jpg"},
        }}
    ]
}


class TestBookSearch:
    def test_open_library_books_become_candidates(self, fetcher):
        fetcher.responses["search.json"] = BOOK_SEARCH
        found = OpenLibraryProvider(fetcher).find_books(
            "The Left Hand of Darkness", "Ursula K. Le Guin")
        assert found[0].title == "The Left Hand of Darkness"
        assert found[0].authors == ["Ursula K. Le Guin"]
        assert found[0].first_published == 1969
        assert found[0].isbn13 == "9780441478125"
        assert found[0].language == "en"          # eng → en
        assert found[0].source == "openlibrary"

    def test_a_cover_url_is_built(self, fetcher):
        fetcher.responses["search.json"] = BOOK_SEARCH
        found = OpenLibraryProvider(fetcher).find_books("x", None)
        assert "8231856" in found[0].cover_url

    def test_an_empty_result_is_an_empty_list(self, fetcher):
        fetcher.responses["search.json"] = {"docs": []}
        assert OpenLibraryProvider(fetcher).find_books("nothing", None) == []


class TestGoogleBooks:
    def test_volumes_become_candidates(self):
        from epub_providers import GoogleBooksProvider
        fetcher = FakeFetcher({"googleapis.com": GOOGLE_BOOKS})
        found = GoogleBooksProvider(fetcher).find_books("Dune", "Frank Herbert")
        assert found[0].title == "Dune"
        assert found[0].synopsis == "A desert planet."
        assert found[0].first_published == 1965
        assert found[0].isbn13 == "9780441013593"
        assert found[0].subjects == ["Fiction / Science Fiction"]
        assert found[0].source == "googlebooks"

    def test_an_api_key_is_sent_when_there_is_one(self):
        from epub_providers import GoogleBooksProvider
        fetcher = FakeFetcher({"googleapis.com": GOOGLE_BOOKS})
        GoogleBooksProvider(fetcher, api_key="abc123").find_books("Dune", None)
        assert "key=abc123" in fetcher.calls[0]

    def test_no_key_is_fine(self):
        from epub_providers import GoogleBooksProvider
        fetcher = FakeFetcher({"googleapis.com": GOOGLE_BOOKS})
        GoogleBooksProvider(fetcher).find_books("Dune", None)
        assert "key=" not in fetcher.calls[0]


class TestBeingPolite:
    """A library-wide run asks Open Library about 400 authors, three
    requests each. Sent flat out that earns 503s, connection resets and
    SSL timeouts — which is exactly what happened. The fetcher spaces
    requests out and retries the failures that are worth retrying.
    """

    def test_requests_are_spaced_out(self):
        from epub_providers import PoliteFetcher
        # Read once to set the mark, then once as "now" and once to
        # re-mark: a tenth of a second has gone by.
        slept, clock = [], iter([0.0, 0.1, 0.1])
        fetcher = PoliteFetcher(lambda url, headers=None: {"ok": True},
                               interval=0.5, sleep=slept.append,
                               clock=lambda: next(clock))
        fetcher("https://example.test/1")
        fetcher("https://example.test/2")
        assert slept and slept[0] == pytest.approx(0.4, abs=0.01)

    def test_the_first_request_does_not_wait(self):
        from epub_providers import PoliteFetcher
        slept = []
        fetcher = PoliteFetcher(lambda url, headers=None: {"ok": True},
                               interval=0.5, sleep=slept.append,
                               clock=lambda: 0.0)
        fetcher("https://example.test/1")
        assert slept == []

    def test_a_server_error_is_retried(self):
        from epub_providers import PoliteFetcher
        calls = []

        def flaky(url, headers=None):
            calls.append(url)
            if len(calls) < 3:
                raise urllib.error.HTTPError(url, 503, "busy", {}, None)
            return {"ok": True}

        fetcher = PoliteFetcher(flaky, interval=0, sleep=lambda s: None,
                               clock=lambda: 0.0)
        assert fetcher("https://example.test/1") == {"ok": True}
        assert len(calls) == 3

    def test_a_connection_reset_is_retried(self):
        from epub_providers import PoliteFetcher
        calls = []

        def flaky(url, headers=None):
            calls.append(url)
            if len(calls) < 2:
                raise urllib.error.URLError(OSError(54, "Connection reset by peer"))
            return {"ok": True}

        fetcher = PoliteFetcher(flaky, interval=0, sleep=lambda s: None,
                               clock=lambda: 0.0)
        assert fetcher("https://example.test/1") == {"ok": True}

    def test_retrying_gives_up_and_says_so(self):
        from epub_providers import PoliteFetcher

        def down(url, headers=None):
            raise urllib.error.HTTPError(url, 503, "busy", {}, None)

        fetcher = PoliteFetcher(down, interval=0, sleep=lambda s: None,
                               clock=lambda: 0.0, attempts=3)
        with pytest.raises(urllib.error.HTTPError):
            fetcher("https://example.test/1")

    def test_a_missing_page_is_not_retried(self):
        from epub_providers import PoliteFetcher
        calls = []

        def missing(url, headers=None):
            calls.append(url)
            raise urllib.error.HTTPError(url, 404, "gone", {}, None)

        fetcher = PoliteFetcher(missing, interval=0, sleep=lambda s: None,
                               clock=lambda: 0.0)
        with pytest.raises(urllib.error.HTTPError):
            fetcher("https://example.test/1")
        assert len(calls) == 1, "a 404 is an answer, not a failure"

    def test_each_wait_is_longer_than_the_last(self):
        from epub_providers import PoliteFetcher
        slept = []

        def down(url, headers=None):
            raise urllib.error.HTTPError(url, 503, "busy", {}, None)

        fetcher = PoliteFetcher(down, interval=0, backoff=0.1,
                               sleep=slept.append, clock=lambda: 0.0,
                               attempts=4, jitter=lambda a, b: 0.0)
        with pytest.raises(urllib.error.HTTPError):
            fetcher("https://example.test/1")
        assert slept == sorted(slept) and len(set(slept)) == len(slept)


class TestGivingUpOnAHostThatIsDown:
    """Retrying is right for a hiccup and wrong for an outage.

    A library-wide run asks about hundreds of authors. When Open Library
    stops answering — which it did, twice — every one of them costs four
    attempts and forty-five seconds of backoff before being abandoned,
    so a run that should take minutes takes hours and achieves nothing.

    After a few whole failures in a row the host is treated as down and
    calls fail at once. One success puts it back, and so does time: the
    service keeps a fetcher for its whole life and must recover without
    being restarted.
    """

    class Down:
        def __init__(self):
            self.calls = 0

        def __call__(self, url, headers=None):
            self.calls += 1
            raise urllib.error.URLError(OSError("timed out"))

    @staticmethod
    def fetcher(fetch, clock, **kwargs):
        from epub_providers import PoliteFetcher
        settings = dict(interval=0, backoff=0, attempts=2,
                        sleep=lambda s: None, clock=clock,
                        jitter=lambda a, b: 0.0)
        settings.update(kwargs)
        return PoliteFetcher(fetch, **settings)

    def test_it_stops_trying(self):
        from epub_providers import ProviderError
        down, now = self.Down(), [0.0]
        fetcher = self.fetcher(down, lambda: now[0], give_up_after=3)

        for _ in range(3):
            with pytest.raises(urllib.error.URLError):
                fetcher("https://openlibrary.org/a")
        calls_before = down.calls

        with pytest.raises(ProviderError):
            fetcher("https://openlibrary.org/b")
        assert down.calls == calls_before, "it asked a host it had given up on"

    def test_it_says_why(self):
        from epub_providers import ProviderError
        down, now = self.Down(), [0.0]
        fetcher = self.fetcher(down, lambda: now[0], give_up_after=1)
        with pytest.raises(urllib.error.URLError):
            fetcher("https://openlibrary.org/a")
        with pytest.raises(ProviderError, match="not answering"):
            fetcher("https://openlibrary.org/b")

    def test_one_success_puts_it_back(self):
        answers = [urllib.error.URLError(OSError("timed out")), {"ok": True}]

        def flaky(url, headers=None):
            result = answers.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        now = [0.0]
        fetcher = self.fetcher(flaky, lambda: now[0], give_up_after=2,
                               attempts=1)
        with pytest.raises(urllib.error.URLError):
            fetcher("https://openlibrary.org/a")
        assert fetcher("https://openlibrary.org/b") == {"ok": True}
        assert fetcher.failures == 0

    def test_it_tries_again_after_a_while(self):
        from epub_providers import ProviderError
        down, now = self.Down(), [0.0]
        fetcher = self.fetcher(down, lambda: now[0], give_up_after=1,
                               retry_after=300)
        with pytest.raises(urllib.error.URLError):
            fetcher("https://openlibrary.org/a")
        with pytest.raises(ProviderError):
            fetcher("https://openlibrary.org/b")

        now[0] = 301
        calls_before = down.calls
        with pytest.raises(urllib.error.URLError):
            fetcher("https://openlibrary.org/c")
        assert down.calls > calls_before, "it never tried again"

    def test_a_missing_page_is_not_an_outage(self):
        # A 404 is an answer. A host that answers is not down.
        def missing(url, headers=None):
            raise urllib.error.HTTPError(url, 404, "gone", {}, None)

        now = [0.0]
        fetcher = self.fetcher(missing, lambda: now[0], give_up_after=1)
        for _ in range(3):
            with pytest.raises(urllib.error.HTTPError):
                fetcher("https://openlibrary.org/a")
        assert fetcher.failures == 0

    def test_each_host_is_judged_on_its_own(self):
        # Google Books being down says nothing about Open Library.
        from epub_providers import ProviderError

        def selective(url, headers=None):
            if "googleapis" in url:
                raise urllib.error.URLError(OSError("timed out"))
            return {"ok": True}

        now = [0.0]
        fetcher = self.fetcher(selective, lambda: now[0], give_up_after=1)
        with pytest.raises(urllib.error.URLError):
            fetcher("https://www.googleapis.com/books/v1/volumes?q=x")
        with pytest.raises(ProviderError):
            fetcher("https://www.googleapis.com/books/v1/volumes?q=y")
        assert fetcher("https://openlibrary.org/a") == {"ok": True}


class TestTheLanguageOfAWork:
    """Open Library answers with every language a work has appeared in:

        The Camel Club → ['vie', 'spa', 'eng', 'fre']

    Taking the first of those made David Baldacci Vietnamese and
    Brandon Sanderson Polish. A list of translations says nothing about
    which one is sitting on your disk.
    """

    @staticmethod
    def language(values):
        from epub_providers import OpenLibraryProvider
        return OpenLibraryProvider._language(values)

    def test_one_language_is_an_answer(self):
        assert self.language(["eng"]) == "en"

    def test_several_languages_are_not(self):
        assert self.language(["vie", "spa", "eng", "fre"]) is None

    def test_the_same_language_repeated_is_still_one_answer(self):
        assert self.language(["eng", "eng"]) == "en"

    def test_nothing_at_all(self):
        assert self.language([]) is None
        assert self.language(None) is None

    def test_marc_codes_become_bcp47(self):
        assert self.language(["ger"]) == "de"
        assert self.language(["dut"]) == "nl"

    def test_a_code_nobody_mapped_is_left_as_it_came(self):
        # Better a code we did not translate than a wrong translation.
        assert self.language(["swe"]) in ("sv", "swe")
