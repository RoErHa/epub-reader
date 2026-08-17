"""Forgetting a book whose file has gone.

A scan notices a deletion but does not act on one: the row is marked
`missing_since` and kept. That is why a vanished book leaves the shelves
at once while your place in it survives — §5 says it in as many words,
"vanished; row never auto-deleted", and it is what makes an unplugged
drive a nuisance rather than a disaster.

It is also the wrong answer for a book you deleted on purpose, which is
why forgetting exists and why it has to be asked for.

> **A whole library that has vanished has not been deleted.** It is on a
> disk that is not mounted, and forgetting it would throw away every
> highlight and every reading position in one go. So when most of the
> library is missing this refuses, and says why.
"""
from __future__ import annotations

import logging

LOG = logging.getLogger("epub-reader.forget")

# Above this share of the library, "missing" means unplugged.
UNPLUGGED = 0.5


class UnpluggedError(RuntimeError):
    """Most of the library is missing, which is not the same as deleted."""


class ForgetMissing:
    def __init__(self, database) -> None:
        self._db = database

    # ── looking ───────────────────────────────────────────────────────
    def plan(self) -> list[dict]:
        """The books whose files are gone, and what would be lost with
        them. Changes nothing."""
        return [dict(row) for row in self._db.connection.execute(
            """
            SELECT b.id, b.path, b.format, b.content_hash,
                   COALESCE(m.title, b.path) AS title,
                   (SELECT COUNT(*) FROM annotation a
                     WHERE a.book_id = b.id) AS marks,
                   (SELECT r.fraction FROM reading_state r
                     WHERE r.content_hash = b.content_hash) AS progress
              FROM book b
              LEFT JOIN metadata m ON m.book_id = b.id
             WHERE b.missing_since IS NOT NULL
             ORDER BY b.path
            """
        )]

    def _library_size(self) -> int:
        return self._db.connection.execute(
            "SELECT COUNT(*) n FROM book").fetchone()["n"]

    # ── doing it ──────────────────────────────────────────────────────
    def run(self, apply: bool = False, force: bool = False) -> dict:
        gone = self.plan()
        report = {"forgotten": 0, "would_forget": len(gone),
                  "marks": sum(g["marks"] for g in gone),
                  "reading": sum(1 for g in gone if g["progress"]),
                  "plan": gone}
        if not apply or not gone:
            return report

        total = self._library_size()
        if not force and total and len(gone) / total > UNPLUGGED:
            raise UnpluggedError(
                f"{len(gone)} of {total} books are missing. That is a disk "
                "that is not mounted, not a library you emptied — nothing "
                "has been forgotten. Say so explicitly if you meant it.")

        cursor = self._db.connection
        cursor.execute("PRAGMA foreign_keys = ON")
        for book in gone:
            # Reading position is keyed by content hash, not book id, so
            # it does not cascade — and it must not outlive the last
            # book that shares that hash.
            others = cursor.execute(
                "SELECT COUNT(*) n FROM book WHERE content_hash = ? AND id != ?",
                (book["content_hash"], book["id"])).fetchone()["n"]
            if not others:
                cursor.execute("DELETE FROM reading_state WHERE content_hash = ?",
                               (book["content_hash"],))
            cursor.execute("DELETE FROM annotation WHERE book_id = ?",
                           (book["id"],))
            cursor.execute("DELETE FROM book WHERE id = ?", (book["id"],))
            LOG.info("forgot %s", book["path"])
            report["forgotten"] += 1
        # A copy kept only because it duplicated a book that is now gone
        # should not stay hidden behind nothing.
        cursor.execute(
            "UPDATE book SET duplicate_of = NULL WHERE duplicate_of IS NOT NULL"
            " AND duplicate_of NOT IN (SELECT id FROM book)")
        return report

    @staticmethod
    def summary(report: dict) -> str:
        if not report["would_forget"] and not report["forgotten"]:
            return "Missing books: none — every book in the library is on disk."
        if report["forgotten"]:
            line = f"Missing books: forgot {report['forgotten']}"
            if report["marks"]:
                line += f", with {report['marks']} highlights"
            return line + "."
        line = (f"Missing books: {report['would_forget']} whose files are"
                " gone")
        if report["marks"] or report["reading"]:
            line += (f" — {report['marks']} highlights and"
                     f" {report['reading']} reading positions would go too")
        return line + "."
