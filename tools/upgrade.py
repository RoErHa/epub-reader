"""Keep the toolchain current: report what is outdated or known-vulnerable,
and upgrade it only when both test suites prove the result.

Ported from dlna-gateway-2.0, whose docs/UPGRADE.md tells how each rule
was earned. The layers (tools/upgrade_layers.py) are THIS app's,
inventoried rather than copied — CLAUDE.md §10.17 lists them and why.

    python3 tools/upgrade.py                  # report, ending in an Attention list
    python3 tools/upgrade.py --check          # exit 1 on a DO, or a NEW DECIDE/WAIT
    python3 tools/upgrade.py --python 3.16    # dry run of a Python minor move
    python3 tools/upgrade.py --apply          # upgrade; kept only if both suites pass
    python3 tools/upgrade.py --apply --python 3.16
    python3 tools/upgrade.py --rollback       # undo the last --apply

Run it with the Homebrew `python3`, not `.venv/bin/python`: the venv is
what it moves aside.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.upgrade_checks import Acknowledged, Attention, Version  # noqa: E402
from tools.upgrade_layers import (  # noqa: E402
    Brew,
    Foliate,
    Gates,
    Npm,
    Osv,
    PythonEnv,
    Service,
    Settings,
    Shell,
)
from tools.upgrade_snapshot import Snapshot  # noqa: E402

TRACKED_FILES = [
    "requirements.lock",
    "requirements-dev.lock",
    "package.json",
    "package-lock.json",
    "README.md",  # the version strip, rewritten before the gates
]
TRACKED_DIRS = [".venv", "node_modules"]
BSDTAR = "/usr/bin/bsdtar"  # epub_rar.BSDTAR; macOS's own, moved only by macOS


class Report:
    """Every layer, then the Attention list: what to DO, DECIDE, WAIT for, NOTE."""

    def __init__(self, settings: Settings, target_python: str | None) -> None:
        self.s, self.target = settings, target_python
        self.attention = Attention()
        self.snapshot = Snapshot(
            ROOT, settings.backups / "upgrade-prev", TRACKED_FILES, TRACKED_DIRS
        )

    def build(self) -> Attention:
        a = self.attention
        minor = PythonEnv.minor()
        print(f"Runtime now: {PythonEnv.runtime()}")
        self._python(minor)

        print("\nHomebrew:")
        plan = Brew.plan(Brew.formulae(minor or "3"))
        for line in plan.lines + plan.held or ["(all current)"]:
            print(f"  {line}")
        if plan.upgrades:
            a.add("DO", f"brew: {', '.join(plan.upgrades)}")
        for line in plan.held:
            a.add("DECIDE", f"brew {line}")

        print("\npip (fresh resolve, runtime + dev):")
        have = PythonEnv.installed()
        moves = {n: v for n, v in PythonEnv.resolve().items() if have.get(n) != v}
        for n, v in moves.items():
            print(f"  {n} {have.get(n, 'new')} → {v}")
        if moves:
            a.add("DO", f"pip: {len(moves)} package(s)")
        else:
            print("  (all current)")

        print("\nnpm:")
        current, target, latest = Npm.plan()
        print(f"  {Npm.PKG} {current}" + (f" → {target}" if target else " (current)"))
        if target:
            a.add("DO", f"npm: {Npm.PKG} {current} → {target}")
        if latest and latest != (target or current):
            a.add("WAIT", f"{Npm.PKG} {latest} is a new MAJOR — upgrade it on its own")

        print("\nfoliate-js (pinned; reported, never moved):")
        behind = Foliate.behind()
        bundled = Foliate.bundled()
        print(
            f"  {'?' if behind is None else behind} commit(s) behind upstream; "
            f"bundles {', '.join(f'{k} {v}' for k, v in bundled.items())}"
        )
        if behind:
            a.add(
                "DECIDE",
                f"foliate-js is {behind} commit(s) behind upstream — "
                "review, then update the submodule by hand (§3)",
            )

        print("\nmacOS:")
        bsdtar = Shell.out([BSDTAR, "--version"]).strip()
        print(f"  {bsdtar or 'bsdtar not found'}")

        self._vulns(have, moves, bundled)
        stale = Shell.run(
            [PythonEnv.PY, "tools/versions.py"], check=False, quiet=True
        ).returncode
        if stale:
            a.add(
                "DO",
                "README version strip is stale — "
                "`.venv/bin/python tools/versions.py --write`",
            )
        if self.snapshot.saved_at():
            a.add(
                "NOTE",
                f"rollback point from {self.snapshot.saved_at()} kept in "
                f"{self.snapshot.where} (`--rollback`)",
            )
        return a

    def _python(self, minor: str | None) -> None:
        target = self.target or Brew.newest_python()
        if not (minor and target and Version.stable(target) > Version.stable(minor)):
            return
        print(
            f"\nPython {target} readiness (venv is on {minor}) — latest release "
            f"of each installed package, checked for a {target} wheel on PyPI:"
        )
        blockers, built = PythonEnv.readiness(target)
        for b in built:
            print(f"  ✓ {b} — no wheel, but VERIFIED to compile here")
        for b in blockers:
            print(f"  ✗ {b}")
        if not blockers:
            print(f"  every package installs on {target}")
        if blockers:
            names = ", ".join(b.split()[0] for b in blockers)
            self.attention.add(
                "WAIT", f"Python {target}: not yet — no {target} wheel for {names}"
            )
        else:
            via = (
                f" ({', '.join(b.split()[0] for b in built)} compiles)" if built else ""
            )
            self.attention.add(
                "DECIDE", f"Python {target}: READY{via} — `--apply --python {target}`"
            )

    def _vulns(self, have: dict, moves: dict, bundled: dict) -> None:
        print("\nKnown vulnerabilities (osv.dev):")
        asked = [("PyPI", n, v) for n, v in sorted(have.items())]
        asked += [("npm", n, v) for n, v in Npm.packages()]
        asked += [("npm", n, v) for n, v in bundled.items()]
        found = Osv.check(asked)
        for pkg, ids in found.items():
            print(f"  ✗ {pkg}: {', '.join(ids[:8])}{' …' if len(ids) > 8 else ''}")
        if not found:
            print("  none known")
        print("  (Homebrew formulae and macOS's bsdtar are not in osv.dev.)")
        ack = Acknowledged()
        for pkg, ids in found.items():
            name = pkg.split()[0]
            if (why := ack.reason(ids)) is not None:
                self.attention.add("NOTE", f"{pkg}: acknowledged — {why}")
            elif name in moves:
                self.attention.add("DO", f"{name} is vulnerable; the upgrade fixes it")
            elif name in bundled:
                self.attention.add(
                    "DECIDE",
                    f"{pkg} is bundled INSIDE foliate-js and "
                    "vulnerable — see whether upstream has moved",
                )
            else:
                self.attention.add(
                    "NOTE", f"{pkg} is vulnerable with no fixed release yet — watch it"
                )

    def print_attention(self) -> None:
        print("\nAttention")
        for kind, text in self.attention.ordered():
            print(f"  {kind:<7} {text}")
        if not self.attention.ordered():
            print("  nothing — all current, nothing known-vulnerable")
        print(f"Attention: {self.attention.summary()}")
        print("Dry run — nothing changed.")


class Upgrader:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.s = Settings()
        self.service = Service(self.s)
        self.snapshot = Snapshot(
            ROOT, self.s.backups / "upgrade-prev", TRACKED_FILES, TRACKED_DIRS
        )

    def report(self) -> int:
        r = Report(self.s, self.args.python)
        attention = r.build()
        r.print_attention()
        if not self.args.check:
            return 0
        try:
            previous = json.loads(self.s.state.read_text())
        except (OSError, ValueError):
            previous = []
        for item in attention.new_since(previous):
            print(f"NEW since last check: {item}")
        notify = attention.should_notify(previous)
        self.s.state.parent.mkdir(parents=True, exist_ok=True)
        self.s.state.write_text(json.dumps(attention.watched(), indent=1))
        return 1 if notify else 0

    def apply(self) -> int:
        minor = PythonEnv.minor()
        if not minor:
            raise SystemExit(
                "no working .venv — create it first (CLAUDE.md, Running it)"
            )
        target = self.args.python or minor
        print(f"Before: {PythonEnv.runtime()}")

        print("\n1. Database backup")
        self.service.backup_db()

        print("\n2. Homebrew")
        Shell.run(["brew", "update", "--quiet"], env=Brew.ENV)
        if target != minor:
            Shell.run(["brew", "install", f"python@{target}"], env=Brew.ENV)
        plan = Brew.plan(Brew.formulae(target))
        for line in plan.held:
            print(f"   ! {line}")
        Brew.upgrade(plan)

        print(f"\n3. Inside the snapshot (previous kept in {self.snapshot.where})")
        self.snapshot.save()
        try:
            ok = all(
                getattr(self, f"_{step}")(target) is not False
                for step in self.GUARDED_STEPS
            )
        except (subprocess.CalledProcessError, RuntimeError, OSError) as e:
            print(f"   ✗ {e}")
            ok = False

        if not ok:
            return self._roll_back("Upgrade FAILED")
        print("\n4. Restart")
        if not self.service.restart():
            return self._roll_back("Service unhealthy after restart")
        print(f"\nAfter: {PythonEnv.runtime()}")
        print(
            "\n✓ Upgrade passed both suites. Review and commit:\n"
            "    git diff --stat\n"
            "  Undo, if it misbehaves live: python3 tools/upgrade.py --rollback"
        )
        return 0

    # Run in this order inside the snapshot; any failure rolls back all of it.
    # `versions` must come BEFORE `gates`: tests/test_versions.py checks the
    # README strip against the NEW locks, so the strip is rewritten first —
    # the first real --apply was refused for exactly this (2026-10-23).
    GUARDED_STEPS = ("venv", "npm", "versions", "gates")

    def _venv(self, target: str) -> None:
        print(f"\n   · fresh .venv on Python {target}")
        PythonEnv.rebuild(target)

    def _npm(self, target: str) -> None:
        print("\n   · npm")
        _, npm_target, _ = Npm.plan()
        Shell.run(["npm", "ci"])
        if npm_target:
            Npm.upgrade(npm_target)
        else:
            Shell.run(["npx", "playwright", "install", "chromium", "webkit"])

    def _versions(self, target: str) -> None:
        print("\n   · README version strip")
        Shell.run([PythonEnv.PY, "tools/versions.py", "--write"])

    def _gates(self, target: str) -> bool:
        print("\n   · both suites")
        return Gates.pass_all(self.snapshot.where / "gates.log")

    def _roll_back(self, why: str) -> int:
        print(f"\n✗ {why} — rolling back")
        self._restore()
        self.service.restart()
        return 1

    def _restore(self) -> None:
        self.snapshot.restore()
        print("   restored .venv, node_modules, both locks, package*.json, README")
        # The upgrade's browser install let Playwright garbage-collect the
        # ones the restored @playwright/test needs; without this, every UI
        # test fails at 0 ms after a rollback (2026-10-23).
        Shell.run(["npx", "playwright", "install", "chromium", "webkit"], check=False)

    def rollback(self) -> int:
        self._restore()
        return 0 if self.service.restart() else 1


class Cli:
    @staticmethod
    def parse(argv: list[str]) -> argparse.Namespace:
        ap = argparse.ArgumentParser(
            description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
        )
        mode = ap.add_mutually_exclusive_group()
        mode.add_argument("--apply", action="store_true")
        mode.add_argument("--check", action="store_true")
        mode.add_argument("--rollback", action="store_true")
        ap.add_argument(
            "--python",
            metavar="3.N",
            help="with --apply: move the venv to this minor; "
            "without: a dry run of that move",
        )
        return ap.parse_args(argv)

    @classmethod
    def main(cls, argv: list[str]) -> int:
        args = cls.parse(argv)
        up = Upgrader(args)
        if args.rollback:
            return up.rollback()
        return up.apply() if args.apply else up.report()


if __name__ == "__main__":
    raise SystemExit(Cli.main(sys.argv[1:]))
