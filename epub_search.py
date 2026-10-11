"""Search over the index.

FTS5 with `unicode61 remove_diacritics 1` — the same tokenizer
dlna-gateway uses — so "le carre" finds "le Carré" and nobody has to
type the accent.

Results are grouped by type. At 1,300 books, a query like "le guin"
should offer the author first, not forty loose rows.
"""
from __future__ import annotations

import re

# FTS5 treats a lot of punctuation as syntax, and people type
# punctuation. Everything is reduced to bare terms with a prefix match on
# the last one, so a query can never be a syntax error.
TERM = re.compile(r"[^\W_]+", re.UNICODE)


class SearchIndex:
    """Keeps book_fts in step with the index."""

    def __init__(self, database) -> None:
        self._db = database

    # What a book's search row should hold. One definition, because the
    # scan compares against it to find rows that have gone stale — and
    # two copies of it would be how they came to disagree.
    EXPECTED = """
        SELECT m.book_id,
               COALESCE(m.title, '') AS title,
               COALESCE((SELECT GROUP_CONCAT(a.name, ' ' ORDER BY ba.position)
                           FROM book_author ba JOIN author a ON a.id = ba.author_id
                          WHERE ba.book_id = m.book_id), '') AS authors,
               COALESCE(s.name, '') AS series,
               COALESCE((SELECT GROUP_CONCAT(g.name, ' ' ORDER BY bg.position)
                           FROM book_genre bg JOIN genre g ON g.id = bg.genre_id
                          WHERE bg.book_id = m.book_id), '') AS genres,
               COALESCE(m.synopsis, '') AS synopsis
          FROM metadata m
          LEFT JOIN series s ON s.id = m.series_id
    """

    def reindex_book(self, book_id: int) -> None:
        cursor = self._db.connection
        row = cursor.execute(
            f"{self.EXPECTED} WHERE m.book_id = ?", (book_id,)).fetchone()
        self.remove_book(book_id)
        if row is None:
            return
        cursor.execute(
            "INSERT INTO book_fts (rowid, title, authors, series, genres, synopsis)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (book_id, row["title"], row["authors"], row["series"],
             row["genres"], row["synopsis"]),
        )

    def stale(self) -> list[int]:
        """Books whose search row no longer says what the book does.

        Every pass that renamed a title, credited an author or filed a
        series wrote the book and not its search row, so 386 books were
        findable only by what they used to be called.
        """
        return [r["book_id"] for r in self._db.connection.execute(
            f"""
            SELECT e.book_id FROM ({self.EXPECTED}) e
              JOIN book_fts f ON f.rowid = e.book_id
             WHERE f.title IS NOT e.title OR f.authors IS NOT e.authors
                OR f.series IS NOT e.series OR f.genres IS NOT e.genres
                OR f.synopsis IS NOT e.synopsis
            """)]

    def remove_book(self, book_id: int) -> None:
        self._db.connection.execute(
            "DELETE FROM book_fts WHERE rowid = ?", (book_id,)
        )


class SearchQueries:
    def __init__(self, database, browse) -> None:
        self._db = database
        self._browse = browse

    @staticmethod
    def to_match(query: str) -> str | None:
        """Turn whatever the user typed into a safe FTS5 MATCH expression."""
        terms = TERM.findall(query or "")
        if not terms:
            return None
        # Every term required; the last one matches as a prefix so results
        # appear while still typing.
        quoted = [f'"{t}"' for t in terms[:-1]]
        quoted.append(f'"{terms[-1]}"*')
        return " AND ".join(quoted)

    def search(self, query: str, limit: int = 50) -> dict:
        match = self.to_match(query)
        empty = {"books": [], "authors": [], "series": [], "query": query}
        if match is None:
            return empty

        rows = self._db.connection.execute(
            """
            SELECT f.rowid AS book_id
              FROM book_fts f
              JOIN book b ON b.id = f.rowid AND b.missing_since IS NULL
             WHERE book_fts MATCH ?
             ORDER BY bm25(book_fts, 10.0, 8.0, 4.0, 2.0, 1.0)
             LIMIT ?
            """,
            (match, limit),
        ).fetchall()
        ids = [r["book_id"] for r in rows]

        return {
            "query": query,
            "books": self._books_by_id(ids),
            "authors": self._authors(query, limit),
            "series": self._series(query, limit),
        }

    def _books_by_id(self, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self._db.connection.execute(
            f"{self._browse.BOOK_SELECT} AND b.id IN ({placeholders})", ids
        ).fetchall()
        found = {r["id"]: self._browse._book(r) for r in rows}
        return [found[i] for i in ids if i in found]  # keep relevance order

    def _like(self, query: str) -> str:
        return "%" + "%".join(TERM.findall(query.lower())) + "%"

    def _authors(self, query: str, limit: int) -> list[dict]:
        rows = self._db.connection.execute(
            """
            SELECT a.id, a.name, a.sort_name, COUNT(*) n
              FROM author a
              JOIN book_author ba ON ba.author_id = a.id
              JOIN book b ON b.id = ba.book_id AND b.missing_since IS NULL
             WHERE lower(a.name) LIKE ? OR lower(a.sort_name) LIKE ?
             GROUP BY a.id ORDER BY a.sort_name LIMIT ?
            """,
            (self._like(query), self._like(query), limit),
        ).fetchall()
        return [{"id": r["id"], "name": r["name"], "sort_name": r["sort_name"],
                 "books": r["n"]} for r in rows]

    def _series(self, query: str, limit: int) -> list[dict]:
        rows = self._db.connection.execute(
            """
            SELECT s.id, s.name, COUNT(*) n
              FROM series s
              JOIN metadata m ON m.series_id = s.id
              JOIN book b ON b.id = m.book_id AND b.missing_since IS NULL
             WHERE lower(s.name) LIKE ?
             GROUP BY s.id ORDER BY s.sort_name LIMIT ?
            """,
            (self._like(query), limit),
        ).fetchall()
        return [{"id": r["id"], "name": r["name"], "books": r["n"]} for r in rows]
