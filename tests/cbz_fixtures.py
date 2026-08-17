"""Builds a comic archive: a zip of images, which is all a CBZ is.

Some carry a ComicInfo.xml from ComicRack — 1 in 120 of the real ones —
so that is optional here too.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path


def page(width: int = 40, height: int = 60, shade: int = 200) -> bytes:
    from PIL import Image
    buffer = BytesIO()
    Image.new("RGB", (width, height), (shade, shade, shade)).save(buffer, "JPEG")
    return buffer.getvalue()


@dataclass
class CbzBuilder:
    pages: int = 3
    series: str | None = None          # ComicInfo.xml, when present
    number: str | None = None
    writer: str | None = None
    summary: str | None = None
    year: str | None = None
    extras: list[str] = field(default_factory=list)   # non-image files

    def build(self, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w") as archive:
            if self.series or self.writer:
                archive.writestr("ComicInfo.xml", self._info())
            for index in range(self.pages):
                # Named as scanners name them, and out of order on
                # purpose: page 10 must not come before page 2.
                archive.writestr(f"{index + 1:03d}.jpg",
                                 page(shade=200 - index * 10))
            for name in self.extras:
                archive.writestr(name, b"not a page")
        return dest

    def _info(self) -> str:
        fields = []
        for tag, value in (("Series", self.series), ("Number", self.number),
                           ("Writer", self.writer), ("Summary", self.summary),
                           ("Year", self.year)):
            if value:
                fields.append(f"  <{tag}>{value}</{tag}>")
        return ("<?xml version=\"1.0\"?>\n<ComicInfo>\n"
                + "\n".join(fields) + "\n</ComicInfo>\n")
