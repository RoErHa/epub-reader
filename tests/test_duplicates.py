"""The same book, twice.

A library assembled over thirty years holds the same work more than
once: a better scan downloaded years later, a MOBI kept beside the
EPUB, and — since M8 made them visible — a copy that was unzipped and
never put back. Measured on the real library: **201 works held more
than once, 437 files, 236 of them spare.**

**No file is ever deleted.** The library is read-only, as it has been
since M2. Dedupe means one row per work on screen; the spare copies keep
their rows, keep their paths, and can be brought back by undoing it.
"""
from __future__ import annotations

import pytest

from epub_duplicates import DuplicateFinder
from tests.epub_fixtures import EpubBuilder
from tests.mobi_fixtures import MobiBuilder
from tests.pdf_fixtures import PdfBuilder


@pytest.fixture
def finder(gateway):
    return DuplicateFinder(gateway.database)


def clear(library):
    for old in library.rglob("*"):
        if old.is_file():
            old.unlink()


def visible(client):
    return [b["title"] for b in client.get("/api/books").json()["books"]]


class TestFindingThem:
    def test_two_copies_of_one_book_are_one_work(self, client, library,
                                                 gateway, finder):
        clear(library)
        for name in ("dune-a.epub", "dune-b.epub"):
            EpubBuilder(title="Dune", author="Frank Herbert",
                        chapters=2 if name.endswith("a.epub") else 3
                        ).build(library / name)
        gateway.reindex()
        groups = finder.plan()
        assert len(groups) == 1
        assert len(groups[0]["copies"]) == 2

    def test_different_books_are_left_alone(self, client, library, gateway,
                                            finder):
        clear(library)
        EpubBuilder(title="Dune", author="Frank Herbert").build(library / "a.epub")
        EpubBuilder(title="Dune Messiah", author="Frank Herbert").build(
            library / "b.epub")
        gateway.reindex()
        assert finder.plan() == []

    def test_the_same_title_by_another_author_is_another_book(self, client,
                                                              library, gateway,
                                                              finder):
        # "Shadows" has been written by a great many people.
        clear(library)
        EpubBuilder(title="Shadows", author="Ann Author").build(library / "a.epub")
        EpubBuilder(title="Shadows", author="Bill Writer").build(library / "b.epub")
        gateway.reindex()
        assert finder.plan() == []

    def test_a_book_with_no_author_is_not_matched_on_its_title_alone(
        self, client, library, gateway, finder
    ):
        # Titles like "Contents" and "Cover" are common enough that a
        # title on its own proves nothing.
        clear(library)
        for name in ("a.epub", "b.epub"):
            EpubBuilder(title="Contents", author="").build(library / name)
        gateway.reindex()
        assert finder.plan() == []

    def test_punctuation_does_not_hide_a_duplicate(self, client, library,
                                                   gateway, finder):
        clear(library)
        EpubBuilder(title="The Left Hand of Darkness",
                    author="Ursula K. Le Guin").build(library / "a.epub")
        EpubBuilder(title="Left Hand of Darkness, The",
                    author="Le Guin, Ursula K.").build(library / "b.epub")
        gateway.reindex()
        assert len(finder.plan()) == 1


class TestSameSeriesSamePosition:
    """Filing books by series found 46 pairs that matching on titles had
    missed — "Enders Game 4 - Children of the Mind.epub" beside the same
    file with the author appended, their stored titles differing just
    enough to look like two books.

    One author cannot have two fourth books in one series, so a shared
    position is the same book.
    """

    def series_pair(self, library, gateway, first, second):
        clear(library)
        EpubBuilder(title=first, author="Orson Scott Card").build(
            library / f"Enders Game 4 - {first}.epub")
        EpubBuilder(title=second, author="Orson Scott Card",
                    chapters=3).build(
            library / f"Enders Game 4 - {second} - Card_ Orson Scott.epub")
        gateway.reindex()
        from epub_series import SeriesFromNames
        SeriesFromNames(gateway.database).run(apply=True)

    def test_a_shared_position_is_a_duplicate(self, client, library, gateway,
                                              finder):
        # The titles differ, so nothing else here would have paired them.
        self.series_pair(library, gateway, "Children of the Mind",
                         "Children of the Mind (v1.1)")
        groups = finder.plan()
        assert len(groups) == 1
        assert len(groups[0]["copies"]) == 2

    def test_two_positions_are_two_books(self, client, library, gateway, finder):
        clear(library)
        for number, title in ((4, "Children of the Mind"), (5, "Ender's Shadow")):
            EpubBuilder(title=title, author="Orson Scott Card").build(
                library / f"Enders Game {number} - {title}.epub")
        gateway.reindex()
        from epub_series import SeriesFromNames
        SeriesFromNames(gateway.database).run(apply=True)
        assert finder.plan() == []

    def test_the_same_position_by_another_author_is_another_book(
        self, client, library, gateway, finder
    ):
        clear(library)
        EpubBuilder(title="One", author="Ann Author").build(
            library / "Chronicles 1 - One.epub")
        EpubBuilder(title="Two", author="Ann Author").build(
            library / "Chronicles 2 - Two.epub")
        EpubBuilder(title="Other", author="Bill Writer").build(
            library / "Chronicles 1 - Other.epub")
        EpubBuilder(title="Second", author="Bill Writer").build(
            library / "Chronicles 2 - Second.epub")
        gateway.reindex()
        from epub_series import SeriesFromNames
        SeriesFromNames(gateway.database).run(apply=True)
        assert finder.plan() == []


class TestTwoPdfsAreNotEnough:
    """A real failure, and the only one in 235: two different Roman
    histories were hidden as one book. Their scanner had written the
    same title into both — "roman family, republican rome army and
    allies, war and imperialism in republican rome" — a list of several
    works, and M7 already established that a PDF's title is not to be
    trusted on its own.

    So when both copies are PDFs, their filenames have to agree too.
    Where one side is an EPUB its title *is* trustworthy, and a PDF
    matching it is a real duplicate.
    """

    def test_two_pdfs_with_one_junk_title_are_not_the_same_book(
        self, client, library, gateway, finder
    ):
        clear(library)
        junk = "Roman Family, Republican Rome Army and Allies, War and Imperialism"
        PdfBuilder(title=junk, author="Various Hands").build(
            library / "War and Imperialism in Republican Rome (not ocr).pdf")
        PdfBuilder(title=junk, author="Various Hands").build(
            library / "Republican Rome, the Army and the Allies (not ocr).pdf")
        gateway.reindex()
        assert finder.plan() == []

    def test_two_pdfs_of_one_book_still_pair_up(self, client, library, gateway,
                                                finder):
        clear(library)
        for name in ("Dune.pdf", "Dune (retail).pdf"):
            PdfBuilder(title="Dune", author="Frank Herbert",
                       pages=2 if "retail" in name else 3).build(library / name)
        gateway.reindex()
        assert len(finder.plan()) == 1

    def test_a_pdf_still_pairs_with_an_epub(self, client, library, gateway,
                                            finder):
        # The EPUB's title is trustworthy, so a PDF agreeing with it is
        # a real duplicate however it is named.
        clear(library)
        EpubBuilder(title="Why Does E=mc2?", author="Brian Cox").build(
            library / "why.epub")
        PdfBuilder(title="Why Does E=mc2?", author="Brian Cox").build(
            library / "Cox, Brian - Why Does E=mc2 (Da Capo, 2009).pdf")
        gateway.reindex()
        assert len(finder.plan()) == 1


class TestWhichCopyToKeep:
    def test_the_reflowable_one_beats_the_scan(self, client, library, gateway,
                                               finder):
        # A PDF is a picture of a book; an EPUB is the book.
        clear(library)
        EpubBuilder(title="Dune", author="Frank Herbert").build(library / "a.epub")
        PdfBuilder(title="Dune", author="Frank Herbert").build(
            library / "Frank Herbert - Dune.pdf")
        gateway.reindex()
        group = finder.plan()[0]
        assert group["keep"]["format"] == "epub"

    def test_epub_beats_mobi(self, client, library, gateway, finder):
        clear(library)
        EpubBuilder(title="Dune", author="Frank Herbert").build(library / "a.epub")
        MobiBuilder(title="Dune", authors=["Frank Herbert"]).build(
            library / "b.mobi")
        gateway.reindex()
        assert finder.plan()[0]["keep"]["format"] == "epub"

    def test_a_cover_breaks_a_tie(self, client, library, gateway, finder):
        clear(library)
        EpubBuilder(title="Dune", author="Frank Herbert",
                    cover=False).build(library / "plain.epub")
        EpubBuilder(title="Dune", author="Frank Herbert",
                    cover=True).build(library / "pretty.epub")
        gateway.reindex()
        keep = finder.plan()[0]["keep"]
        assert keep["cover_path"]

    def test_the_copy_you_are_reading_is_never_the_spare(self, client, library,
                                                         gateway, finder):
        # Your place and your highlights hang off one copy. Hiding that
        # one would hide them.
        clear(library)
        EpubBuilder(title="Dune", author="Frank Herbert",
                    cover=True).build(library / "pretty.epub")
        PdfBuilder(title="Dune", author="Frank Herbert").build(
            library / "Frank Herbert - Dune.pdf")
        gateway.reindex()
        pdf = next(b for b in client.get("/api/books").json()["books"]
                   if b["name"].endswith(".pdf"))
        client.put(f"/api/books/{pdf['id']}/position",
                   json={"cfi": "epubcfi(/6/4)", "fraction": 0.4})

        assert finder.plan()[0]["keep"]["id"] == pdf["id"]

    def test_it_says_why_it_chose(self, client, library, gateway, finder):
        clear(library)
        EpubBuilder(title="Dune", author="Frank Herbert").build(library / "a.epub")
        PdfBuilder(title="Dune", author="Frank Herbert").build(
            library / "Frank Herbert - Dune.pdf")
        gateway.reindex()
        assert "epub" in finder.plan()[0]["reason"].lower()


class TestPuttingItIntoEffect:
    def make_two(self, library, gateway):
        clear(library)
        EpubBuilder(title="Dune", author="Frank Herbert",
                    cover=True).build(library / "good.epub")
        PdfBuilder(title="Dune", author="Frank Herbert").build(
            library / "Frank Herbert - Dune.pdf")
        gateway.reindex()

    def test_a_dry_run_changes_nothing(self, client, library, gateway, finder):
        self.make_two(library, gateway)
        before = len(visible(client))
        report = finder.run(apply=False)
        assert report["hidden"] == 0
        assert report["would_hide"] == 1
        assert len(visible(client)) == before

    def test_applying_leaves_one_row_per_work(self, client, library, gateway,
                                              finder):
        self.make_two(library, gateway)
        finder.run(apply=True)
        assert visible(client).count("Dune") == 1

    def test_no_file_is_touched(self, client, library, gateway, finder):
        self.make_two(library, gateway)
        before = {p: p.stat().st_mtime_ns for p in library.rglob("*")
                  if p.is_file()}
        finder.run(apply=True)
        after = {p: p.stat().st_mtime_ns for p in library.rglob("*")
                 if p.is_file()}
        assert before == after

    def test_the_spare_is_still_in_the_database(self, client, library, gateway,
                                                finder):
        self.make_two(library, gateway)
        finder.run(apply=True)
        assert gateway.database.connection.execute(
            "SELECT COUNT(*) n FROM book WHERE duplicate_of IS NOT NULL"
        ).fetchone()["n"] == 1

    def test_it_can_be_undone(self, client, library, gateway, finder):
        self.make_two(library, gateway)
        finder.run(apply=True)
        assert finder.restore() == 1
        assert visible(client).count("Dune") == 2

    def test_running_twice_finds_nothing_the_second_time(self, client, library,
                                                         gateway, finder):
        self.make_two(library, gateway)
        finder.run(apply=True)
        assert finder.run(apply=True)["hidden"] == 0

    def test_a_hidden_copy_is_out_of_search_too(self, client, library, gateway,
                                                finder):
        self.make_two(library, gateway)
        finder.run(apply=True)
        found = client.get("/api/search?q=Dune").json()
        assert len(found.get("books", [])) <= 1


class TestTheEndpoint:
    def test_it_defaults_to_a_dry_run(self, client, library, gateway):
        clear(library)
        # Two *different* files of one book: byte-identical copies are
        # already one book, since identity is the content hash.
        for i, name in enumerate(("a.epub", "b.epub")):
            EpubBuilder(title="Dune", author="Frank Herbert",
                        chapters=2 + i).build(library / name)
        gateway.reindex()
        assert client.post("/api/books/dedupe").json()["hidden"] == 0
        assert visible(client).count("Dune") == 2

    def test_apply_hides_the_spare(self, client, library, gateway):
        clear(library)
        for i, name in enumerate(("a.epub", "b.epub")):
            EpubBuilder(title="Dune", author="Frank Herbert",
                        chapters=2 + i).build(library / name)
        gateway.reindex()
        assert client.post("/api/books/dedupe?apply=true").json()["hidden"] == 1
        assert visible(client).count("Dune") == 1


class TestTheCommand:
    """`run.sh dedupe` — looking is the default, as with every other
    pass that changes what you see."""

    @pytest.fixture(autouse=True)
    def offline(self, monkeypatch):
        import epub_server

        def refuse(*args, **kwargs):
            def fetch(url, headers=None):
                raise OSError("the test suite does not use the network")
            return fetch

        monkeypatch.setattr(epub_server, "PoliteFetcher", refuse)

    @staticmethod
    def env(tmp_path, library):
        state = tmp_path / "state"
        state.mkdir(exist_ok=True)
        path = tmp_path / ".env.dedupe"
        path.write_text(
            f"EPUB_PORT=8776\nEPUB_BIND=127.0.0.1\nLIBRARY_ROOT={library}\n"
            f"DB_PATH={state}/books.db\nCOVER_CACHE={state}/covers\n"
            f"LOG_LEVEL=WARNING\nLOG_FILE={state}/test.log\n")
        return path

    def two_copies(self, tmp_path):
        library = tmp_path / "books"
        library.mkdir(exist_ok=True)
        for i, name in enumerate(("a.epub", "b.epub")):
            EpubBuilder(title="Dune", author="Frank Herbert",
                        chapters=2 + i).build(library / name)
        return self.env(tmp_path, library)

    def test_it_reports_without_hiding_anything(self, tmp_path, capsys):
        from epub_gateway import GatewayCLI
        env = self.two_copies(tmp_path)
        GatewayCLI.main(["--env", str(env), "--scan"])
        assert GatewayCLI.main(["--env", str(env), "--dedupe"]) == 0
        assert "would hide 1" in capsys.readouterr().out

    def test_apply_hides_the_spare(self, tmp_path, capsys):
        from epub_gateway import GatewayCLI
        env = self.two_copies(tmp_path)
        GatewayCLI.main(["--env", str(env), "--scan"])
        assert GatewayCLI.main(["--env", str(env), "--dedupe", "--apply"]) == 0
        assert "hid 1 spare copy" in capsys.readouterr().out

    def test_a_library_with_no_duplicates_says_so(self, tmp_path, capsys):
        from epub_gateway import GatewayCLI
        library = tmp_path / "books"
        library.mkdir()
        EpubBuilder(title="Dune", author="Frank Herbert").build(
            library / "a.epub")
        env = self.env(tmp_path, library)
        GatewayCLI.main(["--env", str(env), "--scan"])
        GatewayCLI.main(["--env", str(env), "--dedupe"])
        assert "no duplicates" in capsys.readouterr().out
