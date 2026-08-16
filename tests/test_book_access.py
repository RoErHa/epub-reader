"""Books are addressed by their index id, not by filename.

44% of the real library lives in subfolders, so a book's relative path
contains slashes — which a path segment cannot carry, and which made
those books unopenable. The id has none of those problems: no encoding,
no traversal surface, no ambiguity with the /position suffix.
"""
from __future__ import annotations

import pytest

from tests.epub_fixtures import EpubBuilder

CFI = "epubcfi(/6/4[chap01]!/4/2/2[p1]:0)"
LORD = "Lord Foul's Bane"


@pytest.fixture
def nested(client, library, gateway):
    for existing in library.glob("*.epub"):
        existing.unlink()
    EpubBuilder(title="Top Level").build(library / "top.epub")
    deep = library / "Chronicles of Thomas Covenant 1 - 11" / "01 Lord Foul's Bane"
    deep.mkdir(parents=True)
    EpubBuilder(title="Lord Foul's Bane", author="Stephen R. Donaldson").build(
        deep / "01 Lord Foul's Bane - Stephen R. Donaldson.epub"
    )
    gateway.reindex()
    return client


def book_id(client, title):
    return next(b["id"] for b in client.get("/api/books").json()["books"]
                if b["title"] == title)


class TestFetchingByID:
    def test_a_top_level_book_is_served(self, nested):
        r = nested.get(f"/api/books/{book_id(nested, 'Top Level')}/file")
        assert r.status_code == 200
        assert r.content[:2] == b"PK"

    def test_a_book_in_a_subfolder_is_served(self, nested):
        # This is the bug: its path contains slashes and an apostrophe.
        r = nested.get(f"/api/books/{book_id(nested, LORD)}/file")
        assert r.status_code == 200
        assert r.content[:2] == b"PK"

    def test_the_content_type_is_epub(self, nested):
        r = nested.get(f"/api/books/{book_id(nested, 'Top Level')}/file")
        assert r.headers["content-type"] == "application/epub+zip"

    def test_an_unknown_id_is_a_404(self, nested):
        assert nested.get("/api/books/999999/file").status_code == 404

    def test_a_non_numeric_id_is_refused(self, nested):
        assert nested.get("/api/books/../../.env/file").status_code in (404, 422)

    def test_the_library_stays_read_only(self, nested):
        book = book_id(nested, "Top Level")
        assert nested.delete(f"/api/books/{book}/file").status_code == 405


class TestPositionByID:
    def test_a_subfolder_book_can_hold_a_position(self, nested):
        book = book_id(nested, LORD)
        assert nested.put(
            f"/api/books/{book}/position", json={"cfi": CFI, "fraction": 0.3}
        ).status_code == 200
        got = nested.get(f"/api/books/{book}/position").json()["position"]
        assert got["percent"] == 30

    def test_position_still_follows_the_content_not_the_path(
        self, nested, library, gateway
    ):
        book = book_id(nested, "Top Level")
        nested.put(f"/api/books/{book}/position", json={"cfi": CFI, "fraction": 0.4})
        (library / "top.epub").rename(library / "renamed.epub")
        gateway.reindex()
        moved = book_id(nested, "Top Level")
        assert nested.get(
            f"/api/books/{moved}/position"
        ).json()["position"]["percent"] == 40

    def test_an_unknown_id_has_no_position(self, nested):
        assert nested.get("/api/books/999999/position").status_code == 404


class TestListingCarriesWhatTheReaderNeeds:
    def test_each_book_exposes_an_id_and_a_file_url(self, nested):
        book = nested.get("/api/books").json()["books"][0]
        assert isinstance(book["id"], int)
        assert book["file"] == f"/api/books/{book['id']}/file"

    def test_progress_comes_from_the_stored_hash_not_a_rehash(
        self, nested, monkeypatch
    ):
        # The listing must not re-read every file from disk to work out
        # progress — the index already stored each content hash. Make
        # hashing explode, and the listing must still work.
        from epub_server import BookLibrary

        def explode(self, path):
            raise AssertionError("the listing re-hashed a file")

        monkeypatch.setattr(BookLibrary, "content_hash", explode)
        books = nested.get("/api/books").json()["books"]
        assert books and all("percent" in b for b in books)


class TestOtherFormatsAreServed:
    """A format the library lists is a format it must hand over."""

    def test_a_mobi_can_be_fetched(self, client, library, gateway):
        from tests.mobi_fixtures import MobiBuilder
        MobiBuilder(title="Fetch Me", authors=["A Reader"]).build(
            library / "fetch-me.mobi")
        gateway.reindex()
        book = next(b for b in client.get("/api/books").json()["books"]
                    if b["title"] == "Fetch Me")
        res = client.get(f"/api/books/{book['id']}/file")
        assert res.status_code == 200
        assert res.content[60:68] == b"BOOKMOBI"

    def test_a_pdf_can_be_fetched_too(self, client, library, gateway):
        from tests.pdf_fixtures import PdfBuilder
        PdfBuilder(title="Fetch This PDF").build(library / "fetch-me.pdf")
        gateway.reindex()
        book = next(b for b in client.get("/api/books").json()["books"]
                    if b["title"] == "Fetch This PDF")
        res = client.get(f"/api/books/{book['id']}/file")
        assert res.status_code == 200
        assert res.content.startswith(b"%PDF-")

    def test_a_format_nobody_can_read_is_still_refused(self, client, library,
                                                       gateway):
        # CBR is RAR, which nothing in the stack reads.
        (library / "comic.cbr").write_bytes(b"Rar!\x1a\x07\x00")
        gateway.reindex()
        names = [b["name"] for b in client.get("/api/books").json()["books"]]
        assert "comic.cbr" not in names
