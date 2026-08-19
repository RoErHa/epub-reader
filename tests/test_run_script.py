"""The run script is operational surface, so it is tested like code.

These tests never start the real service on the real port — they drive
the argument handling, the scan-on-start behaviour and the status
reporting against a throwaway .env.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.epub_fixtures import EpubBuilder

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "run.sh"

ENV_BODY = """
EPUB_PORT=8779
EPUB_BIND=127.0.0.1
LIBRARY_ROOT={library}
DB_PATH={state}/books.db
COVER_CACHE={state}/covers
LOG_LEVEL=WARNING
LOG_FILE={state}/test.log
"""


@pytest.fixture
def env_file(tmp_path):
    library = tmp_path / "books"
    library.mkdir()
    EpubBuilder(title="First Book").build(library / "first.epub")
    state = tmp_path / "state"
    state.mkdir()
    path = tmp_path / ".env.run"
    path.write_text(ENV_BODY.format(library=library, state=state))
    return path


def run(*args, env_file=None):
    argv = ["bash", str(SCRIPT)]
    if env_file is not None:
        argv += ["--env", str(env_file)]
    argv += list(args)
    return subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=60)


class TestUsage:
    def test_script_exists_and_is_executable(self):
        assert SCRIPT.is_file()
        assert SCRIPT.stat().st_mode & 0o111, "run.sh must be executable"

    def test_no_arguments_prints_usage_and_fails(self):
        result = run()
        assert result.returncode != 0
        assert "usage" in (result.stdout + result.stderr).lower()

    def test_unknown_command_fails_and_names_the_command(self):
        result = run("frobnicate")
        assert result.returncode != 0
        assert "frobnicate" in result.stdout + result.stderr

    def test_help_lists_every_command(self):
        result = run("help")
        assert result.returncode == 0
        out = result.stdout + result.stderr
        for command in ("start", "stop", "restart", "status", "scan"):
            assert command in out


class TestScan:
    def test_scan_reports_books_on_a_first_run(self, env_file):
        result = run("scan", env_file=env_file)
        assert result.returncode == 0, result.stderr
        assert "first.epub" in result.stdout

    def test_scan_reports_nothing_new_on_a_second_run(self, env_file):
        run("scan", env_file=env_file)
        result = run("scan", env_file=env_file)
        assert result.returncode == 0
        assert "no changes" in result.stdout.lower()

    def test_scan_detects_a_book_added_between_runs(self, env_file):
        run("scan", env_file=env_file)
        library = Path(
            [
                line.split("=", 1)[1]
                for line in env_file.read_text().splitlines()
                if line.startswith("LIBRARY_ROOT=")
            ][0]
        )
        EpubBuilder(title="Second Book").build(library / "second.epub")
        result = run("scan", env_file=env_file)
        assert "second.epub" in result.stdout

    def test_missing_env_file_fails_with_a_clear_message(self, tmp_path):
        result = run("scan", env_file=tmp_path / "nope.env")
        assert result.returncode != 0
        assert "not found" in (result.stdout + result.stderr).lower()


class TestStatus:
    def test_status_reports_not_running(self, env_file):
        result = run("status", env_file=env_file)
        assert result.returncode != 0
        assert "not running" in (result.stdout + result.stderr).lower()

    def test_stop_when_not_running_is_not_an_error(self, env_file):
        # Stopping something already stopped is the desired end state.
        result = run("stop", env_file=env_file)
        assert result.returncode == 0


class TestRepair:
    """`run.sh repair` reaches the maintenance passes from a terminal.
    Looking is the default; `--apply` is what changes anything."""

    def test_repair_is_a_known_command(self, tmp_path):
        # An empty library, so the passes have nobody to look up: this
        # is about the script reaching the command, not about the work.
        library = tmp_path / "empty"
        library.mkdir()
        state = tmp_path / "state"
        state.mkdir()
        env = tmp_path / ".env.empty"
        env.write_text(ENV_BODY.format(library=library, state=state))
        result = subprocess.run(
            [str(SCRIPT), "repair", "--env", str(env)],
            capture_output=True, text=True, cwd=ROOT, timeout=60)
        assert result.returncode == 0, result.stderr
        assert "nothing to do" in result.stdout

    def test_repair_is_listed_in_the_help(self):
        result = subprocess.run([str(SCRIPT), "help"], capture_output=True,
                                text=True, cwd=ROOT)
        assert "repair" in result.stdout

    def test_an_unknown_command_is_still_refused(self, env_file):
        result = subprocess.run(
            [str(SCRIPT), "reapir", "--env", str(env_file)],
            capture_output=True, text=True, cwd=ROOT)
        assert result.returncode != 0


class TestRefresh:
    """The weekly launchd job runs `run.sh refresh`, so the script has
    to know the word."""

    def test_refresh_is_a_known_command(self, tmp_path):
        library = tmp_path / "empty"
        library.mkdir()
        state = tmp_path / "state"
        state.mkdir()
        env = tmp_path / ".env.refresh"
        env.write_text(ENV_BODY.format(library=library, state=state))
        result = subprocess.run(
            [str(SCRIPT), "refresh", "--env", str(env)],
            capture_output=True, text=True, cwd=ROOT, timeout=60)
        assert result.returncode == 0, result.stderr
        assert "nothing due" in result.stdout

    def test_refresh_is_listed_in_the_help(self):
        result = subprocess.run([str(SCRIPT), "help"], capture_output=True,
                                text=True, cwd=ROOT)
        assert "refresh" in result.stdout


class TestTheWeeklyJob:
    """The plist that runs it. It is shipped as a template with a
    placeholder path, like the service plist beside it."""

    PLIST = ROOT / "com.roha.epub-reader.refresh.plist"

    def test_it_exists(self):
        assert self.PLIST.is_file()

    def test_it_parses(self):
        import plistlib
        with self.PLIST.open("rb") as handle:
            assert plistlib.load(handle)["Label"] == \
                "com.roha.epub-reader.refresh"

    def test_it_runs_weekly(self):
        import plistlib
        with self.PLIST.open("rb") as handle:
            when = plistlib.load(handle)["StartCalendarInterval"]
        assert "Weekday" in when, "a weekly job needs a day"

    def test_it_does_not_stay_running(self):
        # A refresh is a few seconds of work, not a service.
        import plistlib
        with self.PLIST.open("rb") as handle:
            job = plistlib.load(handle)
        assert job.get("RunAtLoad") is not True
        assert job.get("KeepAlive") is not True

    def test_it_calls_refresh(self):
        import plistlib
        with self.PLIST.open("rb") as handle:
            args = plistlib.load(handle)["ProgramArguments"]
        assert "--refresh" in args


class TestNames:
    """`run.sh names` reads author, series and title off one filename.

    The scope flags are optional, and an empty bash array under
    `set -u` is an *unbound variable*, not an empty list — so the
    unscoped form died with "scope[@]: unbound variable" while the
    scoped one worked perfectly. A pass nobody can run library-wide is
    not much of a pass.
    """

    def test_names_runs_without_a_scope(self, env_file):
        result = run("names", env_file=env_file)
        assert result.returncode == 0, result.stderr
        assert "unbound variable" not in result.stderr

    def test_names_runs_with_a_scope(self, env_file):
        result = run("names", "--author", "Piers Anthony", "--format", "pdf",
                     env_file=env_file)
        assert result.returncode == 0, result.stderr

    def test_names_is_listed_in_the_help(self):
        result = run("help")
        assert "names" in result.stdout + result.stderr


class TestCoAuthors:
    """`run.sh coauthors` frees the people written into titles."""

    def test_coauthors_runs(self, env_file):
        result = run("coauthors", env_file=env_file)
        assert result.returncode == 0, result.stderr

    def test_coauthors_is_listed_in_the_help(self):
        result = run("help")
        assert "coauthors" in result.stdout + result.stderr


class TestFragments:
    """`run.sh fragments` finishes titles that were cut off."""

    def test_fragments_runs_without_a_scope(self, env_file):
        result = run("fragments", env_file=env_file)
        assert result.returncode == 0, result.stderr
        assert "unbound variable" not in result.stderr

    def test_fragments_runs_with_a_scope(self, env_file):
        result = run("fragments", "--author", "Piers Anthony",
                     "--format", "pdf", env_file=env_file)
        assert result.returncode == 0, result.stderr

    def test_fragments_is_listed_in_the_help(self):
        result = run("help")
        assert "fragments" in result.stdout + result.stderr


class TestTags:
    """`run.sh tags` takes the scanner's notes out of titles.

    Its help test also guards the usage block, which used to be a fixed
    line range: every command added pushed the last one off the end, and
    `fragments` fell off silently the day `tags` was added. It now runs
    to the closing rule instead of to a number somebody has to remember.
    """

    def test_tags_runs(self, env_file):
        result = run("tags", env_file=env_file)
        assert result.returncode == 0, result.stderr

    def test_every_command_is_listed_in_the_help(self):
        out = run("help").stdout
        for command in ("start", "stop", "restart", "status", "scan", "repair",
                        "refresh", "dedupe", "covers", "series", "names",
                        "coauthors", "fragments", "tags", "forget"):
            assert command in out, f"{command} has fallen off the help"


class TestTidyOnScan:
    """`run.sh scan` reads the names of what it finds.

    Dropping a book in and restarting is meant to be the whole workflow.
    It was not: nothing built after §6.6 ran on a new arrival, so a book
    added yesterday browsed as `Anthony, Piers - Xanth 12 - …`, credited
    to nobody, exactly as the first forty-five did.
    """

    def test_a_new_arrival_is_read(self, tmp_path):
        library = tmp_path / "books"
        library.mkdir()
        for n, title in ((11, "Heaven Cent"), (12, "Man From Mundania")):
            EpubBuilder(title=f"Anthony, Piers - Xanth {n} - {title}",
                        author="Piers Anthony").build(
                library / f"Anthony, Piers - Xanth {n} - {title}.epub")
        state = tmp_path / "state"
        state.mkdir()
        env = tmp_path / ".env.tidy"
        env.write_text(ENV_BODY.format(library=library, state=state))

        out = run("scan", env_file=env).stdout
        assert "Names:" in out, out
        assert "retitled 2" in out, out

    def test_it_can_be_turned_off(self, tmp_path):
        library = tmp_path / "books"
        library.mkdir()
        EpubBuilder(title="Anthony, Piers - Xanth 11 - Heaven Cent",
                    author="Piers Anthony").build(library / "x.epub")
        state = tmp_path / "state"
        state.mkdir()
        env = tmp_path / ".env.untidy"
        env.write_text(ENV_BODY.format(library=library, state=state)
                       + "TIDY_ON_SCAN=false\n")
        assert "Names:" not in run("scan", env_file=env).stdout
