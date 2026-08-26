"""The HTTP service.

M0 scope: serve the app shell and the reading engine, expose the library
read-only, and put the security headers on everything.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import ClassVar

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from epub_annotations import AnnotationStore
from epub_book_enrich import BookEnricher
from epub_browse import BrowseQueries
from epub_coauthors import CoAuthorsInTitles
from epub_config import Config
from epub_covers import CoverBackfill, ProviderCovers
from epub_credits import BookCredits
from epub_db import Database
from epub_dedupe import AuthorDeduplicator
from epub_duplicates import DuplicateFinder
from epub_enrich import AuthorEnricher
from epub_filenames import CreditsFromNames
from epub_forget import ForgetMissing
from epub_fragments import TitlesFromFragments
from epub_index import LibraryIndexer
from epub_maintenance import LibraryRepair
from epub_maintenance_routes import MaintenanceRoutes
from epub_providers import (
    GoogleBooksProvider,
    OpenLibraryProvider,
    PoliteFetcher,
    http_bytes,
)
from epub_reading import ReadingStateStore
from epub_refresh import AuthorRefresh
from epub_repair import TitleAsAuthorRepair
from epub_routes import (
    AppRoutes,
    BrowseRoutes,
    ReadingRoutes,
    Services,
    ShelfRoutes,
)
from epub_search import SearchQueries
from epub_security import (
    SameOriginMiddleware,
    SecurityHeaders,
    SecurityHeadersMiddleware,
)
from epub_series import SeriesFromNames
from epub_shelves import ShelfStore
from epub_tags import ScannerTags
from epub_titles import TitleFromWorks

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


















class EpubGateway:
    """Builds and owns the FastAPI application."""

    MEDIA_TYPES: ClassVar[dict[str, str]] = {
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
            provider, fetch_image=images)

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
        # Added first so it ends up INSIDE the headers middleware:
        # Starlette runs the last-added outermost, and a 403 should
        # still carry the security headers every other response does.
        app.add_middleware(SameOriginMiddleware)
        app.add_middleware(SecurityHeadersMiddleware, headers=self._headers)
        app.mount(
            "/static", StaticFiles(directory=self._static), name="static"
        )
        self._register_routes(app)
        return app

    def _register_routes(self, app: FastAPI) -> None:
        """Hand each group of routes the collaborators it needs.

        This was 530 lines and 57 nested functions — 56% of this module,
        and the largest thing in the codebase was not a class (§10.14).
        """
        services = Services(
            library=self._library, reading=self._reading,
            database=self._database, browse=self._browse,
            search=self._search, shelves=self._shelves,
            annotations=self._annotations, credits=self._credits,
            config=self._config, static=self._static,
            shell=self._static / "index.html",
            media_types=self.MEDIA_TYPES, enricher=self._enricher,
            books_enricher=self._books_enricher, deduper=self._deduper,
            repairer=self._repairer, titles=self._titles,
            series=self._series, duplicates=self._duplicates,
            forget=self._forget, gateway=self,
            service=SERVICE, version=VERSION,
        )
        for group in (AppRoutes, BrowseRoutes, ShelfRoutes, ReadingRoutes,
                      MaintenanceRoutes):
            group(services).register(app)
