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
import re
import urllib.error
import urllib.parse
import urllib.request

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

    @staticmethod
    def _year(value) -> int | None:
        match = re.search(r"(\d{4})", str(value or ""))
        if not match:
            return None
        year = int(match.group(1))
        return year if 1000 <= year <= 2100 else None
