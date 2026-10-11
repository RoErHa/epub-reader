"""The names that are clear to a person and not to the code.

    Colleen McCullough - Masters of Rome 1 - First Man in Rome.pdf
    Andrew Loomis - Drawing the Head and Hands.pdf
    RAINBOWS END by Vernor Vinge.pdf

§8.13a reads one rigid shape — `Surname, First - …` — and believes it
outright. That is why it needs a comma at the head, why it fired on 241
books and no others, and why `10 is Better Than 01 - Heather Jarman` had
to be *refused* rather than understood. None of the names above has a
comma, so §8.13a sees no author; and with no author there is nobody for
`SeriesFromNames` to agree with and nobody to ask a title of (§6.6). All
three facts are lost to one missing comma, and every one of them is
obvious to a reader.

**So the filename stops being the answer and becomes a hypothesis.** Cut
it every plausible way, ask Open Library which reading is real, and keep
the one that comes back corroborated.

Measured on the real library before a line of this was written — every
splittable name nothing here could read, every candidate segment put to
Open Library:

    69 names, 128 distinct segments
    anchored (the segment is an author it knows)     42/69 = 61%

§12.1 named 20% as the line below which this would have been a different
piece of work. It is 61%.

> **Anchoring is not enough, and the same measurement is why.** Open
> Library confirms `Jack Ryan`, `Star Wars`, `Star Trek`, `Ancient
> Egypt`, `Autumn leaves`, `Marcus Aurelius` and `Wizards of the Coast`
> as authors. `Jack Ryan 1 - Without Remorse` is Tom Clancy's, correctly
> credited already, and Jack Ryan is its protagonist. A pass that
> stopped at "the name is an author" would credit a novel to its own
> hero.

What separates those from Colleen McCullough is §12.1's third step, and
the measurement says it is not garnish but the whole safety property:

> **The anchor has to account for the other end of the name.** Ask that
> author's own works whether they hold the book — §6.3's rule, that a
> title is matched against one author's bibliography and never against a
> global title search. Measured over the same sample: **13 of 16 genuine
> names accounted for, and 0 of 8 impostors.**

The three it missed are left exactly as they were, which is the
direction a mistake here should fall. `Edward Gibbon` wrote *The History
of the Decline and Fall of the Roman Empire* and the file drops two
words out of the middle; a rule that let that through would let far
worse through with it.

> **Here a provider's answer wins, because nothing else is offering.**
> Everywhere else the file leads and enrichment fills silence (§6.4). The
> gate has already established that neither the file nor its name can be
> read, so a confirmed reading is the only evidence in the room — which
> is why what this pass writes is recorded as `openlibrary` and not as
> `filename`, and why `title` is writable here and nowhere else.

**Our own author table is not a witness.** §12.1 proposed a local
certainty — one side of the name being an author we already hold — and
the measurement argued it out again: that table holds `Wiley`, `Daddy`,
`bos`, `Niall` and `scribner`, because earlier passes wrote whatever the
files said into it. A segment matching our own rows is therefore weak
evidence, and only 9% of the population matched one in any case. Every
reading goes to the catalogue, and the cache is what makes that
affordable.

Nothing here touches a file. The fetcher is injected, so no test reaches
the network, and every question and answer is kept in `provider_query`.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from epub_editor import TitleWriter
from epub_filenames import EXTENSION, CreditsFromNames
from epub_match import normalise_title
from epub_names import PersonName
from epub_provenance import Candidates, Provenance
from epub_series import SeriesFromNames
from epub_tags import ScannerTags
from epub_titles import COVERAGE, TitlePolish

LOG = logging.getLogger("epub-reader.hypotheses")

# What a filename puts between two facts. A spaced dash — never a
# hyphen inside a surname — an en- or em-dash, or the word itself.
# Underscores are put back as spaces before this runs (§8.13d), so
# `Andrew_Loomis_-_Successful_Drawing` splits like anything else.
SEPARATORS = re.compile(r"\s+[-–—]\s+|\s+by\s+", re.IGNORECASE)

# A parenthesised name: "Star Trek Star Charts (Geoffrey Mandel)".
BRACKET = re.compile(r"[\(\[]([^\)\]]{2,60})[\)\]]")

# The genitive left over from "Holly Lisle's Create a Character", where
# somebody put the dash in after the possessive.
POSSESSIVE = re.compile(r"['’]s$")

# A segment ending in a number is a position on a shelf — "Erevis Cale
# 01", "Masters of Rome 1". Reading it as a person costs a request and
# can only ever be wrong.
SHELF = re.compile(r"\s\d{1,3}(?:\.\d)?$")

# A person we can put to a catalogue has at least a forename and a
# surname. One word is not a candidate: "Trajan" and "Nero" are authors
# at Open Library, and both of those books are *about* the emperor.
NARROWEST, WIDEST = 2, 4


@dataclass(frozen=True)
class Reading:
    """One way of reading a name: this segment is the person, that one
    is the book."""

    author: str
    title: str


class NameSplitter:
    """Every plausible reading of one filename.

    It judges nothing. Its whole job is to offer the readings a person
    would consider, including the ones §8.13a refuses — which is the
    inversion §12.1 asked for, and the reason `10 is Better Than 01 -
    Heather Jarman` can be understood here rather than merely declined.
    """

    @classmethod
    def segments(cls, stem: str) -> list[str]:
        text = ScannerTags.spaces(EXTENSION.sub("", (stem or "").strip()))
        return [s.strip() for s in SEPARATORS.split(text) if s and s.strip()]

    @classmethod
    def readings(cls, stem: str) -> list[Reading]:
        """Each segment as the person, every other segment as the book.

        A name with one segment yields nothing: `Stardust` says one
        thing, there is no second end to corroborate it with, and a bare
        title search is exactly what §6.3 warns about — "Second Chance"
        returned a book James Patterson did not write.
        """
        segments = cls.segments(stem)
        if len(segments) < 2:
            return []

        out: list[Reading] = []
        for i, segment in enumerate(segments):
            others = [cls._as_title(s) for j, s in enumerate(segments) if j != i]
            for person, without in cls._people(segment):
                # A name lifted out of a bracket leaves the rest of its
                # own segment behind, and that remainder is the book:
                # "Star Trek Star Charts (Geoffrey Mandel)".
                titles = [t for t in (*others, without) if t]
                for title in titles:
                    if title and title != person:
                        out.append(Reading(author=person, title=title))
        return cls._distinct(out)

    @classmethod
    def _people(cls, segment: str) -> list[tuple[str, str | None]]:
        """The ways this segment could name a person, each with whatever
        of the segment is left over."""
        found: list[tuple[str, str | None]] = []
        for inner in BRACKET.findall(segment):
            leftover = cls._as_title(BRACKET.sub(" ", segment))
            found.append((inner.strip(), leftover or None))
        found.append((BRACKET.sub(" ", segment).strip(), None))

        people = []
        for raw, leftover in found:
            name = POSSESSIVE.sub("", re.sub(r"\s+", " ", raw).strip()).strip()
            if not name or SHELF.search(name):
                continue
            person = PersonName.parse(name)
            if person and NARROWEST <= len(person.display.split()) <= WIDEST:
                people.append((person.display, leftover))
        return people

    @staticmethod
    def _as_title(segment: str) -> str:
        """The segment as a book, or nothing if it names a shelf.

        The number rule cuts both ways, and the second edge cost three
        books to find. `Conn Iggulden - Emperor 1 - The Gates of Rome`
        offers `Emperor 1` and `The Gates of Rome` as titles, and Open
        Library holds a work called *Emperor* — so the shelf label
        matched, being the reading offered first, and all three novels
        would have been retitled `Emperor 1`, `Emperor 2`, `Emperor 3`.

        §8.13a says it already: the words in front of a number are the
        shelf talking. A shelf is no more a book than it is a person.
        """
        title = ScannerTags.strip(re.sub(r"\s+", " ", segment or "").strip())
        return "" if SHELF.search(title) else title

    @staticmethod
    def _distinct(readings: list[Reading]) -> list[Reading]:
        seen, out = set(), []
        for reading in readings:
            key = (reading.author.casefold(), reading.title.casefold())
            if key not in seen:
                seen.add(key)
                out.append(reading)
        return out


class NameHypotheses:
    """The pass: split, anchor, account, write."""

    def __init__(self, database, provider,
                 series: SeriesFromNames | None = None) -> None:
        self._db = database
        self._provider = provider
        self._series = series or SeriesFromNames(database)
        self._provenance = Provenance(database)
        self._titles = TitleWriter(database)
        self._asked = 0

    # ── the rule ──────────────────────────────────────────────────────
    @staticmethod
    def accounts_for(titles, ours: str) -> str | None:
        """Whether one of this author's works is the book the name names.

        §6.3's containment rule, pointed at a segment rather than at a
        whole run-on name: a contained title has to account for more
        than half the other, because Open Library caps a bibliography at
        200 works and without it `Foundation` quietly becomes `Foundation
        and Empire`.
        """
        want = normalise_title(ours)
        if not want:
            return None
        for title in titles:
            have = normalise_title(title)
            if not have:
                continue
            if have == want:
                return title
            shorter, longer = sorted((have, want), key=len)
            if shorter in longer and len(shorter) >= COVERAGE * len(longer):
                return title
        return None

    # ── asking ────────────────────────────────────────────────────────
    def _cached(self, question: str):
        row = self._db.connection.execute(
            "SELECT answer FROM provider_query WHERE source = 'openlibrary'"
            " AND question = ?", (question,)).fetchone()
        return json.loads(row["answer"]) if row and row["answer"] else (
            [] if row else None)

    def _remember(self, question: str, answer) -> None:
        self._db.connection.execute(
            "INSERT OR REPLACE INTO provider_query (source, question, answer,"
            " asked_at) VALUES ('openlibrary', ?, ?, ?)",
            (question, json.dumps(answer) if answer else None, int(time.time())))

    def _anchor(self, name: str) -> dict | None:
        """Is this segment somebody a catalogue has heard of?

        A silence is an answer and is kept like any other (§6.6) — a name
        nobody has heard of is the commonest answer there is, and not
        writing it down means asking again for ever.
        """
        question = f"author\x1f{name}"
        cached = self._cached(question)
        if cached is None:
            found = self._provider.find_author(name)
            self._asked += 1
            cached = ([{"key": found["key"], "name": found["name"]}]
                      if found else [])
            self._remember(question, cached)
        return cached[0] if cached else None

    def _works(self, key: str) -> list[str]:
        question = f"works\x1f{key}"
        cached = self._cached(question)
        if cached is None:
            works = self._provider.author_works(key) or []
            self._asked += 1
            cached = [w["title"] for w in works if w.get("title")]
            self._remember(question, cached)
        return cached

    def _confirm(self, reading: Reading) -> dict | None:
        """One reading, put to the catalogue. Both ends or nothing."""
        anchor = self._anchor(reading.author)
        if not anchor:
            return None
        held = self.accounts_for(self._works(anchor["key"]), reading.title)
        if not held:
            LOG.info("%r is an author, but %r is not one of their books",
                     reading.author, reading.title)
            return None
        return {"author": anchor["name"], "title": reading.title, "work": held}

    # ── the gate ──────────────────────────────────────────────────────
    def _books(self, author: str | None, formats: list[str] | None):
        """Books whose name the library is still showing as a title, and
        which nothing here can already read.

        The provenance guard asks with `openlibrary`, so a value a
        catalogue has already confirmed may be confirmed again while a
        reader's word is final (§8.10).
        """
        sql = ("""
            SELECT b.id, b.path, m.title, m.file_title,
                   (SELECT a.name FROM book_author ba
                      JOIN author a ON a.id = ba.author_id
                     WHERE ba.book_id = b.id ORDER BY ba.position LIMIT 1)
                     AS author
              FROM book b
              JOIN metadata m ON m.book_id = b.id
             WHERE b.missing_since IS NULL AND b.duplicate_of IS NULL
               AND m.title IS NOT NULL AND m.title <> ''
        """
            + Candidates.clause("authors", derived_from="openlibrary")
            + Candidates.clause("title", derived_from="openlibrary"))
        params: list = []
        if formats:
            sql += f" AND b.format IN ({','.join('?' * len(formats))})"
            params.extend(formats)
        rows = self._db.connection.execute(sql, params).fetchall()
        kept = [r for r in rows if self._unread(r)]
        if author:
            kept = [r for r in kept if r["author"] == author]
        return kept

    @staticmethod
    def _unread(row) -> bool:
        """Whether nothing here can already read this name.

        Three local certainties, and any one of them means this pass has
        no business with the book:

        - **The file named itself.** What the library shows is not the
          filename, so the file's own metadata answered and asking would
          spend a request confirming what we hold.
        - **§8.13a can read it.** `Anthony, Piers - Xanth 08 - Crewel
          Lye` needs no provider at all, and two passes reading one name
          is how they come to disagree.
        - **There is nothing to split.** `Stardust` names one thing.
        """
        stem = Path(row["path"]).stem
        if row["title"] != stem and row["file_title"] != stem:
            return False
        if CreditsFromNames.read(stem):
            return False
        return bool(NameSplitter.readings(stem))

    # ── what would change ─────────────────────────────────────────────
    def plan(self, author: str | None = None,
             formats: list[str] | None = None) -> list[dict]:
        """What each name turns out to say. Asks the provider — there is
        no way to know without asking — but every question is cached, so
        it is asked once, ever."""
        plan = []
        for row in self._books(author, formats):
            stem = Path(row["path"]).stem
            confirmed = [c for c in (self._confirm(r)
                                     for r in NameSplitter.readings(stem)) if c]
            people = {c["author"].casefold() for c in confirmed}
            if not confirmed:
                continue
            if len(people) > 1:
                # Two confirmed readings of one name is not two answers,
                # it is no answer. Guessing between them is what §12.1's
                # review queue would be for; picking one silently is
                # worse than either.
                LOG.info("%r reads as %s — leaving it alone", stem,
                         " or ".join(sorted(people)))
                continue
            best = confirmed[0]
            # Nothing to do is not the same as nothing to say. `file_title`
            # holds the file's own words for ever (§8.13d), so the gate
            # goes on admitting a book this pass has already answered —
            # and a pass that answers twice never settles (§8.16).
            if (row["author"] == best["author"]
                    and row["title"] == TitlePolish.apply(best["title"],
                                                          subtitles=False)):
                continue
            plan.append({"id": row["id"], "author": best["author"],
                         "title": TitlePolish.apply(best["title"],
                                                    subtitles=False),
                         "was": row["author"], "stem": stem,
                         "work": best["work"]})
        return plan

    # ── writing it down ───────────────────────────────────────────────
    def run(self, apply: bool = False, author: str | None = None,
            formats: list[str] | None = None) -> dict:
        """Authors, then series, then titles — §6.6's order, for the same
        reason: everything else is asked of an author."""
        self._asked = 0
        plan = self.plan(author, formats)
        report = {"credited": 0, "would_credit": len(plan), "filed": 0,
                  "retitled": 0, "anchored": len(plan), "asked": self._asked,
                  "plan": plan}
        if not apply:
            return report

        now = int(time.time())
        for item in plan:
            self._credit(item, now)
            report["credited"] += 1
            LOG.info("book %s credited to %s (was %s)", item["id"],
                     item["author"], item["was"] or "nobody")

        # The series is judged where it is already judged. Its rule —
        # two books by one author must agree — is what keeps a misread
        # off the shelves, and a second implementation of one judgement
        # is how they come to disagree (§8.13a).
        report["filed"] = self._series.run(
            apply=True, formats=formats)["named"]

        for item in plan:
            if self._retitle(item, now):
                report["retitled"] += 1
        report["asked"] = self._asked
        return report

    def _credit(self, item: dict, now: int) -> None:
        cursor = self._db.connection
        cursor.execute("DELETE FROM book_author WHERE book_id = ?", (item["id"],))
        person = PersonName.parse(item["author"])
        if person:
            cursor.execute(
                "INSERT OR IGNORE INTO author (name, sort_name) VALUES (?, ?)",
                (person.display, person.sort))
            author_id = cursor.execute(
                "SELECT id FROM author WHERE name = ?",
                (person.display,)).fetchone()["id"]
            cursor.execute(
                "INSERT OR IGNORE INTO book_author (book_id, author_id, role,"
                " position) VALUES (?, ?, 'author', 0)", (item["id"], author_id))
        self._provenance.record(item["id"], "authors", "provider",
                                "openlibrary", now=now)

    def _retitle(self, item: dict, now: int) -> bool:
        row = self._db.connection.execute(
            "SELECT title FROM metadata WHERE book_id = ?",
            (item["id"],)).fetchone()
        if not row or row["title"] == item["title"]:
            return False
        self._titles.write(item["id"], item["title"], "provider",
                           "openlibrary", now=now)
        return True

    @staticmethod
    def summary(report: dict) -> str:
        done = report["credited"] + report["filed"] + report["retitled"]
        if not done and not report["would_credit"]:
            return "Hypotheses: nothing a catalogue could confirm."
        if not done:
            return (f"Hypotheses: would credit {report['would_credit']} books"
                    " from names nothing here can read.")
        return (f"Hypotheses: credited {report['credited']} books, filed"
                f" {report['filed']} under a series, retitled"
                f" {report['retitled']}.")
