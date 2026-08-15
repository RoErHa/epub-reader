"""Metadata providers.

Open Library first, because it models authors and works as first-class
entities — no other free provider gives you a bibliography, which is what
"Also published" needs.

The fetcher is injected, so the test suite never touches the network and
a rate limiter or cache can be wrapped around it later without changing
this file.
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from epub_match import Candidate
from epub_names import PersonName, fold

LOG = logging.getLogger("epub-reader.providers")

BASE = "https://openlibrary.org"
COVERS = "https://covers.openlibrary.org"
TIMEOUT = 15


class ProviderError(RuntimeError):
    """The provider could not answer. Never fatal to the caller."""


def http_json(url: str, headers: dict | None = None) -> dict:
    request = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


class PoliteFetcher:
    """Spaces requests out and retries the failures worth retrying.

    A library-wide run asks Open Library about 400 authors, three
    requests each. Sent flat out that earns 503s, connection resets and
    SSL handshake timeouts — which is what a real run produced. Wrapping
    the fetcher rather than the provider keeps the providers ignorant of
    it, exactly as the module docstring promised.
    """

    # Statuses worth asking again about. A 404 is an answer.
    AGAIN = frozenset({429, 500, 502, 503, 504})

    def __init__(self, fetch=http_json, interval: float = 0.4,
                 attempts: int = 4, backoff: float = 0.5, sleep=time.sleep,
                 clock=time.monotonic, jitter=random.uniform) -> None:
        self._fetch = fetch
        # Spacing and backing off are different things: one paces a
        # healthy server, the other gives a struggling one room.
        self._interval = interval
        self._backoff = backoff
        self._attempts = attempts
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter
        self._last = None

    def __call__(self, url: str, headers: dict | None = None) -> dict:
        for attempt in range(1, self._attempts + 1):
            self._wait()
            try:
                return self._fetch(url, headers=headers)
            except urllib.error.HTTPError as exc:
                if exc.code not in self.AGAIN or attempt == self._attempts:
                    raise
                LOG.warning("%s answered %s; retrying (%s/%s)", url, exc.code,
                            attempt, self._attempts)
            except (urllib.error.URLError, OSError) as exc:
                if attempt == self._attempts:
                    raise
                LOG.warning("%s did not answer (%s); retrying (%s/%s)", url,
                            exc, attempt, self._attempts)
            # Back off further each time, so a struggling server is not
            # asked again at the same rate that overwhelmed it.
            self._sleep(self._backoff * (2 ** attempt) + self._jitter(0, 0.2))
        raise ProviderError(f"{url} did not answer.")   # unreachable

    def _wait(self) -> None:
        if self._last is None:
            self._last = self._clock()
            return
        now = self._clock()
        remaining = self._interval - (now - self._last)
        if remaining > 0:
            self._sleep(remaining)
        self._last = self._clock()


class WorkClassifier:
    """Decides what kind of thing a work is, from its title and subjects.

    Heuristic and openly imperfect: Open Library does not type its works.
    Decision 6 shows novels and novellas by default, so the cost of being
    wrong is a book hidden behind "show all", never one lost.
    """

    OMNIBUS = (
        r"\bcomplete\b", r"\bomnibus\b", r"\btrilogy\b", r"\bquartet\b",
        r"\bcollected works\b", r"\bboxed set\b", r"\banthology\b",
    )
    COLLECTION = (
        r"\bcollected\b", r"\bselected\b", r"\bshort stories\b",
        r"and other stories\b", r"\bstories\b$", r"\btales\b$",
    )
    NONFICTION_SUBJECTS = {
        "essays", "biography", "autobiography", "memoir", "criticism",
        "literary criticism", "history", "philosophy", "letters",
        "interviews", "nonfiction", "non-fiction",
    }
    COLLECTION_SUBJECTS = {"short stories", "short story", "poetry", "poems"}

    @classmethod
    def classify(cls, title: str, subjects: list[str] | None) -> str:
        text = (title or "").lower()
        folded = {fold(s) for s in (subjects or [])}

        # Open Library lists bind-ups as slash-joined titles — "A Wizard
        # of Earthsea/The Tombs of Atuan". Word characters on both sides,
        # so "Summer 1914/1918" is not caught.
        if re.search(r"[a-z]{3,}\s*/\s*[a-z]{3,}", text):
            return "omnibus"
        for pattern in cls.OMNIBUS:
            if re.search(pattern, text):
                return "omnibus"
        if folded & cls.NONFICTION_SUBJECTS:
            return "nonfiction"
        for pattern in cls.COLLECTION:
            if re.search(pattern, text):
                return "collection"
        if folded & cls.COLLECTION_SUBJECTS:
            return "collection"
        return "novel"


class OpenLibraryProvider:
    def __init__(self, fetch=http_json, contact: str | None = None) -> None:
        self._fetch = fetch
        self._contact = contact

    # ── internals ─────────────────────────────────────────────────────
    def _headers(self) -> dict:
        # Open Library asks that clients identify themselves.
        who = f" ({self._contact})" if self._contact else ""
        return {"User-Agent": f"epub-reader/0.1{who}", "Accept": "application/json"}

    def _get(self, url: str) -> dict:
        try:
            return self._fetch(url, headers=self._headers())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ProviderError(f"Open Library did not answer: {exc}") from exc

    # ── authors ───────────────────────────────────────────────────────
    def find_author(self, name: str) -> dict | None:
        """The author with exactly this name, or nothing.

        A near-miss is left for a human rather than guessed at — the wrong
        author's bibliography is worse than none.
        """
        person = PersonName.parse(name)
        if person is None:
            return None
        query = urllib.parse.quote(person.display)
        data = self._get(f"{BASE}/search/authors.json?q={query}")

        wanted = person.sort
        for doc in data.get("docs", []):
            candidate = PersonName.parse(doc.get("name", ""))
            if candidate and candidate.sort == wanted:
                return {"key": doc.get("key"), "name": doc.get("name"),
                        "works": doc.get("work_count", 0)}
        return None

    def author_details(self, key: str) -> dict:
        data = self._get(f"{BASE}/authors/{key}.json")
        bio = data.get("bio")
        if isinstance(bio, dict):
            bio = bio.get("value")
        photos = [p for p in (data.get("photos") or []) if isinstance(p, int) and p > 0]
        return {
            "name": data.get("name"),
            "bio": (bio or "").strip() or None,
            "born": data.get("birth_date"),
            "died": data.get("death_date"),
            "website": (data.get("links") or [{}])[0].get("url")
            if data.get("links") else None,
            "photo_url": f"{COVERS}/a/id/{photos[0]}-M.jpg" if photos else None,
        }

    def author_works(self, key: str, limit: int = 200) -> list[dict]:
        data = self._get(f"{BASE}/authors/{key}/works.json?limit={limit}")
        works = []
        # The same work appears repeatedly — editions and translations
        # listed under one title — so the first of each wins.
        seen: set[str] = set()
        for entry in data.get("entries", []):
            title = (entry.get("title") or "").strip()
            if not title:
                continue
            key_ = fold(re.sub(r"\s+", " ", title))
            if key_ in seen:
                continue
            seen.add(key_)
            subjects = entry.get("subjects") or []
            works.append({
                "title": title,
                "first_published": self._year(entry.get("first_publish_date")),
                "work_type": WorkClassifier.classify(title, subjects),
                "source": "openlibrary",
            })
        return works

    # ── books ─────────────────────────────────────────────────────────
    def find_books(self, title: str, author: str | None = None,
                   limit: int = 8) -> list[Candidate]:
        query = {"title": title, "limit": str(limit)}
        if author:
            query["author"] = author
        url = f"{BASE}/search.json?{urllib.parse.urlencode(query)}"
        data = self._get(url)
        return [self._candidate(doc) for doc in data.get("docs", [])[:limit]]

    def _candidate(self, doc: dict) -> Candidate:
        isbns = [i for i in (doc.get("isbn") or []) if len(str(i)) == 13]
        cover = doc.get("cover_i")
        return Candidate(
            title=doc.get("title", ""),
            authors=list(doc.get("author_name") or []),
            first_published=doc.get("first_publish_year"),
            language=self._language(doc.get("language")),
            isbn13=str(isbns[0]) if isbns else None,
            subjects=list((doc.get("subject") or [])[:8]),
            cover_url=f"{COVERS}/b/id/{cover}-M.jpg" if cover else None,
            source="openlibrary",
            source_id=doc.get("key"),
        )

    @staticmethod
    def _language(values) -> str | None:
        """Open Library uses MARC codes; the library speaks BCP 47."""
        if not values:
            return None
        code = str(values[0]).lower()
        return {"eng": "en", "ger": "de", "deu": "de", "fre": "fr", "fra": "fr",
                "dut": "nl", "nld": "nl", "spa": "es", "ita": "it"}.get(code, code[:2])

    @staticmethod
    def _year(value) -> int | None:
        match = re.search(r"(\d{4})", str(value or ""))
        if not match:
            return None
        year = int(match.group(1))
        return year if 1000 <= year <= 2100 else None


class GoogleBooksProvider:
    """Broad coverage and good synopses. Works unauthenticated, but is
    rate limited without a key — hence GOOGLE_BOOKS_API_KEY in .env.
    """

    ENDPOINT = "https://www.googleapis.com/books/v1/volumes"

    def __init__(self, fetch=http_json, api_key: str | None = None) -> None:
        self._fetch = fetch
        self._api_key = api_key

    def find_books(self, title: str, author: str | None = None,
                   limit: int = 8) -> list[Candidate]:
        terms = [f'intitle:"{title}"']
        if author:
            terms.append(f'inauthor:"{author}"')
        params = {"q": " ".join(terms), "maxResults": str(min(limit, 40))}
        if self._api_key:
            params["key"] = self._api_key
        url = f"{self.ENDPOINT}?{urllib.parse.urlencode(params)}"
        try:
            data = self._fetch(url, headers={"Accept": "application/json"})
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ProviderError(f"Google Books did not answer: {exc}") from exc
        return [self._candidate(item) for item in (data.get("items") or [])[:limit]]

    def _candidate(self, item: dict) -> Candidate:
        info = item.get("volumeInfo") or {}
        isbns = [i.get("identifier") for i in (info.get("industryIdentifiers") or [])
                 if i.get("type") == "ISBN_13"]
        return Candidate(
            title=info.get("title", ""),
            authors=list(info.get("authors") or []),
            first_published=OpenLibraryProvider._year(info.get("publishedDate")),
            language=info.get("language"),
            isbn13=isbns[0] if isbns else None,
            synopsis=info.get("description"),
            publisher=info.get("publisher"),
            subjects=list(info.get("categories") or []),
            cover_url=(info.get("imageLinks") or {}).get("thumbnail"),
            source="googlebooks",
            source_id=item.get("id"),
        )
