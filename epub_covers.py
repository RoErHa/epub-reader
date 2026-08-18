"""Covers for the books that have none.

A quarter of this library shows a lettered tile: 514 PDFs, 26 EPUBs and
15 PRCs. Their files have not changed since they were indexed, so a scan
short-circuits straight past them (§10.3) — closing the gap needs a pass
of its own, and this is it.

Two sources, in this order:

  the file    a PDF whose first page is the cover. Free, offline, and
              exactly right when it fires: no matching, no provider, no
              wrong edition. 158 of 514 qualify.
  a provider  for the rest, where the file holds no image at all.

Like every other maintenance pass it is **dry-run by default** and
**never writes to a book file**. Where a cover came from is recorded, so
a wrong one can be dropped for good: `origin = 'user'` on the `cover`
field means no pass fetches another.
"""
from __future__ import annotations

import io
import logging
import re
import time
from pathlib import Path

from epub_match import normalise_title, search_title
from epub_names import PersonName, fold
from epub_pdf import PdfCoverRenderer

LOG = logging.getLogger("epub-reader.covers")

# Open Library answers a cover it does not have with a 1-pixel image
# rather than a 404, and a browse tile is 150 points wide.
SMALLEST = 100


class CoverBackfill:
    """Give a cover to books that have none, from their own files."""

    def __init__(self, database, covers, library_root,
                 renderer: PdfCoverRenderer | None = None) -> None:
        self._db = database
        self._covers = covers
        self._root = Path(library_root)
        self._renderer = renderer or PdfCoverRenderer()

    # ── who is missing one ────────────────────────────────────────────
    def candidates(self, limit: int | None = None) -> list[dict]:
        """Books with no cover whose own file might hold one. Reads
        nothing and changes nothing."""
        rows = self._db.connection.execute(
            """
            SELECT b.id, b.path, b.format, b.content_hash,
                   COALESCE(m.title, b.path) AS title
              FROM book b
              JOIN metadata m ON m.book_id = b.id
             WHERE b.missing_since IS NULL
               AND (m.cover_path IS NULL OR m.cover_path = '')
               AND b.format = 'pdf'
               -- A cover a reader threw away stays thrown away.
               AND NOT EXISTS (
                     SELECT 1 FROM field_provenance f
                      WHERE f.book_id = b.id AND f.field = 'cover'
                        AND (f.origin = 'user' OR f.locked = 1))
             ORDER BY b.path
            """
        ).fetchall()
        rows = [dict(r) for r in rows]
        return rows[:limit] if limit else rows

    # ── doing it ──────────────────────────────────────────────────────
    def run(self, apply: bool = False, limit: int | None = None) -> dict:
        report = {"covered": 0, "would_cover": 0, "looked_at": 0,
                  "failed": 0, "plan": []}
        for book in self.candidates(limit):
            report["looked_at"] += 1
            path = self._root / book["path"]
            try:
                data = self._renderer.cover(path)
            except Exception as exc:              # noqa: BLE001
                LOG.info("cover failed for %s: %s", book["path"], exc)
                report["failed"] += 1
                continue
            if not data:
                # Page one is a title page, or the file has gone. Either
                # way the letter stays, which is the honest answer.
                if not path.exists():
                    report["failed"] += 1
                continue

            report["would_cover"] += 1
            report["plan"].append({"id": book["id"], "title": book["title"],
                                   "path": book["path"]})
            if not apply:
                continue
            stored = self._covers.save(book["content_hash"], data, ".jpg")
            if not stored:
                report["failed"] += 1
                continue
            self._db.connection.execute(
                "UPDATE metadata SET cover_path = ? WHERE book_id = ?",
                (stored, book["id"]))
            self._record(book["id"], "file", "pdfium")
            report["covered"] += 1
            LOG.info("cover from page one of %s", book["path"])
        return report

    def _record(self, book_id: int, origin: str, source: str) -> None:
        """Or the next pass would ask again, and a reader who threw a
        cover away would get another one."""
        self._db.connection.execute(
            """
            INSERT INTO field_provenance (book_id, field, origin, source,
                                          locked, updated_at)
            VALUES (?, 'cover', ?, ?, 0, ?)
            ON CONFLICT(book_id, field) DO UPDATE SET
                origin = excluded.origin, source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (book_id, origin, source, int(time.time())),
        )

    @staticmethod
    def summary(report: dict) -> str:
        if not report["looked_at"]:
            return "Covers: every book that can have one already does."
        if report["covered"]:
            line = (f"Covers: {report['covered']} books took their cover from"
                    f" page one, of {report['looked_at']} looked at")
        elif report["would_cover"]:
            line = (f"Covers: {report['would_cover']} of {report['looked_at']}"
                    " books open on their own cover")
        else:
            line = (f"Covers: none of the {report['looked_at']} books opens on"
                    " a cover; they keep their letter")
        if report["failed"]:
            line += f", {report['failed']} unreadable"
        return line + "."


class AuthorHint:
    """The author a filename names, when the database has none.

    291 of the books without a cover have no author at all — their
    metadata came from a filename, and `epub_pdf` reads the author from
    the Info dictionary only. But 249 of those filenames say who wrote
    it, at one end or the other:

        Anthony, Piers - Mute
        Cato 02 - The Eagles Conquest - Simon Scarrow

    This is a hint for matching and nothing more: it is never written to
    the database, because deciding who wrote a book is what the repair
    passes are for (§6.3).
    """

    # "Surname, First" at the head is unambiguous; a bare name is not.
    SURNAME_FIRST = re.compile(r"^\s*([^\d,]{2,30}),\s*([^\d,-]{2,30}?)\s*(?:-|$)")

    def __init__(self, known: set[str] | None = None) -> None:
        # Surnames the library already believes in, for the cases where
        # the name sits at the end and could be anything.
        self._known = {fold(k) for k in (known or set())}

    def surname(self, stem: str) -> str | None:
        """The surname the filename names, or None when it names none."""
        stem = (stem or "").replace("_", " ").strip()
        head = self.SURNAME_FIRST.match(stem)
        if head:
            return fold(head.group(1).strip()) or None

        # Otherwise the name can only be at the end, and only counts
        # when the library knows it: "The Eagle and the Wolves" must not
        # become a Mr Wolves.
        parts = [p.strip() for p in stem.split(" - ") if p.strip()]
        if len(parts) < 2:
            return None
        tail = re.sub(r"\s*[(\[].*", "", parts[-1]).strip()
        if not 2 <= len(tail.split()) <= 4 or any(c.isdigit() for c in tail):
            return None
        person = PersonName.parse(tail)
        if person is None:
            return None
        surname = fold(person.sort.partition(",")[0].strip())
        return surname if surname and surname in self._known else None


class CoverMatch:
    """Is this candidate the book, for the purpose of a picture?

    The enrichment score is deliberately conservative about fields we do
    not have (§10.12) — with no ISBN, no year and often no author it
    cannot exceed 0.5, so it rejects nearly every book here. A cover
    needs its own bar, and the honest one is that **the titles agree**
    once the filename scaffolding is off both of them.

    A wrong cover is worse than no cover: it makes you distrust the
    shelf. So this errs towards the lettered tile.
    """

    # What a match is worth. The title is the guard; the author is
    # corroboration, and its absence is not proof of anything.
    TITLE_AND_AUTHOR = 2
    TITLE_ONLY = 1
    NO = 0

    def rank(self, book: dict, candidate) -> int:
        """How good an answer this is: agreeing on the author as well as
        the title is better, but a name can be spelt more than one way.

        The library holds *The Art of War* by Sunzi and Open Library
        files it under Sun Tzu — both are that man, and vetoing on the
        spelling costs a cover for nothing. So the author ranks answers
        rather than refusing them, and a title-only match is recorded as
        one so an audit can look harder at exactly those.
        """
        if not getattr(candidate, "cover_url", None):
            return self.NO
        ours = fold(search_title(book.get("title") or ""))
        theirs = fold(normalise_title(candidate.title or ""))
        if not ours or not theirs or ours != theirs:
            return self.NO

        surname = fold(book.get("author_surname") or "")
        if not surname:
            return self.TITLE_ONLY
        written = fold(" ".join(candidate.authors or []))
        agrees = surname in written.split() or surname in written
        return self.TITLE_AND_AUTHOR if agrees else self.TITLE_ONLY

    def accepts(self, book: dict, candidate) -> bool:
        return self.rank(book, candidate) > self.NO


class ProviderCovers:
    """Covers for the books whose own files hold no image.

    411 books here, and about 70% of them get an offer from Open
    Library. Roughly three in four of those offers are the right book,
    which is why `CoverMatch` exists and why the rest keep their letter.

    **Asked once, ever.** A miss is written down like a hit — §6.6's
    rule, and at one request every two minutes asking twice is somebody's
    whole afternoon.
    """

    def __init__(self, database, covers, library_root, provider,
                 fetch_image, match: CoverMatch | None = None) -> None:
        self._db = database
        self._covers = covers
        self._root = Path(library_root)
        self._provider = provider
        self._fetch_image = fetch_image
        self._match = match or CoverMatch()
        self._hint = AuthorHint(self._known_surnames())

    # ── who still needs one ───────────────────────────────────────────
    def candidates(self, limit: int | None = None) -> list[dict]:
        rows = self._db.connection.execute(
            """
            SELECT b.id, b.path, b.format, b.content_hash, m.title,
                   m.isbn13, m.first_published, m.language,
                   (SELECT a.name FROM book_author ba
                      JOIN author a ON a.id = ba.author_id
                     WHERE ba.book_id = b.id ORDER BY ba.position LIMIT 1)
                     AS author
              FROM book b
              JOIN metadata m ON m.book_id = b.id
             WHERE b.missing_since IS NULL
               AND b.duplicate_of IS NULL
               AND (m.cover_path IS NULL OR m.cover_path = '')
               AND m.title IS NOT NULL AND m.title <> ''
               -- Asked before, or thrown away by hand: either way, done.
               AND NOT EXISTS (
                     SELECT 1 FROM field_provenance f
                      WHERE f.book_id = b.id AND f.field = 'cover')
             ORDER BY b.path
            """
        ).fetchall()
        books = [dict(r) for r in rows]
        return books[:limit] if limit else books

    def _known_surnames(self) -> set[str]:
        return {row["sort_name"].partition(",")[0].strip()
                for row in self._db.connection.execute(
                    "SELECT sort_name FROM author WHERE sort_name <> ''")}

    # ── doing it ──────────────────────────────────────────────────────
    def run(self, apply: bool = False, limit: int | None = None) -> dict:
        report = {"covered": 0, "would_cover": 0, "asked": 0, "nothing": 0,
                  "failed": 0, "plan": []}
        for book in self.candidates(limit):
            book["author_surname"] = self._surname(book)
            report["asked"] += 1
            try:
                found = self._provider.find_books(
                    search_title(book["title"]), self._query_author(book))
            except Exception as exc:              # noqa: BLE001
                # A provider having a bad day is not this book's fault,
                # and a failed lookup is not written down — it stays in
                # the queue for the next run.
                LOG.info("lookup failed for %s: %s", book["title"], exc)
                report["failed"] += 1
                continue

            # The best answer, not the first: an edition by the author we
            # know beats one by a name we do not recognise.
            ranked = sorted(((self._match.rank(book, c), i, c)
                             for i, c in enumerate(found)),
                            key=lambda t: (-t[0], t[1]))
            best = ranked[0] if ranked and ranked[0][0] > CoverMatch.NO else None
            chosen = best[2] if best else None
            if chosen is None:
                report["nothing"] += 1
                if apply:
                    self._record(book["id"], "provider", "openlibrary-none")
                continue

            report["would_cover"] += 1
            report["plan"].append({"id": book["id"], "title": book["title"],
                                   "matched": chosen.title})
            if not apply:
                continue
            if self._store(book, chosen, best[0]):
                report["covered"] += 1
            else:
                report["failed"] += 1
        return report

    def _store(self, book: dict, chosen, rank: int) -> bool:
        try:
            data = self._fetch_image(chosen.cover_url)
        except Exception as exc:                  # noqa: BLE001
            LOG.info("cover fetch failed for %s: %s", book["title"], exc)
            return False
        if not self._is_a_picture(data):
            # A one-pixel placeholder is how Open Library says "no".
            self._record(book["id"], "provider", "openlibrary-none")
            return False
        stored = self._covers.save(book["content_hash"], data, ".jpg")
        if not stored:
            return False
        self._db.connection.execute(
            "UPDATE metadata SET cover_path = ? WHERE book_id = ?",
            (stored, book["id"]))
        # How sure we were is part of the record, so a wrong cover can
        # be looked for where wrong covers live.
        self._record(book["id"], "provider",
                     "openlibrary" if rank == CoverMatch.TITLE_AND_AUTHOR
                     else "openlibrary-title")
        LOG.info("cover for %r from %r%s", book["title"], chosen.title,
                 "" if rank == CoverMatch.TITLE_AND_AUTHOR else " (title only)")
        return True

    @staticmethod
    def _is_a_picture(data: bytes | None) -> bool:
        if not data:
            return False
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as image:
                return min(image.size) >= SMALLEST
        except Exception:                         # noqa: BLE001
            return False

    def _surname(self, book: dict) -> str | None:
        if book.get("author"):
            person = PersonName.parse(book["author"])
            if person:
                return fold(person.sort.partition(",")[0].strip())
        return self._hint.surname(Path(book["path"]).stem)

    def _query_author(self, book: dict) -> str | None:
        """What to ask with. A surname is enough for Open Library and
        safer than a spelling we are not sure of."""
        return book.get("author") or book.get("author_surname")

    def _record(self, book_id: int, origin: str, source: str) -> None:
        CoverBackfill._record(self, book_id, origin, source)  # noqa: SLF001

    @staticmethod
    def summary(report: dict) -> str:
        if not report["asked"]:
            return "Covers: nobody left to ask about."
        if report["covered"]:
            line = (f"Covers: {report['covered']} of {report['asked']} books"
                    " took a cover from Open Library")
        elif report["would_cover"]:
            line = (f"Covers: {report['would_cover']} of {report['asked']}"
                    " books have one to take")
        else:
            line = (f"Covers: none of the {report['asked']} books asked about"
                    " has a cover there")
        if report["failed"]:
            line += f", {report['failed']} could not be asked"
        return line + "."
