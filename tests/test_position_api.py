"""Position endpoints.

The browser knows a book by filename; the server keys position by content
hash, so renaming a file must not lose your place.
"""
from __future__ import annotations

CFI = "epubcfi(/6/4[chap01]!/4/2/2[p1]:0)"


def book_id(client, name="test-book.epub"):
    """Books are addressed by index id — a relative path with slashes
    cannot be a single path segment."""
    return next(b["id"] for b in client.get("/api/books").json()["books"]
                if b["name"] == name)


class TestReadingPosition:
    def test_a_new_book_has_no_position(self, client):
        r = client.get(f"/api/books/{book_id(client)}/position")
        assert r.status_code == 200
        assert r.json()["position"] is None

    def test_a_position_can_be_saved_and_read_back(self, client):
        put = client.put(
            f"/api/books/{book_id(client)}/position",
            json={"cfi": CFI, "fraction": 0.42},
        )
        assert put.status_code == 200

        position = client.get(f"/api/books/{book_id(client)}/position").json()["position"]
        assert position["cfi"] == CFI
        assert position["percent"] == 42
        assert position["status"] == "reading"

    def test_saving_twice_keeps_the_latest(self, client):
        for fraction in (0.1, 0.7):
            client.put(
                f"/api/books/{book_id(client)}/position",
                json={"cfi": CFI, "fraction": fraction},
            )
        position = client.get(f"/api/books/{book_id(client)}/position").json()["position"]
        assert position["percent"] == 70

    def test_position_survives_a_rename(self, client, library, gateway):
        client.put(
            f"/api/books/{book_id(client)}/position",
            json={"cfi": CFI, "fraction": 0.33},
        )
        (library / "test-book.epub").rename(library / "renamed.epub")
        gateway.reindex()
        moved = book_id(client, "renamed.epub")
        position = client.get(f"/api/books/{moved}/position").json()["position"]
        assert position["percent"] == 33

    def test_books_keep_their_own_positions(self, client):
        client.put(
            f"/api/books/{book_id(client)}/position",
            json={"cfi": CFI, "fraction": 0.2},
        )
        assert (
            client.get(f"/api/books/{book_id(client, 'hostile.epub')}/position").json()["position"] is None
        )


class TestStatus:
    def test_a_book_can_be_marked_finished(self, client):
        client.put(
            f"/api/books/{book_id(client)}/position",
            json={"cfi": CFI, "fraction": 1.0},
        )
        r = client.put(
            f"/api/books/{book_id(client)}/status", json={"status": "finished"}
        )
        assert r.status_code == 200
        assert r.json()["position"]["status"] == "finished"

    def test_an_unknown_status_is_rejected(self, client):
        r = client.put(
            f"/api/books/{book_id(client)}/status", json={"status": "devoured"}
        )
        assert r.status_code == 422


class TestValidation:
    def test_an_unknown_book_is_a_404(self, client):
        assert client.get("/api/books/999999/position").status_code == 404
        assert (
            client.put(
                "/api/books/999999/position", json={"cfi": CFI, "fraction": 0.1}
            ).status_code
            == 404
        )

    def test_a_non_numeric_id_is_refused(self, client):
        assert client.get("/api/books/..%2f.env/position").status_code in (400, 404, 422)

    def test_a_missing_fraction_is_rejected(self, client):
        r = client.put(f"/api/books/{book_id(client)}/position", json={"cfi": CFI})
        assert r.status_code == 422

    def test_an_out_of_range_fraction_is_clamped_not_refused(self, client):
        # The reader can legitimately overshoot on the last page.
        r = client.put(
            f"/api/books/{book_id(client)}/position", json={"cfi": CFI, "fraction": 1.4}
        )
        assert r.status_code == 200
        assert r.json()["position"]["percent"] == 100

    def test_a_position_without_a_cfi_is_accepted(self, client):
        r = client.put(
            f"/api/books/{book_id(client)}/position", json={"cfi": None, "fraction": 0.5}
        )
        assert r.status_code == 200


class TestBookListing:
    def test_the_listing_carries_progress(self, client):
        client.put(
            f"/api/books/{book_id(client)}/position",
            json={"cfi": CFI, "fraction": 0.25},
        )
        books = {b["name"]: b for b in client.get("/api/books").json()["books"]}
        assert books["test-book.epub"]["percent"] == 25
        assert books["test-book.epub"]["status"] == "reading"

    def test_unread_books_report_zero_rather_than_null(self, client):
        books = {b["name"]: b for b in client.get("/api/books").json()["books"]}
        assert books["test-book.epub"]["percent"] == 0
        assert books["test-book.epub"]["status"] == "unread"
