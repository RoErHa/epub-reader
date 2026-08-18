"""Builds a minimal but valid PDF.

Small enough to read at a glance: a catalogue, a page tree, one page per
requested page, a Helvetica font, and an Info dictionary — which is
where the title and author live, and the only part the library cares
about. The cross-reference table is written by hand, so the offsets have
to be counted as the file is built.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PdfBuilder:
    title: str | None = "Test PDF"
    author: str | None = "Test Author"
    subject: str | None = None
    keywords: str | None = None
    created: str | None = None          # e.g. "D:20240115120000Z"
    pages: int = 2
    lines: list[str] = field(default_factory=lambda: ["Hello from a PDF."])
    linearised_junk: bool = False       # a header with leading rubbish
    # A scan of a physical cover: page one is one image and nothing else.
    # `cover_fraction` is how much of the page it covers, so a logo on a
    # title page can be told from a cover.
    cover: bytes | None = None          # JPEG bytes
    cover_fraction: float = 1.0
    cover_text: bool = False            # words over the image, e.g. OCR

    def build(self, path: Path) -> Path:
        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)          # object numbers start at 1

        page_ids = list(range(3, 3 + self.pages * 2, 2))
        font_id = 3 + self.pages * 2
        image_id = font_id + 1
        add(b"<< /Type /Catalog /Pages 2 0 R >>")
        kids = " ".join(f"{n} 0 R" for n in page_ids)
        add(f"<< /Type /Pages /Kids [{kids}] /Count {self.pages} >>".encode())

        for index in range(self.pages):
            first_and_covered = index == 0 and self.cover is not None
            stream = (self._cover_stream() if first_and_covered
                      else self._page_stream(index))
            resources = f"/Font << /F1 {font_id} 0 R >>"
            if first_and_covered:
                resources += f" /XObject << /Im0 {image_id} 0 R >>"
            add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
                f" /Resources << {resources} >>"
                f" /Contents {4 + index * 2} 0 R >>".encode())
            add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                + stream + b"\nendstream")

        add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        if self.cover is not None:
            add(b"<< /Type /XObject /Subtype /Image /Width 600 /Height 800"
                b" /ColorSpace /DeviceRGB /BitsPerComponent 8"
                b" /Filter /DCTDecode /Length "
                + str(len(self.cover)).encode() + b" >>\nstream\n"
                + self.cover + b"\nendstream")
        info = self._info()
        info_id = add(info) if info else None

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        if self.linearised_junk:
            out = bytearray(b"junk before the header\n") + out

        offsets = [0]
        for number, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

        start = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode()
        out += b"0000000000 65535 f \n"
        for offset in offsets[1:]:
            out += f"{offset:010d} 00000 n \n".encode()
        trailer = f"<< /Size {len(objects) + 1} /Root 1 0 R"
        if info_id:
            trailer += f" /Info {info_id} 0 R"
        trailer += " >>"
        out += f"trailer\n{trailer}\nstartxref\n{start}\n%%EOF\n".encode()

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(out))
        return path

    def _cover_stream(self) -> bytes:
        """Draw the image over the page, scaled by cover_fraction. A cover
        fills the page; a publisher's device on a title page does not."""
        width = 612 * self.cover_fraction
        height = 792 * self.cover_fraction
        parts = [f"q {width:.0f} 0 0 {height:.0f} 0 0 cm /Im0 Do Q"]
        if self.cover_text:
            parts += ["BT", "/F1 12 Tf", "72 700 Td",
                      "(An OCR layer, or a page of words photographed) Tj",
                      "0 -14 Td", "(and filed in front of the book itself.) Tj",
                      "ET"]
        return "\n".join(parts).encode("latin-1", "replace")

    def _page_stream(self, index: int) -> bytes:
        lines = [f"Page {index + 1}."] + list(self.lines)
        parts = ["BT", "/F1 18 Tf", "72 720 Td"]
        for line in lines:
            parts.append(f"({self._escape(line)}) Tj")
            parts.append("0 -24 Td")
        parts.append("ET")
        return "\n".join(parts).encode("latin-1", "replace")

    def _info(self) -> bytes | None:
        fields = []
        for key, value in (("Title", self.title), ("Author", self.author),
                           ("Subject", self.subject),
                           ("Keywords", self.keywords)):
            if value:
                fields.append(f"/{key} ({self._escape(value)})")
        if self.created:
            fields.append(f"/CreationDate ({self.created})")
        if not fields:
            return None
        return ("<< " + " ".join(fields) + " >>").encode("latin-1", "replace")

    @staticmethod
    def _escape(value: str) -> str:
        return value.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
