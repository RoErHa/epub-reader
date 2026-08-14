"""The HTTP service.

M0 scope: serve the app shell and the reading engine, expose the library
read-only, and put the security headers on everything.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from epub_config import Config
from epub_db import Database
from epub_reading import STATUSES, ReadingStateStore
from epub_security import SecurityHeaders, SecurityHeadersMiddleware

VERSION = "0.1.0"
SERVICE = "epub-reader"


class BookLibrary:
    """Read-only view of the library folder.

    The service never moves, renames or writes to anything under the
    library root — that is a project-wide rule, so the only write-capable
    operation this class offers is none.
    """

    # EPUB only, deliberately, until the reader is finished. PDF is a
    # separate rendering model and foliate's adapter is self-described as
    # a proof-of-concept; CBR is RAR, which nothing here can read. Listing
    # formats we cannot render well would promise what we do not deliver.
    # MOBI/AZW3 come next — they use the same reflow engine.
    SUFFIXES = frozenset({".epub"})

    def __init__(self, root: Path) -> None:
        self._root = root
        self._hashes: dict[tuple[str, int, int], str] = {}

    @property
    def root(self) -> Path:
        return self._root

    def list(self) -> list[dict[str, object]]:
        if not self._root.is_dir():
            return []
        books = [
            {
                "name": p.name,
                "size": p.stat().st_size,
                "mtime": int(p.stat().st_mtime),
                "format": p.suffix.lstrip(".").lower(),
            }
            for p in sorted(self._root.iterdir())
            if p.is_file() and p.suffix.lower() in self.SUFFIXES
        ]
        return books

    def content_hash(self, path: Path) -> str:
        """Stable identity for a book, independent of its filename.

        Cached on (path, size, mtime) so reopening a book does not re-read
        it from disk. M2 stores this in the book table.
        """
        stat = path.stat()
        key = (str(path), stat.st_size, int(stat.st_mtime))
        cached = self._hashes.get(key)
        if cached:
            return cached
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        self._hashes[key] = digest.hexdigest()
        return self._hashes[key]

    def resolve(self, name: str) -> Path:
        """Map a requested name to a file, refusing anything that tries
        to escape the library root."""
        if not name or "/" in name or "\\" in name or name.startswith("."):
            raise HTTPException(status_code=400, detail="Invalid book name.")
        candidate = (self._root / name).resolve()
        try:
            candidate.relative_to(self._root.resolve())
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid book name.") from None
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="No such book.")
        # A format we do not list is a format we do not serve.
        if candidate.suffix.lower() not in self.SUFFIXES:
            raise HTTPException(status_code=404, detail="No such book.")
        return candidate


class PositionUpdate(BaseModel):
    cfi: str | None = None
    fraction: float = Field(..., description="0..1; clamped, not refused.")


class StatusUpdate(BaseModel):
    status: str

    def validated(self) -> str:
        if self.status not in STATUSES:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{self.status!r} is not a reading status. "
                    f"Use one of: {', '.join(sorted(STATUSES))}."
                ),
            )
        return self.status


class EpubGateway:
    """Builds and owns the FastAPI application."""

    MEDIA_TYPES = {
        ".epub": "application/epub+zip",
        ".azw3": "application/vnd.amazon.ebook",
        ".mobi": "application/x-mobipocket-ebook",
        ".fb2": "application/x-fictionbook+xml",
        ".cbz": "application/vnd.comicbook+zip",
        ".pdf": "application/pdf",
    }

    def __init__(
        self,
        config: Config,
        static_dir: Path | None = None,
        headers: SecurityHeaders | None = None,
    ) -> None:
        self._config = config
        self._static = static_dir or Path(__file__).resolve().parent / "static"
        self._headers = headers or SecurityHeaders()
        self._library = BookLibrary(config.library_root)
        self._database = Database(config.db_path)
        self._database.migrate()
        self._reading = ReadingStateStore(self._database)
        self._app = self._build()

    @property
    def app(self) -> FastAPI:
        return self._app

    @property
    def library(self) -> BookLibrary:
        return self._library

    @property
    def reading(self) -> ReadingStateStore:
        return self._reading

    def close(self) -> None:
        self._database.close()

    def _build(self) -> FastAPI:
        app = FastAPI(title=SERVICE, version=VERSION, docs_url=None, redoc_url=None)
        app.add_middleware(SecurityHeadersMiddleware, headers=self._headers)
        app.mount(
            "/static", StaticFiles(directory=self._static), name="static"
        )
        self._register_routes(app)
        return app

    def _register_routes(self, app: FastAPI) -> None:
        library = self._library
        reading = self._reading
        shell = self._static / "index.html"

        def state_of(name: str):
            return reading.get(library.content_hash(library.resolve(name)))

        @app.get("/healthz")
        def healthz() -> JSONResponse:
            return JSONResponse(
                {
                    "status": "ok",
                    "service": SERVICE,
                    "version": VERSION,
                    "library_root": str(library.root),
                }
            )

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(shell, media_type="text/html")

        @app.get("/api/books")
        def list_books() -> JSONResponse:
            books = library.list()
            hashes = {
                b["name"]: library.content_hash(library.root / b["name"])
                for b in books
            }
            states = reading.get_many(hashes.values())
            for book in books:
                state = states.get(hashes[book["name"]])
                book["percent"] = state.percent if state else 0
                book["status"] = state.status if state else "unread"
            return JSONResponse({"books": books})

        @app.get("/api/books/{name}/position")
        def get_position(name: str) -> JSONResponse:
            state = state_of(name)
            return JSONResponse({"position": state.as_dict() if state else None})

        @app.put("/api/books/{name}/position")
        def put_position(name: str, update: PositionUpdate) -> JSONResponse:
            content_hash = library.content_hash(library.resolve(name))
            state = reading.save(
                content_hash, cfi=update.cfi, fraction=update.fraction
            )
            return JSONResponse({"position": state.as_dict()})

        @app.put("/api/books/{name}/status")
        def put_status(name: str, update: StatusUpdate) -> JSONResponse:
            content_hash = library.content_hash(library.resolve(name))
            state = reading.set_status(content_hash, update.validated())
            return JSONResponse({"position": state.as_dict()})

        @app.get("/api/books/{name}")
        def get_book(name: str) -> FileResponse:
            path = library.resolve(name)
            media = self.MEDIA_TYPES.get(
                path.suffix.lower(), "application/octet-stream"
            )
            return FileResponse(path, media_type=media)
