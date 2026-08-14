"""Builders for synthetic EPUB files used across the test suite.

Kept as a class rather than helper functions so variants (scripted,
series metadata, multi-author) stay a matter of construction arguments.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

NAV_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Contents</title></head>
<body>
  <nav epub:type="toc" id="toc"><h1>Contents</h1>
    <ol><li><a href="chapter1.xhtml">Chapter One</a></li></ol>
  </nav>
</body>
</html>
"""


@dataclass
class EpubBuilder:
    """Builds a minimal but structurally valid EPUB 3 file.

    `scripted=True` embeds JavaScript that sets a global sentinel. The
    reader must never execute it — that is what the CSP rule exists for,
    and asserting on the sentinel is how we prove the rule holds.
    """

    title: str = "Test Book"
    author: str = "Test Author"
    identifier: str = "urn:uuid:test-0000"
    language: str = "en"
    scripted: bool = False
    subjects: list[str] = field(default_factory=list)
    series: str | None = None
    series_index: float | None = None
    first_published: str | None = None

    SENTINEL = "__epub_script_executed"

    def chapter_xhtml(self) -> str:
        script = ""
        if self.scripted:
            script = (
                f'  <script>window.{self.SENTINEL} = true;</script>\n'
                f'  <script src="pwn.js"></script>\n'
            )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml">\n'
            f"<head><title>{self.title}</title></head>\n"
            "<body>\n"
            f"{script}"
            '  <h1 id="ch1">Chapter One</h1>\n'
            "  <p>The quick brown fox jumps over the lazy dog.</p>\n"
            "  <p>A second paragraph, so pagination has something to do.</p>\n"
            "</body>\n"
            "</html>\n"
        )

    def content_opf(self) -> str:
        subjects = "".join(
            f"    <dc:subject>{s}</dc:subject>\n" for s in self.subjects
        )
        meta = ""
        if self.series is not None:
            meta += f'    <meta name="calibre:series" content="{self.series}"/>\n'
        if self.series_index is not None:
            meta += (
                '    <meta name="calibre:series_index" '
                f'content="{self.series_index}"/>\n'
            )
        if self.first_published is not None:
            meta += f"    <dc:date>{self.first_published}</dc:date>\n"
        script_item = ""
        if self.scripted:
            script_item = (
                '    <item id="pwn" href="pwn.js" '
                'media-type="text/javascript"/>\n'
            )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="bookid">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'    <dc:identifier id="bookid">{self.identifier}</dc:identifier>\n'
            f"    <dc:title>{self.title}</dc:title>\n"
            f"    <dc:creator>{self.author}</dc:creator>\n"
            f"    <dc:language>{self.language}</dc:language>\n"
            f"{subjects}{meta}"
            "  </metadata>\n"
            "  <manifest>\n"
            '    <item id="nav" href="nav.xhtml" '
            'media-type="application/xhtml+xml" properties="nav"/>\n'
            '    <item id="ch1" href="chapter1.xhtml" '
            'media-type="application/xhtml+xml"/>\n'
            f"{script_item}"
            "  </manifest>\n"
            '  <spine>\n    <itemref idref="ch1"/>\n  </spine>\n'
            "</package>\n"
        )

    def build(self, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            # The spec requires `mimetype` first and stored uncompressed.
            zf.writestr(
                zipfile.ZipInfo("mimetype"),
                "application/epub+zip",
                compress_type=zipfile.ZIP_STORED,
            )
            zf.writestr("META-INF/container.xml", CONTAINER_XML)
            zf.writestr("OEBPS/content.opf", self.content_opf())
            zf.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            zf.writestr("OEBPS/chapter1.xhtml", self.chapter_xhtml())
            if self.scripted:
                zf.writestr(
                    "OEBPS/pwn.js",
                    f"window.{self.SENTINEL}_external = true;\n",
                )
        return dest
