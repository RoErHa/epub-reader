"""Comic archives.

A CBZ is a zip of images and nothing else. 309 of them are here, and
only one in 120 carries a `ComicInfo.xml` — so the **filename is the
metadata**, and these filenames are full of the scanner's own notes:

    Zootopia 001 (2025) (5 covers) (digital) (Salem-Empire).cbz

The issue number is worth keeping, because it is what orders a run of
comics: `Shadowman 004` is the fourth Shadowman, so the series is
"Shadowman" and the number is 4. Everything in brackets is the scanner
talking, and the year among it is the *edition* — a 2025 scan of a 1985
comic is not a 1985 work, which is the distinction §5 exists for.

Where a `ComicInfo.xml` is present it beats the filename, because
somebody wrote it on purpose.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from epub_metadata import BookMetadata, MetadataError
from epub_names import PersonName, title_sort_key

PAGE_TYPES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")

# "(2025)", "(digital)", "(Salem-Empire)" — the scanner's notes, and
# never part of what the comic is called.
BRACKETED = re.compile(r"\s*[(\[][^)\]]*[)\]]")
YEAR = re.compile(r"[(\[](\d{4})[)\]]")
# "Shadowman 004" — a trailing number is the issue.
ISSUE = re.compile(r"^(?P<series>.+?)\s+#?(?P<number>\d{1,4}(?:\.\d+)?)$")


class CbzMetadataReader:
    """The comic counterpart of EpubMetadataReader, with the same shape:
    give it a path, get a BookMetadata back."""

    def read(self, path: Path) -> BookMetadata:
        path = Path(path)
        try:
            if path.open("rb").read(4) == b"Rar!":
                # Two real ones are named .cbz and are RAR inside. Say
                # which it is, rather than "not a comic archive" — the
                # reader can rename it, and nothing here reads RAR.
                raise MetadataError(
                    f"{path.name} is a RAR archive named .cbz; nothing here"
                    " reads RAR.")
        except FileNotFoundError as exc:
            raise MetadataError(f"{path.name} does not exist.") from exc

        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                pages = sorted(n for n in names
                               if n.lower().endswith(PAGE_TYPES)
                               and not n.startswith("__MACOSX"))
                if not pages:
                    raise MetadataError(f"{path.name} holds no pages.")
                meta = self._from_name(path)
                meta.page_count = len(pages)
                meta.spine_length = path.stat().st_size
                self._cover(archive, pages[0], meta)
                self._comic_info(archive, names, meta)
        except zipfile.BadZipFile as exc:
            raise MetadataError(f"{path.name} is not a comic archive.") from exc
        except FileNotFoundError as exc:
            raise MetadataError(f"{path.name} does not exist.") from exc
        except MetadataError:
            raise
        except OSError as exc:
            raise MetadataError(f"{path.name} could not be read: {exc}") from exc

        meta.title_sort = title_sort_key(meta.title)
        return meta

    # ── the filename ──────────────────────────────────────────────────
    def _from_name(self, path: Path) -> BookMetadata:
        meta = BookMetadata()
        stem = path.stem
        year = YEAR.search(stem)
        if year:
            # The year of the scan, not of the comic. §5 keeps those apart.
            meta.edition_date = year.group(1)

        cleaned = re.sub(r"\s+", " ", BRACKETED.sub("", stem)).strip() or stem
        meta.title = cleaned

        issue = ISSUE.match(cleaned)
        if issue:
            meta.series = issue.group("series").strip(" -–—")
            meta.series_index = float(issue.group("number"))
        return meta

    # ── the pages ─────────────────────────────────────────────────────
    @staticmethod
    def _cover(archive: zipfile.ZipFile, first: str, meta: BookMetadata) -> None:
        data = archive.read(first)
        if data:
            meta.cover_data = data
            meta.cover_suffix = Path(first).suffix.lower()

    # ── ComicInfo.xml, when somebody wrote one ────────────────────────
    def _comic_info(self, archive: zipfile.ZipFile, names: list[str],
                    meta: BookMetadata) -> None:
        found = next((n for n in names
                      if n.lower().endswith("comicinfo.xml")), None)
        if not found:
            return
        try:
            root = ElementTree.fromstring(archive.read(found))
        except ElementTree.ParseError:
            return                      # a broken note is not a broken comic

        fields = {self._tag(child): (child.text or "").strip()
                  for child in root}
        if fields.get("series"):
            meta.series = fields["series"]
            meta.title = fields["series"]
            if fields.get("number"):
                try:
                    meta.series_index = float(fields["number"])
                    meta.title = f"{fields['series']} {fields['number']}"
                except ValueError:
                    pass
        if fields.get("writer"):
            meta.authors = PersonName.parse_many(fields["writer"])
        if fields.get("summary"):
            meta.description = fields["summary"]
        if fields.get("year") and not meta.edition_date:
            meta.edition_date = fields["year"]

    @staticmethod
    def _tag(element) -> str:
        tag = element.tag
        return tag.rsplit("}", 1)[-1].lower()
