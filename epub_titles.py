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
import time

from epub_match import normalise_title, search_title
from epub_names import title_sort_key
from epub_search import SearchIndex

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
# shelf talking, not the book.
COVERAGE = 0.5


# Words that stay lower case inside a title, unless they open or close
# it.
SMALL = frozenset("a an the and but or nor of in on at to for from by with"
                  " as is".split())


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

    SERIES_TAG = re.compile(r"\s*\((?:[^()]*#[\d.]+|[^()]*\bbook\s+[\d.]+)\)\s*$",
                            re.IGNORECASE)

    @classmethod
    def apply(cls, title: str) -> str:
        title = cls.SERIES_TAG.sub("", title).strip() or title
        if not cls._needs_help(title):
            return title
        return cls._title_case(title)

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
        self._search = SearchIndex(database)
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
        ours = search_title(book["title"]) or normalise_title(book["title"])
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

    @staticmethod
    def _contained_in(ours: str, titles: list[str], original: str) -> str | None:
        """The work whose title is written out inside this one.

        Series tokens survive the cleaning — "Tower03 Damia's Children",
        "Tsw 2 The Ship Who Searched" — and drag the ratio down while
        the real title sits there intact. The longest work that appears
        word for word wins, so "Dune" does not beat "Dune Messiah"; a
        tie identifies neither.
        """
        words = ours.split()
        # A token carrying a digit is a series number, not part of the
        # title, so it neither helps nor hinders.
        spoken = sum(1 for w in words if not any(c.isdigit() for c in w))
        found = []
        for title in titles:
            piece = normalise_title(title).split()
            if not piece or len(piece) > len(words):
                continue
            if not any(words[i:i + len(piece)] == piece
                       for i in range(len(words) - len(piece) + 1)):
                continue
            real = sum(1 for w in piece if not any(c.isdigit() for c in w))
            if spoken and real / spoken < COVERAGE:
                # Most of the title is still unaccounted for, so this is
                # a different book that happens to be named inside it.
                LOG.info("%r is only part of %r", title, original)
                continue
            found.append((len(piece), title))
        if not found:
            return None
        found.sort(reverse=True)
        if len(found) > 1 and found[0][0] == found[1][0]:
            LOG.info("%r contains both %r and %r", original, found[0][1],
                     found[1][1])
            return None
        return found[0][1]

    def _works(self, author_id: int) -> list[str]:
        rows = self._db.connection.execute(
            "SELECT title FROM author_work WHERE author_id = ?", (author_id,)
        ).fetchall()
        if rows or author_id in self._asked:
            # Polished before matching, so a series tag on the work does
            # not stop it matching the book it belongs to.
            return [TitlePolish.apply(r["title"]) for r in rows]

        # No bibliography yet: fetch it once, then never ask again this
        # run — an author with nothing on Open Library must not be
        # looked up once per book.
        self._asked.add(author_id)
        self._enricher.enrich(author_id)
        return [TitlePolish.apply(r["title"])
                for r in self._db.connection.execute(
                    "SELECT title FROM author_work WHERE author_id = ?",
                    (author_id,))]

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
        cursor = self._db.connection
        cursor.execute(
            """
            UPDATE metadata
               SET file_title = COALESCE(file_title, title),
                   title = ?, title_sort = ?
             WHERE book_id = ?
            """,
            (change["to"], title_sort_key(change["to"]), change["id"]),
        )
        cursor.execute(
            """
            INSERT INTO field_provenance (book_id, field, origin, source,
                                          updated_at)
            VALUES (?, 'title', 'provider', 'openlibrary-works', ?)
            ON CONFLICT(book_id, field) DO UPDATE SET
                origin = 'provider', source = 'openlibrary-works',
                updated_at = excluded.updated_at
            """,
            (change["id"], int(time.time())),
        )
        # Search holds its own copy of the title.
        self._search.reindex_book(change["id"])
        LOG.info("%r → %r", change["from"], change["to"])
