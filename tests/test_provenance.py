"""Who said so, written down in one place.

§5's provenance rule is the invariant the whole design rests on — a
field records where its value came from, and enrichment may overwrite it
only if the file still owns it and nobody has locked it. Eleven modules
each hand-rolled their own `INSERT INTO field_provenance … ON CONFLICT`
to say so, and eight each carried their own copy of the guard that reads
it back.

That is not a tidiness complaint. **This afternoon a rule was added to
three of those eight by hand** — a title a provider confirmed outranks
one read off a filename — and a rule that must be copied into N places
will eventually be in N−1. `Provenance` is the one place, and
`Candidates` is the one guard.
"""
from __future__ import annotations

import pytest

from epub_provenance import Candidates, Provenance
from tests.epub_fixtures import EpubBuilder


@pytest.fixture
def written(gateway, library):
    for old in library.glob("*.epub"):
        old.unlink()
    EpubBuilder(title="A Book", author="Ann Author").build(library / "a.epub")
    gateway.reindex()
    book_id = gateway.database.connection.execute(
        "SELECT book_id FROM metadata WHERE title = 'A Book'").fetchone()[0]
    return gateway.database, book_id


def stored(database, book_id, field="title"):
    return database.connection.execute(
        "SELECT origin, source, locked FROM field_provenance"
        " WHERE book_id = ? AND field = ?", (book_id, field)).fetchone()


class TestSayingWhoSaidSo:
    def test_it_records_a_field(self, written):
        database, book_id = written
        Provenance(database).record(book_id, "title", "provider", "filename")
        row = stored(database, book_id)
        assert (row["origin"], row["source"], row["locked"]) == \
            ("provider", "filename", 0)

    def test_saying_it_again_updates_rather_than_duplicates(self, written):
        database, book_id = written
        prov = Provenance(database)
        prov.record(book_id, "title", "provider", "filename")
        prov.record(book_id, "title", "provider", "openlibrary")
        rows = database.connection.execute(
            "SELECT source FROM field_provenance WHERE book_id = ?"
            " AND field = 'title'", (book_id,)).fetchall()
        assert [r["source"] for r in rows] == ["openlibrary"]

    def test_a_reader_can_lock_it(self, written):
        database, book_id = written
        Provenance(database).record(book_id, "authors", "user", "hand",
                                    locked=True)
        assert stored(database, book_id, "authors")["locked"] == 1

    def test_it_stamps_the_time(self, written):
        database, book_id = written
        Provenance(database).record(book_id, "title", "provider", "filename")
        assert database.connection.execute(
            "SELECT updated_at FROM field_provenance WHERE book_id = ?",
            (book_id,)).fetchone()[0] > 0

    def test_several_fields_at_once(self, written):
        database, book_id = written
        Provenance(database).record_all(book_id, ("title", "authors"),
                                        "provider", "filename")
        assert stored(database, book_id, "title")["source"] == "filename"
        assert stored(database, book_id, "authors")["source"] == "filename"


class TestTheGuard:
    """What every pass asks before it writes: may I touch this?"""

    def test_a_field_nobody_has_claimed_is_free(self, written):
        database, book_id = written
        assert Candidates(database).may_write(book_id, "title")

    def test_a_reader_beats_everything(self, written):
        database, book_id = written
        Provenance(database).record(book_id, "title", "user", "hand",
                                    locked=True)
        assert not Candidates(database).may_write(book_id, "title")

    def test_a_locked_field_is_locked_whoever_locked_it(self, written):
        database, book_id = written
        Provenance(database).record(book_id, "title", "provider", "filename",
                                    locked=True)
        assert not Candidates(database).may_write(book_id, "title")

    def test_a_confirmed_title_outranks_a_derived_one(self, written):
        """The rule that had to be copied into three passes by hand: a
        title Open Library asserted is not undone by one inferred from a
        filename."""
        database, book_id = written
        Provenance(database).record(book_id, "title", "provider", "openlibrary")
        assert not Candidates(database).may_write(book_id, "title",
                                                  derived_from="filename")
        # ...but the provider that confirmed it may say so again.
        assert Candidates(database).may_write(book_id, "title",
                                              derived_from="openlibrary")

    def test_a_derived_title_may_be_derived_again(self, written):
        database, book_id = written
        Provenance(database).record(book_id, "title", "provider", "filename")
        assert Candidates(database).may_write(book_id, "title",
                                              derived_from="filename")

    def test_the_guard_reads_as_sql_too(self, written):
        """Passes select their candidates in one query rather than
        asking per book, so the same rule has to exist as a WHERE
        clause — and it must agree with the method beside it."""
        database, book_id = written
        Provenance(database).record(book_id, "title", "provider", "openlibrary")
        rows = database.connection.execute(
            "SELECT b.id FROM book b WHERE 1 = 1 "
            + Candidates.clause("title", derived_from="filename")).fetchall()
        assert book_id not in [r["id"] for r in rows]

    def test_the_two_forms_agree(self, written):
        """The method and the clause are the same rule twice, which is
        exactly how they come to disagree. Asserted, not assumed."""
        database, book_id = written
        guard = Candidates(database)
        for origin, source, locked in (
            ("provider", "filename", 0), ("provider", "openlibrary", 0),
            ("user", "hand", 1), ("file", None, 0),
        ):
            database.connection.execute(
                "DELETE FROM field_provenance WHERE book_id = ?", (book_id,))
            Provenance(database).record(book_id, "title", origin, source,
                                        locked=bool(locked))
            by_method = guard.may_write(book_id, "title",
                                        derived_from="filename")
            by_sql = book_id in [r["id"] for r in database.connection.execute(
                "SELECT b.id FROM book b WHERE 1 = 1 "
                + Candidates.clause("title", derived_from="filename"))]
            assert by_method is by_sql, f"{origin}/{source}/locked={locked}"
