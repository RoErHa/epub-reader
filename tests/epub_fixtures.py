"""Builders for synthetic EPUB files used across the test suite.

Kept as a class rather than helper functions so variants (scripted,
series metadata, multi-author) stay a matter of construction arguments.
"""
from __future__ import annotations

import base64
import zipfile
from xml.sax.saxutils import escape
from dataclasses import dataclass, field
from pathlib import Path

# A 1x1 PNG. Enough to make a section that has an image and no text,
# which is what a real book's cover page amounts to.
COVER_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

COVER_XHTML = '''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Cover</title></head>
<body><div><img src="cover.png" alt="Cover"/></div></body>
</html>
'''

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
    chapters: int = 1
    paragraphs_per_chapter: int = 3
    cover: bool = False
    # Real Calibre exports are often named "Author, Name - Title_split_0.html".
    # decodeURI() does not decode %2C, so that comma breaks href resolution.
    filename_prefix: str = ""
    # Calibre writes the NCX/nav href percent-encoded ("Name%2c A.html")
    # while the OPF manifest keeps the literal comma. resolveHref() uses
    # decodeURI(), which does not decode %2C, so the lookup misses.
    encode_nav_hrefs: bool = False

    SENTINEL = "__epub_script_executed"

    NAMES = [
        "Chapter One", "Chapter Two", "Chapter Three", "Chapter Four",
        "Chapter Five", "Chapter Six", "Chapter Seven", "Chapter Eight",
    ]

    def chapter_title(self, n: int) -> str:
        return self.NAMES[n] if n < len(self.NAMES) else f"Chapter {n + 1}"

    def chapter_file(self, n: int) -> str:
        return f"{self.filename_prefix}chapter{n + 1}.xhtml"

    def chapter_xhtml(self, n: int = 0) -> str:
        # Scripts only go in the first chapter — one hostile section is
        # enough to prove containment.
        script = ""
        if self.scripted and n == 0:
            script = (
                f'  <script>window.{self.SENTINEL} = true;</script>\n'
                f'  <script src="pwn.js"></script>\n'
            )
        body = "".join(
            f"  <p>The quick brown fox jumps over the lazy dog. "
            f"This is paragraph {i + 1} of {self.chapter_title(n)}, padded out "
            f"so that pagination has real work to do and more than one page "
            f"exists to turn. Lorem ipsum has no place here.</p>\n"
            for i in range(self.paragraphs_per_chapter)
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml">\n'
            f"<head><title>{self.chapter_title(n)}</title></head>\n"
            "<body>\n"
            f"{script}"
            f'  <h1 id="ch{n + 1}">{self.chapter_title(n)}</h1>\n'
            f"{body}"
            "</body>\n"
            "</html>\n"
        )

    def nav_href(self, n: int) -> str:
        href = self.chapter_file(n)
        return href.replace(",", "%2c") if self.encode_nav_hrefs else href

    def nav_xhtml(self) -> str:
        items = "".join(
            f'      <li><a href="{self.nav_href(n)}">'
            f"{self.chapter_title(n)}</a></li>\n"
            for n in range(self.chapters)
        )
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" '
            'xmlns:epub="http://www.idpf.org/2007/ops">\n'
            "<head><title>Contents</title></head>\n<body>\n"
            '  <nav epub:type="toc" id="toc"><h1>Contents</h1>\n'
            f"    <ol>\n{items}    </ol>\n  </nav>\n"
            "</body>\n</html>\n"
        )

    def content_opf(self) -> str:
        # Real books escape metadata; an unescaped "&" is invalid XML.
        subjects = "".join(
            f"    <dc:subject>{escape(s)}</dc:subject>\n" for s in self.subjects
        )
        meta = ""
        if self.series is not None:
            meta += f'    <meta name="calibre:series" content="{escape(self.series)}"/>\n'
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
        items = "".join(
            f'    <item id="ch{n + 1}" href="{self.chapter_file(n)}" '
            'media-type="application/xhtml+xml"/>\n'
            for n in range(self.chapters)
        )
        spine = "".join(
            f'    <itemref idref="ch{n + 1}"/>\n' for n in range(self.chapters)
        )
        if self.cover:
            items = (
                '    <item id="cover" href="cover.xhtml" '
                'media-type="application/xhtml+xml"/>\n'
                '    <item id="cover-img" href="cover.png" '
                'media-type="image/png" properties="cover-image"/>\n'
            ) + items
            spine = '    <itemref idref="cover"/>\n' + spine
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
            'unique-identifier="bookid">\n'
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
            f'    <dc:identifier id="bookid">{self.identifier}</dc:identifier>\n'
            f"    <dc:title>{escape(self.title)}</dc:title>\n"
            f"    <dc:creator>{escape(self.author)}</dc:creator>\n"
            f"    <dc:language>{self.language}</dc:language>\n"
            f"{subjects}{meta}"
            "  </metadata>\n"
            "  <manifest>\n"
            '    <item id="nav" href="nav.xhtml" '
            'media-type="application/xhtml+xml" properties="nav"/>\n'
            f"{items}{script_item}"
            "  </manifest>\n"
            f"  <spine>\n{spine}  </spine>\n"
            "</package>\n"
        )

    encrypted: list[str] = field(default_factory=list)
    """Files listed in META-INF/encryption.xml.

    Fonts there mean obfuscation, which every reader handles and which
    8 books in the real library use. Documents there mean DRM, which
    nothing can read — 50 books, all bought from Apple.
    """

    def build_unpacked(self, dest: Path) -> Path:
        """The same book, stored as a directory rather than a zip.

        265 of the real library look like this — iTunes exports, and
        anything somebody unzipped and never zipped again. The name
        still ends in .epub; only the container is different.
        """
        packed = dest.parent / (dest.name + ".packing")
        self.build(packed)
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(packed) as archive:
            archive.extractall(dest)
        packed.unlink()
        return dest

    def encryption_xml(self) -> str:
        """As Apple and Adobe write it: one EncryptedData per file."""
        blocks = "\n".join(
            '  <e:EncryptedData>\n'
            '    <e:EncryptionMethod Algorithm="http://itunes.apple.com/dataenc"/>\n'
            '    <e:CipherData>\n'
            f'      <e:CipherReference URI="{name}"/>\n'
            '    </e:CipherData>\n'
            '  </e:EncryptedData>' for name in self.encrypted)
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<c:encryption'
            ' xmlns:c="urn:oasis:names:tc:opendocument:xmlns:container"'
            ' xmlns:e="http://www.w3.org/2001/04/xmlenc#">\n'
            f'{blocks}\n</c:encryption>\n')

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
            if self.encrypted:
                zf.writestr("META-INF/encryption.xml", self.encryption_xml())
            zf.writestr("OEBPS/content.opf", self.content_opf())
            zf.writestr("OEBPS/nav.xhtml", self.nav_xhtml())
            if self.cover:
                zf.writestr("OEBPS/cover.xhtml", COVER_XHTML)
                zf.writestr("OEBPS/cover.png", COVER_PNG)
            for n in range(self.chapters):
                zf.writestr(f"OEBPS/{self.chapter_file(n)}", self.chapter_xhtml(n))
            if self.scripted:
                zf.writestr(
                    "OEBPS/pwn.js",
                    f"window.{self.SENTINEL}_external = true;\n",
                )
        return dest
