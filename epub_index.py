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
from dataclasses import dataclass, field
from pathlib import Path

from epub_cbz import CbzMetadataReader
from epub_metadata import BookMetadata, DrmError, EpubMetadataReader, MetadataError
from epub_mobi import MobiMetadataReader
from epub_names import fold, title_sort_key
from epub_pdf import PdfMetadataReader
from epub_search import SearchIndex

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
    # Books nothing can open: encrypted by the shop that sold them.
    # Not failures — there is no fault to go looking for.
    locked: int = 0
    searchable: int = 0
    seconds: float = 0.0
    added_names: list[str] = field(default_factory=list)
    missing_names: list[str] = field(default_factory=list)
    failed_names: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(
            self.added or self.updated or self.moved or self.missing
            or self.returned or self.failed or self.locked or self.searchable
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
            (self.returned, "back"), (self.missing, "missing"),
            (self.failed, "failed"), (self.locked, "locked by their shop"),
            (self.searchable, "newly searchable"),
        ):
            if count:
                shown = self._books(count) if label == "new" else count
                parts.append(f"{shown} {label}")
        lines = [
            (f"Indexed — {', '.join(parts)}, {self.unchanged} unchanged "
             f"({self.seconds:.1f}s).")
        ]
        for mark, names in (("+", self.added_names), ("-", self.missing_names),
                            ("!", self.failed_names)):
            for name in names[:8]:
                lines.append(f"  {mark} {name}")
            if len(names) > 8:
                lines.append(f"  {mark} …and {len(names) - 8} more")
        return "\n".join(lines)

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

            with Image.open(io.BytesIO(data)) as opened:
                # RGB before JPEG, or a cover with an alpha channel
                # cannot be saved at all.
                image = opened.convert("RGB")
                image.thumbnail((COVER_MAX, COVER_MAX))
                image.save(target, "JPEG", quality=82, optimize=True)
            return rel
        except Exception as exc:
            LOG.debug("cover failed for %s: %s", content_hash[:12], exc)
            return None


class LibraryIndexer:
    def __init__(self, database, library, cover_root: Path) -> None:
        self._db = database
        self._library = library
        self._covers = CoverStore(cover_root)
        # One reader per container. The suffix chooses it, and a file
        # that turns out to be something else fails as that book alone.
        self._readers = {".epub": EpubMetadataReader()}
        for suffix in (".mobi", ".azw3", ".azw", ".prc"):
            self._readers[suffix] = MobiMetadataReader()
        self._readers[".pdf"] = PdfMetadataReader()
        # Zip or RAR, it is the same comic: the reader looks at
        # what the file *is*, not what it is called.
        self._readers[".cbz"] = CbzMetadataReader()
        self._readers[".cbr"] = self._readers[".cbz"]
        self._search = SearchIndex(database)
        self.hashed = 0  # observable so tests can assert we skip work

    @property
    def covers(self) -> CoverStore:
        """Shared with the backfill pass, so covers land in one place."""
        return self._covers

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
                except DrmError as exc:
                    # Not a failure: nothing is wrong with the file or
                    # with us. It simply cannot be opened by anything
                    # except the shop that sold it.
                    LOG.info("%s", exc)
                    self._forget_locked(path, report)
                    report.locked += 1
                except Exception as exc:
                    LOG.warning("indexing %s failed: %s", path, exc)
                    report.failed += 1

        self._drop_locked(report)
        self._mark_missing(seen, report)
        self._backfill_search(report)
        report.seconds = time.time() - started
        return report

    def _backfill_search(self, report: IndexReport) -> None:
        """Books indexed before the search table existed are 'unchanged'
        on every later run, so nothing would ever populate their search
        entry. Catch them up here."""
        rows = self._db.connection.execute(
            """
            SELECT m.book_id FROM metadata m
             WHERE NOT EXISTS (SELECT 1 FROM book_fts f WHERE f.rowid = m.book_id)
            """
        ).fetchall()
        for row in rows:
            self._search.reindex_book(row["book_id"])
        report.searchable += len(rows)
        self._heal()

    def _heal(self) -> None:
        """A title has a sort key and a search row as well as a column,
        and for a long time the passes wrote only the column (§12.3). So
        368 books sorted under the filename they no longer carried.
        Cheap enough to check on every scan — a few thousand short
        strings — and it means a copy that drifts is put back rather
        than lived with."""
        cursor = self._db.connection
        for row in cursor.execute(
                "SELECT book_id, title, title_sort FROM metadata").fetchall():
            key = title_sort_key(row["title"])
            if row["title"] is not None and key != row["title_sort"]:
                cursor.execute(
                    "UPDATE metadata SET title_sort = ? WHERE book_id = ?",
                    (key, row["book_id"]))
        stale = self._search.stale()
        for book_id in stale:
            self._search.reindex_book(book_id)
        if stale:
            LOG.info("search caught up with %d books", len(stale))
        orphans = self._search.drop_orphans()
        if orphans:
            LOG.info("search forgot %d books that are gone", orphans)

    # ── walking ───────────────────────────────────────────────────────
    def _walk(self, root: Path):
        """Every book under the root, of any container.

        A directory named *.epub is a book — 265 of the real library are
        stored that way — so the walk stops there rather than descending
        into it and finding a hundred documents.
        """
        suffixes = self._library.SUFFIXES
        for path in sorted(root.iterdir()):
            if path.is_dir():
                if self._is_unpacked_book(path):
                    yield path
                else:
                    yield from self._walk(path)
            elif path.suffix.lower() in suffixes:
                yield path

    @staticmethod
    def _stamp(path: Path) -> tuple[int, int]:
        """What a book looks like without opening it: total size and
        most recent change.

        A directory's own mtime does not move when a file inside it is
        edited, so an unpacked book is measured by its contents — or
        every scan would call an edited book unchanged.
        """
        if not path.is_dir():
            stat = path.stat()
            return stat.st_size, int(stat.st_mtime)
        size = mtime = 0
        for inner in path.rglob("*"):
            if inner.is_file():
                stat = inner.stat()
                size += stat.st_size
                mtime = max(mtime, int(stat.st_mtime))
        return size, mtime

    @staticmethod
    def _is_unpacked_book(path: Path) -> bool:
        """A directory that is a book rather than a shelf. The name and
        a container are both asked for: "Drive.epub" holding nothing but
        a readme is somebody's notes."""
        return (path.suffix.lower() == ".epub"
                and (path / "META-INF" / "container.xml").is_file())

    # ── per book ──────────────────────────────────────────────────────
    def _index_one(self, path: Path, report: IndexReport) -> int | None:
        rel = str(path.relative_to(self._library.root))
        # Asked before anything is done: a book nothing can open should
        # not be hashed, stored and then removed on every single scan.
        if path.suffix.lower() == ".epub" and self._readers[".epub"].is_locked(
            path
        ):
            raise DrmError(
                f"{path.name} is encrypted and can only be read where it was"
                " bought.")

        size, mtime = self._stamp(path)
        cursor = self._db.connection

        row = cursor.execute(
            "SELECT id, content_hash, size_bytes, mtime, missing_since "
            "FROM book WHERE path = ?", (rel,)
        ).fetchone()

        if row and row["size_bytes"] == size and row["mtime"] == mtime:
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
                (content_hash, size, mtime, row["id"]),
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
                (rel, size, mtime, existing["id"]),
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
                 size, mtime, now),
            )
            book_id = cursor.execute(
                "SELECT id FROM book WHERE content_hash = ?", (content_hash,)
            ).fetchone()["id"]
            report.added += 1
            report.added_names.append(rel)

        try:
            meta = self._reader_for(path).read(path)
        except DrmError:
            # Not a failure, and not this method's to record: the run
            # loop takes the book off the shelves entirely.
            raise
        except MetadataError as exc:
            # Keep whatever metadata we already had — a book that starts
            # failing should not lose its title.
            cursor.execute(
                "UPDATE book SET index_error = ?, indexed_at = ? WHERE id = ?",
                (str(exc), now, book_id),
            )
            report.failed += 1
            report.failed_names.append(rel)
            if report.added and not existing:
                report.added -= 1
                if rel in report.added_names:
                    report.added_names.remove(rel)
            return book_id

        self._store(book_id, content_hash, meta, now)
        return book_id

    def _hash(self, path: Path) -> str:
        digest = hashlib.sha256()
        if path.is_dir():
            # The contents, in a fixed order, each named before it is
            # read — so that moving a file within the book changes the
            # hash, as editing one does.
            for inner in sorted(p for p in path.rglob("*") if p.is_file()):
                digest.update(str(inner.relative_to(path).as_posix()).encode())
                digest.update(b"\0")
                self._feed(digest, inner)
        else:
            self._feed(digest, path)
        self.hashed += 1
        return digest.hexdigest()

    @staticmethod
    def _feed(digest, path: Path) -> None:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)

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

        # The file is the first word on a book's metadata, not the last:
        # a field corrected by hand or by enrichment keeps its value when
        # the book is read again. Without this, replacing a file puts the
        # wrong author straight back.
        spoken_for = self._spoken_for(book_id)
        values = {
            "title": meta.title, "title_sort": meta.title_sort,
            "synopsis": meta.description,
            "first_published": meta.first_published,
            "edition_date": meta.edition_date, "publisher": meta.publisher,
            "language": meta.language, "isbn13": meta.isbn13,
            "spine_length": meta.spine_length, "page_count": meta.page_count,
            "cover_path": cover_path,
            "series_id": series_id, "series_index": meta.series_index,
        }
        keep = [column for column in values if self._field(column) in spoken_for]
        assignments = ", ".join(
            f"{c}=excluded.{c}" for c in values if c not in keep
        )
        if cover_path is None and "cover_path" not in keep:
            # A book whose cover was already extracted keeps it rather
            # than losing it to a file that has none.
            assignments = assignments.replace(
                "cover_path=excluded.cover_path",
                "cover_path=COALESCE(excluded.cover_path, metadata.cover_path)")
        columns = ", ".join(values)
        cursor.execute(
            f"""
            INSERT INTO metadata (book_id, {columns})
            VALUES (?{", ?" * len(values)})
            ON CONFLICT(book_id) DO UPDATE SET {assignments or "book_id=book_id"}
            """,
            (book_id, *values.values()),
        )

        if "authors" not in spoken_for:
            cursor.execute("DELETE FROM book_author WHERE book_id = ?",
                           (book_id,))
            for position, person in enumerate(meta.authors):
                author_id = self._upsert("author", person.display, person.sort)
                cursor.execute(
                    "INSERT OR IGNORE INTO book_author (book_id, author_id,"
                    " role, position) VALUES (?, ?, 'author', ?)",
                    (book_id, author_id, position),
                )

        if "genres" in spoken_for:
            return self._finish(book_id, now)

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

        self._finish(book_id, now)

    def _finish(self, book_id: int, now: int) -> None:
        self._db.connection.execute(
            "UPDATE book SET indexed_at = ?, index_error = NULL WHERE id = ?",
            (now, book_id),
        )
        self._search.reindex_book(book_id)

    @staticmethod
    def _field(column: str) -> str:
        """The provenance field a metadata column belongs to. The sort
        key follows its title, and the series columns move together."""
        return {"title_sort": "title", "series_id": "series",
                "series_index": "series"}.get(column, column)

    def _spoken_for(self, book_id: int) -> set[str]:
        """Fields the file no longer owns: corrected by hand, filled in
        by a provider, or locked."""
        return {
            row["field"] for row in self._db.connection.execute(
                "SELECT field, origin, locked FROM field_provenance"
                " WHERE book_id = ?", (book_id,))
            if row["locked"] or row["origin"] in ("user", "provider")
        }

    def _drop_locked(self, report: IndexReport) -> None:
        """Books listed before the lock was understood.

        A scan short-circuits on an unchanged file, so these would stay
        on the shelves for ever otherwise. Asking costs a tenth of a
        second across 1,600 books, which is cheaper than a command
        somebody has to remember.
        """
        reader = self._readers.get(".epub")
        rows = self._db.connection.execute(
            "SELECT id, path FROM book WHERE format = 'epub'"
            " AND missing_since IS NULL").fetchall()
        for row in rows:
            path = self._library.root / row["path"]
            if not path.exists() or not reader.is_locked(path):
                continue
            LOG.info("%s is locked by the shop that sold it; removing",
                     row["path"])
            self._delete_book(row["id"])
            report.locked += 1

    def _forget_locked(self, path: Path, report: IndexReport) -> None:
        """A locked book that was listed before this was understood
        comes off the shelves — excluding it means excluding it, not
        leaving it there unreadable.

        Its row is created before its metadata is read, so a book found
        to be locked has already been counted as new. It is not new; it
        is not anything.
        """
        rel = str(path.relative_to(self._library.root))
        row = self._db.connection.execute(
            "SELECT id FROM book WHERE path = ?", (rel,)).fetchone()
        if row:
            self._delete_book(row["id"])
        if rel in report.added_names:
            report.added_names.remove(rel)
            report.added -= 1

    def _delete_book(self, book_id: int) -> None:
        """Remove a book row, and free anything pointing at it.

        Two references to a book row have no cascade, and both took the
        scan down in turn: a copy hidden as a duplicate points at the
        copy that was kept, and "Also published" links each work to the
        copy you own. Neither should disappear with the book — the
        hidden copy comes back into view, and the work stays on its
        author's shelf as one you no longer own.
        """
        cursor = self._db.connection
        cursor.execute(
            "UPDATE book SET duplicate_of = NULL WHERE duplicate_of = ?",
            (book_id,))
        cursor.execute(
            "UPDATE author_work SET book_id = NULL WHERE book_id = ?",
            (book_id,))
        cursor.execute("DELETE FROM book WHERE id = ?", (book_id,))

    def _reader_for(self, path: Path):
        reader = self._readers.get(path.suffix.lower())
        if reader is None:
            raise MetadataError(f"{path.name} is not a format this reads.")
        return reader

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
            "SELECT id, path FROM book WHERE missing_since IS NULL"
        ).fetchall()
        gone = [(r["id"], r["path"]) for r in rows if r["id"] not in seen]
        if not gone:
            return
        now = int(time.time())
        cursor.executemany(
            "UPDATE book SET missing_since = ? WHERE id = ?",
            [(now, book_id) for book_id, _ in gone],
        )
        report.missing += len(gone)
        report.missing_names.extend(path for _, path in gone)
