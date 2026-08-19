"""The genre vocabulary (decision 5).

Providers return BISAC codes, free text and folksonomy tags
interchangeably, so everything normalises onto a fixed ~40-term subset of
BISAC subject headings held in `data/genres.tsv`, with `data/genre_map.tsv`
translating what providers actually say.

**Unmapped strings are logged, never guessed.** The map is meant to grow
from real data; inventing a mapping would quietly file books wrongly.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path

from epub_names import fold

LOG = logging.getLogger("epub-reader.genres")
DATA = Path(__file__).resolve().parent / "data"

# Providers path their subjects with either separator, and often end on a
# meaningless leaf: "Fantasy - General", "Fiction / Fantasy / General".
SEGMENTS = re.compile(r"\s*[/>]\s*|\s+[-–—]\s+")
GENERIC = {"general", "fiction", "series", "other", "misc", "miscellaneous"}


class GenreVocabulary:
    def __init__(self, genres: dict[str, str], mapping: dict[str, str]) -> None:
        self.genres = genres            # bisac code → display name
        self.mapping = mapping          # folded provider string → code
        self.unmapped: Counter = Counter()

    @classmethod
    def load(cls, root: Path | None = None) -> GenreVocabulary:
        root = Path(root) if root else DATA
        genres = dict(cls._rows(root / "genres.tsv"))
        mapping = {fold(k): v for k, v in cls._rows(root / "genre_map.tsv")}
        # A genre's own name always maps to itself, so the map does not
        # have to repeat the vocabulary.
        for code, name in genres.items():
            mapping.setdefault(fold(name), code)
        return cls(genres, mapping)

    @staticmethod
    def _rows(path: Path):
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0].strip() and parts[1].strip():
                yield parts[0].strip(), parts[1].strip()

    def map(self, subject: str | None) -> str | None:
        """The genre this provider string belongs to, or nothing."""
        if not subject or not str(subject).strip():
            return None
        raw = str(subject).strip()

        # A BISAC code, given directly.
        if raw.upper() in self.genres:
            return self.genres[raw.upper()]

        key = fold(raw)
        code = self.mapping.get(key)
        if code is None:
            # Walk the path from the most specific end, skipping
            # meaningless leaves: "Fantasy - General" is fantasy.
            parts = [p.strip() for p in SEGMENTS.split(raw) if p.strip()]
            for part in reversed(parts):
                if fold(part) in GENERIC:
                    continue
                code = self.mapping.get(fold(part))
                if code:
                    break
            if code is None:
                # Only now settle for a generic segment.
                for part in reversed(parts):
                    code = self.mapping.get(fold(part))
                    if code:
                        break
        if code is None:
            # Recorded, not guessed — this is how the map grows.
            self.unmapped[key] += 1
            LOG.debug("unmapped genre: %r", raw)
            return None
        return self.genres.get(code)

    def map_all(self, subjects) -> list[str]:
        """Mapped genres, in order, without repeats."""
        out: list[str] = []
        for subject in subjects or []:
            name = self.map(subject)
            if name and name not in out:
                out.append(name)
        return out
