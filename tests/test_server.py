"""M0 acceptance: the service starts, serves the reader shell and the
engine, exposes the library read-only, and puts CSP on everything."""
from __future__ import annotations

from epub_security import ContentSecurityPolicy


class TestHealth:
    def test_healthz_reports_ok(self, client):
        r = client.get("/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["service"] == "epub-reader"

    def test_healthz_reports_the_library_root(self, client, library):
        assert client.get("/healthz").json()["library_root"] == str(library)


class TestReaderShell:
    def test_root_serves_the_app_shell(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert 'id="reader-stage"' in r.text

    def test_shell_boots_the_app_as_an_es_module(self, client):
        # ReaderEngine creates <foliate-view> at runtime, so the shell's
        # contract is the module entry point, not the element.
        assert 'type="module" src="/static/app.js"' in client.get("/").text

    def test_app_assets_are_served(self, client):
        assert client.get("/static/app.css").status_code == 200
        assert client.get("/static/app.js").status_code == 200

    def test_reader_module_is_served(self, client):
        r = client.get("/static/reader.js")
        assert r.status_code == 200
        assert "class" in r.text

    def test_unknown_asset_is_a_404(self, client):
        assert client.get("/static/nope.js").status_code == 404


class TestLibraryEndpoint:
    def test_books_are_listed(self, client):
        names = {b["name"] for b in client.get("/api/books").json()["books"]}
        assert "test-book.epub" in names

    def test_a_book_can_be_fetched_by_name(self, client):
        r = client.get("/api/books/test-book.epub")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/epub+zip"
        assert r.content[:2] == b"PK"

    def test_missing_book_is_a_404(self, client):
        assert client.get("/api/books/absent.epub").status_code == 404

    def test_path_traversal_is_refused(self, client):
        for attempt in ("../.env", "..%2f.env", "/etc/passwd"):
            r = client.get(f"/api/books/{attempt}")
            assert r.status_code in (400, 404), attempt

    def test_library_is_read_only(self, client):
        assert client.delete("/api/books/test-book.epub").status_code == 405
        assert client.post("/api/books/test-book.epub").status_code == 405


class TestSecurityHeaders:
    def test_csp_is_present_on_the_shell(self, client):
        header = client.get("/").headers["content-security-policy"]
        assert header == ContentSecurityPolicy().header_value()

    def test_csp_is_present_on_assets(self, client):
        assert "content-security-policy" in client.get("/static/app.js").headers

    def test_csp_is_present_on_api_responses(self, client):
        assert "content-security-policy" in client.get("/api/books").headers

    def test_csp_is_present_on_errors(self, client):
        assert "content-security-policy" in client.get("/nope").headers

    def test_hardening_headers_are_set(self, client):
        h = client.get("/").headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["referrer-policy"] == "no-referrer"
