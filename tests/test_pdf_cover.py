"""Covers for PDFs — but only when page one really is one.

514 of the library's PDFs show a lettered tile. A third of them open on a
scan of the physical cover, and that is the picture we want; the rest
open on a title page, which is a worse tile than the letter it replaces.

So the rule is about what page one *is*, not about what the file is:

  a raster image covering the page, with no text layer over it.

That keeps text-born books whose first page is a proper cover image, and
refuses a publisher's title page, a page of words, and a blank flyleaf.
"""
from __future__ import annotations

import io

import pytest

from epub_pdf import PdfCoverRenderer
from tests.pdf_fixtures import PdfBuilder

pytest.importorskip("pypdfium2")
Image = pytest.importorskip("PIL.Image")


def jpeg(colour=(180, 40, 40), size=(600, 800), noise=True):
    """A picture, not a flat wash: a real cover has detail in it."""
    from PIL import Image as PilImage
    from PIL import ImageDraw
    img = PilImage.new("RGB", size, colour)
    if noise:
        draw = ImageDraw.Draw(img)
        for i in range(0, size[1], 40):
            draw.rectangle([20, i, size[0] - 20, i + 18],
                           fill=(250, 230, 90) if i % 80 else (20, 20, 60))
    out = io.BytesIO()
    img.save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def renderer():
    return PdfCoverRenderer()


class TestWhatCountsAsACover:
    def test_a_full_page_image_is_a_cover(self, tmp_path, renderer):
        book = PdfBuilder(cover=jpeg()).build(tmp_path / "scan.pdf")
        assert renderer.cover(book) is not None

    def test_what_comes_back_is_an_image(self, tmp_path, renderer):
        from PIL import Image as PilImage
        book = PdfBuilder(cover=jpeg()).build(tmp_path / "scan.pdf")
        img = PilImage.open(io.BytesIO(renderer.cover(book)))
        assert img.format == "JPEG"
        assert img.width == PdfCoverRenderer.WIDTH

    def test_a_page_of_text_is_not_a_cover(self, tmp_path, renderer):
        # A title page is a worse tile than the letter it would replace.
        book = PdfBuilder(pages=3).build(tmp_path / "text.pdf")
        assert renderer.cover(book) is None

    def test_a_small_image_is_not_a_cover(self, tmp_path, renderer):
        # A publisher's device on a title page covers a corner, not a page.
        book = PdfBuilder(cover=jpeg(), cover_fraction=0.25).build(
            tmp_path / "logo.pdf")
        assert renderer.cover(book) is None

    def test_words_over_the_image_are_not_a_cover(self, tmp_path, renderer):
        # A photographed page of words, or a scan with an OCR layer: the
        # image covers the page, but the page is text.
        book = PdfBuilder(cover=jpeg(), cover_text=True).build(
            tmp_path / "ocr.pdf")
        assert renderer.cover(book) is None

    def test_a_blank_scan_is_not_a_cover(self, tmp_path, renderer):
        # Scanners photograph the flyleaf too. One flat colour is not a
        # picture of anything, and a white tile says less than a letter.
        book = PdfBuilder(cover=jpeg(colour=(255, 255, 255), noise=False)
                          ).build(tmp_path / "blank.pdf")
        assert renderer.cover(book) is None


class TestRefusingRatherThanCrashing:
    """A scan runs over 2,000 books: one bad file must never stop it."""

    def test_a_file_that_is_not_a_pdf(self, tmp_path, renderer):
        rubbish = tmp_path / "not.pdf"
        rubbish.write_bytes(b"this is not a PDF at all")
        assert renderer.cover(rubbish) is None

    def test_a_file_that_is_not_there(self, tmp_path, renderer):
        assert renderer.cover(tmp_path / "absent.pdf") is None

    def test_the_book_file_is_never_written_to(self, tmp_path, renderer):
        book = PdfBuilder(cover=jpeg()).build(tmp_path / "scan.pdf")
        before = book.stat()
        renderer.cover(book)
        after = book.stat()
        assert (after.st_mtime_ns, after.st_size) == (before.st_mtime_ns,
                                                      before.st_size)


class TestReadingAPdfBook:
    """The reader is the way in for a new book, so a cover arrives with
    the rest of the metadata rather than as a second pass."""

    def test_a_scanned_pdf_brings_its_cover(self, tmp_path):
        from epub_pdf import PdfMetadataReader
        book = PdfBuilder(title="Scanned Book", cover=jpeg()).build(
            tmp_path / "Author, An - Scanned Book.pdf")
        meta = PdfMetadataReader().read(book)
        assert meta.cover_data
        assert meta.cover_suffix == ".jpg"

    def test_a_text_pdf_brings_none(self, tmp_path):
        from epub_pdf import PdfMetadataReader
        book = PdfBuilder(title="Text Book").build(
            tmp_path / "Author, An - Text Book.pdf")
        assert PdfMetadataReader().read(book).cover_data is None
