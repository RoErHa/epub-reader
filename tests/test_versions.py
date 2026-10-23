"""The README's version strip tells the truth.

`tools/versions.py` renders it from the software itself, never typed. This
gates the PINNED half — read from files every clone shares: the two locks,
package-lock.json, and the foliate-js submodule (its commit, and the
libraries its own package.json says it bundles). The PLATFORM half
(python, sqlite, openssl, pdfium, node, libarchive) belongs to the machine;
gating it here would turn any clone with another Python red, so
tools/upgrade.py watches it instead (CLAUDE.md §10.18).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.versions import Readme, Stack, VersionStrip  # noqa: E402


class TestThePinnedHalfIsTrue:
    def test_the_readme_shows_the_pinned_versions(self):
        expected = VersionStrip(Stack.pinned()).pinned_badges()
        assert expected, "no pinned badges rendered at all"
        stale = [b for b in expected if b not in Readme().current()]
        assert stale == [], (
            "README version strip is stale — run: "
            ".venv/bin/python tools/versions.py --write"
        )

    def test_every_pinned_version_was_found(self):
        assert [k for k, v in Stack.pinned().items() if not v] == []


class TestRendering:
    STACK = {  # noqa: RUF012 — read-only fixture
        "python": "3.15.0",
        "foliate-js": "78914ae",
        "node": None,
        "security": (0, 1, "2026-10-23"),
    }

    def test_a_missing_tool_is_left_out_not_shown_blank(self):
        assert "node" not in VersionStrip(self.STACK).render()

    def test_a_dash_in_a_date_is_doubled_for_shields(self):
        assert "osv.dev%202026--10--23" in VersionStrip(self.STACK).render()

    def test_acknowledged_is_shown_apart_from_open(self):
        strip = VersionStrip(self.STACK).render()
        assert "0%20open%20%C2%B7%201%20acknowledged" in strip
        assert "-brightgreen.svg" in strip

    def test_an_open_vulnerability_turns_it_red(self):
        stack = {**self.STACK, "security": (2, 0, "2026-10-23")}
        assert "-red.svg" in VersionStrip(stack).render()

    def test_the_strip_round_trips_its_security_badge(self):
        strip = VersionStrip(self.STACK).render()
        assert Readme.security_in(strip) == (0, 1, "2026-10-23")
