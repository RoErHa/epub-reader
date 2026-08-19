"""The routes, in groups that mean something.

`EpubGateway._register_routes` was **530 lines holding 57 nested
functions** — 56% of a 945-line module, and the largest thing in the
codebase was not a class at all, which §10.1 says everything here should
be. It could not be read in one sitting and no route could be tested
without standing up the whole app.

> **Reaching into another object's privates only moves the problem.**
> The routes needed nineteen of the gateway's attributes, and a route
> class saying `gateway._browse` would be the same coupling with more
> files. `Services` is the collaborators, named and public, handed over
> once.

Five helpers the routes share — turning a book id into a row, a row into
a path, a path into bytes — are methods on `Routes`, where a subclass can
use them and a test can call them.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from epub_annotations import AnnotationError
from epub_browse import BrowseError
from epub_rar import RarArchive, RarError, repack
from epub_reading import STATUSES
from epub_shelves import SMART, ShelfError, ShelfExists


class PositionUpdate(BaseModel):
    cfi: str | None = None
    fraction: float = Field(..., description="0..1; clamped, not refused.")

class AuthorMerge(BaseModel):
    """Say so by hand when the rules refuse a merge they cannot prove."""

    keep: int
    merge: list[int]
    name: str | None = None

class NewAnnotation(BaseModel):
    """A highlight: where it is, and what it says there."""

    cfi: str
    text: str
    note: str | None = None
    color: str | None = None
    fraction: float = 0.0

class AnnotationEdit(BaseModel):
    """Second thoughts. The words and the place are not editable —
    changing those would make it a different highlight."""

    note: str | None = None
    color: str | None = None

class BookAuthors(BaseModel):
    """Who wrote it, when the file is wrong about that."""

    authors: list[str]

class BookSeries(BaseModel):
    """Which series a book belongs to, and where in it. No series takes
    it out of one."""

    series: str | None = None
    index: float | None = None

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


@dataclass(frozen=True)
class Services:
    """What the routes are allowed to reach for, named rather than
    rummaged for."""

    library: object
    reading: object
    database: object
    browse: object
    search: object
    shelves: object
    annotations: object
    credits: object
    config: object
    static: Path
    shell: Path
    media_types: dict
    enricher: object
    books_enricher: object
    deduper: object
    repairer: object
    titles: object
    series: object
    duplicates: object
    forget: object
    gateway: object
    # Reported by the health check, and defined in epub_server —
    # passed rather than imported, because importing them back
    # would be a circle.
    service: str
    version: str


class Routes:
    """A group of routes and the helpers they share."""

    def __init__(self, services: Services) -> None:
        self.services = services

    def register(self, app: FastAPI) -> None:
        raise NotImplementedError

    def with_progress(self, books: list[dict]) -> list[dict]:
        # The index already knows every book's content hash, so this
        # never touches the filesystem.
        states = self.services.reading.get_many(b["content_hash"] for b in books)
        marks = self.services.annotations.counts(b["id"] for b in books)
        for book in books:
            state = states.get(book["content_hash"])
            book["percent"] = state.percent if state else 0
            book["status"] = state.status if state else "unread"
            book["annotations"] = marks.get(book["id"], 0)
        return books

    def book_or_404(self, book_id: int):
        row = self.services.browse.book_row(book_id)
        if row is None:
            raise HTTPException(status_code=404, detail="No such book.")
        return row

    def known_book_or_404(self, book_id: int):
        """Any book the library knows, including one whose file has
        gone missing — its annotations outlive the file."""
        row = self.services.database.connection.execute(
            "SELECT id FROM book WHERE id = ?", (book_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="No such book.")
        return row

    def zip_unpacked(self, root: Path) -> bytes:
        """An unpacked book, packed. The mimetype goes first and
        uncompressed, as the EPUB container spec asks and as some
        readers check."""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            mimetype = root / "mimetype"
            archive.writestr(
                zipfile.ZipInfo("mimetype"),
                mimetype.read_bytes() if mimetype.is_file()
                else b"application/epub+zip",
                compress_type=zipfile.ZIP_STORED,
            )
            for inner in sorted(p for p in root.rglob("*") if p.is_file()):
                name = inner.relative_to(root).as_posix()
                if name == "mimetype":
                    continue
                archive.write(inner, name)
        return buffer.getvalue()

    def book_file(self, row):
        path = (self.services.library.root / row["path"]).resolve()
        try:
            path.relative_to(self.services.library.root.resolve())
        except ValueError:
            raise HTTPException(
                status_code=404, detail="No such book."
            ) from None
        # A book may be a directory: 265 of the real library were
        # never zipped. Both are books; only the container differs.
        if not path.is_file() and not path.is_dir():
            raise HTTPException(status_code=404, detail="No such book.")
        return path


class AppRoutes(Routes):
    """The shell, the health check, the status line and the cover
    files: everything about the app rather than about a book."""

    def register(self, app: FastAPI) -> None:
        @app.get("/healthz")
        def healthz() -> JSONResponse:
            return JSONResponse(
                {
                    "status": "ok",
                    "service": self.services.service,
                    "version": self.services.version,
                    "library_root": str(self.services.library.root),
                }
            )

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(self.services.shell, media_type="text/html")

        @app.get("/api/status")
        def index_status() -> JSONResponse:
            status = self.services.browse.status()
            status["pending_review"] = self.services.books_enricher.pending_count()
            return JSONResponse(status)

        @app.get("/api/covers/{bucket}/{filename}")
        def cover(bucket: str, filename: str) -> FileResponse:
            if not bucket.isalnum() or "/" in filename or ".." in filename:
                raise HTTPException(status_code=400, detail="Invalid cover path.")
            path = (self.services.config.cover_cache / bucket / filename).resolve()
            try:
                path.relative_to(self.services.config.cover_cache.resolve())
            except ValueError:
                raise HTTPException(
                    status_code=400, detail="Invalid cover path."
                ) from None
            if not path.is_file():
                raise HTTPException(status_code=404, detail="No such cover.")
            return FileResponse(path, media_type="image/jpeg")


class BrowseRoutes(Routes):
    """Browse and search — the modes of §4, each a list and a
    drill-down."""

    def register(self, app: FastAPI) -> None:
        @app.get("/api/books")
        def list_books(
            sort: str = "title", letter: str | None = None,
            author_id: int | None = None, series_id: int | None = None,
            genre_id: int | None = None, decade: int | None = None,
            shelf_id: int | None = None, limit: int = 200, offset: int = 0,
        ) -> JSONResponse:
            try:
                page = self.services.browse.books(
                    sort=sort, letter=letter, author_id=author_id,
                    series_id=series_id, genre_id=genre_id, decade=decade,
                    shelf_id=shelf_id, limit=min(limit, 500), offset=offset,
                )
            except BrowseError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            return JSONResponse(
                {"books": self.with_progress(page.rows), "total": page.total}
            )

        @app.get("/api/authors")
        def list_authors() -> JSONResponse:
            return JSONResponse({"authors": self.services.browse.authors()})

        @app.get("/api/authors/{author_id}/books")
        def author_books(author_id: int) -> JSONResponse:
            page = self.services.browse.books(
                author_id=author_id, sort="series", limit=500)
            return JSONResponse(
                {"books": self.with_progress(page.rows), "total": page.total}
            )

        @app.get("/api/series")
        def list_series() -> JSONResponse:
            return JSONResponse({"series": self.services.browse.series()})

        @app.get("/api/series/{series_id}/books")
        def series_books(series_id: int) -> JSONResponse:
            page = self.services.browse.books(
                series_id=series_id, sort="series", limit=500)
            return JSONResponse(
                {"books": self.with_progress(page.rows), "total": page.total}
            )

        @app.get("/api/genres")
        def list_genres() -> JSONResponse:
            return JSONResponse({"genres": self.services.browse.genres()})

        @app.get("/api/genres/{genre_id}/books")
        def genre_books(genre_id: int) -> JSONResponse:
            page = self.services.browse.books(genre_id=genre_id, limit=500)
            return JSONResponse(
                {"books": self.with_progress(page.rows), "total": page.total}
            )

        @app.get("/api/formats")
        def list_formats() -> JSONResponse:
            return JSONResponse({"formats": self.services.browse.formats()})

        @app.get("/api/formats/{group}/books")
        def format_books(group: str) -> JSONResponse:
            try:
                page = self.services.browse.books(format_group=group, limit=500)
            except BrowseError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            return JSONResponse(
                {"books": self.with_progress(page.rows), "total": page.total}
            )

        @app.get("/api/years")
        def list_years() -> JSONResponse:
            return JSONResponse({"decades": self.services.browse.decades()})

        @app.get("/api/letters")
        def list_letters(mode: str = "titles") -> JSONResponse:
            try:
                return JSONResponse({"letters": self.services.browse.letters(mode)})
            except BrowseError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None

        @app.get("/api/search")
        def search(q: str = "", limit: int = 50) -> JSONResponse:
            result = self.services.search.search(q, limit=min(limit, 200))
            result["books"] = self.with_progress(result["books"])
            return JSONResponse(result)

        @app.get("/api/sorts")
        def list_sorts() -> JSONResponse:
            return JSONResponse({"sorts": self.services.browse.sorts()})

        @app.get("/api/books/{book_id}/detail")
        def book_detail(book_id: int, show_all: bool = False) -> JSONResponse:
            self.book_or_404(book_id)
            book = self.services.browse.book(book_id)
            if book is None:
                raise HTTPException(status_code=404, detail="No such book.")
            self.with_progress([book])
            book["shelves"] = self.services.shelves.shelves_of(book_id)
            author = self.services.browse.primary_author(book_id)
            return JSONResponse({
                "book": book,
                "genres": self.services.browse.genres_of(book_id),
                "author": author,
                "also_published": (
                    self.services.browse.also_published(author["id"], book_id, show_all)
                    if author else []
                ),
                "hidden_works": (
                    self.services.browse.hidden_work_count(author["id"], book_id)
                    if author and not show_all else 0
                ),
            })


class ShelfRoutes(Routes):
    """Shelves: the stored ones a reader keeps, and the smart ones
    that are a view of reading state (§10.9)."""

    def register(self, app: FastAPI) -> None:
        @app.get("/api/shelves")
        def list_shelves() -> JSONResponse:
            return JSONResponse({"shelves": self.services.shelves.list()})

        @app.post("/api/shelves")
        def create_shelf(body: ShelfName) -> JSONResponse:
            try:
                return JSONResponse({"shelf": self.services.shelves.create(body.name)})
            except ShelfExists as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            except ShelfError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None

        @app.put("/api/shelves/{shelf_id}")
        def rename_shelf(shelf_id: int, body: ShelfName) -> JSONResponse:
            try:
                return JSONResponse(
                    {"shelf": self.services.shelves.rename(shelf_id, body.name)})
            except ShelfExists as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            except ShelfError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None

        @app.delete("/api/shelves/{shelf_id}")
        def delete_shelf(shelf_id: int) -> JSONResponse:
            try:
                self.services.shelves.delete(shelf_id)
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            return JSONResponse({"deleted": shelf_id})

        @app.get("/api/shelves/{shelf_id}/books")
        def shelf_books(shelf_id: int) -> JSONResponse:
            page = self.services.browse.books(shelf_id=shelf_id, limit=500)
            return JSONResponse(
                {"books": self.with_progress(page.rows), "total": page.total}
            )

        @app.put("/api/shelves/{shelf_id}/books/{book_id}")
        def add_to_shelf(shelf_id: int, book_id: int) -> JSONResponse:
            try:
                self.services.shelves.add_book(shelf_id, book_id)
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            return JSONResponse({"shelf": self.services.shelves.get(shelf_id)})

        @app.delete("/api/shelves/{shelf_id}/books/{book_id}")
        def remove_from_shelf(shelf_id: int, book_id: int) -> JSONResponse:
            try:
                self.services.shelves.remove_book(shelf_id, book_id)
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            return JSONResponse({"shelf": self.services.shelves.get(shelf_id)})

        @app.get("/api/shelves/smart/{key}/books")
        def smart_shelf_books(key: str) -> JSONResponse:
            if key not in SMART:
                raise HTTPException(status_code=404, detail="No such shelf.")
            page = self.services.browse.books(smart=key, sort="opened", limit=500)
            return JSONResponse(
                {"books": self.with_progress(page.rows), "total": page.total}
            )

        @app.get("/api/books/{book_id}/shelves")
        def book_shelves(book_id: int) -> JSONResponse:
            self.book_or_404(book_id)
            return JSONResponse({"shelves": self.services.shelves.shelves_of(book_id)})


class ReadingRoutes(Routes):
    """Reading: where you are, what you marked, and the file
    itself — the routes the reader talks to."""

    def register(self, app: FastAPI) -> None:
        @app.get("/api/books/{book_id}/position")
        def get_position(book_id: int) -> JSONResponse:
            row = self.book_or_404(book_id)
            state = self.services.reading.get(row["content_hash"])
            return JSONResponse({"position": state.as_dict() if state else None})

        @app.put("/api/books/{book_id}/position")
        def put_position(book_id: int, update: PositionUpdate) -> JSONResponse:
            row = self.book_or_404(book_id)
            state = self.services.reading.save(
                row["content_hash"], cfi=update.cfi, fraction=update.fraction
            )
            return JSONResponse({"position": state.as_dict()})

        @app.put("/api/books/{book_id}/status")
        def put_status(book_id: int, update: StatusUpdate) -> JSONResponse:
            row = self.book_or_404(book_id)
            state = self.services.reading.set_status(
                row["content_hash"], update.validated())
            return JSONResponse({"position": state.as_dict()})

        @app.get("/api/books/{book_id}/annotations")
        def get_annotations(book_id: int) -> JSONResponse:
            # Not book_or_404: a book whose file has vanished still has
            # a row, and what you wrote on it is still yours.
            self.known_book_or_404(book_id)
            return JSONResponse(
                {"annotations": self.services.annotations.for_book(book_id)})

        @app.post("/api/books/{book_id}/annotations", status_code=201)
        def post_annotation(book_id: int, body: NewAnnotation) -> JSONResponse:
            self.known_book_or_404(book_id)
            try:
                mark = self.services.annotations.add(
                    book_id, cfi=body.cfi, text=body.text, note=body.note,
                    color=body.color, fraction=body.fraction)
            except AnnotationError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            return JSONResponse(mark, status_code=201)

        @app.patch("/api/annotations/{annotation_id}")
        def patch_annotation(annotation_id: int,
                             body: AnnotationEdit) -> JSONResponse:
            # Only what the request actually mentioned: emptying a note
            # is not the same as not mentioning one.
            changes = {field: getattr(body, field)
                       for field in body.model_fields_set}
            try:
                return JSONResponse(
                    self.services.annotations.update(annotation_id, **changes))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            except AnnotationError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None

        @app.delete("/api/annotations/{annotation_id}", status_code=204)
        def delete_annotation(annotation_id: int) -> Response:
            if not self.services.annotations.remove(annotation_id):
                raise HTTPException(status_code=404,
                                    detail="No such annotation.")
            return Response(status_code=204)

        @app.get("/api/books/{book_id}/file")
        def get_book(book_id: int) -> FileResponse:
            # Addressed by id, not by path: 44% of a real library lives in
            # subfolders, and a relative path with slashes cannot be a
            # single path segment.
            path = self.book_file(self.book_or_404(book_id))
            if RarArchive.is_rar(path):
                # A comic packed with RAR, packed as a zip. foliate reads
                # a zip; the reader, the positions and the highlights
                # carry on knowing nothing about it (§8.5's path).
                try:
                    body = repack(RarArchive(path))
                except RarError as exc:
                    raise HTTPException(status_code=422,
                                        detail=str(exc)) from None
                return Response(
                    content=body,
                    media_type="application/vnd.comicbook+zip",
                    headers={"Content-Disposition":
                             f'inline; filename="{path.stem}.cbz"'},
                )
            if path.is_dir():
                # foliate reads a zip, so a book that was never zipped
                # is zipped on the way out. Everything downstream — the
                # reader, CFI positions, highlights — carries on
                # knowing nothing about it.
                return Response(
                    content=self.zip_unpacked(path),
                    media_type="application/epub+zip",
                    headers={"Content-Disposition":
                             f'inline; filename="{path.name}"'},
                )
            media = self.services.media_types.get(
                path.suffix.lower(), "application/octet-stream"
            )
            return FileResponse(path, media_type=media)
