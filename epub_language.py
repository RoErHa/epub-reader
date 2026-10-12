"""What language a book is written in.

Measured on the real library before this was written: 1,143 of 1,325
EPUBs say, spelt seven ways for English alone — `en`, `EN`, `En`, `eng`,
`en-US`, `en-us`, `en-GB` — and three for Dutch. No PDF says anything,
and no comic. So a Languages browse built on what files declare would
have had one English shelf per spelling and a Dutch one of eight books.

**One spelling.** `LanguageCode` reduces whatever a file or a catalogue
wrote to the language alone: a primary subtag, lower case, with MARC's
three letters translated rather than truncated.

**And the words themselves.** `LanguageGuess` counts the commonest words
of a closed set of languages — the ones a Dutch and English library
actually holds, and their neighbours that share words with them. Two
files in the library are wrong about themselves (`Higgs.epub` is Dutch
and says `en`; *Lonesome Dove* is English and says `nld`), and the count
read both correctly while agreeing with the file on every other book of
a 209-book sample it could judge. So the text is not only a way to fill
a blank: where it is sure and the file disagrees, it is the better
witness.
"""
from __future__ import annotations

import json
import logging
import re
import time
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar

from epub_provenance import Candidates, Provenance

LOG = logging.getLogger("epub-reader.language")

# Open Library and older files speak MARC; the library speaks BCP 47.
# Truncating is not translating: "swe" would become Swahili and "chi"
# Chamorro.
MARC_TO_BCP47 = {
    "eng": "en", "ger": "de", "deu": "de", "fre": "fr", "fra": "fr",
    "dut": "nl", "nld": "nl", "spa": "es", "ita": "it", "por": "pt",
    "rus": "ru", "swe": "sv", "nor": "no", "dan": "da", "fin": "fi",
    "pol": "pl", "cze": "cs", "ces": "cs", "gre": "el", "ell": "el",
    "heb": "he", "hun": "hu", "rum": "ro", "ron": "ro", "tur": "tr",
    "ara": "ar", "chi": "zh", "zho": "zh", "jpn": "ja", "kor": "ko",
    "hin": "hi", "vie": "vi", "tha": "th", "ice": "is", "isl": "is",
    "lat": "la", "gle": "ga", "wel": "cy", "cym": "cy", "cat": "ca",
}

# What a file says when it does not know what language it is in.
NOT_A_LANGUAGE = frozenset(
    {"", "und", "unknown", "xx", "zxx", "mul", "n/a", "none"})


class LanguageCode:
    """One spelling per language."""

    SHAPE = re.compile(r"^([a-z]{2,3})(?:[-_][a-z0-9]{1,8})*$")

    @classmethod
    def normalise(cls, raw: str | None) -> str | None:
        """`EN-us`, `eng` and `en_GB` are all `en`. Anything that is not
        shaped like a language tag is nothing rather than a guess."""
        text = (raw or "").strip().lower()
        if text in NOT_A_LANGUAGE:
            return None
        match = cls.SHAPE.match(text)
        if not match:
            return None
        primary = match.group(1)
        if primary in NOT_A_LANGUAGE:
            return None
        if len(primary) == 3:
            # A code nobody mapped is kept as it came, which is at least
            # true; truncating it would not be.
            return MARC_TO_BCP47.get(primary, primary)
        return primary


class LanguageGuess:
    """The language of some prose, from its commonest words.

    No dependency and no model: the words that make up a fifth of any
    page of ordinary prose are different in every language, and a
    closed set is all a library of English and Dutch books needs. The
    neighbours are here because they share words with those two —
    `de` is Dutch, French and Spanish, `die` Dutch and German — and a
    set without them would call a German book Dutch.
    """

    WORDS: ClassVar[dict[str, str]] = {
        "en": """the and of to a in that is was he for it with as his on be
            at by i had not are but from or have an they which you were her
            she there this all their we him been has when who will more no
            if out so said what up its about into than them can only other
            could these then do my now like our over me even most made after
            did before must through back where much your way well down
            should because each just those how too very""",
        "nl": """de het een en van in is dat op te zijn met voor niet aan er
            die maar om ook als dan bij of uit nog wel naar ze zo hij was had
            ik je we wat al door over hem haar heeft geen meer toen kan zou
            werd hun moet mijn veel waar alleen zich worden dit deze tot u
            wie weer ons onder tegen nu hebben iets niets daar hier altijd
            zelf omdat wordt kunnen""",
        "de": """der die und in den von zu das mit sich des auf für ist im
            dem nicht ein eine als auch es an werden aus er hat dass sie nach
            wird bei einer um am sind noch wie einem über einen so zum war
            haben nur oder aber vor zur bis mehr durch man sein wurde sei ich
            kein keine""",
        "fr": """de la le et les des en un du une que est pour qui dans par
            plus pas au sur ne se il ce sont avec son mais elle ou comme nous
            je vous lui leur été fait""",
        "es": """de la que el en y a los se del las un por con no una su
            para es al lo como más pero sus le ya o fue este ha sí porque
            esta son entre cuando muy sin sobre""",
        "it": """di e il la che in a per un è del non le si da una con sono
            i al dei della gli come ma lo più nel alla anche ha delle
            questo""",
    }
    SETS: ClassVar[dict[str, frozenset[str]]] = {
        code: frozenset(words.split()) for code, words in WORDS.items()}

    WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
    ENOUGH = 150        # words; fewer is a title page, not prose
    AT_MOST = 6000      # words; more changes nothing and costs time
    # Ordinary prose is a fifth to a third these words. Below this the
    # text is a table, an index, or OCR nobody corrected.
    SHARE = 0.15
    # Sure means clearly ahead of the next language, not merely ahead.
    MARGIN = 1.5

    @classmethod
    def of(cls, text: str | None) -> str | None:
        words = [w.lower() for w in cls.WORD.findall(text or "")][:cls.AT_MOST]
        if len(words) < cls.ENOUGH:
            return None
        scores = sorted(
            ((sum(1 for w in words if w in common), code)
             for code, common in cls.SETS.items()), reverse=True)
        (best, code), (second, _) = scores[0], scores[1]
        if best < cls.SHARE * len(words) or best < cls.MARGIN * max(second, 1):
            return None
        return code


class _Words(HTMLParser):
    """The prose of a document, without its markup or its scripts."""

    SILENT = frozenset({"script", "style", "head"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._silent = 0

    def handle_starttag(self, tag, attrs) -> None:
        if tag in self.SILENT:
            self._silent += 1
        self.parts.append(" ")

    def handle_endtag(self, tag) -> None:
        if tag in self.SILENT and self._silent:
            self._silent -= 1
        self.parts.append(" ")

    def handle_data(self, data) -> None:
        if not self._silent:
            self.parts.append(data)


class BookText:
    """Enough of a book's own words to tell its language by.

    Never raises: this runs inside a scan of thousands of books, and one
    malformed file must not stop it (§3.2).
    """

    DOCUMENTS = (".xhtml", ".html", ".htm")
    # The largest sections, because the front matter — a cover, a
    # copyright page, a publisher's list in another language — is small.
    SECTIONS = 3
    # Past the front matter of a PDF, where the book begins.
    PDF_FROM, PDF_PAGES = 3, 10

    def sample(self, path: Path, fmt: str) -> str | None:
        try:
            if fmt == "epub":
                return self._epub(Path(path))
            if fmt == "pdf":
                return self._pdf(Path(path))
        except Exception as exc:
            LOG.info("no text from %s: %s", Path(path).name,
                     type(exc).__name__)
        return None

    def _epub(self, path: Path) -> str:
        if path.is_dir():
            files = sorted(
                (f for f in path.rglob("*")
                 if f.suffix.lower() in self.DOCUMENTS and f.is_file()),
                key=lambda f: -f.stat().st_size)[:self.SECTIONS]
            raw = [f.read_bytes() for f in files]
        else:
            with zipfile.ZipFile(path) as archive:
                entries = sorted(
                    (i for i in archive.infolist()
                     if i.filename.lower().endswith(self.DOCUMENTS)),
                    key=lambda i: -i.file_size)[:self.SECTIONS]
                raw = [archive.read(i) for i in entries]
        words = _Words()
        for document in raw:
            words.feed(document.decode("utf-8", "replace"))
        return " ".join(words.parts)

    def _pdf(self, path: Path) -> str | None:
        try:
            import pypdfium2 as pdfium
        except ImportError:                     # pragma: no cover
            return None
        document = pdfium.PdfDocument(path)
        try:
            count = len(document)
            # A short PDF has no front matter worth skipping.
            first = self.PDF_FROM if count > self.PDF_FROM * 2 else 0
            return " ".join(
                document[i].get_textpage().get_text_range()
                for i in range(first, min(count, first + self.PDF_PAGES)))
        finally:
            document.close()


class LanguagesFromText:
    """Reads the language of the books whose words can be read.

    **What the file said stands unless the text is sure and disagrees.**
    A language set by hand, or confirmed by a catalogue, is never read
    over (§5). Every book is read **once**: the answer, or the fact that
    there was none, is kept in `provider_query` against the book's
    content hash, so a restart reads only what is new — and a file
    replaced by a better copy is a new book and is read again.
    """

    SOURCE = "text"
    CACHE = "text-language"
    FORMATS = ("epub", "pdf")

    def __init__(self, database, library_root: Path,
                 reader: BookText | None = None) -> None:
        self._db = database
        self._root = Path(library_root)
        self._provenance = Provenance(database)
        self.reader = reader or BookText()

    # ── what would change ─────────────────────────────────────────────
    def plan(self) -> list[dict]:
        plan = []
        for row in self._books():
            read = self._read(row)
            held = LanguageCode.normalise(row["language"])
            if read and read != held:
                plan.append({"id": row["id"], "language": read,
                             "was": row["language"], "path": row["path"]})
        return plan

    def untidy(self) -> list[tuple[str | None, int]]:
        """Stored spellings that are not the one spelling."""
        return [(LanguageCode.normalise(r["language"]), r["book_id"])
                for r in self._db.connection.execute(
                    "SELECT book_id, language FROM metadata"
                    " WHERE language IS NOT NULL")
                if LanguageCode.normalise(r["language"]) != r["language"]]

    def run(self, apply: bool = False) -> dict:
        untidy = self.untidy()
        plan = self.plan()
        report = {
            "detected": 0, "tidied": 0,
            "would_detect": len(plan), "would_tidy": len(untidy),
            "corrected": sum(1 for item in plan
                             if LanguageCode.normalise(item["was"])),
            "plan": plan,
        }
        if not apply:
            return report

        connection = self._db.connection
        # The same statement, spelt the library's way: the file still
        # owns it, so nothing is recorded.
        connection.executemany(
            "UPDATE metadata SET language = ? WHERE book_id = ?", untidy)
        now = int(time.time())
        for item in plan:
            connection.execute(
                "UPDATE metadata SET language = ? WHERE book_id = ?",
                (item["language"], item["id"]))
            # Recorded, or the next rescan takes the file's word back.
            self._provenance.record(item["id"], "language", "provider",
                                    self.SOURCE, now=now)
        report["detected"], report["tidied"] = len(plan), len(untidy)
        if plan:
            LOG.info("read the language of %d books", len(plan))
        return report

    @staticmethod
    def summary(report: dict) -> str:
        if report["detected"] or report["tidied"]:
            return (f"Languages: read {report['detected']} books"
                    f" ({report['corrected']} correcting what the file"
                    f" said), tidied {report['tidied']} spellings.")
        if report["would_detect"] or report["would_tidy"]:
            return (f"Languages: would read {report['would_detect']} books"
                    f" ({report['corrected']} correcting what the file"
                    f" said) and tidy {report['would_tidy']} spellings."
                    " Run with --apply to write them.")
        return "Languages: nothing new to read."

    # ── internals ─────────────────────────────────────────────────────
    def _books(self):
        formats = ",".join(f"'{f}'" for f in self.FORMATS)
        return self._db.connection.execute(
            f"""
            SELECT b.id, b.path, b.format, b.content_hash, m.language
              FROM book b
              JOIN metadata m ON m.book_id = b.id
             WHERE b.missing_since IS NULL AND b.duplicate_of IS NULL
               AND b.format IN ({formats})
            """ + Candidates.clause("language")).fetchall()

    def _read(self, row) -> str | None:
        """What the text says, read once and remembered."""
        connection = self._db.connection
        cached = connection.execute(
            "SELECT answer FROM provider_query"
            " WHERE source = ? AND question = ?",
            (self.CACHE, row["content_hash"])).fetchone()
        if cached is not None:
            return json.loads(cached["answer"]) if cached["answer"] else None
        language = LanguageGuess.of(
            self.reader.sample(self._root / row["path"], row["format"]))
        connection.execute(
            "INSERT OR REPLACE INTO provider_query"
            " (source, question, answer, asked_at) VALUES (?, ?, ?, ?)",
            (self.CACHE, row["content_hash"],
             json.dumps(language) if language else None, int(time.time())))
        return language
