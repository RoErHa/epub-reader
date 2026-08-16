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

    def build(self, path: Path) -> Path:
        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)          # object numbers start at 1

        page_ids = list(range(3, 3 + self.pages * 2, 2))
        add(b"<< /Type /Catalog /Pages 2 0 R >>")
        kids = " ".join(f"{n} 0 R" for n in page_ids)
        add(f"<< /Type /Pages /Kids [{kids}] /Count {self.pages} >>".encode())

        for index in range(self.pages):
            stream = self._page_stream(index)
            add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
                f" /Resources << /Font << /F1 {3 + self.pages * 2} 0 R >> >>"
                f" /Contents {4 + index * 2} 0 R >>".encode())
            add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                + stream + b"\nendstream")

        add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
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
