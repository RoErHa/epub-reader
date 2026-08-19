"""What the scanner wrote into the title, and the book underneath it.

    Blue Moon (v2.2)
    Enemies of the Roman Order (not ocr)
    Rome and Her Enemies (Osprey)(not ocr)

A version stamp, a note that a scan was never put through OCR, a bare
format — none of these is part of a book's name. **17 titles carry one**,
measured 2026-08-19, and they are what the library-wide `names` run
(§8.13a) could not reach: that pass tidies what it was asked to and no
more, so this is the rule it deliberately left unwritten.

> **A closed vocabulary, never "strip any bracket".** The same titles
> also hold `(The Murderbot Diaries)`, `(Osprey, Men at Arms #121)`,
> `(PhD, 1998)`, `(Collins Shorts, Book 1)` and `(Mysterious
> Creatures)` — every one part of the book or of its shelf. Guessing at
> brackets would cost more than it cleared. Known values only, which is
> the rule §6.1 follows for genres and §8.13a for file extensions.

Nothing here touches a file. `metadata.file_title` keeps what the file
said, and a title set by hand is never touched.
"""
from __future__ import annotations

import logging
import re
import time

LOG = logging.getLogger("epub-reader.tags")

# Everything the scanners in this library actually wrote, and nothing
# else:
#   (v1.0) (v2.1) (V 2.0)   a version stamp on a converted file
#   (uc)                    uncorrected
#   (not ocr)               a page scan nobody ran OCR over
#   (txt) (pdf) (epub) …    the format it was converted from
TAG = re.compile(
    r"\s*\(\s*(?:"
    r"v\s?\d[\d.]*"
    r"|uc"
    r"|not\s*ocr"
    r"|doc|docx|txt|rtf|lit|pdb|htm|html|pdf|epub|mobi|prc|azw3?|zip"
    r")\s*\)",
    re.I)


class ScannerTags:
    def __init__(self, database) -> None:
        self._db = database

    @classmethod
    def strip(cls, title: str) -> str:
        """The title without the scanner's notes.

        A title is never emptied: a book called "(v1.0)" is absurd, but a
        book called nothing is worse — it falls out of every list that
        sorts by title.
        """
        cleaned = TAG.sub("", title or "").strip()
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return cleaned or title

    def plan(self) -> list[dict]:
        plan = []
        for row in self._books():
            cleaned = self.strip(row["title"])
            if cleaned != row["title"]:
                plan.append({"id": row["id"], "title": cleaned,
                             "was": row["title"]})
        return plan

    def _books(self):
        return self._db.connection.execute(
            """
            SELECT b.id, m.title
              FROM book b
              JOIN metadata m ON m.book_id = b.id
             WHERE b.missing_since IS NULL AND b.duplicate_of IS NULL
               AND m.title LIKE '%(%'
               AND NOT EXISTS (
                     SELECT 1 FROM field_provenance f
                      WHERE f.book_id = b.id AND f.field = 'title'
                        AND (f.origin = 'user' OR f.locked = 1))
            """
        ).fetchall()

    def run(self, apply: bool = False) -> dict:
        plan = self.plan()
        report = {"cleaned": 0, "would_clean": len(plan), "plan": plan}
        if not apply:
            return report

        now = int(time.time())
        for item in plan:
            self._db.connection.execute(
                """UPDATE metadata
                      SET file_title = COALESCE(file_title, title), title = ?
                    WHERE book_id = ?""", (item["title"], item["id"]))
            # Recorded, or the next rescan writes the tag back.
            self._db.connection.execute(
                """
                INSERT INTO field_provenance (book_id, field, origin, source,
                                              updated_at)
                VALUES (?, 'title', 'provider', 'title', ?)
                ON CONFLICT(book_id, field) DO UPDATE SET
                    origin = 'provider', source = 'title',
                    updated_at = excluded.updated_at
                """, (item["id"], now))
            report["cleaned"] += 1
            LOG.info("book %s: %r -> %r", item["id"], item["was"], item["title"])
        return report

    @staticmethod
    def summary(report: dict) -> str:
        if not report["cleaned"] and not report["would_clean"]:
            return "Scanner tags: no title carries one."
        if not report["cleaned"]:
            return (f"Scanner tags: would clean {report['would_clean']} titles.")
        return f"Scanner tags: cleaned {report['cleaned']} titles."
