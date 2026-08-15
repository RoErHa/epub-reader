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

from epub_names import PersonName, fold
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
    def __init__(self, database, resolver) -> None:
        self._db = database
        self._resolver = resolver

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
        resolved = {}

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

        if not confirmed:
            return None
        reasons = []
        if by_spacing:
            reasons.append("identical once spacing is ignored")
        if key and len(confirmed) > len(by_spacing):
            reasons.append("confirmed by Open Library")
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

    def _record(self, book_ids: list[int]) -> None:
        now = int(time.time())
        for book_id in book_ids:
            self._db.connection.execute(
                """
                INSERT INTO field_provenance (book_id, field, origin, source,
                                              updated_at)
                VALUES (?, 'authors', 'provider', 'dedupe', ?)
                ON CONFLICT(book_id, field) DO UPDATE SET
                    origin = 'provider', source = 'dedupe',
                    updated_at = excluded.updated_at
                """,
                (book_id, now),
            )

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
