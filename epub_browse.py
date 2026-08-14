"""Browse queries over the index.

Sort keys were computed at ingest, so everything here is a plain indexed
ORDER BY. No string mangling at render time — that is what keeps browsing
fast at 10k books.
"""
from __future__ import annotations

from dataclasses import dataclass

# Whitelisted, because these are interpolated into ORDER BY.
SORTS = {
    "title": "m.title_sort",
    "author": "author_sort",
    "series": "series_sort, m.series_index",
    "year": "m.first_published",
    "added": "b.added_at DESC",
    "size": "b.size_bytes DESC",
}

MODES = {"titles": "m.title_sort", "authors": "a.sort_name", "series": "s.sort_name"}


class BrowseError(ValueError):
    """A request we refuse rather than guess at."""


@dataclass(frozen=True)
class Page:
    rows: list[dict]
    total: int


class BrowseQueries:
    """Read-only queries. All SQL lives server side."""

    BOOK_SELECT = """
        SELECT b.id, b.content_hash, b.path, b.format, b.size_bytes,
               m.title, m.title_sort, m.first_published, m.language,
               m.series_index, m.cover_path, m.synopsis,
               s.name AS series_name, s.sort_name AS series_sort,
               (SELECT GROUP_CONCAT(a2.name, ' & ')
                  FROM book_author ba2 JOIN author a2 ON a2.id = ba2.author_id
                 WHERE ba2.book_id = b.id ORDER BY ba2.position) AS author_names,
               (SELECT a3.sort_name
                  FROM book_author ba3 JOIN author a3 ON a3.id = ba3.author_id
                 WHERE ba3.book_id = b.id ORDER BY ba3.position LIMIT 1)
                 AS author_sort
          FROM book b
          JOIN metadata m ON m.book_id = b.id
          LEFT JOIN series s ON s.id = m.series_id
         WHERE b.missing_since IS NULL
    """

    def __init__(self, database) -> None:
        self._db = database

    # ── books ─────────────────────────────────────────────────────────
    def books(self, sort: str = "title", letter: str | None = None,
              author_id: int | None = None, series_id: int | None = None,
              genre_id: int | None = None, decade: int | None = None,
              limit: int = 200, offset: int = 0) -> Page:
        if sort not in SORTS:
            raise BrowseError(
                f"{sort!r} is not a sort order. "
                f"Use one of: {', '.join(sorted(SORTS))}."
            )
        sql = self.BOOK_SELECT
        params: list = []

        if author_id is not None:
            sql += (" AND b.id IN (SELECT book_id FROM book_author"
                    " WHERE author_id = ?)")
            params.append(author_id)
        if series_id is not None:
            sql += " AND m.series_id = ?"
            params.append(series_id)
        if genre_id is not None:
            sql += " AND b.id IN (SELECT book_id FROM book_genre WHERE genre_id = ?)"
            params.append(genre_id)
        if decade is not None:
            sql += " AND m.first_published BETWEEN ? AND ?"
            params += [decade, decade + 9]
        if letter:
            column = "author_sort" if sort == "author" else "m.title_sort"
            if letter == "#":
                sql += f" AND (substr({column},1,1) < 'a' OR substr({column},1,1) > 'z')"
            else:
                sql += f" AND substr({column},1,1) = ?"
                params.append(letter.lower())

        total = self._db.connection.execute(
            f"SELECT COUNT(*) n FROM ({sql})", params
        ).fetchone()["n"]

        sql += f" ORDER BY {SORTS[sort]}, m.title_sort LIMIT ? OFFSET ?"
        rows = self._db.connection.execute(sql, params + [limit, offset]).fetchall()
        return Page([self._book(r) for r in rows], total)

    @staticmethod
    def _book(row) -> dict:
        authors = row["author_names"].split(" & ") if row["author_names"] else []
        return {
            "id": row["id"],
            "name": row["path"].rsplit("/", 1)[-1],
            "path": row["path"],
            "title": row["title"],
            "authors": authors,
            "series": row["series_name"],
            "series_index": row["series_index"],
            "first_published": row["first_published"],
            "language": row["language"],
            "format": row["format"],
            "size": row["size_bytes"],
            "cover": f"/api/covers/{row['cover_path']}" if row["cover_path"] else None,
        }

    # ── browse modes ──────────────────────────────────────────────────
    def authors(self) -> list[dict]:
        return [
            {"id": r["id"], "name": r["name"], "sort_name": r["sort_name"],
             "books": r["n"]}
            for r in self._db.connection.execute("""
                SELECT a.id, a.name, a.sort_name, COUNT(*) n
                  FROM author a
                  JOIN book_author ba ON ba.author_id = a.id
                  JOIN book b ON b.id = ba.book_id AND b.missing_since IS NULL
                 GROUP BY a.id ORDER BY a.sort_name
            """)
        ]

    def series(self) -> list[dict]:
        return [
            {"id": r["id"], "name": r["name"], "sort_name": r["sort_name"],
             "books": r["n"]}
            for r in self._db.connection.execute("""
                SELECT s.id, s.name, s.sort_name, COUNT(*) n
                  FROM series s
                  JOIN metadata m ON m.series_id = s.id
                  JOIN book b ON b.id = m.book_id AND b.missing_since IS NULL
                 GROUP BY s.id ORDER BY s.sort_name
            """)
        ]

    def genres(self) -> list[dict]:
        return [
            {"id": r["id"], "name": r["name"], "books": r["n"]}
            for r in self._db.connection.execute("""
                SELECT g.id, g.name, COUNT(*) n
                  FROM genre g
                  JOIN book_genre bg ON bg.genre_id = g.id
                  JOIN book b ON b.id = bg.book_id AND b.missing_since IS NULL
                 GROUP BY g.id ORDER BY n DESC, g.sort_name
            """)
        ]

    def decades(self) -> list[dict]:
        return [
            {"decade": r["decade"], "books": r["n"]}
            for r in self._db.connection.execute("""
                SELECT (m.first_published / 10) * 10 AS decade, COUNT(*) n
                  FROM metadata m
                  JOIN book b ON b.id = m.book_id AND b.missing_since IS NULL
                 WHERE m.first_published IS NOT NULL
                 GROUP BY decade ORDER BY decade
            """)
        ]

    def letters(self, mode: str = "titles") -> list[str]:
        if mode not in MODES:
            raise BrowseError(
                f"{mode!r} is not a browse mode. "
                f"Use one of: {', '.join(sorted(MODES))}."
            )
        column = MODES[mode]
        joins = {
            "titles": "FROM metadata m JOIN book b ON b.id = m.book_id",
            "authors": ("FROM author a JOIN book_author ba ON ba.author_id = a.id"
                        " JOIN book b ON b.id = ba.book_id"),
            "series": ("FROM series s JOIN metadata m ON m.series_id = s.id"
                       " JOIN book b ON b.id = m.book_id"),
        }[mode]
        rows = self._db.connection.execute(
            f"SELECT DISTINCT substr({column}, 1, 1) c {joins}"
            " WHERE b.missing_since IS NULL AND c IS NOT NULL AND c != ''"
        ).fetchall()
        letters = sorted({r["c"].upper() for r in rows if r["c"].isalpha()})
        if any(not r["c"].isalpha() for r in rows):
            letters.append("#")
        return letters

    def status(self) -> dict:
        one = lambda sql: self._db.connection.execute(sql).fetchone()[0]  # noqa: E731
        return {
            "books": one("SELECT COUNT(*) FROM book WHERE missing_since IS NULL"),
            "missing": one("SELECT COUNT(*) FROM book WHERE missing_since IS NOT NULL"),
            "failed": one("SELECT COUNT(*) FROM book WHERE index_error IS NOT NULL"),
            "authors": one("SELECT COUNT(*) FROM author"),
            "series": one("SELECT COUNT(*) FROM series"),
            "genres": one("SELECT COUNT(*) FROM genre"),
            "indexed_at": one("SELECT COALESCE(MAX(indexed_at), 0) FROM book"),
        }
