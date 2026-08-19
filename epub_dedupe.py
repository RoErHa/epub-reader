"""Merging authors that are really the same person.

A real library accumulates the same writer several times over: "Stephen
R. Donaldson", "Stephen Donaldson", "Stephan Donaldson" — a middle
initial crossed with a typo — or "Le Guin" against "LeGuin".

Merging is destructive, so the bar is deliberately high. A group is only
merged when **either**:

  * Open Library resolves the spellings to the *same* author, which also
    supplies the correct spelling; **or**
  * the names are identical once spacing and punctuation are removed,
    which needs no provider at all.

Anything else is reported and left alone. Fusing two real people is far
worse than leaving a duplicate on screen.
"""
from __future__ import annotations

import difflib
import logging
import re
import time

from epub_match import normalise_title, search_title
from epub_names import PersonName, fold
from epub_provenance import Provenance
from epub_providers import ProviderError

LOG = logging.getLogger("epub-reader.dedupe")

# Close enough to be worth asking Open Library about. Below this we do
# not even ask: "John Williams" and "John Wyndham" are not a question.
SIMILAR = 0.86

# Close enough to call a misspelling, when Open Library knows one name
# and has never heard of the other. Tighter than SIMILAR: at 0.88 sits
# "L. E. Modesitt" against "L. E. Modesitt Jr.", which is a suffix
# rather than a mistake, and merging it would lose the "Jr.".
TYPO = 0.90


def squash(name: str) -> str:
    """A name with spacing and punctuation removed — "Le Guin" and
    "LeGuin" collapse to the same thing."""
    return re.sub(r"[^\w]", "", fold(name or ""))


class AuthorDeduplicator:
    def __init__(self, database, resolver, enricher=None) -> None:
        self._db = database
        self._resolver = resolver
        # Optional: fetches a bibliography that is not stored yet.
        self._enricher = enricher
        self._works: dict[int, set[str]] = {}

    # ── planning ──────────────────────────────────────────────────────
    def _authors(self) -> list[dict]:
        return [
            {"id": r["id"], "name": r["name"], "sort_name": r["sort_name"],
             "books": r["n"]}
            for r in self._db.connection.execute(
                """
                SELECT a.id, a.name, a.sort_name, COUNT(*) n
                  FROM author a
                  JOIN book_author ba ON ba.author_id = a.id
                 GROUP BY a.id ORDER BY n DESC, a.sort_name
                """
            )
        ]

    def _clusters(self, authors: list[dict]) -> list[list[dict]]:
        """Authors similar enough to be worth checking. Deliberately
        loose: the verdict is made later, this only decides what to ask
        about."""
        clusters: list[list[dict]] = []
        for author in authors:
            for cluster in clusters:
                if any(self._similar(author, other) for other in cluster):
                    cluster.append(author)
                    break
            else:
                clusters.append([author])
        return [c for c in clusters if len(c) > 1]

    def _spelled_by_provider(self, spellings: list[str]) -> str:
        """The provider's spelling of a name we already believe in.

        Book count picked the survivor, which is how "Raymond E Feist"
        beat "Raymond E. Feist". Every spelling in the group is offered,
        because the provider may only recognise one of them. Only
        punctuation and capitals may come back this way: an answer that
        is a *different* name is discarded, so a wrong lookup cannot
        rename anyone.
        """
        ours = spellings[0]
        for spelling in spellings:
            try:
                found = self._resolver.find_author(spelling)
            except ProviderError as exc:
                LOG.warning("no spelling for %r: %s", spelling, exc)
                return ours
            theirs = (found or {}).get("name")
            if (theirs and squash(theirs) == squash(ours)
                    and self._better_spelled(theirs, ours)):
                return theirs
        return ours

    @staticmethod
    def _better_spelled(theirs: str, ours: str) -> bool:
        """Whether a provider spelling is an improvement on ours.

        Open Library holds "J.k. Rowling", which is not. Capitals are
        what separates the two spellings of a name we already agree on;
        when they tie, the provider wins, and that is where the full
        stops in "Raymond E. Feist" come from.
        """
        if theirs.isupper() or theirs.islower():
            return False
        capitals = (sum(c.isupper() for c in theirs),
                    sum(c.isupper() for c in ours))
        return capitals[0] >= capitals[1]

    @staticmethod
    def _best_spelling(candidates: list[dict]) -> str:
        """The nicest-looking of several spellings of one name.

        Book count decides which row survives, but not how it is spelled:
        "DIANE CAREY" had more books than "Diane Carey" and was winning on
        count alone.
        """
        def rank(author: dict) -> tuple:
            name = author["name"]
            shouting = name.isupper()
            whispering = name.islower()
            return (shouting or whispering, -author["books"], name)

        return min(candidates, key=rank)["name"]

    @staticmethod
    def _ratio(a: dict, b: dict) -> float:
        # Compare sort names, so "Donaldson, Stephen" lines up with
        # "Donaldson, Stephen R." rather than being pulled apart by the
        # given name coming first.
        return difflib.SequenceMatcher(
            None, fold(a["sort_name"]), fold(b["sort_name"])
        ).ratio()

    @classmethod
    def _similar(cls, a: dict, b: dict) -> bool:
        if squash(a["name"]) == squash(b["name"]):
            return True
        return cls._ratio(a, b) >= SIMILAR

    def plan(self) -> tuple[list[dict], list[list[str]]]:
        """What would be merged and why, and what was refused. Changes
        nothing."""
        groups, unresolved = [], []
        for cluster in self._clusters(self._authors()):
            decided = self._decide(cluster)
            if decided:
                groups.append(decided)
            else:
                # Look-alikes we would not fuse. Reporting them is the
                # whole safety valve: a human can see what was left.
                unresolved.append([a["name"] for a in cluster])
        return groups, unresolved

    @staticmethod
    def _split(keeper: dict, others: list[dict]) -> tuple[list, list]:
        """Spacing-only differences, which need no provider, and the
        rest, which do."""
        spacing = [a for a in others
                   if squash(a["name"]) == squash(keeper["name"])]
        return spacing, [a for a in others if a not in spacing]

    @staticmethod
    def _key(resolved: dict, author: dict) -> str | None:
        return (resolved.get(author["id"]) or {}).get("key")

    def _resolve(self, authors: list[dict]) -> dict:
        """Open Library's answer for each name, or nothing at all if it
        cannot be reached — without it we cannot tell a typo from a
        different person, so nothing beyond spacing is merged."""
        try:
            return {a["id"]: self._resolver.find_author(a["name"])
                    for a in authors}
        except ProviderError as exc:
            LOG.warning("dedupe could not reach the provider: %s", exc)
            return {}

    def _decide(self, cluster: list[dict]) -> dict | None:
        # The one with the most books is the natural survivor: fewest
        # rows to move, and usually the best-spelled.
        keeper = max(cluster, key=lambda a: a["books"])
        others = [a for a in cluster if a["id"] != keeper["id"]]

        by_spacing, remaining = self._split(keeper, others)
        resolved: dict = {}
        by_books: list[dict] = []

        if remaining:
            resolved = self._resolve([keeper, *remaining])
            # Book count picks the survivor, but a spelling Open Library
            # has never heard of is not the one to keep: the library
            # holds three "Robert Aspirin" against one "Robert Asprin".
            if not self._key(resolved, keeper):
                known = [a for a in remaining if self._key(resolved, a)]
                if known:
                    keeper = max(known, key=lambda a: a["books"])
                    others = [a for a in cluster if a["id"] != keeper["id"]]
                    by_spacing, remaining = self._split(keeper, others)
                    resolved |= self._resolve(
                        [a for a in remaining if a["id"] not in resolved])

        canonical, key = self._best_spelling([keeper, *by_spacing]), None
        confirmed = list(by_spacing)
        if by_spacing:
            # Best first, then the rest: Open Library holds no
            # "Raymond E Feist", so the spelling has to come from the
            # other row in the group.
            spellings = [canonical] + [
                a["name"] for a in [keeper, *by_spacing] if a["name"] != canonical
            ]
            canonical = self._spelled_by_provider(spellings)

        if remaining:
            keeper_key = self._key(resolved, keeper)
            if keeper_key:
                # A provider answer beats any spelling heuristic.
                canonical = resolved[keeper["id"]].get("name") or canonical
                key = keeper_key
                confirmed += [a for a in remaining
                              if self._key(resolved, a) == keeper_key]
                # A name Open Library has never heard of, sitting beside
                # one it knows well, is a misspelling: "Issac Asimov",
                # "Peirs Anthony", "J.R.R. Tolkein". A name it *does*
                # know is a person, however alike they look — which is
                # what keeps Stephen King away from Stephen Hawking.
                confirmed += [a for a in remaining
                              if not self._key(resolved, a)
                              and self._ratio(keeper, a) >= TYPO]

        if remaining:
            # The strongest evidence is not the name but the books. Open
            # Library holds two records for Donaldson, and "Stephan
            # Donaldson" is too far from "Stephen R. Donaldson" to be
            # called a typo — but all their books are Thomas Covenant
            # novels, every one in the keeper's bibliography.
            known = {a["id"] for a in confirmed}
            by_books = [a for a in remaining if a["id"] not in known
                        and self._writes_their_books(keeper, a)]
            confirmed += by_books

        if not confirmed:
            return None
        reasons = []
        if by_spacing:
            reasons.append("identical once spacing is ignored")
        if key and len(confirmed) > len(by_spacing) + len(by_books):
            reasons.append("confirmed by Open Library")
        if by_books:
            reasons.append("writes the keeper's books")
        return {"keep": keeper, "merge": confirmed, "canonical": canonical,
                "key": key, "reason": "; ".join(reasons)}

    # ── doing it ──────────────────────────────────────────────────────
    def run(self, apply: bool = False) -> dict:
        groups, unresolved = self.plan()
        planned = sum(len(g["merge"]) for g in groups)
        report = {"groups": len(groups), "merged": 0, "would_merge": planned,
                  "plan": groups, "unresolved": unresolved}
        if not apply:
            return report

        for group in groups:
            report["merged"] += self._merge(group)
        return report

    # ── the books as evidence ─────────────────────────────────────────
    def _writes_their_books(self, keeper: dict, other: dict) -> bool:
        """Whether everything the other name is credited with belongs to
        the keeper's bibliography.

        One shared title proves nothing — two writers can name a book the
        same — so this asks for all of them.
        """
        works = self._bibliography(keeper["id"])
        if not works:
            return False
        titles = [search_title(t) or normalise_title(t)
                  for t in self._titles_of(other["id"])]
        titles = [t for t in titles if t]
        return bool(titles) and all(t in works for t in titles)

    def _titles_of(self, author_id: int) -> list[str]:
        return [r["title"] for r in self._db.connection.execute(
            """SELECT m.title FROM metadata m
                 JOIN book_author ba ON ba.book_id = m.book_id
                 JOIN book b ON b.id = m.book_id
                WHERE ba.author_id = ? AND b.missing_since IS NULL
                  AND m.title IS NOT NULL""", (author_id,))]

    def _bibliography(self, author_id: int) -> set[str]:
        if author_id in self._works:
            return self._works[author_id]

        def stored():
            return {normalise_title(r["title"]) for r in
                    self._db.connection.execute(
                        "SELECT title FROM author_work WHERE author_id = ?",
                        (author_id,))}

        works = stored()
        if not works and self._enricher is not None:
            self._enricher.enrich(author_id)
            works = stored()
        self._works[author_id] = works
        return works

    def _record(self, book_ids: list[int]) -> None:
        now = int(time.time())
        for book_id in book_ids:
            Provenance(self._db).record(book_id, "authors", "provider",
                                        "dedupe", now=now)

    def merge(self, keeper_id: int, other_ids: list[int],
              name: str | None = None) -> dict:
        """Merge by hand, on a reader's say-so.

        The rules above refuse whatever they cannot prove, and some of
        what they refuse is still one person — Open Library holds two
        records for Anne Rice and two for Arthur C. Clarke, and their
        books do not overlap here, so nothing can settle it. This is how
        you say so yourself, and it records provenance like any other
        correction so a rescan does not undo it.
        """
        keeper = self._author(keeper_id)
        others = []
        for other_id in other_ids:
            if other_id == keeper_id:
                raise ValueError("An author cannot be merged into itself.")
            others.append(self._author(other_id))
        if not others:
            return {"merged": 0, "name": keeper["name"]}

        group = {"keep": keeper, "merge": others,
                 "canonical": name or keeper["name"]}
        moved = self._merge(group)
        return {"merged": moved, "name": group["canonical"]}

    def _author(self, author_id: int) -> dict:
        row = self._db.connection.execute(
            "SELECT id, name, sort_name FROM author WHERE id = ?", (author_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"No author with id {author_id}.")
        return {"id": row["id"], "name": row["name"],
                "sort_name": row["sort_name"], "books": 0}

    def _merge(self, group: dict) -> int:
        cursor = self._db.connection
        keeper = group["keep"]["id"]
        moved = 0
        for other in group["merge"]:
            # The books about to move. Their author no longer comes from
            # the file, and the indexer has to know that or the next
            # rescan files them under the old spelling again.
            moving = [r["book_id"] for r in cursor.execute(
                "SELECT book_id FROM book_author WHERE author_id = ?",
                (other["id"],))]
            # INSERT OR IGNORE, because a book can already credit both
            # spellings — the primary key would collide otherwise.
            cursor.execute(
                "INSERT OR IGNORE INTO book_author (book_id, author_id, role,"
                " position) SELECT book_id, ?, role, position FROM book_author"
                " WHERE author_id = ?",
                (keeper, other["id"]),
            )
            cursor.execute("DELETE FROM book_author WHERE author_id = ?",
                           (other["id"],))
            cursor.execute(
                "UPDATE OR IGNORE author_work SET author_id = ? WHERE author_id = ?",
                (keeper, other["id"]),
            )
            cursor.execute("DELETE FROM author WHERE id = ?", (other["id"],))
            self._record(moving)
            moved += 1

        # The surviving row takes the correct spelling, and its sort name
        # is recomputed from it rather than left stale.
        person = PersonName.parse(group["canonical"])
        if person:
            cursor.execute(
                "UPDATE author SET name = ?, sort_name = ? WHERE id = ?",
                (person.display, person.sort, keeper),
            )
        return moved
