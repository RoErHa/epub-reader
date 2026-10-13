"""A PalmDOC book, handed to the reader as a MOBI.

Six PRC files here are the older PalmDB container: they announce
themselves as `TEXtREAd`, and their record 0 is a bare PalmDOC header
with no MOBI header after it. What they hold is Mobipocket's HTML all
the same — `<mbp:pagebreak>`, `filepos` links — so the only thing
between them and the reader is the label. foliate opens `BOOKMOBI` and
nothing else (the `TEXtREAd` half of its check is commented out), and
said "File type not supported".

> **The container is relabelled on the way out, and the text is not
> touched.** Record 0 keeps its PalmDOC header — compression, length,
> the number of text records — and gains the MOBI header foliate reads:
> no EXTH, no index, no huffman tables, no trailing entries, pictures
> after the text. Every other record is passed over byte for byte. It is
> §8.15's rule for a RAR comic: the reader, positions and highlights
> carry on knowing nothing about it, and the file on disk is never
> written.
"""
from __future__ import annotations

import struct
from pathlib import Path

PALM_HEADER = 78
RECORD_ENTRY = 8
TYPE_AT = 60

PALMDOC = b"TEXtREAd"
MOBI = b"BOOKMOBI"

# The MOBI header as foliate reads it: 232 bytes from offset 16 of
# record 0. Every offset below is counted from the start of record 0,
# as the MOBI documentation counts them (§8.3).
MOBI_LENGTH = 232
WINDOWS_1252 = 1252     # what Palm-era Mobipocket wrote
VERSION = 6             # MOBI 6: not KF8, so no fragments or skeletons
NONE = 0xFFFFFFFF       # "no such record"


class PalmDoc:
    def __init__(self, path) -> None:
        self._path = Path(path)

    @staticmethod
    def is_palmdoc(path) -> bool:
        path = Path(path)
        if not path.is_file():
            return False
        with path.open("rb") as handle:
            handle.seek(TYPE_AT)
            return handle.read(len(PALMDOC)) == PALMDOC

    def as_mobi(self) -> bytes:
        data = self._path.read_bytes()
        count = struct.unpack_from(">H", data, 76)[0]
        offsets = [struct.unpack_from(">I", data, PALM_HEADER + RECORD_ENTRY * i)[0]
                   for i in range(count)] + [len(data)]
        records = [data[offsets[i]:offsets[i + 1]] for i in range(count)]
        records[0] = self._record_zero(records[0], data[:32])

        header = bytearray(data[:PALM_HEADER])
        header[TYPE_AT:TYPE_AT + len(MOBI)] = MOBI
        table = bytearray()
        offset = PALM_HEADER + RECORD_ENTRY * count + 2
        for index in range(count):
            # Attributes and unique id are kept; only the offset moves,
            # because record 0 has grown.
            entry = bytearray(data[PALM_HEADER + RECORD_ENTRY * index:
                                   PALM_HEADER + RECORD_ENTRY * (index + 1)])
            struct.pack_into(">I", entry, 0, offset)
            table += entry
            offset += len(records[index])
        return bytes(header + table + b"\0\0" + b"".join(records))

    @staticmethod
    def _record_zero(zero: bytes, name: bytes) -> bytes:
        # The PalmDOC header's first twelve bytes are what it means;
        # the four after them are filler in these files, and the MOBI
        # header's own encryption field is the one foliate looks at.
        palmdoc = zero[:12] + b"\0\0\0\0"
        texts = struct.unpack_from(">H", palmdoc, 8)[0]
        title = name.split(b"\0", 1)[0]

        mobi = bytearray(MOBI_LENGTH)
        mobi[0:4] = b"MOBI"

        def put(at: int, value: int) -> None:
            struct.pack_into(">I", mobi, at - 16, value)

        put(20, MOBI_LENGTH)
        put(24, 2)                       # a book
        put(28, WINDOWS_1252)
        put(36, VERSION)
        put(80, texts + 1)               # first record that is not text
        put(84, 16 + MOBI_LENGTH)        # the title follows the header
        put(88, len(title))
        put(108, texts + 1)              # pictures follow the text
        put(112, 0)                      # no huffman tables
        put(128, 0)                      # no EXTH
        put(240, 0)                      # no trailing entries
        put(244, NONE)                   # no index
        padding = b"\0" * ((4 - len(title) % 4) % 4 + 4)
        return palmdoc + bytes(mobi) + title + padding
