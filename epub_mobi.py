"""Reading metadata out of MOBI, AZW3 and PRC files.

The *reader* gets these for free: foliate-js sniffs the container and
parses it itself, so nothing in `static/` changes. The **library** does
not — title, author and cover live in a PalmDB record rather than an
OPF, and this is the one piece MOBI support actually costs.

A MOBI file is a PalmDB database: a 78-byte header naming the database,
then a table of record offsets, then the records. Record 0 carries a
PalmDOC header, a MOBI header, and an EXTH block of typed key/value
entries — which is where everything interesting is. Only the fields the
library shows are read; the text records are left alone, because
foliate is what renders them.

Nothing here raises for a book that is merely odd. A scan of 1,300
books must not stop because one file is truncated, so anything
unreadable becomes a MetadataError, which the indexer already catches
per book.
"""
from __future__ import annotations

import html
import re
import struct
from pathlib import Path

from epub_metadata import BookMetadata, MetadataError
from epub_names import PersonName, title_sort_key

# The PalmDB types a MOBI can announce itself as. AZW3 and PRC use the
# same container; the extension is a hint, not the fact.
PALM_TYPES = (b"BOOKMOBI", b"TEXtREAd")

PALM_HEADER = 78
RECORD_ENTRY = 8

# Offsets **within record 0**, which is how the MOBI documentation
# numbers them and how foliate-js reads them. Numbering them from the
# "MOBI" magic instead puts the title and the locale 16 bytes out: the
# titles still came back, because most files repeat the title in the
# EXTH block, but every locale read as nonsense — 209 and 206 are not
# languages.
MAGIC_AT = 16
HEADER_LENGTH_AT = 20
VERSION_AT = 36
TITLE_OFFSET_AT = 84
TITLE_LENGTH_AT = 88
LOCALE_REGION_AT = 94
LOCALE_LANGUAGE_AT = 95

# EXTH record types worth reading, from the MOBI format documentation.
AUTHOR = 100
PUBLISHER = 101
DESCRIPTION = 103
ISBN = 104
SUBJECT = 105
PUBLISHED = 106
COVER_OFFSET = 201
UPDATED_TITLE = 503

# MOBI stores a Windows language id rather than a BCP 47 tag. Only the
# common ones are mapped: a number nobody mapped is left as nothing
# rather than guessed at, which is the same rule the genre map follows.
LANGUAGES = {
    9: "en", 1033: "en", 2057: "en-GB",
    12: "fr", 7: "de", 10: "es", 16: "it", 19: "nl", 22: "pt",
    25: "ru", 29: "sv", 30: "th", 31: "tr", 6: "da", 20: "hr",
    5: "cs", 8: "el", 13: "he", 14: "hu", 21: "pl", 24: "ro",
    17: "ja", 18: "ko", 4: "zh", 11: "fi", 15: "is", 20504: "nb",
}

COVER_KINDS = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF8", ".gif"),
    (b"RIFF", ".webp"),
)


class MobiMetadataReader:
    """The MOBI counterpart of EpubMetadataReader, with the same shape:
    give it a path, get a BookMetadata back."""

    def read(self, path: Path) -> BookMetadata:
        path = Path(path)
        try:
            data = path.read_bytes()
        except FileNotFoundError as exc:
            raise MetadataError(f"{path.name} does not exist.") from exc
        except OSError as exc:
            raise MetadataError(f"{path.name} could not be read: {exc}") from exc

        records = self._records(data, path.name)
        record0 = records[0]
        exth = self._exth(record0)

        meta = BookMetadata()
        meta.title = self._title(record0, exth) or path.stem
        meta.title_sort = title_sort_key(meta.title)
        meta.authors = [person for raw in exth.get(AUTHOR, [])
                        if (person := PersonName.parse(self._text(raw)))]
        meta.publisher = self._first(exth, PUBLISHER)
        meta.description = self._plain(self._first(exth, DESCRIPTION))
        meta.subjects = [s for raw in exth.get(SUBJECT, [])
                         if (s := self._text(raw).strip())]
        meta.isbn13 = self._isbn(self._first(exth, ISBN))
        meta.language = self._language(record0)
        meta.spine_length = sum(len(r) for r in records[1:])

        published = self._first(exth, PUBLISHED)
        if published:
            meta.edition_date = published
            year = re.match(r"\s*(\d{4})", published)
            if year:
                meta.first_published = int(year.group(1))

        cover = self._cover(records, exth)
        if cover:
            meta.cover_data, meta.cover_suffix = cover
        return meta

    # ── the container ─────────────────────────────────────────────────
    def _records(self, data: bytes, name: str) -> list[bytes]:
        if len(data) < PALM_HEADER + RECORD_ENTRY:
            raise MetadataError(f"{name} is too short to be a MOBI file.")
        if data[60:68] not in PALM_TYPES:
            raise MetadataError(f"{name} is not a MOBI file.")

        count = struct.unpack_from(">H", data, 76)[0]
        if not count:
            raise MetadataError(f"{name} holds no records.")
        needed = PALM_HEADER + count * RECORD_ENTRY
        if len(data) < needed:
            raise MetadataError(f"{name} ends in the middle of its records.")

        offsets = [struct.unpack_from(">I", data, PALM_HEADER + i * RECORD_ENTRY)[0]
                   for i in range(count)]
        records = []
        for index, start in enumerate(offsets):
            end = offsets[index + 1] if index + 1 < count else len(data)
            if start > len(data) or end < start:
                raise MetadataError(f"{name} has a record pointing nowhere.")
            records.append(data[start:end])
        return records

    def _exth(self, record0: bytes) -> dict[int, list[bytes]]:
        """The EXTH block: typed entries, several of some types — a book
        with two authors carries two author records."""
        start = record0.find(b"EXTH")
        if start < 0 or len(record0) < start + 12:
            return {}
        count = struct.unpack_from(">I", record0, start + 8)[0]
        entries: dict[int, list[bytes]] = {}
        cursor = start + 12
        for _ in range(count):
            if cursor + 8 > len(record0):
                break                      # truncated: keep what was read
            kind, length = struct.unpack_from(">II", record0, cursor)
            if length < 8 or cursor + length > len(record0):
                break
            entries.setdefault(kind, []).append(record0[cursor + 8:cursor + length])
            cursor += length
        return entries

    # ── the fields ────────────────────────────────────────────────────
    @staticmethod
    def _has_header(record0: bytes) -> bool:
        return record0[MAGIC_AT:MAGIC_AT + 4] == b"MOBI"

    def _title(self, record0: bytes, exth: dict) -> str:
        """The EXTH title first: the PalmDB name is capped at 31 bytes,
        so a long title is truncated there and written again here."""
        updated = self._first(exth, UPDATED_TITLE)
        if updated:
            return updated

        if not self._has_header(record0) or len(record0) < TITLE_LENGTH_AT + 4:
            return ""
        offset, length = struct.unpack_from(">II", record0, TITLE_OFFSET_AT)
        if not length or offset + length > len(record0):
            return ""
        return self._text(record0[offset:offset + length]).strip()

    def _language(self, record0: bytes) -> str | None:
        """The locale, when the header is long enough to have one.

        Older files declare a shorter header that stops before this
        field, and reading past its end gives a number that means
        nothing — several real books produced 209 and 206, which are not
        languages. A number nobody mapped is left as nothing rather than
        guessed at, the same rule the genre map follows.
        """
        if not self._has_header(record0) or len(record0) <= LOCALE_LANGUAGE_AT:
            return None
        declared = struct.unpack_from(">I", record0, HEADER_LENGTH_AT)[0]
        if declared + MAGIC_AT <= LOCALE_LANGUAGE_AT:
            return None                       # an older, shorter header
        return LANGUAGES.get(record0[LOCALE_LANGUAGE_AT])

    def _cover(self, records: list[bytes], exth: dict) -> tuple[bytes, str] | None:
        """The cover is an image record, named by its offset from the
        first one. Where that is not readable, the first record that
        looks like an image will do — a book with a cover we cannot
        name is still a book with a cover."""
        raw = self._first_raw(exth, COVER_OFFSET)
        images = [r for r in records[1:] if self._kind(r)]
        if raw and len(raw) >= 4:
            index = struct.unpack(">I", raw[:4])[0]
            if index < len(images):
                chosen = images[index]
                return chosen, self._kind(chosen)
        return (images[0], self._kind(images[0])) if images else None

    @staticmethod
    def _kind(record: bytes) -> str | None:
        for magic, suffix in COVER_KINDS:
            if record.startswith(magic):
                return suffix
        return None

    # ── tidying ───────────────────────────────────────────────────────
    @staticmethod
    def _text(raw: bytes) -> str:
        # MOBI says UTF-8 or CP1252 in its header, and lies about it
        # often enough that guessing per string is more reliable.
        for encoding in ("utf-8", "cp1252", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")

    def _first(self, exth: dict, kind: int) -> str | None:
        values = exth.get(kind)
        return self._text(values[0]).strip() if values else None

    @staticmethod
    def _first_raw(exth: dict, kind: int) -> bytes | None:
        values = exth.get(kind)
        return values[0] if values else None

    @staticmethod
    def _plain(value: str | None) -> str | None:
        """Publishers put markup in the description; the app shows text."""
        if not value:
            return None
        text = re.sub(r"<br\s*/?>|</p>", " ", value, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        return re.sub(r"\s+", " ", html.unescape(text)).strip() or None

    @staticmethod
    def _isbn(value: str | None) -> str | None:
        if not value:
            return None
        digits = re.sub(r"[^0-9Xx]", "", value)
        return digits if len(digits) == 13 else None
