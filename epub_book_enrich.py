"""Enriching a book: apply, queue, or leave it alone.

Decision 4 sets the bands — at or above 0.90 we write, between 0.60 and
0.90 a human looks, below that we leave the book alone.

The provenance rule from §5 is what makes this safe to run across a whole
library: **a field may be overwritten only if its origin is the file and
it is unlocked.** A value set by hand is never overwritten automatically.
"""
from __future__ import annotations

import json
import logging
import time

from epub_match import Candidate, MatchScorer, search_title
from epub_provenance import Provenance
from epub_providers import ProviderError

LOG = logging.getLogger("epub-reader.enrich")

# Fields enrichment may write. `title` is deliberately absent: the file's
# title is what you see in your own folders, and changing it silently
# would be disorienting. It can still be accepted explicitly in review.
WRITABLE = ("synopsis", "publisher", "isbn13", "first_published", "language")


class BookEnricher:
    def __init__(self, database, providers, scorer: MatchScorer | None = None):
        self._db = database
        self._providers = list(providers)
        self._scorer = scorer or MatchScorer()
        # A provider that has rate-limited us is done for this run.
        # Google Books answers 429 to every unauthenticated request, and
        # asking it once per book just fills the log.
        self._exhausted: set[int] = set()

    # ── the run ───────────────────────────────────────────────────────
    def enrich(self, book_id: int) -> dict:
        book = self._book(book_id)
        if book is None:
            raise LookupError(f"No book with id {book_id}.")

        candidates, errors = self._gather(book)
        if not candidates:
            report = {"verdict": "none", "score": 0.0, "candidates": 0}
            if errors:
                report["error"] = "; ".join(errors)
            return report

        ranked = self._scorer.rank(book, candidates)
        best = ranked[0]
        self._remember(book_id, best)

        if best.verdict == "apply":
            written = self._apply(book_id, best.candidate)
            self._set_status(book_id, best, "accepted")
            return {"verdict": "apply", "score": best.score,
                    "candidates": len(candidates), "written": written}
        if best.verdict == "review":
            return {"verdict": "review", "score": best.score,
                    "candidates": len(candidates)}
        # Below the floor: leave the book untouched and keep nothing.
        self._forget(book_id)
        return {"verdict": "reject", "score": best.score,
                "candidates": len(candidates)}

    def _gather(self, book: dict) -> tuple[list[Candidate], list[str]]:
        # The query is cleaned; the comparison is not. A third of the
        # real library finds nothing when searched by its raw title.
        query = search_title(book.get("title"))
        author = (book.get("authors") or [None])[0]
        found: list[Candidate] = []
        errors: list[str] = []
        for provider in self._providers:
            if id(provider) in self._exhausted:
                continue
            try:
                found.extend(provider.find_books(query, author))
            except ProviderError as exc:
                # One provider being down must not stop the others.
                LOG.warning("provider failed for %r: %s", query, exc)
                errors.append(str(exc))
                if "429" in str(exc) or "rate" in str(exc).lower():
                    LOG.warning(
                        "%s is rate limiting us; skipping it for this run",
                        type(provider).__name__,
                    )
                    self._exhausted.add(id(provider))
        return found, errors

    # ── writing ───────────────────────────────────────────────────────
    def _apply(self, book_id: int, candidate: Candidate) -> list[str]:
        allowed = self._writable_fields(book_id)
        values = {
            "synopsis": candidate.synopsis,
            "publisher": candidate.publisher,
            "isbn13": candidate.isbn13,
            "first_published": candidate.first_published,
            "language": candidate.language,
        }
        written = []
        now = int(time.time())
        for field, value in values.items():
            if value in (None, "") or field not in allowed:
                continue
            self._db.connection.execute(
                f"UPDATE metadata SET {field} = ? WHERE book_id = ?",
                (value, book_id),
            )
            Provenance(self._db).record(book_id, field, "provider",
                                        candidate.source, now=now)
            written.append(field)
        return written

    def _writable_fields(self, book_id: int) -> set[str]:
        """Fields enrichment is allowed to touch — the provenance rule,
        made enforceable."""
        held = {
            r["field"] for r in self._db.connection.execute(
                "SELECT field FROM field_provenance"
                " WHERE book_id = ? AND (origin = 'user' OR locked = 1)",
                (book_id,),
            )
        }
        return set(WRITABLE) - held

    # ── review queue ──────────────────────────────────────────────────
    def _remember(self, book_id: int, scored) -> None:
        # One row per book: a fresh run supersedes the last.
        self._forget(book_id)
        self._db.connection.execute(
            """
            INSERT INTO metadata_candidate
                (book_id, provider, source_id, payload, parts, score,
                 status, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (book_id, scored.candidate.source, scored.candidate.source_id,
             json.dumps(scored.candidate.as_dict()), json.dumps(scored.parts),
             scored.score, int(time.time())),
        )

    def _forget(self, book_id: int) -> None:
        self._db.connection.execute(
            "DELETE FROM metadata_candidate WHERE book_id = ? AND status = 'pending'",
            (book_id,),
        )

    def _set_status(self, book_id: int, scored, status: str) -> None:
        self._db.connection.execute(
            "UPDATE metadata_candidate SET status = ? WHERE book_id = ?"
            " AND status = 'pending'",
            (status, book_id),
        )

    def accept(self, candidate_id: int) -> dict:
        row = self._candidate(candidate_id)
        candidate = Candidate(**{
            k: v for k, v in json.loads(row["payload"]).items()
            if k in Candidate.__dataclass_fields__
        })
        written = self._apply(row["book_id"], candidate)
        self._db.connection.execute(
            "UPDATE metadata_candidate SET status = 'accepted' WHERE id = ?",
            (candidate_id,),
        )
        return {"accepted": candidate_id, "written": written}

    def reject(self, candidate_id: int) -> dict:
        self._candidate(candidate_id)
        self._db.connection.execute(
            "UPDATE metadata_candidate SET status = 'rejected' WHERE id = ?",
            (candidate_id,),
        )
        return {"rejected": candidate_id}

    # ── the queue ─────────────────────────────────────────────────────
    def pending(self, limit: int = 100) -> list[dict]:
        """Everything waiting for a decision, with the reasoning shown.

        A bare score is not reviewable, so each item carries the parts it
        was made of and exactly what would change.
        """
        rows = self._db.connection.execute(
            """
            SELECT c.id, c.book_id, c.provider, c.payload, c.parts, c.score,
                   m.title, m.synopsis, m.publisher, m.isbn13,
                   m.first_published, m.language, m.cover_path
              FROM metadata_candidate c
              JOIN metadata m ON m.book_id = c.book_id
              JOIN book b ON b.id = c.book_id AND b.missing_since IS NULL
             WHERE c.status = 'pending'
             ORDER BY c.score DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()

        items = []
        for row in rows:
            candidate = json.loads(row["payload"])
            items.append({
                "id": row["id"],
                "score": row["score"],
                "provider": row["provider"],
                "parts": json.loads(row["parts"] or "{}"),
                "book": {
                    "id": row["book_id"], "title": row["title"],
                    "cover": (f"/api/covers/{row['cover_path']}"
                              if row["cover_path"] else None),
                },
                "candidate": candidate,
                "changes": self._changes(row, candidate),
            })
        return items

    def _changes(self, row, candidate: dict) -> list[dict]:
        """Only the fields that would actually change, and are allowed to."""
        allowed = self._writable_fields(row["book_id"])
        out = []
        for field in WRITABLE:
            new = candidate.get(field)
            if new in (None, "") or field not in allowed:
                continue
            # `keys()` is not optional: sqlite3.Row has no
            # __contains__, so `field in row` raises rather than answers.
            old = row[field] if field in row.keys() else None  # noqa: SIM118
            if (old or None) == (new or None):
                continue
            out.append({"field": field, "from": old, "to": new})
        return out

    def accept_all(self) -> dict:
        accepted = 0
        for item in self.pending(limit=1000):
            self.accept(item["id"])
            accepted += 1
        return {"accepted": accepted}

    def pending_count(self) -> int:
        return self._db.connection.execute(
            "SELECT COUNT(*) n FROM metadata_candidate WHERE status = 'pending'"
        ).fetchone()["n"]

    # ── reads ─────────────────────────────────────────────────────────
    def _candidate(self, candidate_id: int):
        row = self._db.connection.execute(
            "SELECT * FROM metadata_candidate WHERE id = ?", (candidate_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"No candidate with id {candidate_id}.")
        return row

    def _book(self, book_id: int) -> dict | None:
        row = self._db.connection.execute(
            """
            SELECT m.title, m.first_published, m.language, m.isbn13,
                   (SELECT GROUP_CONCAT(a.name, '|')
                      FROM book_author ba JOIN author a ON a.id = ba.author_id
                     WHERE ba.book_id = m.book_id) AS authors
              FROM metadata m
              JOIN book b ON b.id = m.book_id AND b.missing_since IS NULL
             WHERE m.book_id = ?
            """,
            (book_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "title": row["title"], "first_published": row["first_published"],
            "language": row["language"], "isbn13": row["isbn13"],
            "authors": row["authors"].split("|") if row["authors"] else [],
        }
