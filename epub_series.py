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
from epub_provenance import Candidates, Provenance
from epub_tags import ScannerTags

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
        self._provenance = Provenance(database)

    # ── reading the names ─────────────────────────────────────────────
    def plan(self, author: str | None = None,
             formats: list[str] | None = None) -> list[dict]:
        """What each book would be filed under. Changes nothing.

        A scope narrows *which* books are read, never how they are
        judged: agreement is still counted within the scope, so asking
        about one author's PDFs cannot borrow corroboration from books
        the caller did not ask about.
        """
        candidates: dict[tuple, list[dict]] = {}
        for row in self._books(author, formats):
            found = self._read(row)
            if found:
                candidates.setdefault(found["key"], []).append(found)

        filed = self._already_filed()
        named = []
        for key, group in candidates.items():
            # A series of one is a misread: "10 is Better Than 01" is a
            # book, not book 1 of "is Better Than". A sibling already on
            # the shelf agrees just as well as one that is not, though —
            # Geodyssey 02 and 04 were filed by an earlier run, and
            # without this 03 was refused as a series of one.
            if len(group) + filed.get(key, 0) < AGREEMENT:
                LOG.info("only one book claims %r; leaving it alone",
                         group[0]["series"])
                continue
            named.extend(group)
        return named

    def _already_filed(self) -> dict[tuple, int]:
        """How many books each author already has under each series
        name. Agreement is about the library, not about this run."""
        counts: dict[tuple, int] = {}
        for row in self._db.connection.execute(
            """
            SELECT s.name AS series, a.sort_name AS author_sort, COUNT(*) n
              FROM book b
              JOIN metadata m ON m.book_id = b.id
              JOIN series s ON s.id = m.series_id
              LEFT JOIN book_author ba ON ba.book_id = b.id AND ba.position = 0
              LEFT JOIN author a ON a.id = ba.author_id
             WHERE b.missing_since IS NULL AND b.duplicate_of IS NULL
             GROUP BY s.name, a.sort_name
            """
        ):
            counts[(fold(row["series"]), fold(row["author_sort"] or ""))] = row["n"]
        return counts

    def _books(self, author: str | None = None,
               formats: list[str] | None = None):
        sql = """
            SELECT b.id, b.path, m.series_id, a.name AS author,
                   a.sort_name AS author_sort
              FROM book b
              JOIN metadata m ON m.book_id = b.id
              LEFT JOIN book_author ba ON ba.book_id = b.id AND ba.position = 0
              LEFT JOIN author a ON a.id = ba.author_id
             WHERE b.missing_since IS NULL AND b.duplicate_of IS NULL
               AND m.series_id IS NULL
        """ + Candidates.clause("series", derived_from="filename")
        params: list = []
        if author:
            sql += " AND a.name = ?"
            params.append(author)
        if formats:
            sql += f" AND b.format IN ({','.join('?' * len(formats))})"
            params.extend(formats)
        return self._db.connection.execute(sql, params).fetchall()

    def _read(self, row) -> dict | None:
        # Underscores put back as spaces first (§8.13d): a filesystem
        # that never learned about them hides "Anita Blake 04" inside
        # "Anita_Blake_04", where nothing matches.
        match = PATTERN.search(ScannerTags.spaces(Path(row["path"]).stem))
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
    def run(self, apply: bool = False, author: str | None = None,
            formats: list[str] | None = None) -> dict:
        plan = self.plan(author, formats)
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
            self._provenance.record(item["id"], "series", "provider",
                                    "filename", now=now)
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
