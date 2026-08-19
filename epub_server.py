"""The HTTP service.

M0 scope: serve the app shell and the reading engine, expose the library
read-only, and put the security headers on everything.
"""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from epub_annotations import AnnotationError, AnnotationStore
from epub_browse import BrowseError, BrowseQueries
from epub_covers import CoverBackfill, ProviderCovers
from epub_credits import BookCredits
from epub_duplicates import DuplicateFinder
from epub_forget import ForgetMissing, UnpluggedError
from epub_coauthors import CoAuthorsInTitles
from epub_filenames import CreditsFromNames
from epub_fragments import TitlesFromFragments
from epub_series import SeriesFromNames
from epub_tags import ScannerTags
from epub_config import Config
from epub_db import Database
from epub_index import LibraryIndexer
from epub_rar import RarArchive, RarError, repack
from epub_reading import STATUSES, ReadingStateStore
from epub_book_enrich import BookEnricher
from epub_dedupe import AuthorDeduplicator
from epub_maintenance import LibraryRepair
from epub_refresh import AuthorRefresh
from epub_repair import TitleAsAuthorRepair
from epub_titles import TitleFromWorks
from epub_enrich import AuthorEnricher
from epub_providers import (GoogleBooksProvider, OpenLibraryProvider,
                            PoliteFetcher, http_bytes)
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

    # Everything the reflow engine actually reads. MOBI, AZW3 and PRC
    # share one container, and foliate sniffs it rather than trusting
    # the extension — so the reader needed no change at all; only the
    # library did, because their metadata lives in a PalmDB record.
    #
    # PDF is a different rendering model — pdf.js rasterises pages
    # rather than reflowing text — but it is 585 books here, and a
    # reader that cannot open them is a reader you keep leaving.
    #
    # CBZ is a zip of images, which foliate draws. CBR is a RAR of
    # images, which libarchive reads and foliate cannot — so it is
    # repacked as a zip on the way out (§8.15), the same trick §8.5
    # already plays on an unpacked EPUB.
    SUFFIXES = frozenset({".epub", ".mobi", ".azw3", ".azw", ".prc", ".pdf",
                          ".cbz", ".cbr"})

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


class EpubGateway:
    """Builds and owns the FastAPI application."""

    MEDIA_TYPES = {
        ".epub": "application/epub+zip",
        ".azw3": "application/vnd.amazon.ebook",
        ".mobi": "application/x-mobipocket-ebook",
        ".fb2": "application/x-fictionbook+xml",
        ".cbz": "application/vnd.comicbook+zip",
        # A CBR is served repacked, so what leaves here is a zip
        # whatever the file on disk is.
        ".cbr": "application/vnd.comicbook+zip",
        ".pdf": "application/pdf",
    }

    def __init__(
        self,
        config: Config,
        static_dir: Path | None = None,
        headers: SecurityHeaders | None = None,
        fetch=None,
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
        self._annotations = AnnotationStore(self._database)
        self._duplicates = DuplicateFinder(self._database)
        self._series = SeriesFromNames(self._database)
        self._filenames = CreditsFromNames(self._database, self._series)
        self._coauthors = CoAuthorsInTitles(self._database)
        self._tags = ScannerTags(self._database)
        self._forget = ForgetMissing(self._database)
        self._credits = BookCredits(self._database)
        # One fetcher for every provider, so the pacing is the whole
        # service's rather than each caller's: four separate streams is
        # how a library-wide run earns 503s.
        self._fetch = fetch or PoliteFetcher(rps=config.provider_max_rps)
        self._open_library = OpenLibraryProvider(
            self._fetch, contact=config.provider_key("OPENLIBRARY_CONTACT_EMAIL"))
        # After the provider, obviously — and on the shared,
        # browsing-paced fetcher: a scoped run is tens of requests, not
        # the hundreds that earned the cover sweep its own one-a-minute
        # clock (§8.13).
        self._fragments = TitlesFromFragments(self._database,
                                              self._open_library)
        self._enricher = AuthorEnricher(
            self._database, self._open_library, cover_root=config.cover_cache,
        )
        self._deduper = AuthorDeduplicator(self._database, self._open_library,
                                           enricher=self._enricher)
        self._repairer = TitleAsAuthorRepair(self._database, [self._open_library])
        self._titles = TitleFromWorks(self._database, self._enricher)
        self._refresher = AuthorRefresh(self._database, self._enricher)
        self._maintenance = LibraryRepair(
            self._database, self._open_library, [self._open_library],
            self._enricher)
        self._books_enricher = BookEnricher(self._database, [
            self._open_library,
            GoogleBooksProvider(
                self._fetch,
                api_key=config.provider_key("GOOGLE_BOOKS_API_KEY")),
        ])
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

    @property
    def covers(self):
        return self._indexer.covers

    @property
    def library_root(self) -> Path:
        return self._config.library_root

    def reindex(self):
        return self._indexer.run()

    def refresh_authors(self, limit: int = 10) -> str:
        """Bring the oldest author data up to date, and say what
        happened in one line."""
        return AuthorRefresh.summary(self._refresher.run(limit=limit))

    def forget_missing(self, apply: bool = False, force: bool = False) -> str:
        """Remove books whose files are gone, and say what that cost."""
        return ForgetMissing.summary(self._forget.run(apply=apply, force=force))

    def find_covers(self, apply: bool = False, limit: int | None = None,
                    online: bool = False) -> str:
        """Give a cover to books showing a lettered tile, and say how
        many took one.

        Offline by default — page one of a PDF, when page one is the
        cover. `online` asks Open Library about the books whose files
        hold no image at all, one request a minute.
        """
        if online:
            return ProviderCovers.summary(
                self._provider_covers().run(apply=apply, limit=limit))
        backfill = CoverBackfill(self._database, self._indexer.covers,
                                 self._config.library_root)
        return CoverBackfill.summary(backfill.run(apply=apply, limit=limit))

    def _provider_covers(self) -> ProviderCovers:
        """Its own fetcher, on its own clock: a sweep of 400 lookups is
        closer to bulk than to browsing, so it goes far slower than the
        service's shared one (§6.5)."""
        interval = self._config.cover_fetch_interval
        slow = PoliteFetcher(self._fetch, interval=interval)
        provider = OpenLibraryProvider(
            slow, contact=self._config.provider_key("OPENLIBRARY_CONTACT_EMAIL"))
        # The pictures come off a different host and are static files,
        # with a published allowance of about 100 every five minutes —
        # so they keep the service's ordinary pace. Only the search API
        # gets the slow clock, and it is the one doing the asking.
        images = PoliteFetcher(http_bytes, rps=self._config.provider_max_rps)
        return ProviderCovers(
            self._database, self._indexer.covers, self._config.library_root,
            provider, fetch_image=lambda url: images(url))

    def name_series(self, apply: bool = False) -> str:
        """File books under the series their names give, in one line."""
        return SeriesFromNames.summary(self._series.run(apply=apply))

    def read_names(self, apply: bool = False, author: str | None = None,
                   formats: list[str] | None = None) -> str:
        """Author, series and title from one filename, in one line."""
        return CreditsFromNames.summary(
            self._filenames.run(apply=apply, author=author, formats=formats))

    def read_coauthors(self, apply: bool = False) -> str:
        """Free the people written into titles, in one line."""
        return CoAuthorsInTitles.summary(self._coauthors.run(apply=apply))

    def mend_fragments(self, apply: bool = False, author: str | None = None,
                       formats: list[str] | None = None) -> str:
        """Finish the titles that were cut off, in one line."""
        return TitlesFromFragments.summary(
            self._fragments.run(apply=apply, author=author, formats=formats))

    def clean_tags(self, apply: bool = False) -> str:
        """Take the scanner's notes out of the titles, in one line."""
        return ScannerTags.summary(self._tags.run(apply=apply))

    def tidy(self) -> dict:
        """Read what the new arrivals' names say, in §6.6's order.

        **Authors before anything asked of an author**, then the
        co-authors that need a first author to join, and the scanner's
        notes last because they are nobody's dependency. Nothing here
        reaches a provider: `fragments` does, and stays opt-in.
        """
        names = self._filenames.run(apply=True)
        coauthors = self._coauthors.run(apply=True)
        tags = self._tags.run(apply=True)
        return {
            "credited": names["credited"], "filed": names["filed"],
            "retitled": names["retitled"], "freed": coauthors["credited"],
            "cleaned": tags["cleaned"],
        }

    @staticmethod
    def tidy_summary(report: dict) -> str:
        done = sum(report.values())
        if not done:
            return "Names: nothing new to read."
        return (f"Names: credited {report['credited']}, filed"
                f" {report['filed']} under a series, retitled"
                f" {report['retitled']}, freed {report['freed']} co-authors,"
                f" cleaned {report['cleaned']} titles.")

    def dedupe_books(self, apply: bool = False) -> str:
        """One row per work, and one line about it."""
        return DuplicateFinder.summary(self._duplicates.run(apply=apply))

    def repair(self, apply: bool = False) -> str:
        """Check authors and titles, and say what happened in one line."""
        return LibraryRepair.summary(self._maintenance.run(apply=apply))

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
            marks = self._annotations.counts(b["id"] for b in books)
            for book in books:
                state = states.get(book["content_hash"])
                book["percent"] = state.percent if state else 0
                book["status"] = state.status if state else "unread"
                book["annotations"] = marks.get(book["id"], 0)
            return books

        def book_or_404(book_id: int):
            row = browse.book_row(book_id)
            if row is None:
                raise HTTPException(status_code=404, detail="No such book.")
            return row

        def known_book_or_404(book_id: int):
            """Any book the library knows, including one whose file has
            gone missing — its annotations outlive the file."""
            row = self._database.connection.execute(
                "SELECT id FROM book WHERE id = ?", (book_id,)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="No such book.")
            return row

        def zip_unpacked(root: Path) -> bytes:
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

        def book_file(row):
            path = (library.root / row["path"]).resolve()
            try:
                path.relative_to(library.root.resolve())
            except ValueError:
                raise HTTPException(
                    status_code=404, detail="No such book."
                ) from None
            # A book may be a directory: 265 of the real library were
            # never zipped. Both are books; only the container differs.
            if not path.is_file() and not path.is_dir():
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

        @app.get("/api/formats")
        def list_formats() -> JSONResponse:
            return JSONResponse({"formats": browse.formats()})

        @app.get("/api/formats/{group}/books")
        def format_books(group: str) -> JSONResponse:
            try:
                page = browse.books(format_group=group, limit=500)
            except BrowseError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
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
        def book_detail(book_id: int, show_all: bool = False) -> JSONResponse:
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
                    browse.also_published(author["id"], book_id, show_all)
                    if author else []
                ),
                "hidden_works": (
                    browse.hidden_work_count(author["id"], book_id)
                    if author and not show_all else 0
                ),
            })

        @app.get("/api/books/{book_id}/shelves")
        def book_shelves(book_id: int) -> JSONResponse:
            book_or_404(book_id)
            return JSONResponse({"shelves": shelves.shelves_of(book_id)})

        enricher = self._books_enricher

        @app.post("/api/books/{book_id}/enrich")
        def enrich_book(book_id: int) -> JSONResponse:
            try:
                return JSONResponse(enricher.enrich(book_id))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None

        @app.get("/api/review")
        def review_queue(limit: int = 100) -> JSONResponse:
            return JSONResponse({"items": enricher.pending(min(limit, 500))})

        @app.post("/api/review/accept-all")
        def accept_all() -> JSONResponse:
            return JSONResponse(enricher.accept_all())

        @app.post("/api/review/{candidate_id}/accept")
        def accept_candidate(candidate_id: int) -> JSONResponse:
            try:
                return JSONResponse(enricher.accept(candidate_id))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None

        @app.post("/api/review/{candidate_id}/reject")
        def reject_candidate(candidate_id: int) -> JSONResponse:
            try:
                return JSONResponse(enricher.reject(candidate_id))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None

        # Both default to a dry run: they change the index, so seeing the
        # plan first is the point.
        @app.post("/api/authors/dedupe")
        def dedupe_authors(apply: bool = False) -> JSONResponse:
            return JSONResponse(self._deduper.run(apply=apply))

        @app.post("/api/authors/merge")
        def merge_authors(body: AuthorMerge) -> JSONResponse:
            """Merge authors the rules would not merge on their own."""
            try:
                return JSONResponse(
                    self._deduper.merge(body.keep, body.merge, body.name))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None

        @app.post("/api/books/repair-authors")
        def repair_authors(apply: bool = False) -> JSONResponse:
            return JSONResponse(self._repairer.run(apply=apply))

        @app.post("/api/books/forget-missing")
        def forget_missing(apply: bool = False,
                           force: bool = False) -> JSONResponse:
            """Remove books whose files are gone. A scan only marks
            them; this is the deliberate part."""
            try:
                report = self._forget.run(apply=apply, force=force)
            except UnpluggedError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            return JSONResponse({k: v for k, v in report.items() if k != "plan"})

        @app.post("/api/books/name-series")
        def name_series(apply: bool = False) -> JSONResponse:
            """File books under the series their filenames name."""
            report = self._series.run(apply=apply)
            return JSONResponse({k: v for k, v in report.items() if k != "plan"})

        @app.post("/api/books/dedupe")
        def dedupe_books(apply: bool = False) -> JSONResponse:
            """One row per work. No file is deleted — the spare copies
            keep their rows and can be restored."""
            report = self._duplicates.run(apply=apply)
            return JSONResponse({k: v for k, v in report.items()
                                 if k != "plan"} | {"plan": [
                {"title": g["title"], "keep": g["keep"]["path"],
                 "reason": g["reason"],
                 "spares": [c["path"] for c in g["spares"]]}
                for g in report["plan"]]})

        @app.post("/api/books/restore-duplicates")
        def restore_duplicates() -> JSONResponse:
            return JSONResponse({"restored": self._duplicates.restore()})

        @app.post("/api/books/repair-titles")
        def repair_titles(apply: bool = False) -> JSONResponse:
            """Correct filenames-as-titles from each author's works."""
            return JSONResponse(self._titles.run(apply=apply))

        @app.post("/api/authors/{author_id}/enrich")
        def enrich_author(author_id: int) -> JSONResponse:
            """Look this author up. Never fails the request: a provider
            being unreachable is reported, not raised."""
            try:
                return JSONResponse(self._enricher.enrich(author_id))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None

        @app.get("/api/status")
        def index_status() -> JSONResponse:
            status = browse.status()
            status["pending_review"] = enricher.pending_count()
            return JSONResponse(status)

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

        @app.delete("/api/books/{book_id}/cover")
        def drop_cover(book_id: int) -> JSONResponse:
            """Say a cover is wrong. Page one of a PDF is the book's own
            cover nine times in ten; the tenth is a title page, and a
            lettered tile says more than that does. Kept as yours and
            locked, so no pass ever fetches another."""
            book_or_404(book_id)
            row = self._database.connection.execute(
                "SELECT cover_path FROM metadata WHERE book_id = ?",
                (book_id,)).fetchone()
            stored = row["cover_path"] if row else None
            self._database.connection.execute(
                "UPDATE metadata SET cover_path = NULL WHERE book_id = ?",
                (book_id,))
            self._credits.record(book_id, "cover")
            if stored:
                # The picture itself goes too: it is derived, and keeping
                # it would only confuse the next person to look in there.
                file = Path(self._config.cover_cache) / stored
                file.unlink(missing_ok=True)
            return JSONResponse({"dropped": book_id, "cover": None})

        @app.put("/api/books/{book_id}/authors")
        def put_authors(book_id: int, body: BookAuthors) -> JSONResponse:
            """Say who wrote a book. Kept as yours and locked: a file
            that is confidently wrong stays wrong on every rescan."""
            try:
                return JSONResponse(
                    {"authors": self._credits.set(book_id, body.authors)})
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None

        @app.put("/api/books/{book_id}/series")
        def put_series(book_id: int, body: BookSeries) -> JSONResponse:
            """File a book under a series by hand — for the ones whose
            filenames carry no number and whose files say nothing."""
            try:
                return JSONResponse(
                    self._credits.set_series(book_id, body.series, body.index))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None

        @app.get("/api/books/{book_id}/annotations")
        def get_annotations(book_id: int) -> JSONResponse:
            # Not book_or_404: a book whose file has vanished still has
            # a row, and what you wrote on it is still yours.
            known_book_or_404(book_id)
            return JSONResponse(
                {"annotations": self._annotations.for_book(book_id)})

        @app.post("/api/books/{book_id}/annotations", status_code=201)
        def post_annotation(book_id: int, body: NewAnnotation) -> JSONResponse:
            known_book_or_404(book_id)
            try:
                mark = self._annotations.add(
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
                    self._annotations.update(annotation_id, **changes))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            except AnnotationError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None

        @app.delete("/api/annotations/{annotation_id}", status_code=204)
        def delete_annotation(annotation_id: int) -> Response:
            if not self._annotations.remove(annotation_id):
                raise HTTPException(status_code=404,
                                    detail="No such annotation.")
            return Response(status_code=204)

        @app.get("/api/books/{book_id}/file")
        def get_book(book_id: int) -> FileResponse:
            # Addressed by id, not by path: 44% of a real library lives in
            # subfolders, and a relative path with slashes cannot be a
            # single path segment.
            path = book_file(book_or_404(book_id))
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
                    content=zip_unpacked(path),
                    media_type="application/epub+zip",
                    headers={"Content-Disposition":
                             f'inline; filename="{path.name}"'},
                )
            media = self.MEDIA_TYPES.get(
                path.suffix.lower(), "application/octet-stream"
            )
            return FileResponse(path, media_type=media)
