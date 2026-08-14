"""Reading position and status.

Decision 7: one shared position per book across every device, last write
wins — no per-device state, no conflict UI. So this is deliberately a
plain upsert rather than anything with vector clocks in it.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from epub_db import Database

STATUSES = frozenset({"unread", "reading", "finished", "abandoned"})


@dataclass(frozen=True)
class ReadingState:
    content_hash: str
    cfi: str | None
    fraction: float
    status: str
    opened_at: int
    finished_at: int | None

    @property
    def percent(self) -> int:
        return round(self.fraction * 100)

    def as_dict(self) -> dict[str, object]:
        return {
            "cfi": self.cfi,
            "fraction": self.fraction,
            "percent": self.percent,
            "status": self.status,
            "opened_at": self.opened_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_row(cls, row) -> "ReadingState":
        return cls(
            content_hash=row["content_hash"],
            cfi=row["cfi"],
            fraction=row["fraction"],
            status=row["status"],
            opened_at=row["opened_at"] or 0,
            finished_at=row["finished_at"],
        )


class ReadingStateStore:
    def __init__(self, database: Database) -> None:
        self._db = database

    def get(self, content_hash: str) -> ReadingState | None:
        row = self._db.connection.execute(
            "SELECT * FROM reading_state WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return ReadingState.from_row(row) if row else None

    def get_many(self, hashes) -> dict[str, ReadingState]:
        hashes = list(hashes)
        if not hashes:
            return {}
        placeholders = ",".join("?" * len(hashes))
        rows = self._db.connection.execute(
            f"SELECT * FROM reading_state WHERE content_hash IN ({placeholders})",
            hashes,
        ).fetchall()
        return {r["content_hash"]: ReadingState.from_row(r) for r in rows}

    def save(self, content_hash: str, cfi: str | None, fraction) -> ReadingState:
        if not content_hash:
            raise ValueError("content_hash is required to save a position.")
        try:
            fraction = float(fraction)
        except (TypeError, ValueError):
            raise ValueError(f"fraction must be a number, got {fraction!r}.") from None
        fraction = min(1.0, max(0.0, fraction))

        now = int(time.time())
        # Reopening a finished book records the new position but must not
        # silently un-finish it — only set_status() changes that.
        self._db.connection.execute(
            """
            INSERT INTO reading_state
                (content_hash, cfi, fraction, status, opened_at)
            VALUES (?, ?, ?, 'reading', ?)
            ON CONFLICT(content_hash) DO UPDATE SET
                cfi       = excluded.cfi,
                fraction  = excluded.fraction,
                opened_at = excluded.opened_at,
                status    = CASE
                                WHEN reading_state.status IN ('finished', 'abandoned')
                                THEN reading_state.status
                                ELSE 'reading'
                            END
            """,
            (content_hash, cfi, fraction, now),
        )
        return self.get(content_hash)

    def set_status(self, content_hash: str, status: str) -> ReadingState:
        if status not in STATUSES:
            raise ValueError(
                f"{status!r} is not a reading status. "
                f"Use one of: {', '.join(sorted(STATUSES))}."
            )
        now = int(time.time())
        finished_at = now if status == "finished" else None
        self._db.connection.execute(
            """
            INSERT INTO reading_state (content_hash, status, finished_at, opened_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(content_hash) DO UPDATE SET
                status      = excluded.status,
                finished_at = excluded.finished_at
            """,
            (content_hash, status, finished_at, now),
        )
        return self.get(content_hash)
