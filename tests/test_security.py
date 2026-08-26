"""foliate-js cannot contain EPUB scripts by itself — its README says so
outright. CSP is the containment, so it is tested directly rather than
assumed."""
from __future__ import annotations

import pytest

from epub_security import ContentSecurityPolicy


@pytest.fixture
def directives():
    return ContentSecurityPolicy().directives()


class TestScriptContainment:
    def test_scripts_are_restricted_to_self(self, directives):
        assert directives["script-src"] == ["'self'"]

    def test_script_src_never_allows_unsafe_inline(self, directives):
        assert "'unsafe-inline'" not in directives["script-src"]

    def test_script_src_never_allows_unsafe_eval(self, directives):
        assert "'unsafe-eval'" not in directives["script-src"]

    def test_script_src_never_allows_blob(self, directives):
        # Book resources are served as blob: URLs. Allowing blob: here
        # would hand every EPUB in the library arbitrary code execution.
        assert "blob:" not in directives["script-src"]

    def test_plugins_are_blocked(self, directives):
        assert directives["object-src"] == ["'none'"]


class TestBookRenderingStillWorks:
    """The policy has to be survivable: book content genuinely needs
    blob: frames, inline styles, and data: images."""

    def test_frames_allow_blob_for_book_sections(self, directives):
        assert "blob:" in directives["frame-src"]

    def test_images_allow_blob_and_data(self, directives):
        assert "blob:" in directives["img-src"]
        assert "data:" in directives["img-src"]

    def test_inline_styles_are_permitted(self, directives):
        # EPUB CSS arrives inline; styles cannot execute script.
        assert "'unsafe-inline'" in directives["style-src"]

    def test_blob_stylesheets_are_permitted(self, directives):
        # Real books link their stylesheet, and foliate serves every book
        # resource as a blob: URL — so blocking blob: here silently
        # strips a book's typography. Stylesheets cannot execute script,
        # which is why this is safe while script-src stays locked down.
        assert "blob:" in directives["style-src"]

    def test_fonts_allow_blob_and_data(self, directives):
        assert "blob:" in directives["font-src"]
        assert "data:" in directives["font-src"]


class TestClickjackingAndInjection:
    def test_framing_by_others_is_refused(self, directives):
        # 'self', NOT 'none'. WebKit propagates this policy into blob:
        # documents, and foliate renders every book section in a blob:
        # iframe — so 'none' makes the book refuse to load in Safari,
        # which is the browser this is mostly read in. 'self' still
        # refuses framing by any other origin.
        assert directives["frame-ancestors"] == ["'self'"]

    def test_the_page_still_refuses_cross_origin_framing(self):
        from epub_security import SecurityHeaders

        # X-Frame-Options carries the same protection and is NOT
        # inherited by blob: documents, so it costs the reader nothing.
        assert SecurityHeaders().values()["X-Frame-Options"] == "DENY"

    def test_base_uri_is_pinned(self, directives):
        assert directives["base-uri"] == ["'self'"]

    def test_form_submission_is_refused(self, directives):
        assert directives["form-action"] == ["'none'"]

    def test_default_src_is_self(self, directives):
        assert directives["default-src"] == ["'self'"]


class TestHeaderRendering:
    def test_header_value_is_semicolon_separated(self):
        value = ContentSecurityPolicy().header_value()
        assert "; " in value
        assert value.startswith("default-src 'self'")

    def test_every_directive_appears_in_the_header(self):
        policy = ContentSecurityPolicy()
        value = policy.header_value()
        for name in policy.directives():
            assert name in value

    def test_header_name_is_enforcing_not_report_only(self):
        assert ContentSecurityPolicy().header_name == "Content-Security-Policy"


class TestCrossSiteRequests:
    """The service has no authentication and binds a port on a machine
    somebody else's browser can also reach. Nothing here uses cookies, so
    a cross-site POST cannot be authenticated — but it does not need to
    be: `POST /api/books/forget-missing?apply=true&force=true` acts on
    whoever can reach the port. A bodiless POST is a "simple request", so
    it is sent without a preflight and CORS never gets a say; only the
    *response* is hidden from the caller. Refusing a foreign `Origin` is
    what stops the request being acted on in the first place.
    """

    def test_a_foreign_origin_cannot_post(self, client):
        r = client.post("/api/shelves", json={"name": "Stolen"},
                        headers={"Origin": "https://evil.example"})
        assert r.status_code == 403

    def test_a_foreign_origin_cannot_put(self, client):
        r = client.put("/api/books/1/status", json={"status": "finished"},
                       headers={"Origin": "https://evil.example"})
        assert r.status_code == 403

    def test_a_foreign_origin_cannot_delete(self, client):
        r = client.delete("/api/shelves/1",
                          headers={"Origin": "https://evil.example"})
        assert r.status_code == 403

    def test_our_own_origin_is_allowed(self, client):
        r = client.post("/api/shelves", json={"name": "Mine"},
                        headers={"Origin": "http://testserver"})
        assert r.status_code < 400

    def test_a_request_with_no_origin_is_allowed(self, client):
        # curl, `run.sh`, and this suite send no Origin at all. Refusing
        # those would break every non-browser caller to defend against a
        # browser, which is the only thing that can be made to forge one.
        r = client.post("/api/shelves", json={"name": "From The Shell"})
        assert r.status_code < 400

    def test_reading_is_never_refused(self, client):
        # A GET changes nothing, and the same-origin policy already stops
        # a foreign page reading the answer.
        r = client.get("/api/status", headers={"Origin": "https://evil.example"})
        assert r.status_code == 200
