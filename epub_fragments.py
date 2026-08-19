"""Titles that were cut off, and the provider that can finish them.

    Anthony, Piers - Jason Striker 02 - Bamboo Bloodbath & Ninj.pdf
    Anthony, Piers - The Caterpillars Questi (with Philip Jose Farmer).pdf

Somebody's filesystem or unzipper truncated these years ago, and no
amount of matching fixes a fragment: Open Library holds *The
Caterpillar's Question*, and `The Caterpillars Questi` matches nothing at
all, for ever.

§12.3's **tier 1**. The damage is always at the *end*, so trimming back
to the last complete word turns a dead query live — probed on the real
provider before any of this was written:

    "The Caterpillars Questi"  ->  nothing found
    "The Caterpillars"         ->  The caterpillar's question, 1992

> **There is no clever damage detector, and there should not be.** A
> title whose last word is cut mid-syllable cannot be told from an
> invented one without a dictionary, and this library is full of Xanth,
> Chthon and Phthor. So the provider *is* the detector: ask about the
> title as it stands, and only if nothing comes back is there anything to
> repair. That makes the pass one request per book, which is why it takes
> a scope.

> **The answer must COMPLETE our fragment, word for word.** Everything
> but our last word must match exactly, and our last word must be the
> beginning of theirs. A rule that can only ever *lengthen* a name cannot
> quietly swap in a different book — it is §6.3's containment logic
> pointed the other way.

That rule refuses `Bamboo Bloodbath & Ninj`, and is right to: the file
names *two* novels and Open Library holds one of them, so their answer is
shorter than what we have. Completing a fragment is not the same as
replacing it with half of itself, and that book wants §12.5's editor.

Nothing here touches a file, and the fetcher is injected, so no test
reaches the network.
"""
from __future__ import annotations

import json
import logging
import re
import time

from epub_names import fold
from epub_titles import TitlePolish

LOG = logging.getLogger("epub-reader.fragments")

# Leading articles, ours or theirs: a catalogue writes "The Bamboo
# Bloodbath" for a spine that says "Bamboo Bloodbath".
ARTICLES = frozenset({"the", "a", "an"})

# Everything that is not a letter or a digit is a space, so "&" is not a
# word at all.
WORDS = re.compile(r"[^\w]+", re.UNICODE)

# ...except an apostrophe, which joins rather than separates: a
# catalogue's "caterpillar's" and a filename's "caterpillars" are the
# same word, and splitting on it makes them three words against two.
JOINERS = re.compile(r"[\u2019'\u02bc]")


class TitlesFromFragments:
    def __init__(self, database, provider) -> None:
        self._db = database
        self._provider = provider

    # ── the rule ──────────────────────────────────────────────────────
    @classmethod
    def completes(cls, ours: str, theirs: str) -> bool:
        """Whether `theirs` is `ours` with its last word finished."""
        mine, yours = cls._words(ours), cls._words(theirs)
        if not mine or not yours or len(yours) < len(mine):
            return False
        # A fragment has to be corroborated by a whole word that matches
        # exactly. Without one there is nothing holding the answer to
        # the book: "The Ring" would happily become "The Rings of
        # Saturn", which is a different book by a different author. In
        # practice `trimmed()` refuses a one-word title anyway; this is
        # the same judgement stated where the rule lives.
        if len(mine) < 2:
            return False
        # Everything before the damage has to be the same words.
        if mine[:-1] != yours[:len(mine) - 1]:
            return False
        last, matching = mine[-1], yours[len(mine) - 1]
        # A *proper* prefix: equal words mean nothing was cut off, and a
        # title that needs no repair must not be rewritten.
        return len(last) < len(matching) and matching.startswith(last)

    @staticmethod
    def _words(title: str) -> list[str]:
        words = [w for w in WORDS.split(JOINERS.sub("", fold(title or ""))) if w]
        if len(words) > 1 and words[0] in ARTICLES:
            words = words[1:]
        return words

    @staticmethod
    def trimmed(title: str) -> str | None:
        """The title without its last word, or nothing if that leaves
        nothing to ask about."""
        head = (title or "").strip().rsplit(" ", 1)[0].strip(" -–—_&,")
        words = [w for w in WORDS.split(JOINERS.sub("", fold(head))) if w]
        if not words or (len(words) == 1 and words[0] in ARTICLES):
            return None
        return head or None

    # ── asking ────────────────────────────────────────────────────────
    def _ask(self, title: str, author: str) -> list:
        """The provider, through a cache. A silence is an answer and is
        kept like any other (§6.6)."""
        question = f"{title}\x1f{author}"
        row = self._db.connection.execute(
            "SELECT answer FROM provider_query WHERE source = 'openlibrary'"
            " AND question = ?", (question,)).fetchone()
        if row is not None:
            return json.loads(row["answer"]) if row["answer"] else []

        found = self._provider.find_books(title, author=author) or []
        titles = [c.title for c in found if getattr(c, "title", None)]
        self._db.connection.execute(
            "INSERT OR REPLACE INTO provider_query (source, question, answer,"
            " asked_at) VALUES ('openlibrary', ?, ?, ?)",
            (question, json.dumps(titles) if titles else None, int(time.time())))
        return titles

    def plan(self, author: str | None = None,
             formats: list[str] | None = None) -> list[dict]:
        """What would be repaired. Asks the provider — there is no way to
        know without asking — but every answer is cached, so it asks each
        question once, ever."""
        plan = []
        for row in self._books(author, formats):
            title, who = row["title"], row["author"]
            if any(self._same(title, t) for t in self._ask(title, who)):
                continue                    # the provider knows it as it is
            short = self.trimmed(title)
            if not short:
                continue
            mended = next((t for t in self._ask(short, who)
                           if self.completes(title, t)), None)
            if not mended:
                continue
            # A trailing bracket is the shelf talking, not the book.
            cleaned = TitlePolish.apply(mended)
            plan.append({"id": row["id"], "title": cleaned, "was": title,
                         "asked": short})
        return plan

    @classmethod
    def _same(cls, ours: str, theirs: str) -> bool:
        return cls._words(ours) == cls._words(theirs)

    def _books(self, author: str | None, formats: list[str] | None):
        """Books with a title and somebody to attribute it to.

        **The author is not optional.** It is what makes a two-word query
        safe: "The Caterpillars" on its own is a search for anything.
        """
        sql = """
            SELECT b.id, m.title,
                   (SELECT a.name FROM book_author ba
                      JOIN author a ON a.id = ba.author_id
                     WHERE ba.book_id = b.id ORDER BY ba.position LIMIT 1)
                     AS author
              FROM book b
              JOIN metadata m ON m.book_id = b.id
             WHERE b.missing_since IS NULL AND b.duplicate_of IS NULL
               AND m.title IS NOT NULL AND m.title <> ''
               AND NOT EXISTS (
                     SELECT 1 FROM field_provenance f
                      WHERE f.book_id = b.id AND f.field = 'title'
                        AND (f.origin = 'user' OR f.locked = 1))
        """
        params: list = []
        if formats:
            sql += f" AND b.format IN ({','.join('?' * len(formats))})"
            params.extend(formats)
        rows = self._db.connection.execute(sql, params).fetchall()
        rows = [r for r in rows if r["author"]]
        if author:
            rows = [r for r in rows if r["author"] == author]
        return rows

    # ── writing it down ───────────────────────────────────────────────
    def run(self, apply: bool = False, author: str | None = None,
            formats: list[str] | None = None) -> dict:
        plan = self.plan(author, formats)
        report = {"mended": 0, "would_mend": len(plan), "plan": plan}
        if not apply:
            return report

        now = int(time.time())
        for item in plan:
            self._db.connection.execute(
                """UPDATE metadata
                      SET file_title = COALESCE(file_title, title), title = ?
                    WHERE book_id = ?""", (item["title"], item["id"]))
            # The title only. Open Library calls The Bamboo Bloodbath
            # Jason Striker #3 while the file calls it 02, and both are
            # right — the file is an omnibus of novels 3 and 4.
            self._db.connection.execute(
                """
                INSERT INTO field_provenance (book_id, field, origin, source,
                                              updated_at)
                VALUES (?, 'title', 'provider', 'openlibrary', ?)
                ON CONFLICT(book_id, field) DO UPDATE SET
                    origin = 'provider', source = 'openlibrary',
                    updated_at = excluded.updated_at
                """, (item["id"], now))
            report["mended"] += 1
            LOG.info("book %s: %r -> %r (asked %r)", item["id"], item["was"],
                     item["title"], item["asked"])
        return report

    @staticmethod
    def summary(report: dict) -> str:
        if not report["mended"] and not report["would_mend"]:
            return "Fragments: no title needed finishing."
        if not report["mended"]:
            return f"Fragments: would finish {report['would_mend']} titles."
        return f"Fragments: finished {report['mended']} titles."
