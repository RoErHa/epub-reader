"""Who said so, and whether we may overwrite them.

§5's provenance rule is the invariant the whole design rests on: every
field records where its value came from — `file`, `provider`, `user` —
and enrichment may overwrite it only where the file still owns it and
nobody has locked it. That is what makes re-running a pass over the whole
library safe rather than a gamble.

It was also written eleven times. Every pass hand-rolled its own
`INSERT INTO field_provenance … ON CONFLICT`, and eight carried their own
copy of the guard that reads it back.

> **A rule that must be copied into N places will eventually be in
> N−1.** That is not a tidiness complaint: one afternoon a new rule — a
> title a provider *confirmed* outranks one *derived* from a filename —
> had to be added to three of those eight by hand, and the one it was
> missing from spent a scan undoing the other two's work.

So: `Provenance` writes it, `Candidates` reads it, and the guard exists
**twice on purpose** — as a method for one book and as a SQL clause for a
pass selecting thousands. Two forms of one rule is how they come to
disagree, so a test asserts they never do.
"""
from __future__ import annotations

import time

# Within `provider`, not all sources are equal. A catalogue *asserts* a
# title; a filename lets us *infer* one. So a value confirmed by a
# provider is not overwritten by one derived from a name — while the
# provider that confirmed it may of course say so again.
CONFIRMED = ("openlibrary", "googlebooks", "hardcover")


class Provenance:
    """Writes down who said so."""

    def __init__(self, database) -> None:
        self._db = database

    def record(self, book_id: int, field: str, origin: str,
               source: str | None = None, locked: bool = False,
               now: int | None = None) -> None:
        """One field. Recorded, or the next scan takes the file's word
        for it again the moment the file is touched (§6.4)."""
        self._db.connection.execute(
            """
            INSERT INTO field_provenance (book_id, field, origin, source,
                                          locked, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(book_id, field) DO UPDATE SET
                origin = excluded.origin, source = excluded.source,
                locked = excluded.locked, updated_at = excluded.updated_at
            """,
            (book_id, field, origin, source, int(bool(locked)),
             now if now is not None else int(time.time())),
        )

    def record_all(self, book_id: int, fields, origin: str,
                   source: str | None = None, locked: bool = False,
                   now: int | None = None) -> None:
        stamp = now if now is not None else int(time.time())
        for field in fields:
            self.record(book_id, field, origin, source, locked, stamp)


class Candidates:
    """Reads it back: may this pass write to this field?"""

    def __init__(self, database) -> None:
        self._db = database

    def may_write(self, book_id: int, field: str,
                  derived_from: str | None = None) -> bool:
        row = self._db.connection.execute(
            "SELECT origin, source, locked FROM field_provenance"
            " WHERE book_id = ? AND field = ?", (book_id, field)).fetchone()
        if row is None:
            return True
        if row["origin"] == "user" or row["locked"]:
            return False        # a reader's word is final (§8.10)
        if derived_from in CONFIRMED:
            return True         # the confirmer may confirm again
        return row["source"] not in CONFIRMED

    @staticmethod
    def clause(field: str, derived_from: str | None = None,
               table: str = "b") -> str:
        """The same rule as a `WHERE` fragment.

        A pass selects its candidates in one query rather than asking
        book by book, so the guard has to exist in SQL as well. It is
        built here so that both forms change together, and
        `test_the_two_forms_agree` refuses to let them drift.

        No parameters: `field` and `derived_from` are the pass's own
        constants, never anything a reader typed.
        """
        if field not in {"title", "authors", "series", "cover", "synopsis",
                         "genres", "first_published", "language", "isbn13",
                         "publisher", "page_count"}:
            raise ValueError(f"{field!r} is not a field with provenance.")
        guard = (
            f" AND NOT EXISTS ("
            f" SELECT 1 FROM field_provenance fp"
            f" WHERE fp.book_id = {table}.id AND fp.field = '{field}'"
            f" AND (fp.origin = 'user' OR fp.locked = 1))"
        )
        if derived_from not in CONFIRMED:
            confirmed = ", ".join(f"'{s}'" for s in CONFIRMED)
            guard += (
                f" AND NOT EXISTS ("
                f" SELECT 1 FROM field_provenance fc"
                f" WHERE fc.book_id = {table}.id AND fc.field = '{field}'"
                f" AND fc.source IN ({confirmed}))"
            )
        return guard
