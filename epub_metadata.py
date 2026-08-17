"""Reading metadata out of an EPUB.

Handles EPUB 2 and 3, since the real library is full of both, and treats
every kind of breakage as a MetadataError so one bad book can never stop
an index run.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from epub_names import PersonName, title_sort_key

CONTAINER = "META-INF/container.xml"
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg"}


class MetadataError(Exception):
    """The book could not be read. Always caught by the indexer."""


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


@dataclass
class BookMetadata:
    title: str = ""
    title_sort: str = ""
    subtitle: str | None = None
    authors: list[PersonName] = field(default_factory=list)
    language: str | None = None
    publisher: str | None = None
    description: str | None = None
    subjects: list[str] = field(default_factory=list)
    isbn13: str | None = None
    edition_date: str | None = None
    first_published: int | None = None
    series: str | None = None
    series_index: float | None = None
    spine_length: int = 0
    # Pages, where a format counts them: PDFs and comics do, EPUBs do
    # not — a reflowable book has as many pages as your screen decides.
    page_count: int = 0
    cover_data: bytes | None = None
    cover_suffix: str | None = None


# Files whose encryption means obfuscation rather than DRM. Mangling a
# font is ordinary and every reader handles it; 8 books here do it,
# including the Earthsea novels.
FONT_TYPES = (".otf", ".ttf", ".woff", ".woff2", ".eot")

# What a file says when it does not know what language it is in.
NOT_A_LANGUAGE = {"", "und", "unknown", "xx", "zxx", "mul", "n/a", "none"}


class DrmError(MetadataError):
    """The book is encrypted, and the key is not in the file.

    50 books in the real library are Apple Books purchases. Nothing here
    or anywhere else can open them, so listing them would promise what
    we cannot deliver.
    """


class ZipContents:
    """The files inside a zipped EPUB."""

    def __init__(self, archive: zipfile.ZipFile) -> None:
        self._archive = archive

    def names(self) -> list[str]:
        return self._archive.namelist()

    def read(self, name: str) -> bytes:
        return self._archive.read(name)


class FolderContents:
    """The files inside an EPUB that was never zipped.

    265 books in the real library are directories — iTunes exports, and
    anything somebody unzipped and never put back. The name still ends
    in .epub and the layout is identical, so the parser only needs to
    be told where to read from.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def names(self) -> list[str]:
        return [str(p.relative_to(self._root).as_posix())
                for p in self._root.rglob("*") if p.is_file()]

    def read(self, name: str) -> bytes:
        target = (self._root / name).resolve()
        # A book must not be able to name a file outside itself.
        if not str(target).startswith(str(self._root.resolve())):
            raise MetadataError(f"{name} is outside the book.")
        return target.read_bytes()


class EpubMetadataReader:
    def read(self, path: Path) -> BookMetadata:
        path = Path(path)
        if path.is_dir():
            return self._read_book(FolderContents(path), path)
        try:
            with zipfile.ZipFile(path) as zf:
                return self._read_book(ZipContents(zf), path)
        except zipfile.BadZipFile as exc:
            raise MetadataError(f"{path.name} is not a valid EPUB archive.") from exc
        except FileNotFoundError as exc:
            raise MetadataError(f"{path.name} does not exist.") from exc
        except MetadataError:
            raise
        except Exception as exc:  # noqa: BLE001 — one book must not stop a run
            raise MetadataError(f"{path.name}: {type(exc).__name__}: {exc}") from exc

    # ── internals ─────────────────────────────────────────────────────
    def _read_book(self, contents, path: Path) -> BookMetadata:
        zf = contents            # a zip or a folder; both answer the same way
        names = contents.names()
        self._refuse_if_locked(contents, names, path)
        container = next((n for n in names if n.endswith(CONTAINER)), None)
        if container is None:
            raise MetadataError(f"{path.name} has no META-INF/container.xml.")

        match = re.search(r'full-path="([^"]+)"', zf.read(container).decode(
            "utf-8", "replace"))
        if not match:
            raise MetadataError(f"{path.name}: container names no rootfile.")
        opf_path = match.group(1)
        if opf_path not in names:
            raise MetadataError(f"{path.name}: {opf_path} missing from the archive.")

        root = ElementTree.fromstring(zf.read(opf_path))
        base = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""

        meta = BookMetadata()
        self._read_dublin_core(root, meta)
        self._read_series(root, meta)
        meta.spine_length = len(self._children(root, "spine"))
        if not meta.title:
            meta.title = path.stem
        meta.title_sort = title_sort_key(meta.title)
        self._read_cover(zf, root, base, meta)
        return meta

    def is_locked(self, path: Path) -> bool:
        """Whether a book is encrypted by the shop that sold it.

        Cheap enough — one small file out of the archive — to ask about
        every book on every scan, which is what keeps a library that was
        indexed before this rule existed from carrying them for ever.
        """
        path = Path(path)
        try:
            if path.is_dir():
                contents = FolderContents(path)
                self._refuse_if_locked(contents, contents.names(), path)
                return False
            with zipfile.ZipFile(path) as archive:
                contents = ZipContents(archive)
                self._refuse_if_locked(contents, contents.names(), path)
        except DrmError:
            return True
        except Exception:  # noqa: BLE001 — anything else is not this question
            return False
        return False

    @staticmethod
    def _refuse_if_locked(contents, names: list[str], path: Path) -> None:
        """Encrypted *documents* mean DRM; encrypted *fonts* mean
        obfuscation, which is ordinary and readable. The difference is
        what is listed, not that anything is listed at all."""
        found = next((n for n in names
                      if n.endswith("META-INF/encryption.xml")), None)
        if not found:
            return
        try:
            declared = contents.read(found).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 — an unreadable note is not DRM
            return

        locked = [uri for uri in re.findall(r'CipherReference[^>]*URI="([^"]+)"',
                                            declared)
                  if not uri.lower().endswith(FONT_TYPES)]
        if not locked:
            return
        where = ("Apple Books" if "itunes.apple.com" in declared
                 else "the shop it came from")
        raise DrmError(
            f"{path.name} is encrypted and can only be read in {where}.")

    def _children(self, root, parent_name: str) -> list:
        for child in root:
            if _localname(child.tag) == parent_name:
                return list(child)
        return []

    def _read_dublin_core(self, root, meta: BookMetadata) -> None:
        creators: list[str] = []
        for node in self._children(root, "metadata"):
            name = _localname(node.tag)
            text = (node.text or "").strip()
            if not text and name != "meta":
                continue
            if name == "title" and not meta.title:
                meta.title = text
            elif name == "creator":
                creators.append(text)
            elif name == "language" and not meta.language:
                # "UND" is the OPF shrugging, not a language. Showing it
                # as one is worse than showing none.
                meta.language = text if text.strip().lower() not in NOT_A_LANGUAGE else None
            elif name == "publisher" and not meta.publisher:
                meta.publisher = text
            elif name == "description" and not meta.description:
                meta.description = text
            elif name == "subject":
                meta.subjects.append(text)
            elif name == "date" and not meta.edition_date:
                meta.edition_date = text
                meta.first_published = self._year(text)
            elif name == "identifier":
                isbn = re.search(r"(97[89]\d{10})", text.replace("-", ""))
                if isbn and not meta.isbn13:
                    meta.isbn13 = isbn.group(1)

        for raw in creators:
            meta.authors.extend(PersonName.parse_many(raw))

    @staticmethod
    def _year(text: str) -> int | None:
        match = re.match(r"\s*(\d{4})\b", text or "")
        if not match:
            return None
        year = int(match.group(1))
        return year if 1000 <= year <= 2100 else None

    def _read_series(self, root, meta: BookMetadata) -> None:
        for node in self._children(root, "metadata"):
            if _localname(node.tag) != "meta":
                continue
            name = node.get("name")
            content = node.get("content")
            if name == "calibre:series" and content:
                meta.series = content.strip()
            elif name == "calibre:series_index" and content:
                try:
                    meta.series_index = float(content)
                except ValueError:
                    pass
            # EPUB 3 equivalent
            elif node.get("property") == "belongs-to-collection" and node.text:
                meta.series = meta.series or node.text.strip()
            elif node.get("property") == "group-position" and node.text:
                try:
                    meta.series_index = meta.series_index or float(node.text)
                except ValueError:
                    pass

    def _read_cover(self, zf, root, base: str, meta: BookMetadata) -> None:
        items = [n for n in self._children(root, "manifest")
                 if _localname(n.tag) == "item"]
        by_id = {n.get("id"): n for n in items if n.get("id")}

        href = None
        # EPUB 3: properties="cover-image"
        for node in items:
            if "cover-image" in (node.get("properties") or ""):
                href = node.get("href")
                break
        # EPUB 2: <meta name="cover" content="item-id"/>
        if href is None:
            for node in self._children(root, "metadata"):
                if _localname(node.tag) == "meta" and node.get("name") == "cover":
                    item = by_id.get(node.get("content"))
                    if item is not None:
                        href = item.get("href")
                    break
        if not href:
            return

        from urllib.parse import unquote
        target = unquote(href)
        candidates = [f"{base}/{target}" if base else target, target]
        names = set(zf.names())
        found = next((c for c in candidates if c in names), None)
        if found is None:
            # Some books percent-encode inconsistently; fall back to a
            # basename match rather than losing the cover.
            leaf = target.rsplit("/", 1)[-1]
            found = next((n for n in names if n.rsplit("/", 1)[-1] == leaf), None)
        if found is None:
            return

        suffix = Path(found).suffix.lower()
        if suffix not in IMAGE_TYPES:
            return
        data = zf.read(found)
        if data:
            meta.cover_data = data
            meta.cover_suffix = suffix
