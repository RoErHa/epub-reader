"""The pure decisions behind tools/upgrade.py — no network, no subprocess.

Split from the side-effecting half so every rule that must never be wrong
is tested directly (tests/test_upgrade.py). STANDARD LIBRARY ONLY: the
upgrade moves `.venv` aside while it runs, so nothing here may import from
it.
"""

from __future__ import annotations

import json
import re
from typing import ClassVar


class EnvFile:
    """Reads `.env` — the ONE configuration file — without python-dotenv,
    which lives in the venv this tool moves aside. Same KEY=value lines,
    `export` prefix and quotes the app accepts."""

    @staticmethod
    def parse(text: str) -> dict[str, str]:
        out = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.removeprefix("export ").partition("=")
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            out[key.strip()] = value
        return out


class Version:
    """Comparing versions from three package managers that disagree on format."""

    @staticmethod
    def key(v: str) -> tuple[int, ...]:
        """Brew-tolerant: '9.0.2_1' > '9.0.2', '2026-10-20' compares by date."""
        return tuple(int(p) for p in re.findall(r"\d+", v))

    @staticmethod
    def stable(v: str) -> tuple[int, ...] | None:
        """A plain release as a tuple; a pre-release (rc, beta, canary) → None."""
        return (
            tuple(int(p) for p in v.split("."))
            if re.fullmatch(r"\d+(\.\d+)*", v)
            else None
        )

    @classmethod
    def newest(cls, versions, current: str, same_major: bool) -> str | None:
        """The newest stable release above `current`, or None when current is it."""
        cur = cls.stable(current)
        if cur is None:
            return None
        found = [t for t in (cls.stable(v) for v in versions) if t]
        if same_major:
            found = [t for t in found if t[0] == cur[0]]
        best = max(found, default=None)
        return ".".join(map(str, best)) if best and best > cur else None


class BrewPlan:
    """`brew outdated --json=v2`, split into what to upgrade and what to HOLD.

    Held = brew's stable is OLDER than what is installed: the formula was
    repointed to another release line (openssl@3 → the 3.5 LTS, seen on
    dlna-gateway 2026-10-23), so `brew upgrade` would DOWNGRADE. That is a
    person's decision; the upgrade pins it for the duration."""

    def __init__(self, formulae: list[dict]) -> None:
        self.upgrades: list[str] = []
        self.held: list[str] = []
        self.lines: list[str] = []
        for f in formulae:
            have = max(f["installed_versions"], key=Version.key)
            kegs = ", ".join(f["installed_versions"])
            line = f"{f['name']} {kegs} → {f['current_version']}"
            if Version.key(f["current_version"]) < Version.key(have):
                self.held.append(line + " is a DOWNGRADE — held")
            else:
                self.upgrades.append(f["name"])
                self.lines.append(line)


class Wheel:
    """Would a package install on a new Python minor without compiling?"""

    @staticmethod
    def fits(filename: str, minor: tuple[int, int]) -> bool:
        """Apple-silicon macOS, CPython `minor`: a pure wheel, a wheel for that
        exact CPython, or a stable-ABI (abi3) wheel from an older minor. The
        free-threaded ABI (cp315t) is not this interpreter."""
        if not filename.endswith(".whl"):
            return False
        parts = filename[:-4].split("-")
        if len(parts) < 5:
            return False
        pys, abi, plats = parts[-3].split("."), parts[-2], parts[-1].split(".")
        if not any(
            p == "any"
            or (p.startswith("macosx") and p.endswith(("arm64", "universal2")))
            for p in plats
        ):
            return False
        cp = f"cp{minor[0]}{minor[1]}"
        if abi == "none":
            return any(t in ("py3", f"py{minor[0]}{minor[1]}", cp) for t in pys)
        if abi == cp:
            return True
        if abi == "abi3":
            return any(
                re.fullmatch(r"cp3(\d+)", t) and int(t[3:]) <= minor[1] for t in pys
            )
        return False

    @classmethod
    def blockers(
        cls, files_by_pkg: dict[str, tuple[str, list[str]]], minor: tuple[int, int]
    ) -> list[tuple[str, str, str]]:
        """(name, version, why) for every package with no installable wheel.
        'source only' is a RISK, not a verdict — upgrade.py test-builds it."""
        out = []
        for name, (ver, files) in sorted(files_by_pkg.items()):
            if any(cls.fits(f, minor) for f in files):
                continue
            why = (
                "source only"
                if any(f.endswith((".tar.gz", ".zip")) for f in files)
                else "nothing installable"
            )
            out.append((name, ver, why))
        return out


class NpmLock:
    """package-lock.json → every installed package, by its real name."""

    @staticmethod
    def packages(text: str) -> list[tuple[str, str]]:
        out = set()
        for path, meta in json.loads(text).get("packages", {}).items():
            if path and meta.get("version"):
                out.add((path.rsplit("node_modules/", 1)[-1], meta["version"]))
        return sorted(out)


class FoliateVendor:
    """The libraries foliate-js carries inside itself (it vendors them under
    vendor/, and declares them in its own package.json). They ship to every
    reader, so they are checked like our own — but foliate-js is pinned and
    updated deliberately (§3), so they are never upgraded from here."""

    BUNDLED = ("pdfjs-dist", "@zip.js/zip.js", "fflate")

    @classmethod
    def bundled(cls, package_json: str) -> dict[str, str]:
        data = json.loads(package_json)
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        return {name: deps[name].lstrip("^~=") for name in cls.BUNDLED if name in deps}


class OsvAnswer:
    """An osv.dev querybatch answer, paired with the questions asked."""

    def __init__(self, asked: list[tuple[str, str, str]], answer: dict) -> None:
        self.asked, self.results = asked, answer.get("results", [])

    def findings(self) -> dict[str, list[str]]:
        if len(self.results) != len(self.asked):
            # A short answer must never read as "nothing is vulnerable".
            raise ValueError(
                f"osv.dev answered {len(self.results)} of {len(self.asked)}"
            )
        out = {}
        for (eco, name, ver), res in zip(self.asked, self.results, strict=True):
            ids = sorted(v["id"] for v in (res or {}).get("vulns", []))
            if ids:
                out[f"{name} {ver} ({eco})"] = ids
        return out


class Acknowledged:
    """Advisories a person INVESTIGATED and found not to apply here, each
    with the reason. Matching is by version, and a version match can be
    wrong (a bundler may strip the vulnerable code), but the tool cannot
    see that; a person can, once, and writes down why. An advisory not on
    this record — even a second one for the same package — still surfaces.
    """

    RECORD: ClassVar[dict[str, str]] = {
        "GHSA-px8p-9vwx-vf98": (
            "CVE-2026-45820, fflate unzipSync() ZIP64 infinite loop. foliate-js "
            "bundles fflate as `export { unzlibSync } from 'fflate'` "
            "(rollup/fflate.js); the shipped vendor/fflate.js holds neither "
            "unzipSync nor z64e (checked 2026-10-23). EPUBs are unzipped by zip.js."
        ),
    }

    def __init__(self, record: dict[str, str] | None = None) -> None:
        self.record = self.RECORD if record is None else record
        if any(not reason.strip() for reason in self.record.values()):
            raise ValueError("an acknowledgement must say WHY it does not apply")

    def reason(self, ids: list[str]) -> str | None:
        """The reason, when EVERY advisory in a finding is acknowledged."""
        if ids and all(i in self.record for i in ids):
            return "; ".join(dict.fromkeys(self.record[i] for i in ids))
        return None


class Attention:
    """What a person should do about each finding.

    DO      safe to apply; --apply does it and the gates prove it
    DECIDE  a person's call (a held downgrade; a Python minor that is ready;
            a pinned dependency that has moved on)
    WAIT    should NOT be done yet — and why
    NOTE    information

    The weekly check pings on a DO, or on a DECIDE/WAIT that was not there
    last time. A ping that repeats the same thing every week trains you to
    ignore it (dlna-gateway, the first Saturday that never came)."""

    KINDS = ("DO", "DECIDE", "WAIT", "NOTE")

    def __init__(self) -> None:
        self._items: list[tuple[str, str]] = []

    def add(self, kind: str, text: str) -> None:
        if kind not in self.KINDS:
            raise ValueError(f"unknown attention kind {kind!r}")
        self._items.append((kind, text))

    def ordered(self) -> list[tuple[str, str]]:
        return sorted(self._items, key=lambda item: self.KINDS.index(item[0]))

    def summary(self) -> str:
        return " · ".join(
            f"{sum(k == kind for k, _ in self._items)} {kind.lower()}"
            for kind in self.KINDS
        )

    def watched(self) -> list[str]:
        return [f"{k} {t}" for k, t in self._items if k in ("DECIDE", "WAIT")]

    def new_since(self, previous: list[str]) -> list[str]:
        seen = set(previous)
        return [item for item in self.watched() if item not in seen]

    def should_notify(self, previous: list[str]) -> bool:
        return any(k == "DO" for k, _ in self._items) or bool(self.new_since(previous))
