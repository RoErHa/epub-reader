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

**Covers come from page one, but only when page one is one.** M7 left
this out — rasterising a page needs a renderer, which was a heavy
dependency for a format then handled as a proof-of-concept. At 514 PDFs
showing a lettered tile that reasoning expired, and pdfium is BSD and
ships as a wheel with no build step.

Measured over the real library: **158 of 514 open on a raster image that
covers the page with no text over it**, and those are the scans of the
physical cover. The other 356 open on a title page, which is a worse
tile than the letter it would replace — so they keep the letter.

Only the Info dictionary is read, with a plain scan rather than a full
parser. Object streams and xref streams are not decoded — a PDF that
hides its Info dict inside one is simply a PDF with no usable metadata,
which is the same outcome as the 161 that have none at all.
"""
from __future__ import annotations

import contextlib
import logging
import re
from pathlib import Path

from epub_metadata import BookMetadata, MetadataError
from epub_names import PersonName, title_sort_key

LOG = logging.getLogger("epub-reader.pdf")

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


class PdfCoverRenderer:
    """Page one of a PDF, when page one is the cover.

    The signal is what the page *is*, not what the file is: one raster
    image covering the page, with no text layer over it. That keeps the
    scans and the text-born books that carry a real cover image, and
    refuses a publisher's title page — which is the shape 356 of the
    library's PDFs have.

    pdfium is imported where it is used, so a library without it indexes
    exactly as before, one lettered tile the poorer.
    """

    # A cover fills the page. Margins are common in text-born books, so
    # half is enough to mean "this page is the picture".
    COVERS_THE_PAGE = 0.5
    # Words over the image mean the page is a page. A stray character
    # from a scanner's stamp should not cost a cover.
    QUIET = 40
    WIDTH = 400                 # px, the widest a browse tile is drawn
    QUALITY = 82
    # A flat colour is a flyleaf, not a picture of anything.
    FLAT = 4.0

    def cover(self, path) -> bytes | None:
        """JPEG bytes, or None when page one is not a cover. Never
        raises: this runs inside a scan of thousands of books."""
        try:
            import pypdfium2 as pdfium
            import pypdfium2.raw as raw
        except ImportError:                     # pragma: no cover
            LOG.info("pypdfium2 is not installed; PDFs keep their tiles")
            return None

        document = None
        try:
            document = pdfium.PdfDocument(Path(path))
            if len(document) == 0:
                return None
            page = document[0]
            if not self._is_a_cover(page, raw):
                return None
            image = page.render(scale=self._scale(page)).to_pil()
            return self._jpeg(image)
        except Exception as exc:
            # Any malformed file, any pdfium error. One bad book must
            # never stop a scan — §3.2.
            LOG.info("no cover from %s: %s", Path(path).name,
                     type(exc).__name__)
            return None
        finally:
            if document is not None:
                with contextlib.suppress(Exception):
                    document.close()

    # ── is this a cover? ──────────────────────────────────────────────
    def _is_a_cover(self, page, raw) -> bool:
        width, height = page.get_size()
        area = width * height
        if area <= 0:
            return False
        biggest = 0.0
        for obj in page.get_objects():
            if obj.type != raw.FPDF_PAGEOBJ_IMAGE:
                continue
            left, bottom, right, top = obj.get_bounds()
            biggest = max(biggest, abs(right - left) * abs(top - bottom) / area)
        if biggest < self.COVERS_THE_PAGE:
            return False
        text = page.get_textpage().get_text_bounded().strip()
        return len(text) <= self.QUIET

    def _scale(self, page) -> float:
        width = page.get_size()[0] or self.WIDTH
        return max(0.1, min(4.0, self.WIDTH / width))

    def _jpeg(self, image) -> bytes | None:
        import io

        image = image.convert("RGB")
        if self._flat(image):
            return None
        if image.width != self.WIDTH:
            ratio = self.WIDTH / image.width
            image = image.resize(
                (self.WIDTH, max(1, round(image.height * ratio))))
        out = io.BytesIO()
        image.save(out, "JPEG", quality=self.QUALITY)
        return out.getvalue()

    def _flat(self, image) -> bool:
        """A scanner photographs the flyleaf too, and a white tile says
        less than the letter it replaced."""
        small = image.resize((32, 32))
        pixels = list(small.getdata())
        greys = [(r * 299 + g * 587 + b * 114) / 1000 for r, g, b in pixels]
        mean = sum(greys) / len(greys)
        spread = (sum((g - mean) ** 2 for g in greys) / len(greys)) ** 0.5
        return spread < self.FLAT


class PdfMetadataReader:
    """The PDF counterpart of EpubMetadataReader, with the same shape:
    give it a path, get a BookMetadata back."""

    def __init__(self, covers: PdfCoverRenderer | None = None) -> None:
        self._covers = covers or PdfCoverRenderer()

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

        cover = self._covers.cover(path)
        if cover:
            meta.cover_data = cover
            meta.cover_suffix = ".jpg"
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
        words = re.findall(r"[a-z]{2,}", stem.lower())
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
            for raw in re.split(r"[,;]", info.get(key, "")):
                part = raw.strip()
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
