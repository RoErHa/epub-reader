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
    return subprocess.run(argv, cwd=ROOT, capture_output=True, text=True,
                          timeout=60, check=False)


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
        library = Path(next(
            line.split("=", 1)[1]
            for line in env_file.read_text().splitlines()
            if line.startswith("LIBRARY_ROOT=")
        ))
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
        result = subprocess.run(  # noqa: PLW1510 — the return code IS the assertion
            [str(SCRIPT), "repair", "--env", str(env)],
            capture_output=True, text=True, cwd=ROOT, timeout=60)
        assert result.returncode == 0, result.stderr
        assert "nothing to do" in result.stdout

    def test_repair_is_listed_in_the_help(self):
        result = subprocess.run([str(SCRIPT), "help"], capture_output=True,
                                text=True, cwd=ROOT, check=False)
        assert "repair" in result.stdout

    def test_an_unknown_command_is_still_refused(self, env_file):
        result = subprocess.run(  # noqa: PLW1510 — the return code IS the assertion
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
        result = subprocess.run(  # noqa: PLW1510 — the return code IS the assertion
            [str(SCRIPT), "refresh", "--env", str(env)],
            capture_output=True, text=True, cwd=ROOT, timeout=60)
        assert result.returncode == 0, result.stderr
        assert "nothing due" in result.stdout

    def test_refresh_is_listed_in_the_help(self):
        result = subprocess.run([str(SCRIPT), "help"], capture_output=True,
                                text=True, cwd=ROOT, check=False)
        assert "refresh" in result.stdout


class TestTheWeeklyJob:
    """The plist that runs it. It is shipped as a template with a
    placeholder path, like the service plist beside it."""

    PLIST = ROOT / "com.example.epub-reader.refresh.plist"

    def test_it_exists(self):
        assert self.PLIST.is_file()

    def test_it_parses(self):
        import plistlib
        with self.PLIST.open("rb") as handle:
            assert plistlib.load(handle)["Label"] == \
                "com.example.epub-reader.refresh"

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


class TestTheLaunchdLabel:
    """Which LaunchAgent this script drives is configuration.

    It was a constant: one person's reverse domain baked into a script
    anybody can run. The example says `com.example.epub-reader`, the
    name reserved for exactly this (RFC 2606), and a reader who owns a
    domain says so in their own `.env`.
    """

    # Assembled rather than written out, so this file does not itself
    # contain the string it is here to forbid.
    PERSONAL = "com." + "roha"

    def _env(self, tmp_path, label=None):
        library = tmp_path / "books"
        library.mkdir()
        EpubBuilder(title="A Book").build(library / "a.epub")
        state = tmp_path / "state"
        state.mkdir()
        env = tmp_path / ".env.label"
        body = ENV_BODY.format(library=library, state=state)
        if label is not None:
            body += f"LAUNCHD_LABEL={label}\n"
        env.write_text(body)
        return env

    def test_the_label_comes_from_the_env(self, tmp_path):
        env = self._env(tmp_path, "com.someone.epub-reader")
        out = run("status", env_file=env).stdout
        assert "com.someone.epub-reader" in out
        assert self.PERSONAL not in out

    def test_it_falls_back_to_the_example_name(self, tmp_path):
        """A reader who sets nothing gets the reserved example domain,
        never somebody else's."""
        env = self._env(tmp_path)
        result = run("status", env_file=env)
        # 3 is "not running", which is the truth and not an error.
        assert result.returncode in (0, 3), result.stderr
        assert "com.example.epub-reader" in result.stdout
        assert self.PERSONAL not in result.stdout

    def test_nothing_ships_with_a_personal_label(self):
        """The one that would have been missed: a grep over what is
        actually committed."""
        import subprocess
        tracked = subprocess.run(["git", "ls-files"], cwd=ROOT,
                                 capture_output=True, text=True,
                                 check=False).stdout.split()
        for name in tracked:
            path = ROOT / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            assert self.PERSONAL not in text, f"{name} still names a person"


class TestLaunchdAgentSurvivesARestart:
    """A LaunchAgent that a restart uninstalls is worse than none.

    `do_stop` boots the agent out, which is right — a stopped service
    must not be resurrected by `KeepAlive` a second later. But `do_start`
    then asked whether the agent was loaded, found that it was not
    because the stop had just unloaded it, and started a plain process
    with a pidfile instead. One `./run.sh restart` therefore demoted the
    service to something launchd knew nothing about, silently, and it no
    longer came back at login.

    launchctl is faked here: a marker file stands in for "the agent is
    loaded", so the branch can be driven without touching the real
    launchd or the real port.
    """

    LABEL = "com.test.epub-reader"

    FAKE_LAUNCHCTL = """#!/bin/bash
# Records what it was asked, and keeps a marker file standing in for
# "this agent is loaded".
echo "$*" >>"${LAUNCHCTL_LOG}"
case "$1" in
  print)     [ -f "${LAUNCHCTL_MARKER}" ] ;;
  bootstrap) touch "${LAUNCHCTL_MARKER}" ;;
  bootout)   rm -f "${LAUNCHCTL_MARKER}" ;;
  kickstart) [ -f "${LAUNCHCTL_MARKER}" ] ;;
  *)         : ;;
esac
"""

    @pytest.fixture(autouse=True)
    def no_stray_process(self, tmp_path):
        """A test that drives the launchd branch must never leave a real
        service behind — which is exactly what the bug did, since falling
        through to the pidfile path starts one."""
        yield
        pidfile = ROOT / ".run" / f"{(tmp_path / '.env.run').name}.pid"
        if pidfile.is_file():
            import contextlib
            import os
            import signal
            with contextlib.suppress(ValueError, ProcessLookupError,
                                     PermissionError):
                os.kill(int(pidfile.read_text().strip()), signal.SIGTERM)
            pidfile.unlink()

    def _world(self, tmp_path, loaded):
        """A fake launchctl on PATH, a fake HOME with the plist in it."""
        library = tmp_path / "books"
        library.mkdir()
        EpubBuilder(title="First Book").build(library / "first.epub")
        state = tmp_path / "state"
        state.mkdir()
        env_file = tmp_path / ".env.run"
        env_file.write_text(
            ENV_BODY.format(library=library, state=state)
            + f"LAUNCHD_LABEL={self.LABEL}\n"
        )

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "launchctl"
        fake.write_text(self.FAKE_LAUNCHCTL)
        fake.chmod(0o755)

        home = tmp_path / "home"
        agents = home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        (agents / f"{self.LABEL}.plist").write_text("<plist/>")

        marker = tmp_path / "loaded"
        if loaded:
            marker.write_text("")

        import os
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["HOME"] = str(home)
        env["LAUNCHCTL_LOG"] = str(tmp_path / "launchctl.log")
        env["LAUNCHCTL_MARKER"] = str(marker)
        return env_file, env, marker

    def _run(self, *args, env_file, env):
        argv = ["bash", str(SCRIPT), "--env", str(env_file), *args]
        return subprocess.run(argv, cwd=ROOT, capture_output=True,
                              text=True, timeout=120, check=False, env=env)

    def test_restart_leaves_the_agent_loaded(self, tmp_path):
        """The regression: after a restart the service is still launchd's,
        not a pidfile's."""
        env_file, env, marker = self._world(tmp_path, loaded=True)
        result = self._run("restart", env_file=env_file, env=env)
        assert result.returncode == 0, result.stderr
        assert marker.exists(), (
            "restart booted the agent out and never loaded it again"
        )
        assert "launchd" in result.stdout
        assert "Logs:" not in result.stdout, (
            "restart fell through to the pidfile path"
        )

    def test_start_loads_an_agent_that_is_installed_but_stopped(self, tmp_path):
        """A stopped agent is booted out, not deleted — its plist is
        still there, so starting means loading it again."""
        env_file, env, marker = self._world(tmp_path, loaded=False)
        result = self._run("start", env_file=env_file, env=env)
        assert result.returncode == 0, result.stderr
        assert marker.exists(), "an installed agent was not loaded"
        assert "Logs:" not in result.stdout

    def test_an_install_with_no_agent_still_uses_the_pidfile(self, tmp_path):
        """The fallback is untouched: no plist means no launchd, and the
        script starts a plain process as it always did."""
        env_file, env, _ = self._world(tmp_path, loaded=False)
        plist = Path(env["HOME"]) / "Library" / "LaunchAgents" / f"{self.LABEL}.plist"
        plist.unlink()
        try:
            result = self._run("start", env_file=env_file, env=env)
            assert result.returncode == 0, result.stderr
            assert "Logs:" in result.stdout
        finally:
            self._run("stop", env_file=env_file, env=env)
