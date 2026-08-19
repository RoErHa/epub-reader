"""Comics that were packed with RAR.

295 of them, and every one invisible: 103 of the Doctor Strange run, 63
Sandman, all 27 Science Comics, the Matt Groening. §2.1 excluded them
because "nothing in the stack reads RAR", and that stopped being true —
**macOS ships `/usr/bin/bsdtar`, which is libarchive, and libarchive
reads RAR**.

> **The extractor is injected, and no test spawns one.** libarchive can
> *read* RAR and cannot write it, and nothing on this machine can either,
> so there is no way to build a fixture archive the way `EpubBuilder` and
> `CbzBuilder` build theirs. A test that needed a real RAR would be a
> test that could only run on a machine that happened to have one. This
> is §10.11's rule about the fetcher, for the same reason: what is tested
> here is our reading of the answers, not somebody else's tool.

The real archives are measured against the real library instead, and the
number is written down (§8.15).
"""
from __future__ import annotations

import zipfile

import pytest

from epub_metadata import MetadataError
from epub_rar import RarArchive, RarError


class FakeExtractor:
    """Stands in for the bsdtar *process*, writing what it writes.

    The process is the boundary worth faking: what needs testing is our
    reading of `-tv` and our splitting of `-xO`, which is where a bug
    would live. Entries keep insertion order, because archive order is
    what makes the split exact.
    """

    def __init__(self, entries: dict[str, bytes] | None = None,
                 available: bool = True, directories: tuple[str, ...] = ()):
        self.entries = entries if entries is not None else {
            "cover.jpg": b"\xff\xd8cover", "p01.jpg": b"\xff\xd8one",
            "p02.jpg": b"\xff\xd8two", "notes.txt": b"scanner was here",
        }
        self.available = available
        self.directories = directories
        self.calls: list[list[str]] = []

    def __call__(self, argv):
        from epub_rar import RarError
        self.calls.append(argv)
        if not self.available:
            raise RarError("/usr/bin/bsdtar is not on this machine")
        if argv[0] == "-tvf":
            lines = [f"drwxr-xr-x  0 0      0           0 29 mrt.  2003 {d}"
                     for d in self.directories]
            lines += [
                f"-rw-r--r--  0 0      0 {len(data):>11} 29 mrt.  2003 {name}"
                for name, data in self.entries.items()]
            return ("\n".join(lines) + "\n").encode()
        if "--" in argv:                       # one entry by name
            return self.entries[argv[argv.index("--") + 1]]
        return b"".join(self.entries.values())  # everything, end to end


@pytest.fixture
def archive(tmp_path):
    path = tmp_path / "Shadowman 004 (2019) (digital).cbr"
    path.write_bytes(b"Rar!\x1a\x07\x01\x00" + b"pretend")
    return path


class TestReadingOne:
    def test_it_lists_what_is_inside(self, archive):
        rar = RarArchive(archive, run=FakeExtractor())
        assert rar.names() == ["cover.jpg", "notes.txt", "p01.jpg", "p02.jpg"]

    def test_it_reads_one_entry(self, archive):
        rar = RarArchive(archive, run=FakeExtractor())
        assert rar.read("p01.jpg") == b"\xff\xd8one"

    def test_listing_costs_one_call_however_often_it_is_asked(self, archive):
        """`-tv` decompresses nothing, and asking twice should not run
        bsdtar twice."""
        extractor = FakeExtractor()
        rar = RarArchive(archive, run=extractor)
        rar.names()
        rar.pages()
        rar.listing()
        assert [c[0] for c in extractor.calls] == ["-tvf"]

    def test_reading_a_cover_does_not_unpack_the_archive(self, archive):
        """The largest comic here is 315 MB. Holding all of it to
        produce a thumbnail is not a trade worth making."""
        extractor = FakeExtractor()
        rar = RarArchive(archive, run=extractor)
        rar.read("p01.jpg")
        asked = [c for c in extractor.calls if c[0] == "-xOf"]
        assert asked and "--" in asked[0], "it must ask for one entry by name"

    def test_reading_everything_is_one_pass(self, archive):
        """`-xO` writes the entries end to end and `-tv` has given their
        sizes in the same order, so the stream splits exactly."""
        extractor = FakeExtractor()
        rar = RarArchive(archive, run=extractor)
        assert rar.read_all()["p02.jpg"] == b"\xff\xd8two"
        assert len([c for c in extractor.calls if c[0] == "-xOf"]) == 1

    def test_a_directory_entry_is_not_a_page(self, archive):
        """Real archives carry one: `Marvel Premiere 6/` holds the pages
        and is not one. It has no bytes, so counting it would slide every
        page along by nothing and every name along by one."""
        rar = RarArchive(archive, run=FakeExtractor(directories=("Some Dir/",)))
        assert "Some Dir/" not in rar.names()
        assert rar.read_all()["p01.jpg"] == b"\xff\xd8one"

    def test_a_stream_that_does_not_add_up_is_refused(self, archive):
        """If the bytes and the sizes disagree, where the pages divide is
        a guess — and a guess would hand the reader half of one page
        stuck to half of the next."""
        from epub_rar import RarError
        extractor = FakeExtractor()
        rar = RarArchive(archive, run=extractor)
        rar.listing()
        extractor.entries = {"p01.jpg": b"short"}
        with pytest.raises(RarError) as exc:
            rar.read_all()
        assert "divide" in str(exc.value)

    def test_an_entry_that_is_not_there(self, archive):
        rar = RarArchive(archive, run=FakeExtractor())
        with pytest.raises(KeyError):
            rar.read("p99.jpg")

    def test_no_extractor_says_so_plainly(self, archive):
        """A missing tool is a fact about the machine, not a broken
        book, and the message has to say which."""
        rar = RarArchive(archive, run=FakeExtractor(available=False))
        with pytest.raises(RarError) as exc:
            rar.names()
        assert "bsdtar" in str(exc.value)

    def test_it_recognises_a_rar_by_its_magic(self, tmp_path, archive):
        assert RarArchive.is_rar(archive)
        plain = tmp_path / "ordinary.cbz"
        plain.write_bytes(b"PK\x03\x04nope")
        assert not RarArchive.is_rar(plain)
        assert not RarArchive.is_rar(tmp_path / "absent.cbr")


class TestReadingItAsAComic:
    def test_the_metadata_comes_out_as_it_does_for_a_zip(self, archive):
        from epub_cbz import CbzMetadataReader
        meta = CbzMetadataReader(run=FakeExtractor()).read(archive)
        # The issue number stays in the title, exactly as it does for
        # a CBZ — "Zootopia 001" is what that test asserts too.
        assert meta.title == "Shadowman 004"
        assert meta.series == "Shadowman"
        assert meta.series_index == 4
        assert meta.page_count == 3           # the .txt is not a page
        assert meta.edition_date == "2019"

    def test_the_cover_is_the_first_page(self, archive):
        from epub_cbz import CbzMetadataReader
        meta = CbzMetadataReader(run=FakeExtractor()).read(archive)
        assert meta.cover_data == b"\xff\xd8cover"

    def test_an_archive_with_no_pages(self, archive):
        from epub_cbz import CbzMetadataReader
        reader = CbzMetadataReader(run=FakeExtractor({"notes.txt": b"x"}))
        with pytest.raises(MetadataError) as exc:
            reader.read(archive)
        assert "no pages" in str(exc.value).lower()

    def test_a_cbz_that_is_really_a_rar_is_simply_read(self, tmp_path):
        """§2.1 recorded two files named .cbz that are RAR inside, and
        the error said so rather than "not a comic archive". Now that
        RAR is read, the wart goes: what matters is what the file *is*,
        not what it is called."""
        from epub_cbz import CbzMetadataReader
        path = tmp_path / "Zootopia 001.cbz"
        path.write_bytes(b"Rar!\x1a\x07\x01\x00" + b"pretend")
        meta = CbzMetadataReader(run=FakeExtractor()).read(path)
        assert meta.title == "Zootopia 001"
        assert meta.page_count == 3


class TestServingIt:
    def test_a_cbr_is_repacked_as_a_cbz(self, archive):
        """The reader wants a zip, so a comic packed with RAR is packed
        again on the way out — the same path an unpacked EPUB already
        takes (§8.5). Nothing downstream knows."""
        from epub_rar import repack
        data = repack(RarArchive(archive, run=FakeExtractor()))
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
            assert zf.namelist() == ["cover.jpg", "p01.jpg", "p02.jpg"]
            assert zf.read("p01.jpg") == b"\xff\xd8one"

    def test_only_the_pages_are_carried_over(self, archive):
        """A comic is its pages. The scanner's notes.txt is not one, and
        a reader that meets it mid-run has to decide what to do with it —
        so it does not travel."""
        from epub_rar import repack
        data = repack(RarArchive(archive, run=FakeExtractor()))
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
            assert "notes.txt" not in zf.namelist()

    def test_the_pages_keep_their_order(self, archive):
        """Scanners pad their filenames, so 002 really does come before
        010 — sorted, as §8.7 already found for CBZ covers."""
        from epub_rar import repack
        pages = {f"p{n:03d}.jpg": f"page{n}".encode() for n in (1, 2, 10, 11)}
        data = repack(RarArchive(archive, run=FakeExtractor(pages)))
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as zf:
            assert zf.namelist() == ["p001.jpg", "p002.jpg", "p010.jpg",
                                     "p011.jpg"]


class TestTheLibraryAcceptsIt:
    """The two ends: a `.cbr` is a book the scan will take, and a `.cbr`
    is served as a zip so the reader never learns the difference."""

    def test_cbr_is_a_format_the_library_reads(self):
        from epub_server import BookLibrary
        assert ".cbr" in BookLibrary.SUFFIXES

    def test_a_cbr_is_served_as_a_comic_zip(self):
        from epub_server import EpubGateway
        assert EpubGateway.MEDIA_TYPES[".cbr"] == "application/vnd.comicbook+zip"

    def test_the_indexer_has_a_reader_for_it(self, gateway):
        readers = gateway._indexer._readers
        assert ".cbr" in readers
        assert readers[".cbr"].__class__.__name__ == "CbzMetadataReader"

    def test_comics_browse_counts_them(self, gateway):
        """§8.14's Comics group already names both containers, so the
        moment CBR is indexed it appears there and nowhere new."""
        from epub_browse import FORMAT_GROUPS
        assert "cbr" in FORMAT_GROUPS["comics"]["formats"]
