"""Editing a book's whole record by hand (§12.3).

Hand-editing was authors and series only (§8.10). That reaches the files
that lie about who wrote them, and the books no filename numbers — but
not `Bamboo Bloodbath & Ninj`, which names two novels and was cut off
mid-word, nor a series nobody else has heard of, nor a synopsis no file
carried. Only a person can, so the rest of the record opens up: title,
synopsis, genres, first published.

What a person says is `origin = 'user'`, locked, and never touched again
by a scan or a pass — §6.4's rule, used deliberately. That is also why
`title` becomes writable here when enrichment may not touch it (§10.13):
the objection there is to changing it *silently*, and nothing here is
silent.

> **A title has three copies.** The column, its sort key and the search
> row. Six passes rewrote the first and five forgot the sort key, all six
> forgot search — so 368 books sorted under the filename they no longer
> carried, and 386 could be found only by it. `TitleWriter` is now the
> one place a title is written, and a test refuses a seventh.
"""
from __future__ import annotations

import logging
import time

from epub_credits import BookCredits
from epub_language import LanguageCode
from epub_names import PersonName, fold, title_sort_key
from epub_provenance import Provenance
from epub_search import SearchIndex

LOG = logging.getLogger("epub-reader.editor")


class TitleWriter:
    """The one place a book's title changes."""

    def __init__(self, database) -> None:
        self._db = database
        self._search = SearchIndex(database)
        self._provenance = Provenance(database)

    def write(self, book_id: int, title: str, origin: str | None = None,
              source: str | None = None, locked: bool = False,
              now: int | None = None) -> None:
        """Title, sort key and search row together, and the file's own
        words kept in `file_title` the first time they are replaced, so
        every rename stays reversible (§6.3).

        With an `origin`, who said so is recorded too — and a pass must
        say, or the next scan takes the file's word for it again (§6.4).
        """
        self._db.connection.execute(
            """UPDATE metadata
                  SET file_title = COALESCE(file_title, title),
                      title = ?, title_sort = ?
                WHERE book_id = ?""",
            (title, title_sort_key(title), book_id))
        if origin is not None:
            self._provenance.record(book_id, "title", origin, source,
                                    locked=locked, now=now)
        self._search.reindex_book(book_id)


class BookEditor:
    """A person's say over the whole record."""

    # The fields a person may set. `series_index` rides with `series`
    # and is recorded as part of it, as it always has been (§8.10).
    FIELDS = ("title", "authors", "series", "series_index", "synopsis",
              "genres", "first_published", "language")

    # The year the work first appeared. Gilgamesh is older than 3000 BC
    # and is not in this library; a typo of 19755 is, sooner or later.
    EARLIEST = -3000

    def __init__(self, database) -> None:
        self._db = database
        self._titles = TitleWriter(database)
        self._credits = BookCredits(database)
        self._search = SearchIndex(database)
        self._provenance = Provenance(database)

    def edited(self, book_id: int) -> list[str]:
        """Which of this book's fields are the reader's, so the screen
        can say so — a value nothing will ever overwrite should look
        like one."""
        editable = {f for f in self.FIELDS if f != "series_index"}
        return [r["field"] for r in self._db.connection.execute(
            "SELECT field FROM field_provenance"
            " WHERE book_id = ? AND origin = 'user' ORDER BY field",
            (book_id,)) if r["field"] in editable]

    def edit(self, book_id: int, changes: dict) -> list[str]:
        """Apply what was said, and only that. A field not mentioned is
        not touched; one mentioned as empty is cleared — §8.2's
        distinction, and for the same reason.

        All or nothing: every value is checked before any is written,
        because half a save is a record nobody asked for. Returns the
        fields that actually changed.
        """
        current = self._current(book_id)
        wanted = self._validated(changes)
        changed = {field: value for field, value in wanted.items()
                   if current.get(field) != value}
        # A number moved within the same series is still the series.
        if "series_index" in changed and "series" not in changed:
            changed["series"] = current["series"]
        changed.pop("series_index", None)
        if not changed:
            return []

        connection = self._db.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._apply(book_id, changed, wanted, current)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        LOG.info("book %s edited by hand: %s", book_id, ", ".join(changed))
        return sorted(changed)

    # ── checking ──────────────────────────────────────────────────────
    def _validated(self, changes: dict) -> dict:
        unknown = set(changes) - set(self.FIELDS)
        if unknown:
            raise ValueError(
                f"{', '.join(sorted(unknown))} cannot be edited here.")
        out = {}
        if "title" in changes:
            title = (changes["title"] or "").strip()
            if not title:
                # A book called nothing falls out of every list sorted
                # by title.
                raise ValueError("A book needs a title.")
            out["title"] = title
        if "authors" in changes:
            people = [p for name in changes["authors"] or []
                      if (p := PersonName.parse(name))]
            if not people:
                raise ValueError("A book needs at least one author.")
            out["authors"] = [p.display for p in people]
        if "series" in changes:
            name = changes["series"]
            name = str(name).strip() if name is not None else ""
            out["series"] = name or None
        if "series_index" in changes:
            index = changes["series_index"]
            out["series_index"] = float(index) if index is not None else None
        if "synopsis" in changes:
            text = (changes["synopsis"] or "").strip()
            out["synopsis"] = text or None
        if "genres" in changes:
            out["genres"] = self._genres(changes["genres"] or [])
        if "first_published" in changes:
            out["first_published"] = self._year(changes["first_published"])
        if "language" in changes:
            out["language"] = self._language(changes["language"])
        return out

    def _year(self, value) -> int | None:
        if value is None:
            return None
        year = int(value)
        latest = time.gmtime().tm_year + 1
        if year == 0 or not self.EARLIEST <= year <= latest:
            # There is no year 0, and nothing here was first published
            # in the future.
            raise ValueError(f"{value} is not a year a book appeared in.")
        return year

    @staticmethod
    def _language(value) -> str | None:
        """A code, spelt the library's way; nothing clears it. A name is
        refused rather than looked up: the form offers codes, and a
        guess at "Flemish" is how a shelf gets a language nobody chose."""
        if value is None or not str(value).strip():
            return None
        code = LanguageCode.normalise(str(value))
        if code is None:
            raise ValueError(f"{value} is not a language code, like en or nl.")
        return code

    @staticmethod
    def _genres(names: list[str]) -> list[str]:
        out, seen = [], set()
        for given in names:
            name = str(given).strip()
            if name and fold(name) not in seen:
                seen.add(fold(name))
                out.append(name)
        return out

    def _current(self, book_id: int) -> dict:
        connection = self._db.connection
        row = connection.execute(
            """SELECT m.title, m.synopsis, m.first_published, m.series_index,
                      m.language,
                      s.name AS series
                 FROM book b LEFT JOIN metadata m ON m.book_id = b.id
                 LEFT JOIN series s ON s.id = m.series_id
                WHERE b.id = ?""", (book_id,)).fetchone()
        if row is None:
            raise LookupError(f"No book with id {book_id}.")
        current = dict(row)
        current["authors"] = [r["name"] for r in connection.execute(
            """SELECT a.name FROM book_author ba JOIN author a
                   ON a.id = ba.author_id
                WHERE ba.book_id = ? ORDER BY ba.position""", (book_id,))]
        current["genres"] = [r["name"] for r in connection.execute(
            """SELECT g.name FROM book_genre bg JOIN genre g
                   ON g.id = bg.genre_id
                WHERE bg.book_id = ? ORDER BY bg.position""", (book_id,))]
        return current

    # ── writing ───────────────────────────────────────────────────────
    def _apply(self, book_id: int, changed: dict, wanted: dict,
               current: dict) -> None:
        now = int(time.time())
        connection = self._db.connection
        if "title" in changed:
            self._titles.write(book_id, changed["title"], "user", "hand",
                               locked=True, now=now)
        if "authors" in changed:
            self._credits.set(book_id, changed["authors"])
        if "series" in changed:
            index = wanted.get("series_index", current["series_index"])
            self._credits.set_series(book_id, changed["series"], index)
        for column in ("synopsis", "first_published", "language"):
            if column in changed:
                connection.execute(
                    f"UPDATE metadata SET {column} = ? WHERE book_id = ?",
                    (changed[column], book_id))
                self._mine(book_id, column, now)
        if "genres" in changed:
            self._write_genres(book_id, changed["genres"])
            self._mine(book_id, "genres", now)
        # Search holds its own copy of authors, series, genres and
        # synopsis as well as the title.
        self._search.reindex_book(book_id)

    def _write_genres(self, book_id: int, names: list[str]) -> None:
        connection = self._db.connection
        connection.execute("DELETE FROM book_genre WHERE book_id = ?",
                           (book_id,))
        for position, name in enumerate(names):
            # One spelling per genre, or the Genres browse splits in two.
            row = connection.execute(
                "SELECT id FROM genre WHERE name = ? COLLATE NOCASE",
                (name,)).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO genre (name, sort_name) VALUES (?, ?)",
                    (name, fold(name)))
                row = connection.execute(
                    "SELECT id FROM genre WHERE name = ?", (name,)).fetchone()
            connection.execute(
                "INSERT OR IGNORE INTO book_genre (book_id, genre_id, position)"
                " VALUES (?, ?, ?)", (book_id, row["id"], position))

    def _mine(self, book_id: int, field: str, now: int) -> None:
        self._provenance.record(book_id, field, "user", "hand", locked=True,
                                now=now)
