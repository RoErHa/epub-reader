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

from epub_names import PersonName

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

        # Locked, not merely recorded: a scan would otherwise take the
        # file's word for it again the moment the file is touched.
        cursor.execute(
            """
            INSERT INTO field_provenance (book_id, field, origin, source,
                                          locked, updated_at)
            VALUES (?, 'authors', 'user', 'hand', 1, ?)
            ON CONFLICT(book_id, field) DO UPDATE SET
                origin = 'user', source = 'hand', locked = 1,
                updated_at = excluded.updated_at
            """,
            (book_id, int(time.time())),
        )
        shown = [p.display for p in people]
        LOG.info("book %s credited to %s", book_id, ", ".join(shown))
        return shown
