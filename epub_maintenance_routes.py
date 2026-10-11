"""The maintenance routes: everything §6.3 does, over HTTP.

Split from `epub_routes` for the reason `epub_routes` was split from
`epub_server` — a module you cannot read in one sitting is one nobody
reads. These seventeen have a common shape of their own: each drives a
pass, each is dry-run by default, each changes the database and none
touches a file.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from epub_forget import UnpluggedError
from epub_routes import AuthorMerge, BookAuthors, BookEdit, BookSeries, Routes


class MaintenanceRoutes(Routes):
    """The passes of §6.3, and the two things a reader may say
    by hand. Every one changes the database and none touches a file."""

    def register(self, app: FastAPI) -> None:
        @app.post("/api/books/{book_id}/enrich")
        def enrich_book(book_id: int) -> JSONResponse:
            try:
                return JSONResponse(self.services.books_enricher.enrich(book_id))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None

        @app.get("/api/review")
        def review_queue(limit: int = 100) -> JSONResponse:
            return JSONResponse(
                {"items": self.services.books_enricher.pending(min(limit, 500))})

        @app.post("/api/review/accept-all")
        def accept_all() -> JSONResponse:
            return JSONResponse(self.services.books_enricher.accept_all())

        @app.post("/api/review/{candidate_id}/accept")
        def accept_candidate(candidate_id: int) -> JSONResponse:
            try:
                return JSONResponse(self.services.books_enricher.accept(candidate_id))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None

        @app.post("/api/review/{candidate_id}/reject")
        def reject_candidate(candidate_id: int) -> JSONResponse:
            try:
                return JSONResponse(self.services.books_enricher.reject(candidate_id))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None

        @app.post("/api/authors/dedupe")
        def dedupe_authors(apply: bool = False) -> JSONResponse:
            return JSONResponse(self.services.deduper.run(apply=apply))

        @app.post("/api/authors/merge")
        def merge_authors(body: AuthorMerge) -> JSONResponse:
            """Merge authors the rules would not merge on their own."""
            try:
                return JSONResponse(
                    self.services.deduper.merge(body.keep, body.merge, body.name))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None

        @app.post("/api/books/repair-authors")
        def repair_authors(apply: bool = False) -> JSONResponse:
            return JSONResponse(self.services.repairer.run(apply=apply))

        @app.post("/api/books/forget-missing")
        def forget_missing(apply: bool = False,
                           force: bool = False) -> JSONResponse:
            """Remove books whose files are gone. A scan only marks
            them; this is the deliberate part."""
            try:
                report = self.services.forget.run(apply=apply, force=force)
            except UnpluggedError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from None
            return JSONResponse({k: v for k, v in report.items() if k != "plan"})

        @app.post("/api/books/name-series")
        def name_series(apply: bool = False) -> JSONResponse:
            """File books under the series their filenames name."""
            report = self.services.series.run(apply=apply)
            return JSONResponse({k: v for k, v in report.items() if k != "plan"})

        @app.post("/api/books/dedupe")
        def dedupe_books(apply: bool = False) -> JSONResponse:
            """One row per work. No file is deleted — the spare copies
            keep their rows and can be restored."""
            report = self.services.duplicates.run(apply=apply)
            return JSONResponse({k: v for k, v in report.items()
                                 if k != "plan"} | {"plan": [
                {"title": g["title"], "keep": g["keep"]["path"],
                 "reason": g["reason"],
                 "spares": [c["path"] for c in g["spares"]]}
                for g in report["plan"]]})

        @app.post("/api/books/restore-duplicates")
        def restore_duplicates() -> JSONResponse:
            return JSONResponse({"restored": self.services.duplicates.restore()})

        @app.post("/api/books/repair-titles")
        def repair_titles(apply: bool = False) -> JSONResponse:
            """Correct filenames-as-titles from each author's works."""
            return JSONResponse(self.services.titles.run(apply=apply))

        @app.post("/api/authors/{author_id}/enrich")
        def enrich_author(author_id: int) -> JSONResponse:
            """Look this author up. Never fails the request: a provider
            being unreachable is reported, not raised."""
            try:
                return JSONResponse(self.services.enricher.enrich(author_id))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None

        @app.delete("/api/books/{book_id}/cover")
        def drop_cover(book_id: int) -> JSONResponse:
            """Say a cover is wrong. Page one of a PDF is the book's own
            cover nine times in ten; the tenth is a title page, and a
            lettered tile says more than that does. Kept as yours and
            locked, so no pass ever fetches another."""
            self.book_or_404(book_id)
            row = self.services.database.connection.execute(
                "SELECT cover_path FROM metadata WHERE book_id = ?",
                (book_id,)).fetchone()
            stored = row["cover_path"] if row else None
            self.services.database.connection.execute(
                "UPDATE metadata SET cover_path = NULL WHERE book_id = ?",
                (book_id,))
            self.services.credits.record(book_id, "cover")
            if stored:
                # The picture itself goes too: it is derived, and keeping
                # it would only confuse the next person to look in there.
                file = Path(self.services.config.cover_cache) / stored
                file.unlink(missing_ok=True)
            return JSONResponse({"dropped": book_id, "cover": None})

        @app.put("/api/books/{book_id}/authors")
        def put_authors(book_id: int, body: BookAuthors) -> JSONResponse:
            """Say who wrote a book. Kept as yours and locked: a file
            that is confidently wrong stays wrong on every rescan."""
            try:
                return JSONResponse(
                    {"authors": self.services.credits.set(book_id, body.authors)})
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None

        @app.patch("/api/books/{book_id}/metadata")
        def patch_metadata(book_id: int, body: BookEdit) -> JSONResponse:
            """Say what any part of the record is. Kept as yours and
            locked, field by field; answers with the book as it now
            reads, so the screen shows what was kept and not what was
            sent."""
            try:
                self.services.editor.edit(book_id, body.named())
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            book = self.services.browse.book(book_id)
            if book is None:
                raise HTTPException(status_code=404, detail="No such book.")
            book["edited"] = self.services.editor.edited(book_id)
            return JSONResponse({
                "book": book,
                "genres": self.services.browse.genres_of(book_id),
            })

        @app.put("/api/books/{book_id}/series")
        def put_series(book_id: int, body: BookSeries) -> JSONResponse:
            """File a book under a series by hand — for the ones whose
            filenames carry no number and whose files say nothing."""
            try:
                return JSONResponse(
                    self.services.credits.set_series(book_id, body.series, body.index))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
