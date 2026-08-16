"""Builds a minimal but structurally valid MOBI file.

MOBI is a PalmDB database: a 78-byte header, a record-offset table,
then the records themselves. Record 0 holds the PalmDOC header, the
MOBI header, and the EXTH block where everything interesting lives —
author, description, subjects, cover.

Enough of it is built here to read metadata back out. This is not a
MOBI *writer*: the text records are not compressed the way a real one
would be, because nothing under test reads them.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

# EXTH record types, from the MOBI format documentation.
EXTH_AUTHOR = 100
EXTH_PUBLISHER = 101
EXTH_DESCRIPTION = 103
EXTH_ISBN = 104
EXTH_SUBJECT = 105
EXTH_PUBLISHED = 106
EXTH_COVER_OFFSET = 201
EXTH_UPDATED_TITLE = 503


def jpeg(width: int = 60, height: int = 90) -> bytes:
    """A real, if dull, JPEG. The cover store rasterises what it is
    given, so bytes that merely start with the right magic number are
    not enough."""
    from io import BytesIO

    from PIL import Image

    buffer = BytesIO()
    Image.new("RGB", (width, height), (26, 42, 74)).save(buffer, "JPEG")
    return buffer.getvalue()


@dataclass
class MobiBuilder:
    title: str = "Test Book"
    authors: list[str] = field(default_factory=lambda: ["Test Author"])
    publisher: str | None = None
    description: str | None = None
    subjects: list[str] = field(default_factory=list)
    isbn: str | None = None
    published: str | None = None
    language: int = 9          # 9 = English, as MOBI stores it
    cover: bytes | None = None
    kf8: bool = False          # AZW3 announces itself as version 8
    updated_title: str | None = None
    header_length: int | None = None   # older files declare a short one

    text: str = "<html><body><p>Text.</p></body></html>"

    def build(self, path: Path) -> Path:
        # Order matters: text records come first, then resources. A
        # reader takes records 1..numTextRecords as the book, so a cover
        # filed in front of the text *is* the book as far as it knows —
        # which is exactly what foliate rendered when this was wrong.
        body = self.text.encode("utf-8")
        records = [b"", body]
        resource_start = len(records)
        cover_offset = None
        if self.cover is not None:
            cover_offset = len(records) - resource_start
            records.append(self.cover)

        records[0] = self._record_zero(cover_offset, len(body), resource_start)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self._palmdb(records))
        return path

    # ── record 0 ──────────────────────────────────────────────────────
    def _record_zero(self, cover_offset: int | None, text_length: int,
                     resource_start: int) -> bytes:
        exth = self._exth(cover_offset)
        title = self.title.encode("utf-8")

        # PalmDOC header: compression (1 = none), unused, text length,
        # number of text records, record size, encryption.
        palmdoc = struct.pack(">HHIHHHH", 1, 0, text_length, 1, 4096, 0, 0)

        # The MOBI header. Only the fields read back matter; the rest is
        # padded so the offsets are where a reader expects them.
        mobi = bytearray(232)
        mobi[0:4] = b"MOBI"
        struct.pack_into(">I", mobi, 4, self.header_length or 232)  # length
        struct.pack_into(">I", mobi, 8, 2)                    # mobi type
        struct.pack_into(">I", mobi, 12, 65001)               # utf-8
        struct.pack_into(">I", mobi, 20, 8 if self.kf8 else 6)  # version
        # Every offset in the MOBI documentation is counted from the
        # start of record 0, and the MOBI header begins 16 bytes in — so
        # a field documented at 84 is written at 84 - 16 here.
        title_offset = len(palmdoc) + len(mobi) + len(exth)
        struct.pack_into(">I", mobi, 84 - 16, title_offset)
        struct.pack_into(">I", mobi, 88 - 16, len(title))
        mobi[95 - 16] = self.language & 0xFF                  # locale language
        struct.pack_into(">I", mobi, 108 - 16, resource_start)  # first image
        struct.pack_into(">I", mobi, 128 - 16, 0x40)          # EXTH present

        return b"".join([palmdoc, bytes(mobi), exth, title])

    def _exth(self, cover_offset: int | None) -> bytes:
        entries: list[tuple[int, bytes]] = []
        for author in self.authors:
            entries.append((EXTH_AUTHOR, author.encode("utf-8")))
        if self.publisher:
            entries.append((EXTH_PUBLISHER, self.publisher.encode("utf-8")))
        if self.description:
            entries.append((EXTH_DESCRIPTION, self.description.encode("utf-8")))
        if self.isbn:
            entries.append((EXTH_ISBN, self.isbn.encode("utf-8")))
        for subject in self.subjects:
            entries.append((EXTH_SUBJECT, subject.encode("utf-8")))
        if self.published:
            entries.append((EXTH_PUBLISHED, self.published.encode("utf-8")))
        if cover_offset is not None:
            # Counted from the first resource record, not from record 0.
            entries.append((EXTH_COVER_OFFSET, struct.pack(">I", cover_offset)))
        if self.updated_title:
            entries.append((EXTH_UPDATED_TITLE,
                            self.updated_title.encode("utf-8")))

        body = b"".join(struct.pack(">II", kind, len(value) + 8) + value
                        for kind, value in entries)
        length = 12 + len(body)
        padding = (4 - length % 4) % 4
        return b"EXTH" + struct.pack(">II", length + padding,
                                     len(entries)) + body + b"\0" * padding

    # ── the container ─────────────────────────────────────────────────
    def _palmdb(self, records: list[bytes]) -> bytes:
        name = self.title.encode("ascii", "replace")[:31]
        header = name + b"\0" * (32 - len(name))
        header += struct.pack(">HHIIIIII", 0, 0, 0, 0, 0, 0, 0, 0)
        header += b"BOOK" + b"MOBI"
        header += struct.pack(">IIH", 0, 0, len(records))

        offset = len(header) + 8 * len(records) + 2
        table = b""
        for index, record in enumerate(records):
            table += struct.pack(">IBBH", offset, 0, 0, index)
            offset += len(record)
        return header + table + b"\0\0" + b"".join(records)
