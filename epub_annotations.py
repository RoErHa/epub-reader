"""Highlights and notes.

Server-side and shared across devices, like reading position (decision
7): a passage marked on the iPad is marked on the Mac. What stays per
device is how the page looks, not what is on it.

Two things are deliberate. **The quoted words are stored beside the
CFI**, because a CFI can stop resolving — a book replaced by a better
scan, a file edited — and a highlight whose words survive is
recoverable while a dangling pointer is not. And **a fraction is stored
beside both**, because CFIs do not sort as strings and the highlights
panel has to list them in reading order.
"""
from __future__ import annotations

import time

# The colours a highlight can be. A closed set, because these are drawn
# by the reader and a value nothing can render is not a colour.
COLOURS = ("yellow", "green", "blue", "pink", "underline")


class AnnotationError(ValueError):
    """The annotation cannot be stored as asked."""


class AnnotationStore:
    def __init__(self, database) -> None:
        self._db = database

    # ── reading ───────────────────────────────────────────────────────
    def for_book(self, book_id: int) -> list[dict]:
        """Every mark on one book, in reading order."""
        return [self._row(r) for r in self._db.connection.execute(
            "SELECT * FROM annotation WHERE book_id = ?"
            " ORDER BY fraction, id", (book_id,))]

    def counts(self, book_ids) -> dict[int, int]:
        """How many marks each book carries. Read in one go rather than
        once per row, since this decorates the whole listing."""
        book_ids = list(book_ids)
        if not book_ids:
            return {}
        marks = ",".join("?" * len(book_ids))
        return {r["book_id"]: r["n"] for r in self._db.connection.execute(
            f"SELECT book_id, COUNT(*) n FROM annotation"
            f" WHERE book_id IN ({marks}) GROUP BY book_id", book_ids)}

    @staticmethod
    def _row(row) -> dict:
        return {"id": row["id"], "book_id": row["book_id"], "cfi": row["cfi"],
                "text": row["text"], "note": row["note"], "color": row["color"],
                "fraction": row["fraction"], "created_at": row["created_at"]}

    # ── writing ───────────────────────────────────────────────────────
    def add(self, book_id: int, cfi: str, text: str, note: str | None = None,
            color: str | None = None, fraction: float = 0.0) -> dict:
        cfi, text = (cfi or "").strip(), (text or "").strip()
        if not cfi:
            raise AnnotationError("A highlight needs a position in the book.")
        if not text:
            # Without the words there is nothing left when a CFI breaks.
            raise AnnotationError("A highlight needs the words it marks.")
        color = self._colour(color)

        now = int(time.time())
        cursor = self._db.connection.execute(
            "INSERT INTO annotation (book_id, cfi, text, note, color,"
            " fraction, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (book_id, cfi, text, note, color, self._fraction(fraction), now, now),
        )
        return self.get(cursor.lastrowid)

    def update(self, annotation_id: int, note: str | None = None,
               color: str | None = None) -> dict:
        """A note and a colour are second thoughts, and may change. The
        words and the place are what the highlight *is*: changing those
        would make it a different highlight wearing the same id."""
        current = self.get(annotation_id)
        if current is None:
            raise LookupError(f"No annotation with id {annotation_id}.")
        self._db.connection.execute(
            "UPDATE annotation SET note = ?, color = ?, updated_at = ?"
            " WHERE id = ?",
            (current["note"] if note is None else note,
             current["color"] if color is None else self._colour(color),
             int(time.time()), annotation_id),
        )
        return self.get(annotation_id)

    def remove(self, annotation_id: int) -> bool:
        cursor = self._db.connection.execute(
            "DELETE FROM annotation WHERE id = ?", (annotation_id,))
        return cursor.rowcount > 0

    def get(self, annotation_id: int) -> dict | None:
        row = self._db.connection.execute(
            "SELECT * FROM annotation WHERE id = ?", (annotation_id,)).fetchone()
        return self._row(row) if row else None

    # ── house rules ───────────────────────────────────────────────────
    @staticmethod
    def _colour(colour: str | None) -> str | None:
        if colour is None or colour == "":
            return None
        if colour not in COLOURS:
            raise AnnotationError(
                f"{colour!r} is not a colour a highlight can be drawn in.")
        return colour

    @staticmethod
    def _fraction(fraction) -> float:
        """Clamped rather than refused: an odd number should not cost
        somebody their highlight."""
        try:
            return min(1.0, max(0.0, float(fraction)))
        except (TypeError, ValueError):
            return 0.0
