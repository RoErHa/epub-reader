"""The linter runs in the suite, or it does not run.

Ruff was added to a codebase that had gone without one, and found 116
things and no bugs — which is the point twice over: none of it was
wrong, and nothing had been checking. The eleven *unused* `# noqa`
comments were the tell, suppressions written for a linter that had never
existed.

A tidy-up nobody enforces is a tidy-up that lasts until the next commit,
so it is asserted here rather than left to a habit.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUFF = ROOT / ".venv" / "bin" / "ruff"


@pytest.mark.skipif(not RUFF.exists() and not shutil.which("ruff"),
                    reason="ruff is not installed; see requirements-dev.txt")
def test_the_code_is_clean():
    ruff = str(RUFF) if RUFF.exists() else "ruff"
    result = subprocess.run([ruff, "check", ".", "--output-format", "concise"],
                            cwd=ROOT, capture_output=True, text=True,
                            check=False)
    assert result.returncode == 0, "\n" + result.stdout


@pytest.mark.skipif(not RUFF.exists() and not shutil.which("ruff"),
                    reason="ruff is not installed; see requirements-dev.txt")
def test_the_config_is_the_one_that_ships():
    """A rule set that lives in somebody's shell history is not a rule
    set."""
    assert (ROOT / "ruff.toml").is_file()
