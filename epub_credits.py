"""Correcting who wrote a book.

Some files simply lie, and confidently. "Star Wars - Refugee" carries
`Dave Taylor` as its `dc:creator` and "Edge of Victory - Rebirth" carries
`David Taylor`; the books are by Sean Williams & Shane Dix and by Greg
Keyes. Nothing automatic can fix that — the file is not damaged, the
title is not a filename, and the two wrong names look for all the world
like one author who needs merging.

So the reader says who wrote it, and what they say is kept: `origin =
'user'`, locked, and never touched again by a scan or by any pass. That
is the same rule §6.4 gives everything else, used deliberately.
"""
from __future__ import annotations

import logging
import time

from epub_names import PersonName, title_sort_key

LOG = logging.getLogger("epub-reader.credits")


class BookCredits:
    def __init__(self, database) -> None:
        self._db = database

    def set(self, book_id: int, names: list[str]) -> list[str]:
        """Say who wrote a book. Returns the names as they will be
        shown."""
        row = self._db.connection.execute(
            "SELECT id FROM book WHERE id = ?", (book_id,)).fetchone()
        if row is None:
            raise LookupError(f"No book with id {book_id}.")

        people = [person for name in names or []
                  if (person := PersonName.parse(name))]
        if not people:
            # A book credited to nobody is worse than one credited to
            # the wrong person: it falls out of the Authors browse
            # entirely.
            raise ValueError("A book needs at least one author.")

        cursor = self._db.connection
        cursor.execute("DELETE FROM book_author WHERE book_id = ?", (book_id,))
        for position, person in enumerate(people):
            cursor.execute(
                "INSERT OR IGNORE INTO author (name, sort_name) VALUES (?, ?)",
                (person.display, person.sort))
            author_id = cursor.execute(
                "SELECT id FROM author WHERE name = ?",
                (person.display,)).fetchone()["id"]
            cursor.execute(
                "INSERT OR IGNORE INTO book_author (book_id, author_id, role,"
                " position) VALUES (?, ?, 'author', ?)",
                (book_id, author_id, position))

        shown = [p.display for p in people]
        self._record(book_id, "authors")
        LOG.info("book %s credited to %s", book_id, ", ".join(shown))
        return shown

    def set_series(self, book_id: int, series: str | None,
                   index: float | None = None) -> dict:
        """Say which series a book belongs to, and where in it.

        M9 reads series from filenames and refuses to invent one from a
        single book — "The Light of All That Falls" is book three of the
        Licanius Trilogy, its file says nothing, and its name carries no
        number, so nothing automatic will ever file it.

        Passing no series takes the book out of one.
        """
        row = self._db.connection.execute(
            "SELECT id FROM book WHERE id = ?", (book_id,)).fetchone()
        if row is None:
            raise LookupError(f"No book with id {book_id}.")

        cursor = self._db.connection
        if series is not None and not str(series).strip():
            raise ValueError("A series needs a name.")

        series_id = None
        name = None
        if series is not None:
            name = str(series).strip()
            cursor.execute(
                "INSERT OR IGNORE INTO series (name, sort_name) VALUES (?, ?)",
                (name, title_sort_key(name)))
            series_id = cursor.execute(
                "SELECT id FROM series WHERE name = ?", (name,)).fetchone()["id"]

        # REAL, because novellas are book 2.5.
        position = float(index) if index is not None and series_id else None
        cursor.execute(
            "UPDATE metadata SET series_id = ?, series_index = ? WHERE book_id = ?",
            (series_id, position, book_id))
        self._record(book_id, "series")
        LOG.info("book %s filed under %s %s", book_id, name, position or "")
        return {"series": name, "index": position}

    def _record(self, book_id: int, field: str) -> None:
        """Kept as the reader's, and locked: a scan would otherwise take
        the file's word for it again the moment the file is touched."""
        self._db.connection.execute(
            """
            INSERT INTO field_provenance (book_id, field, origin, source,
                                          locked, updated_at)
            VALUES (?, ?, 'user', 'hand', 1, ?)
            ON CONFLICT(book_id, field) DO UPDATE SET
                origin = 'user', source = 'hand', locked = 1,
                updated_at = excluded.updated_at
            """,
            (book_id, field, int(time.time())),
        )
