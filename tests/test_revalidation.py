"""A book the reader already has costs a header, not a book.

Every book response carried a validator and **nothing ever acted on
one**. Starlette computes an ETag in `FileResponse` and answers 304 only
from `StaticFiles`, which this app does not use — so the header was
advice nobody took, and re-opening a book downloaded it again. On an
iPad over a tailnet the radio is the expensive part, and a 315 MB comic
is the whole of it.

Two of these responses are not files at all: a comic packed with RAR and
an EPUB that was never zipped are **built** on the way out (§8.5,
§8.15). That is what shapes the rule below — the validator names the
source, because a validator that costs a repack is not a saving.
"""
from __future__ import annotations

import io
import zipfile
from types import SimpleNamespace

import pytest

from epub_caching import Revalidation
from tests.epub_fixtures import EpubBuilder

HASH = "a" * 64
OTHER = "b" * 64


def asked(offered: str | None = None) -> Revalidation:
    """A reader asking, with or without a book already in hand."""
    headers = {"if-none-match": offered} if offered else {}
    return Revalidation(SimpleNamespace(headers=headers))


class TestTheValidator:
    def test_it_names_the_recipe_and_the_source(self):
        tag = Revalidation.weak("epub1", HASH)
        assert "epub1" in tag and HASH[:16] in tag

    def test_a_body_we_build_is_weak(self):
        """The same book packed twice is not the same bytes — a zip
        carries the mtimes of what went into it — while being the same
        book. `W/` is the honest way to say so."""
        assert Revalidation.weak("epub1", HASH).startswith('W/"')

    def test_bytes_that_are_on_disk_are_strong(self):
        """Nothing is built, so the bytes really are stable — and a
        strong tag is the only kind `If-Range` may use."""
        assert Revalidation.strong("file1", HASH).startswith('"')

    def test_the_two_are_the_same_tag_wearing_a_prefix(self):
        assert Revalidation.weak("file1", HASH) == "W/" + Revalidation.strong(
            "file1", HASH)

    def test_two_books_never_share_one(self):
        assert Revalidation.weak("epub1", HASH) != Revalidation.weak("epub1", OTHER)

    def test_the_recipe_is_part_of_it(self):
        """What we hand over is the source *and* the way we packed it.
        Change the packing and the copy in somebody's browser is stale,
        so the recipe carries a version of its own."""
        assert Revalidation.weak("epub1", HASH) != Revalidation.weak("cbz1", HASH)

    def test_the_same_book_asked_twice_is_the_same_validator(self):
        assert Revalidation.weak("epub1", HASH) == Revalidation.weak("epub1", HASH)


class TestWhatTheReaderOffers:
    def test_a_reader_with_nothing_holds_nothing(self):
        assert not asked().holds(Revalidation.weak("epub1", HASH))

    def test_the_one_we_sent_matches(self):
        tag = Revalidation.weak("epub1", HASH)
        assert asked(tag).holds(tag)

    def test_another_book_does_not(self):
        assert not asked(Revalidation.weak("epub1", OTHER)).holds(
            Revalidation.weak("epub1", HASH))

    def test_one_of_several_matches(self):
        """A browser may offer everything it has for a URL."""
        tag = Revalidation.weak("epub1", HASH)
        offered = f'{Revalidation.weak("epub1", OTHER)}, {tag}'
        assert asked(offered).holds(tag)

    def test_a_star_matches_anything(self):
        assert asked("*").holds(Revalidation.weak("epub1", HASH))

    def test_the_comparison_is_weak(self):
        """A cache may hand back what we sent with the `W/` off, and
        304 is decided by weak comparison — the tags are equal if their
        opaque parts are."""
        tag = Revalidation.weak("epub1", HASH)
        assert asked(tag.removeprefix("W/")).holds(tag)


class TestTheAnswer:
    def build(self):
        from fastapi import Response
        return Response(content=b"a whole book", media_type="application/epub+zip")

    def test_a_reader_without_it_gets_the_book(self):
        response = asked().answer(Revalidation.weak("epub1", HASH), self.build)
        assert response.status_code == 200
        assert response.body == b"a whole book"

    def test_the_book_carries_its_validator(self):
        tag = Revalidation.weak("epub1", HASH)
        response = asked().answer(tag, self.build)
        assert response.headers["etag"] == tag

    def test_and_says_to_ask_again(self):
        """Without this the browser has no freshness information at all
        and falls back to a heuristic, which is how a validator ends up
        never being offered back."""
        response = asked().answer(Revalidation.weak("epub1", HASH), self.build)
        assert response.headers["cache-control"] == Revalidation.CONTROL

    def test_a_reader_who_has_it_is_told_so(self):
        tag = Revalidation.weak("epub1", HASH)
        assert asked(tag).answer(tag, self.build).status_code == 304

    def test_and_gets_no_body(self):
        tag = Revalidation.weak("epub1", HASH)
        assert asked(tag).answer(tag, self.build).body == b""

    def test_the_304_carries_the_validator_back(self):
        tag = Revalidation.weak("epub1", HASH)
        assert asked(tag).answer(tag, self.build).headers["etag"] == tag

    def test_the_book_is_never_built(self):
        """The point of all of it. Building the answer is the cost being
        avoided, so a 304 that packs the book first has saved nothing but
        the transfer — and on the server it has saved nothing at all."""
        tag = Revalidation.weak("cbz1", HASH)

        def explode():
            raise AssertionError("the book was packed to answer a 304")

        assert asked(tag).answer(tag, explode).status_code == 304


@pytest.fixture
def one_book(client, library, gateway):
    for existing in library.glob("*.epub"):
        existing.unlink()
    EpubBuilder(title="Only Book", author="A Reader").build(library / "only.epub")
    gateway.reindex()
    return client


def only(client, title="Only Book"):
    return next(b for b in client.get("/api/books").json()["books"]
                if b["title"] == title)


class TestOverHTTP:
    """The route, end to end: a file, and the two books that are built."""

    def test_a_book_comes_with_a_validator(self, one_book):
        res = one_book.get(f"/api/books/{only(one_book)['id']}/file")
        assert res.status_code == 200
        # A file, sent as it sits on disk: strong, and ours rather than
        # the mtime-and-size one Starlette would have set.
        assert res.headers["etag"].startswith('"file1-')

    def test_and_says_to_ask_again(self, one_book):
        res = one_book.get(f"/api/books/{only(one_book)['id']}/file")
        assert res.headers["cache-control"] == Revalidation.CONTROL

    def test_offering_it_back_costs_a_header(self, one_book):
        url = f"/api/books/{only(one_book)['id']}/file"
        tag = one_book.get(url).headers["etag"]
        again = one_book.get(url, headers={"If-None-Match": tag})
        assert again.status_code == 304
        assert again.content == b""

    def test_a_stale_validator_gets_the_book(self, one_book):
        url = f"/api/books/{only(one_book)['id']}/file"
        again = one_book.get(url, headers={"If-None-Match": 'W/"epub1-nonsense"'})
        assert again.status_code == 200
        assert again.content[:2] == b"PK"

    def test_a_book_that_changed_gets_a_new_validator(self, one_book, library,
                                                      gateway):
        """Identity is the content hash (§5), so replacing the file with
        a better scan invalidates what everybody is holding — which is
        the case an mtime-based validator gets wrong when a copy keeps
        its timestamps."""
        url = f"/api/books/{only(one_book)['id']}/file"
        before = one_book.get(url).headers["etag"]
        EpubBuilder(title="Only Book", author="A Reader",
                    chapters=4).build(library / "only.epub")
        gateway.reindex()
        after = one_book.get(f"/api/books/{only(one_book)['id']}/file")
        assert after.headers["etag"] != before

    def test_two_books_do_not_share_one(self, one_book, library, gateway):
        EpubBuilder(title="Second Book", author="A Reader").build(
            library / "second.epub")
        gateway.reindex()
        first = one_book.get(f"/api/books/{only(one_book)['id']}/file")
        second = one_book.get(
            f"/api/books/{only(one_book, 'Second Book')['id']}/file")
        assert first.headers["etag"] != second.headers["etag"]


class TestABookThatWasNeverZipped:
    """265 books in the real library are directories, and each one is
    packed on the way out. Packing it to discover it need not be sent is
    the one thing this must not do."""

    @pytest.fixture
    def unpacked(self, client, library, gateway):
        EpubBuilder(title="Drive", author="James Sallis").build_unpacked(
            library / "Drive.epub")
        gateway.reindex()
        return client

    def url(self, client):
        return f"/api/books/{only(client, 'Drive')['id']}/file"

    def test_it_comes_with_a_validator(self, unpacked):
        res = unpacked.get(self.url(unpacked))
        assert res.status_code == 200
        assert res.headers["etag"].startswith('W/"')
        with zipfile.ZipFile(io.BytesIO(res.content)) as archive:
            assert "META-INF/container.xml" in archive.namelist()

    def test_offering_it_back_is_a_304(self, unpacked):
        url = self.url(unpacked)
        tag = unpacked.get(url).headers["etag"]
        assert unpacked.get(url, headers={"If-None-Match": tag}).status_code == 304

    def test_and_the_book_is_never_packed(self, unpacked, monkeypatch):
        from epub_routes import Routes
        url = self.url(unpacked)
        tag = unpacked.get(url).headers["etag"]

        def explode(self, root):
            raise AssertionError("a 304 packed the book anyway")

        monkeypatch.setattr(Routes, "zip_unpacked", explode)
        assert unpacked.get(url, headers={"If-None-Match": tag}).status_code == 304


class TestAComicPackedWithRar:
    """The same, for the response that costs the most to build: the
    largest comic here is 315 MB, and libarchive reads it end to end.

    bsdtar is faked exactly as §8.15 fakes it — the process is the
    boundary, because nothing on this machine can *write* a RAR for a
    fixture to use.
    """

    @pytest.fixture
    def comic(self, client, library, gateway, monkeypatch):
        from tests.cbz_fixtures import CbzBuilder
        CbzBuilder(pages=2).build(library / "Shadowman 004 (2019).cbz")
        gateway.reindex()
        monkeypatch.setattr("epub_routes.RarArchive.is_rar",
                            staticmethod(lambda path: True))
        return client

    def packed(self, monkeypatch) -> list:
        """Counts what libarchive was asked to do."""
        calls = []

        def fake_repack(archive):
            calls.append(archive)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as zf:
                zf.writestr("001.jpg", b"\xff\xd8page")
            return buffer.getvalue()

        monkeypatch.setattr("epub_routes.repack", fake_repack)
        return calls

    def url(self, client):
        book = next(b for b in client.get("/api/books").json()["books"]
                    if b["name"].endswith(".cbz"))
        return f"/api/books/{book['id']}/file"

    def test_a_repacked_comic_carries_a_validator(self, comic, monkeypatch):
        self.packed(monkeypatch)
        res = comic.get(self.url(comic))
        assert res.status_code == 200
        assert res.headers["etag"].startswith('W/"')

    def test_it_is_not_the_validator_the_same_file_would_get_unpacked(
        self, comic, monkeypatch
    ):
        """What is handed over is a zip we made, not the archive on
        disk, so the recipe is part of what the reader is holding."""
        self.packed(monkeypatch)
        repacked = comic.get(self.url(comic)).headers["etag"]
        monkeypatch.setattr("epub_routes.RarArchive.is_rar",
                            staticmethod(lambda path: False))
        plain = comic.get(self.url(comic)).headers["etag"]
        assert repacked != plain

    def test_a_second_open_unpacks_nothing(self, comic, monkeypatch):
        calls = self.packed(monkeypatch)
        url = self.url(comic)
        tag = comic.get(url).headers["etag"]
        assert len(calls) == 1
        assert comic.get(url, headers={"If-None-Match": tag}).status_code == 304
        assert len(calls) == 1, "the comic was unpacked to answer a 304"


class TestCovers:
    """The browse grid asks for a couple of thousand thumbnails, and
    every one of them was sent in full on every visit.

    A cover is **not** immutable, however much its URL looks it.
    `CoverStore` keys the file by the *book's* content hash, not the
    picture's, so `covers --apply` writing a better cover, or a reader
    saying one is wrong (§8.13) and a later pass fetching another, both
    land on the same URL with different bytes. `max-age` would pin a
    wrong cover in somebody's browser for as long as it ran, and — being
    on the iPad rather than here — no restart could take it back.
    Revalidating costs a request and keeps the picture correctable.
    """

    @pytest.fixture
    def illustrated(self, client, library, gateway):
        EpubBuilder(title="With A Cover", author="A Reader",
                    cover=True).build(library / "with-a-cover.epub")
        gateway.reindex()
        return client

    def cover_url(self, illustrated):
        return next(b["cover"] for b in illustrated.get("/api/books").json()["books"]
                    if b.get("cover"))

    def test_a_cover_comes_with_a_validator(self, illustrated):
        res = illustrated.get(self.cover_url(illustrated))
        assert res.status_code == 200
        assert res.headers["etag"].startswith('"cover1-')

    def test_asking_again_costs_a_header(self, illustrated):
        url = self.cover_url(illustrated)
        tag = illustrated.get(url).headers["etag"]
        again = illustrated.get(url, headers={"If-None-Match": tag})
        assert again.status_code == 304
        assert again.content == b""

    def test_it_is_never_cached_without_asking(self, illustrated):
        """The one thing a revert cannot undo is a cache that was told
        not to ask. So it is always told to ask."""
        control = illustrated.get(self.cover_url(illustrated)).headers["cache-control"]
        assert control == Revalidation.CONTROL
        assert "max-age" not in control and "immutable" not in control

    def test_a_replaced_cover_is_a_new_validator(self, illustrated, gateway):
        """The same URL, different bytes — the case `max-age` would get
        wrong. The validator follows the file, not the name."""
        url = self.cover_url(illustrated)
        before = illustrated.get(url).headers["etag"]
        path = (gateway._config.cover_cache / url.split("/api/covers/")[1])
        path.write_bytes(path.read_bytes() + b"\x00repainted")
        assert illustrated.get(url).headers["etag"] != before

    def test_a_cover_that_is_not_there_is_still_a_404(self, illustrated):
        assert illustrated.get("/api/covers/ab/abcdef.jpg").status_code == 404
