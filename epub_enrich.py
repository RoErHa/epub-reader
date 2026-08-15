"""Writing what a provider found into the index.

The provenance rule from §5 is what makes this safe to re-run across a
whole library: **a value the reader set by hand is never overwritten
automatically.** Only fields whose origin is the file or a provider are
refreshed.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from epub_names import fold
from epub_providers import ProviderError

LOG = logging.getLogger("epub-reader.enrich")


class AuthorEnricher:
    def __init__(self, database, provider, cover_root: Path | None = None) -> None:
        self._db = database
        self._provider = provider
        self._covers = Path(cover_root) if cover_root else None

    def enrich(self, author_id: int) -> dict:
        row = self._db.connection.execute(
            "SELECT id, name, bio, bio_source FROM author WHERE id = ?",
            (author_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"No author with id {author_id}.")

        now = int(time.time())
        try:
            match = self._provider.find_author(row["name"])
        except ProviderError as exc:
            # Never fatal: a provider being down is not the reader's problem.
            LOG.warning("enriching %s failed: %s", row["name"], exc)
            return {"matched": False, "works": 0, "error": str(exc)}

        if match is None:
            # Record the attempt so the screen stops promising a bio.
            self._db.connection.execute(
                "UPDATE author SET fetched_at = ? WHERE id = ?", (now, author_id)
            )
            return {"matched": False, "works": 0,
                    "reason": f"No author called {row['name']!r} on Open Library."}

        try:
            details = self._provider.author_details(match["key"])
            works = self._provider.author_works(match["key"])
        except ProviderError as exc:
            LOG.warning("enriching %s failed: %s", row["name"], exc)
            return {"matched": False, "works": 0, "error": str(exc)}

        self._store_author(author_id, row, details, now)
        stored = self._store_works(author_id, works, now)
        return {"matched": True, "key": match["key"], "works": stored,
                "bio": bool(details.get("bio"))}

    # ── writing ───────────────────────────────────────────────────────
    def _store_author(self, author_id: int, row, details: dict, now: int) -> None:
        # A bio the reader wrote is theirs; we only refresh our own.
        keep_bio = row["bio_source"] == "user" and row["bio"]
        bio = row["bio"] if keep_bio else details.get("bio")
        source = "user" if keep_bio else ("openlibrary" if details.get("bio") else None)

        self._db.connection.execute(
            """
            UPDATE author
               SET bio = ?, bio_source = ?, born = COALESCE(?, born),
                   died = COALESCE(?, died), website = COALESCE(?, website),
                   fetched_at = ?
             WHERE id = ?
            """,
            (bio, source, details.get("born"), details.get("died"),
             details.get("website"), now, author_id),
        )

    def _store_works(self, author_id: int, works: list[dict], now: int) -> int:
        cursor = self._db.connection
        # The provider returns the whole bibliography each time, so this
        # replaces rather than merges. A UNIQUE(author_id, title, year)
        # would not have held: SQLite treats NULL years as distinct, so
        # every undated work would duplicate on the second run.
        cursor.execute(
            "DELETE FROM author_work WHERE author_id = ? AND source = ?",
            (author_id, "openlibrary"),
        )
        owned = self._owned_titles(author_id)
        for work in works:
            cursor.execute(
                """
                INSERT INTO author_work
                    (author_id, title, first_published, work_type, source,
                     fetched_at, book_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (author_id, work["title"], work.get("first_published"),
                 work.get("work_type", "unknown"), work.get("source", "openlibrary"),
                 now, owned.get(fold(work["title"]))),
            )
        return len(works)

    def _owned_titles(self, author_id: int) -> dict[str, int]:
        """Folded title → book id, so punctuation and accents do not keep
        a book off its own author's bibliography. Read once per run
        rather than once per work."""
        rows = self._db.connection.execute(
            """
            SELECT b.id, m.title FROM book b
              JOIN metadata m ON m.book_id = b.id
              JOIN book_author ba ON ba.book_id = b.id
             WHERE ba.author_id = ? AND b.missing_since IS NULL
            """,
            (author_id,),
        ).fetchall()
        return {fold(r["title"] or ""): r["id"] for r in rows if r["title"]}
