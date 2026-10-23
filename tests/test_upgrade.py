"""tools/upgrade.py — the decisions behind keeping the toolchain current.

Ported from dlna-gateway-2.0, where each of these rules was earned on the
first day it ran (docs there: docs/UPGRADE.md). What is tested here is
what must never be wrong:

  * a Homebrew "upgrade" that is really a DOWNGRADE is held, not taken
  * "no wheel for the new Python" is a risk to test, not a verdict
  * the Attention list says DO / DECIDE / WAIT / NOTE, and the weekly
    check pings only on a DO or on something NEW since last time
  * the snapshot restores exactly what an --apply moved aside
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.upgrade_checks import (  # noqa: E402
    Acknowledged,
    Attention,
    BrewPlan,
    EnvFile,
    FoliateVendor,
    NpmLock,
    OsvAnswer,
    Version,
    Wheel,
)
from tools.upgrade_snapshot import Snapshot  # noqa: E402


class TestEnvFile:
    def test_reads_values_the_way_the_app_does(self):
        text = (
            '# comment\nEPUB_PORT=8770\nexport LAUNCHD_LABEL="com.x.reader"\n'
            "EMPTY=\nQUOTED='a b'   \n\nNOT A LINE\n"
        )
        assert EnvFile.parse(text) == {
            "EPUB_PORT": "8770",
            "LAUNCHD_LABEL": "com.x.reader",
            "EMPTY": "",
            "QUOTED": "a b",
        }


class TestVersion:
    def test_numeric_not_lexical(self):
        assert Version.key("1.10.0") > Version.key("1.9.0")

    def test_a_brew_revision_counts(self):
        assert Version.key("9.0.2_1") > Version.key("9.0.2")

    def test_prerelease_is_not_stable(self):
        assert Version.stable("1.7.0-rc.3") is None
        assert Version.stable("3.15") == (3, 15)

    def test_newest_within_a_major(self):
        versions = ["1.62.1", "1.64.1", "2.0.0", "1.65.0-beta.1"]
        assert Version.newest(versions, "1.62.1", same_major=True) == "1.64.1"
        assert Version.newest(versions, "1.62.1", same_major=False) == "2.0.0"
        assert Version.newest(versions, "2.0.0", same_major=False) is None


class TestBrewPlan:
    @staticmethod
    def f(name, installed, current):
        return {
            "name": name,
            "installed_versions": installed,
            "current_version": current,
        }

    def test_a_revision_bump_is_an_upgrade(self):
        plan = BrewPlan([self.f("node", ["26.10.0_1"], "26.12.1")])
        assert plan.upgrades == ["node"] and plan.held == []

    def test_a_repointed_formula_is_held_not_downgraded(self):
        # dlna-gateway, 2026-10-23: openssl@3 repointed to the 3.5 LTS line.
        plan = BrewPlan([self.f("openssl@3", ["3.6.5"], "3.5.9")])
        assert plan.upgrades == []
        assert plan.held[0].startswith("openssl@3 3.6.5 → 3.5.9")

    def test_compares_with_the_newest_installed_keg(self):
        plan = BrewPlan([self.f("sqlite", ["3.53.4", "3.54.0"], "3.54.1")])
        assert plan.upgrades == ["sqlite"]


class TestWheel:
    M = (3, 15)

    @pytest.mark.parametrize(
        "name",
        [
            "rich-15.0.0-py3-none-any.whl",
            "x-1-cp315-cp315-macosx_11_0_arm64.whl",
            "x-1-cp315-cp315-macosx_10_13_universal2.whl",
            "x-1-cp39-abi3-macosx_11_0_arm64.whl",
        ],
    )
    def test_installs(self, name):
        assert Wheel.fits(name, self.M)

    @pytest.mark.parametrize(
        "name",
        [
            "x-1-cp314-cp314-macosx_11_0_arm64.whl",  # another CPython
            "x-1-cp315-cp315-macosx_10_13_x86_64.whl",  # another CPU
            "x-1-cp315-cp315t-macosx_11_0_arm64.whl",  # free-threaded build
            "x-1-cp316-abi3-macosx_11_0_arm64.whl",  # stable ABI from the future
            "x-1.tar.gz",  # source, not a wheel
        ],
    )
    def test_does_not_install(self, name):
        assert not Wheel.fits(name, self.M)

    def test_blockers_say_why(self):
        files = {
            "rich": ("15.0.0", ["rich-15.0.0-py3-none-any.whl"]),
            "watchdog": ("6.0.0", ["watchdog-6.0.0.tar.gz"]),
            "odd": ("1.0", []),
        }
        assert Wheel.blockers(files, self.M) == [
            ("odd", "1.0", "nothing installable"),
            ("watchdog", "6.0.0", "source only"),
        ]


NPM_LOCK = {
    "packages": {
        "": {"name": "epub-reader"},
        "node_modules/@playwright/test": {"version": "1.62.1"},
        "node_modules/playwright": {"version": "1.62.1"},
        "node_modules/a/node_modules/b": {"version": "2.0.0"},
    }
}


class TestNpm:
    def test_every_installed_package_with_its_real_name(self):
        assert NpmLock.packages(json.dumps(NPM_LOCK)) == [
            ("@playwright/test", "1.62.1"),
            ("b", "2.0.0"),
            ("playwright", "1.62.1"),
        ]


class TestFoliateVendor:
    def test_the_libraries_foliate_bundles(self):
        pkg = json.dumps(
            {
                "devDependencies": {
                    "pdfjs-dist": "^5.5.207",
                    "@zip.js/zip.js": "~2.7.52",
                    "fflate": "0.8.2",
                    "eslint": "^9",
                }
            }
        )
        assert FoliateVendor.bundled(pkg) == {
            "pdfjs-dist": "5.5.207",
            "@zip.js/zip.js": "2.7.52",
            "fflate": "0.8.2",
        }


class TestOsvAnswer:
    def test_pairs_each_answer_with_its_question(self):
        asked = [("PyPI", "a", "1"), ("npm", "pdfjs-dist", "5.5.207")]
        answer = {"results": [{}, {"vulns": [{"id": "GHSA-2"}, {"id": "CVE-1"}]}]}
        assert OsvAnswer(asked, answer).findings() == {
            "pdfjs-dist 5.5.207 (npm)": ["CVE-1", "GHSA-2"]
        }

    def test_a_short_answer_is_an_error_not_a_clean_bill(self):
        with pytest.raises(ValueError):
            OsvAnswer(
                [("PyPI", "a", "1"), ("PyPI", "b", "2")], {"results": [{}]}
            ).findings()


class TestAttention:
    def test_items_are_sorted_by_what_to_do(self):
        a = Attention()
        a.add("NOTE", "n")
        a.add("WAIT", "w")
        a.add("DO", "d")
        assert [k for k, _ in a.ordered()] == ["DO", "WAIT", "NOTE"]
        assert a.summary() == "1 do · 0 decide · 1 wait · 1 note"

    def test_an_unknown_kind_is_refused(self):
        with pytest.raises(ValueError):
            Attention().add("MAYBE", "x")

    def test_pings_on_a_do(self):
        a = Attention()
        a.add("DO", "pip: 3 packages")
        assert a.should_notify(previous=[])

    def test_an_unchanged_wait_stays_quiet(self):
        a = Attention()
        a.add("WAIT", "Python 3.16: not yet")
        assert not a.should_notify(previous=a.watched())

    def test_a_changed_verdict_is_new(self):
        a = Attention()
        a.add("DECIDE", "Python 3.16: READY")
        assert a.should_notify(previous=["WAIT Python 3.16: not yet"])


class TestSnapshot:
    def test_restore_puts_back_exactly_what_was_moved(self, tmp_path):
        project, prev = tmp_path / "project", tmp_path / "prev"
        (project / ".venv" / "bin").mkdir(parents=True)
        (project / ".venv" / "bin" / "python").write_text("old python")
        (project / "requirements.lock").write_text("old lock")
        snap = Snapshot(project, prev, files=["requirements.lock"], dirs=[".venv"])

        snap.save()
        assert not (project / ".venv").exists()  # moved aside, not copied
        (project / ".venv" / "bin").mkdir(parents=True)  # the "upgrade"
        (project / ".venv" / "bin" / "python").write_text("new python")
        (project / "requirements.lock").write_text("new lock")

        snap.restore()
        assert (project / ".venv" / "bin" / "python").read_text() == "old python"
        assert (project / "requirements.lock").read_text() == "old lock"

    def test_a_directory_that_did_not_exist_is_removed_on_restore(self, tmp_path):
        project, prev = tmp_path / "project", tmp_path / "prev"
        project.mkdir()
        snap = Snapshot(project, prev, files=[], dirs=["node_modules"])
        snap.save()
        (project / "node_modules").mkdir()
        snap.restore()
        assert not (project / "node_modules").exists()

    def test_restore_without_a_save_refuses(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            Snapshot(tmp_path, tmp_path / "nothing", files=[], dirs=[".venv"]).restore()


ACK = {"GHSA-1": "the vulnerable function is not in the shipped bundle"}


class TestAcknowledged:
    """A finding a person investigated and found not to apply is recorded
    WITH its reason — and anything not on that record still surfaces."""

    def test_a_fully_acknowledged_finding_carries_its_reason(self):
        assert Acknowledged(ACK).reason(["GHSA-1"]) == (
            "the vulnerable function is not in the shipped bundle")

    def test_one_new_advisory_on_the_same_package_is_not_covered(self):
        assert Acknowledged(ACK).reason(["GHSA-1", "GHSA-2"]) is None

    def test_an_acknowledgement_without_a_reason_is_refused(self):
        with pytest.raises(ValueError):
            Acknowledged({"GHSA-1": "  "})

    def test_the_real_record_gives_every_entry_a_reason(self):
        assert all(r.strip() for r in Acknowledged.RECORD.values())


class TestApplyOrder:
    """The steps --apply runs inside the snapshot, in order. Found on the
    first real --apply (2026-10-23): the README strip was rewritten only
    AFTER the gates, so the pinned-version gate compared the old strip with
    the new locks and refused every upgrade that moved a pin."""

    def test_the_strip_is_rewritten_before_the_suites_judge_it(self):
        from tools.upgrade import Upgrader

        steps = Upgrader.GUARDED_STEPS
        assert steps.index("versions") < steps.index("gates")
        assert steps.index("venv") < steps.index("versions")

    def test_every_step_has_a_method(self):
        from tools.upgrade import Upgrader

        missing = [s for s in Upgrader.GUARDED_STEPS if not hasattr(Upgrader, f"_{s}")]
        assert missing == []

    def test_a_rollback_restores_the_readme_it_rewrote(self):
        from tools.upgrade import TRACKED_FILES

        assert "README.md" in TRACKED_FILES


class TestRollingBack:
    """Found on the first real --apply (2026-10-23): the upgrade installed
    new Playwright browsers, Playwright garbage-collected the old ones (the
    old node_modules was moved aside, so nothing referenced them), and the
    rollback restored a @playwright/test whose browsers no longer existed —
    every UI test failed at 0 ms. A rollback must leave both suites able
    to run."""

    def test_a_rollback_reinstalls_the_restored_browsers(self, monkeypatch):
        from tools import upgrade

        ran = []
        up = object.__new__(upgrade.Upgrader)
        up.snapshot = type("S", (), {"restore": lambda self: ran.append("restore")})()
        up.service = type("V", (), {"restart": lambda self: ran.append("restart")})()
        monkeypatch.setattr(upgrade.Shell, "run",
                            staticmethod(lambda cmd, **kw: ran.append(" ".join(cmd))))

        assert up._roll_back("test") == 1
        install = "npx playwright install chromium webkit"
        assert install in ran
        assert ran.index("restore") < ran.index(install) < ran.index("restart")
