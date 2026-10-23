"""The "what is this actually built from" strip in README.md.

Every badge is READ from the thing it describes, never typed:

  Runtime   python · sqlite · openssl   the .venv interpreter — sqlite and
                                        openssl are what IT loads, Homebrew
                                        libraries, not part of Python
            fastapi · … · pypdfium2     requirements.lock
            pdfium                      the build pypdfium2 carries — the
                                        engine that actually parses a PDF
            libarchive                  macOS's /usr/bin/bsdtar, which
                                        unpacks RAR comics
  Reader    foliate-js                  the submodule commit this repo pins
            pdf.js · zip.js · fflate    what foliate-js's own package.json
                                        says it bundles
  Tooling   node                        the Homebrew Node the UI suite runs on
            @playwright/test · ruff     package-lock.json, requirements-dev.lock
  Security  open · acknowledged         osv.dev on --write (tools/upgrade.py's
                                        own check, Acknowledged included)

PINNED versions (the locks, package-lock.json, the submodule) are the same
on every clone and gated by tests/test_versions.py. PLATFORM versions move
with Homebrew and macOS, so tools/upgrade.py watches those: a stale strip
is a DO item, and a passing --apply rewrites it (CLAUDE.md §10.18).

    .venv/bin/python tools/versions.py            # report; exit 1 if stale
    .venv/bin/python tools/versions.py --write    # rewrite (asks osv.dev)
"""

from __future__ import annotations

import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.regen_lock import LockFile  # noqa: E402
from tools.upgrade_checks import FoliateVendor, NpmLock  # noqa: E402

FOLIATE = "static/vendor/foliate-js"


class Stack:
    """Collects the versions: `pinned()` from tracked files, `platform()`
    from this machine."""

    PINNED_PKGS = (  # (label, lock, package)
        ("fastapi", "requirements.lock", "fastapi"),
        ("starlette", "requirements.lock", "starlette"),
        ("hypercorn", "requirements.lock", "hypercorn"),
        ("pillow", "requirements.lock", "pillow"),
        ("pypdfium2", "requirements.lock", "pypdfium2"),
        ("ruff", "requirements-dev.lock", "ruff"),
    )
    PROBE = (
        "import sys,sqlite3,ssl,pypdfium2.version as v;"
        "print(sys.version.split()[0],sqlite3.sqlite_version,"
        "ssl.OPENSSL_VERSION.split()[1],v.PDFIUM_INFO)"
    )
    BUNDLED = (
        ("pdf.js", "pdfjs-dist"),
        ("zip.js", "@zip.js/zip.js"),
        ("fflate", "fflate"),
    )

    @staticmethod
    def _out(argv: list[str]) -> str:
        try:
            r = subprocess.run(
                argv, capture_output=True, text=True, timeout=30, check=False, cwd=ROOT
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return r.stdout

    @classmethod
    def pinned(cls) -> dict:
        pins = {}
        for name in ("requirements.lock", "requirements-dev.lock"):
            for p in LockFile.pins((ROOT / name).read_text()):
                pkg, _, ver = p.partition("==")
                pins[pkg] = ver
        stack = {label: pins.get(pkg) for label, _, pkg in cls.PINNED_PKGS}
        npm = dict(NpmLock.packages((ROOT / "package-lock.json").read_text()))
        stack["@playwright/test"] = npm.get("@playwright/test")
        tree = cls._out(["git", "ls-tree", "HEAD", FOLIATE]).split()
        stack["foliate-js"] = tree[2][:7] if len(tree) >= 3 else None
        bundled = FoliateVendor.bundled((ROOT / FOLIATE / "package.json").read_text())
        for label, pkg in cls.BUNDLED:
            stack[label] = bundled.get(pkg)
        return stack

    @classmethod
    def platform(cls) -> dict:
        stack = {}
        py = str(ROOT / ".venv" / "bin" / "python")
        probe = cls._out(
            [
                py,
                "-c",
                cls.PROBE,
            ]
        ).split()
        if len(probe) >= 4:
            stack.update(python=probe[0], sqlite=probe[1], openssl=probe[2])
            # PDFIUM_INFO reads like 153.0.7999.0: the build is the third field.
            build = probe[3].split(".")
            stack["pdfium"] = f"build {build[2]}" if len(build) > 2 else probe[3]
        m = re.search(r"libarchive (\S+)", cls._out(["/usr/bin/bsdtar", "--version"]))
        stack["libarchive"] = f"{m.group(1)} · macOS" if m else None
        node = cls._out(["node", "--version"]).strip().lstrip("v")
        stack["node"] = node or None
        return stack

    @classmethod
    def security(cls) -> tuple[int, int, str]:
        """(open, acknowledged, date): the same osv.dev check upgrade.py runs."""
        from tools.upgrade_checks import Acknowledged
        from tools.upgrade_layers import Foliate, Npm, Osv, PythonEnv

        asked = [("PyPI", n, v) for n, v in sorted(PythonEnv.installed().items())]
        asked += [("npm", n, v) for n, v in Npm.packages()]
        asked += [("npm", n, v) for n, v in Foliate.bundled().items()]
        found = Osv.check(asked)
        ack = Acknowledged()
        acked = sum(1 for ids in found.values() if ack.reason(ids) is not None)
        return len(found) - acked, acked, datetime.date.today().isoformat()


class VersionStrip:
    BEGIN = "<!-- versions: generated by tools/versions.py -->"
    END = "<!-- /versions -->"
    SHIELDS = "https://img.shields.io/badge"
    ROWS = (  # (row title, colour, [(label, link)])
        (
            "Runtime",
            "blue",
            [
                ("python", "https://www.python.org/"),
                ("sqlite", "https://sqlite.org/"),
                ("openssl", "https://openssl-library.org/"),
                ("fastapi", "https://pypi.org/project/fastapi/"),
                ("starlette", "https://pypi.org/project/starlette/"),
                ("hypercorn", "https://pypi.org/project/hypercorn/"),
                ("pillow", "https://pypi.org/project/pillow/"),
                ("pypdfium2", "https://pypi.org/project/pypdfium2/"),
                ("pdfium", "https://pdfium.googlesource.com/pdfium/"),
                ("libarchive", "https://libarchive.org/"),
            ],
        ),
        (
            "Reader",
            "blue",
            [
                ("foliate-js", "https://github.com/johnfactotum/foliate-js"),
                ("pdf.js", "https://github.com/mozilla/pdf.js"),
                ("zip.js", "https://github.com/gildas-lormeau/zip.js"),
                ("fflate", "https://github.com/101arrowz/fflate"),
            ],
        ),
        (
            "Tooling",
            "lightgrey",
            [
                ("node", "https://nodejs.org/"),
                ("@playwright/test", "https://playwright.dev/"),
                ("ruff", "https://docs.astral.sh/ruff/"),
            ],
        ),
    )
    PINNED = (
        "fastapi",
        "starlette",
        "hypercorn",
        "pillow",
        "pypdfium2",
        "ruff",
        "@playwright/test",
        "foliate-js",
        "pdf.js",
        "zip.js",
        "fflate",
    )

    def __init__(self, stack: dict) -> None:
        self.stack = stack

    @staticmethod
    def quote(text: str) -> str:
        """shields.io: `-` and `_` are field separators and are DOUBLED."""
        return (
            text.replace("-", "--")
            .replace("_", "__")
            .replace(" ", "%20")
            .replace("·", "%C2%B7")
            .replace("/", "%2F")
            .replace("@", "%40")
        )

    def badge(self, label: str, message: str, colour: str, link: str) -> str:
        slug = f"{self.quote(label)}-{self.quote(message)}-{colour}"
        return f"[![{label} {message}]({self.SHIELDS}/{slug}.svg)]({link})"

    def render(self) -> str:
        lines = [self.BEGIN]
        for title, colour, items in self.ROWS:
            badges = [
                self.badge(label, self.stack[label], colour, link)
                for label, link in items
                if self.stack.get(label)
            ]
            if badges:
                lines.append(f"**{title}** " + " ".join(badges) + "  ")
        if self.stack.get("security"):
            open_, acked, date = self.stack["security"]
            colour = "red" if open_ else "brightgreen"
            lines.append(
                "**Security** "
                + self.badge(
                    "known vulnerabilities",
                    f"{open_} open · {acked} acknowledged · osv.dev {date}",
                    colour,
                    "#keeping-the-toolchain-current",
                )
            )
        lines.append(self.END)
        return "\n".join(lines)

    def pinned_badges(self) -> list[str]:
        every = re.findall(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)", self.render())
        return [b for b in every if any(b.startswith(f"[![{p} ") for p in self.PINNED)]


class Readme:
    PATH = ROOT / "README.md"

    def current(self) -> str:
        text = self.PATH.read_text()
        start, end = text.find(VersionStrip.BEGIN), text.find(VersionStrip.END)
        if start == -1 or end == -1:
            raise SystemExit("README.md has no version markers")
        return text[start : end + len(VersionStrip.END)]

    def write(self, strip: str) -> bool:
        current = self.current()
        if current == strip:
            return False
        self.PATH.write_text(self.PATH.read_text().replace(current, strip, 1))
        return True

    @staticmethod
    def security_in(strip: str) -> tuple[int, int, str] | None:
        """Read the last osv.dev result back, to re-render without the network."""
        m = re.search(
            r"(\d+)%20open%20%C2%B7%20(\d+)%20acknowledged%20%C2%B7%20"
            r"osv.dev%20(\d{4}--\d{2}--\d{2})",
            strip,
        )
        if not m:
            return None
        return int(m.group(1)), int(m.group(2)), m.group(3).replace("--", "-")


class Cli:
    @staticmethod
    def main(argv: list[str]) -> int:
        readme = Readme()
        stack = {**Stack.pinned(), **Stack.platform()}
        if "--write" in argv:
            try:
                stack["security"] = Stack.security()
            except (OSError, ValueError) as e:
                print(f"osv.dev unreachable ({e}) — keeping the last security badge")
                stack["security"] = Readme.security_in(readme.current())
            changed = readme.write(VersionStrip(stack).render())
            print(
                "Version strip updated."
                if changed
                else "Version strip was already right."
            )
            return 0
        stack["security"] = Readme.security_in(readme.current())
        if readme.current() == VersionStrip(stack).render():
            print("Version strip is current.")
            return 0
        print(
            "Version strip is STALE — run: .venv/bin/python tools/versions.py --write"
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(Cli.main(sys.argv[1:]))
