"""Reading what little a PDF will tell you about itself.

Measured on the real library — 585 PDFs — the embedded metadata is
mostly worthless:

    151  (empty)
     76  'Local Disk'
     39  'Book Cover' / 'BOOK COVER'
      5  'Contents'
        … 129 distinct values across 585 files

while the *filenames* are excellent and follow the same convention the
repair passes already understand: "Anthony, Piers - Mute.pdf".

So the filename leads and the embedded title is taken only when it is
plausibly better. That is the opposite of EPUB and MOBI, and it is what
the evidence says.
"""
from __future__ import annotations

import pytest

from epub_metadata import MetadataError
from epub_pdf import PdfMetadataReader
from tests.pdf_fixtures import PdfBuilder


@pytest.fixture
def reader():
    return PdfMetadataReader()


class TestWhatTheFileSays:
    def test_a_real_title_is_taken(self, reader, tmp_path):
        path = PdfBuilder(title="The Left Hand of Darkness").build(
            tmp_path / "whatever.pdf")
        assert reader.read(path).title == "The Left Hand of Darkness"

    def test_the_author(self, reader, tmp_path):
        path = PdfBuilder(author="Ursula K. Le Guin").build(tmp_path / "a.pdf")
        assert [a.display for a in reader.read(path).authors] == \
            ["Ursula K. Le Guin"]

    def test_the_page_count(self, reader, tmp_path):
        path = PdfBuilder(pages=7).build(tmp_path / "a.pdf")
        assert reader.read(path).page_count == 7

    def test_subject_and_keywords_become_subjects(self, reader, tmp_path):
        path = PdfBuilder(subject="Science Fiction",
                          keywords="space, politics").build(tmp_path / "a.pdf")
        assert reader.read(path).subjects == ["Science Fiction", "space",
                                              "politics"]

    def test_a_creation_date_is_the_edition_not_the_work(self, reader, tmp_path):
        # A 2009 scan of a 1965 novel is not a 2009 book. §5 exists
        # because conflating those two is the commonest metadata error
        # there is, so first_published is left alone.
        path = PdfBuilder(created="D:20090115120000Z").build(tmp_path / "a.pdf")
        meta = reader.read(path)
        assert meta.edition_date == "2009-01-15"
        assert meta.first_published is None

    def test_a_utf16_title_is_decoded(self, reader, tmp_path):
        path = PdfBuilder(title="Céline").build(tmp_path / "a.pdf")
        assert "line" in reader.read(path).title


class TestWhenTheFileTalksNonsense:
    """Every one of these is a real value from the real library."""

    @pytest.mark.parametrize("junk", [
        "", "   ", "Local Disk", "Book Cover", "BOOK COVER", "Cover",
        "Contents", "Title Page", "untitled", "Untitled Document",
        "Kear_0553902164_oeb_c01_r1.htm", "document.rtf", "Slide 1",
    ])
    def test_it_is_refused_in_favour_of_the_filename(self, reader, tmp_path,
                                                     junk):
        path = PdfBuilder(title=junk).build(
            tmp_path / "Anthony, Piers - Mute.pdf")
        assert reader.read(path).title == "Anthony, Piers - Mute"

    def test_a_converter_wrapper_is_unwrapped(self, reader, tmp_path):
        # "Microsoft Word - RAINBOWS END by Vernor Vinge" has the title
        # inside it; the wrapper is not part of the book.
        path = PdfBuilder(title="Microsoft Word - RAINBOWS END.doc").build(
            tmp_path / "rainbows.pdf")
        assert reader.read(path).title == "RAINBOWS END"

    def test_a_wrapper_around_nothing_falls_back(self, reader, tmp_path):
        path = PdfBuilder(title="Microsoft Word - Document1.doc").build(
            tmp_path / "Herbert, Frank - Dune.pdf")
        assert reader.read(path).title == "Herbert, Frank - Dune"

    def test_mojibake_is_refused(self, reader, tmp_path):
        # A real one, from a scan of Gibbon: the bytes decode, but not
        # into anything a person would call a title.
        path = PdfBuilder(title="®7ÛTIãoXNáj-L").build(
            tmp_path / "Gibbon - Decline and Fall.pdf")
        assert reader.read(path).title == "Gibbon - Decline and Fall"

    def test_no_metadata_at_all_is_not_a_failure(self, reader, tmp_path):
        path = PdfBuilder(title=None, author=None).build(
            tmp_path / "Cussler, Clive - Pacific Vortex.pdf")
        meta = reader.read(path)
        assert meta.title == "Cussler, Clive - Pacific Vortex"
        assert meta.authors == []

    def test_an_author_that_is_a_program_is_refused(self, reader, tmp_path):
        path = PdfBuilder(author="Adobe Acrobat").build(tmp_path / "a.pdf")
        assert reader.read(path).authors == []


class TestWhenTheFileNamesADifferentBook:
    """The worst failure here is not a missing title but somebody
    else's. These PDFs were assembled from other files and kept the
    wrong name inside: "Jody Lynn Nye - School Of Light" on a file
    called "Nye, Jody Lynn - Dreamland", "Herbert The Lazarus Effect" on
    "Destination: Void". Both are real.

    So an embedded title has to agree with the filename to be believed.
    The filename is the better source anyway; this only lets it be
    tidied, never replaced.
    """

    def test_a_tidier_version_of_the_same_book_is_taken(self, reader, tmp_path):
        path = PdfBuilder(title="Crossroads of Twilight").build(
            tmp_path / "Jordan, Robert - Wheel of Time 10 - Crossroads of"
                       " Twilight.pdf")
        assert reader.read(path).title == "Crossroads of Twilight"

    def test_another_book_entirely_is_refused(self, reader, tmp_path):
        path = PdfBuilder(title="Jody Lynn Nye - School Of Light").build(
            tmp_path / "Nye, Jody Lynn - Dreamland.pdf")
        assert reader.read(path).title == "Nye, Jody Lynn - Dreamland"

    def test_a_section_heading_is_refused(self, reader, tmp_path):
        # "Also by Neil Gaiman" is a page in the book, not the book.
        path = PdfBuilder(title="Also by Neil Gaiman").build(
            tmp_path / "Gaiman, Neil - Stardust.pdf")
        assert reader.read(path).title == "Gaiman, Neil - Stardust"

    def test_an_image_name_is_refused(self, reader, tmp_path):
        path = PdfBuilder(title="cover.jpg").build(
            tmp_path / "Kritzer, Naomi - Eliena.pdf")
        assert reader.read(path).title == "Kritzer, Naomi - Eliena"

    def test_a_stray_newline_does_not_make_it_a_title(self, reader, tmp_path):
        path = PdfBuilder(title="Dallas, Texas").build(
            tmp_path / "Weather and Climate.pdf")
        assert reader.read(path).title == "Weather and Climate"


class TestWhenItIsNotAPdf:
    def test_something_else_entirely(self, reader, tmp_path):
        path = tmp_path / "a.pdf"
        path.write_bytes(b"this is not a pdf")
        with pytest.raises(MetadataError):
            reader.read(path)

    def test_a_file_that_is_not_there(self, reader, tmp_path):
        with pytest.raises(MetadataError):
            reader.read(tmp_path / "nothing.pdf")

    def test_rubbish_before_the_header_is_tolerated(self, reader, tmp_path):
        # Real files do this; the header is not always at byte zero.
        path = PdfBuilder(title="Still A Book",
                          linearised_junk=True).build(tmp_path / "a.pdf")
        assert reader.read(path).title == "Still A Book"


class TestTheSortKey:
    def test_it_follows_the_title(self, reader, tmp_path):
        path = PdfBuilder(title="The Dispossessed").build(tmp_path / "a.pdf")
        assert reader.read(path).title_sort == "dispossessed"
