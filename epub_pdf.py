"""Reading what little a PDF will tell you about itself.

**The filename leads here, and the embedded metadata only gets a say
when it earns one.** That is the opposite of EPUB and MOBI, and it is
what the real library says: of 585 PDFs, 406 carry a `/Title`, and

    151  (empty)
     76  'Local Disk'
     39  'Book Cover' / 'BOOK COVER'
      5  'Contents'

— 129 distinct values across 585 files, most of them a scanner's idea of
a name. Meanwhile the filenames follow the same convention the repair
passes already understand: "Anthony, Piers - Mute.pdf".

No cover is extracted. Rasterising the first page needs a PDF renderer,
which is a heavyweight dependency for a format the reader still handles
as a proof-of-concept; the browse list falls back to a lettered tile as
it does for any book without one.

Only the Info dictionary is read, with a plain scan rather than a full
parser. Object streams and xref streams are not decoded — a PDF that
hides its Info dict inside one is simply a PDF with no usable metadata,
which is the same outcome as the 161 that have none at all.
"""
from __future__ import annotations

import re
from pathlib import Path

from epub_metadata import BookMetadata, MetadataError
from epub_names import PersonName, title_sort_key

# How much of a file to scan. The Info dictionary is small and sits
# near one end or the other; reading 633 MB of scanned comic to find it
# would not be a kindness.
HEAD = 2_000_000
TAIL = 2_000_000

INFO_FIELD = re.compile(
    rb"/(Title|Author|Subject|Keywords|CreationDate)\s*"
    rb"(?:\((?P<literal>(?:\\.|[^\\)])*)\)|<(?P<hex>[0-9A-Fa-f\s]*)>)",
    re.DOTALL,
)
PAGE_COUNT = re.compile(rb"/Type\s*/Pages\b[^>]*?/Count\s+(\d+)", re.DOTALL)
PDF_DATE = re.compile(r"D:(\d{4})(\d{2})?(\d{2})?")

# Names a scanner gives a file when nobody gave it one. Every one of
# these is a real value from the real library.
NOT_A_TITLE = {
    "local disk", "book cover", "cover", "contents", "title page",
    "untitled", "untitled document", "document", "unknown", "none",
    "microsoft word", "slide 1", "print", "chapter 1", "index",
}
# ...and the same for authors: a program is not a person.
NOT_AN_AUTHOR = {
    "adobe acrobat", "acrobat", "microsoft word", "word", "pdfcreator",
    "calibre", "abbyy finereader", "scansnap", "user", "administrator",
    "owner", "unknown", "none", "author",
}

# A converter's wrapper, with the real title inside it.
WRAPPER = re.compile(r"^(?:microsoft word|adobe indesign|quarkxpress)\s*[-–]\s*",
                     re.IGNORECASE)
FILE_ENDING = re.compile(
    r"\.(?:docx?|rtf|html?|txt|qxd|indd|pdf|pm\d|wpd|eps|ai|psd|jpe?g|png|tiff?)$",
    re.IGNORECASE)

# What a program calls a file it was given no name for.
PLACEHOLDER = re.compile(
    r"^(?:document|doc|untitled|book|page|slide|scan|image|file|new)\s*\d*$",
    re.IGNORECASE)


class PdfMetadataReader:
    """The PDF counterpart of EpubMetadataReader, with the same shape:
    give it a path, get a BookMetadata back."""

    def read(self, path: Path) -> BookMetadata:
        path = Path(path)
        try:
            with path.open("rb") as handle:
                head = handle.read(HEAD)
                size = path.stat().st_size
                if size > HEAD:
                    handle.seek(max(HEAD, size - TAIL))
                    tail = handle.read(TAIL)
                else:
                    tail = b""
        except FileNotFoundError as exc:
            raise MetadataError(f"{path.name} does not exist.") from exc
        except OSError as exc:
            raise MetadataError(f"{path.name} could not be read: {exc}") from exc

        # The header is not always at byte zero — linearised files and
        # careless downloads put rubbish in front of it.
        if b"%PDF-" not in head[:1024]:
            raise MetadataError(f"{path.name} is not a PDF.")

        data = head + tail
        info = self._info(data)

        meta = BookMetadata()
        meta.title = self._title(info.get("Title"), path)
        meta.title_sort = title_sort_key(meta.title)
        meta.authors = self._authors(info.get("Author"))
        meta.subjects = self._subjects(info)
        meta.page_count = self._pages(data)
        meta.spine_length = path.stat().st_size

        # A 2009 scan of a 1965 novel is not a 2009 book. §5 keeps those
        # two apart on purpose, so this only ever fills the edition date.
        meta.edition_date = self._date(info.get("CreationDate"))
        return meta

    # ── the Info dictionary ───────────────────────────────────────────
    def _info(self, data: bytes) -> dict[str, str]:
        found: dict[str, str] = {}
        for match in INFO_FIELD.finditer(data):
            key = match.group(1).decode("ascii")
            if key in found:
                continue                    # the first one wins
            if match.group("literal") is not None:
                value = self._literal(match.group("literal"))
            else:
                value = self._hex(match.group("hex"))
            if value:
                found[key] = value
        return found

    @staticmethod
    def _literal(raw: bytes) -> str:
        raw = re.sub(rb"\\([()\\])", rb"\1", raw)
        if raw.startswith(b"\xfe\xff"):
            return raw[2:].decode("utf-16-be", "replace").strip()
        # PDFDocEncoding is close enough to Latin-1 for names.
        return raw.decode("latin-1", "replace").strip()

    @staticmethod
    def _hex(raw: bytes) -> str:
        digits = re.sub(rb"\s", b"", raw or b"")
        if len(digits) % 2:
            digits += b"0"
        try:
            decoded = bytes.fromhex(digits.decode("ascii"))
        except ValueError:
            return ""
        if decoded.startswith(b"\xfe\xff"):
            return decoded[2:].decode("utf-16-be", "replace").strip()
        return decoded.decode("latin-1", "replace").strip()

    # ── judging what it said ──────────────────────────────────────────
    def _title(self, value: str | None, path: Path) -> str:
        cleaned = self._usable_title(value)
        if not cleaned:
            return path.stem
        # The filename leads only when it says something. "a.pdf" and
        # "rainbows.pdf" name nothing, so anything the file itself
        # offers is an improvement.
        if self._names_nothing(path.stem) or self._same_book(cleaned, path.stem):
            return cleaned
        return path.stem

    @classmethod
    def _names_nothing(cls, stem: str) -> bool:
        words = [w for w in re.findall(r"[a-z]{2,}", stem.lower())]
        return len(words) < 2 or bool(PLACEHOLDER.match(stem.strip()))

    @staticmethod
    def _same_book(title: str, stem: str) -> bool:
        """Whether an embedded title is this book, tidied — or another
        book altogether.

        These PDFs were assembled from other files and kept the wrong
        name inside: "Jody Lynn Nye - School Of Light" on a file called
        "Nye, Jody Lynn - Dreamland". The filename is the better source
        here, so the embedded one only gets to tidy it, never replace
        it, and it earns that by sharing most of its words.
        """
        skip = {"the", "a", "an", "of", "and", "or", "in", "on", "to", "by"}
        words = [w for w in re.findall(r"[a-z0-9]+", title.lower())
                 if w not in skip]
        if not words:
            return False
        # Compared against the *title* part of the filename — its last
        # segment — not the whole thing. "Jody Lynn Nye - School Of
        # Light" shares an author with "Nye, Jody Lynn - Dreamland" and
        # nothing else, and an author in common is not a book in common.
        known = set(re.findall(r"[a-z0-9]+", stem.rsplit(" - ", 1)[-1].lower()))
        shared = sum(1 for w in words if w in known)
        return shared / len(words) >= 0.6

    @classmethod
    def _usable_title(cls, value: str | None) -> str | None:
        if not value:
            return None
        text = WRAPPER.sub("", value.strip()).strip()
        text = FILE_ENDING.sub("", text).strip()
        if not text or text.lower() in NOT_A_TITLE or PLACEHOLDER.match(text):
            return None
        if not cls._readable(text):
            return None
        return text

    @staticmethod
    def _readable(text: str) -> bool:
        """Whether a string is words rather than wreckage.

        Scanned PDFs carry titles like "®7ÛTIãoXNáj-L", which decode
        without error and mean nothing. Letters and spaces should be
        most of a title.
        """
        if len(text) < 2:
            return False
        # Latin letters and spaces should be most of it. This is the one
        # rule here that is unfair to a language: a title written wholly
        # in Cyrillic or Japanese would be refused and the filename used
        # instead. Every filename in this library is Latin, and refusing
        # a real title costs less than accepting wreckage.
        plain = sum(("a" <= c.lower() <= "z") or c.isspace() for c in text)
        return plain / len(text) >= 0.6

    def _authors(self, value: str | None) -> list[PersonName]:
        if not value:
            return []
        text = value.strip()
        if not text or text.lower() in NOT_AN_AUTHOR or not self._readable(text):
            return []
        return PersonName.parse_many(text)

    @staticmethod
    def _subjects(info: dict[str, str]) -> list[str]:
        subjects: list[str] = []
        for key in ("Subject", "Keywords"):
            for part in re.split(r"[,;]", info.get(key, "")):
                part = part.strip()
                if part and part not in subjects:
                    subjects.append(part)
        return subjects

    @staticmethod
    def _pages(data: bytes) -> int:
        counts = [int(m.group(1)) for m in PAGE_COUNT.finditer(data)]
        return max(counts) if counts else 0

    @staticmethod
    def _date(value: str | None) -> str | None:
        if not value:
            return None
        match = PDF_DATE.match(value.strip())
        if not match:
            return None
        year, month, day = match.group(1), match.group(2), match.group(3)
        parts = [year]
        if month:
            parts.append(month)
        if day:
            parts.append(day)
        return "-".join(parts)
