"""Covers for the books that have none.

A quarter of this library shows a lettered tile: 514 PDFs, 26 EPUBs and
15 PRCs. Their files have not changed since they were indexed, so a scan
short-circuits straight past them (§10.3) — closing the gap needs a pass
of its own, and this is it.

Two sources, in this order:

  the file    a PDF whose first page is the cover. Free, offline, and
              exactly right when it fires: no matching, no provider, no
              wrong edition. 158 of 514 qualify.
  a provider  for the rest, where the file holds no image at all.

Like every other maintenance pass it is **dry-run by default** and
**never writes to a book file**. Where a cover came from is recorded, so
a wrong one can be dropped for good: `origin = 'user'` on the `cover`
field means no pass fetches another.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from epub_pdf import PdfCoverRenderer

LOG = logging.getLogger("epub-reader.covers")


class CoverBackfill:
    """Give a cover to books that have none, from their own files."""

    def __init__(self, database, covers, library_root,
                 renderer: PdfCoverRenderer | None = None) -> None:
        self._db = database
        self._covers = covers
        self._root = Path(library_root)
        self._renderer = renderer or PdfCoverRenderer()

    # ── who is missing one ────────────────────────────────────────────
    def candidates(self, limit: int | None = None) -> list[dict]:
        """Books with no cover whose own file might hold one. Reads
        nothing and changes nothing."""
        rows = self._db.connection.execute(
            """
            SELECT b.id, b.path, b.format, b.content_hash,
                   COALESCE(m.title, b.path) AS title
              FROM book b
              JOIN metadata m ON m.book_id = b.id
             WHERE b.missing_since IS NULL
               AND (m.cover_path IS NULL OR m.cover_path = '')
               AND b.format = 'pdf'
               -- A cover a reader threw away stays thrown away.
               AND NOT EXISTS (
                     SELECT 1 FROM field_provenance f
                      WHERE f.book_id = b.id AND f.field = 'cover'
                        AND (f.origin = 'user' OR f.locked = 1))
             ORDER BY b.path
            """
        ).fetchall()
        rows = [dict(r) for r in rows]
        return rows[:limit] if limit else rows

    # ── doing it ──────────────────────────────────────────────────────
    def run(self, apply: bool = False, limit: int | None = None) -> dict:
        report = {"covered": 0, "would_cover": 0, "looked_at": 0,
                  "failed": 0, "plan": []}
        for book in self.candidates(limit):
            report["looked_at"] += 1
            path = self._root / book["path"]
            try:
                data = self._renderer.cover(path)
            except Exception as exc:              # noqa: BLE001
                LOG.info("cover failed for %s: %s", book["path"], exc)
                report["failed"] += 1
                continue
            if not data:
                # Page one is a title page, or the file has gone. Either
                # way the letter stays, which is the honest answer.
                if not path.exists():
                    report["failed"] += 1
                continue

            report["would_cover"] += 1
            report["plan"].append({"id": book["id"], "title": book["title"],
                                   "path": book["path"]})
            if not apply:
                continue
            stored = self._covers.save(book["content_hash"], data, ".jpg")
            if not stored:
                report["failed"] += 1
                continue
            self._db.connection.execute(
                "UPDATE metadata SET cover_path = ? WHERE book_id = ?",
                (stored, book["id"]))
            self._record(book["id"], "file", "pdfium")
            report["covered"] += 1
            LOG.info("cover from page one of %s", book["path"])
        return report

    def _record(self, book_id: int, origin: str, source: str) -> None:
        """Or the next pass would ask again, and a reader who threw a
        cover away would get another one."""
        self._db.connection.execute(
            """
            INSERT INTO field_provenance (book_id, field, origin, source,
                                          locked, updated_at)
            VALUES (?, 'cover', ?, ?, 0, ?)
            ON CONFLICT(book_id, field) DO UPDATE SET
                origin = excluded.origin, source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (book_id, origin, source, int(time.time())),
        )

    @staticmethod
    def summary(report: dict) -> str:
        if not report["looked_at"]:
            return "Covers: every book that can have one already does."
        if report["covered"]:
            line = (f"Covers: {report['covered']} books took their cover from"
                    f" page one, of {report['looked_at']} looked at")
        elif report["would_cover"]:
            line = (f"Covers: {report['would_cover']} of {report['looked_at']}"
                    " books open on their own cover")
        else:
            line = (f"Covers: none of the {report['looked_at']} books opens on"
                    " a cover; they keep their letter")
        if report["failed"]:
            line += f", {report['failed']} unreadable"
        return line + "."
