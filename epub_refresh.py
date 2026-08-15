"""Keeping author data from going stale.

A bibliography is not static — living authors publish — but re-asking
Open Library about every author at once is a bulk sweep, which is what
they ask people not to do. So the refresh is a slow round-robin: the
oldest handful, once a week, ten at a time. 395 authors at ten a week is
a full round in about forty weeks, and costs some fifteen seconds of
traffic a week.

**Oldest-first rather than alphabetical**, because surnames are not
evenly spread. This library has 49 authors filed under S and one under
U, so a letter-per-fortnight would do 49 authors one week and one the
next, with several weeks doing nothing at all — and the S authors
waiting a year whatever they published. Sorting by when we last asked
balances itself, needs no calendar, and picks up wherever it left off if
a week is missed.

The same budget is then spent where books actually appear:

    living author      every 6 months
    author who died    every 3 years — nothing new is coming
    never found        every year — asking twice for the same silence

Nothing here touches a file, and a provider being unreachable is
reported rather than raised.
"""
from __future__ import annotations

import logging
import time

from epub_providers import ProviderError

LOG = logging.getLogger("epub-reader.refresh")

DAY = 86400

# How long each kind of author keeps for.
LIVING = 182 * DAY
DEAD = 3 * 365 * DAY
UNKNOWN_TO_THE_PROVIDER = 365 * DAY

# How many to ask about in one round. Ten a week walks a library this
# size in about forty weeks.
BATCH = 10


class AuthorRefresh:
    def __init__(self, database, enricher) -> None:
        self._db = database
        self._enricher = enricher

    def due(self, now: int | None = None) -> list[dict]:
        """Authors whose data has gone stale, oldest first. An author we
        have never asked about comes first of all."""
        now = now or int(time.time())
        rows = self._db.connection.execute(
            """
            SELECT a.id, a.name, a.fetched_at, a.died,
                   (SELECT COUNT(*) FROM author_work w
                     WHERE w.author_id = a.id) AS works
              FROM author a
              JOIN book_author ba ON ba.author_id = a.id
              -- Only authors still on the shelves: a book that has
              -- vanished keeps its row, and refreshing the author of a
              -- book you no longer have is asking for nothing.
              JOIN book b ON b.id = ba.book_id AND b.missing_since IS NULL
             GROUP BY a.id
             ORDER BY a.fetched_at IS NOT NULL, a.fetched_at
            """
        ).fetchall()
        return [{"id": r["id"], "name": r["name"]}
                for r in rows if self._stale(r, now)]

    @staticmethod
    def _stale(row, now: int) -> bool:
        if not row["fetched_at"]:
            return True                       # never asked
        age = now - row["fetched_at"]
        if not row["works"]:
            # Open Library has never heard of them. A few hundred of
            # these are series names and scanner handles.
            return age >= UNKNOWN_TO_THE_PROVIDER
        if row["died"]:
            return age >= DEAD
        return age >= LIVING

    def run(self, limit: int = BATCH, now: int | None = None) -> dict:
        due = self.due(now)
        report = {"due": len(due), "refreshed": 0, "failed": 0,
                  "works": 0, "names": [], "limit": limit}
        for author in due[:limit]:
            try:
                result = self._enricher.enrich(author["id"])
            except ProviderError as exc:
                # One author being unreachable must not end the round.
                LOG.warning("refreshing %s failed: %s", author["name"], exc)
                report["failed"] += 1
                continue
            report["refreshed"] += 1
            report["works"] += result.get("works", 0)
            report["names"].append(author["name"])
        return report

    @staticmethod
    def summary(report: dict) -> str:
        """One line: who was asked about, and how much of the round is
        left."""
        if not report["due"]:
            return "Author data: nothing due."
        if not report["refreshed"] and not report["failed"]:
            return "Author data: nothing due."

        names = report["names"]
        shown = ", ".join(names[:3])
        if len(names) > 3:
            shown += f" and {len(names) - 3} more"
        line = f"Author data: refreshed {report['refreshed']} "
        line += "author" if report["refreshed"] == 1 else "authors"
        if shown:
            line += f" ({shown})"
        if report["works"]:
            line += f", {report['works']} works"
        line += "."
        if report["failed"]:
            line += f" {report['failed']} could not be reached."
        left = report["due"] - report["refreshed"] - report["failed"]
        if left > 0:
            line += f" {left} still due."
        return line
