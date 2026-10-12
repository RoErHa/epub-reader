"""Browse queries over the index.

Sort keys were computed at ingest, so everything here is a plain indexed
ORDER BY. No string mangling at render time — that is what keeps browsing
fast at 10k books.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Whitelisted, because these are interpolated into ORDER BY.
SORTS = {
    "title": "m.title_sort",
    "author": "author_sort",
    "series": "series_sort, m.series_index",
    "year": "m.first_published",
    "added": "b.added_at DESC",
    "size": "b.size_bytes DESC",
    # Untouched books sort last rather than first — COALESCE, not NULLS
    # LAST, so the ordering is the same on any SQLite.
    "opened": "COALESCE(r.opened_at, 0) DESC",
    "progress": "COALESCE(r.fraction, 0) DESC",
}

SORT_LABELS = {
    "title": "Title", "author": "Author", "series": "Series",
    "year": "First published", "added": "Recently added",
    "opened": "Recently read", "progress": "Progress", "size": "File size",
}

MODES = {"titles": "m.title_sort", "authors": "a.sort_name", "series": "s.sort_name"}

# What a book *is*, as a reader thinks of it rather than as the disk
# stores it. Six formats are indexed and four are worth browsing by:
# `prc` and `azw3` are the same Palm container as `mobi` (§8.3), and
# somebody after a comic does not care whether the pictures are zipped
# or RAR'd. Ordered as they are shown.
FORMAT_GROUPS = {
    "epub":   {"name": "EPUB",   "formats": ("epub",)},
    "mobi":   {"name": "MOBI",   "formats": ("mobi", "azw3", "prc")},
    "pdf":    {"name": "PDF",    "formats": ("pdf",)},
    "comics": {"name": "Comics", "formats": ("cbz", "cbr")},
}


# A language is browsed by its code. The names are the browser's to
# give (Intl.DisplayNames), in the reader's own language rather than a
# table of ours. `unknown` is the shelf for a book that says nothing
# and whose words could not be read — every comic, and the scans.
UNKNOWN_LANGUAGE = "unknown"
LANGUAGE = re.compile(r"^[a-z]{2,3}$")


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
               -- ORDER BY belongs INSIDE the aggregate. Outside it, it
               -- orders the single row the aggregate returns, which is
               -- no order at all: the names came back in author_id
               -- order, so a collaborator already in the library
               -- outranked the author whose book it is. §5 says credit
               -- order matters; this is where it is kept.
               (SELECT GROUP_CONCAT(a2.name, ' & ' ORDER BY ba2.position)
                  FROM book_author ba2 JOIN author a2 ON a2.id = ba2.author_id
                 WHERE ba2.book_id = b.id) AS author_names,
               (SELECT a3.sort_name
                  FROM book_author ba3 JOIN author a3 ON a3.id = ba3.author_id
                 WHERE ba3.book_id = b.id ORDER BY ba3.position LIMIT 1)
                 AS author_sort
          FROM book b
          JOIN metadata m ON m.book_id = b.id
          LEFT JOIN series s ON s.id = m.series_id
          LEFT JOIN reading_state r ON r.content_hash = b.content_hash
         -- A work held twice is one book on screen. The spare copy
         -- keeps its row and its path; it is simply not shown.
         WHERE b.missing_since IS NULL AND b.duplicate_of IS NULL
    """

    def __init__(self, database) -> None:
        self._db = database

    # ── books ─────────────────────────────────────────────────────────
    def books(self, sort: str = "title", letter: str | None = None,
              author_id: int | None = None, series_id: int | None = None,
              genre_id: int | None = None, decade: int | None = None,
              shelf_id: int | None = None, smart: str | None = None,
              format_group: str | None = None,
              language: str | None = None,
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
        if format_group is not None:
            if format_group not in FORMAT_GROUPS:
                raise BrowseError(
                    f"{format_group!r} is not a format. "
                    f"Use one of: {', '.join(FORMAT_GROUPS)}.")
            formats = FORMAT_GROUPS[format_group]["formats"]
            sql += f" AND b.format IN ({','.join('?' * len(formats))})"
            params.extend(formats)
        if language is not None:
            if language == UNKNOWN_LANGUAGE:
                sql += " AND m.language IS NULL"
            elif LANGUAGE.match(language):
                sql += " AND m.language = ?"
                params.append(language)
            else:
                raise BrowseError(
                    f"{language!r} is not a language code. Use one like"
                    f" 'en' or 'nl', or '{UNKNOWN_LANGUAGE}'.")
        if shelf_id is not None:
            sql += " AND b.id IN (SELECT book_id FROM shelf_book WHERE shelf_id = ?)"
            params.append(shelf_id)
        if smart == "reading":
            sql += " AND r.status = 'reading'"
        elif smart == "finished":
            sql += " AND r.status = 'finished'"
        if decade is not None:
            sql += " AND m.first_published BETWEEN ? AND ?"
            params += [decade, decade + 9]
        if letter:
            column = "author_sort" if sort == "author" else "m.title_sort"
            if letter == "#":
                sql += (f" AND (substr({column},1,1) < 'a'"
                        f" OR substr({column},1,1) > 'z')")
            else:
                sql += f" AND substr({column},1,1) = ?"
                params.append(letter.lower())

        total = self._db.connection.execute(
            f"SELECT COUNT(*) n FROM ({sql})", params
        ).fetchone()["n"]

        sql += f" ORDER BY {SORTS[sort]}, m.title_sort LIMIT ? OFFSET ?"
        rows = self._db.connection.execute(
            sql, [*params, limit, offset]).fetchall()
        return Page([self._book(r) for r in rows], total)

    @staticmethod
    def _book(row) -> dict:
        authors = row["author_names"].split(" & ") if row["author_names"] else []
        return {
            "id": row["id"],
            "file": f"/api/books/{row['id']}/file",
            "content_hash": row["content_hash"],
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
                   AND b.duplicate_of IS NULL
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
                       AND b.duplicate_of IS NULL
                 GROUP BY g.id ORDER BY n DESC, g.sort_name
            """)
        ]

    def formats(self) -> list[dict]:
        """The format groups a reader actually holds.

        A group nobody owns is not listed — an empty shelf is worse than
        no shelf, and this library has no CBR indexed at all (§2.1).
        """
        # Joined through metadata, exactly as every book list is: a book
        # whose archive could not be read has a row and no metadata, so
        # counting rows would promise ten comics the shelf cannot show
        # (§6.8).
        counts = {row["format"]: row["n"] for row in self._db.connection.execute(
            """SELECT b.format, COUNT(*) n FROM book b
                JOIN metadata m ON m.book_id = b.id
                WHERE b.missing_since IS NULL AND b.duplicate_of IS NULL
                GROUP BY b.format""")}
        groups = []
        for key, group in FORMAT_GROUPS.items():
            total = sum(counts.get(f, 0) for f in group["formats"])
            if total:
                groups.append({"id": key, "name": group["name"], "books": total})
        return groups

    def languages(self) -> list[dict]:
        """Every language the shelves hold, the largest first, and the
        books nobody could place last — they are a remainder, not a
        language, however many of them there are."""
        rows = [{"id": r["code"] or UNKNOWN_LANGUAGE, "books": r["n"]}
                for r in self._db.connection.execute(
                    """SELECT m.language AS code, COUNT(*) n FROM book b
                        JOIN metadata m ON m.book_id = b.id
                        WHERE b.missing_since IS NULL AND b.duplicate_of IS NULL
                        GROUP BY m.language ORDER BY n DESC, m.language""")]
        return ([r for r in rows if r["id"] != UNKNOWN_LANGUAGE]
                + [r for r in rows if r["id"] == UNKNOWN_LANGUAGE])

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

    def book(self, book_id: int) -> dict | None:
        row = self._db.connection.execute(
            f"{self.BOOK_SELECT} AND b.id = ?", (book_id,)
        ).fetchone()
        if row is None:
            return None
        # One book, so the blurb comes too; a list of two hundred
        # leaves it behind.
        return {**self._book(row), "synopsis": row["synopsis"]}

    def genres_of(self, book_id: int) -> list[str]:
        return [r["name"] for r in self._db.connection.execute(
            "SELECT g.name FROM genre g JOIN book_genre bg ON bg.genre_id = g.id"
            " WHERE bg.book_id = ? ORDER BY bg.position",
            (book_id,),
        )]

    def primary_author(self, book_id: int) -> dict | None:
        row = self._db.connection.execute(
            """
            SELECT a.id, a.name, a.sort_name, a.bio, a.bio_source, a.photo_path,
                   a.born, a.died, a.nationality, a.website, a.fetched_at
              FROM author a
              JOIN book_author ba ON ba.author_id = a.id
             WHERE ba.book_id = ? ORDER BY ba.position LIMIT 1
            """,
            (book_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"], "name": row["name"], "sort_name": row["sort_name"],
            "bio": row["bio"], "bio_source": row["bio_source"],
            "photo": f"/api/covers/{row['photo_path']}" if row["photo_path"] else None,
            "born": row["born"], "died": row["died"],
            "nationality": row["nationality"], "website": row["website"],
            # Says plainly whether About the author has been filled in yet.
            "enriched": row["fetched_at"] is not None,
        }

    # Decision 6: novels and novellas by default; everything fetched is
    # kept, so "show all" needs no re-fetch.
    SHOWN_WORK_TYPES = ("novel", "novella")

    def also_published(self, author_id: int, exclude_book_id: int,
                       show_all: bool = False) -> list[dict]:
        """What else this author wrote — owned and not.

        A fetched bibliography supersedes the local list, because it
        knows about books you do not have. Without one, the rest of the
        library by that author is still worth showing.
        """
        fetched = self._fetched_works(author_id, exclude_book_id, show_all)
        return fetched or self._owned_works(author_id, exclude_book_id)

    def _fetched_works(self, author_id: int, exclude_book_id: int,
                       show_all: bool) -> list[dict]:
        sql = """
            SELECT w.title, w.first_published, w.series_name, w.series_index,
                   w.work_type, w.book_id
              FROM author_work w
             WHERE w.author_id = ?
               AND (w.book_id IS NULL OR w.book_id != ?)
        """
        params: list = [author_id, exclude_book_id]
        if not show_all:
            placeholders = ",".join("?" * len(self.SHOWN_WORK_TYPES))
            sql += f" AND w.work_type IN ({placeholders})"
            params += list(self.SHOWN_WORK_TYPES)
        sql += (" ORDER BY COALESCE(w.series_name, 'zzz'), w.series_index,"
                " COALESCE(w.first_published, 9999), w.title")
        return [
            {"title": r["title"], "first_published": r["first_published"],
             "series": r["series_name"], "series_index": r["series_index"],
             "work_type": r["work_type"],
             "owned": r["book_id"] is not None, "book_id": r["book_id"]}
            for r in self._db.connection.execute(sql, params)
        ]

    def _owned_works(self, author_id: int, exclude_book_id: int) -> list[dict]:
        rows = self._db.connection.execute(
            """
            SELECT b.id, m.title, m.title_sort, m.first_published,
                   m.series_index, s.name AS series_name, s.sort_name AS series_sort
              FROM book b
              JOIN metadata m ON m.book_id = b.id
              JOIN book_author ba ON ba.book_id = b.id
              LEFT JOIN series s ON s.id = m.series_id
             WHERE ba.author_id = ? AND b.id != ? AND b.missing_since IS NULL
             ORDER BY COALESCE(s.sort_name, 'zzz'), m.series_index, m.title_sort
            """,
            (author_id, exclude_book_id),
        ).fetchall()
        return [
            {"title": r["title"], "first_published": r["first_published"],
             "series": r["series_name"], "series_index": r["series_index"],
             "work_type": "novel", "owned": True, "book_id": r["id"]}
            for r in rows
        ]

    def hidden_work_count(self, author_id: int, exclude_book_id: int) -> int:
        """How many works the novels-and-novellas filter is holding back,
        so the screen can offer to show them."""
        placeholders = ",".join("?" * len(self.SHOWN_WORK_TYPES))
        return self._db.connection.execute(
            f"""SELECT COUNT(*) n FROM author_work
                 WHERE author_id = ? AND (book_id IS NULL OR book_id != ?)
                   AND work_type NOT IN ({placeholders})""",
            [author_id, exclude_book_id, *self.SHOWN_WORK_TYPES],
        ).fetchone()["n"]

    def sorts(self) -> list[dict]:
        return [{"key": key, "label": SORT_LABELS.get(key, key)} for key in SORTS]

    def book_row(self, book_id: int):
        return self._db.connection.execute(
            "SELECT id, path, content_hash, format FROM book"
            " WHERE id = ? AND missing_since IS NULL",
            (book_id,),
        ).fetchone()

    def status(self) -> dict:
        one = lambda sql: self._db.connection.execute(sql).fetchone()[0]  # noqa: E731
        return {
            # Joined to metadata, not counted off `book`: an archive we
            # could not read gets a row and no metadata, so counting rows
            # counted ten comics no list could ever show (§8.15). The
            # `failed` count below is where they are reported.
            "books": one("SELECT COUNT(*) FROM book b JOIN metadata m"
                         " ON m.book_id = b.id WHERE b.missing_since IS NULL"
                         " AND b.duplicate_of IS NULL"),
            "duplicates": one("SELECT COUNT(*) FROM book"
                              " WHERE duplicate_of IS NOT NULL"),
            "missing": one("SELECT COUNT(*) FROM book WHERE missing_since IS NOT NULL"),
            "failed": one("SELECT COUNT(*) FROM book WHERE index_error IS NOT NULL"),
            # What is on the shelves, not how many rows there are. A
            # repair leaves author rows behind rather than deleting one
            # another book might use, and the genre vocabulary is loaded
            # whole from genres.tsv whether a book uses a term or not —
            # so counting rows said 590 authors while Browse listed 395.
            "authors": one("""
                SELECT COUNT(DISTINCT ba.author_id) FROM book_author ba
                  JOIN book b ON b.id = ba.book_id AND b.missing_since IS NULL
                   AND b.duplicate_of IS NULL
            """),
            "series": one("""
                SELECT COUNT(DISTINCT m.series_id) FROM metadata m
                  JOIN book b ON b.id = m.book_id AND b.missing_since IS NULL
                   AND b.duplicate_of IS NULL
                 WHERE m.series_id IS NOT NULL
            """),
            "genres": one("""
                SELECT COUNT(DISTINCT bg.genre_id) FROM book_genre bg
                  JOIN book b ON b.id = bg.book_id AND b.missing_since IS NULL
            """),
            "indexed_at": one("SELECT COALESCE(MAX(indexed_at), 0) FROM book"),
        }
