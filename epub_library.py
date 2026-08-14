"""Library scanning.

Answers one question at startup: what has appeared, vanished or changed
in the library folder since last time?

Detection is stat-based — name, size and mtime. This runs on every start,
and content-hashing a large library each time would be wasteful. M2's
index owns content hashing and permanent identity; this manifest is the
cheap change detector in front of it, and M2 will replace its JSON
storage with the books table while keeping this interface.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class BookFile:
    """A book as the filesystem currently presents it."""

    name: str
    size: int
    mtime: int

    def fingerprint(self) -> dict[str, int]:
        return {"size": self.size, "mtime": self.mtime}

    @classmethod
    def from_path(cls, path: Path) -> "BookFile":
        stat = path.stat()
        return cls(name=path.name, size=stat.st_size, mtime=int(stat.st_mtime))


@dataclass(frozen=True)
class ScanReport:
    """The difference between the library folder and the last manifest."""

    added: list[BookFile] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[BookFile] = field(default_factory=list)
    unchanged: int = 0
    first_run: bool = False

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def as_dict(self) -> dict[str, object]:
        return {
            "added": [b.name for b in self.added],
            "removed": list(self.removed),
            "changed": [b.name for b in self.changed],
            "unchanged": self.unchanged,
            "first_run": self.first_run,
        }

    @staticmethod
    def _books(count: int) -> str:
        return f"{count} book" if count == 1 else f"{count} books"

    def summary(self, sample: int = 8) -> str:
        if not self.has_changes:
            return f"Library unchanged — no changes, {self._books(self.unchanged)}."

        lines: list[str] = []
        if self.first_run:
            lines.append(f"First scan — {self._books(len(self.added))} found.")
        else:
            parts = []
            if self.added:
                parts.append(f"{len(self.added)} new")
            if self.changed:
                parts.append(f"{len(self.changed)} changed")
            if self.removed:
                parts.append(f"{len(self.removed)} removed")
            lines.append(
                f"Library updated — {', '.join(parts)}, {self.unchanged} unchanged."
            )

        for label, names in (
            ("+", [b.name for b in self.added]),
            ("~", [b.name for b in self.changed]),
            ("-", list(self.removed)),
        ):
            for name in names[:sample]:
                lines.append(f"  {label} {name}")
            if len(names) > sample:
                lines.append(f"  {label} …and {len(names) - sample} more")
        return "\n".join(lines)


class ScanManifest:
    """Remembers what the library looked like at the end of the last scan."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.is_file()

    def load(self) -> dict[str, dict] | None:
        """Previous fingerprints, or None if there is nothing usable.

        A corrupt manifest is treated as absent rather than fatal: the
        worst case is one scan reporting everything as new, which is
        recoverable, whereas refusing to start is not.
        """
        if not self.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            books = data.get("books")
            return books if isinstance(books, dict) else None
        except (json.JSONDecodeError, OSError, AttributeError):
            return None

    def save(self, books: dict[str, dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"scanned_at": int(time.time()), "books": books}
        self._path.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )


class LibraryScanner:
    """Compares the library folder against the manifest."""

    def __init__(self, library, manifest: ScanManifest) -> None:
        self._library = library
        self._manifest = manifest

    def current(self) -> dict[str, BookFile]:
        root = self._library.root
        if not root.is_dir():
            return {}
        return {
            p.name: BookFile.from_path(p)
            for p in sorted(root.iterdir())
            if p.is_file() and p.suffix.lower() in self._library.SUFFIXES
        }

    def scan(self) -> ScanReport:
        current = self.current()
        previous = self._manifest.load()
        first_run = previous is None
        previous = previous or {}

        added, changed, unchanged = [], [], 0
        for name, book in current.items():
            if name not in previous:
                added.append(book)
            elif previous[name] != book.fingerprint():
                changed.append(book)
            else:
                unchanged += 1

        removed = sorted(set(previous) - set(current))
        return ScanReport(
            added=added,
            removed=removed,
            changed=changed,
            unchanged=unchanged,
            first_run=first_run,
        )

    def record(self, books: dict[str, BookFile] | None = None) -> None:
        books = self.current() if books is None else books
        self._manifest.save({n: b.fingerprint() for n, b in books.items()})

    def scan_and_record(self) -> ScanReport:
        current = self.current()
        report = self.scan()
        self.record(current)
        return report

    @classmethod
    def for_config(cls, config, library) -> "LibraryScanner":
        """Manifest lives beside the database — it is internal state, not
        configuration, so it gets no .env key of its own."""
        manifest = ScanManifest(config.db_path.with_name("library-manifest.json"))
        return cls(library, manifest)
