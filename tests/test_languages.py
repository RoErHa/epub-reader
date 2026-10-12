"""What language a book is written in, and browsing by it.

Measured on the real library before any of this was written: 1,143 of
1,325 EPUBs say what language they are in, spelt seven ways for English
alone (`en`, `EN`, `En`, `eng`, `en-US`, `en-us`, `en-GB`) and three for
Dutch. No PDF and no comic says anything. And two files that do say are
wrong — `Higgs.epub` is Dutch and calls itself English, *Lonesome Dove*
is English and calls itself Dutch — while a count of common words read
both correctly, and agreed with the file on every other book of a
209-book sample it could judge.
"""
from __future__ import annotations

import pytest

from tests.epub_fixtures import EpubBuilder
from tests.pdf_fixtures import PdfBuilder

DUTCH = ("Het was een koude avond in de stad en de kinderen liepen naar huis. "
         "Ze hadden geen geld meer, maar ze waren niet bang. Hij zei dat het "
         "niet zo ver was en dat ze er voor het donker zouden zijn. Toen ze "
         "bij de brug kwamen, zag ik dat er iets in het water lag.")
ENGLISH = ("It was a cold evening in the city and the children walked home. "
           "They had no money left, but they were not afraid. He said that it "
           "was not far and that they would be there before dark. When they "
           "came to the bridge, I saw that there was something in the water.")
GERMAN = ("Es war ein kalter Abend in der Stadt und die Kinder gingen nach "
          "Hause. Sie hatten kein Geld mehr, aber sie hatten keine Angst. Er "
          "sagte, dass es nicht weit sei und dass sie vor der Dunkelheit dort "
          "sein würden. Als sie an die Brücke kamen, sah ich etwas im Wasser.")


def epub(library, name, prose, language="en", **kw):
    EpubBuilder(title=name, author="Test Author", language=language,
                prose=prose, chapters=2, paragraphs_per_chapter=4,
                **kw).build(library / f"{name}.epub")


def language_of(gateway, title):
    row = gateway.database.connection.execute(
        "SELECT language FROM metadata WHERE title = ?", (title,)).fetchone()
    return row["language"]


# ── codes ───────────────────────────────────────────────────────────────
class TestLanguageCode:
    @pytest.mark.parametrize("raw, code", [
        ("en", "en"), ("EN", "en"), ("En", "en"), ("en-US", "en"),
        ("en_GB", "en"), ("eng", "en"), ("nl", "nl"), ("NL", "nl"),
        ("nld", "nl"), ("dut", "nl"), ("ger", "de"), ("fra", "fr"),
        ("th", "th"), ("  ru  ", "ru"),
    ])
    def test_every_spelling_of_a_language_is_one_code(self, raw, code):
        from epub_language import LanguageCode
        assert LanguageCode.normalise(raw) == code

    @pytest.mark.parametrize("raw", [
        None, "", "und", "UND", "mul", "zxx", "unknown", "n/a", "x",
        "english", "123", "en-",
    ])
    def test_what_is_not_a_language_is_nothing(self, raw):
        from epub_language import LanguageCode
        assert LanguageCode.normalise(raw) is None

    def test_a_marc_code_nobody_mapped_is_kept_as_it_came(self):
        # Truncating is not translating: "swe" is Swedish, not Swahili.
        from epub_language import LanguageCode
        assert LanguageCode.normalise("swe") == "sv"
        assert LanguageCode.normalise("haw") == "haw"


# ── reading the words ──────────────────────────────────────────────────
class TestLanguageGuess:
    @pytest.mark.parametrize("prose, code", [
        (DUTCH, "nl"), (ENGLISH, "en"), (GERMAN, "de")])
    def test_common_words_say_what_the_language_is(self, prose, code):
        from epub_language import LanguageGuess
        assert LanguageGuess.of(prose * 4) == code

    def test_too_few_words_say_nothing(self):
        from epub_language import LanguageGuess
        assert LanguageGuess.of(DUTCH[:80]) is None

    def test_words_that_belong_to_no_language_say_nothing(self):
        # A table of figures, an index, a scan's OCR rubbish.
        from epub_language import LanguageGuess
        assert LanguageGuess.of("Xq zorbl frim " * 100) is None


# ── the pass ───────────────────────────────────────────────────────────
class TestLanguagesFromText:
    def test_a_book_that_says_nothing_is_read(self, library, gateway):
        epub(library, "Stille Boek", DUTCH, language="und")
        gateway.reindex()
        assert language_of(gateway, "Stille Boek") is None
        gateway.read_languages(apply=True)
        assert language_of(gateway, "Stille Boek") == "nl"

    def test_a_dry_run_changes_nothing(self, library, gateway):
        epub(library, "Stille Boek", DUTCH, language="und")
        gateway.reindex()
        report = gateway.languages.run(apply=False)
        assert report["would_detect"] >= 1
        assert language_of(gateway, "Stille Boek") is None

    def test_a_file_that_is_wrong_about_itself_is_corrected(
            self, library, gateway):
        # Higgs.epub: Dutch from the first page, `en` in its own OPF.
        epub(library, "Higgs", DUTCH, language="en")
        gateway.reindex()
        report = gateway.languages.run(apply=True)
        assert language_of(gateway, "Higgs") == "nl"
        assert any(item["was"] == "en" for item in report["plan"])

    def test_a_file_that_is_right_is_left_alone(self, library, gateway):
        epub(library, "Plain English", ENGLISH, language="en-US")
        gateway.reindex()
        report = gateway.languages.run(apply=True)
        assert language_of(gateway, "Plain English") == "en"
        assert all(item["id"] != self._id(gateway, "Plain English")
                   for item in report["plan"])

    def test_a_language_you_set_is_never_read_over(self, client, library,
                                                    gateway):
        epub(library, "Mine", DUTCH, language="und")
        gateway.reindex()
        book_id = self._id(gateway, "Mine")
        assert client.patch(f"/api/books/{book_id}/metadata",
                            json={"language": "de"}).status_code == 200
        gateway.read_languages(apply=True)
        assert language_of(gateway, "Mine") == "de"

    def test_what_it_read_survives_a_rescan(self, library, gateway):
        epub(library, "Stille Boek", DUTCH, language="und")
        gateway.reindex()
        gateway.read_languages(apply=True)
        import os
        import time
        path = library / "Stille Boek.epub"
        os.utime(path, (time.time() + 5, time.time() + 5))
        gateway.reindex()
        assert language_of(gateway, "Stille Boek") == "nl"

    def test_a_book_is_read_once_ever(self, library, gateway):
        # Every restart runs this; reading two thousand books each time
        # would be the scan's whole cost. The answer is kept against the
        # book's content, so a replaced file is read again.
        epub(library, "Stille Boek", DUTCH, language="und")
        gateway.reindex()
        gateway.read_languages(apply=True)
        reads = []
        gateway.languages.reader = _Counting(gateway.languages.reader, reads)
        gateway.read_languages(apply=True)
        assert reads == []

    def test_a_pdf_is_read_from_its_pages(self, library, gateway):
        PdfBuilder(title="Een Pdf", pages=4,
                   lines=[DUTCH[i:i + 70] for i in range(0, len(DUTCH), 70)]
                   * 3).build(library / "Een Pdf.pdf")
        gateway.reindex()
        gateway.read_languages(apply=True)
        assert language_of(gateway, "Een Pdf") == "nl"

    def test_the_provenance_says_it_was_read_from_the_text(
            self, library, gateway):
        epub(library, "Stille Boek", DUTCH, language="und")
        gateway.reindex()
        gateway.read_languages(apply=True)
        row = gateway.database.connection.execute(
            "SELECT origin, source FROM field_provenance"
            " WHERE book_id = ? AND field = 'language'",
            (self._id(gateway, "Stille Boek"),)).fetchone()
        assert (row["origin"], row["source"]) == ("provider", "text")

    def test_it_runs_with_the_other_free_passes(self, library, gateway):
        epub(library, "Stille Boek", DUTCH, language="und")
        gateway.reindex()
        report = gateway.tidy()
        assert report["languages"] >= 1
        assert language_of(gateway, "Stille Boek") == "nl"
        assert "language" in gateway.tidy_summary(report)

    @staticmethod
    def _id(gateway, title):
        return gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title = ?",
            (title,)).fetchone()["book_id"]


class _Counting:
    def __init__(self, inner, reads):
        self._inner, self._reads = inner, reads

    def sample(self, path, fmt):
        self._reads.append(path)
        return self._inner.sample(path, fmt)


# ── a scan writes one spelling ─────────────────────────────────────────
class TestIngest:
    def test_a_scan_stores_the_code_not_the_spelling(self, library, gateway):
        epub(library, "Spelt Oddly", ENGLISH, language="EN-us")
        gateway.reindex()
        assert language_of(gateway, "Spelt Oddly") == "en"

    def test_what_is_already_stored_is_tidied(self, library, gateway):
        epub(library, "Old Row", ENGLISH, language="en")
        gateway.reindex()
        gateway.database.connection.execute(
            "UPDATE metadata SET language = 'eng' WHERE title = 'Old Row'")
        gateway.read_languages(apply=True)
        assert language_of(gateway, "Old Row") == "en"


# ── browsing ───────────────────────────────────────────────────────────
class TestBrowse:
    @pytest.fixture
    def shelved(self, client, library, gateway):
        for old in library.glob("*.epub"):
            old.unlink()
        epub(library, "Een", DUTCH, language="nl")
        epub(library, "Twee", DUTCH, language="NLD")
        epub(library, "One", ENGLISH, language="en")
        PdfBuilder(title="Nameless Scan", lines=["1 2 3"]).build(
            library / "Nameless Scan.pdf")
        gateway.reindex()
        return client

    def test_languages_are_listed_by_code_with_their_counts(self, shelved):
        groups = shelved.get("/api/languages").json()["languages"]
        counts = {g["id"]: g["books"] for g in groups}
        assert counts == {"nl": 2, "en": 1, "unknown": 1}

    def test_unknown_comes_last(self, shelved):
        groups = shelved.get("/api/languages").json()["languages"]
        assert groups[-1]["id"] == "unknown"

    def test_a_language_lists_its_books(self, shelved):
        books = shelved.get("/api/languages/nl/books").json()["books"]
        assert sorted(b["title"] for b in books) == ["Een", "Twee"]

    def test_unknown_lists_the_books_that_say_nothing(self, shelved):
        books = shelved.get("/api/languages/unknown/books").json()["books"]
        assert [b["title"] for b in books] == ["Nameless Scan"]

    def test_a_code_that_is_not_one_is_refused(self, shelved):
        assert shelved.get("/api/languages/klingon!/books").status_code == 422


# ── by hand ────────────────────────────────────────────────────────────
class TestEditing:
    @pytest.fixture
    def book_id(self, client, library, gateway):
        epub(library, "Hand Set", ENGLISH, language="en")
        gateway.reindex()
        return gateway.database.connection.execute(
            "SELECT book_id FROM metadata WHERE title = 'Hand Set'"
        ).fetchone()["book_id"]

    def test_a_language_can_be_set_and_is_kept_as_yours(
            self, client, gateway, book_id):
        response = client.patch(f"/api/books/{book_id}/metadata",
                                json={"language": "NLD"})
        assert response.status_code == 200
        assert language_of(gateway, "Hand Set") == "nl"
        row = gateway.database.connection.execute(
            "SELECT origin, locked FROM field_provenance"
            " WHERE book_id = ? AND field = 'language'", (book_id,)).fetchone()
        assert (row["origin"], row["locked"]) == ("user", 1)

    def test_it_can_be_cleared(self, client, gateway, book_id):
        client.patch(f"/api/books/{book_id}/metadata", json={"language": None})
        assert language_of(gateway, "Hand Set") is None

    def test_a_language_that_is_not_one_is_refused(self, client, gateway,
                                                   book_id):
        response = client.patch(f"/api/books/{book_id}/metadata",
                                json={"language": "Elvish"})
        assert response.status_code == 422
        assert language_of(gateway, "Hand Set") == "en"
