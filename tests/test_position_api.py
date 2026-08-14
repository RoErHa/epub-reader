"""Position endpoints.

The browser knows a book by filename; the server keys position by content
hash, so renaming a file must not lose your place.
"""
from __future__ import annotations

CFI = "epubcfi(/6/4[chap01]!/4/2/2[p1]:0)"


class TestReadingPosition:
    def test_a_new_book_has_no_position(self, client):
        r = client.get("/api/books/test-book.epub/position")
        assert r.status_code == 200
        assert r.json()["position"] is None

    def test_a_position_can_be_saved_and_read_back(self, client):
        put = client.put(
            "/api/books/test-book.epub/position",
            json={"cfi": CFI, "fraction": 0.42},
        )
        assert put.status_code == 200

        position = client.get("/api/books/test-book.epub/position").json()["position"]
        assert position["cfi"] == CFI
        assert position["percent"] == 42
        assert position["status"] == "reading"

    def test_saving_twice_keeps_the_latest(self, client):
        for fraction in (0.1, 0.7):
            client.put(
                "/api/books/test-book.epub/position",
                json={"cfi": CFI, "fraction": fraction},
            )
        position = client.get("/api/books/test-book.epub/position").json()["position"]
        assert position["percent"] == 70

    def test_position_survives_a_rename(self, client, library):
        client.put(
            "/api/books/test-book.epub/position",
            json={"cfi": CFI, "fraction": 0.33},
        )
        (library / "test-book.epub").rename(library / "renamed.epub")
        position = client.get("/api/books/renamed.epub/position").json()["position"]
        assert position["percent"] == 33

    def test_books_keep_their_own_positions(self, client):
        client.put(
            "/api/books/test-book.epub/position",
            json={"cfi": CFI, "fraction": 0.2},
        )
        assert (
            client.get("/api/books/hostile.epub/position").json()["position"] is None
        )


class TestStatus:
    def test_a_book_can_be_marked_finished(self, client):
        client.put(
            "/api/books/test-book.epub/position",
            json={"cfi": CFI, "fraction": 1.0},
        )
        r = client.put(
            "/api/books/test-book.epub/status", json={"status": "finished"}
        )
        assert r.status_code == 200
        assert r.json()["position"]["status"] == "finished"

    def test_an_unknown_status_is_rejected(self, client):
        r = client.put(
            "/api/books/test-book.epub/status", json={"status": "devoured"}
        )
        assert r.status_code == 422


class TestValidation:
    def test_an_unknown_book_is_a_404(self, client):
        assert client.get("/api/books/absent.epub/position").status_code == 404
        assert (
            client.put(
                "/api/books/absent.epub/position", json={"cfi": CFI, "fraction": 0.1}
            ).status_code
            == 404
        )

    def test_traversal_is_refused(self, client):
        assert client.get("/api/books/..%2f.env/position").status_code in (400, 404)

    def test_a_missing_fraction_is_rejected(self, client):
        r = client.put("/api/books/test-book.epub/position", json={"cfi": CFI})
        assert r.status_code == 422

    def test_an_out_of_range_fraction_is_clamped_not_refused(self, client):
        # The reader can legitimately overshoot on the last page.
        r = client.put(
            "/api/books/test-book.epub/position", json={"cfi": CFI, "fraction": 1.4}
        )
        assert r.status_code == 200
        assert r.json()["position"]["percent"] == 100

    def test_a_position_without_a_cfi_is_accepted(self, client):
        r = client.put(
            "/api/books/test-book.epub/position", json={"cfi": None, "fraction": 0.5}
        )
        assert r.status_code == 200


class TestBookListing:
    def test_the_listing_carries_progress(self, client):
        client.put(
            "/api/books/test-book.epub/position",
            json={"cfi": CFI, "fraction": 0.25},
        )
        books = {b["name"]: b for b in client.get("/api/books").json()["books"]}
        assert books["test-book.epub"]["percent"] == 25
        assert books["test-book.epub"]["status"] == "reading"

    def test_unread_books_report_zero_rather_than_null(self, client):
        books = {b["name"]: b for b in client.get("/api/books").json()["books"]}
        assert books["test-book.epub"]["percent"] == 0
        assert books["test-book.epub"]["status"] == "unread"
