"""Finding covers for the books that have none.

A quarter of the library shows a lettered tile — 514 PDFs, 26 EPUBs and
15 PRCs whose files carry no usable image. Those books were indexed long
ago and their files have not changed, so a scan short-circuits past them
(§10.3): closing the gap needs a pass of its own.

Like every other maintenance pass it is **dry-run by default** and
**never writes to a book file**.
"""
from __future__ import annotations

import io

import pytest

from epub_covers import CoverBackfill
from tests.pdf_fixtures import PdfBuilder

pytest.importorskip("pypdfium2")


def jpeg(colour=(30, 90, 160)):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (600, 800), colour)
    draw = ImageDraw.Draw(img)
    for i in range(0, 800, 40):
        draw.rectangle([20, i, 580, i + 18],
                       fill=(250, 230, 90) if i % 80 else (10, 10, 40))
    out = io.BytesIO()
    img.save(out, "JPEG")
    return out.getvalue()


@pytest.fixture
def library_of_pdfs(client, library, gateway):
    """Two PDFs: one that opens on its cover, one that opens on words."""
    for existing in library.glob("*"):
        if existing.is_file():
            existing.unlink()
    PdfBuilder(title="Scanned Book", cover=jpeg()).build(
        library / "Scanner, A - Scanned Book.pdf")
    PdfBuilder(title="Wordy Book").build(library / "Writer, A - Wordy Book.pdf")
    gateway.reindex()
    return gateway


def strip_covers(gateway):
    """The state the real library is in: indexed before covers existed."""
    gateway.database.connection.execute("UPDATE metadata SET cover_path = NULL")


def backfill(gateway):
    return CoverBackfill(gateway.database, gateway.covers, gateway.library_root)


def cover_of(gateway, title):
    return gateway.database.connection.execute(
        "SELECT cover_path FROM metadata m JOIN book b ON b.id = m.book_id"
        " WHERE m.title = ?", (title,)).fetchone()["cover_path"]


class TestLookingWithoutChangingAnything:
    def test_a_dry_run_finds_the_cover_but_writes_nothing(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        report = backfill(gateway).run(apply=False)

        assert report["would_cover"] == 1
        assert report["covered"] == 0
        assert cover_of(gateway, "Scanned Book") is None

    def test_it_says_which_books(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        titles = [b["title"] for b in backfill(gateway).run()["plan"]]
        assert titles == ["Scanned Book"]

    def test_the_book_file_is_never_written_to(self, library_of_pdfs, library):
        gateway = library_of_pdfs
        strip_covers(gateway)
        book = library / "Scanner, A - Scanned Book.pdf"
        before = book.stat()
        backfill(gateway).run(apply=True)
        assert book.stat().st_mtime_ns == before.st_mtime_ns


class TestApplying:
    def test_the_cover_is_stored(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        report = backfill(gateway).run(apply=True)

        assert report["covered"] == 1
        stored = cover_of(gateway, "Scanned Book")
        assert stored
        assert (gateway.covers.root / stored).exists()

    def test_a_book_of_words_keeps_its_letter(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        backfill(gateway).run(apply=True)
        assert cover_of(gateway, "Wordy Book") is None

    def test_a_book_that_already_has_one_is_left_alone(self, library_of_pdfs):
        # Covers extracted at index time are not re-derived: this pass is
        # for the gap, not a second opinion.
        gateway = library_of_pdfs
        report = backfill(gateway).run(apply=True)
        assert report["would_cover"] == 0

    def test_where_it_came_from_is_recorded(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        backfill(gateway).run(apply=True)
        row = gateway.database.connection.execute(
            "SELECT origin, source FROM field_provenance WHERE field = 'cover'"
        ).fetchone()
        assert (row["origin"], row["source"]) == ("file", "pdfium")

    def test_running_it_twice_changes_nothing(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        backfill(gateway).run(apply=True)
        assert backfill(gateway).run(apply=True)["covered"] == 0


class TestRefusingRatherThanCrashing:
    def test_a_book_whose_file_has_gone_is_skipped(self, library_of_pdfs, library):
        gateway = library_of_pdfs
        strip_covers(gateway)
        (library / "Scanner, A - Scanned Book.pdf").unlink()
        report = backfill(gateway).run(apply=True)
        assert report["covered"] == 0
        assert report["failed"] == 1

    def test_a_cover_a_reader_removed_is_not_fetched_again(self, library_of_pdfs):
        # Dropping a wrong cover is a fact about the book, like a
        # corrected author: `origin = 'user'` and no pass touches it.
        gateway = library_of_pdfs
        strip_covers(gateway)
        book_id = gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title = 'Scanned Book'"
        ).fetchone()["book_id"]
        gateway.database.connection.execute(
            "INSERT INTO field_provenance (book_id, field, origin, source,"
            " locked, updated_at) VALUES (?, 'cover', 'user', 'hand', 1, 0)",
            (book_id,))

        assert backfill(gateway).run(apply=True)["covered"] == 0

    def test_only_so_many_at_a_time(self, library_of_pdfs, library):
        gateway = library_of_pdfs
        PdfBuilder(title="Another Scan", cover=jpeg((120, 20, 90))).build(
            library / "Scanner, A - Another Scan.pdf")
        gateway.reindex()
        strip_covers(gateway)
        assert backfill(gateway).run(apply=True, limit=1)["covered"] == 1


class TestThrowingACoverAway:
    """Page one is a cover 91% of the time; the rest are title pages,
    and a reader can say so. What they say is theirs and locked, so no
    later pass fetches another — the same rule as a corrected author."""

    def test_the_cover_goes(self, library_of_pdfs, client):
        gateway = library_of_pdfs
        book_id = gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title = 'Scanned Book'"
        ).fetchone()["book_id"]
        assert cover_of(gateway, "Scanned Book")

        assert client.delete(f"/api/books/{book_id}/cover").status_code == 200
        assert cover_of(gateway, "Scanned Book") is None

    def test_the_book_shows_a_letter_again(self, library_of_pdfs, client):
        gateway = library_of_pdfs
        book_id = gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title = 'Scanned Book'"
        ).fetchone()["book_id"]
        client.delete(f"/api/books/{book_id}/cover")
        book = client.get(f"/api/books/{book_id}/detail").json()["book"]
        assert book["cover"] is None

    def test_it_is_not_fetched_again(self, library_of_pdfs, client):
        gateway = library_of_pdfs
        book_id = gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title = 'Scanned Book'"
        ).fetchone()["book_id"]
        client.delete(f"/api/books/{book_id}/cover")
        assert backfill(gateway).run(apply=True)["covered"] == 0

    def test_the_book_file_is_untouched(self, library_of_pdfs, client, library):
        gateway = library_of_pdfs
        book = library / "Scanner, A - Scanned Book.pdf"
        before = book.stat()
        book_id = gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title = 'Scanned Book'"
        ).fetchone()["book_id"]
        client.delete(f"/api/books/{book_id}/cover")
        assert book.stat().st_mtime_ns == before.st_mtime_ns

    def test_an_unknown_book_is_a_404(self, library_of_pdfs, client):
        assert client.delete("/api/books/99999/cover").status_code == 404


# ── the provider half ─────────────────────────────────────────────────
from epub_covers import AuthorHint, CoverMatch, ProviderCovers  # noqa: E402
from epub_match import Candidate  # noqa: E402


class FakeProvider:
    """Answers from a script. No test may touch the network."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.asked = []

    def find_books(self, title, author=None, limit=8):
        self.asked.append((title, author))
        return self.answers.get(title, [])


def candidate(title, authors=("Test Author",), cover=True):
    return Candidate(title=title, authors=list(authors),
                     cover_url="https://covers/x-M.jpg" if cover else None,
                     source="openlibrary")


class TestWhoWroteIt:
    """A cover is matched on the words we have, and 249 of these books
    name their author in the filename and nowhere else."""

    def test_a_name_written_surname_first_is_taken(self):
        assert AuthorHint().surname("Anthony, Piers - Mute") == "anthony"

    def test_a_name_at_the_end_is_taken_when_the_library_knows_it(self):
        hint = AuthorHint({"scarrow"})
        assert hint.surname("Cato 02 - The Eagles Conquest - Simon Scarrow") \
            == "scarrow"

    def test_a_name_at_the_end_nobody_knows_is_not_a_name(self):
        # "The Eagle and the Wolves" must not become a Mr Wolves.
        assert AuthorHint(set()).surname(
            "Cato 04 - The Eagle and the Wolves") is None

    def test_a_title_with_no_name_in_it(self):
        assert AuthorHint().surname("Graph_Databases_2e_Neo4j") is None


class TestWhichCandidateIsTheBook:
    """The enrichment score is deliberately conservative about fields we
    do not have (§10.12), so it rejects nearly every one of these. A
    cover needs its own bar: the titles must actually agree."""

    def test_the_same_title_is_the_book(self):
        assert CoverMatch().accepts({"title": "Midnight's Mask"},
                                    candidate("Midnight's Mask"))

    def test_a_different_title_is_not(self):
        # "Anthony, Piers - Mute" answered with "God on Mute", which is a
        # book about prayer.
        assert not CoverMatch().accepts({"title": "Mute"},
                                        candidate("God on Mute"))

    def test_scaffolding_in_our_title_is_ignored(self):
        assert CoverMatch().accepts(
            {"title": "Elrod, P. N. - Vampire Files 02 - Lifeblood"},
            candidate("Lifeblood"))

    def test_a_candidate_with_no_cover_is_no_use(self):
        assert not CoverMatch().accepts({"title": "Lifeblood"},
                                        candidate("Lifeblood", cover=False))

    def test_the_author_corroborates_rather_than_vetoes(self):
        # This once refused a candidate whose author disagreed, which
        # cost us "The Art of War": we hold it under Sunzi and Open
        # Library files it under Sun Tzu. Agreement now ranks an answer
        # above a title-only one instead of throwing it away.
        book = {"title": "Blood River", "author_surname": "tilley"}
        theirs = CoverMatch().rank(book, candidate("Blood River",
                                                   ["Someone Else"]))
        ours = CoverMatch().rank(book, candidate("Blood River",
                                                 ["Patrick Tilley"]))
        assert ours > theirs > CoverMatch.NO


class TestFetchingThem:
    def _pass(self, gateway, provider, image=None):
        return ProviderCovers(gateway.database, gateway.covers,
                              gateway.library_root, provider,
                              fetch_image=image or (lambda url: jpeg()))

    def test_a_matching_cover_is_stored(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        provider = FakeProvider({"wordy book": [candidate("Wordy Book")]})
        report = self._pass(gateway, provider).run(apply=True)

        assert report["covered"] >= 1
        assert cover_of(gateway, "Wordy Book")

    def test_a_dry_run_asks_but_writes_nothing(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        provider = FakeProvider({"wordy book": [candidate("Wordy Book")]})
        report = self._pass(gateway, provider).run(apply=False)

        assert report["would_cover"] >= 1
        assert cover_of(gateway, "Wordy Book") is None

    def test_a_book_is_asked_about_once_ever(self, library_of_pdfs):
        # §6.6: everything a provider says is written down, including
        # that it said nothing. At one request every two minutes, asking
        # twice is a day of somebody's afternoon.
        gateway = library_of_pdfs
        strip_covers(gateway)
        provider = FakeProvider({})
        self._pass(gateway, provider).run(apply=True)
        asked_first = len(provider.asked)
        self._pass(gateway, provider).run(apply=True)
        assert len(provider.asked) == asked_first

    def test_a_wrong_answer_leaves_the_letter(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        provider = FakeProvider({"wordy book": [candidate("Something Else")]})
        report = self._pass(gateway, provider).run(apply=True)
        assert report["covered"] == 0
        assert cover_of(gateway, "Wordy Book") is None

    def test_a_cover_a_reader_threw_away_is_not_replaced(self, library_of_pdfs,
                                                        client):
        gateway = library_of_pdfs
        book_id = gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title = 'Scanned Book'"
        ).fetchone()["book_id"]
        client.delete(f"/api/books/{book_id}/cover")

        provider = FakeProvider({"scanned book": [candidate("Scanned Book")]})
        self._pass(gateway, provider).run(apply=True)
        assert cover_of(gateway, "Scanned Book") is None

    def test_a_picture_too_small_to_be_one_is_refused(self, library_of_pdfs):
        # Open Library answers a missing cover with a 1-pixel image
        # rather than a 404.
        from PIL import Image
        tiny = io.BytesIO()
        Image.new("RGB", (1, 1), "white").save(tiny, "JPEG")
        gateway = library_of_pdfs
        strip_covers(gateway)
        provider = FakeProvider({"wordy book": [candidate("Wordy Book")]})
        report = self._pass(gateway, provider,
                            image=lambda url: tiny.getvalue()).run(apply=True)
        assert report["covered"] == 0

    def test_a_provider_that_fails_does_not_stop_the_run(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)

        class Broken(FakeProvider):
            def find_books(self, title, author=None, limit=8):
                raise OSError("Open Library is having a bad day")

        report = self._pass(gateway, Broken()).run(apply=True)
        assert report["failed"] >= 1

    def test_only_so_many_at_a_time(self, library_of_pdfs):
        # The whole point of --max: a day's run is resumable.
        gateway = library_of_pdfs
        strip_covers(gateway)
        provider = FakeProvider({})
        self._pass(gateway, provider).run(apply=True, limit=1)
        assert len(provider.asked) == 1


class TestWhenTheAuthorIsSpeltAnotherWay:
    """The library holds "The Art of War" by Sunzi; Open Library files it
    under Sun Tzu. Both are that man, and a veto on the spelling costs a
    cover we could have had.

    So the author is a *strength*, not a veto: a candidate whose author
    agrees is preferred, and a title-only match is taken when nothing
    better is on offer — recorded as such, so an audit can look harder
    at exactly those.
    """

    def test_agreement_ranks_above_the_title_alone(self):
        book = {"title": "The Art of War", "author_surname": "sunzi"}
        agrees = candidate("The Art of War", ["Sunzi"])
        differs = candidate("The Art of War", ["Sun Tzu"])
        assert CoverMatch().rank(book, agrees) > CoverMatch().rank(book, differs)

    def test_a_title_only_match_is_still_a_match(self):
        book = {"title": "The Art of War", "author_surname": "sunzi"}
        assert CoverMatch().rank(book, candidate("The Art of War", ["Sun Tzu"]))

    def test_a_different_book_is_still_refused(self):
        # The guard that matters is the title: "God on Mute" is a book
        # about prayer, whoever wrote it.
        book = {"title": "Mute", "author_surname": "anthony"}
        assert CoverMatch().rank(book, candidate("God on Mute")) == 0

    def test_the_best_answer_wins_not_the_first(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        provider = FakeProvider({"wordy book": [
            candidate("Wordy Book", ["Someone Else"]),
            candidate("Wordy Book", ["Test Author"]),
        ]})
        pass_ = ProviderCovers(gateway.database, gateway.covers,
                               gateway.library_root, provider,
                               fetch_image=lambda url: jpeg())
        pass_.run(apply=True)
        source = gateway.database.connection.execute(
            "SELECT source FROM field_provenance WHERE field = 'cover'"
            " ORDER BY book_id DESC LIMIT 1").fetchone()["source"]
        assert source == "openlibrary"

    def test_a_title_only_match_says_so(self, library_of_pdfs):
        gateway = library_of_pdfs
        strip_covers(gateway)
        provider = FakeProvider({"wordy book": [
            candidate("Wordy Book", ["Someone Else"])]})
        pass_ = ProviderCovers(gateway.database, gateway.covers,
                               gateway.library_root, provider,
                               fetch_image=lambda url: jpeg())
        report = pass_.run(apply=True)
        assert report["covered"] == 1
        sources = {r["source"] for r in gateway.database.connection.execute(
            "SELECT source FROM field_provenance WHERE field = 'cover'")}
        assert "openlibrary-title" in sources
