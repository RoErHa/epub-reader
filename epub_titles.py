"""Correcting a title from its author's bibliography.

A global title search is a poor way to identify a book — "Second Chance"
matches hundreds — but once the author is known the question gets much
easier: match the messy title against that author's works and nothing
else. Open Library's list is then the answer, not a guess.

The real library is full of titles that are really filenames:

    Cussler, Clive - Dirk Pitt 01 - Pacific Vortex  →  Pacific Vortex!
    Anthony, Piers - Novel - Ghost.doc              →  Ghost

**Nothing here touches a file.** Only the database changes, the title
the file gave is kept in `metadata.file_title`, and a title you set by
hand is never overwritten.
"""
from __future__ import annotations

import difflib
import logging
import re

from epub_editor import TitleWriter
from epub_match import normalise_title, search_title

LOG = logging.getLogger("epub-reader.titles")

# How well the cleaned title has to match a work before it is believed.
# The author is already settled, so this is only asking *which* of their
# books it is — but a series list is full of near-twins, so the bar
# stays high.
CONFIDENCE = 0.90

# How far ahead of the runner-up the winner has to be. "Damia's
# Children" beats "Damia" comfortably; a title that fits two works
# equally well identifies neither.
MARGIN = 0.04

# How much of a title a contained work has to account for. Open Library
# caps a bibliography at 200 works, so Asimov's list holds "Foundation"
# but not "Foundation and Empire" — and without this the one becomes
# the other. Series designations are not counted: "Catteni 3" is the
# shelf talking, not the book. Above half rather than at it, because
# "Hunter's Pride" is half "The Hunters" and is not that book.
COVERAGE = 0.6


# Words that stay lower case inside a title, unless they open or close
# it.
SMALL = frozenset(["a", "an", "the", "and", "but", "or", "nor", "of",
                   "in", "on", "at", "to", "for", "from", "by",
                   "with", "as", "is"])

# Articles that a normalised work title drops, so they turn up as
# leftovers in front of a match and mean nothing.
ARTICLES = frozenset({"the", "a", "an"})


class TitlePolish:
    """Open Library's capitalisation is whatever the cataloguer typed:
    "Acorna's search" beside "THE WINGS OF PEGASUS". A title is only
    rewritten when it is plainly not title case — one that already looks
    deliberate is left exactly as it is."""

    # "1 - A Wizard In Mind", "11.Scion of Cyador", "01 Lord Foul's
    # Bane". A bare number followed by a space only counts when it is
    # zero-padded, because "10 is Better Than 01" and "50 Philosophy
    # Ideas You Really Need to Know" are titles.
    SERIES_NUMBER = re.compile(
        r"^(?:\d{1,3}\s*[-–—._)]+\s*|0\d{0,2}\s+)(?=[^\W\d])", re.UNICODE)

    # A trailing bracket on a provider title is the shelf talking, not
    # the book: "(Alex Cross 1)", "(Garrett Files)", "(Pern #8.5)".
    SERIES_TAG = re.compile(r"\s*\([^()]*\)\s*$")

    @classmethod
    def apply(cls, title: str, subtitles: bool = True) -> str:
        title = cls.SERIES_TAG.sub("", title).strip() or title
        if not subtitles:
            title = cls.without_subtitle(title)
        if not cls._needs_help(title):
            return title
        return cls._title_case(title)

    @staticmethod
    def without_subtitle(title: str) -> str:
        """The title without what follows the colon.

        Publishers sell books there — "Kiss the Girls: A gripping
        detective thriller in the Sunday Times bestselling series" — and
        a library of fiction reads badly with sales copy in it. Only
        used when the file itself had no subtitle, so nothing the reader
        already knew about is lost.
        """
        head = title.split(":")[0].strip()
        return head or title

    @classmethod
    def without_series_number(cls, title: str) -> str:
        """The title with its position in the series taken off the
        front. Needs no provider — it is only reading what is there."""
        return cls.SERIES_NUMBER.sub("", title).strip() or title

    @staticmethod
    def _needs_help(title: str) -> bool:
        words = [w for w in title.split() if any(c.isalpha() for c in w)]
        if len(words) < 2:
            return title.isupper() or title.islower()
        if title.isupper() or title.islower():
            return True
        # Sentence case: nothing after the first word is capitalised,
        # which no title in a library is meant to look like.
        return not any(w[:1].isupper() for w in words[1:])

    @staticmethod
    def _title_case(title: str) -> str:
        words = title.split()
        out = []
        for i, word in enumerate(words):
            lowered = word.lower()
            if i not in (0, len(words) - 1) and lowered.strip(".,:;") in SMALL:
                out.append(lowered)
            elif word.isupper() or word.islower():
                out.append(lowered[:1].upper() + lowered[1:])
            else:
                out.append(word)      # already mixed: MacLeod, McGuffin
        return " ".join(out)


class TitleFromWorks:
    def __init__(self, database, enricher) -> None:
        self._db = database
        self._enricher = enricher
        self._titles = TitleWriter(database)
        self._asked: set[int] = set()

    # ── planning ──────────────────────────────────────────────────────
    def plan(self) -> list[dict]:
        """What would be renamed, and to what. Changes nothing except
        fetching a bibliography that is not here yet."""
        changes = []
        for book in self._books():
            proposal = self._proposal(book)
            if proposal:
                changes.append(proposal)
        return changes

    def _books(self) -> list[dict]:
        rows = self._db.connection.execute(
            """
            SELECT b.id, m.title, a.id AS author_id, a.name AS author
              FROM book b
              JOIN metadata m ON m.book_id = b.id
              JOIN book_author ba ON ba.book_id = b.id AND ba.position = 0
              JOIN author a ON a.id = ba.author_id
             WHERE b.missing_since IS NULL AND m.title IS NOT NULL
               -- Stricter than `Candidates.clause` on purpose: this
               -- pass leaves alone any title a provider has already
               -- set, including one derived from a filename, because
               -- asking a bibliography about a book somebody else has
               -- already answered for is a request spent on nothing.
               AND NOT EXISTS (
                     SELECT 1 FROM field_provenance f
                      WHERE f.book_id = b.id AND f.field = 'title'
                        AND (f.locked = 1 OR f.origin IN ('user', 'provider')))
             ORDER BY a.id
            """
        ).fetchall()
        return [{"id": r["id"], "title": r["title"], "author": r["author"],
                 "author_id": r["author_id"]} for r in rows]

    def _proposal(self, book: dict) -> dict | None:
        best = self._best_work(book)
        if best is not None and ":" not in book["title"]:
            # A correction may tidy a title; it may not invent a
            # subtitle the book never had.
            best = TitlePolish.apply(best, subtitles=False)
        if best is None:
            # No bibliography to go on, but a number in front of the
            # title can still come off without asking anybody.
            stripped = TitlePolish.without_series_number(book["title"])
            best = stripped if stripped != book["title"] else None
        if best is None:
            return None
        # Same title already, give or take punctuation and case.
        if normalise_title(best) == normalise_title(book["title"]):
            return None
        return {"id": book["id"], "from": book["title"], "to": best,
                "author": book["author"]}

    def _best_work(self, book: dict) -> str | None:
        titles = self._works(book["author_id"])
        if not titles:
            return None
        ours = self._query(book["title"])
        if not ours:
            return None

        scored = sorted(
            ((difflib.SequenceMatcher(None, ours, normalise_title(t)).ratio(), t)
             for t in titles),
            reverse=True,
        )
        best, runner_up = scored[0], scored[1] if len(scored) > 1 else (0.0, "")
        if best[0] >= CONFIDENCE:
            if best[0] - runner_up[0] < MARGIN:
                # Two works fit equally well, so this identifies neither.
                LOG.info("%r matches both %r and %r", book["title"], best[1],
                         runner_up[1])
                return None
            return best[1]
        return self._contained_in(ours, titles, book["title"])

    # "The Belgariad 1: Pawn of Prophecy" — a colon straight after a
    # number separates the shelf from the book, and is not the start of
    # a subtitle.
    SERIES_COLON = re.compile(r"(\d)\s*:\s*")
    BRACKETED = re.compile(r"\([^()]*\)")

    @classmethod
    def _query(cls, title: str) -> str:
        """The title as something to match with.

        Cleaning treats what follows a colon as a subtitle and drops it,
        which is right for "On Writing Well: An Informal Guide" and
        catastrophic for "The Belgariad 1: Pawn of Prophecy" — that
        leaves "belgariad 1", which matches the series omnibus, and five
        different novels become one.
        """
        title = cls.SERIES_COLON.sub(r"\1 - ", title)
        # "(With Nye, J.L.)" is who else worked on it, not what the book
        # is called.
        title = cls.BRACKETED.sub(" ", title).strip() or title
        return search_title(title) or normalise_title(title)

    @staticmethod
    def _contained_in(ours: str, titles: list[str], original: str) -> str | None:
        """The work whose title is written out inside this one.

        Series scaffolding survives the cleaning — "Tower03 Damia's
        Children", "A Series of Unfortunate Events 1 - The Bad
        Beginning" — and drags the ratio down while the real title sits
        there intact.

        Two rules keep it honest, and they pull in opposite directions:

        * A work that **ends** the title wins when what comes before it
          is scaffolding — another work, a series number, an article.
          Without this, five different Snicket books became the name of
          the series they are in, the series being a work too, and a
          longer one. Scaffolding has to carry a number, or "Storm
          Warning Storm Rising" would resolve to the second of the two
          books it names.
        * A work that leaves most of the title unaccounted for is a
          different book that happens to be named inside it. Open
          Library caps a bibliography at 200 works, so Asimov's list has
          "Foundation" but not "Foundation and Empire", and without this
          the one quietly becomes the other.

        What settles them is *what the leftover words are*. In "A Series
        of Unfortunate Events 1 - The Bad Beginning" everything before
        the book is another work, a number and an article — scaffolding.
        In "Forward the Foundation" it is "forward", which is part of
        the title, so that one is refused.
        """
        words = ours.split()
        matches = []
        for title in titles:
            piece = normalise_title(title).split()
            if not piece or len(piece) > len(words):
                continue
            at = [i for i in range(len(words) - len(piece) + 1)
                  if words[i:i + len(piece)] == piece]
            if at:
                # The last occurrence: "Dune 02 - Dune Messiah" names
                # the book after the series, not before it.
                start = max(at)
                matches.append((start, start + len(piece), title))
        if not matches:
            return None

        occupied = {i for start, end, _ in matches for i in range(start, end)}
        ends = [m for m in matches if m[1] == len(words)]
        # Scaffolding carries a number: "Series 03 - Title". Without
        # one, a work in front is just another book — "Storm Warning
        # Storm Rising" names two of them and identifies neither.
        scaffolded = [
            m for m in ends
            if not TitleFromWorks._strays(words, m, occupied)
            and any(c.isdigit() for w in words[:m[0]] for c in w)
        ]
        if scaffolded:
            return TitleFromWorks._longest(scaffolded, original)

        # Nothing ends the title, so fall back to how much of it each
        # work accounts for. A series number is the shelf talking and
        # counts for neither side.
        spoken = sum(1 for w in words if not any(c.isdigit() for c in w))
        enough = []
        for match in matches:
            piece = words[match[0]:match[1]]
            real = sum(1 for w in piece if not any(c.isdigit() for c in w))
            if spoken and real / spoken < COVERAGE:
                LOG.info("%r is only part of %r", match[2], original)
                continue
            enough.append(match)
        return TitleFromWorks._longest(enough, original) if enough else None

    @staticmethod
    def _strays(words: list[str], match: tuple, occupied: set) -> list[str]:
        """Words in front of a match that belong to no work, are not a
        series number, and are not a stray article."""
        start = match[0]
        mine = set(range(start, match[1]))
        return [words[i] for i in range(start)
                if i not in (occupied - mine)
                and not any(c.isdigit() for c in words[i])
                and words[i] not in ARTICLES]

    @staticmethod
    def _longest(matches: list[tuple], original: str) -> str | None:
        """The longest of several, unless two tie — a title that fits
        two works equally identifies neither."""
        ranked = sorted(matches, key=lambda m: m[1] - m[0], reverse=True)
        if len(ranked) > 1 and (ranked[0][1] - ranked[0][0]) == (
                ranked[1][1] - ranked[1][0]):
            LOG.info("%r contains both %r and %r", original, ranked[0][2],
                     ranked[1][2])
            return None
        return ranked[0][2]

    def _works(self, author_id: int) -> list[str]:
        rows = self._db.connection.execute(
            "SELECT title FROM author_work WHERE author_id = ?", (author_id,)
        ).fetchall()
        if rows or author_id in self._asked or self._already_asked(author_id):
            # Polished before matching, so a series tag on the work does
            # not stop it matching the book it belongs to.
            return self._distinct(TitlePolish.apply(r["title"]) for r in rows)

        # No bibliography yet: fetch it once, then never ask again — not
        # this run, and not on the next restart either. An author with
        # nothing on Open Library must not cost a request every time the
        # service comes up.
        self._asked.add(author_id)
        self._enricher.enrich(author_id)
        return self._distinct(
            TitlePolish.apply(r["title"])
            for r in self._db.connection.execute(
                "SELECT title FROM author_work WHERE author_id = ?",
                (author_id,)))

    @staticmethod
    def _distinct(titles) -> list[str]:
        """One entry per work. Open Library lists editions and
        translations under the same author, so a bibliography holds the
        same title several times over — 22 duplicates in Le Guin's 200 —
        and two identical titles are not an ambiguity to refuse over.
        """
        best: dict[str, str] = {}
        for title in titles:
            key = normalise_title(title)
            kept = best.get(key)
            if kept is None or TitleFromWorks._fuller(title, kept):
                best[key] = title
        return list(best.values())

    @staticmethod
    def _fuller(candidate: str, kept: str) -> bool:
        """Whether one spelling of a work is more complete than another.

        Open Library lists "A Falcon Flies" beside "Falcon Flies" and
        "The Bad Beginning" beside "Bad Beginning". They are one work
        written two ways, and the article belongs to the book.
        """
        opens = candidate.split(" ")[0].lower() in ARTICLES
        kept_opens = kept.split(" ")[0].lower() in ARTICLES
        if opens != kept_opens:
            return opens
        if len(candidate.split()) != len(kept.split()):
            return len(candidate.split()) > len(kept.split())
        # Same words: "Dune." is not a fuller "Dune".
        return candidate.rstrip(".,;:").strip() == candidate != kept

    def _already_asked(self, author_id: int) -> bool:
        """Whether this author has been looked up before and had nothing
        to give. Enrichment stamps `fetched_at` even when it finds
        nothing, exactly so the question is not asked twice."""
        row = self._db.connection.execute(
            "SELECT fetched_at FROM author WHERE id = ?", (author_id,)
        ).fetchone()
        return bool(row and row["fetched_at"])

    # ── doing it ──────────────────────────────────────────────────────
    def run(self, apply: bool = False) -> dict:
        changes = self.plan()
        report = {"corrected": 0, "would_correct": len(changes),
                  "plan": changes}
        if not apply:
            return report

        for change in changes:
            self._rename(change)
            report["corrected"] += 1
        return report

    def _rename(self, change: dict) -> None:
        self._titles.write(change["id"], change["to"], "provider",
                           "openlibrary-works")
        LOG.info("%r → %r", change["from"], change["to"])
