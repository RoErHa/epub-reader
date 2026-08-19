"""Author, series and title, read from one filename.

45 Piers Anthony PDFs browse as a single run-on title:

    Anthony, Piers - Xanth 08 - Crewel Lye

credited to nobody, or to `harry` — whoever ran the scanner. §8.4 made
the filename lead for PDFs, but only for the *title*: `epub_pdf` reads
the author from the Info dictionary alone, so 35 of the 45 have no
author at all.

That first missing step is what breaks the other two. §6.6 runs its
passes authors → books → titles because everything else is asked *of*
an author, and `SeriesFromNames` says so in as many words — it refuses
a name when `not row["author_sort"]`, because there is nobody to agree
with. So the Xanth novels are not a series either, and never could
become one.

The name says all three things, and reading it needs no provider. The
danger is reading it too eagerly, so this pass is narrow by
construction:

> **A file that named itself is not this pass's business.** It acts only
> where the title the database holds *is* the filename, which is the
> mark of a file that said nothing. An EPUB whose OPF gives a real title
> is left alone even when its filename would parse.

> **The author leads, or nothing happens.** `Surname, First - …` at the
> head is a strong shape and it is the one this library uses. A name
> with its author at the *end* — "10 is Better Than 01 - Heather
> Jarman" — is a book called that, and is refused. This is the rule
> `AuthorHint` (§8.13) already applies for cover matching; the
> difference is that here it is written down, which is why it is
> narrower.

> **The series is not judged here.** `SeriesFromNames` already decides
> that, and its rule — two books by one author must agree — is what
> keeps "is Better Than" out of the library. This pass credits the
> author first so that rule can finally run, then asks it, then reads
> the title in the light of its answer. Two implementations of one
> judgement is how they come to disagree.

Nothing here touches a file. The title the file gave is kept in
`metadata.file_title`, and what a reader set by hand is never touched.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from epub_names import PersonName
from epub_series import SeriesFromNames

LOG = logging.getLogger("epub-reader.filenames")

# "Anthony, Piers - <the rest>". The comma is what makes this a name
# written for sorting rather than two words that happen to lead: a
# surname, a comma, given names, and then the book.
AUTHOR = re.compile(r"^(?P<surname>[^,\d]{2,40}),\s*(?P<given>[^-\d]{1,40}?)"
                    r"\s+-\s+(?P<rest>\S.*)$")

# Two people, each written surname-first: "Pratchett, Terry & Gaiman,
# Neil - Good Omens". Read as one name it gave "Terry & Gaiman, Neil
# Pratchett".
AMPERSAND = re.compile(r"\s*&\s*")

# Given names are short, capitalised, and have no brackets in them.
# "Raymond E." and "L. Frank" are names; "A Guide to (Mysterious
# Creatures)" is a title that happens to follow a comma, and
# "Maxim (ed.)" is an anthology saying who edited it — §5 has a role
# column for that, and until something fills it, saying nothing beats
# saying the wrong thing.
GIVEN = re.compile(r"^(?:[^\W\d_][\w.'\u2019-]*)(?:\s+[^\W\d_][\w.'\u2019-]*){0,3}$",
                   re.UNICODE)

# A title never ends in ".txt". 12 books here do — `Volk.doc`,
# `Running with the Demon.txt`, `Nanny Ogg's Cookbook.htm` — the mark of
# a library converted from one format to another by somebody who kept the
# old name. Named formats only: a closed list cannot mistake `Mr. Norrell`
# or a title ending in an initial for a file.
EXTENSION = re.compile(
    r"\.(?:doc|docx|txt|rtf|lit|pdb|htm|html|pdf|epub|mobi|prc|azw3?|zip)$",
    re.I)

# What is left once the author is off the front, when it names a shelf:
# "Xanth 08 - Crewel Lye". Only read after SeriesFromNames has agreed
# that the shelf is real.
SHELF = re.compile(r"^(?P<series>.{2,48}?)\s+(?P<number>\d{1,3}(?:\.\d)?)"
                   r"\s*-\s*(?P<title>\S.*)$")


class CreditsFromNames:
    def __init__(self, database, series: SeriesFromNames | None = None) -> None:
        self._db = database
        self._series = series or SeriesFromNames(database)

    # ── reading the names ─────────────────────────────────────────────
    @classmethod
    def read(cls, stem: str) -> dict | None:
        """What a filename says, or nothing if it does not lead with a
        name written surname-first."""
        match = AUTHOR.match(stem)
        if not match:
            return None
        surname = match.group("surname").strip()
        given = match.group("given").strip()

        # "Pratchett, Terry & Gaiman, Neil" is two people.
        parts = AMPERSAND.split(f"{surname}, {given}")
        people = []
        for part in parts:
            name, _, rest = part.partition(",")
            if not cls._is_a_name(name.strip(), rest.strip()):
                return None
            person = PersonName.parse(f"{name.strip()}, {rest.strip()}")
            if not person:
                return None
            people.append(person.display)
        if not people:
            return None
        return {"author": people[0], "authors": people,
                "rest": match.group("rest").strip()}

    @staticmethod
    def _is_a_name(surname: str, given: str) -> bool:
        """Whether these two halves are a person rather than a title
        that happens to hold a comma."""
        if not surname or not given:
            return False
        # A surname may be hyphenated; a *spaced* dash is a separator.
        # "e-Book - Feist, Raymond E." is a junk prefix, not a name.
        if " - " in surname or " - " in given:
            return False
        return bool(GIVEN.match(given))

    @staticmethod
    def title_of(rest: str, series: str | None,
                 index: float | None = None) -> str:
        """The words that are the book.

        With a series believed, everything up to and including its
        number is the shelf talking. Without one, the words are left
        whole rather than cut at a guess — a number nobody has
        corroborated is not evidence of anything.

        The file need not spell the shelf the way the library does.
        "Geodessey 01 - Isle of Women" is a misspelling of Geodyssey,
        which is exactly why nothing corroborated it and why somebody
        had to file it by hand; once they have, the words in front of
        the number are the shelf however they are spelt. **The number
        is what ties them to it**, so it has to agree — without that
        there is nothing connecting these words to that shelf, and they
        stay part of the book.
        """
        rest = EXTENSION.sub("", rest).strip()
        if not series:
            return rest
        match = SHELF.match(rest)
        if not match:
            return rest
        named = match.group("series").strip().casefold() == series.casefold()
        numbered = index is not None and float(match.group("number")) == index
        if not (named or numbered):
            return rest
        return match.group("title").strip()

    # ── who wrote it ──────────────────────────────────────────────────
    def _credit_plan(self, author: str | None,
                     formats: list[str] | None) -> list[dict]:
        plan = []
        for row in self._nameless(author, formats):
            found = self.read(self.source_name(row) or "")
            if not found:
                continue
            if author and found["author"] != author:
                continue
            if found["author"] == row["author"]:
                continue          # already credited to the right person
            plan.append({"id": row["id"], "author": found["author"],
                         "authors": found["authors"], "was": row["author"]})
        return plan

    def _nameless(self, author: str | None, formats: list[str] | None,
                  ours: bool = False):
        """Books whose title is still their filename, and whose author
        nobody has settled by hand.

        `ours` also takes in the books this pass has already retitled.
        A title it wrote is still its own to refine: a book alone on its
        shelf keeps its number, and when a sibling turns up the number
        becomes the shelf talking. Without this the first answer stood
        for ever, because it no longer looked like a filename.
        """
        sql = """
            SELECT b.id, b.path, a.name AS author
              FROM book b
              JOIN metadata m ON m.book_id = b.id
              LEFT JOIN book_author ba ON ba.book_id = b.id AND ba.position = 0
              LEFT JOIN author a ON a.id = ba.author_id
             WHERE b.missing_since IS NULL AND b.duplicate_of IS NULL
               AND m.title IS NOT NULL
               AND NOT EXISTS (
                     SELECT 1 FROM field_provenance f
                      WHERE f.book_id = b.id AND f.field IN ('authors', 'title')
                        AND (f.origin = 'user' OR f.locked = 1))
        """
        params: list = []
        if formats:
            sql += f" AND b.format IN ({','.join('?' * len(formats))})"
            params.extend(formats)
        rows = self._db.connection.execute(sql, params).fetchall()
        # The mark of a file that said nothing about itself. Compared in
        # Python because SQLite has no basename().
        return [r for r in rows
                if self._unnamed(r)
                or (ours and self._wrote_title(r["id"]))]

    def _unnamed(self, row) -> bool:
        """Whether the title the library shows came out of a folder.

        The obvious test is that it *is* the filename, and that was the
        only test for a while. It is too narrow: four Bio of a Space
        Tyrant EPUBs carry `Anthony, Piers - Tyrant 1 - Refugee` in their
        own OPF while the file on disk has the author appended a second
        time, so the two are not equal and the pass walked past them. The
        file did speak; what it said was a filename.

        So either the title is the filename, or the title is *shaped*
        like one — leading with a surname, a comma and a forename, which
        is how a shelf is written and not how a book is titled.
        """
        return self.source_name(row) is not None

    def source_name(self, row) -> str | None:
        """The string to read the book's facts out of.

        Usually the filename, because usually the title *is* the
        filename. When the stored title is itself filename-shaped it
        wins, though: the Tyrant EPUBs carry a clean
        `Anthony, Piers - Tyrant 1 - Refugee` in their OPF while the file
        on disk has the author appended a second time, and reading the
        file would leave "Refugee - Anthony, Piers" as the title. Take
        the tidier of the two names for the same book.
        """
        title = self._title_of(row["id"])
        stem = Path(row["path"]).stem
        if title and AUTHOR.match(title):
            return title
        if title and title == stem:
            return stem
        return None

    def _wrote_title(self, book_id: int) -> bool:
        return self._db.connection.execute(
            "SELECT 1 FROM field_provenance WHERE book_id = ? AND"
            " field = 'title' AND origin = 'provider' AND source = 'filename'",
            (book_id,)).fetchone() is not None

    def _title_of(self, book_id: int) -> str | None:
        row = self._db.connection.execute(
            "SELECT title FROM metadata WHERE book_id = ?", (book_id,)
        ).fetchone()
        return row["title"] if row else None

    def _credit(self, item: dict, now: int) -> None:
        """Everyone the name gives, in the order it gives them — §5 says
        credit order matters, and "Pratchett, Terry & Gaiman, Neil" says
        who came first."""
        cursor = self._db.connection
        cursor.execute("DELETE FROM book_author WHERE book_id = ?", (item["id"],))
        for position, name in enumerate(item.get("authors") or [item["author"]]):
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
        self._record(item["id"], "authors", now)

    # ── what it is called ─────────────────────────────────────────────
    def _title_plan(self, author: str | None,
                    formats: list[str] | None) -> list[dict]:
        """Read after the series is settled, so a number that turned out
        to be a shelf can come off and one that did not stays put."""
        plan = []
        for row in self._nameless(author, formats, ours=True):
            found = self.read(self.source_name(row) or Path(row["path"]).stem)
            if not found:
                continue
            if author and found["author"] != author:
                continue
            filed = self._db.connection.execute(
                "SELECT s.name, m.series_index FROM metadata m"
                " LEFT JOIN series s ON s.id = m.series_id"
                " WHERE m.book_id = ?", (row["id"],)).fetchone()
            title = self.title_of(found["rest"], filed["name"],
                                  filed["series_index"])
            if title and title != self._title_of(row["id"]):
                plan.append({"id": row["id"], "title": title})
        return plan

    def _retitle(self, item: dict, now: int) -> None:
        self._db.connection.execute(
            """UPDATE metadata
                  SET file_title = COALESCE(file_title, title), title = ?
                WHERE book_id = ?""", (item["title"], item["id"]))
        self._record(item["id"], "title", now)

    # ── writing it down ───────────────────────────────────────────────
    def _record(self, book_id: int, field: str, now: int) -> None:
        """Recorded, or the next rescan takes it straight back out."""
        self._db.connection.execute(
            """
            INSERT INTO field_provenance (book_id, field, origin, source,
                                          updated_at)
            VALUES (?, ?, 'provider', 'filename', ?)
            ON CONFLICT(book_id, field) DO UPDATE SET
                origin = 'provider', source = 'filename',
                updated_at = excluded.updated_at
            """, (book_id, field, now))

    def run(self, apply: bool = False, author: str | None = None,
            formats: list[str] | None = None) -> dict:
        """Authors, then series, then titles — §6.6's order, for the
        same reason: everything else is asked of an author."""
        now = int(time.time())
        credits = self._credit_plan(author, formats)
        report = {"credited": 0, "would_credit": len(credits),
                  "filed": 0, "would_file": 0, "retitled": 0,
                  "would_retitle": 0, "plan": credits}
        if not apply:
            # Without the author written down there is nobody for the
            # series rule to agree with, so a dry run can only honestly
            # report the first step.
            return report

        for item in credits:
            self._credit(item, now)
            report["credited"] += 1
            LOG.info("book %s credited to %s (was %s)",
                     item["id"], item["author"], item["was"] or "nobody")

        filed = self._series.run(apply=True, author=author, formats=formats)
        report["filed"] = filed["named"]

        titles = self._title_plan(author, formats)
        report["would_retitle"] = len(titles)
        for item in titles:
            self._retitle(item, now)
            report["retitled"] += 1
        return report

    @staticmethod
    def summary(report: dict) -> str:
        done = report["credited"] + report["filed"] + report["retitled"]
        if not done and not report["would_credit"]:
            return "Names: nothing to read."
        if not done:
            return (f"Names: would credit {report['would_credit']} books"
                    " from their filenames.")
        # A later run credits nobody because everybody is credited, and
        # may still file and retitle. Reporting only the first step is
        # how a pass that worked comes to look as though it did not.
        return (f"Names: credited {report['credited']} books, filed"
                f" {report['filed']} under a series, retitled"
                f" {report['retitled']}.")
