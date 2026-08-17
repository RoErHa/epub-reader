"""Shelves — the reader's own groupings.

Two kinds, deliberately:

  stored — you make them ("Holiday", "Re-read"), and put books on them.
  smart  — derived from reading state ("Reading now", "Finished"). They
           cannot be edited, because they are a view of a fact rather
           than a list someone maintains.
"""
from __future__ import annotations

import pytest

from tests.epub_fixtures import EpubBuilder

CFI = "epubcfi(/6/4[chap01]!/4/2/2[p1]:0)"


@pytest.fixture
def stocked(client, library, gateway):
    for existing in library.glob("*.epub"):
        existing.unlink()
    for title in ("Dune", "Neuromancer", "Emma"):
        EpubBuilder(title=title, author=f"{title} Author").build(
            library / f"{title.lower()}.epub"
        )
    gateway.reindex()
    return client


def book_id(client, title):
    return next(b["id"] for b in client.get("/api/books").json()["books"]
                if b["title"] == title)


def shelf_named(client, name):
    return next(s for s in client.get("/api/shelves").json()["shelves"]
                if s["name"] == name)


class TestCreating:
    def test_a_shelf_can_be_created(self, stocked):
        r = stocked.post("/api/shelves", json={"name": "Holiday"})
        assert r.status_code == 200
        assert r.json()["shelf"]["name"] == "Holiday"

    def test_a_new_shelf_is_empty(self, stocked):
        stocked.post("/api/shelves", json={"name": "Holiday"})
        assert shelf_named(stocked, "Holiday")["books"] == 0

    def test_a_blank_name_is_refused(self, stocked):
        for name in ("", "   "):
            assert stocked.post("/api/shelves", json={"name": name}).status_code == 422

    def test_names_are_trimmed(self, stocked):
        stocked.post("/api/shelves", json={"name": "  Holiday  "})
        assert shelf_named(stocked, "Holiday")

    def test_the_same_name_twice_is_refused(self, stocked):
        stocked.post("/api/shelves", json={"name": "Holiday"})
        assert stocked.post(
            "/api/shelves", json={"name": "holiday"}
        ).status_code == 409

    def test_a_shelf_can_be_renamed(self, stocked):
        shelf = stocked.post("/api/shelves", json={"name": "Holiday"}).json()["shelf"]
        r = stocked.put(f"/api/shelves/{shelf['id']}", json={"name": "Summer"})
        assert r.status_code == 200
        assert shelf_named(stocked, "Summer")

    def test_a_shelf_can_be_deleted(self, stocked):
        shelf = stocked.post("/api/shelves", json={"name": "Holiday"}).json()["shelf"]
        assert stocked.delete(f"/api/shelves/{shelf['id']}").status_code == 200
        names = [s["name"] for s in stocked.get("/api/shelves").json()["shelves"]]
        assert "Holiday" not in names

    def test_deleting_a_shelf_leaves_the_books_alone(self, stocked):
        shelf = stocked.post("/api/shelves", json={"name": "Holiday"}).json()["shelf"]
        stocked.put(f"/api/shelves/{shelf['id']}/books/{book_id(stocked, 'Dune')}")
        stocked.delete(f"/api/shelves/{shelf['id']}")
        assert len(stocked.get("/api/books").json()["books"]) == 3


class TestPuttingBooksOn:
    def test_a_book_can_be_added(self, stocked):
        shelf = stocked.post("/api/shelves", json={"name": "Holiday"}).json()["shelf"]
        dune = book_id(stocked, "Dune")
        assert stocked.put(f"/api/shelves/{shelf['id']}/books/{dune}").status_code == 200
        books = stocked.get(f"/api/shelves/{shelf['id']}/books").json()["books"]
        assert [b["title"] for b in books] == ["Dune"]

    def test_adding_twice_is_harmless(self, stocked):
        shelf = stocked.post("/api/shelves", json={"name": "Holiday"}).json()["shelf"]
        dune = book_id(stocked, "Dune")
        stocked.put(f"/api/shelves/{shelf['id']}/books/{dune}")
        stocked.put(f"/api/shelves/{shelf['id']}/books/{dune}")
        assert shelf_named(stocked, "Holiday")["books"] == 1

    def test_a_book_can_be_taken_off(self, stocked):
        shelf = stocked.post("/api/shelves", json={"name": "Holiday"}).json()["shelf"]
        dune = book_id(stocked, "Dune")
        stocked.put(f"/api/shelves/{shelf['id']}/books/{dune}")
        assert stocked.delete(
            f"/api/shelves/{shelf['id']}/books/{dune}"
        ).status_code == 200
        assert shelf_named(stocked, "Holiday")["books"] == 0

    def test_a_book_can_sit_on_several_shelves(self, stocked):
        dune = book_id(stocked, "Dune")
        for name in ("Holiday", "Re-read"):
            shelf = stocked.post("/api/shelves", json={"name": name}).json()["shelf"]
            stocked.put(f"/api/shelves/{shelf['id']}/books/{dune}")
        shelves = stocked.get(f"/api/books/{dune}/shelves").json()["shelves"]
        assert {s["name"] for s in shelves} == {"Holiday", "Re-read"}

    def test_an_unknown_shelf_is_a_404(self, stocked):
        assert stocked.put(
            f"/api/shelves/999/books/{book_id(stocked, 'Dune')}"
        ).status_code == 404

    def test_an_unknown_book_is_a_404(self, stocked):
        shelf = stocked.post("/api/shelves", json={"name": "Holiday"}).json()["shelf"]
        assert stocked.put(
            f"/api/shelves/{shelf['id']}/books/999999"
        ).status_code == 404

    def test_a_missing_book_drops_out_of_the_shelf_listing(
        self, stocked, library, gateway
    ):
        shelf = stocked.post("/api/shelves", json={"name": "Holiday"}).json()["shelf"]
        stocked.put(f"/api/shelves/{shelf['id']}/books/{book_id(stocked, 'Dune')}")
        (library / "dune.epub").unlink()
        gateway.reindex()
        books = stocked.get(f"/api/shelves/{shelf['id']}/books").json()["books"]
        assert books == []


class TestSmartShelves:
    def test_reading_now_follows_reading_state(self, stocked):
        dune = book_id(stocked, "Dune")
        assert stocked.get("/api/shelves/smart/reading/books").json()["books"] == []
        stocked.put(f"/api/books/{dune}/position",
                    json={"cfi": CFI, "fraction": 0.3})
        titles = [b["title"] for b in
                  stocked.get("/api/shelves/smart/reading/books").json()["books"]]
        assert titles == ["Dune"]

    def test_finished_follows_status(self, stocked):
        dune = book_id(stocked, "Dune")
        stocked.put(f"/api/books/{dune}/position", json={"cfi": CFI, "fraction": 1.0})
        stocked.put(f"/api/books/{dune}/status", json={"status": "finished"})
        titles = [b["title"] for b in
                  stocked.get("/api/shelves/smart/finished/books").json()["books"]]
        assert titles == ["Dune"]
        # And it is no longer "reading now".
        assert stocked.get("/api/shelves/smart/reading/books").json()["books"] == []

    def test_smart_shelves_are_listed_with_counts(self, stocked):
        stocked.put(f"/api/books/{book_id(stocked, 'Dune')}/position",
                    json={"cfi": CFI, "fraction": 0.3})
        smart = {s["key"]: s for s in stocked.get("/api/shelves").json()["shelves"]
                 if s.get("key")}
        assert smart["reading"]["books"] == 1
        assert smart["reading"]["smart"] is True

    def test_a_smart_shelf_cannot_be_edited(self, stocked):
        dune = book_id(stocked, "Dune")
        assert stocked.put(f"/api/shelves/smart/reading/books/{dune}").status_code in (
            404, 405
        )

    def test_an_unknown_smart_shelf_is_a_404(self, stocked):
        assert stocked.get("/api/shelves/smart/nonsense/books").status_code == 404


class TestSorting:
    def test_books_can_be_sorted_by_when_they_were_last_opened(self, stocked):
        emma, dune = book_id(stocked, "Emma"), book_id(stocked, "Dune")
        stocked.put(f"/api/books/{emma}/position", json={"cfi": CFI, "fraction": 0.1})
        stocked.put(f"/api/books/{dune}/position", json={"cfi": CFI, "fraction": 0.1})
        titles = [b["title"] for b in
                  stocked.get("/api/books?sort=opened").json()["books"]]
        # Most recently opened first; untouched books last.
        assert titles[:2] == ["Dune", "Emma"]

    def test_books_can_be_sorted_by_progress(self, stocked):
        stocked.put(f"/api/books/{book_id(stocked, 'Emma')}/position",
                    json={"cfi": CFI, "fraction": 0.9})
        stocked.put(f"/api/books/{book_id(stocked, 'Dune')}/position",
                    json={"cfi": CFI, "fraction": 0.2})
        books = stocked.get("/api/books?sort=progress").json()["books"]
        assert books[0]["title"] == "Emma"
        assert books[0]["percent"] == 90

    def test_the_sort_list_is_offered(self, stocked):
        sorts = stocked.get("/api/sorts").json()["sorts"]
        keys = {s["key"] for s in sorts}
        assert {"title", "author", "series", "year", "added", "opened",
                "progress"} <= keys
        assert all("label" in s for s in sorts)


def smart_titles(client, key):
    return [b["title"] for b in
            client.get(f"/api/shelves/smart/{key}/books").json()["books"]]


class TestMarkingABookRead:
    """Marking a book read is a change to a fact, and the smart shelves
    follow it: it leaves "Reading now" and appears under "Finished".

    That is also the only way *off* a smart shelf. There is still no
    route that puts a book on one — membership is a consequence, so you
    change the cause.
    """

    def test_marking_it_read_moves_it_from_reading_to_finished(self, stocked):
        dune = book_id(stocked, "Dune")
        stocked.put(f"/api/books/{dune}/position",
                    json={"cfi": CFI, "fraction": 0.4})
        assert smart_titles(stocked, "reading") == ["Dune"]

        assert stocked.put(f"/api/books/{dune}/status",
                           json={"status": "finished"}).status_code == 200
        assert smart_titles(stocked, "reading") == []
        assert smart_titles(stocked, "finished") == ["Dune"]

    def test_marking_it_read_keeps_your_place(self, stocked):
        # Finishing a book is not forgetting it: you may want the last
        # page again, and the highlights hang off the same row.
        dune = book_id(stocked, "Dune")
        stocked.put(f"/api/books/{dune}/position",
                    json={"cfi": CFI, "fraction": 0.4})
        stocked.put(f"/api/books/{dune}/status", json={"status": "finished"})

        place = stocked.get(f"/api/books/{dune}/position").json()["position"]
        assert place["cfi"] == CFI
        assert place["fraction"] == pytest.approx(0.4)
        assert place["finished_at"]

    def test_a_book_can_be_marked_read_without_being_opened(self, stocked):
        # You finished it in print, or years ago on something else.
        emma = book_id(stocked, "Emma")
        stocked.put(f"/api/books/{emma}/status", json={"status": "finished"})
        assert smart_titles(stocked, "finished") == ["Emma"]

    def test_taking_it_off_reading_now_keeps_your_place(self, stocked):
        # The − on a smart shelf changes the fact rather than deleting a
        # row: the book leaves the list, and your place survives.
        dune = book_id(stocked, "Dune")
        stocked.put(f"/api/books/{dune}/position",
                    json={"cfi": CFI, "fraction": 0.4})
        stocked.put(f"/api/books/{dune}/status", json={"status": "unread"})

        assert smart_titles(stocked, "reading") == []
        assert smart_titles(stocked, "finished") == []
        place = stocked.get(f"/api/books/{dune}/position").json()["position"]
        assert place["cfi"] == CFI
        assert place["fraction"] == pytest.approx(0.4)

    def test_a_book_can_stop_being_finished(self, stocked):
        dune = book_id(stocked, "Dune")
        stocked.put(f"/api/books/{dune}/status", json={"status": "finished"})
        stocked.put(f"/api/books/{dune}/status", json={"status": "unread"})
        assert smart_titles(stocked, "finished") == []
        assert stocked.get(f"/api/books/{dune}/position"
                           ).json()["position"]["finished_at"] is None

    def test_the_shelf_counts_follow(self, stocked):
        dune = book_id(stocked, "Dune")
        stocked.put(f"/api/books/{dune}/status", json={"status": "finished"})
        counts = {s.get("key"): s["books"]
                  for s in stocked.get("/api/shelves").json()["shelves"]}
        assert counts["finished"] == 1
        assert counts["reading"] == 0

    def test_a_status_nobody_can_render_is_refused(self, stocked):
        dune = book_id(stocked, "Dune")
        assert stocked.put(f"/api/books/{dune}/status",
                           json={"status": "half"}).status_code == 422
