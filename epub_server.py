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

from epub_browse import BrowseError, BrowseQueries
from epub_config import Config
from epub_db import Database
from epub_index import LibraryIndexer
from epub_reading import STATUSES, ReadingStateStore
from epub_search import SearchQueries
from epub_shelves import SMART, ShelfError, ShelfExists, ShelfStore
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


class ShelfName(BaseModel):
    name: str


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
        self._browse = BrowseQueries(self._database)
        self._search = SearchQueries(self._database, self._browse)
        self._shelves = ShelfStore(self._database)
        self._indexer = LibraryIndexer(
            self._database, self._library, config.cover_cache
        )
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

    @property
    def database(self) -> Database:
        return self._database

    @property
    def indexer(self) -> LibraryIndexer:
        return self._indexer

    def reindex(self):
        return self._indexer.run()

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

        browse = self._browse
        covers = self._config.cover_cache

        def with_progress(books: list[dict]) -> list[dict]:
            # The index already knows every book's content hash, so this
            # never touches the filesystem.
            states = reading.get_many(b["content_hash"] for b in books)
            for book in books:
                state = states.get(book["content_hash"])
                book["percent"] = state.percent if state else 0
                book["status"] = state.status if state else "unread"
            return books

        def book_or_404(book_id: int):
            row = browse.book_row(book_id)
            if row is None:
                raise HTTPException(status_code=404, detail="No such book.")
            return row

        def book_file(row):
            path = (library.root / row["path"]).resolve()
            try:
                path.relative_to(library.root.resolve())
            except ValueError:
                raise HTTPException(
                    status_code=404, detail="No such book."
                ) from None
            if not path.is_file():
                raise HTTPException(status_code=404, detail="No such book.")
            return path

        @app.get("/api/books")
        def list_books(
            sort: str = "title", letter: str | None = None,
            author_id: int | None = None, series_id: int | None = None,
            genre_id: int | None = None, decade: int | None = None,
            shelf_id: int | None = None, limit: int = 200, offset: int = 0,
        ) -> JSONResponse:
            try:
                page = browse.books(
                    sort=sort, letter=letter, author_id=author_id,
                    series_id=series_id, genre_id=genre_id, decade=decade,
                    shelf_id=shelf_id, limit=min(limit, 500), offset=offset,
                )
            except BrowseError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            return JSONResponse(
                {"books": with_progress(page.rows), "total": page.total}
            )

        @app.get("/api/authors")
        def list_authors() -> JSONResponse:
            return JSONResponse({"authors": browse.authors()})

        @app.get("/api/authors/{author_id}/books")
        def author_books(author_id: int) -> JSONResponse:
            page = browse.books(author_id=author_id, sort="series", limit=500)
            return JSONResponse(
                {"books": with_progress(page.rows), "total": page.total}
            )

        @app.get("/api/series")
        def list_series() -> JSONResponse:
            return JSONResponse({"series": browse.series()})

        @app.get("/api/series/{series_id}/books")
        def series_books(series_id: int) -> JSONResponse:
            page = browse.books(series_id=series_id, sort="series", limit=500)
            return JSONResponse(
                {"books": with_progress(page.rows), "total": page.total}
            )

        @app.get("/api/genres")
        def list_genres() -> JSONResponse:
            return JSONResponse({"genres": browse.genres()})

        @app.get("/api/genres/{genre_id}/books")
        def genre_books(genre_id: int) -> JSONResponse:
            page = browse.books(genre_id=genre_id, limit=500)
            return JSONResponse(
                {"books": with_progress(page.rows), "total": page.total}
            )

        @app.get("/api/years")
        def list_years() -> JSONResponse:
            return JSONResponse({"decades": browse.decades()})

        @app.get("/api/letters")
        def list_letters(mode: str = "titles") -> JSONResponse:
            try:
                return JSONResponse({"letters": browse.letters(mode)})
            except BrowseError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None

        @app.get("/api/search")
        def search(q: str = "", limit: int = 50) -> JSONResponse:
            result = self._search.search(q, limit=min(limit, 200))
            result["books"] = with_progress(result["books"])
            return JSONResponse(result)

        shelves = self._shelves

        @app.get("/api/sorts")
        def list_sorts() -> JSONResponse:
            return JSONResponse({"sorts": browse.sorts()})

        @app.get("/api/shelves")
        def list_shelves() -> JSONResponse:
            return JSONResponse({"shelves": shelves.list()})

        @app.post("/api/shelves")
        def create_shelf(body: ShelfName) -> JSONResponse:
            try:
                return JSONResponse({"shelf": shelves.create(body.name)})
            except ShelfExists as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            except ShelfError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None

        @app.put("/api/shelves/{shelf_id}")
        def rename_shelf(shelf_id: int, body: ShelfName) -> JSONResponse:
            try:
                return JSONResponse({"shelf": shelves.rename(shelf_id, body.name)})
            except ShelfExists as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            except ShelfError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None

        @app.delete("/api/shelves/{shelf_id}")
        def delete_shelf(shelf_id: int) -> JSONResponse:
            try:
                shelves.delete(shelf_id)
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            return JSONResponse({"deleted": shelf_id})

        @app.get("/api/shelves/{shelf_id}/books")
        def shelf_books(shelf_id: int) -> JSONResponse:
            page = browse.books(shelf_id=shelf_id, limit=500)
            return JSONResponse(
                {"books": with_progress(page.rows), "total": page.total}
            )

        @app.put("/api/shelves/{shelf_id}/books/{book_id}")
        def add_to_shelf(shelf_id: int, book_id: int) -> JSONResponse:
            try:
                shelves.add_book(shelf_id, book_id)
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            return JSONResponse({"shelf": shelves.get(shelf_id)})

        @app.delete("/api/shelves/{shelf_id}/books/{book_id}")
        def remove_from_shelf(shelf_id: int, book_id: int) -> JSONResponse:
            try:
                shelves.remove_book(shelf_id, book_id)
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            return JSONResponse({"shelf": shelves.get(shelf_id)})

        # Smart shelves are read-only by construction: there is no route
        # to put a book on one, because membership is a consequence.
        @app.get("/api/shelves/smart/{key}/books")
        def smart_shelf_books(key: str) -> JSONResponse:
            if key not in SMART:
                raise HTTPException(status_code=404, detail="No such shelf.")
            page = browse.books(smart=key, sort="opened", limit=500)
            return JSONResponse(
                {"books": with_progress(page.rows), "total": page.total}
            )

        @app.get("/api/books/{book_id}/detail")
        def book_detail(book_id: int) -> JSONResponse:
            book_or_404(book_id)
            book = browse.book(book_id)
            if book is None:
                raise HTTPException(status_code=404, detail="No such book.")
            with_progress([book])
            book["shelves"] = shelves.shelves_of(book_id)
            author = browse.primary_author(book_id)
            return JSONResponse({
                "book": book,
                "genres": browse.genres_of(book_id),
                "author": author,
                "also_published": (
                    browse.also_published(author["id"], book_id) if author else []
                ),
            })

        @app.get("/api/books/{book_id}/shelves")
        def book_shelves(book_id: int) -> JSONResponse:
            book_or_404(book_id)
            return JSONResponse({"shelves": shelves.shelves_of(book_id)})

        @app.get("/api/status")
        def index_status() -> JSONResponse:
            return JSONResponse(browse.status())

        @app.get("/api/covers/{bucket}/{filename}")
        def cover(bucket: str, filename: str) -> FileResponse:
            if not bucket.isalnum() or "/" in filename or ".." in filename:
                raise HTTPException(status_code=400, detail="Invalid cover path.")
            path = (covers / bucket / filename).resolve()
            try:
                path.relative_to(covers.resolve())
            except ValueError:
                raise HTTPException(
                    status_code=400, detail="Invalid cover path."
                ) from None
            if not path.is_file():
                raise HTTPException(status_code=404, detail="No such cover.")
            return FileResponse(path, media_type="image/jpeg")

        @app.get("/api/books/{book_id}/position")
        def get_position(book_id: int) -> JSONResponse:
            row = book_or_404(book_id)
            state = reading.get(row["content_hash"])
            return JSONResponse({"position": state.as_dict() if state else None})

        @app.put("/api/books/{book_id}/position")
        def put_position(book_id: int, update: PositionUpdate) -> JSONResponse:
            row = book_or_404(book_id)
            state = reading.save(
                row["content_hash"], cfi=update.cfi, fraction=update.fraction
            )
            return JSONResponse({"position": state.as_dict()})

        @app.put("/api/books/{book_id}/status")
        def put_status(book_id: int, update: StatusUpdate) -> JSONResponse:
            row = book_or_404(book_id)
            state = reading.set_status(row["content_hash"], update.validated())
            return JSONResponse({"position": state.as_dict()})

        @app.get("/api/books/{book_id}/file")
        def get_book(book_id: int) -> FileResponse:
            # Addressed by id, not by path: 44% of a real library lives in
            # subfolders, and a relative path with slashes cannot be a
            # single path segment.
            path = book_file(book_or_404(book_id))
            media = self.MEDIA_TYPES.get(
                path.suffix.lower(), "application/octet-stream"
            )
            return FileResponse(path, media_type=media)
