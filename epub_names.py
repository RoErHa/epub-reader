"""Author names and title sort keys.

Sort keys are computed once at ingest and stored as indexed columns, so
browsing is a plain ORDER BY and stays fast at library scale. Nothing
here runs at render time.

The real library writes 30% of its authors as "Last, First" and the rest
as "First Last". Both must collapse to the same sort key or browsing by
author is useless.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Placeholders that mean "no author", not an author called Unknown.
NON_NAMES = frozenset({"unknown", "unknown author", "anonymous", "anon", "n/a", "-"})

# Particles belong to the surname: Le Guin, van der Meer, de Beauvoir.
PARTICLES = frozenset({
    "de", "del", "della", "di", "da", "dos", "du", "des",
    "van", "von", "der", "den", "ter", "ten", "te",
    "le", "la", "les", "el", "al", "bin", "ibn", "mac", "mc", "st",
})

SUFFIXES = frozenset({"jr", "jr.", "sr", "sr.", "ii", "iii", "iv", "phd", "md"})

# Leading articles, in the languages this library actually contains.
ARTICLES = frozenset({
    "the", "a", "an",
    "de", "het", "een",
    "le", "la", "les", "l'", "un", "une",
    "der", "die", "das", "ein", "eine",
    "el", "los", "las", "il", "lo", "gli",
})

SPLIT_AUTHORS = re.compile(
    r"\s*(?:&|;|,\s*and\s+|\s+and\s+|\s+with\s+)\s*", re.IGNORECASE)


def fold(text: str) -> str:
    """Lowercase and strip diacritics — for sort keys only, never display."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.lower()


@dataclass(frozen=True)
class PersonName:
    display: str
    sort: str

    @staticmethod
    def _is_placeholder(raw: str) -> bool:
        return fold(raw).strip() in NON_NAMES

    @classmethod
    def parse(cls, raw: str | None) -> PersonName | None:
        if not raw:
            return None
        raw = re.sub(r"\s+", " ", str(raw)).strip()
        if not raw or cls._is_placeholder(raw):
            return None

        if "," in raw:
            last, _, first = raw.partition(",")
            last, first = last.strip(), first.strip()
            if first:
                return cls(display=f"{first} {last}", sort=fold(f"{last}, {first}"))
            raw = last

        return cls(display=raw, sort=cls._invert(raw))

    @staticmethod
    def _invert(name: str) -> str:
        parts = name.split()
        if len(parts) < 2:
            return fold(name)

        # Trailing suffixes never take the sort position.
        suffixes: list[str] = []
        while len(parts) > 2 and fold(parts[-1]) in SUFFIXES:
            suffixes.insert(0, parts.pop())

        # Walk back over particles so they stay with the surname.
        cut = len(parts) - 1
        while cut > 1 and fold(parts[cut - 1]) in PARTICLES:
            cut -= 1

        # Chicago order: "King, Martin Luther, Jr." — the suffix trails
        # the given names rather than clinging to the surname.
        surname = " ".join(parts[cut:])
        given = " ".join(parts[:cut] + suffixes)
        return fold(f"{surname}, {given}") if given else fold(surname)

    @classmethod
    def parse_many(cls, raw: str | None) -> list[PersonName]:
        if not raw:
            return []
        text = re.sub(r"\s+", " ", str(raw)).strip()
        # "Ludlum, Robert" is one person; only split on a comma when it
        # is clearly separating multiple names.
        parts = SPLIT_AUTHORS.split(text) if SPLIT_AUTHORS.search(text) else [text]
        names = [cls.parse(p) for p in parts]
        return [n for n in names if n is not None]


def title_sort_key(title: str | None) -> str:
    """Lowercased, article-stripped, diacritic-folded, punctuation removed."""
    if not title:
        return ""
    text = fold(re.sub(r"\s+", " ", str(title)).strip())
    # Keep commas — "Hitchhiker's Guide, The" should still read that way.
    text = re.sub(r"[^\w\s,]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    first, _, rest = text.partition(" ")
    if rest and first in ARTICLES:
        return rest.strip()
    return text
