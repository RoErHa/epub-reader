"""Shelves.

Two kinds, and the difference matters:

  stored — the reader makes them and puts books on them.
  smart  — derived from reading state ("Reading now", "Finished"). They
           are a view of a fact rather than a list anyone maintains, so
           they have no rows and cannot be edited.
"""
from __future__ import annotations

import sqlite3
import time

from epub_names import fold

# Order is the order they appear in the UI.
SMART = {
    "reading": {"name": "Reading now", "status": "reading"},
    "finished": {"name": "Finished", "status": "finished"},
}


class ShelfError(ValueError):
    """A request we refuse rather than guess at."""


class ShelfExists(ShelfError):
    """That name is already taken."""


class ShelfStore:
    def __init__(self, database) -> None:
        self._db = database

    # ── stored shelves ────────────────────────────────────────────────
    def create(self, name: str) -> dict:
        name = (name or "").strip()
        if not name:
            raise ShelfError("A shelf needs a name.")
        try:
            self._db.connection.execute(
                "INSERT INTO shelf (name, sort_name, created_at) VALUES (?, ?, ?)",
                (name, fold(name), int(time.time())),
            )
        except sqlite3.IntegrityError:
            raise ShelfExists(f"There is already a shelf called {name!r}.") from None
        return self.get(self._last_id())

    def rename(self, shelf_id: int, name: str) -> dict:
        name = (name or "").strip()
        if not name:
            raise ShelfError("A shelf needs a name.")
        self._require(shelf_id)
        try:
            self._db.connection.execute(
                "UPDATE shelf SET name = ?, sort_name = ? WHERE id = ?",
                (name, fold(name), shelf_id),
            )
        except sqlite3.IntegrityError:
            raise ShelfExists(f"There is already a shelf called {name!r}.") from None
        return self.get(shelf_id)

    def delete(self, shelf_id: int) -> None:
        self._require(shelf_id)
        # Only the shelf goes; the books are not ours to remove.
        self._db.connection.execute("DELETE FROM shelf WHERE id = ?", (shelf_id,))

    def get(self, shelf_id: int) -> dict:
        row = self._require(shelf_id)
        return {"id": row["id"], "name": row["name"], "smart": False,
                "books": self._count(shelf_id)}

    def list(self) -> list[dict]:
        """Smart shelves first — they are what you reach for most."""
        shelves = [
            {"key": key, "name": meta["name"], "smart": True,
             "books": self._smart_count(meta["status"])}
            for key, meta in SMART.items()
        ]
        shelves += [
            {"id": r["id"], "name": r["name"], "smart": False, "books": r["n"]}
            for r in self._db.connection.execute("""
                SELECT s.id, s.name, COUNT(sb.book_id) n
                  FROM shelf s
                  LEFT JOIN shelf_book sb ON sb.shelf_id = s.id
                  LEFT JOIN book b
                    ON b.id = sb.book_id AND b.missing_since IS NULL
                 GROUP BY s.id ORDER BY s.sort_name
            """)
        ]
        return shelves

    # ── books on shelves ──────────────────────────────────────────────
    def add_book(self, shelf_id: int, book_id: int) -> None:
        self._require(shelf_id)
        self._require_book(book_id)
        self._db.connection.execute(
            "INSERT OR IGNORE INTO shelf_book (shelf_id, book_id, added_at)"
            " VALUES (?, ?, ?)",
            (shelf_id, book_id, int(time.time())),
        )

    def remove_book(self, shelf_id: int, book_id: int) -> None:
        self._require(shelf_id)
        self._db.connection.execute(
            "DELETE FROM shelf_book WHERE shelf_id = ? AND book_id = ?",
            (shelf_id, book_id),
        )

    def shelves_of(self, book_id: int) -> list[dict]:
        return [
            {"id": r["id"], "name": r["name"]}
            for r in self._db.connection.execute(
                "SELECT s.id, s.name FROM shelf s"
                " JOIN shelf_book sb ON sb.shelf_id = s.id"
                " WHERE sb.book_id = ? ORDER BY s.sort_name",
                (book_id,),
            )
        ]

    # ── internals ─────────────────────────────────────────────────────
    def _require(self, shelf_id: int):
        row = self._db.connection.execute(
            "SELECT id, name FROM shelf WHERE id = ?", (shelf_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"No shelf with id {shelf_id}.")
        return row

    def _require_book(self, book_id: int) -> None:
        row = self._db.connection.execute(
            "SELECT id FROM book WHERE id = ? AND missing_since IS NULL", (book_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"No book with id {book_id}.")

    def _last_id(self) -> int:
        return self._db.connection.execute(
            "SELECT id FROM shelf ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

    def _count(self, shelf_id: int) -> int:
        return self._db.connection.execute(
            "SELECT COUNT(*) n FROM shelf_book sb"
            " JOIN book b ON b.id = sb.book_id AND b.missing_since IS NULL"
            " WHERE sb.shelf_id = ?",
            (shelf_id,),
        ).fetchone()["n"]

    def _smart_count(self, status: str) -> int:
        return self._db.connection.execute(
            "SELECT COUNT(*) n FROM reading_state r"
            " JOIN book b ON b.content_hash = r.content_hash"
            "   AND b.missing_since IS NULL"
            " WHERE r.status = ?",
            (status,),
        ).fetchone()["n"]
