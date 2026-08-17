"""Reading metadata out of an EPUB.

Must cope with what the real library actually holds: EPUB 2 and 3,
Calibre series metadata, missing fields, and files that are broken in
several different ways. A corrupt book must never stop an index run.
"""
from __future__ import annotations

import zipfile

import pytest

from epub_metadata import EpubMetadataReader, MetadataError
from tests.epub_fixtures import EpubBuilder


@pytest.fixture
def read():
    return EpubMetadataReader().read


class TestCoreFields:
    def test_title_and_author(self, tmp_path, read):
        path = EpubBuilder(title="Dune", author="Frank Herbert").build(
            tmp_path / "b.epub"
        )
        meta = read(path)
        assert meta.title == "Dune"
        assert [a.display for a in meta.authors] == ["Frank Herbert"]
        assert meta.authors[0].sort == "herbert, frank"

    def test_title_sort_key_is_computed(self, tmp_path, read):
        path = EpubBuilder(title="The Left Hand of Darkness").build(
            tmp_path / "b.epub"
        )
        assert read(path).title_sort == "left hand of darkness"

    def test_language(self, tmp_path, read):
        path = EpubBuilder(language="nl").build(tmp_path / "b.epub")
        assert read(path).language == "nl"

    def test_inverted_author_is_normalised(self, tmp_path, read):
        path = EpubBuilder(author="Ludlum, Robert").build(tmp_path / "b.epub")
        meta = read(path)
        assert meta.authors[0].display == "Robert Ludlum"
        assert meta.authors[0].sort == "ludlum, robert"

    def test_multiple_authors(self, tmp_path, read):
        path = EpubBuilder(author="Neil Gaiman & Terry Pratchett").build(
            tmp_path / "b.epub"
        )
        assert len(read(path).authors) == 2

    def test_subjects_become_genres(self, tmp_path, read):
        path = EpubBuilder(subjects=["Science Fiction", "Adventure"]).build(
            tmp_path / "b.epub"
        )
        assert read(path).subjects == ["Science Fiction", "Adventure"]


class TestSeries:
    def test_calibre_series_metadata_is_read(self, tmp_path, read):
        path = EpubBuilder(series="Hainish Cycle", series_index=4).build(
            tmp_path / "b.epub"
        )
        meta = read(path)
        assert meta.series == "Hainish Cycle"
        assert meta.series_index == pytest.approx(4.0)

    def test_a_book_without_series_says_so(self, tmp_path, read):
        meta = read(EpubBuilder().build(tmp_path / "b.epub"))
        assert meta.series is None
        assert meta.series_index is None

    def test_a_fractional_index_survives(self, tmp_path, read):
        path = EpubBuilder(series="X", series_index=2.5).build(tmp_path / "b.epub")
        assert read(path).series_index == pytest.approx(2.5)


class TestDates:
    def test_a_full_date_yields_a_year(self, tmp_path, read):
        path = EpubBuilder(first_published="1969-03-01").build(tmp_path / "b.epub")
        meta = read(path)
        assert meta.first_published == 1969
        assert meta.edition_date == "1969-03-01"

    def test_a_year_only_date_works(self, tmp_path, read):
        path = EpubBuilder(first_published="1969").build(tmp_path / "b.epub")
        assert read(path).first_published == 1969

    def test_an_unparseable_date_is_dropped_not_fatal(self, tmp_path, read):
        path = EpubBuilder(first_published="sometime in the sixties").build(
            tmp_path / "b.epub"
        )
        assert read(path).first_published is None


class TestCover:
    def test_the_cover_image_is_extracted(self, tmp_path, read):
        path = EpubBuilder(cover=True).build(tmp_path / "b.epub")
        meta = read(path)
        assert meta.cover_data is not None
        assert meta.cover_data[:4] == b"\x89PNG"

    def test_a_book_without_a_cover_has_none(self, tmp_path, read):
        assert read(EpubBuilder().build(tmp_path / "b.epub")).cover_data is None


class TestBrokenBooks:
    """The real library holds all of these. None may raise anything but
    MetadataError, and none may take down an index run."""

    def test_a_file_that_is_not_a_zip(self, tmp_path, read):
        path = tmp_path / "broken.epub"
        path.write_bytes(b"this is not a zip file at all")
        with pytest.raises(MetadataError, match="not a valid"):
            read(path)

    def test_a_zip_with_no_container(self, tmp_path, read):
        path = tmp_path / "b.epub"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("hello.txt", "nothing useful here")
        with pytest.raises(MetadataError, match="container"):
            read(path)

    def test_a_container_pointing_at_a_missing_opf(self, tmp_path, read):
        path = tmp_path / "b.epub"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(
                "META-INF/container.xml",
                '<container><rootfiles><rootfile full-path="gone.opf"/>'
                "</rootfiles></container>",
            )
        with pytest.raises(MetadataError):
            read(path)

    def test_a_missing_file(self, tmp_path, read):
        with pytest.raises(MetadataError):
            read(tmp_path / "absent.epub")

    def test_a_book_with_no_title_falls_back_to_the_filename(self, tmp_path, read):
        path = tmp_path / "Some Book Name.epub"
        EpubBuilder(title="").build(path)
        assert read(path).title == "Some Book Name"

    def test_an_empty_spine_is_reported(self, tmp_path, read):
        path = EpubBuilder(chapters=0).build(tmp_path / "b.epub")
        assert read(path).spine_length == 0


class TestAnUnpackedBook:
    """265 books in the real library are directories rather than zips —
    iTunes exports, and anything somebody unzipped and never zipped
    again. The name still ends in .epub; only the container differs, so
    the same parser should read both."""

    def test_it_reads_like_any_other(self, tmp_path):
        from tests.epub_fixtures import EpubBuilder
        EpubBuilder(title="Drive", author="James Sallis",
                    subjects=["Crime"]).build_unpacked(tmp_path / "Drive.epub")
        meta = EpubMetadataReader().read(tmp_path / "Drive.epub")
        assert meta.title == "Drive"
        assert [a.display for a in meta.authors] == ["James Sallis"]
        assert meta.subjects == ["Crime"]

    def test_it_agrees_with_the_zipped_version(self, tmp_path):
        from tests.epub_fixtures import EpubBuilder
        builder = EpubBuilder(title="The Dispossessed", author="Le Guin, Ursula",
                              series="Hainish", series_index=6, cover=True)
        builder.build(tmp_path / "packed.epub")
        builder.build_unpacked(tmp_path / "Unpacked.epub")

        reader = EpubMetadataReader()
        packed = reader.read(tmp_path / "packed.epub")
        loose = reader.read(tmp_path / "Unpacked.epub")
        assert packed.title == loose.title
        assert packed.title_sort == loose.title_sort
        assert [a.sort for a in packed.authors] == [a.sort for a in loose.authors]
        assert packed.series == loose.series
        assert packed.cover_data == loose.cover_data

    def test_its_cover_is_found(self, tmp_path):
        from tests.epub_fixtures import EpubBuilder
        EpubBuilder(title="Covered", cover=True).build_unpacked(
            tmp_path / "Covered.epub")
        meta = EpubMetadataReader().read(tmp_path / "Covered.epub")
        assert meta.cover_data
        assert meta.cover_suffix == ".png"

    def test_a_directory_with_no_container_is_reported(self, tmp_path):
        empty = tmp_path / "Nothing.epub"
        (empty / "OEBPS").mkdir(parents=True)
        with pytest.raises(MetadataError):
            EpubMetadataReader().read(empty)

    def test_an_opf_the_container_promises_but_lacks(self, tmp_path):
        broken = tmp_path / "Broken.epub"
        (broken / "META-INF").mkdir(parents=True)
        (broken / "META-INF" / "container.xml").write_text(
            '<container><rootfiles><rootfile full-path="OEBPS/gone.opf"/>'
            '</rootfiles></container>')
        with pytest.raises(MetadataError):
            EpubMetadataReader().read(broken)


class TestBooksNobodyCanOpen:
    """50 books in the real library are Apple Books purchases: every
    document inside is encrypted with FairPlay, and the key lives in an
    Apple ID rather than in the file. No reader here or anywhere else
    can open them, so listing them promises something we cannot deliver.

    But `META-INF/encryption.xml` is *also* how fonts are obfuscated,
    which is ordinary and harmless — 8 books here do it, including the
    Earthsea novels. Excluding those would be losing books that work.
    """

    def test_an_encrypted_book_is_refused(self, tmp_path):
        from epub_metadata import DrmError
        from tests.epub_fixtures import EpubBuilder
        EpubBuilder(title="Secret Europe",
                    encrypted=["OEBPS/chapter1.xhtml", "nav.xhtml"]).build(
            tmp_path / "drm.epub")
        with pytest.raises(DrmError):
            EpubMetadataReader().read(tmp_path / "drm.epub")

    def test_obfuscated_fonts_are_not_drm(self, tmp_path):
        from tests.epub_fixtures import EpubBuilder
        EpubBuilder(title="The Tombs of Atuan", author="Ursula K. Le Guin",
                    encrypted=["OEBPS/fonts/Charter.otf",
                               "OEBPS/fonts/Charter-Bold.ttf"]).build(
            tmp_path / "fonts.epub")
        meta = EpubMetadataReader().read(tmp_path / "fonts.epub")
        assert meta.title == "The Tombs of Atuan"

    def test_a_book_with_no_encryption_at_all_is_fine(self, tmp_path):
        from tests.epub_fixtures import EpubBuilder
        EpubBuilder(title="Plain").build(tmp_path / "plain.epub")
        assert EpubMetadataReader().read(tmp_path / "plain.epub").title == "Plain"

    def test_the_message_says_where_to_read_it(self, tmp_path):
        from epub_metadata import DrmError
        from tests.epub_fixtures import EpubBuilder
        EpubBuilder(encrypted=["OEBPS/chapter1.xhtml"]).build(
            tmp_path / "drm.epub")
        with pytest.raises(DrmError, match="Apple"):
            EpubMetadataReader().read(tmp_path / "drm.epub")

    def test_it_holds_for_an_unpacked_book_too(self, tmp_path):
        from epub_metadata import DrmError
        from tests.epub_fixtures import EpubBuilder
        EpubBuilder(encrypted=["OEBPS/chapter1.xhtml"]).build_unpacked(
            tmp_path / "Secret Europe.epub")
        with pytest.raises(DrmError):
            EpubMetadataReader().read(tmp_path / "Secret Europe.epub")


class TestALanguageThatIsNotOne:
    """Some files declare "UND" — undetermined — which is the OPF's way
    of shrugging. Showing it as a language is worse than showing none."""

    @pytest.mark.parametrize("declared", ["UND", "und", "xx", "unknown", "  "])
    def test_it_is_treated_as_nothing(self, tmp_path, declared):
        from tests.epub_fixtures import EpubBuilder
        EpubBuilder(title="Shrug", language=declared).build(tmp_path / "a.epub")
        assert EpubMetadataReader().read(tmp_path / "a.epub").language is None

    def test_a_real_language_still_comes_through(self, tmp_path):
        from tests.epub_fixtures import EpubBuilder
        EpubBuilder(title="Dutch", language="nl").build(tmp_path / "a.epub")
        assert EpubMetadataReader().read(tmp_path / "a.epub").language == "nl"
