"""Matching a book against a provider's candidates.

The hard part of enrichment is not calling APIs. Most EPUBs have no
ISBN, so matching is fuzzy against providers that confidently return the
wrong edition — which is why the weights, the thresholds and the
normalisation all live in one small, heavily tested place.

Scoring (design §6): ISBN exact → 1.0 and stop; otherwise
title ×0.5 + author surname ×0.3 + year proximity ×0.1 + language ×0.1.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from epub_names import PersonName, fold, title_sort_key

# Decision 4. Above AUTO_APPLY we write; between the two a human looks;
# below REVIEW_FLOOR we leave the book alone and mark it unmatched.
AUTO_APPLY = 0.90
REVIEW_FLOOR = 0.60

WEIGHTS = {"title": 0.5, "author": 0.3, "year": 0.1, "language": 0.1}

# Bracketed junk, scanner watermarks and version tags, which real
# filenames are full of.
BRACKETS = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
VERSION = re.compile(r"[\s\-–—]+v\.?\s*\d+(\.\d+)*\s*$", re.I)
NOISE = re.compile(
    r"\b(retail|epub|mobi|azw3?|ebook|e-book|unabridged|illustrated|"
    r"annotated|reprint|scan(ned)?|proofed?|edition)\b",
    re.I,
)


def normalise_title(title: str | None) -> str:
    """Reduce a title to the part worth comparing."""
    if not title:
        return ""
    text = str(title)
    text = BRACKETS.sub(" ", text)
    text = VERSION.sub("", text)
    # A subtitle after a colon is the publisher's, not the book's — but
    # a title that is *only* a subtitle would then match anything.
    head = text.split(":", 1)[0]
    text = head if head.strip() else text.replace(":", " ")
    text = NOISE.sub(" ", text)
    # A hyphen joins two words; deleting it welds them into a token
    # nothing will match ("Gilden-Fire" → "gildenfire"), so it becomes a
    # space. After VERSION, which needs the dash to spot "- v1.2".
    text = re.sub(r"[-–—]+", " ", text)
    # title_sort_key folds accents, strips leading articles and keeps
    # word boundaries; the remaining punctuation goes here.
    text = title_sort_key(text)
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", text)).strip()


# "Surname, First - " and "Author Name - " prefixes, as filenames use.
# The comma sits INSIDE the name ("Elliott, Kate - "), so it has to be
# part of the character class rather than trailing the group.
AUTHOR_PREFIX = re.compile(r"^\s*[\w.'’,-]+(\s+[\w.'’,-]+){0,3}\s+[-–—]\s+")
# "Series 01 - ", "Covenant 3 - ", "Tsw 5 " — a name followed by a number.
SERIES_PREFIX = re.compile(r"^\s*[\w'’&.]+(\s+[\w'’&.]+){0,3}\s+\d{1,3}\s*[-–—]?\s+")
# Leading list numbers: "05 ", "# 1 ", "01 - ".
LEADING_NUMBER = re.compile(r"^\s*#?\s*\d{1,3}\s*[-–—.]?\s+")


def search_title(title: str | None) -> str:
    """The query to send a provider.

    Deliberately more aggressive than normalise_title: a search wants the
    book's actual name, whereas comparison wants to keep whatever both
    sides might share. A third of the real library found nothing because
    its titles begin "Elliott, Kate - Crown Of Stars 01 - ".
    """
    if not title:
        return ""
    text = str(title)
    for _ in range(3):          # prefixes stack: author, then series
        before = text
        text = BRACKETS.sub(" ", text)
        text = AUTHOR_PREFIX.sub("", text)
        text = SERIES_PREFIX.sub("", text)
        text = LEADING_NUMBER.sub("", text)
        if text == before:
            break
    cleaned = normalise_title(text)
    # Never hand back nothing: a poor query beats no query.
    return cleaned or normalise_title(title) or str(title).strip().lower()


def surnames(names) -> set[str]:
    """The part of a name that actually identifies someone."""
    out = set()
    for raw in names or []:
        person = PersonName.parse(raw)
        if person:
            out.add(person.sort.split(",", 1)[0].strip())
    return out


@dataclass
class Candidate:
    title: str = ""
    authors: list[str] = field(default_factory=list)
    first_published: int | None = None
    language: str | None = None
    isbn13: str | None = None
    synopsis: str | None = None
    publisher: str | None = None
    subjects: list[str] = field(default_factory=list)
    cover_url: str | None = None
    source: str = "unknown"
    source_id: str | None = None

    def as_dict(self) -> dict:
        return {
            "title": self.title, "authors": list(self.authors),
            "first_published": self.first_published, "language": self.language,
            "isbn13": self.isbn13, "synopsis": self.synopsis,
            "publisher": self.publisher, "subjects": list(self.subjects),
            "cover_url": self.cover_url, "source": self.source,
            "source_id": self.source_id,
        }


@dataclass
class ScoredCandidate:
    candidate: Candidate
    score: float
    parts: dict

    @property
    def verdict(self) -> str:
        return MatchScorer.verdict(self.score)


class MatchScorer:
    @staticmethod
    def verdict(score: float) -> str:
        if score >= AUTO_APPLY:
            return "apply"
        if score >= REVIEW_FLOOR:
            return "review"
        return "reject"

    @staticmethod
    def _isbn(value) -> str:
        return re.sub(r"[^0-9X]", "", str(value or "").upper())

    def score(self, book: dict, candidate: Candidate) -> float:
        return self.explain(book, candidate)[0]

    def explain(self, book: dict, candidate: Candidate) -> tuple[float, dict]:
        """The score, and where it came from — the review screen shows
        this rather than an unexplained number."""
        ours, theirs = self._isbn(book.get("isbn13")), self._isbn(candidate.isbn13)
        if ours and theirs and ours == theirs:
            return 1.0, {"isbn": 1.0}

        parts = {
            "title": self._title(book, candidate),
            "author": self._author(book, candidate),
            "year": self._year(book, candidate),
            "language": self._language(book, candidate),
        }
        # Weights are NOT redistributed over the fields we happen to
        # know. Redistributing meant a title-only match scored 1.0 and
        # auto-applied — precisely the case that returns the wrong
        # edition. A thin match should score thinly: corroboration has to
        # be earned, so an unknown field simply contributes nothing.
        score = sum(WEIGHTS[k] * v for k, v in parts.items() if v is not None)

        if ours and theirs and ours != theirs:
            # Not proof of a different work — editions differ — but it is
            # evidence, and enough to keep this out of auto-apply.
            score *= 0.85
            parts["isbn"] = 0.0

        return round(min(score, 1.0), 4), parts

    # ── parts ─────────────────────────────────────────────────────────
    @staticmethod
    def _title(book, candidate) -> float | None:
        ours = normalise_title(book.get("title"))
        theirs = normalise_title(candidate.title)
        if not ours or not theirs:
            return None
        if ours == theirs:
            return 1.0
        return difflib.SequenceMatcher(None, ours, theirs).ratio()

    @staticmethod
    def _author(book, candidate) -> float | None:
        ours, theirs = surnames(book.get("authors")), surnames(candidate.authors)
        if not ours or not theirs:
            return None
        if ours & theirs:
            return 1.0
        # Near-misses happen ("LeGuin" against "Le Guin"), so compare the
        # closest pair rather than giving up.
        best = max(
            (difflib.SequenceMatcher(None, a, b).ratio() for a in ours for b in theirs),
            default=0.0,
        )
        return best

    @staticmethod
    def _year(book, candidate) -> float | None:
        ours, theirs = book.get("first_published"), candidate.first_published
        if not ours or not theirs:
            return None
        gap = abs(int(ours) - int(theirs))
        if gap == 0:
            return 1.0
        # Editions and reprints drift by a year or two; a decade is a
        # different book.
        return max(0.0, 1.0 - gap / 10)

    @staticmethod
    def _language(book, candidate) -> float | None:
        ours = (book.get("language") or "").split("-")[0].lower()
        theirs = (candidate.language or "").split("-")[0].lower()
        if not ours or not theirs:
            return None
        return 1.0 if fold(ours) == fold(theirs) else 0.0

    # ── choosing ──────────────────────────────────────────────────────
    def rank(self, book: dict, candidates) -> list[ScoredCandidate]:
        scored = []
        for candidate in candidates:
            value, parts = self.explain(book, candidate)
            scored.append(ScoredCandidate(candidate, value, parts))
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored

    def best(self, book: dict, candidates) -> ScoredCandidate | None:
        ranked = self.rank(book, candidates)
        return ranked[0] if ranked else None
