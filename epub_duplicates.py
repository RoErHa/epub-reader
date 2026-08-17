"""The same book, twice.

A library assembled over thirty years holds the same work more than
once: a better scan downloaded years later, a MOBI kept beside the EPUB,
and — since the unpacked directories became visible — a copy somebody
unzipped and never put back. Measured on the real library: **201 works
held more than once, 437 files, 236 of them spare.**

**No file is ever deleted.** The library has been read-only since M2 and
stays that way. What this does is show one row per work: the spare
copies keep their rows and their paths, and `restore()` brings them
back. Deleting anything is the reader's business, and they can see
exactly which paths are involved before deciding.

Matching is deliberately narrow — same title, same author, after the
normalisation the rest of the app already uses. A title alone proves
nothing: "Shadows" has been written by a great many people, and a
library full of PDFs called "Contents" would collapse into one book.
"""
from __future__ import annotations

import logging

from epub_match import normalise_title
from epub_names import fold

LOG = logging.getLogger("epub-reader.duplicates")

# Which copy to read, all else being equal. An EPUB is the book; a PDF
# is a picture of one, and cannot reflow to the screen you are holding.
FORMATS = {"epub": 5, "azw3": 4, "mobi": 3, "prc": 2, "pdf": 1}


class DuplicateFinder:
    def __init__(self, database) -> None:
        self._db = database

    # ── finding ───────────────────────────────────────────────────────
    def plan(self) -> list[dict]:
        """Works held more than once, each with the copy worth keeping
        and why. Changes nothing."""
        groups: dict[tuple, list[dict]] = {}
        for row in self._books():
            if not row["author"]:
                # A title on its own proves nothing.
                continue
            key = (normalise_title(row["title"]),
                   fold(row["author"].split(",")[0]))
            if not key[0]:
                continue
            groups.setdefault(key, []).append(dict(row))

        found = []
        for copies in groups.values():
            if len(copies) < 2:
                continue
            keeper = self._best(copies)
            found.append({
                "title": keeper["title"],
                "keep": keeper,
                "copies": copies,
                "spares": [c for c in copies if c["id"] != keeper["id"]],
                "reason": self._reason(keeper, copies),
            })
        return found

    def _books(self):
        return self._db.connection.execute(
            """
            SELECT b.id, b.path, b.format, b.size_bytes,
                   m.title, m.cover_path, m.isbn13, m.series_id,
                   a.sort_name AS author,
                   (SELECT COUNT(*) FROM annotation an
                     WHERE an.book_id = b.id) AS marks,
                   (SELECT r.fraction FROM reading_state r
                     WHERE r.content_hash = b.content_hash) AS progress
              FROM book b
              JOIN metadata m ON m.book_id = b.id
              LEFT JOIN book_author ba ON ba.book_id = b.id AND ba.position = 0
              LEFT JOIN author a ON a.id = ba.author_id
             WHERE b.missing_since IS NULL AND b.duplicate_of IS NULL
               AND m.title IS NOT NULL
            """
        ).fetchall()

    @staticmethod
    def _rank(copy: dict) -> tuple:
        """Highest wins. Reading beats everything: your place and your
        highlights hang off one copy, and hiding that one would hide
        them."""
        return (
            bool(copy["marks"]) or bool(copy["progress"]),
            FORMATS.get(copy["format"], 0),
            bool(copy["cover_path"]),
            bool(copy["isbn13"]) + bool(copy["series_id"]),
            copy["size_bytes"] or 0,
        )

    @classmethod
    def _best(cls, copies: list[dict]) -> dict:
        return max(copies, key=cls._rank)

    @staticmethod
    def _reason(keeper: dict, copies: list[dict]) -> str:
        if keeper["marks"] or keeper["progress"]:
            return "this is the copy you have been reading"
        formats = {c["format"] for c in copies}
        if len(formats) > 1:
            return f"{keeper['format']} reads better than the rest"
        if keeper["cover_path"]:
            return "this copy has a cover"
        return "the fuller copy"

    # ── doing it ──────────────────────────────────────────────────────
    def run(self, apply: bool = False) -> dict:
        groups = self.plan()
        spare = sum(len(g["spares"]) for g in groups)
        report = {"works": len(groups), "hidden": 0, "would_hide": spare,
                  "plan": groups}
        if not apply:
            return report

        for group in groups:
            for copy in group["spares"]:
                self._db.connection.execute(
                    "UPDATE book SET duplicate_of = ? WHERE id = ?",
                    (group["keep"]["id"], copy["id"]))
                LOG.info("%s: keeping %s, hiding %s", group["title"],
                         group["keep"]["path"], copy["path"])
                report["hidden"] += 1
        return report

    @staticmethod
    def summary(report: dict) -> str:
        """One line: what was found, or what was done about it."""
        if not report["works"]:
            return "Duplicates: no duplicates found."
        works = f"{report['works']} work" + ("" if report["works"] == 1 else "s")
        if report["hidden"]:
            spare = "copy" if report["hidden"] == 1 else "copies"
            return (f"Duplicates: hid {report['hidden']} spare {spare} of"
                    f" {works}. No file was deleted; run"
                    f" `restore-duplicates` to show them again.")
        spare = "copy" if report["would_hide"] == 1 else "copies"
        return (f"Duplicates: {works} held more than once — would hide"
                f" {report['would_hide']} spare {spare}.")

    def restore(self) -> int:
        """Show every hidden copy again. Nothing was lost, so nothing
        needs recovering — the rows never went anywhere."""
        cursor = self._db.connection.execute(
            "UPDATE book SET duplicate_of = NULL WHERE duplicate_of IS NOT NULL")
        return cursor.rowcount
