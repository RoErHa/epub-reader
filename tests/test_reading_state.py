"""Reading position, stored server-side.

Decision 7: one shared position per book across every device, last write
wins. There is no per-device state and no conflict UI, so the store is
deliberately a plain upsert.

Position is keyed by content hash, not filename — renaming a book must
not lose your place. M2 introduces book.id and can add the foreign key.
"""
from __future__ import annotations

import pytest

from epub_db import Database
from epub_reading import ReadingState, ReadingStateStore

HASH = "a" * 64
OTHER = "b" * 64
CFI = "epubcfi(/6/4[chap01]!/4/2/2[p1]:0)"


@pytest.fixture
def store(tmp_path):
    database = Database(tmp_path / "books.db")
    database.migrate()
    yield ReadingStateStore(database)
    database.close()


class TestUnread:
    def test_an_unknown_book_has_no_position(self, store):
        assert store.get(HASH) is None

    def test_listing_positions_for_no_books_is_empty(self, store):
        assert store.get_many([]) == {}


class TestSaving:
    def test_a_position_round_trips(self, store):
        store.save(HASH, cfi=CFI, fraction=0.42)
        state = store.get(HASH)
        assert state.cfi == CFI
        assert state.fraction == pytest.approx(0.42)

    def test_saving_marks_the_book_as_being_read(self, store):
        store.save(HASH, cfi=CFI, fraction=0.1)
        assert store.get(HASH).status == "reading"

    def test_saving_again_overwrites_rather_than_duplicating(self, store):
        store.save(HASH, cfi=CFI, fraction=0.1)
        store.save(HASH, cfi=CFI, fraction=0.9)
        assert store.get(HASH).fraction == pytest.approx(0.9)
        assert len(store.get_many([HASH])) == 1

    def test_opened_at_is_recorded(self, store):
        store.save(HASH, cfi=CFI, fraction=0.1)
        assert store.get(HASH).opened_at > 0

    def test_books_are_kept_apart(self, store):
        store.save(HASH, cfi=CFI, fraction=0.2)
        store.save(OTHER, cfi=CFI, fraction=0.8)
        assert store.get(HASH).fraction == pytest.approx(0.2)
        assert store.get(OTHER).fraction == pytest.approx(0.8)

    def test_a_position_with_no_cfi_is_still_useful(self, store):
        # Fixed-layout formats and PDFs have no CFI; fraction alone is
        # enough to reopen at roughly the right place.
        store.save(HASH, cfi=None, fraction=0.5)
        assert store.get(HASH).cfi is None
        assert store.get(HASH).fraction == pytest.approx(0.5)


class TestValidation:
    @pytest.mark.parametrize("fraction", [-0.1, 1.5, 2, -3])
    def test_fraction_is_clamped_to_the_unit_interval(self, store, fraction):
        store.save(HASH, cfi=CFI, fraction=fraction)
        assert 0.0 <= store.get(HASH).fraction <= 1.0

    def test_a_non_numeric_fraction_is_refused(self, store):
        with pytest.raises(ValueError):
            store.save(HASH, cfi=CFI, fraction="halfway")

    def test_an_empty_hash_is_refused(self, store):
        with pytest.raises(ValueError):
            store.save("", cfi=CFI, fraction=0.1)


class TestFinishing:
    def test_a_book_can_be_marked_finished(self, store):
        store.save(HASH, cfi=CFI, fraction=1.0)
        store.set_status(HASH, "finished")
        state = store.get(HASH)
        assert state.status == "finished"
        assert state.finished_at > 0

    def test_an_unknown_status_is_refused(self, store):
        store.save(HASH, cfi=CFI, fraction=0.1)
        with pytest.raises(ValueError):
            store.set_status(HASH, "devoured")

    def test_reopening_a_finished_book_does_not_silently_unfinish_it(self, store):
        store.save(HASH, cfi=CFI, fraction=1.0)
        store.set_status(HASH, "finished")
        store.save(HASH, cfi=CFI, fraction=0.05)
        assert store.get(HASH).status == "finished"


class TestBulkRead:
    def test_positions_come_back_keyed_by_hash(self, store):
        store.save(HASH, cfi=CFI, fraction=0.3)
        store.save(OTHER, cfi=CFI, fraction=0.6)
        found = store.get_many([HASH, OTHER, "c" * 64])
        assert set(found) == {HASH, OTHER}
        assert found[HASH].fraction == pytest.approx(0.3)


class TestValueObject:
    def test_percent_is_a_rounded_whole_number(self):
        assert ReadingState(HASH, CFI, 0.474, "reading", 0, None).percent == 47

    def test_percent_of_an_unstarted_book_is_zero(self):
        assert ReadingState(HASH, None, 0.0, "unread", 0, None).percent == 0

    def test_state_serialises_for_the_api(self):
        payload = ReadingState(HASH, CFI, 0.5, "reading", 1, None).as_dict()
        assert payload["cfi"] == CFI
        assert payload["percent"] == 50
