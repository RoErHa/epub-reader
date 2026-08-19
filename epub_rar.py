"""Comics that were packed with RAR.

295 of them, and every one invisible until now: 103 of the Doctor
Strange run, 63 Sandman, all 27 Science Comics, the Matt Groening. §2.1
excluded CBR because "nothing in the stack reads RAR", and that stopped
being true — **macOS ships `/usr/bin/bsdtar`, which is libarchive, and
libarchive reads RAR**. No new dependency, nothing to install, nothing
proprietary.

Three things the real archives decided:

> **Listing is cheap and extracting is not.** `-tv` reads the headers and
> decompresses nothing, so names and sizes cost nothing. Reading the
> cover therefore asks for **one entry**, not for the archive: the
> largest comic here is 315 MB, and unpacking all of it to look at page
> one would be 315 MB held to produce a thumbnail.

> **Extracting to stdout, never to disk.** No temporary directory, no
> permissions, no path that could climb out of one. `-tv` gives every
> entry's size in order and `-xO` gives every entry's bytes in the same
> order, so the stream splits exactly — checked against a 315 MB comic,
> byte for byte.

> **libarchive reads most RAR, not all.** 4 in 60 sampled are refused
> with *"Prefix found: Inappropriate file type or format"*, and all four
> are one run of 2003-vintage archives. A book that cannot be unpacked
> is reported and skipped, like any other unreadable file (§3.2) — it
> does not take a scan down.

> **It reads RAR and cannot write it**, and neither can anything else
> here, so there is no `RarBuilder` beside `EpubBuilder` and `CbzBuilder`
> and there cannot be. The **process** is the injected boundary — tests
> fake what bsdtar writes, so what is tested is our reading of it, which
> is where the bugs are. The tool itself is measured against the real
> library (§8.15).

A comic packed with RAR is packed again as a zip on the way out, which is
§8.5's trick with different pictures: foliate reads a zip, and the
reader, the positions and the highlights carry on knowing nothing.
"""
from __future__ import annotations

import io
import logging
import subprocess
import zipfile
from pathlib import Path

LOG = logging.getLogger("epub-reader.rar")

# libarchive, as macOS ships it. Named rather than found on PATH, so
# what runs is what was tested.
BSDTAR = "/usr/bin/bsdtar"

# `Rar!\x1a\x07\x00` is RAR 4 and `...\x07\x01\x00` is RAR 5; the first
# six bytes settle it, and libarchive reads both.
MAGIC = b"Rar!\x1a\x07"

PAGE_TYPES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")


class RarError(Exception):
    """The archive could not be read, and why."""


def _run(argv: list[str]) -> bytes:
    """bsdtar, once. Stdout back, or `RarError` with what it said."""
    try:
        result = subprocess.run([BSDTAR, *argv], capture_output=True,
                                timeout=300, check=False)
    except FileNotFoundError as exc:
        raise RarError(
            f"{BSDTAR} is not on this machine, so RAR comics cannot be read"
            " here. It ships with macOS; on Linux install libarchive-tools."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RarError("it took too long to unpack.") from exc
    if result.returncode != 0:
        first = (result.stderr or b"").decode("utf-8", "replace").strip()
        raise RarError(first.splitlines()[0] if first
                       else "bsdtar could not read it")
    return result.stdout


class RarArchive:
    """A RAR, with the same manners as `ZipContents` (§8.5): it answers
    `names()` and `read()` and knows nothing about books."""

    def __init__(self, path, run=_run) -> None:
        self._path = Path(path)
        self._run = run
        self._listing: list[tuple[str, int]] | None = None

    @staticmethod
    def is_rar(path) -> bool:
        """What the file *is*, never what it is called. Two comics here
        are named `.cbz` and are RAR inside (§2.1)."""
        try:
            with Path(path).open("rb") as handle:
                return handle.read(len(MAGIC)) == MAGIC
        except OSError:
            return False

    # ── what is inside ────────────────────────────────────────────────
    def listing(self) -> list[tuple[str, int]]:
        """Every entry and its size, in archive order. Headers only —
        nothing is decompressed to answer this."""
        if self._listing is None:
            out = self._run(["-tvf", str(self._path)])
            entries = []
            for line in out.decode("utf-8", "replace").splitlines():
                # `drwxr-xr-x 0 0 0 0 29 mrt. 2003 Some Dir/`
                fields = line.split(maxsplit=8)
                if len(fields) < 9 or fields[0].startswith("d"):
                    continue        # a directory holds no pages
                try:
                    size = int(fields[4])
                except ValueError:
                    continue
                entries.append((fields[8], size))
            self._listing = entries
        return self._listing

    def names(self) -> list[str]:
        return sorted(name for name, _ in self.listing())

    def pages(self) -> list[str]:
        """The images, in order. Scanners pad their filenames, so sorted
        really does put 002 before 010 (§8.7)."""
        return [n for n in self.names()
                if n.lower().endswith(PAGE_TYPES)
                and not n.startswith("__MACOSX")]

    # ── getting the bytes ─────────────────────────────────────────────
    def read(self, name: str) -> bytes:
        """One entry. Asking for a cover must not cost the archive."""
        if name not in dict(self.listing()):
            raise KeyError(name)
        return self._run(["-xOf", str(self._path), "--", name])

    def read_all(self) -> dict[str, bytes]:
        """Every entry, in one pass.

        `-xO` writes the entries end to end with nothing between them,
        and `-tv` has already given their sizes in the same order — so
        the stream splits exactly. Verified against a 315 MB comic.
        """
        entries = self.listing()
        blob = self._run(["-xOf", str(self._path)])
        expected = sum(size for _, size in entries)
        if len(blob) != expected:
            raise RarError(
                f"unpacked {len(blob)} bytes where the listing promised "
                f"{expected}; refusing to guess where the pages divide")
        out, at = {}, 0
        for name, size in entries:
            out[name] = blob[at:at + size]
            at += size
        return out


def repack(archive: RarArchive) -> bytes:
    """A comic packed with RAR, packed as a zip.

    Only the pages travel: a comic is its pages, and a scanner's
    `notes.txt` arriving mid-run is something the reader would have to
    decide about.
    """
    entries = archive.read_all()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as out:
        # STORED, not DEFLATED: these are JPEGs, already compressed, and
        # deflating them again spends time to save nothing.
        for name in archive.pages():
            out.writestr(name, entries[name])
    return buffer.getvalue()
