"""The README's badges are generated, so they can also be checked.

dlna-gateway's badge numbers are typed by hand, which is fine until
somebody adds a test and the strip quietly stops being true. A number
nobody verifies is worse than no number, because a reader believes it —
so the strip is rendered from the suites themselves and this test fails
when the file has drifted from them. Same rule as `test_lint.py`: a
tidy-up nobody enforces lasts until the next commit.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.badges import BadgeStrip, CountError, Readme, TestCounts  # noqa: E402


class TestTheStripIsGenerated:
    def test_the_readme_carries_the_markers(self):
        # Without them the writer has nowhere to put anything, and would
        # rewrite the whole file or nothing at all.
        assert Readme().current().startswith("<!-- badges:")

    def test_a_count_is_rendered_readably(self):
        strip = BadgeStrip(1190, 428).render()
        # A thousands separator has to survive shields.io's escaping, or
        # the badge reads "1190" where the README says "1,190".
        assert "1%2C190%20unit" in strip
        assert "428%20browser" in strip

    def test_a_hyphen_would_break_a_shields_label(self):
        # `-` is shields.io's own field separator, so it must be doubled
        # rather than percent-encoded. Getting this wrong splits a label
        # into a second colour field and the badge renders wrong.
        assert BadgeStrip._quote("a-b") == "a--b"


class TestTheNumbersAreTrue:
    """The point of the whole exercise."""

    def test_the_unit_count_matches_the_suite(self):
        assert TestCounts().unit() == self._written_unit()

    def test_the_browser_count_matches_the_suite(self):
        try:
            counted = TestCounts().browser()
        except CountError as exc:
            pytest.skip(f"playwright not available here: {exc}")
        assert counted == self._written_browser()

    def test_the_strip_on_disk_is_what_the_suites_render(self):
        counts = TestCounts()
        try:
            browser = counts.browser()
        except CountError as exc:
            pytest.skip(f"playwright not available here: {exc}")
        assert Readme().current() == BadgeStrip(counts.unit(), browser).render()

    # ── reading the numbers back out of the badge ─────────────────────
    @staticmethod
    def _written_unit() -> int:
        import re
        found = re.search(r"tests-([\d%A-C]+?)%20unit", Readme().current())
        assert found, "the tests badge does not name a unit count"
        return int(found.group(1).replace("%2C", ""))

    @staticmethod
    def _written_browser() -> int:
        import re
        found = re.search(r"%C2%B7%20(\d+)%20browser", Readme().current())
        assert found, "the tests badge does not name a browser count"
        return int(found.group(1))
