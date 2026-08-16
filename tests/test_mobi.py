"""Reading metadata out of MOBI, AZW3 and PRC files.

The reader gets these for free — foliate-js sniffs the container and
parses them itself — but the *library* does not. Title, author, cover
and the rest live in a PalmDB record rather than an OPF, so this is the
one piece MOBI support actually costs.

The real library holds 89 of them: 34 .mobi, 52 .prc and 3 .azw3.
"""
from __future__ import annotations

import pytest

from epub_metadata import MetadataError
from epub_mobi import MobiMetadataReader
from tests.mobi_fixtures import MobiBuilder


@pytest.fixture
def reader():
    return MobiMetadataReader()


class TestReadingOne:
    def test_the_title(self, reader, tmp_path):
        path = MobiBuilder(title="Dune").build(tmp_path / "a.mobi")
        assert reader.read(path).title == "Dune"

    def test_the_author(self, reader, tmp_path):
        path = MobiBuilder(authors=["Frank Herbert"]).build(tmp_path / "a.mobi")
        assert [a.display for a in reader.read(path).authors] == ["Frank Herbert"]

    def test_an_author_written_surname_first(self, reader, tmp_path):
        # MOBI files are as full of "Herbert, Frank" as EPUBs are.
        path = MobiBuilder(authors=["Herbert, Frank"]).build(tmp_path / "a.mobi")
        person = reader.read(path).authors[0]
        assert person.display == "Frank Herbert"
        assert person.sort == "herbert, frank"

    def test_several_authors(self, reader, tmp_path):
        path = MobiBuilder(authors=["Larry Niven", "Jerry Pournelle"]).build(
            tmp_path / "a.mobi")
        assert len(reader.read(path).authors) == 2

    def test_the_publisher(self, reader, tmp_path):
        path = MobiBuilder(publisher="Chilton").build(tmp_path / "a.mobi")
        assert reader.read(path).publisher == "Chilton"

    def test_the_description(self, reader, tmp_path):
        path = MobiBuilder(description="A desert planet.").build(tmp_path / "a.mobi")
        assert reader.read(path).description == "A desert planet."

    def test_html_in_a_description_is_not_shown_as_html(self, reader, tmp_path):
        # Publishers put markup in there; the app shows text.
        path = MobiBuilder(
            description="<p>A <i>desert</i> planet.</p>").build(tmp_path / "a.mobi")
        assert reader.read(path).description == "A desert planet."

    def test_the_subjects(self, reader, tmp_path):
        path = MobiBuilder(subjects=["Science Fiction", "Adventure"]).build(
            tmp_path / "a.mobi")
        assert reader.read(path).subjects == ["Science Fiction", "Adventure"]

    def test_the_isbn(self, reader, tmp_path):
        path = MobiBuilder(isbn="978-0-441-01359-3").build(tmp_path / "a.mobi")
        assert reader.read(path).isbn13 == "9780441013593"

    def test_the_year_it_was_published(self, reader, tmp_path):
        path = MobiBuilder(published="1965-10-01T00:00:00+00:00").build(
            tmp_path / "a.mobi")
        meta = reader.read(path)
        assert meta.first_published == 1965
        assert meta.edition_date.startswith("1965")

    def test_the_language(self, reader, tmp_path):
        path = MobiBuilder(language=9).build(tmp_path / "a.mobi")
        assert reader.read(path).language == "en"

    def test_a_header_too_short_to_hold_a_language_is_not_read_anyway(
        self, reader, tmp_path
    ):
        # Older MOBI headers stop before the locale field. Reading past
        # the end of one gives a number that means nothing — several
        # real files produced 209 and 206, which are not languages.
        path = MobiBuilder(title="Old Book", header_length=24).build(
            tmp_path / "a.mobi")
        meta = reader.read(path)
        assert meta.title == "Old Book"
        assert meta.language is None

    def test_a_language_nobody_mapped_is_left_alone(self, reader, tmp_path):
        path = MobiBuilder(language=0).build(tmp_path / "a.mobi")
        assert reader.read(path).language is None

    def test_the_sort_title_is_computed(self, reader, tmp_path):
        path = MobiBuilder(title="The Left Hand of Darkness").build(
            tmp_path / "a.mobi")
        assert reader.read(path).title_sort == "left hand of darkness"

    def test_a_better_title_in_the_exth_wins(self, reader, tmp_path):
        # The PalmDB name is capped at 31 bytes, so a long title is
        # truncated there and written again in the EXTH block.
        path = MobiBuilder(
            title="The Hitchhikers Guide to the Ga",
            updated_title="The Hitchhiker's Guide to the Galaxy",
        ).build(tmp_path / "a.mobi")
        assert reader.read(path).title == "The Hitchhiker's Guide to the Galaxy"


class TestTheCover:
    def test_it_is_extracted(self, reader, tmp_path):
        art = b"\xff\xd8\xff\xe0" + b"jpeg pretending" * 4
        path = MobiBuilder(cover=art).build(tmp_path / "a.mobi")
        meta = reader.read(path)
        assert meta.cover_data == art
        assert meta.cover_suffix == ".jpg"

    def test_a_png_cover_keeps_its_own_kind(self, reader, tmp_path):
        art = b"\x89PNG\r\n\x1a\n" + b"png pretending" * 4
        path = MobiBuilder(cover=art).build(tmp_path / "a.mobi")
        assert reader.read(path).cover_suffix == ".png"

    def test_a_book_with_no_cover_is_not_a_failure(self, reader, tmp_path):
        path = MobiBuilder().build(tmp_path / "a.mobi")
        assert reader.read(path).cover_data is None


class TestWhenItIsNotOne:
    def test_a_file_that_is_not_a_mobi_is_reported(self, reader, tmp_path):
        path = tmp_path / "a.mobi"
        path.write_bytes(b"this is not a palm database")
        with pytest.raises(MetadataError):
            reader.read(path)

    def test_an_epub_pretending_to_be_one_is_reported(self, reader, tmp_path):
        path = tmp_path / "a.mobi"
        path.write_bytes(b"PK\x03\x04" + b"\0" * 200)
        with pytest.raises(MetadataError):
            reader.read(path)

    def test_a_file_that_is_not_there(self, reader, tmp_path):
        with pytest.raises(MetadataError):
            reader.read(tmp_path / "nothing.mobi")

    def test_a_truncated_file_does_not_take_the_scan_down(self, reader, tmp_path):
        whole = MobiBuilder(title="Dune").build(tmp_path / "a.mobi").read_bytes()
        path = tmp_path / "half.mobi"
        path.write_bytes(whole[:40])
        with pytest.raises(MetadataError):
            reader.read(path)


class TestAZW3:
    def test_a_kf8_file_reads_the_same_way(self, reader, tmp_path):
        path = MobiBuilder(title="Modern Book", kf8=True).build(tmp_path / "a.azw3")
        assert reader.read(path).title == "Modern Book"
