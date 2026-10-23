"""Generate the two lock files from the working `.venv`.

`requirements.txt` / `requirements-dev.txt` are the SPEC — loose ranges.
These are the INSTALL — the exact transitive closure, every package pinned:

    requirements.lock       runtime: the closure of requirements.txt
    requirements-dev.lock   `-r requirements.lock`, then only what the dev
                            tools pull in beyond it — one package, one pin

Ported from dlna-gateway-2.0, where the dev lock was added after an
upgrade moved certifi (a dependency of a dependency of a test tool) and no
tracked file recorded it. `tests/test_lock_sync.py` fails when either file
no longer matches what is installed.

    .venv/bin/python tools/regen_lock.py           # rewrite both
    .venv/bin/python tools/regen_lock.py --check   # exit 1 if stale

The dev lock is only written when every dev tool is installed: a
runtime-only venv must not overwrite it with a truncated set.
"""
from __future__ import annotations

import datetime
import importlib.metadata as md
import platform
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Requirements:
    """Reading a requirements file: which packages it names."""

    @staticmethod
    def norm(name: str) -> str:
        return name.lower().replace("_", "-")

    @staticmethod
    def names(text: str) -> list[str]:
        out = []
        for line in text.splitlines():
            spec = line.split("#", 1)[0].strip()
            if not spec or spec.startswith("-"):
                continue
            out.append(re.split(r"[=<>!~;\[ ]", spec, maxsplit=1)[0])
        return out


class Closure:
    """Everything installed that a set of root packages needs, name → version."""

    def __init__(self, roots: list[str]) -> None:
        self._seen: dict[str, str] = {}
        for root in roots:
            self._walk(root)

    def pins(self) -> dict[str, str]:
        return dict(self._seen)

    def _walk(self, name: str) -> None:
        key = Requirements.norm(name)
        if key in self._seen:
            return
        dist = self._distribution(name)
        if dist is None:
            return  # not installed here (an extra, or another platform's)
        self._seen[key] = dist.version
        for req in dist.requires or []:
            if ";" in req and "extra" in req.split(";", 1)[1]:
                continue
            base = re.split(r"[=<>!~;\[ (]", req.split(";")[0], maxsplit=1)[0].strip()
            if base:
                self._walk(base)

    @staticmethod
    def _distribution(name: str):
        for candidate in (name, Requirements.norm(name)):
            try:
                return md.distribution(candidate)
            except md.PackageNotFoundError:
                continue
        return None


class LockFile:
    """One lock: where it lives, what it should say, and whether it does."""

    HEADER = """# {name} — {what}
#
# GENERATED ARTIFACT — do not hand-edit. Regenerate after any dependency
# change:  .venv/bin/python tools/regen_lock.py
# Generated {date} from the working .venv
# (Python {python}, {system}/{machine}).
#
# {spec} is the SPEC (loose ranges); this is the INSTALL.
#
#     .venv/bin/pip install -r {name}
"""

    def __init__(self, path: Path, spec: Path, what: str,
                 include: LockFile | None = None) -> None:
        self.path, self.spec, self.what, self.include = path, spec, what, include

    @classmethod
    def runtime(cls) -> LockFile:
        return cls(ROOT / "requirements.lock", ROOT / "requirements.txt",
                   "EXACT runtime dependency set")

    @classmethod
    def dev(cls) -> LockFile:
        return cls(ROOT / "requirements-dev.lock", ROOT / "requirements-dev.txt",
                   "EXACT test and lint tooling set", include=cls.runtime())

    @staticmethod
    def pins(text: str) -> list[str]:
        return sorted(ln.split("#", 1)[0].strip() for ln in text.splitlines()
                      if ln.strip() and not ln.lstrip().startswith(("#", "-")))

    def roots(self) -> list[str]:
        return Requirements.names(self.spec.read_text())

    def missing_roots(self) -> list[str]:
        return [r for r in self.roots() if Closure._distribution(r) is None]

    def written(self) -> str:
        return self.path.read_text() if self.path.exists() else ""

    def expected(self) -> str:
        pins = Closure(self.roots()).pins()
        if self.include is not None:
            already = Closure(self.include.roots()).pins()
            pins = {k: v for k, v in pins.items() if k not in already}
        direct = {Requirements.norm(r) for r in self.roots()}
        header = self.HEADER.format(
            name=self.path.name, what=self.what, date=datetime.date.today(),
            python=platform.python_version(), system=platform.system(),
            machine=platform.machine(), spec=self.spec.name)
        include = f"\n-r {self.include.path.name}" if self.include else ""
        body = "\n".join(f"{k}=={pins[k]}" + ("        # direct" if k in direct else "")
                         for k in sorted(pins))
        return f"{header}{include}\n{body}\n"

    def is_current(self) -> bool:
        # Only the pins: the header's date and interpreter differ run to run.
        return self.pins(self.expected()) == self.pins(self.written())

    def write(self) -> None:
        self.path.write_text(self.expected())


class Regenerator:
    """The command line: rewrite both locks, or report whether they are stale."""

    def __init__(self, argv: list[str]) -> None:
        self.check = "--check" in argv

    def run(self) -> int:
        locks = [LockFile.runtime()]
        dev = LockFile.dev()
        if dev.missing_roots():
            print(f"dev tools not installed ({', '.join(dev.missing_roots())}) — "
                  f"{dev.path.name} left as it is", file=sys.stderr)
        else:
            locks.append(dev)
        stale = 0
        for lock in locks:
            if self.check:
                current = lock.is_current()
                stale += not current
                print(f"{lock.path.name} is {'up to date' if current else 'STALE'}")
            else:
                lock.write()
                print(f"wrote {lock.path.name} ({len(lock.pins(lock.written()))} pins)")
        return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(Regenerator(sys.argv[1:]).run())
