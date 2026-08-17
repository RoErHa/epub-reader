"""Comic archives.

A CBZ is a zip of images and nothing else: 309 of them here, and only
one in 120 carries a ComicInfo.xml. So the name is the metadata, and
these names are full of the scanner's own notes:

    Zootopia 001 (2025) (5 covers) (digital) (Salem-Empire).cbz

The issue number is worth keeping — it is what orders a run of comics —
and everything in brackets is not.
"""
from __future__ import annotations

import pytest

from epub_cbz import CbzMetadataReader
from epub_metadata import MetadataError
from tests.cbz_fixtures import CbzBuilder


@pytest.fixture
def reader():
    return CbzMetadataReader()


class TestTheNameIsTheMetadata:
    def test_the_title_comes_from_the_filename(self, reader, tmp_path):
        path = CbzBuilder().build(tmp_path / "Those Not Afraid 001 (2025).cbz")
        assert reader.read(path).title == "Those Not Afraid 001"

    def test_the_scanners_notes_are_not_part_of_it(self, reader, tmp_path):
        path = CbzBuilder().build(
            tmp_path / "Zootopia 001 (2025) (5 covers) (digital)"
                       " (Salem-Empire).cbz")
        meta = reader.read(path)
        assert meta.title == "Zootopia 001"
        assert "digital" not in meta.title.lower()

    def test_the_issue_number_orders_the_run(self, reader, tmp_path):
        path = CbzBuilder().build(tmp_path / "Shadowman 004 (2025).cbz")
        meta = reader.read(path)
        assert meta.series == "Shadowman"
        assert meta.series_index == 4

    def test_a_comic_with_no_number_keeps_its_whole_name(self, reader, tmp_path):
        path = CbzBuilder().build(tmp_path / "Venom War - Lethal Protectors.cbz")
        meta = reader.read(path)
        assert meta.title == "Venom War - Lethal Protectors"
        assert meta.series is None

    def test_a_year_in_brackets_is_the_edition_not_the_work(self, reader,
                                                            tmp_path):
        path = CbzBuilder().build(tmp_path / "Zootopia 001 (2025) (digital).cbz")
        assert reader.read(path).edition_date == "2025"

    def test_the_pages_are_counted(self, reader, tmp_path):
        path = CbzBuilder(pages=22).build(tmp_path / "Long One 001.cbz")
        assert reader.read(path).page_count == 22

    def test_files_that_are_not_pages_are_not_counted(self, reader, tmp_path):
        path = CbzBuilder(pages=3, extras=["readme.txt", "Thumbs.db"]).build(
            tmp_path / "Short One 001.cbz")
        assert reader.read(path).page_count == 3


class TestTheCover:
    def test_it_is_the_first_page(self, reader, tmp_path):
        path = CbzBuilder(pages=4).build(tmp_path / "Covered 001.cbz")
        meta = reader.read(path)
        assert meta.cover_data
        assert meta.cover_suffix == ".jpg"

    def test_page_ten_does_not_come_before_page_two(self, reader, tmp_path):
        # Sorted as text, "010" beats "002" only if the padding is
        # respected — and scanners do pad.
        import zipfile
        path = CbzBuilder(pages=12).build(tmp_path / "Twelve 001.cbz")
        with zipfile.ZipFile(path) as archive:
            first = archive.read("001.jpg")
        assert reader.read(path).cover_data == first


class TestComicInfo:
    """One in 120 has one, and when it does it beats the filename."""

    def test_the_series_and_number_are_taken(self, reader, tmp_path):
        path = CbzBuilder(series="Saga", number="12").build(
            tmp_path / "whatever 099.cbz")
        meta = reader.read(path)
        assert meta.series == "Saga"
        assert meta.series_index == 12

    def test_the_writer_is_the_author(self, reader, tmp_path):
        path = CbzBuilder(series="Saga", writer="Brian K. Vaughan").build(
            tmp_path / "saga 001.cbz")
        assert [a.display for a in reader.read(path).authors] == \
            ["Brian K. Vaughan"]

    def test_the_summary_is_the_synopsis(self, reader, tmp_path):
        path = CbzBuilder(series="Saga", summary="A war, and a family.").build(
            tmp_path / "saga 001.cbz")
        assert reader.read(path).description == "A war, and a family."


class TestWhenItIsNotOne:
    def test_something_that_is_not_a_zip(self, reader, tmp_path):
        path = tmp_path / "broken.cbz"
        path.write_bytes(b"not a zip at all")
        with pytest.raises(MetadataError):
            reader.read(path)

    def test_a_rar_wearing_a_cbz_extension_says_so(self, reader, tmp_path):
        # Two real ones. CBR is RAR, which nothing in the stack reads,
        # and a file named .cbz is not necessarily a zip.
        path = tmp_path / "Doctor Strange Sorcerer Supreme #46.cbz"
        path.write_bytes(b"Rar!\x1a\x07\x00" + b"\0" * 64)
        with pytest.raises(MetadataError, match="RAR"):
            reader.read(path)

    def test_a_zip_with_no_pages_in_it(self, reader, tmp_path):
        import zipfile
        path = tmp_path / "empty.cbz"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("readme.txt", b"nothing to see")
        with pytest.raises(MetadataError):
            reader.read(path)

    def test_a_file_that_is_not_there(self, reader, tmp_path):
        with pytest.raises(MetadataError):
            reader.read(tmp_path / "nothing.cbz")
