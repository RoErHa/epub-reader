"""The names that are clear to a person and not to the code.

    Colleen McCullough - Masters of Rome 1 - First Man in Rome.pdf
    Andrew Loomis - Drawing the Head and Hands.pdf
    RAINBOWS END by Vernor Vinge.pdf

§8.13a reads one rigid shape — `Surname, First - …` — and reads it
outright. That is why it fires on the books it does and walks past
these: there is no comma, so there is no name, so there is nobody to ask
a series or a title of. Every one of the above is instantly legible to a
person.

§12.1's inversion is what this module is: **the filename stops being the
answer and becomes a hypothesis.** Cut the name every plausible way, ask
Open Library which reading is real, and keep the one that comes back
corroborated.

Measured on the real library before any of this was written — 69
splittable unreadable names, 128 distinct candidate segments, every one
put to Open Library:

    anchored (the segment is an author it knows)     42/69 = 61%

§12.1 set 20% as the line below which this would be a different piece of
work. It is 61%, so it is this one.

**But anchoring is not enough, and the same measurement is why.** Open
Library confirms `Jack Ryan`, `Star Wars`, `Star Trek`, `Ancient Egypt`,
`Autumn leaves`, `Marcus Aurelius` and `Wizards of the Coast` as authors.
Crediting *Without Remorse* to Jack Ryan — its protagonist — would be a
regression dressed as a repair, and `Jack Ryan 1 - Without Remorse` is
already correctly credited to Tom Clancy.

What tells those from Colleen McCullough is §12.1's third step, and the
measurement says it is not garnish but the whole safety property:

> **The anchor has to account for the other end of the name.** Ask that
> author's own works whether they hold the book. Measured over the same
> sample: 13 of 16 genuine names accounted for, and **0 of 8 impostors**
> — Jack Ryan, Star Wars, Star Trek, Ancient Egypt, Autumn leaves,
> Marcus Aurelius and Wizards of the Coast every one refused.

The three genuine names it missed (Edward Gibbon, Jeff Mellem, Maxim
Jakubowski) are left exactly as they were, which is the direction a
mistake here should fall.

No test touches the network: the provider is injected, as §10.11 says.
"""
from __future__ import annotations

import pytest

from epub_hypotheses import NameHypotheses, NameSplitter
from tests.pdf_fixtures import PdfBuilder


class FakeProvider:
    """Knows only who it has been told about, and remembers every
    question — the asking is half of what these tests are about."""

    def __init__(self) -> None:
        self.authors: dict[str, dict] = {}
        self.works: dict[str, list[str]] = {}
        self.asked: list[tuple[str, str]] = []

    def knows(self, name: str, *titles: str, key: str | None = None):
        key = key or "OL" + str(abs(hash(name)) % 10000) + "A"
        self.authors[name.casefold()] = {"key": key, "name": name,
                                         "works": len(titles)}
        self.works[key] = list(titles)
        return self

    def find_author(self, name):
        self.asked.append(("author", name))
        return self.authors.get(name.casefold())

    def author_works(self, key, limit=200):
        self.asked.append(("works", key))
        return [{"title": t, "source": "openlibrary"}
                for t in self.works.get(key, [])]


@pytest.fixture
def provider():
    return FakeProvider()


@pytest.fixture
def reader(gateway, provider):
    return NameHypotheses(gateway.database, provider)


def clear(library):
    for old in library.rglob("*"):
        if old.is_file():
            old.unlink()


def pdf(library, name, author=None, title=None):
    """A scan whose filename knows and whose Info dictionary does not."""
    PdfBuilder(title=title, author=author, lines=[name]).build(
        library / f"{name}.pdf")


def book(gateway, stem):
    return gateway.database.connection.execute(
        """SELECT b.id, m.title, m.file_title, m.series_index,
                  s.name AS series,
                  (SELECT GROUP_CONCAT(a.name, ' & ') FROM book_author ba
                     JOIN author a ON a.id = ba.author_id
                    WHERE ba.book_id = b.id ORDER BY ba.position) AS authors
             FROM book b JOIN metadata m ON m.book_id = b.id
             LEFT JOIN series s ON s.id = m.series_id
            WHERE b.path LIKE ?""", (f"%{stem}%",)).fetchone()


class TestCuttingTheNameEveryWay:
    """The splitter alone: no database, no provider, no judgement."""

    def test_both_orderings_are_offered(self):
        readings = NameSplitter.readings("Andrew Loomis - Successful Drawing")
        assert ("Andrew Loomis", "Successful Drawing") in [
            (r.author, r.title) for r in readings]
        assert ("Successful Drawing", "Andrew Loomis") in [
            (r.author, r.title) for r in readings]

    def test_the_author_may_sit_in_the_middle(self):
        """`Forgotten Realms - Erevis Cale 01 - Paul S. Kemp - Twilight
        Falling` names a shelf, a position, a person and a book, in that
        order. Only one segment is the person and it is neither end."""
        readings = NameSplitter.readings(
            "Forgotten Realms - Erevis Cale 01 - Paul S. Kemp - Twilight Falling")
        assert ("Paul S. Kemp", "Twilight Falling") in [
            (r.author, r.title) for r in readings]

    @pytest.mark.parametrize("stem,author,title", [
        ("RAINBOWS END by Vernor Vinge", "Vernor Vinge", "RAINBOWS END"),
        ("Kimon Nicolaides – The Natural Way to Draw",
         "Kimon Nicolaides", "The Natural Way to Draw"),
        ("Sketching People Life Drawing Basics - Jeff Mellem",
         "Jeff Mellem", "Sketching People Life Drawing Basics"),
    ])
    def test_the_separators_real_filenames_use(self, stem, author, title):
        """A spaced dash, an en-dash, and the word `by`."""
        assert (author, title) in [
            (r.author, r.title) for r in NameSplitter.readings(stem)]

    def test_underscores_stand_in_for_spaces(self):
        """§8.13d's rule, and it has to be applied before splitting or
        the name is one long word."""
        readings = NameSplitter.readings("Andrew_Loomis_-_Successful_Drawing")
        assert ("Andrew Loomis", "Successful Drawing") in [
            (r.author, r.title) for r in readings]

    def test_a_possessive_is_a_person(self):
        """`Holly Lisle's - Create A Character` is a person and a book,
        with the genitive left over from "Holly Lisle's Create a …"."""
        assert "Holly Lisle" in [
            r.author for r in NameSplitter.readings(
                "Holly Lisle's - Create A Character")]

    def test_a_name_written_for_sorting_is_read_first_name_first(self):
        """`Kemp, Paul S.` is the same person as `Paul S. Kemp`, and the
        library displays people the way they are printed (§7)."""
        assert "Paul S. Kemp" in [
            r.author for r in NameSplitter.readings(
                "Forgotten Realms - War of the Spider Queen 06"
                " - Kemp, Paul S. - Resurrection")]

    def test_a_name_in_brackets_is_still_a_name(self):
        assert "Geoffrey Mandel" in [
            r.author for r in NameSplitter.readings(
                "Star Trek - Star Trek Star Charts (Geoffrey Mandel)")]

    @pytest.mark.parametrize("stem,why", [
        ("Trajan - A Life",
         ("a one-word segment is not a person we can confirm; the emperor"
          " would anchor and the biography be credited to its subject")),
        ("Nero - end dynasty", "the same, and this one is a subtitle"),
    ])
    def test_a_single_word_is_never_a_candidate_author(self, stem, why):
        assert stem.split(" - ")[0] not in [
            r.author for r in NameSplitter.readings(stem)], why

    def test_a_segment_ending_in_a_number_is_a_shelf(self):
        """`Erevis Cale 01` is a position on a shelf. Reading it as a
        person costs a request and can only ever be wrong."""
        assert "Erevis Cale 01" not in [
            r.author for r in NameSplitter.readings(
                "Forgotten Realms - Erevis Cale 01 - Paul S. Kemp"
                " - Twilight Falling")]

    def test_a_numbered_segment_is_not_a_book_either(self):
        """The number rule cuts both ways, and the second edge cost three
        books to find. `Emperor 1` is a shelf and a position — and Open
        Library holds a Conn Iggulden work called *Emperor*, so offering
        it as a title retitled all three novels `Emperor 1`, `Emperor 2`
        and `Emperor 3`. A shelf is no more a book than it is a person.
        """
        titles = [r.title for r in NameSplitter.readings(
            "Conn Iggulden - Emperor 1 - The Gates of Rome")]
        assert "The Gates of Rome" in titles
        assert "Emperor 1" not in titles

    def test_a_name_with_nothing_to_split_yields_nothing(self):
        """`Stardust` says one thing. There is no second end to
        corroborate it with, and a bare title search is what §6.3 warns
        about."""
        assert NameSplitter.readings("Stardust") == []


class TestTheAnchorMustAccountForTheBook:
    """The safety property, and the reason the pass is worth having."""

    def test_a_reading_both_ends_confirm_is_written(self, client, library,
                                                    gateway, reader, provider):
        clear(library)
        pdf(library, "Andrew Loomis - Drawing the Head and Hands")
        gateway.reindex()
        provider.knows("Andrew Loomis", "Drawing the Head and Hands",
                       "Successful Drawing")

        reader.run(apply=True)
        row = book(gateway, "Drawing the Head")
        assert row["authors"] == "Andrew Loomis"
        assert row["title"] == "Drawing the Head and Hands"

    @pytest.mark.parametrize("stem,impostor,theirs", [
        # Every one of these is an author at Open Library, and every one
        # was refused by the works check in the measurement.
        ("Star Wars - New Jedi Order 07 - Edge of Victory I - Conquest",
         "Star Wars", ["The Phantom Menace", "Heir to the Empire"]),
        ("Ancient Egypt - The Definitive Visual History",
         "Ancient Egypt", ["Ancient Egypt"]),
        ("Anthony Birley - Marcus Aurelius - Routledge",
         "Marcus Aurelius", ["Meditations", "The Communings with Himself"]),
        ("Wizards of the Coast - Writing Guidelines",
         "Wizards of the Coast", ["Player's Handbook"]),
    ])
    def test_an_author_who_does_not_hold_the_book_is_refused(
            self, client, library, gateway, reader, provider,
            stem, impostor, theirs):
        """**Every one of these is an author at Open Library**, and not
        one of them wrote the book its filename sits on. Anchoring alone
        would credit a Star Wars novel to `Star Wars`, a history of Rome
        to the emperor it is about, and a style guide to its publisher.

        Asking that author's own works is the only thing standing
        between this pass and all four.
        """
        clear(library)
        pdf(library, stem)
        gateway.reindex()
        provider.knows(impostor, *theirs)

        reader.run(apply=True)
        assert book(gateway, stem.split(" - ")[-1])["authors"] is None

    def test_a_number_makes_a_segment_a_shelf_before_anybody_is_asked(
            self, client, library, gateway, reader, provider):
        """`Jack Ryan 1 - Without Remorse` is Tom Clancy's, and Jack Ryan
        is both its protagonist and — Open Library being what it is — an
        author with sixteen works. This one never reaches the works
        check: the trailing number says the segment is a shelf, so the
        question is never asked at all."""
        clear(library)
        pdf(library, "Jack Ryan 1 - Without Remorse")
        gateway.reindex()
        provider.knows("Jack Ryan", "Without Remorse", "Patriot Games")

        reader.run(apply=True)
        assert book(gateway, "Without Remorse")["authors"] is None
        assert ("author", "Jack Ryan") not in provider.asked

    def test_a_segment_nobody_has_heard_of_is_left_alone(
            self, client, library, gateway, reader, provider):
        clear(library)
        pdf(library, "Ancient Warfare - A Very Short Introduction")
        gateway.reindex()

        report = reader.run(apply=True)
        assert report["credited"] == 0
        assert book(gateway, "Ancient Warfare")["authors"] is None

    def test_two_readings_that_both_account_are_refused(
            self, client, library, gateway, reader, provider):
        """Two confirmed readings of one name is not two answers, it is
        no answer. Guessing between them is exactly what §12.1's review
        queue would be for, and picking one silently is worse than
        both."""
        clear(library)
        pdf(library, "Alice North - Bella South")
        gateway.reindex()
        provider.knows("Alice North", "Bella South")
        provider.knows("Bella South", "Alice North")

        report = reader.run(apply=True)
        assert report["credited"] == 0
        assert book(gateway, "Alice North")["authors"] is None

    def test_the_confirmed_spelling_is_the_one_kept(
            self, client, library, gateway, reader, provider):
        """Open Library holds `Kimon Nicolaïdes` for a file that says
        `Kimon Nicolaides`. The catalogue is what asserts the name, so
        the catalogue's spelling is what the library shows."""
        clear(library)
        pdf(library, "Kimon Nicolaides - The Natural Way to Draw")
        gateway.reindex()
        provider.knows("Kimon Nicolaïdes", "The Natural Way to Draw")
        provider.authors["kimon nicolaides"] = provider.authors[
            "kimon nicolaïdes"]

        reader.run(apply=True)
        assert book(gateway, "Natural Way")["authors"] == "Kimon Nicolaïdes"


class TestAskingOnce:
    """§6.6's rule, and §8.13c's table. A restart must not become a
    thousand requests."""

    def test_the_second_run_asks_nothing(self, client, library, gateway,
                                         reader, provider):
        clear(library)
        pdf(library, "Andrew Loomis - Successful Drawing")
        gateway.reindex()
        provider.knows("Andrew Loomis", "Successful Drawing")

        reader.run(apply=True)
        provider.asked.clear()
        reader.run(apply=True)
        assert provider.asked == []

    def test_a_silence_is_an_answer_and_is_kept(self, client, library,
                                                gateway, reader, provider):
        """A name nobody has heard of is the commonest answer there is.
        Not writing it down means asking again for ever."""
        clear(library)
        pdf(library, "Ancient Warfare - A Very Short Introduction")
        gateway.reindex()

        reader.run(apply=True)
        asked = len(provider.asked)
        assert asked, "the first run has to ask"
        provider.asked.clear()
        reader.run(apply=True)
        assert provider.asked == []

    def test_the_questions_are_kept_where_the_other_pass_keeps_them(
            self, client, library, gateway, reader, provider):
        clear(library)
        pdf(library, "Andrew Loomis - Successful Drawing")
        gateway.reindex()
        provider.knows("Andrew Loomis", "Successful Drawing")
        reader.run(apply=True)

        rows = gateway.database.connection.execute(
            "SELECT question, answer FROM provider_query"
            " WHERE source = 'openlibrary'").fetchall()
        assert rows, "provider_query is where an asked string lives (§8.13c)"


class TestWhatItWillNotTouch:
    """The gate. Most books never pass it, and that is the point."""

    def test_a_name_the_code_can_already_read_is_not_its_business(
            self, client, library, gateway, reader, provider):
        """`Anthony, Piers - Xanth 08 - Crewel Lye` is §8.13a's, and it
        needs no provider at all. Two passes reading one name is how
        they come to disagree (§8.13a)."""
        clear(library)
        pdf(library, "Anthony, Piers - Xanth 08 - Crewel Lye")
        gateway.reindex()

        report = reader.run(apply=True)
        assert report["credited"] == 0
        assert provider.asked == []

    def test_a_file_that_named_itself_is_left_alone(
            self, client, library, gateway, reader, provider):
        """The file's own metadata gives a real title and a real author,
        so there is nothing here anybody needs to ask about — and asking
        would spend a request confirming what we already hold."""
        clear(library)
        pdf(library, "Neil Gaiman - Stardust", author="Neil Gaiman",
            title="Stardust")
        gateway.reindex()

        report = reader.run(apply=True)
        assert report["credited"] == 0
        assert provider.asked == []

    def test_a_reader_who_has_said_who_wrote_it_is_final(
            self, client, library, gateway, reader, provider):
        """§8.10: what a person says is `origin = 'user'`, locked, and no
        pass overrules it — not even one holding a catalogue's word."""
        clear(library)
        pdf(library, "Andrew Loomis - Successful Drawing")
        gateway.reindex()
        provider.knows("Andrew Loomis", "Successful Drawing")
        row = book(gateway, "Successful Drawing")
        gateway.database.connection.execute(
            "INSERT INTO field_provenance (book_id, field, origin, source,"
            " locked, updated_at) VALUES (?, 'authors', 'user', NULL, 1, 0)",
            (row["id"],))

        reader.run(apply=True)
        assert book(gateway, "Successful Drawing")["authors"] is None

    def test_a_dry_run_writes_nothing(self, client, library, gateway,
                                      reader, provider):
        clear(library)
        pdf(library, "Andrew Loomis - Successful Drawing")
        gateway.reindex()
        provider.knows("Andrew Loomis", "Successful Drawing")

        report = reader.run()
        assert report["would_credit"] == 1
        assert book(gateway, "Successful Drawing")["authors"] is None


class TestWritingItDown:
    def test_the_name_the_file_gave_is_kept(self, client, library, gateway,
                                            reader, provider):
        """Every rename is reversible, which is how twelve bad ones were
        undone rather than argued about (§6.3)."""
        clear(library)
        pdf(library, "Andrew Loomis - Successful Drawing")
        gateway.reindex()
        provider.knows("Andrew Loomis", "Successful Drawing")

        reader.run(apply=True)
        row = book(gateway, "Successful Drawing")
        assert row["file_title"] == "Andrew Loomis - Successful Drawing"

    def test_a_catalogue_is_recorded_as_the_source(self, client, library,
                                                   gateway, reader, provider):
        """Not `filename`: this is what Open Library asserted, and §6.4's
        ranking turns on the difference — a confirmed value outranks an
        inferred one, so the next scan cannot put the run-on name back."""
        clear(library)
        pdf(library, "Andrew Loomis - Successful Drawing")
        gateway.reindex()
        provider.knows("Andrew Loomis", "Successful Drawing")
        reader.run(apply=True)

        rows = dict(gateway.database.connection.execute(
            "SELECT field, source FROM field_provenance WHERE book_id = ?"
            " AND origin = 'provider'",
            (book(gateway, "Successful Drawing")["id"],)).fetchall())
        assert rows.get("authors") == "openlibrary"
        assert rows.get("title") == "openlibrary"

    def test_two_runs_in_a_row_settle(self, client, library, gateway,
                                      reader, provider):
        """§8.16's rule. A pass that takes a turn with another one, or
        with itself, never stops changing the library."""
        clear(library)
        pdf(library, "Andrew Loomis - Successful Drawing")
        gateway.reindex()
        provider.knows("Andrew Loomis", "Successful Drawing")

        reader.run(apply=True)
        again = reader.run(apply=True)
        assert again["credited"] == 0
        assert again["retitled"] == 0

    def test_the_series_is_judged_where_it_is_already_judged(
            self, client, library, gateway, reader, provider):
        """§8.13a's rule, unchanged: `SeriesFromNames` decides, and its
        agreement rule is what keeps a misread off the shelves. Two
        implementations of one judgement is how they disagree."""
        clear(library)
        for n, title in ((1, "First Man in Rome"), (3, "Fortunes Favourites")):
            pdf(library, f"Colleen McCullough - Masters of Rome {n} - {title}")
        gateway.reindex()
        provider.knows("Colleen McCullough", "The First Man in Rome",
                       "Fortune's Favourites")

        reader.run(apply=True)
        row = book(gateway, "First Man in Rome")
        assert row["authors"] == "Colleen McCullough"
        assert row["series"] == "Masters of Rome"
        assert row["series_index"] == 1
        assert row["title"] == "First Man in Rome"


class TestTheSummary:
    def test_it_says_when_there_was_nothing_to_read(self):
        assert "nothing" in NameHypotheses.summary(
            {"credited": 0, "would_credit": 0, "filed": 0, "retitled": 0,
             "anchored": 0, "asked": 0}).lower()

    def test_a_dry_run_says_what_it_would_do(self):
        line = NameHypotheses.summary(
            {"credited": 0, "would_credit": 3, "filed": 0, "retitled": 0,
             "anchored": 3, "asked": 9})
        assert "3" in line

    def test_it_reports_every_step_it_took(self):
        """A run that credits nobody because everybody is credited may
        still file and retitle. Reporting only the first step is how a
        pass that worked comes to look as though it did not (§8.13a)."""
        line = NameHypotheses.summary(
            {"credited": 2, "would_credit": 2, "filed": 5, "retitled": 2,
             "anchored": 2, "asked": 6})
        assert "5" in line and "2" in line
