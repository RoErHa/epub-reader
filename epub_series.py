"""Series, read from the filenames.

Only 135 books of 2,323 have a series, and every one came from a
`calibre:series` tag inside the file. §6 said series would come from
providers; that was never built, so most of the library has no thread
between its books at all — the Dune novels sit beside each other as
strangers.

The filenames do know. 597 of them carry the shape:

    Cussler, Clive - Dirk Pitt 01 - Pacific Vortex
    Dune Chronicles 1 - Hunters of Dune
    Forgotten Realms - Empires 02 - Dragonwall

and reading them needs no provider at all.

The danger is reading them too eagerly. "10 is Better Than 01 - Heather
Jarman" is a book *called* "10 is Better Than 01", not book 1 of a
series called "is Better Than". So one rule does most of the work:

> **A series of one is a misread.** A name is only believed when at
> least two books by the same author agree on it.

That is cheap, local, and it throws out every false positive the real
library produced.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from epub_names import fold, title_sort_key

LOG = logging.getLogger("epub-reader.series")

# "<something> 01 - <title>", anywhere after an optional author prefix.
# The number may be padded, and may be a half — novellas are book 2.5,
# which is why series_index is REAL.
PATTERN = re.compile(
    r"(?:^|\s-\s)\s*(?P<series>[^-]{2,48}?)\s+"
    r"(?P<number>\d{1,3}(?:\.\d)?)\s*-\s*(?P<title>\S.*)$"
)

# A number that is really a year, not a position in a series.
YEARS = range(1400, 2200)

# How many books have to agree before a name is believed.
AGREEMENT = 2


class SeriesFromNames:
    def __init__(self, database) -> None:
        self._db = database

    # ── reading the names ─────────────────────────────────────────────
    def plan(self) -> list[dict]:
        """What each book would be filed under. Changes nothing."""
        candidates: dict[tuple, list[dict]] = {}
        for row in self._books():
            found = self._read(row)
            if found:
                candidates.setdefault(found["key"], []).append(found)

        named = []
        for group in candidates.values():
            # A series of one is a misread: "10 is Better Than 01" is a
            # book, not book 1 of "is Better Than".
            if len(group) < AGREEMENT:
                LOG.info("only one book claims %r; leaving it alone",
                         group[0]["series"])
                continue
            named.extend(group)
        return named

    def _books(self):
        return self._db.connection.execute(
            """
            SELECT b.id, b.path, m.series_id, a.name AS author,
                   a.sort_name AS author_sort
              FROM book b
              JOIN metadata m ON m.book_id = b.id
              LEFT JOIN book_author ba ON ba.book_id = b.id AND ba.position = 0
              LEFT JOIN author a ON a.id = ba.author_id
             WHERE b.missing_since IS NULL AND b.duplicate_of IS NULL
               AND m.series_id IS NULL
               AND NOT EXISTS (
                     SELECT 1 FROM field_provenance f
                      WHERE f.book_id = b.id AND f.field = 'series')
            """
        ).fetchall()

    def _read(self, row) -> dict | None:
        match = PATTERN.search(Path(row["path"]).stem)
        if not match:
            return None
        series = match.group("series").strip(" -–—_")
        number = float(match.group("number"))

        if not self._plausible(series, number, row):
            return None
        return {
            "id": row["id"],
            "series": series,
            "index": number,
            # Agreement is within one author's shelf: two authors using
            # the word "Book" are not in a series together.
            "key": (fold(series), fold(row["author_sort"] or "")),
        }

    @staticmethod
    def _plausible(series: str, number: float, row) -> bool:
        if len(series) < 3 or not any(c.isalpha() for c in series):
            return False                       # "A 01 -" names nothing
        if number in YEARS and float(number).is_integer():
            return False                       # a year, not a position
        if not row["author_sort"]:
            return False                       # nobody to agree with
        # An author is not a series, however often their name is on the
        # shelf: "Clive Cussler 01 - Pacific Vortex".
        author = fold(row["author"] or "")
        surname = fold((row["author_sort"] or "").split(",")[0])
        folded = fold(series)
        return folded not in (author, surname)

    # ── writing it down ───────────────────────────────────────────────
    def run(self, apply: bool = False) -> dict:
        plan = self.plan()
        report = {"named": 0, "would_name": len(plan),
                  "series": len({p["series"] for p in plan}), "plan": plan}
        if not apply:
            return report

        now = int(time.time())
        for item in plan:
            series_id = self._series_id(item["series"])
            self._db.connection.execute(
                "UPDATE metadata SET series_id = ?, series_index = ?"
                " WHERE book_id = ?", (series_id, item["index"], item["id"]))
            # Recorded, or the next rescan takes it straight back out.
            self._db.connection.execute(
                """
                INSERT INTO field_provenance (book_id, field, origin, source,
                                              updated_at)
                VALUES (?, 'series', 'provider', 'filename', ?)
                ON CONFLICT(book_id, field) DO UPDATE SET
                    origin = 'provider', source = 'filename',
                    updated_at = excluded.updated_at
                """,
                (item["id"], now),
            )
            report["named"] += 1
        return report

    def _series_id(self, name: str) -> int:
        cursor = self._db.connection
        cursor.execute(
            "INSERT OR IGNORE INTO series (name, sort_name) VALUES (?, ?)",
            (name, title_sort_key(name)))
        return cursor.execute(
            "SELECT id FROM series WHERE name = ?", (name,)).fetchone()["id"]

    @staticmethod
    def summary(report: dict) -> str:
        if not report["would_name"] and not report["named"]:
            return "Series: nothing to file."
        if report["named"]:
            return (f"Series: filed {report['named']} books under"
                    f" {report['series']} series, read from their names.")
        return (f"Series: would file {report['would_name']} books under"
                f" {report['series']} series.")
