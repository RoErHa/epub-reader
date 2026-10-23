"""The layers tools/upgrade.py works on — each one a part of the toolchain,
asked about and upgraded through its own package manager.

  Brew, PythonEnv, Npm   upgraded (gated by both suites, rolled back on failure)
  Foliate                REPORTED only — pinned submodule, updated by hand (§3)
  Osv                    known vulnerabilities, PyPI and npm
  Service, Gates         restart through run.sh, and the two test suites

Standard library only: the upgrade moves the venv aside while this runs.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.upgrade_checks import (  # noqa: E402
    BrewPlan,
    EnvFile,
    FoliateVendor,
    NpmLock,
    OsvAnswer,
    Version,
    Wheel,
)

UA = "epub-reader-upgrade/1.0"


class Shell:
    """Subprocesses, echoed when they change something."""

    @staticmethod
    def run(cmd, check=True, quiet=False, env=None, cwd=ROOT):
        if not quiet:
            print("   $ " + " ".join(map(str, cmd)), flush=True)
        return subprocess.run(
            list(map(str, cmd)),
            check=check,
            text=True,
            env=env,
            cwd=cwd,
            capture_output=quiet,
        )

    @classmethod
    def out(cls, cmd, cwd=ROOT) -> str:
        r = cls.run(cmd, check=False, quiet=True, cwd=cwd)
        return r.stdout if r.returncode == 0 else ""


class Http:
    @staticmethod
    def json(url: str, body: dict | None = None, timeout: int = 30):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"User-Agent": UA, "Content-Type": "application/json"}
        req = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)


class Settings:
    """Everything configurable comes from `.env` (rule 7) — the same file the
    app reads, parsed without the venv this tool moves aside."""

    def __init__(self) -> None:
        env = EnvFile.parse((ROOT / ".env").read_text())
        missing = [
            k
            for k in ("EPUB_PORT", "LAUNCHD_LABEL", "DB_PATH", "UPGRADE_BACKUP_DIR")
            if not env.get(k)
        ]
        if missing:
            raise SystemExit(f".env is missing {missing} — see .env.example")
        self.port = int(env["EPUB_PORT"])
        self.label = env["LAUNCHD_LABEL"]
        self.db = (ROOT / env["DB_PATH"]).resolve()
        self.backups = Path(env["UPGRADE_BACKUP_DIR"]).expanduser()
        plain = [a.strip() for a in env.get("EPUB_PLAIN_BIND", "").split(",")]
        host = "127.0.0.1" if "127.0.0.1" in plain else (plain[0] or "127.0.0.1")
        self.health = f"http://{host}:{self.port}/healthz"
        self.state = self.backups / "upgrade-attention.json"


class Osv:
    @staticmethod
    def check(asked: list[tuple[str, str, str]]) -> dict[str, list[str]]:
        queries = [
            {"package": {"name": n, "ecosystem": e}, "version": v} for e, n, v in asked
        ]
        answer = Http.json("https://api.osv.dev/v1/querybatch", {"queries": queries})
        return OsvAnswer(asked, answer).findings()


class Brew:
    ENV: ClassVar[dict[str, str]] = {
        **os.environ,
        "HOMEBREW_NO_INSTALL_CLEANUP": "1",
        "HOMEBREW_NO_ENV_HINTS": "1",
    }

    @staticmethod
    def formulae(minor: str) -> list[str]:
        """python@<minor> plus what it links — asked, never listed: on
        2026-10-23 a PATCH release moved Python from openssl@3 to openssl@4."""
        deps = Shell.out(["brew", "deps", f"python@{minor}"]).split()
        installed = set(Shell.out(["brew", "list", "--formula", "-1"]).split())
        return [f for f in (f"python@{minor}", *deps, "node") if f in installed]

    @staticmethod
    def plan(names: list[str]) -> BrewPlan:
        raw = Shell.out(["brew", "outdated", "--json=v2", *names]) or "{}"
        return BrewPlan(json.loads(raw).get("formulae", []))

    @staticmethod
    def newest_python() -> str | None:
        raw = Shell.out(["brew", "info", "--json=v2", "python@3"])
        try:
            return json.loads(raw)["formulae"][0]["name"].split("@")[1]
        except (ValueError, KeyError, IndexError):
            return None

    @classmethod
    def upgrade(cls, plan: BrewPlan) -> None:
        held = [line.split()[0] for line in plan.held]
        for name in held:
            Shell.run(["brew", "pin", name], check=False)
        try:
            if plan.upgrades:
                Shell.run(["brew", "upgrade", *plan.upgrades], env=cls.ENV)
        finally:
            for name in held:
                Shell.run(["brew", "unpin", name], check=False)


class PythonEnv:
    VENV = ROOT / ".venv"
    PY = VENV / "bin" / "python"

    @classmethod
    def minor(cls) -> str | None:
        return (
            Shell.out(
                [cls.PY, "-c", "import sys;print('%d.%d'%sys.version_info[:2])"]
            ).strip()
            or None
        )

    @classmethod
    def runtime(cls) -> str:
        code = (
            "import sys,sqlite3,ssl;print(sys.version.split()[0],'· sqlite',"
            "sqlite3.sqlite_version,'·',ssl.OPENSSL_VERSION)"
        )
        return Shell.out([cls.PY, "-c", code]).strip() or "no .venv"

    @classmethod
    def installed(cls) -> dict[str, str]:
        raw = Shell.out([cls.PY, "-m", "pip", "list", "--format=json"]) or "[]"
        return {
            p["name"].lower().replace("_", "-"): p["version"] for p in json.loads(raw)
        }

    @classmethod
    def resolve(cls) -> dict[str, str]:
        """What a fresh eager install would pick."""
        r = Shell.run(
            [
                cls.PY,
                "-m",
                "pip",
                "install",
                "--dry-run",
                "--quiet",
                "--report",
                "-",
                "--ignore-installed",
                "-r",
                "requirements-dev.txt",
            ],
            check=False,
            quiet=True,
        )
        if r.returncode:
            raise RuntimeError("pip could not resolve:\n" + r.stderr[-2000:])
        return {
            i["metadata"]["name"].lower().replace("_", "-"): i["metadata"]["version"]
            for i in json.loads(r.stdout).get("install", [])
        }

    @staticmethod
    def pypi_latest(names: list[str]) -> dict[str, tuple[str, list[str]]]:
        def one(name):
            try:
                d = Http.json(f"https://pypi.org/pypi/{name}/json", timeout=20)
            except (OSError, ValueError) as e:
                print(f"  ! PyPI lookup failed for {name}: {e}")
                return name, None
            return name, (d["info"]["version"], [u["filename"] for u in d["urls"]])

        with ThreadPoolExecutor(max_workers=8) as pool:
            return {n: v for n, v in pool.map(one, names) if v}

    @classmethod
    def readiness(cls, target: str) -> tuple[list[str], list[str]]:
        """(blockers, compiled-here) for moving to `target`. A source-only
        package is BUILT in a throwaway venv before being called a blocker:
        watchdog had no macOS wheel past cp313 and compiled fine all along."""
        names = [n for n in cls.installed() if n not in ("pip", "setuptools")]
        blockers = Wheel.blockers(cls.pypi_latest(names), Version.stable(target)[:2])
        py = Path(f"/opt/homebrew/opt/python@{target}/bin/python{target}")
        built = []
        if py.exists() and any(why == "source only" for _, _, why in blockers):
            with tempfile.TemporaryDirectory(prefix="upgrade-rehearse-") as tmp:
                Shell.run([py, "-m", "venv", tmp], quiet=True)
                for name, ver, why in blockers:
                    if why != "source only":
                        continue
                    print(f"  … building {name} {ver} on {target} (throwaway venv)")
                    r = Shell.run(
                        [
                            Path(tmp) / "bin" / "python",
                            "-m",
                            "pip",
                            "install",
                            "--quiet",
                            "--no-deps",
                            f"{name}=={ver}",
                        ],
                        check=False,
                        quiet=True,
                    )
                    if r.returncode == 0:
                        built.append(f"{name} {ver}")
        still = [
            f"{n} {v} ({why})" for n, v, why in blockers if f"{n} {v}" not in built
        ]
        return still, built

    @classmethod
    def rebuild(cls, minor: str) -> None:
        Shell.run(
            [
                f"/opt/homebrew/opt/python@{minor}/bin/python{minor}",
                "-m",
                "venv",
                cls.VENV,
            ]
        )
        Shell.run([cls.PY, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
        Shell.run(
            [
                cls.PY,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--upgrade",
                "--upgrade-strategy",
                "eager",
                "-r",
                "requirements-dev.txt",
            ]
        )
        Shell.run([cls.PY, "tools/regen_lock.py"])


class Npm:
    PKG = "@playwright/test"

    @classmethod
    def plan(cls) -> tuple[str | None, str | None, str | None]:
        """(current, target within its major, latest)."""
        lock = json.loads((ROOT / "package-lock.json").read_text())
        current = lock["packages"].get(f"node_modules/{cls.PKG}", {}).get("version")
        versions = json.loads(
            Shell.out(["npm", "view", cls.PKG, "versions", "--json"]) or "[]"
        )
        latest = Shell.out(["npm", "view", cls.PKG, "version"]).strip() or None
        target = Version.newest(versions, current, same_major=True) if current else None
        return current, target, latest

    @staticmethod
    def packages() -> list[tuple[str, str]]:
        return NpmLock.packages((ROOT / "package-lock.json").read_text())

    @classmethod
    def upgrade(cls, target: str) -> None:
        Shell.run(["npm", "install", "--save-dev", f"{cls.PKG}@{target}"])
        Shell.run(["npx", "playwright", "install", "chromium", "webkit"])


class Foliate:
    DIR = ROOT / "static" / "vendor" / "foliate-js"

    @classmethod
    def behind(cls) -> int | None:
        if Shell.run(
            ["git", "fetch", "-q", "origin"], check=False, quiet=True, cwd=cls.DIR
        ).returncode:
            return None
        n = Shell.out(["git", "rev-list", "--count", "HEAD..origin/HEAD"], cwd=cls.DIR)
        return int(n) if n.strip().isdigit() else None

    @classmethod
    def bundled(cls) -> dict[str, str]:
        return FoliateVendor.bundled((cls.DIR / "package.json").read_text())


class Service:
    def __init__(self, settings: Settings) -> None:
        self.s = settings

    def restart(self) -> bool:
        """run.sh is launchd-aware (§8.19), so it is the one way to restart."""
        Shell.run(["./run.sh", "restart"], check=False)
        deadline = time.time() + 180  # restart scans the library first
        while time.time() < deadline:
            time.sleep(3)
            try:
                urllib.request.urlopen(self.s.health, timeout=5)
                print(f"   service answers {self.s.health}")
                return True
            except OSError:
                continue
        print(f"   ✗ service did not answer {self.s.health} within 180 s")
        return False

    def backup_db(self) -> Path:
        """SQLite's online backup — consistent while the service writes."""
        self.s.backups.mkdir(parents=True, exist_ok=True)
        dest = self.s.backups / f"books-{time.strftime('%Y%m%d-%H%M%S')}.db"
        with sqlite3.connect(self.s.db) as src, sqlite3.connect(dest) as out:
            src.backup(out)
        print(f"   backed up {self.s.db.name} → {dest}")
        return dest


class Gates:
    @staticmethod
    def pass_all(log: Path) -> bool:
        with log.open("w") as fh:
            for cmd in (
                [PythonEnv.PY, "-m", "pytest", "-q", "-p", "no:warnings"],
                ["npx", "playwright", "test"],
            ):
                print("   $ " + " ".join(map(str, cmd)), flush=True)
                if subprocess.run(
                    list(map(str, cmd)),
                    cwd=ROOT,
                    stdout=fh,
                    stderr=subprocess.STDOUT,
                    check=False,
                ).returncode:
                    tail = log.read_text(errors="replace")[-3000:]
                    print(f"   ✗ gate failed (full log: {log})\n{tail}")
                    return False
        return True
