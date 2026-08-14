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

    def test_fonts_allow_blob_and_data(self, directives):
        assert "blob:" in directives["font-src"]
        assert "data:" in directives["font-src"]


class TestClickjackingAndInjection:
    def test_framing_by_others_is_refused(self, directives):
        assert directives["frame-ancestors"] == ["'none'"]

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
