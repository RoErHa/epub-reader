"""Indexing the library into SQLite.

Shaped by what the real library turned out to be: 44% of books live in
subfolders, identity has to survive moves, some files are corrupt, and
re-scanning must not mean re-hashing tens of gigabytes.

So: walk recursively, skip anything whose size and mtime are unchanged,
hash only what is new or edited, and catch every per-book failure.
"""
from __future__ import annotations

import hashlib
import io
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from epub_metadata import BookMetadata, EpubMetadataReader, MetadataError
from epub_names import fold, title_sort_key

LOG = logging.getLogger("epub-reader.index")

COVER_MAX = 480  # px on the long edge — enough for a retina grid tile


@dataclass
class IndexReport:
    added: int = 0
    updated: int = 0
    moved: int = 0
    unchanged: int = 0
    missing: int = 0
    returned: int = 0
    failed: int = 0
    seconds: float = 0.0

    @property
    def has_changes(self) -> bool:
        return bool(
            self.added or self.updated or self.moved or self.missing
            or self.returned or self.failed
        )

    @staticmethod
    def _books(n: int) -> str:
        return f"{n} book" if n == 1 else f"{n} books"

    def summary(self) -> str:
        if not self.has_changes:
            return f"Library unchanged — no changes, {self._books(self.unchanged)}."
        parts = []
        for count, label in (
            (self.added, "new"), (self.updated, "updated"), (self.moved, "moved"),
            (self.returned, "back"), (self.missing, "missing"), (self.failed, "failed"),
        ):
            if count:
                parts.append(f"{self._books(count) if label == 'new' else count} {label}")
        return (
            f"Indexed — {', '.join(parts)}, {self.unchanged} unchanged "
            f"({self.seconds:.1f}s)."
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "added": self.added, "updated": self.updated, "moved": self.moved,
            "unchanged": self.unchanged, "missing": self.missing,
            "returned": self.returned, "failed": self.failed,
            "seconds": round(self.seconds, 2),
        }


class CoverStore:
    """Cover thumbnails on disk, keyed by content hash — never blobs in
    SQLite, mirroring dlna-gateway's art_cache."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def relative_path(self, content_hash: str) -> str:
        return f"{content_hash[:2]}/{content_hash}.jpg"

    def save(self, content_hash: str, data: bytes, suffix: str | None) -> str | None:
        if not data or suffix == ".svg":
            return None  # Pillow cannot rasterise SVG; skip rather than fail
        rel = self.relative_path(content_hash)
        target = self._root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                image = image.convert("RGB")
                image.thumbnail((COVER_MAX, COVER_MAX))
                image.save(target, "JPEG", quality=82, optimize=True)
            return rel
        except Exception as exc:  # noqa: BLE001 — a bad cover is not fatal
            LOG.debug("cover failed for %s: %s", content_hash[:12], exc)
            return None


class LibraryIndexer:
    def __init__(self, database, library, cover_root: Path) -> None:
        self._db = database
        self._library = library
        self._covers = CoverStore(cover_root)
        self._reader = EpubMetadataReader()
        self.hashed = 0  # observable so tests can assert we skip work

    # ── public ────────────────────────────────────────────────────────
    def run(self) -> IndexReport:
        started = time.time()
        report = IndexReport()
        root = self._library.root
        seen: set[int] = set()

        if root.is_dir():
            for path in self._walk(root):
                try:
                    book_id = self._index_one(path, report)
                    if book_id:
                        seen.add(book_id)
                except Exception as exc:  # noqa: BLE001 — never stop the run
                    LOG.warning("indexing %s failed: %s", path, exc)
                    report.failed += 1

        self._mark_missing(seen, report)
        report.seconds = time.time() - started
        return report

    # ── walking ───────────────────────────────────────────────────────
    def _walk(self, root: Path):
        suffixes = self._library.SUFFIXES
        for path in sorted(root.rglob("*")):
            # Unpacked EPUB directories (262 of them in the real library)
            # are not handled yet — they need a directory-serving route.
            if path.is_file() and path.suffix.lower() in suffixes:
                yield path

    # ── per book ──────────────────────────────────────────────────────
    def _index_one(self, path: Path, report: IndexReport) -> int | None:
        rel = str(path.relative_to(self._library.root))
        stat = path.stat()
        cursor = self._db.connection

        row = cursor.execute(
            "SELECT id, content_hash, size_bytes, mtime, missing_since "
            "FROM book WHERE path = ?", (rel,)
        ).fetchone()

        if row and row["size_bytes"] == stat.st_size and row["mtime"] == int(
            stat.st_mtime
        ):
            if row["missing_since"] is not None:
                cursor.execute(
                    "UPDATE book SET missing_since = NULL WHERE id = ?", (row["id"],)
                )
                report.returned += 1
            else:
                report.unchanged += 1
            return row["id"]

        content_hash = self._hash(path)
        existing = cursor.execute(
            "SELECT id, path FROM book WHERE content_hash = ?", (content_hash,)
        ).fetchone()

        now = int(time.time())
        if row and not existing:
            # Same path, new bytes: the file was edited or replaced with a
            # better copy — common in a real library. Update the row in
            # place rather than orphaning it and adding a duplicate.
            cursor.execute(
                "UPDATE book SET content_hash = ?, size_bytes = ?, mtime = ?, "
                "missing_since = NULL WHERE id = ?",
                (content_hash, stat.st_size, int(stat.st_mtime), row["id"]),
            )
            book_id = row["id"]
            report.updated += 1
        elif existing:
            # Same bytes under a different path. If the recorded path is
            # still on disk this is a duplicate copy, not a move — leave
            # the row alone, or the two paths trade places every run.
            recorded = self._library.root / existing["path"]
            if existing["path"] != rel and recorded.is_file():
                report.unchanged += 1
                return existing["id"]
            moved = existing["path"] != rel
            cursor.execute(
                "UPDATE book SET path = ?, size_bytes = ?, mtime = ?, "
                "missing_since = NULL WHERE id = ?",
                (rel, stat.st_size, int(stat.st_mtime), existing["id"]),
            )
            book_id = existing["id"]
            if moved:
                report.moved += 1
                return book_id  # same bytes: metadata cannot have changed
            report.updated += 1
        else:
            cursor.execute(
                "INSERT INTO book (content_hash, path, format, size_bytes, mtime,"
                " added_at) VALUES (?, ?, ?, ?, ?, ?)",
                (content_hash, rel, path.suffix.lstrip(".").lower(),
                 stat.st_size, int(stat.st_mtime), now),
            )
            book_id = cursor.execute(
                "SELECT id FROM book WHERE content_hash = ?", (content_hash,)
            ).fetchone()["id"]
            report.added += 1

        try:
            meta = self._reader.read(path)
        except MetadataError as exc:
            # Keep whatever metadata we already had — a book that starts
            # failing should not lose its title.
            cursor.execute(
                "UPDATE book SET index_error = ?, indexed_at = ? WHERE id = ?",
                (str(exc), now, book_id),
            )
            report.failed += 1
            if report.added and not existing:
                report.added -= 1
            return book_id

        self._store(book_id, content_hash, meta, now)
        return book_id

    def _hash(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        self.hashed += 1
        return digest.hexdigest()

    # ── writing ───────────────────────────────────────────────────────
    def _store(self, book_id: int, content_hash: str, meta: BookMetadata,
               now: int) -> None:
        cursor = self._db.connection
        cover_path = None
        if meta.cover_data:
            cover_path = self._covers.save(
                content_hash, meta.cover_data, meta.cover_suffix
            )

        series_id = None
        if meta.series:
            series_id = self._upsert(
                "series", meta.series, title_sort_key(meta.series)
            )

        cursor.execute(
            """
            INSERT INTO metadata (book_id, title, title_sort, synopsis,
                first_published, edition_date, publisher, language, isbn13,
                spine_length, cover_path, series_id, series_index)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(book_id) DO UPDATE SET
                title=excluded.title, title_sort=excluded.title_sort,
                synopsis=excluded.synopsis,
                first_published=excluded.first_published,
                edition_date=excluded.edition_date,
                publisher=excluded.publisher, language=excluded.language,
                isbn13=excluded.isbn13, spine_length=excluded.spine_length,
                cover_path=COALESCE(excluded.cover_path, metadata.cover_path),
                series_id=excluded.series_id, series_index=excluded.series_index
            """,
            (book_id, meta.title, meta.title_sort, meta.description,
             meta.first_published, meta.edition_date, meta.publisher,
             meta.language, meta.isbn13, meta.spine_length, cover_path,
             series_id, meta.series_index),
        )

        cursor.execute("DELETE FROM book_author WHERE book_id = ?", (book_id,))
        for position, person in enumerate(meta.authors):
            author_id = self._upsert("author", person.display, person.sort)
            cursor.execute(
                "INSERT OR IGNORE INTO book_author (book_id, author_id, role,"
                " position) VALUES (?, ?, 'author', ?)",
                (book_id, author_id, position),
            )

        cursor.execute("DELETE FROM book_genre WHERE book_id = ?", (book_id,))
        for position, subject in enumerate(meta.subjects):
            name = subject.strip()
            if not name:
                continue
            genre_id = self._upsert("genre", name, fold(name))
            cursor.execute(
                "INSERT OR IGNORE INTO book_genre (book_id, genre_id, position)"
                " VALUES (?, ?, ?)",
                (book_id, genre_id, position),
            )

        cursor.execute(
            "UPDATE book SET indexed_at = ?, index_error = NULL WHERE id = ?",
            (now, book_id),
        )

    def _upsert(self, table: str, name: str, sort_name: str) -> int:
        cursor = self._db.connection
        cursor.execute(
            f"INSERT OR IGNORE INTO {table} (name, sort_name) VALUES (?, ?)",
            (name, sort_name),
        )
        return cursor.execute(
            f"SELECT id FROM {table} WHERE name = ?", (name,)
        ).fetchone()["id"]

    def _mark_missing(self, seen: set[int], report: IndexReport) -> None:
        cursor = self._db.connection
        rows = cursor.execute(
            "SELECT id FROM book WHERE missing_since IS NULL"
        ).fetchall()
        gone = [r["id"] for r in rows if r["id"] not in seen]
        if not gone:
            return
        now = int(time.time())
        cursor.executemany(
            "UPDATE book SET missing_since = ? WHERE id = ?",
            [(now, book_id) for book_id in gone],
        )
        report.missing += len(gone)
