"""A co-author written into the title, because a file has one name.

    Anthony, Piers - Dead Morn (with Roberto Fuentes).pdf
    Asprin, Robert Lynn - Catwoman - Tiger Hunt (With Lynn Abbey).epub

*Dead Morn* is by Piers Anthony **and Roberto Fuentes**. Whoever filed it
had one filename and two authors, so the second went in brackets — and
every pass since has read it as part of the book's name.

This is §12.3's **tier 0**: a rule, not a lookup. No provider, no key, no
network. It is built first because it costs nothing, and because it makes
the strings shorter and truer before the later tiers ever send one
anywhere.

**13 books on the shelves carry it**, measured 2026-08-19. Every other
parenthetical in the library is the scanner talking — `(not ocr)`,
`(v2.1)`, `(txt)`, `(9781982156176)`, `(1946)`, `(Book 1)` — and the word
`with` is what tells the two apart. It does most of the work; the rest is
guards.

> **A co-author is a *second* author, so there must be a first.** Two
> P. N. Elrod PDFs credit nobody, because their filenames name him and
> `epub_pdf` reads only the Info dictionary. Crediting Nigel Bennett
> alone would promote the collaborator to author, which is worse than
> leaving the bracket where it is. So those books are left for
> `./run.sh names` — §6.6's order once more: authors first, then anything
> asked of them.

> **Only the `with` bracket goes.** `Elvenblood (With Mercedes Lackey)
> (v1.0)` keeps its version tag: it is somebody else's rubbish to clear,
> and a pass that tidies more than it was asked to is one nobody can
> predict.
"""
from __future__ import annotations

import logging
import re
import time

from epub_names import PersonName
from epub_provenance import Candidates, Provenance

LOG = logging.getLogger("epub-reader.coauthors")

# "(with Roberto Fuentes)", "(With Lynn Abbey)". Only `with` — `and` and
# `&` open the door to "(and Other Stories)" and buy us nothing: every
# one of the 13 real cases says `with`.
CREDIT = re.compile(r"\s*\(\s*with\s+(?P<names>[^)]+?)\s*\)", re.IGNORECASE)

# "James Richey and Alan Riggs" is two people in one bracket.
SPLIT = re.compile(r"\s*(?:,|&|\band\b)\s*", re.IGNORECASE)


class CoAuthorsInTitles:
    def __init__(self, database) -> None:
        self._db = database
        self._provenance = Provenance(database)

    # ── reading it ────────────────────────────────────────────────────
    @classmethod
    def read(cls, title: str) -> tuple[str, list[str]] | None:
        """The title without the bracket, and the people who were in it.

        Nothing at all if the bracket holds something that is not a
        person — which the `with` already makes unlikely, but `(with 2)`
        is cheap to refuse and expensive to write down.
        """
        match = CREDIT.search(title or "")
        if not match:
            return None
        people = [p for part in SPLIT.split(match.group("names"))
                  if (p := part.strip())]
        if not people or not all(cls._is_a_name(p) for p in people):
            return None
        cleaned = (title[:match.start()] + " " + title[match.end():]).strip()
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return (cleaned, people) if cleaned else None

    @staticmethod
    def _is_a_name(text: str) -> bool:
        """A person has at least two parts and no digits in them.

        Initials count as a part — "Robert E. Margroff" is three — and a
        single word is refused, because one word in a bracket is as
        likely to be a format as a person.
        """
        parts = text.split()
        if len(parts) < 2 or any(any(c.isdigit() for c in p) for p in parts):
            return False
        return all(p[:1].isalpha() for p in parts)

    def plan(self) -> list[dict]:
        """What would change. Changes nothing."""
        plan = []
        for row in self._books():
            # `file_title` is what the file said, and it does not change
            # between runs; the current title may already have had the
            # bracket taken off by §8.13a.
            source = row["file_title"] or row["title"]
            found = self.read(source)
            if not found:
                continue
            _, people = found
            # Only the current title is rewritten, and only if it still
            # holds the bracket. Otherwise this pass credits people and
            # leaves the title exactly as it found it.
            cleaned = self.read(row["title"])
            title = cleaned[0] if cleaned else row["title"]
            # A second author needs a first. Left for ./run.sh names.
            if not row["author"]:
                LOG.info("%r credits nobody yet; leaving it for the names pass",
                         row["title"])
                continue
            credited = {name.casefold() for name in
                        (row["credited"] or "").split("|") if name}
            fresh = [p for p in people
                     if (person := PersonName.parse(p))
                     and person.display.casefold() not in credited]
            if not fresh and title == row["title"]:
                continue          # nothing to credit and nothing to rename
            plan.append({"id": row["id"], "title": title, "people": fresh,
                         "was": row["title"]})
        return plan

    def _books(self):
        """Books whose title still holds a bracket, and whose title and
        authors nobody has settled by hand."""
        return self._db.connection.execute(
            """
            SELECT b.id, m.title, m.file_title,
                   (SELECT a.name FROM book_author ba
                      JOIN author a ON a.id = ba.author_id
                     WHERE ba.book_id = b.id ORDER BY ba.position LIMIT 1)
                     AS author,
                   (SELECT GROUP_CONCAT(a.name, '|') FROM book_author ba
                      JOIN author a ON a.id = ba.author_id
                     WHERE ba.book_id = b.id) AS credited
              FROM book b
              JOIN metadata m ON m.book_id = b.id
             WHERE b.missing_since IS NULL AND b.duplicate_of IS NULL
               AND (m.title LIKE '%(%' OR m.file_title LIKE '%(%')
            """
            + Candidates.clause("authors", derived_from="title")
            + Candidates.clause("title", derived_from="title")
        ).fetchall()

    # ── writing it down ───────────────────────────────────────────────
    def run(self, apply: bool = False) -> dict:
        plan = self.plan()
        report = {"credited": 0, "would_credit": len(plan),
                  "people": sum(len(p["people"]) for p in plan), "plan": plan}
        if not apply:
            return report

        now = int(time.time())
        for item in plan:
            self._credit(item, now)
            if item["title"] != item["was"]:
                self._retitle(item, now)
            report["credited"] += 1
            LOG.info("book %s: %r -> %r, with %s", item["id"], item["was"],
                     item["title"], ", ".join(item["people"]) or "nobody new")
        return report

    def _credit(self, item: dict, now: int) -> None:
        cursor = self._db.connection
        position = cursor.execute(
            "SELECT COALESCE(MAX(position), -1) + 1 n FROM book_author"
            " WHERE book_id = ?", (item["id"],)).fetchone()["n"]
        for name in item["people"]:
            person = PersonName.parse(name)
            if not person:
                continue
            cursor.execute(
                "INSERT OR IGNORE INTO author (name, sort_name) VALUES (?, ?)",
                (person.display, person.sort))
            author_id = cursor.execute(
                "SELECT id FROM author WHERE name = ?",
                (person.display,)).fetchone()["id"]
            cursor.execute(
                "INSERT OR IGNORE INTO book_author (book_id, author_id, role,"
                " position) VALUES (?, ?, 'author', ?)",
                (item["id"], author_id, position))
            position += 1
        if item["people"]:
            self._provenance.record(item["id"], "authors", "provider",
                                    "title", now=now)

    def _retitle(self, item: dict, now: int) -> None:
        self._db.connection.execute(
            """UPDATE metadata
                  SET file_title = COALESCE(file_title, title), title = ?
                WHERE book_id = ?""", (item["title"], item["id"]))
        self._provenance.record(item["id"], "title", "provider",
                                "title", now=now)

    @staticmethod
    def summary(report: dict) -> str:
        if not report["credited"] and not report["would_credit"]:
            return "Co-authors: nothing written into a title."
        if not report["credited"]:
            return (f"Co-authors: would free {report['would_credit']} titles"
                    f" and credit {report['people']} people.")
        return (f"Co-authors: freed {report['credited']} titles and credited"
                f" {report['people']} people who were in brackets.")
