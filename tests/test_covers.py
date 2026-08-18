"""Finding covers for the books that have none.

A quarter of the library shows a lettered tile — 514 PDFs, 26 EPUBs and
15 PRCs whose files carry no usable image. Those books were indexed long
ago and their files have not changed, so a scan short-circuits past them
(§10.3): closing the gap needs a pass of its own.

Like every other maintenance pass it is **dry-run by default** and
**never writes to a book file**.
"""
from __future__ import annotations

import io

import pytest

from epub_covers import CoverBackfill
from tests.pdf_fixtures import PdfBuilder

pytest.importorskip("pypdfium2")


def jpeg(colour=(30, 90, 160)):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (600, 800), colour)
    draw = ImageDraw.Draw(img)
    for i in range(0, 800, 40):
        draw.rectangle([20, i, 580, i + 18],
                       fill=(250, 230, 90) if i % 80 else (10, 10, 40))
    out = io.BytesIO()
    img.save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def library_of_pdfs(client, library, gateway):
    """Two PDFs: one that opens on its cover, one that opens on words."""
    for existing in library.glob("*"):
        if existing.is_file():
            existing.unlink()
    PdfBuilder(title="Scanned Book", cover=jpeg()).build(
        library / "Scanner, A - Scanned Book.pdf")
    PdfBuilder(title="Wordy Book").build(library / "Writer, A - Wordy Book.pdf")
    gateway.reindex()
    return gateway


def strip_covers(gateway):
    """The state the real library is in: indexed before covers existed."""
    gateway.database.connection.execute("UPDATE metadata SET cover_path = NULL")


def backfill(gateway):
    return CoverBackfill(gateway.database, gateway.covers, gateway.library_root)


def cover_of(gateway, title):
    return gateway.database.connection.execute(
        "SELECT cover_path FROM metadata m JOIN book b ON b.id = m.book_id"
        " WHERE m.title = ?", (title,)).fetchone()["cover_path"]


class TestLookingWithoutChangingAnything:
    def test_a_dry_run_finds_the_cover_but_writes_nothing(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        report = backfill(gateway).run(apply=False)

        assert report["would_cover"] == 1
        assert report["covered"] == 0
        assert cover_of(gateway, "Scanned Book") is None

    def test_it_says_which_books(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        titles = [b["title"] for b in backfill(gateway).run()["plan"]]
        assert titles == ["Scanned Book"]

    def test_the_book_file_is_never_written_to(self, library_of_pdfs, library):
        gateway = library_of_pdfs
        strip_covers(gateway)
        book = library / "Scanner, A - Scanned Book.pdf"
        before = book.stat()
        backfill(gateway).run(apply=True)
        assert book.stat().st_mtime_ns == before.st_mtime_ns


class TestApplying:
    def test_the_cover_is_stored(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        report = backfill(gateway).run(apply=True)

        assert report["covered"] == 1
        stored = cover_of(gateway, "Scanned Book")
        assert stored
        assert (gateway.covers.root / stored).exists()

    def test_a_book_of_words_keeps_its_letter(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        backfill(gateway).run(apply=True)
        assert cover_of(gateway, "Wordy Book") is None

    def test_a_book_that_already_has_one_is_left_alone(self, library_of_pdfs):
        # Covers extracted at index time are not re-derived: this pass is
        # for the gap, not a second opinion.
        gateway = library_of_pdfs
        report = backfill(gateway).run(apply=True)
        assert report["would_cover"] == 0

    def test_where_it_came_from_is_recorded(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        backfill(gateway).run(apply=True)
        row = gateway.database.connection.execute(
            "SELECT origin, source FROM field_provenance WHERE field = 'cover'"
        ).fetchone()
        assert (row["origin"], row["source"]) == ("file", "pdfium")

    def test_running_it_twice_changes_nothing(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        backfill(gateway).run(apply=True)
        assert backfill(gateway).run(apply=True)["covered"] == 0


class TestRefusingRatherThanCrashing:
    def test_a_book_whose_file_has_gone_is_skipped(self, library_of_pdfs, library):
        gateway = library_of_pdfs
        strip_covers(gateway)
        (library / "Scanner, A - Scanned Book.pdf").unlink()
        report = backfill(gateway).run(apply=True)
        assert report["covered"] == 0
        assert report["failed"] == 1

    def test_a_cover_a_reader_removed_is_not_fetched_again(self, library_of_pdfs):
        # Dropping a wrong cover is a fact about the book, like a
        # corrected author: `origin = 'user'` and no pass touches it.
        gateway = library_of_pdfs
        strip_covers(gateway)
        book_id = gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title = 'Scanned Book'"
        ).fetchone()["book_id"]
        gateway.database.connection.execute(
            "INSERT INTO field_provenance (book_id, field, origin, source,"
            " locked, updated_at) VALUES (?, 'cover', 'user', 'hand', 1, 0)",
            (book_id,))

        assert backfill(gateway).run(apply=True)["covered"] == 0

    def test_only_so_many_at_a_time(self, library_of_pdfs, library):
        gateway = library_of_pdfs
        PdfBuilder(title="Another Scan", cover=jpeg((120, 20, 90))).build(
            library / "Scanner, A - Another Scan.pdf")
        gateway.reindex()
        strip_covers(gateway)
        assert backfill(gateway).run(apply=True, limit=1)["covered"] == 1


class TestThrowingACoverAway:
    """Page one is a cover 91% of the time; the rest are title pages,
    and a reader can say so. What they say is theirs and locked, so no
    later pass fetches another — the same rule as a corrected author."""

    def test_the_cover_goes(self, library_of_pdfs, client):
        gateway = library_of_pdfs
        book_id = gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title = 'Scanned Book'"
        ).fetchone()["book_id"]
        assert cover_of(gateway, "Scanned Book")

        assert client.delete(f"/api/books/{book_id}/cover").status_code == 200
        assert cover_of(gateway, "Scanned Book") is None

    def test_the_book_shows_a_letter_again(self, library_of_pdfs, client):
        gateway = library_of_pdfs
        book_id = gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title = 'Scanned Book'"
        ).fetchone()["book_id"]
        client.delete(f"/api/books/{book_id}/cover")
        book = client.get(f"/api/books/{book_id}/detail").json()["book"]
        assert book["cover"] is None

    def test_it_is_not_fetched_again(self, library_of_pdfs, client):
        gateway = library_of_pdfs
        book_id = gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title = 'Scanned Book'"
        ).fetchone()["book_id"]
        client.delete(f"/api/books/{book_id}/cover")
        assert backfill(gateway).run(apply=True)["covered"] == 0

    def test_the_book_file_is_untouched(self, library_of_pdfs, client, library):
        gateway = library_of_pdfs
        book = library / "Scanner, A - Scanned Book.pdf"
        before = book.stat()
        book_id = gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title = 'Scanned Book'"
        ).fetchone()["book_id"]
        client.delete(f"/api/books/{book_id}/cover")
        assert book.stat().st_mtime_ns == before.st_mtime_ns

    def test_an_unknown_book_is_a_404(self, library_of_pdfs, client):
        assert client.delete("/api/books/99999/cover").status_code == 404
