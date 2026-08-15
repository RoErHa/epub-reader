"""Repairing books whose "author" is really their title.

The real library has 23 books where the author field holds the title
exactly, and around 190 more where it holds the title with a list-number
prefix removed — "1 - A Wizard In Mind" with the author "A Wizard In
Mind". The book is then filed under itself, and its real author is
nowhere.

The fix is to look the title up and take the author from a provider.
**Nothing here touches a file**: the library is read-only and only the
database changes.
"""
from __future__ import annotations

import difflib
import logging
import time

from epub_match import normalise_title, search_title
from epub_names import PersonName, fold
from epub_providers import ProviderError

LOG = logging.getLogger("epub-reader.repair")

# The title has to match almost exactly. Author is no help here — it is
# the broken field — so the title is carrying the whole decision, and it
# must therefore carry it convincingly.
TITLE_CONFIDENCE = 0.95

# How close two surnames have to be to count as the same person. The
# file spells Robert Asprin "Aspirin, Robert Lynn", so exact is too
# strict, but "Patterson" must stay away from "Koin".
SURNAME = 0.85



class TitleAsAuthorRepair:
    def __init__(self, database, providers) -> None:
        self._db = database
        self._providers = list(providers)
        self._surnames: set[str] | None = None
        # A provider that has rate-limited us is done for this run:
        # Google Books answers 429 to every unauthenticated request, and
        # asking it once per book just fills the log.
        self._exhausted: set[int] = set()

    # ── finding ───────────────────────────────────────────────────────
    def _rows(self) -> list:
        return self._db.connection.execute(
            """
            SELECT b.id, m.title, a.id AS author_id, a.name AS author
              FROM book b
              JOIN metadata m ON m.book_id = b.id
              JOIN book_author ba ON ba.book_id = b.id
              JOIN author a ON a.id = ba.author_id
             WHERE b.missing_since IS NULL AND m.title IS NOT NULL
            """
        ).fetchall()

    def find(self) -> list[dict]:
        """Books credited to their own title, whole or in part."""
        broken = []
        for row in self._rows():
            if (self._is_title(row["author"], row["title"])
                    or self._is_fragment(row["author"], row["title"])):
                broken.append({"id": row["id"], "title": row["title"],
                               "author": row["author"],
                               "author_id": row["author_id"]})
        return broken

    def _is_fragment(self, author: str, title: str) -> bool:
        """True when the 'author' is a piece of the title and the title
        names somebody else.

        "Jack And Jill" was split on the "and" and both halves became
        people. On its own that is too loose a test — an author really
        can share a word with their title — so it only counts when the
        title names an author the library knows, and it is not this one.
        """
        named = self._named_in_title(title)
        if not named or self._same_surname(author, named):
            return False
        piece = normalise_title(author)
        return bool(piece) and piece in normalise_title(title)

    @staticmethod
    def _is_title(author: str, title: str) -> bool:
        """True when the 'author' is just the title again.

        Compared both ways: plainly, and against the title with its
        list-number prefix stripped, which is how most of them look.
        """
        if not author or not title:
            return False
        author_key = normalise_title(author)
        if not author_key:
            return False
        return author_key in {normalise_title(title), search_title(title)}

    # ── repairing ─────────────────────────────────────────────────────
    def run(self, apply: bool = False) -> dict:
        broken = self.find()
        if not apply:
            return {"found": len(broken), "repaired": 0,
                    "would_repair": len(broken)}

        repaired = 0
        for book in broken:
            if self._repair(book):
                repaired += 1
        return {"found": len(broken), "repaired": repaired,
                "would_repair": len(broken)}

    def _repair(self, book: dict) -> bool:
        query = search_title(book["title"])
        candidates = []
        for provider in self._providers:
            if id(provider) in self._exhausted:
                continue
            try:
                candidates.extend(provider.find_books(query))
            except ProviderError as exc:
                LOG.warning("lookup failed for %r: %s", query, exc)
                if "429" in str(exc) or "rate" in str(exc).lower():
                    LOG.warning("%s is rate limiting us; skipping it for"
                                " this run", type(provider).__name__)
                    self._exhausted.add(id(provider))
        if not candidates:
            best = self._author_the_library_knows(book["title"])
            if best is None:
                return False
            self._set_author(book, best)
            return True

        best = self._best_author(book["title"], candidates)
        if best is None:
            # Nothing confident from the provider, but the file may name
            # its own author, and the library may already know them.
            best = self._author_the_library_knows(book["title"])
        if best is None:
            return False
        self._set_author(book, best)
        return True

    def _best_author(self, title: str, candidates) -> str | None:
        # Compare on the same footing as the query: the raw title still
        # carries its list-number prefix, which sinks the ratio.
        ours = search_title(title) or normalise_title(title)
        wanted = self._named_in_title(title)
        best_name, best_score = None, 0.0
        for candidate in candidates:
            names = [n for n in (candidate.authors or []) if n and n.strip()]
            if not names:
                continue
            # A provider that repeats the same mistake is no help.
            if any(self._is_title(n, title) for n in names):
                continue
            if wanted:
                # The file names its own author. "Second Chance" is a
                # common enough title that the lookup answers with
                # somebody else's book, and the file is right.
                names = [n for n in names if self._same_surname(n, wanted)]
                if not names:
                    continue
            ratio = difflib.SequenceMatcher(
                None, ours, normalise_title(candidate.title)
            ).ratio()
            if ratio > best_score:
                best_name, best_score = names[0], ratio
        return best_name if best_score >= TITLE_CONFIDENCE else None

    # ── the author named in the filename ──────────────────────────────
    def _named_in_title(self, title: str) -> str | None:
        """The surname of the author the title names, if it names one.

        These files are mostly "Author - Series 03 - Title", so the head
        of the title is the best evidence there is. A franchise sits in
        the same position — "Star Trek - TNG 01 - Ghost Ship" — so a
        name only counts when it is written surname-first, or when the
        library already knows that surname from a book filed properly.
        """
        if " - " not in title:
            # No "Author - " in front, so the whole thing is the title.
            # Without this "The Lost World" reads as a Mr World.
            return None
        head = title.split(" - ")[0].strip()
        if not head or any(c.isdigit() for c in head):
            return None
        if not 2 <= len(head.split()) <= 4:
            return None
        person = PersonName.parse(head)
        if person is None:
            return None
        surname = person.sort.partition(",")[0].strip()
        if not surname:
            return None
        if "," in head:
            return surname
        return surname if self._known_surname(surname) else None

    def _author_the_library_knows(self, title: str) -> str | None:
        """The author named in the title, spelled the way the library
        already spells them.

        Only names the library independently believes in are used, so
        "Star Wars - Shatterpoint" writes nothing, while "C J Cherryh -
        Faded Sun 1 - Kesrith" becomes "C. J. Cherryh" — 23 other books
        vouch for the spelling.
        """
        if self._named_in_title(title) is None:
            return None
        person = PersonName.parse(title.split(" - ")[0].strip())
        if person is None:
            return None

        # Most books first, so a well-established spelling wins over a
        # stray one that happens to match as well.
        for row in self._db.connection.execute(
            """
            SELECT a.name, COUNT(*) n FROM author a
              JOIN book_author ba ON ba.author_id = a.id
             GROUP BY a.id ORDER BY n DESC
            """
        ):
            if self._same_person(person.display, row["name"]):
                return row["name"]
        return None

    @classmethod
    def _same_person(cls, ours: str, theirs: str) -> bool:
        """Whether two spellings name one person.

        The surname has to match, and the given names have to be
        compatible rather than merely similar: "Robert Lynn Asprin" is
        "Robert Asprin" with a middle name, and "C J Cherryh" is
        "C. J. Cherryh" without the full stops, but "Bob Salvatore" is
        not "R. A. Salvatore".
        """
        a, b = PersonName.parse(ours), PersonName.parse(theirs)
        if a is None or b is None:
            return False
        a_last, _, a_given = a.sort.partition(",")
        b_last, _, b_given = b.sort.partition(",")
        if not cls._same_surname_key(a_last.strip(), b_last.strip()):
            return False
        return cls._compatible_given(a_given, b_given)

    @staticmethod
    def _compatible_given(a: str, b: str) -> bool:
        first = [t.strip(".") for t in fold(a).replace(".", ". ").split()]
        second = [t.strip(".") for t in fold(b).replace(".", ". ").split()]
        first = [t for t in first if t]
        second = [t for t in second if t]
        if not first or not second:
            return False
        short, long_ = sorted((first, second), key=len)
        for ours, theirs in zip(short, long_):
            # An initial stands in for the name it begins.
            if len(ours) == 1 or len(theirs) == 1:
                if ours[0] != theirs[0]:
                    return False
            elif ours != theirs:
                return False
        return True

    def _known_surname(self, surname: str) -> bool:
        if self._surnames is None:
            # Authors the library already believes in. The broken rows
            # are titles, not people, so they are left out.
            # Only the plain title-as-author rows: asking find() here
            # would call back into this method.
            broken = {r["author_id"] for r in self._rows()
                      if self._is_title(r["author"], r["title"])}
            rows = self._db.connection.execute(
                "SELECT id, sort_name FROM author WHERE sort_name IS NOT NULL")
            self._surnames = {
                r["sort_name"].partition(",")[0].strip()
                for r in rows if r["id"] not in broken
            }
            self._surnames.discard("")
        return any(self._same_surname_key(surname, known)
                   for known in self._surnames)

    @classmethod
    def _same_surname(cls, name: str, surname: str) -> bool:
        person = PersonName.parse(name)
        if person is None:
            return False
        return cls._same_surname_key(person.sort.partition(",")[0].strip(),
                                     surname)

    @staticmethod
    def _same_surname_key(a: str, b: str) -> bool:
        a, b = fold(a), fold(b)
        if a == b:
            return True
        return difflib.SequenceMatcher(None, a, b).ratio() >= SURNAME

    def _set_author(self, book: dict, name: str) -> None:
        person = PersonName.parse(name)
        if person is None:
            return
        cursor = self._db.connection
        cursor.execute(
            "INSERT OR IGNORE INTO author (name, sort_name) VALUES (?, ?)",
            (person.display, person.sort),
        )
        author_id = cursor.execute(
            "SELECT id FROM author WHERE name = ?", (person.display,)
        ).fetchone()["id"]

        # The old credit was wrong, so it goes rather than being kept
        # alongside. The author row itself is left: it may be orphaned
        # now, but deleting rows another book might use is not our call.
        cursor.execute("DELETE FROM book_author WHERE book_id = ?", (book["id"],))
        cursor.execute(
            "INSERT OR IGNORE INTO book_author (book_id, author_id, role,"
            " position) VALUES (?, ?, 'author', 0)",
            (book["id"], author_id),
        )
        cursor.execute(
            """
            INSERT INTO field_provenance (book_id, field, origin, source,
                                          updated_at)
            VALUES (?, 'authors', 'provider', 'lookup', ?)
            ON CONFLICT(book_id, field) DO UPDATE SET
                origin = 'provider', updated_at = excluded.updated_at
            """,
            (book["id"], int(time.time())),
        )
        LOG.info("%r: author %r → %r", book["title"], book["author"],
                 person.display)
