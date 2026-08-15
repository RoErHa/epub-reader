"""Keeping the library tidy as it grows.

Books arrive with the same defects the first 1,300 had: a name spelled
three ways, a book filed under its own title, a filename where a title
should be. The three passes in §6.3 fix those, and this runs them
together in the order they depend on:

    authors  →  books  →  titles

Authors first, because everything else is asked *of* an author: a book
filed under its own title is looked up by the name in its filename, and
a title is matched against that author's bibliography and nothing else.
Fixing the names last would mean asking the wrong person.

Dry-run by default, like every pass it wraps. Nothing here touches a
file — the library is read-only, and only the database changes.
"""
from __future__ import annotations

import logging

from epub_dedupe import AuthorDeduplicator
from epub_repair import TitleAsAuthorRepair
from epub_titles import TitleFromWorks

LOG = logging.getLogger("epub-reader.maintenance")


class LibraryRepair:
    def __init__(self, database, resolver, providers, enricher) -> None:
        self._deduper = AuthorDeduplicator(database, resolver,
                                           enricher=enricher)
        self._repairer = TitleAsAuthorRepair(database, providers)
        self._titles = TitleFromWorks(database, enricher)

    def run(self, apply: bool = False) -> dict:
        authors = self._deduper.run(apply=apply)
        books = self._repairer.run(apply=apply)
        titles = self._titles.run(apply=apply)
        return {
            "passes": {"authors": authors, "books": books, "titles": titles},
            "merged": authors["merged"],
            "would_merge": authors["would_merge"],
            "repaired": books["repaired"],
            "would_repair": books["would_repair"],
            "corrected": titles["corrected"],
            "would_correct": titles["would_correct"],
            "unresolved": authors["unresolved"],
            "applied": apply,
        }

    @staticmethod
    def summary(report: dict) -> str:
        """One line, written from the reader's side: what changed, or
        what would."""
        done = report["applied"]
        parts = [
            (report["merged"] if done else report["would_merge"],
             "author", "authors", "merged" if done else "would merge"),
            (report["repaired"] if done else report["would_repair"],
             "book", "books", "given an author" if done
             else "would get an author"),
            (report["corrected"] if done else report["would_correct"],
             "title", "titles", "corrected" if done else "would correct"),
        ]
        said = [
            f"{verb} {count} {one if count == 1 else many}"
            for count, one, many, verb in parts if count
        ]
        if not said:
            return "Library metadata: nothing to do."
        line = "Library metadata: " + ", ".join(said) + "."
        left = len(report["unresolved"])
        if left:
            line += (f" {left} look-alike name"
                     f"{'' if left == 1 else 's'} left for you to judge.")
        return line
