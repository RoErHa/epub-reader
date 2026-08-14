"""The fixture builder is test infrastructure, so it gets tests of its own —
a broken builder would produce false confidence everywhere else."""
from __future__ import annotations

import zipfile

from tests.epub_fixtures import EpubBuilder


class TestEpubStructure:
    def test_mimetype_is_first_entry_and_stored(self, tmp_path):
        path = EpubBuilder().build(tmp_path / "b.epub")
        with zipfile.ZipFile(path) as zf:
            first = zf.infolist()[0]
            assert first.filename == "mimetype"
            assert first.compress_type == zipfile.ZIP_STORED
            assert zf.read("mimetype") == b"application/epub+zip"

    def test_container_points_at_the_package_document(self, tmp_path):
        path = EpubBuilder().build(tmp_path / "b.epub")
        with zipfile.ZipFile(path) as zf:
            container = zf.read("META-INF/container.xml").decode()
        assert "OEBPS/content.opf" in container

    def test_package_carries_title_and_author(self, tmp_path):
        path = EpubBuilder(title="Dune", author="Frank Herbert").build(
            tmp_path / "b.epub"
        )
        with zipfile.ZipFile(path) as zf:
            opf = zf.read("OEBPS/content.opf").decode()
        assert "<dc:title>Dune</dc:title>" in opf
        assert "<dc:creator>Frank Herbert</dc:creator>" in opf

    def test_series_metadata_is_emitted_when_given(self, tmp_path):
        path = EpubBuilder(series="Hainish Cycle", series_index=4).build(
            tmp_path / "b.epub"
        )
        with zipfile.ZipFile(path) as zf:
            opf = zf.read("OEBPS/content.opf").decode()
        assert 'name="calibre:series" content="Hainish Cycle"' in opf
        assert 'content="4"' in opf


class TestScriptedVariant:
    def test_plain_book_contains_no_script(self, tmp_path):
        path = EpubBuilder().build(tmp_path / "b.epub")
        with zipfile.ZipFile(path) as zf:
            chapter = zf.read("OEBPS/chapter1.xhtml").decode()
            assert "pwn.js" not in zf.namelist()
        assert "<script" not in chapter

    def test_scripted_book_embeds_inline_and_external_script(self, tmp_path):
        path = EpubBuilder(scripted=True).build(tmp_path / "b.epub")
        with zipfile.ZipFile(path) as zf:
            chapter = zf.read("OEBPS/chapter1.xhtml").decode()
            assert "OEBPS/pwn.js" in zf.namelist()
        assert EpubBuilder.SENTINEL in chapter
        assert '<script src="pwn.js">' in chapter
