# epub-reader

A browser-based e-book library and reader, built in the shape of `dlna-gateway-2.0`: **Browse · Search · Read**.

EPUB first, MOBI/AZW3 shortly after, PDF eventually.

**Status: M0–M8 complete, M9 in progress. Running against the real library: 2,268 books in six formats, 289 duplicate copies folded away, 154 series.** All seven decisions settled (§9).

The reader has contents, themes, typography controls, server-side position, and highlights with notes — reachable on a phone and an iPad as well as a Mac. The library reads EPUB, MOBI, AZW3, PRC, PDF and CBZ, including EPUBs stored as directories. Five maintenance passes tidy what thirty years of other people's filing left behind, all dry-run by default and none of them touching a file.

**Next:** series for books whose filenames do not name one — Frank Herbert's own Dune novels among them (§8.8).

---

## 1. What this is

Calibre is a library manager with a viewer bolted on. Foliate and Thorium are readers with a file list bolted on. Apple Books is polished but gives you no control over your own metadata.

**The product is the intersection: a reader you enjoy using, on a library you can actually curate**, reachable from the couch, the phone, and the desk — the same way `dlna-gateway` already serves music.

Because the reading engine is a dependency (§3), it is close to commodity. Effort goes into the **index, the browse UI, and the metadata**, because that is the only part that is ours.

**Non-goals.** Each is a large product on its own, and none of them is what this is for.

- Format conversion · e-reader device sync · DRM · news/RSS recipes · EPUB editing
- **No Calibre interaction of any kind** — no importing its database, no shelling out to its CLI, no reading its library folders. Everything is built here. (§9, decision 3)

## 2. Stack

**The dlna-gateway stack, because this is a sibling application to it.** Same shapes, same conventions, same machine.

```
FastAPI + hypercorn                  same as dlna-gateway
SQLite + FTS5                        same, incl. unicode61 remove_diacritics
watchdog                             library folder watching
Pillow                               cover thumbnails
static/{index.html,app.css,app.js}   vanilla, no build step, no framework
static/vendor/foliate-js/            pinned submodule
```

**It runs as its own service** (decision 2), not as a tab inside dlna-gateway:

| | |
| --- | --- |
| Service | `epub-reader` |
| launchd label | `com.roha.epub-reader` |
| Port | **8770** — clear of dlna-gateway's 8765 and localfs's 8200 |
| Database | `books.db` in the project directory |
| Entry point | `epub_gateway.py` |
| TLS | same Tailscale cert host as dlna-gateway, read from `.env` |

Carried over unchanged: one `app.css` with `:root` tokens, one `app.js` of plain functions, launchd plist, `.env` config, PWA manifest + service worker, rotating `gateway.log`.

## 2.1 Scope: what the reader can open

**EPUB, MOBI, AZW3, PRC, PDF and CBZ.** Only CBR stays out: it is RAR,
which nothing in the stack reads. Two files named `.cbz` turned out to
be RAR inside, and the error says so rather than "not a comic archive" —
the reader can act on the first and not on the second.

PDF is a different rendering model: pdf.js rasterises pages onto a
canvas rather than reflowing text, so the typography controls do not
apply to it. It is 585 books here, though, and a reader that cannot open
them is a reader you keep leaving.

`BookLibrary.SUFFIXES` is the single switch, and `resolve()` refuses to
serve anything outside it.

## 3. Reading engine: foliate-js

We do **not** write a rendering engine. [foliate-js](https://github.com/johnfactotum/foliate-js) (MIT) handles parsing, reflow pagination, TOC, in-book search, and CFI generation/resolution — the genuinely hard part of annotations.

Assessment from reading the source: ~7,100 lines of plain ES modules, no build step, a 374-line README documenting interfaces as contracts. Vendored deps are zip.js (BSD-3), fflate (MIT), pdf.js (Apache) — all permissive.

Three risks, each now a rule:

**It declares itself unstable** (`version: 0.0.0`, README says expect the API to change at any time).

> **Rule: exactly one file imports foliate-js** — `static/reader.js`, exposing our own `open / goTo / position / annotate / search`. Upstream churn lands in one file. Pin it as a submodule; update deliberately.

**Bus factor of one** — 268 of ~290 commits from a single maintainer, bursty cadence. MIT plus 7k readable lines makes forking a genuine fallback.

**Scripted content is a real hole.** EPUBs can contain JavaScript, and the README is blunt that iframe sandboxing cannot contain it (same-origin `blob:` URLs, plus a WebKit bug forcing `allow-scripts`).

> **Rule: a CSP blocking all non-`self` scripts ships from day one**, set as a response header in FastAPI. Not a later hardening pass. Test against <https://github.com/johnfactotum/epub-test>.

### 3.1 What M0 established

Pinned at `78914ae` (2026-05-01) as a submodule at `static/vendor/foliate-js`.

- **It renders.** EPUB parsing, pagination, TOC and metadata all work under the strict CSP, unmodified. `view.js` disables zip.js web workers itself, so no `worker-src` relaxation is needed.
- **CSP genuinely contains EPUB scripts.** A hostile fixture carrying both an inline and an external script is rendered; neither runs. Verified negatively — weakening `script-src` to allow `'unsafe-inline' blob:` makes the test fail, so it is not passing vacuously.
- **`foliate-view` attaches a CLOSED shadow root.** CSS selectors cannot pierce it, so `frameLocator('foliate-view iframe')` never matches. Book sections must be reached through `page.frames()` by their `blob:` URL. This cost time once; `tests/frontend/reader.spec.js` documents it.
- **Fonts.** dlna-gateway loads Syne + DM Mono from Google Fonts. This app uses a system stack so the CSP needs no external origins at all. **Self-hosting those two faces was flagged for M1 and was not done — it carries to M2.** The app chrome therefore still differs from dlna-gateway's typography, though the palette matches.

Known upstream weak spots we design around: the paginator uses CSS multi-column (epub.js's strategy, same slowness and CSS quirks); in-book search is slow enough to need incremental loading; **the PDF adapter is self-described as a proof-of-concept**. Upstream tests cover CFI parsing only.

### 3.2 What the real library taught us

Validated against `~/Books` when M2 began: 34 GB, 1,327 EPUB files, 585
PDFs, 309 CBZ, 295 CBR. Everything below is measured, not assumed. (The
library is 2,268 books now, and all of those formats but CBR are read —
§2.1.)

**Two structural surprises, both of which M2 must handle:**

| Finding | Count | Consequence |
| --- | --- | --- |
| Unpacked EPUBs stored as **directories** (`Drive.epub/`, iTunes exports with `iTunesMetadata.plist`) | 265 | Fixed in M8 — indexed in place and zipped on the way out. |
| Books in **subfolders** (series and collection folders) | 1,131 | Invisible today — the scan is not recursive. That is 44% of the library. |

**Metadata quality** (260-book sample) — better than feared, except names:

| | |
| --- | --- |
| title / author present | 100% |
| publication date | 97% |
| authors written `Last, First` | **30%** — sort-name normalisation is not optional |
| ISBN | 18% |
| subject/genre | 19% |
| description | 16% |
| series | **7%** — so series must come from providers, not files |

**Broken files exist and must never crash a scan:** 8 EPUBs with an empty
spine, 5 whose manifest references missing files, and at least one file
that is not a zip at all. M2's indexer catches per-book errors and keeps going.

**Performance:** hashing 1,093 books cold takes ~1.0 s and is then cached
in memory (~0.02 s). Acceptable now, but the cache dies with the process —
M2 persists `content_hash` in the `book` table.

#### Two upstream workarounds, both contained in `static/reader.js`

Real books broke in ways no synthetic fixture did. Both fixes live in the
adapter, since the submodule is never edited.

1. **`render()` before a document exists.** `paginator.js` observes the
   container with a `ResizeObserver` that calls `render()` →
   `View.columnize()`, which dereferences `this.document.documentElement`
   with no null check. Any resize landing while a section loads throws and
   abandons the layout pass. `#guardRenderer()` makes `render()` a no-op
   until there is a document.
2. **`%2C` in table-of-contents hrefs.** Calibre writes the NCX href
   percent-encoded (`Name%2c A_split_0.html`) while the OPF manifest keeps
   the literal comma. `resolveHref()` looks it up with `decodeURI()`, which
   by design does **not** decode reserved characters, so the lookup misses
   and navigation silently fails. **This affected ~22% of the real
   library.** `goTo()` now resolves first and retries with reserved
   characters decoded — resolving before navigating matters, because a
   failed `goTo()` logs an error we cannot suppress. Measured on a
   50-book sample afterwards: warnings fell from 18-in-80 to 1-in-50.

Affected books were always readable by paging; it was the contents that
broke. Fixtures now model both cases (`cover=True`, `encode_nav_hrefs=True`).

## 4. Interface

Same shell as dlna-gateway, with the same responsive behaviour: three columns on desktop, two on tablet, bottom nav on phone.

```
┌──────────────────────────────────────────────────────────────────────┐
│ BOOKS  v2.0   [LIB ▾]  [SORT ▾]      🔍 Search library…      Ready   │  header
├──────────────────────────────────────────────────────────────────────┤
│  Browse │ Search │ Read                                              │  tabs
├──────────────────────────────────────────────────────────────────────┤
│ Authors  Titles  Series  Genres  Years  Shelves                      │  browse modes
│ A B C D E F G H I J K L M N O P Q R S T U V W X Y Z #                │  letter bar
├──────────────────┬────────────────────────────┬──────────────────────┤
│ ← back           │                            │  Shelves             │
│ Le Guin, Ursula  │      BOOK DETAIL           │  ─────────           │
│                  │           or               │  📕 Reading now (3)  │
│  ▸ Rocannon's…   │      READER (full width)   │  ⭐ Favourites       │
│  ▸ The Left Ha…  │                            │  📥 Want to read     │
│  ▸ The Dispos…   │                            │  ＋ New shelf        │
│                  │                            │                      │
│ ‹ Prev  1/4 Next›│                            │                      │
├──────────────────┴────────────────────────────┴──────────────────────┤
│ ● Indexed 2,418 books · 12 unmatched                                 │  statusbar
└──────────────────────────────────────────────────────────────────────┘
```

**Browse modes** map one-for-one onto the music app's Artists/Albums/Tracks/Genres/Decades:

| Mode | Drill-down |
| --- | --- |
| Authors | author → their books, grouped by series |
| Titles | flat A–Z list of every book |
| Series | series → books in reading order |
| Genres | genre → books |
| Years | decade → year → books, by **first published** (§5) |
| Shelves | collection → books |

Letter bar, drill-down with `← back`, and pagination behave exactly as in dlna-gateway. Sort applies within any list: title, author, series, first published, date added, last opened, progress.

**Search** is FTS5 over title, subtitle, author, series, genre, and synopsis, with the same `unicode61 remove_diacritics 1` tokenizer already in use. Results group by type — Authors, Series, Books — so `le guin` offers the author page as the top hit rather than 40 loose rows.

**Read** is the third tab and the analogue of Now Playing: full viewport, chrome auto-hides, right panel becomes TOC / Bookmarks / Highlights. A **Continue reading** bar (the mini-player analogue) sits at the bottom of every other view showing the current book, chapter, and percentage.

### 4.1 Book detail

```
┌─────────────────────────────────────────────────────────────┐
│ ┌─────────┐   The Left Hand of Darkness                     │
│ │         │   Hainish Cycle · Book 4                        │  ← series & number
│ │  cover  │   Ursula K. Le Guin                             │
│ │         │   Science Fiction · First published 1969        │  ← genre, first published
│ └─────────┘   EPUB · 312 pp · English · 47% read            │
│                                                             │
│   ▶ Read      ⭐ Favourite   ＋ Shelf   ✏️ Edit metadata     │
├─────────────────────────────────────────────────────────────┤
│ SYNOPSIS                                                    │
│ Genly Ai is an emissary from a consortium of planets…       │
├─────────────────────────────────────────────────────────────┤
│ ABOUT THE AUTHOR                                            │
│ ┌────┐  Ursula K. Le Guin  ·  1929–2018  ·  American        │
│ │ 📷 │  Best known for the Earthsea and Hainish books…      │
│ └────┘                                                      │
├─────────────────────────────────────────────────────────────┤
│ ALSO PUBLISHED — novels & novellas          [ show all ▾ ]  │
│                                                             │
│ Hainish Cycle                                               │
│   1  Rocannon's World                 1966   ✓ in library   │
│   2  Planet of Exile                  1966   ✓ in library   │
│   3  City of Illusions                1967   ·  not owned   │
│   4  The Left Hand of Darkness        1969   ● this book    │
│   5  The Dispossessed                 1974   ✓ in library   │
│                                                             │
│ Earthsea                                                    │
│   1  A Wizard of Earthsea             1968   ✓ in library   │
│   …                                                         │
│                                                             │
│ Standalone                                                  │
│   The Lathe of Heaven                 1971   ·  not owned   │
└─────────────────────────────────────────────────────────────┘
```

**Also published** shows **novels and novellas only** (decision 6) — collections, anthologies, omnibuses, and non-fiction are filtered out, because an unfiltered Open Library bibliography is mostly noise. `show all` reveals everything, including works we could not classify. It is grouped by series and ordered by number, it includes works you do **not** own so it doubles as a gap-finder, and owned rows click straight through.

### 4.2 Reader

Full viewport, paginated by default. Click zones left and right, arrow keys, swipe on touch. Chrome fades while reading and returns on tap.

Controls: font family and size, line height, margin width, column count, theme (light / sepia / dark / black), and paginated vs scrolled — the paginator exposes all of these as element attributes, so they are cheap. Position is written back as a CFI on every relocate, debounced. Selecting text offers highlight, note, copy, and search-in-book.

## 5. Data model

Two decisions carry the design.

**Files stay where they are.** We index in place, so **identity is the content hash, not the path** — moving or renaming a file updates a row instead of creating a duplicate. We never move, rename, or write to the user's book files.

**Metadata carries field-level provenance.** `book_meta` in dlna-gateway already gestures at this with its `source` / `fetched_at` columns; this is the full version:

> Every field records where its value came from — `file`, `provider`, `user` — and whether it is locked. **Enrichment may overwrite a field only if its origin is `file` and it is unlocked. A `user` value is never overwritten automatically.**

That is what makes re-running enrichment across the whole library safe rather than a gamble.

```sql
-- Physical files. Identity is content hash, so moves don't duplicate.
CREATE TABLE book (
  id            INTEGER PRIMARY KEY,
  content_hash  TEXT    NOT NULL UNIQUE,   -- sha256
  path          TEXT    NOT NULL,          -- rewritten on move
  format        TEXT    NOT NULL,          -- epub|azw3|mobi|fb2|cbz|pdf
  size_bytes    INTEGER NOT NULL,
  mtime         INTEGER NOT NULL,
  added_at      INTEGER NOT NULL,
  missing_since INTEGER,                   -- vanished; row never auto-deleted
  -- The same work held twice. The spare keeps its row and its path and
  -- is simply not shown, so this is reversible and no file is touched.
  duplicate_of  INTEGER REFERENCES book(id)
);

CREATE TABLE metadata (
  book_id          INTEGER PRIMARY KEY REFERENCES book(id) ON DELETE CASCADE,
  title            TEXT,
  subtitle         TEXT,
  title_sort       TEXT,     -- precomputed, see §7
  file_title       TEXT,     -- what the file called it, before §6.3
  synopsis         TEXT,     -- the book's own blurb

  -- THE TWO DATES ARE NOT THE SAME THING, and conflating them is the
  -- single most common metadata error in e-book libraries.
  first_published  INTEGER,  -- year the WORK first appeared      → 1969
  edition_date     TEXT,     -- ISO date of THIS edition/printing → 2019-10-01

  publisher        TEXT,
  language         TEXT,     -- BCP 47
  isbn13           TEXT,
  page_count       INTEGER,
  series_id        INTEGER REFERENCES series(id),
  series_index     REAL      -- REAL, because novellas are book 2.5
);

-- Genre is many-to-many: books legitimately have several.
-- position 0 is the primary genre, used for the Genres browse mode.
-- bisac_code anchors us to the standard subset; see §6.
CREATE TABLE genre (
  id         INTEGER PRIMARY KEY,
  bisac_code TEXT UNIQUE,          -- e.g. 'FIC028000'
  name       TEXT NOT NULL UNIQUE, -- display: 'Science Fiction'
  sort_name  TEXT NOT NULL
);
CREATE TABLE book_genre (
  book_id  INTEGER NOT NULL REFERENCES book(id) ON DELETE CASCADE,
  genre_id INTEGER NOT NULL REFERENCES genre(id),
  position INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (book_id, genre_id)
);

CREATE TABLE author (
  id        INTEGER PRIMARY KEY,
  name      TEXT NOT NULL UNIQUE,  -- display: "Ursula K. Le Guin"
  sort_name TEXT NOT NULL,         -- sort:    "Le Guin, Ursula K."
  -- "About the author"
  bio         TEXT,
  bio_source  TEXT,                -- 'openlibrary' | 'wikipedia' | 'user'
  photo_path  TEXT,
  born        TEXT,                -- ISO, may be year-only
  died        TEXT,
  nationality TEXT,
  website     TEXT,
  fetched_at  INTEGER
);

CREATE TABLE book_author (
  book_id   INTEGER NOT NULL REFERENCES book(id) ON DELETE CASCADE,
  author_id INTEGER NOT NULL REFERENCES author(id),
  role      TEXT    NOT NULL DEFAULT 'author',  -- author|editor|translator
  position  INTEGER NOT NULL,                   -- credit order matters
  PRIMARY KEY (book_id, author_id, role)
);

CREATE TABLE series (
  id        INTEGER PRIMARY KEY,
  name      TEXT NOT NULL UNIQUE,
  sort_name TEXT NOT NULL
);

-- "Also published": the author's bibliography, INCLUDING works we don't own.
-- book_id is non-null only when a work resolves to a file we have.
-- work_type drives the novels-and-novellas filter; see §6.
CREATE TABLE author_work (
  id              INTEGER PRIMARY KEY,
  author_id       INTEGER NOT NULL REFERENCES author(id) ON DELETE CASCADE,
  title           TEXT    NOT NULL,
  first_published INTEGER,
  series_name     TEXT,
  series_index    REAL,
  isbn13          TEXT,
  work_type       TEXT    NOT NULL DEFAULT 'unknown',
                  -- novel|novella|collection|anthology|omnibus|nonfiction|unknown
  book_id         INTEGER REFERENCES book(id),  -- NULL = not in library
  source          TEXT    NOT NULL,
  fetched_at      INTEGER NOT NULL,
  UNIQUE(author_id, title, first_published)
);

CREATE TABLE field_provenance (
  book_id    INTEGER NOT NULL REFERENCES book(id) ON DELETE CASCADE,
  field      TEXT    NOT NULL,   -- 'title', 'first_published', ...
  origin     TEXT    NOT NULL,   -- 'file' | 'provider' | 'user'
  source     TEXT,               -- provider id
  locked     INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (book_id, field)
);

-- Server-side and shared across all devices (decision 7). Last write wins.
CREATE TABLE reading_state (
  book_id     INTEGER PRIMARY KEY REFERENCES book(id) ON DELETE CASCADE,
  cfi         TEXT,      -- exact position, engine-native
  fraction    REAL,      -- 0..1, for display and as CFI fallback
  status      TEXT NOT NULL DEFAULT 'unread',  -- unread|reading|finished|abandoned
  opened_at   INTEGER,
  finished_at INTEGER
);

CREATE TABLE annotation (
  id         INTEGER PRIMARY KEY,
  book_id    INTEGER NOT NULL REFERENCES book(id) ON DELETE CASCADE,
  cfi        TEXT NOT NULL,   -- range CFI
  text       TEXT NOT NULL,   -- quoted text, denormalized on purpose
  note       TEXT,
  color      TEXT,            -- one of a closed set; see §8.1
  fraction   REAL NOT NULL DEFAULT 0,  -- CFIs do not sort as strings
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE VIRTUAL TABLE book_fts USING fts5(
  title, subtitle, author, series, genre, synopsis,
  content=metadata, content_rowid=book_id,
  tokenize='unicode61 remove_diacritics 1'
);
```

Shelves/collections follow the same join-table shape. Covers and author photos live on disk keyed by hash, mirroring `art_cache/`, not as blobs in SQLite.

`annotation.text` deliberately duplicates what the CFI points at: CFIs can fail to resolve if a file is replaced, and a highlight whose text survives is recoverable while a dangling CFI is not.

## 6. Metadata enrichment

The hard part is not calling APIs. It is that most EPUBs have no ISBN, so matching is fuzzy against providers that confidently return the wrong edition.

```
extract → normalize → fetch candidates → score → ┬ ≥0.90      apply automatically
                                                 ├ 0.60–0.90  queue for review
                                                 └ <0.60      leave alone, mark unmatched
```

| Provider | Cost | Gives us |
| --- | --- | --- |
| **Open Library** | free | ISBN lookup, covers, **author bio, and the works list that powers "Also published"** |
| Google Books | free | broad coverage, good synopses, BISAC-ish categories |
| Hardcover | free tier | modern GraphQL; Goodreads' API died in 2020 |
| Wikipedia/Wikidata | free | richer author bios, birth/death, nationality, photo |
| ISBNdb | paid | best coverage; only if the free tiers disappoint |

Open Library is the primary source precisely because it models **authors and works as first-class entities**, which is what §4.1's bottom two panels need — no other free provider gives you a bibliography.

**Normalization does most of the work:** strip subtitles after `:`, strip bracketed junk and scanner watermarks, drop leading articles, NFKD + fold diacritics, normalize `Last, First` / `First Last` / `J.R.R.` vs `J. R. R.`

**Scoring** (starting point, to tune against the real library): ISBN exact → 1.0 and stop. Otherwise title similarity ×0.5 + author surname ×0.3 + year proximity ×0.1 + language ×0.1. **Auto-apply at ≥0.90** (decision 4).

**Series** comes from provider data first. An EPUB's OPF may also carry a `calibre:series` / `series_index` custom meta — that is just a field inside the file, so we read it as a hint like any other embedded metadata. It implies no dependency on Calibre.

### 6.3 Repairing a library that was catalogued by hand

A real collection carries thirty years of other people's filing. Four maintenance passes fix it, all of them **dry-run by default**, all of them **database-only — no file is ever renamed, moved or written**.

| Pass | Endpoint | Module |
| --- | --- | --- |
| Merge duplicate authors | `POST /api/authors/dedupe` | `epub_dedupe.py` |
| Books filed under their own title | `POST /api/books/repair-authors` | `epub_repair.py` |
| Titles that are really filenames | `POST /api/books/repair-titles` | `epub_titles.py` |
| Merge two authors on your say-so | `POST /api/authors/merge` | `epub_dedupe.py` |
| One row per work | `./run.sh dedupe [--apply]` | `epub_duplicates.py` |
| Series from filenames | `./run.sh series [--apply]` | `epub_series.py` |
| All three together | `./run.sh repair [--apply]` | `epub_maintenance.py` |
| Weekly author refresh | `./run.sh refresh [--max N]` | `epub_refresh.py` |

**Merging authors is destructive, so the bar is high.** A group merges only when the names are identical once spacing and punctuation are removed, when Open Library resolves both to the same author, when one spelling is *unknown to Open Library* while a near-identical one is well known — the signature of a typo — or when **everything the other name is credited with is in the keeper's bibliography**.

That last rule is the strongest of the four, because it asks what the books *are* rather than how the name is spelled, and it is the only one that settled the Donaldsons: Open Library holds a **second record** for "Stephen Donaldson" (38 works beside the canonical 126), and "Stephan Donaldson" scores 0.87 against "Stephen R. Donaldson" — just under the typo bar. But all four of their books are Thomas Covenant novels, every one in the keeper's works list. One shared title would prove nothing, since two writers can name a book the same, so it asks for all of them. That last rule is what separates `Issac Asimov` from `Isaac Asimov` while leaving `Stephen King` and `Stephen Hawking` alone: both of those are real people, and the provider knows both. Whatever is refused is **reported**, never silently dropped — and `POST /api/authors/merge` is how a reader who knows better overrides the refusal. That is what settled `Anne Rice / Ann Rice`, `Arthur Clarke / Arthur C. Clarke`, and the four spellings of `L. E. Modesitt Jr.`: Open Library holds two records for each of the first two, and none of their books overlap in the library, so nothing could prove it. A hand merge records provenance exactly like an automatic one, so a rescan does not undo it.

The spelling that survives is not simply the one with the most books — that kept `DIANE CAREY` over `Diane Carey` and `Raymond E Feist` over `Raymond E. Feist`. Open Library supplies the spelling, but only when its answer is the same name and better formed: it holds `J.k. Rowling`, which is not an improvement on anything.

**A book filed under its own title** ("Annihilation" by Annihilation) is looked up by title. The trap is that a bare title is weak evidence — "Second Chance" returned a book James Patterson did not write — so where the filename names its author (`Cussler, Clive - Dirk Pitt 01 - Pacific Vortex`), that name arbitrates, and a candidate contradicting it is refused. The same rule catches authors holding a *fragment* of the title: `Jack And Jill` was split on the "and" and both halves became people.

**A provider's title is not always a title.** Open Library carries the publisher's marketing — `Kiss the Girls: A gripping detective thriller in the Sunday Times bestselling series (Alex Cross 2)` — which is a blurb with a book's name in front of it. A trailing bracket is the shelf talking (`(Alex Cross 1)`, `(Garrett Files)`), and a **correction may tidy a title but never invent a subtitle the file did not have**, so anything after a colon is dropped unless the file had one too. Editions and translations mean the same work is listed several times over; those collapse to one, keeping the fuller spelling, because `A Falcon Flies` and `Falcon Flies` are one book and the article belongs to it.

**Titles come from the author's bibliography, not from a title search** — the idea being that once the author is settled, the only question left is *which of their books this is*. Matching is against that author's works and nothing else. Two guards earn their keep, and they pull against each other. Open Library caps a bibliography at 200 works, so Asimov's list has `Foundation` but not `Foundation and Empire` — a contained work must account for **more than half** the title, or the one quietly becomes the other. Above half rather than at it, because `Hunter's Pride` is exactly half `The Hunters` and is not that book. But a work that **ends** the title should win, because a filename puts the shelf first and the book last; without that, five different Snicket books became `A Series of Unfortunate Events`, the series being a work too, and a longer one.

**A colon after a number separates the shelf from the book.** Cleaning treats what follows a colon as a subtitle and drops it, which is right for `On Writing Well: An Informal Guide` and catastrophic for `The Belgariad 1: Pawn of Prophecy` — that leaves `belgariad 1`, which matches the series omnibus. Applied to the real library it turned five Eddings novels into `The Belgariad`, four of Shiloh Walker's into `The Hunters`, and three Anne Rices into `Vampire Chronicles`. Twelve books, caught by looking for corrected titles that had become identical to a sibling's.

What settles the two containment rules is **what the leftover words are**. In `A Series of Unfortunate Events 1 - The Bad Beginning` everything in front of the book is another work, a number and an article — scaffolding. In `Forward the Foundation` it is `forward`, which is part of the title. Scaffolding must carry a **number**, or `Storm Warning Storm Rising` resolves to the second of the two books it names instead of neither. And a leading series number comes off without asking anyone (`01 Lord Foul's Bane`), except when the number is the title: `10 is Better Than 01` and `50 Philosophy Ideas You Really Need to Know` are real books, and zero-padding is what tells them apart.

The title the file gave is kept in `metadata.file_title`, so every rename is reversible — which is how twelve bad ones were undone rather than argued about. Two titles no rule can reach, because the filename hides the book in the middle (`Anne Rice - Memnoch The Devil - Vampire Chronicles #5`), are set by hand and **locked**: `origin = 'user'` means no pass touches them again.

**Reviewing the result matters as much as the rules.** Sampling thirty of 251 changes found the marketing subtitles; the collapses only showed up by asking which corrected titles had become *identical to a sibling's*. Sixteen groups now share a title and all but none are genuine — they are duplicate copies of a book, which is a useful thing to have learned about the library.

### 6.4 The file is the first word, not the last

Corrections have to survive a rescan, and until this was fixed they did not: `_write_metadata` overwrote every column and ran `DELETE FROM book_author` unconditionally, so replacing a book file put its wrong author straight back. The indexer now reads `field_provenance` first and **only takes from the file what the file still owns** — the same rule enrichment follows. A routine scan short-circuits on unchanged size and mtime, so this only ever bit when a file was touched, edited, or replaced with a better copy.

Every pass records provenance for what it changes, or the next scan would undo it.

### 6.8 The status line counts what is on the shelves

`/api/status` reported **590 authors** while the Browse tab listed 395, and both numbers were honest. A repair leaves an author row behind rather than deleting one another book might still use, and `genres.tsv` is loaded whole at startup whether a book uses a term or not — so counting rows counts things nobody can see.

The status counts now join through to a **present** book, exactly as the browse lists do. A status line that disagrees with the screen below it is wrong however defensible each number is on its own.

### 6.6 Running the passes as the library grows

New books arrive with the same defects the first 1,300 had, so the three passes are also a command:

```sh
./run.sh repair            # check authors and titles, change nothing
./run.sh repair --apply    # ...and fix what it can prove
```

They run in dependency order — **authors → books → titles** — because everything else is asked *of* an author: a book filed under its own title is looked up by the name in its filename, and a title is matched against that author's bibliography. Fixing the names last would mean asking the wrong person.

`REPAIR_ON_SCAN=true` in `.env` makes `run.sh start|restart` do it after the scan, so dropping books into `LIBRARY_ROOT` and restarting is the whole workflow. It is **off by default**: reaching a provider is not something to do on someone's behalf without being asked.

**A restart must not become 1,200 requests.** Everything a provider says is written to the database — the works list into `author_work`, the attempt itself into `author.fetched_at`, which is stamped even when Open Library has never heard of the author. Both are checked before asking, so an author is looked up **once, ever**, and every later run reads our own tables. That is what makes repair-on-restart affordable rather than antisocial.

### 6.7 Keeping author data fresh: oldest first, ten a week

A bibliography is not static — living authors publish — so author data needs re-asking about once a year. The obvious schedule is a letter of the alphabet every fortnight, which covers 26 letters in a year. **Surnames are not evenly spread, so that does not work**: this library has 49 authors filed under S and one under U, several letters with none at all, and a few sort names starting with a digit. One fortnight would make 147 requests and the next three, while the S authors waited a year regardless of what they published.

Sorting by **when we last asked** balances itself, needs no calendar, and picks up wherever it left off when a week is missed.

```sh
./run.sh refresh            # the ten oldest, which is the weekly job
./run.sh refresh --max 25   # more of it at once
```

`com.roha.epub-reader.refresh.plist` runs that every Sunday at 04:12 — not on the hour and not at midnight, where every other cron job lives. Ten authors is thirty requests, some fifteen seconds of traffic, and a full round of 395 authors in about forty weeks.

The same budget then goes where books actually appear:

| | Comes round | Because |
| --- | --- | --- |
| Living author | every 6 months | they publish |
| Author who died | every 3 years | nothing new is coming, though editions and scholarship still trickle in |
| Never found on Open Library | every year | a few hundred of these are series names and scanner handles; asking twice a year is asking twice for the same silence |

An author whose books have all vanished from the library is not refreshed at all.

### 6.5 Asking a provider 1,200 times

A library-wide title run asks Open Library about 400 authors, three requests each. Sent flat out that earns `503`, `Connection reset by peer`, and SSL handshake timeouts — all three, in one run. **Open Library publishes its limits: 1 request/second anonymously, 3/second for a client that identifies itself** with an application name and contact address — which `OPENLIBRARY_CONTACT_EMAIL` is for. `PROVIDER_MAX_RPS` sets the pace, defaults to **2** to leave headroom, and is capped at their published ceiling. Their APIs are explicitly not a bulk backend; for bulk work they ask you to use the monthly data dumps, which is a fair description of what a 1,300-book sweep is, and the reason every answer is cached.

`PoliteFetcher` spaces requests out and retries what is worth retrying (429, 5xx, connection failures; never a 404, which is an answer). It wraps the fetcher rather than the providers, so the providers stay ignorant of it, and **one instance is shared by the whole service** — four providers each pacing themselves is four unsynchronised streams.

**Retrying is right for a hiccup and wrong for an outage.** Open Library went down twice during this work, and each *new* author then cost four attempts and 48 seconds of backoff before being abandoned — for the ~200 authors the MOBI books brought in, that is close to three hours of achieving nothing. After four whole failures in a row a host is treated as down and calls fail at once, which turned the same run into two and a half minutes.

The judgement is **per host**, so Google Books being down says nothing about Open Library, and an HTTP error does not count: a 404 is an answer, and a host that answers is not a host that is down. One success clears it, and so does five minutes — the service keeps a fetcher for its whole life and has to recover without being restarted. Nothing is lost either way: a failed lookup does not stamp `fetched_at`, so those authors stay queued for the weekly refresh.

The test suite injects a fetcher that refuses immediately, so nothing in it touches the network. One endpoint test had been quietly making real requests all along; it took 48 seconds on the day Open Library was struggling.

### 6.1 Genre vocabulary — BISAC subset (decision 5)

Providers return BISAC codes, free-text categories, and folksonomy tags interchangeably. We normalize onto a **fixed ~40-term subset of BISAC subject headings**, stored in `genres.tsv` in the repo and loaded into the `genre` table at startup.

- Each row is `bisac_code` + display name, e.g. `FIC028000` → Science Fiction, `FIC022000` → Mystery & Detective, `FIC009000` → Fantasy
- Take BISAC's major sections and one level of subcategory, no deeper — `FICTION / Science Fiction` not `FICTION / Science Fiction / Space Opera`. Deeper is unbrowsable at library scale
- A `genre_map.tsv` maps provider strings (`"Science fiction"`, `"sf"`, `"Sci-Fi & Fantasy"`) to codes. Unmapped strings are logged, not guessed, so the map can be extended from real data
- Raw provider strings are also kept as free tags, so nothing is lost

Without this the Genres browse mode is unusable.

### 6.2 "Also published" — novels and novellas only (decision 6)

Open Library's works list is long and noisy: anthologies, omnibus editions, single short stories, forewords, and non-fiction all appear alongside the novels. Default view is `work_type IN ('novel','novella')`.

Classification is heuristic and will not be perfect, so it is transparent about that:

- Title patterns exclude collections — `Complete Works`, `Collected Stories`, `Selected …`, `… Omnibus`, `… and Other Stories`, `The … Trilogy`
- Open Library subjects and Google Books categories flag non-fiction
- Page count, where known, separates novella from novel
- Anything unclassified stays `unknown` — **hidden by default, shown under `show all`**, never silently dropped

The filter is a display filter; every fetched work is stored, so reclassification never needs a re-fetch.

## 7. Sorting

Sort keys are **computed once at ingest and stored as indexed columns**, so browsing is a plain `ORDER BY` and stays fast at 10k+ books. No string mangling at render time.

- `title_sort` — lowercased, leading article stripped (locale-aware: `The`/`A`/`An`, `De`/`Het`/`Een`, `Le`/`La`/`Les`), NFKD, diacritics folded
- `author.sort_name` — `Last, First`, particles handled (`Le Guin`, `van der Meer`, `de Beauvoir`), suffixes out of sort position
- `series_index` is REAL, so 2.5 sorts between 2 and 3; unnumbered entries sort last
- Years browse uses `first_published`, never `edition_date` — a 2019 printing of a 1969 novel belongs in the sixties

## 8. Milestones

| | Milestone | Size |
| --- | --- | --- |
| ✅ M0 | Service skeleton on :8770 + plist; FastAPI serves a static page rendering an EPUB via foliate-js; CSP containment proven by test | done |
| ✅ M1 | Reader: pagination, TOC, themes, font controls, CFI position persisted server-side | done |
| ✅ M2 | Library: scan, hash, SQLite index, cover extraction, Browse tab with modes + letter bar | done |
| ✅ M3 | Search (FTS5), sorting, series, shelves | done |
| ✅ M4 | Book detail: synopsis, About the author, Also published | done |
| ✅ M5 | Enrichment: providers, scoring, BISAC genre map, review queue | done |
| ✅ M5.1 | Library repair: author dedupe, title-as-author, titles from bibliographies, rescan safety | done |
| ✅ M6 | Annotations (highlights, notes, panel); MOBI/AZW3/PRC | done |
| ✅ M7 | PDF: metadata from the filename first, pdf.js under the existing CSP | done |
| ✅ M8 | Unpacked EPUB directories; book-level dedupe; CBZ | done |
| 🔄 M9 | Series from filenames ✅; Frank Herbert's Dune and the rest still need a provider | in progress |

M0 existed to de-risk the two things that could have invalidated the plan: the engine rendering correctly, and CSP not breaking it. **Both are now settled facts** — see §3.1.

### 8.1 M6 part 1: annotations, server side

Highlights and notes are **server-side and shared**, exactly like reading position (decision 7): a passage marked on the iPad is marked on the Mac. What stays per device is how the page looks, not what is on it.

```
GET    /api/books/{id}/annotations
POST   /api/books/{id}/annotations     {cfi, text, note?, color?, fraction}
PATCH  /api/annotations/{id}           {note?, color?}
DELETE /api/annotations/{id}
```

Three rules the tests pin down:

- **The quoted words are required**, not optional decoration. `annotation.text` duplicates what the CFI points at on purpose (§5), so a highlight survives a book being replaced by a better scan. Refusing an empty one is what makes that guarantee real.
- **A note and a colour may change; the words and the place may not.** They are what the highlight *is*, and editing them would make it a different highlight wearing the same id. `PATCH` accepts only `note` and `color`.
- **A fraction is stored beside the CFI**, because CFIs do not sort as strings and the highlights panel lists them in reading order. It is clamped rather than refused — an odd number should not cost somebody their highlight.

A colour outside the drawable set is a 422: a value nothing can render is not a colour. And annotations outlive a missing file — a book whose file has vanished keeps its row, so `GET` uses a laxer lookup than the one guarding `/file`.

### 8.2 M6 part 2: highlighting in the reader

Selecting text offers **Highlight, Note, Copy** and a row of colours; the right panel gains a **Highlights** tab beside Contents and Display. A mark is drawn immediately, listed in the panel, and still there when the book is opened again on another device.

**`static/reader.js` remains the only importer of foliate-js.** It gained `mark` / `unmark` / `drawAll` / `goToMark` / `selection`, and it answers foliate's `draw-annotation` event by saying what a mark should look like — the engine asks rather than decides, so the palette lives in one place and matches the set the server accepts.

Three things the shape of the DOM forced:

- **The offer belongs to the page, not the book.** A document cannot draw outside itself, and the popover has to sit above the reader — so `selection()` converts the range's rectangle from frame coordinates to page ones, and the menu is positioned above the words unless there is no room, in which case below. A menu covering the words you just chose is no use.
- **Selection has to be watched per section.** foliate replaces the document when you move between sections, so every `load` attaches its own listeners.
- **A section gets a fresh overlay when it loads**, so everything drawn on it must be drawn again — `#redraw()` runs on every load, and marks belonging to other sections fail quietly.

> **Rule: an annotation test clears the book's marks first.** Marks are shared server-side like position, but unlike position they accumulate *within* a spec file as well as across it. Position could only be handled by reserving a book nothing else opens (§10.2); marks can be deleted, so `beforeEach` starts each test from a clean book. `annotate-me.epub` is still reserved — nothing else may open it.

The note flow asks for the text **before** making the mark, so a cancelled note leaves no orphan highlight behind. It is a composer, not `window.prompt`: prompt blocks the page, cannot be styled, and on iOS reads as the browser asking rather than the app. Enter saves, Shift+Enter is a new line, Escape gives up, and a selection settling while a note is open does not snatch it away. Notes can also be written or changed afterwards from the panel, where the note is rather than somewhere else on the screen.

> **Emptying a note is not the same as not mentioning one.** `PATCH` applies only the fields the request actually names — `{"note": null}` clears it, `{}` changes nothing. Without that distinction a colour change would quietly erase a note, since both arrive as `None`. The store refuses any field outside `note` and `color` rather than ignoring it.

### 8.3 M6 part 3: MOBI, AZW3 and PRC

**The reader half really was free.** foliate-js sniffs the container
rather than trusting the extension, so nothing in `static/` changed at
all — one line in `BookLibrary.SUFFIXES` made 89 books openable.

The library half was not. Their metadata lives in a PalmDB record, not
an OPF, so `epub_mobi.py` reads it: a 78-byte database header, a table
of record offsets, then record 0 carrying a PalmDOC header, a MOBI
header and an **EXTH** block of typed entries — author, description,
subjects, ISBN, published date, cover.

**Measured on the real library: 89 files (52 `.prc`, 34 `.mobi`, 3
`.azw3`), all 89 read, none failed.** 89 with a title, 72 with a cover,
75 with a language, 63 with an author — the missing authors are the
same filing problem §6.3 fixes for EPUBs.

Two things the format itself forced:

- **Every offset in the MOBI documentation is counted from the start of
  record 0, not from the `MOBI` magic 16 bytes into it.** Reading them
  the other way still produced correct titles — because most files
  repeat the title in the EXTH block, which masked it — but every
  locale came back as nonsense: 209 and 206 are not languages. Reading
  them where foliate reads them took languages from 6 to **75**, and
  three books that claim Thai really are the Pali canon printed in
  Bangkok.
- **Text records come before resources.** A reader takes records
  1..`numTextRecords` as the book, so a cover filed in front of the text
  *is* the book as far as it knows — which is exactly what foliate
  rendered when the test fixture had them the wrong way round.

Older files declare a short header that stops before the locale field;
reading past its end is what produced the nonsense, so the declared
length is checked first. A number nobody mapped is left as nothing
rather than guessed at — the same rule the genre map follows.

### 8.4 M7: PDF

**The CSP needed no change at all.** pdf.js runs its parser in a Web
Worker, which nothing else here does — and `worker-src` falls back to
`default-src 'self'`, which already allows a same-origin worker. There
is a test that watches the console for refusals while a PDF opens, so
if that ever stops being true it is a failing test rather than a blank
page.

**The filename leads, and the embedded metadata only gets a say when it
earns one.** That is the opposite of EPUB and MOBI, and it is what the
real library says. Of 585 PDFs, 406 carry a `/Title`:

| | |
| --- | --- |
| (empty) | 151 |
| `Local Disk` | 76 |
| `Book Cover` / `BOOK COVER` | 39 |
| `Contents` | 5 |

— 129 distinct values across 585 files. Meanwhile the filenames follow
the same convention the repair passes already understand:
`Anthony, Piers - Mute.pdf`.

**The worst failure is not a missing title but somebody else's.** These
files were assembled from others and kept the wrong name inside:
`Jody Lynn Nye - School Of Light` on a file called
`Nye, Jody Lynn - Dreamland`; `Also by Neil Gaiman` on `Stardust`. So an
embedded title must **agree with the filename** to be believed, compared
against the filename's *last* segment rather than the whole thing —
sharing an author is not sharing a book. That took accepted titles from
125 to 40, and the 40 are right. Where the filename says nothing
(`a.pdf`), anything inside the file is an improvement, so it wins.

A creation date fills `edition_date` and never `first_published`: a 2009
scan of a 1965 novel is not a 2009 book, and §5 exists because
conflating those is the commonest metadata error there is.

**No cover is extracted.** Rasterising the first page needs a PDF
renderer, which is a heavy dependency for a proof-of-concept reader;
the browse list falls back to a lettered tile as it does for any book
without one. Only the Info dictionary is read, by scanning the first
and last 2 MB rather than parsing — reading 633 MB of scanned comic to
find a title would not be a kindness. A PDF that hides its Info dict in
an object stream is simply one with no usable metadata, which is where
161 of them already are.

**Measured: 585 files read in 1.3 s, one failure** — a file with a
`.pdf` extension that is not a PDF, reported per book, scan unaffected.
The library is now 2,008 books: 1,352 EPUB, 567 PDF, 52 PRC, 34 MOBI,
3 AZW3.

### 8.5 M8: books that were never zipped

265 books in the library are directories rather than files — iTunes exports, and anything somebody unzipped and never put back. They had been invisible since M2, because the scan listed files only. That is 265 books you own and cannot see.

**Zipped on demand.** foliate reads a zip, so a book stored loose is packed on the way out, mimetype first and uncompressed as the container spec asks. Everything downstream — the reader, CFI positions, highlights, the loading bar — carries on knowing nothing about it. A 500 kB book takes a few milliseconds to pack, and the alternative, serving the tree file by file for foliate's directory loader, would have put a second book-fetching path through the CSP and the position code for no gain.

Four things the container change touched:

- **The walk stops at a book.** A directory named `*.epub` holding a `META-INF/container.xml` is a book, so the scan yields it and does not descend; without both tests, `Drive.epub/` becomes a hundred documents, and someone's `Notes.epub/` folder becomes a book.
- **A directory has no size or mtime of its own that means anything.** Editing a file inside one does not move the directory's timestamp, so an unpacked book is stamped by the total size and the most recent change *of its contents* — otherwise every scan would call an edited book unchanged.
- **The hash covers names as well as bytes**, in a fixed order, so moving a file within a book changes its identity as editing one does.
- **The parser reads a loader, not a zip.** `ZipContents` and `FolderContents` answer `names()` and `read()`; everything above them is unchanged, and a book cannot name a file outside itself.

**Measured: 264 new books in 4.9 s, no failures.** The library is now 2,271 books.

| # | Decision | Consequence |
| --- | --- | --- |
| 1 | **Same stack as dlna-gateway** | FastAPI + hypercorn + SQLite/FTS5 + vanilla static SPA. No framework, no build step, no desktop shell. |
| 2 | **Separate service** | Own port (8770), own plist (`com.roha.epub-reader`), own `books.db`. No coupling to dlna-gateway beyond shared conventions and TLS cert host. |
| 3 | **No Calibre interaction at all** | No `metadata.db` import, no `ebook-convert`, no library-folder reading. `origin` enum is `file/provider/user` only. All metadata is built here from providers and embedded file data. |
| 4 | **Auto-apply at 0.90** | Below it, candidates go to the review queue rather than being written. |
| 5 | **Standard genre subset** | ~40-term BISAC subset in `genres.tsv`, with `genre_map.tsv` translating provider strings. Unmapped values logged, not guessed. |
| 6 | **Novels and novellas only** | `author_work.work_type` filters the Also published list; everything fetched is stored, `unknown` is hidden but reachable via `show all`. |
| 7 | **Server-side reading position** | One shared position per book across all devices, last write wins. No per-device state, no sync conflict UI. |

## 10. Way of working

Non-negotiable, and it applies to every change:

1. **All code is OOP** — classes, server side and in the browser.
2. **Tests first, then make the code true.** Write the failing test, watch it fail, then implement. Never backfill tests.
3. **Use git.** A real commit at every meaningful step.
4. **Always regression testing.** Both suites run before every commit; nothing lands red.
5. **Both layers tested** — pytest for code, Playwright for UI.
6. **`CLAUDE.md` and `design.html` are updated in the same commit** as the code, so the design never drifts from what is built.
7. **One `.env` file is the only configuration source.** Every secret, key, port and path lives there — no hardcoded values, no second config file. `.env` is gitignored; `.env.example` documents every key.

A security test that passes for the wrong reason is worse than none, so
tests that assert a protection holds are verified by **temporarily
breaking the protection and confirming the test fails**. This was done
for the CSP containment test in M0.

### Running it

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # then set LIBRARY_ROOT

./run.sh start                  # scan the library, then start in the background
./run.sh restart                # stop, scan, start again
./run.sh stop | status | scan
./run.sh start --foreground     # run in this terminal, Ctrl-C to quit

.venv/bin/python -m pytest      # code tests
npx playwright test             # UI tests (regenerates its own fixture library)
```

**`start` and `restart` always scan the library first**, so books dropped
into `LIBRARY_ROOT` are picked up without a separate step. The scan also
runs inside the server at startup, so a launchd-managed start behaves the
same as a scripted one.

`run.sh` is launchd-aware: when `com.roha.epub-reader` is loaded it drives
`launchctl` instead of a pidfile, so the script and the LaunchAgent can
never both bind :8770.

### Library scanning

`epub_library.py` answers one question: what appeared, changed or vanished
since last time?

- Detection is **stat-based** — name, size, mtime — because this runs on
  every start and hashing a large library each time would be wasteful.
- State lives in `library-manifest.json` beside the database. It is
  internal state, not configuration, so it gets no `.env` key.
- A corrupt manifest is treated as absent, not fatal: the worst case is
  one scan reporting everything as new, which is recoverable — refusing
  to start is not.
- **M2 replaces the JSON with the `book` table and takes over identity via
  `content_hash`, keeping the `LibraryScanner` interface.**

### Layout

```
epub_gateway.py     entry point — GatewayRunner, GatewayCLI, LoggingSetup
epub_config.py      Config, ConfigError — the only reader of .env
epub_security.py    ContentSecurityPolicy, SecurityHeaders, middleware
epub_server.py      EpubGateway, BookLibrary — routes and read-only library
epub_library.py     LibraryScanner, ScanManifest, ScanReport, BookFile
epub_names.py       PersonName, title_sort_key — sort keys, computed at ingest
epub_metadata.py    EpubMetadataReader, BookMetadata, MetadataError
epub_mobi.py        MobiMetadataReader — PalmDB/EXTH, for MOBI/AZW3/PRC
epub_pdf.py         PdfMetadataReader — the Info dict, when it is worth having
epub_cbz.py         CbzMetadataReader — comics: a filename and a first page
epub_series.py      SeriesFromNames — series read from filenames
epub_index.py       LibraryIndexer, IndexReport, CoverStore
epub_browse.py      BrowseQueries — all browse SQL, indexed ORDER BY only
epub_search.py      SearchIndex, SearchQueries — FTS5, grouped results
epub_shelves.py     ShelfStore — stored shelves and smart ones
epub_providers.py   OpenLibraryProvider, WorkClassifier — injected fetcher
epub_enrich.py      AuthorEnricher — provider results, honouring provenance
epub_match.py       MatchScorer, Candidate, normalise_title, search_title
epub_book_enrich.py BookEnricher — apply, queue, accept, reject
epub_genres.py      GenreVocabulary — the BISAC subset and the map
epub_maintenance.py LibraryRepair — the three passes, in dependency order
epub_duplicates.py  DuplicateFinder — one row per work, nothing deleted
epub_refresh.py     AuthorRefresh — the weekly round-robin, oldest first
data/genres.tsv     ~40 BISAC terms; data/genre_map.tsv translates providers
static/review.js    ReviewPanel — why it scored that, and what would change
static/browse.js    Browser — mode strip, letter bar, drill-down
static/detail.js    BookDetail — the book-detail screen
epub_db.py          Database, Migration, MigrationRunner — forward-only
epub_reading.py     ReadingState, ReadingStateStore — position, server-side
static/settings.js  ReaderSettings — display prefs, per device
static/annotations.js Annotations — the selection offer and the marks panel
epub_annotations.py AnnotationStore — highlights and notes, shared
static/toc.js       TableOfContents
run.sh              start / stop / restart / status / scan / repair / refresh
com.roha.epub-reader.refresh.plist   the weekly author refresh
static/reader.js    ReaderEngine — THE ONLY importer of foliate-js
static/app.js       BooksApp, BookList
tests/              pytest + epub_fixtures.EpubBuilder
tests/frontend/     Playwright specs
tools/              make_test_library.py
```

### 10.2 Test isolation

Reading position is **shared server-side by design** (decision 7), and the
UI suite runs in parallel against one server and one database. Two tests
touching the same book therefore overwrite each other's position — which
produced a flake that passed in isolation and failed in the full run.

> **Rule: the UI suite runs serially (`workers: 1`).** One server, one
> database, and position shared across devices by design means parallel
> tests race over real application state. Three serial runs are clean at
> ~14s; parallel runs failed a different test each time.

> **Rule: a position test uses a book no other test OPENS.** Opening a
> book writes reading state — it is not enough to avoid asserting on it.
> `position-a.epub` and `position-b.epub` exist for the two position
> tests, and `untouched.epub` for the "no progress" assertion. Nothing
> else may open any of them.

### 10.13 The review queue, and what providers actually give you

Decision 4's bands are enforced in `epub_book_enrich.py`: at or above
0.90 we write, 0.60–0.90 waits for a human, below that the book is left
alone and nothing is kept.

- **The provenance rule is enforced on every write**, automatic or
  accepted-in-review: a field whose origin is `user`, or which is locked,
  is never touched. `title` is deliberately *not* in the writable set —
  the file's title is what you see in your own folders, and changing it
  silently would be disorienting.
- **A score without a reason is not reviewable**, so each queued
  candidate stores the parts it was made of and exactly which fields
  would change, old beside new.
- **Accept-all belongs to the queue, not to an item.** It was first built
  inside the item pane, which meant opening a book to accept everything.

**What real providers gave us**, enriching 25 random books:

| | |
| --- | --- |
| applied automatically | 12 |
| queued for review | 6 |
| no candidate at all | 7 |
| fields actually written | only `first_published` and `language` |

Two things that measurement exposed:

- **Google Books answers 429 to every unauthenticated request.** It
  contributed nothing and logged 25 identical failures. A provider that
  rate-limits us is now dropped for the rest of the run, and
  `GOOGLE_BOOKS_API_KEY` is what makes it usable in bulk.
- **Open Library's search results are thin** — title, author, year, ISBN,
  language, cover, but *no synopsis or publisher*. So auto-apply writes
  little. Fetching each matched work's own record would fix it at the
  cost of a second request per book; that is the obvious next
  improvement, and it is recorded here rather than left looking finished.

**Genre coverage** on the real library: the map reaches **57% of
book-genre rows** (up from 45% once hyphenated paths like
`Fantasy - General` were walked, not just slashed ones). The rest is
genuine junk — `Rand al'Thor (Fictitious character)`, `wierook32` — which
is exactly what "logged, never guessed" is for.

### 10.12 Matching: scored by evidence, calibrated on real books

The hard part of enrichment is not calling APIs. Most EPUBs have no
ISBN, so matching is fuzzy against providers that confidently return the
wrong edition — so the weights, thresholds and normalisation live in one
small, heavily tested place (`epub_match.py`).

**Two flaws the tests caught before any of it ran:**

- **Weights must not be redistributed** over the fields we happen to
  know. Doing so made a title-only match score 1.0 and auto-apply —
  exactly the case that returns the wrong edition. An unknown field now
  contributes nothing, so a thin match scores thinly: corroboration has
  to be earned.
- **A conflicting ISBN is evidence**, not something to ignore. Different
  ISBNs mean at least a different edition, so the score is discounted
  enough to keep it out of auto-apply while still allowing review.

**Calibrated against the real library, not assumed.** A first run over 18
random books returned *no candidates at all for 33% of them* — every one
had filename scaffolding in its title: `Elliott, Kate - Crown Of Stars 01
- King's Dragon`, `05 The Wounded Land`, `Anne McCaffrey - Tsw 5 City Who
Fought`. What we **send** a provider is therefore not what we **compare**
against: `search_title()` strips author, series and list-number prefixes,
while `normalise_title()` stays conservative. That moved no-candidates
from 33% to 22% and auto-apply from 38% to 44%.

A hyphen becomes a space rather than nothing, because `Gilden-Fire` →
`gildenfire` is a token no provider will ever match.

### 10.11 Author enrichment, and what real bibliographies look like

Open Library fills *About the author* and *Also published*. **The fetcher
is injected**, so no test touches the network and a cache or rate limiter
can wrap it later without changing the provider.

- **A near-miss is never guessed at.** An author matches only when the
  folded sort names are identical; anything else is left for a human,
  because the wrong author's bibliography is worse than none.
- **A hand-written bio is never overwritten** — `bio_source = 'user'`
  survives every re-run. That is the provenance rule doing its job.
- **Failure is reported, never raised.** A provider being unreachable
  returns `matched: false` with the reason on screen.
- **`author_work` was designed in the docs but never migrated.** The
  tests found it instantly. Its `UNIQUE(author_id, title, year)` would
  not have worked either: SQLite treats NULL years as distinct, so every
  undated work would duplicate on the second run. The provider returns
  the whole bibliography each time, so it replaces rather than merges.

**What real data looks like** (Le Guin, live): 200 works returned, of
which 22 were duplicate titles — Open Library lists editions and
translations under one author — and bind-ups appear as slash-joined
titles (*A Wizard of Earthsea/The Tombs of Atuan*). After collapsing
duplicates and treating slash-joined titles as omnibuses: 178 works, 141
novels. **Translations remain** (*A Mão Esquerda da Escuridão*), because
the works list carries no language. That is M5's problem, not a bug here.

### 10.10 Choosing a book is not opening it

Per §4.1 the centre pane shows **either** the book's details **or** the
reader. Choosing a book now shows it — cover, series and number, author,
genre, year, synopsis, author, and what else they wrote — and reading
starts when you press **Read**. Navigation is a hierarchy: library →
book → reading, and back retraces it one level at a time rather than
dumping you at the library from the middle of a chapter.

Empty panels say what is missing rather than showing nothing: *"Nothing
about Stephen R. Donaldson has been looked up yet."*

> **Rule: the UI suite never reuses a running server** (`reuseExistingServer:
> false`). A stale server from an earlier code version served the whole
> suite once and produced a 500 that had nothing to do with the code
> under test. Free the port instead.

### 10.9 Shelves, sorting, and a TLS trap

**Two kinds of shelf, and the distinction is the design.** Stored shelves
are lists the reader maintains. Smart shelves ("Reading now", "Finished")
are derived from reading state — a view of a fact, not a list — so they
have no rows and there is deliberately **no route to put a book on one**.

Sorting gained `opened` and `progress`, which need a join to
`reading_state`; untouched books sort last via `COALESCE`, not `NULLS
LAST`, so the order is the same on any SQLite. The chosen sort lives with
the other per-device display settings, not on the server: how you like
the list arranged is a fact about you, where you are in a book is a fact
about the book.

> **TLS paths are configuration, not a guess.** Setting
> `TAILSCALE_CERT_HOST` killed the service with a bare `FileNotFoundError`
> from inside hypercorn, because the code assumed certificates live in
> `~/.config/tailscale-certs/`. They are now `TLS_CERT_FILE` /
> `TLS_KEY_FILE` in `.env`, falling back to `<host>.crt` beside the .env
> file — and a missing certificate is reported with what to set, rather
> than improvised or crashed on.

### 10.8 Touch layout: one screen at a time

Three columns on a touch screen leaves the reading pane a couple of
hundred points wide, and the paginator has nothing to lay out into — the
book showed nothing at all on iOS. Choosing a book now gives the reader
the whole screen, with a back arrow to the library, switched by
`body[data-view]`.

**The breakpoint comes from the devices this is read on, not from a
convention:** an **iPad 9th generation is 810 × 1080 points**, so
*landscape at 1080* has to be covered too, and an **iPhone 15** is
393 × 852. `max-width: 1180px` clears both.

**The panels had to come with it, and did not.** Giving the reader the whole screen took away the right-hand column, and with it Contents, Highlights and — the one actually missed — the typography controls. An iPad is read at arm's length and the default size is small, so "make the text bigger" was unreachable on the device that needed it most.

The reader now carries an **Aa** button that brings the *same* panels in over the page, as a sheet from the right. **The sheet opposes the page it covers** — dark over a light or sepia book, light over a dark or black one — because reading on black with a black panel is how a panel disappears, and the point of it is to be found. Four contrast tests hold it above WCAG AA on all four reading themes.

> **Name a token that exists.** The first version of this sheet asked for `var(--panel)` and `var(--line)`, which are not in the palette — the real names are `--surface`, `--border`, `--ink`. An unknown custom property resolves to nothing, so the background silently vanished and the book showed straight through the panel. CSS fails quietly; a test that reads the *computed* background and asserts it is opaque does not. The same panels, not a second copy: two sets of controls for one setting is how they end up disagreeing. Closing is a 44 pt target inside the safe area, and leaving the book takes its panels with it.

> **Rule: the Mac layout does not change.** Every normal Mac window
> (1280 and up) keeps the three-column layout, and tests pin it at 1280,
> 1440 and 1728 — three columns, no back arrow, library exactly 330 px.

Also: `height: 100dvh`, because 100% of a viewport that moves as Safari's
toolbars slide is not a height; a 44 pt tap target inside
`env(safe-area-inset-*)`; the search box gives way to the book while
reading; and on phones the title is clamped to one line — it wrapped to
three and pushed the header to ~90 pt.

**A dom key collision broke the entire app** while building this: a new
`back:` entry silently overwrote the browse back-link, so `Browser.start()`
threw on *every* screen size. It surfaced only because a phone test
captured `pageerror`. Distinct names, and watch for duplicate keys.

### 10.7 The loading indicator, and another WebKit lesson

The book's own cover, turning, above a bar and a label. Two phases,
because only one can be measured honestly:

- **downloading** — a real percentage, from `Content-Length` against
  bytes read off the stream. The book is fetched by the app rather than
  by foliate so the bytes can be counted, then handed over as a **File**
  (not a Blob: foliate sniffs format partly by `file.name`).
- **preparing** — foliate exposes no progress while it parses and lays
  out, and a 1.2 MB single-section book spends 6-8 s there. The bar goes
  indeterminate and the label says what is happening, rather than
  inventing a number that would be wrong.

> **WebKit will not paint an `<img>` inside a `transform-style:
> preserve-3d` context.** The cover rendered as a flat navy rectangle in
> Safari while Chromium was perfect, with identical markup. Neither a
> wrapper element nor dropping `backface-visibility` helped; only
> removing `preserve-3d` did. The book now turns within ±58° using
> perspective alone, which needs no back face and keeps the cover
> visible throughout. A test samples the image's actual pixels rather
> than merely asserting the element exists.

Also fixed while building it: the overlay covered the whole window
because `#reader` was not positioned, so the library was unusable
during a load.

### 10.6 Books that are one enormous section

Some EPUBs hold the whole book in a single document. CSS multi-column
lays that out as one strip whose width scales with the content: a 1.18 MB
section measured **8,120,880 px wide**, which neither Chromium nor WebKit
can paint — you got the cover and then a blank pane. 252 kB paginates
correctly, so the failure is not about file size but section size.

- `ReaderEngine` falls back to **scrolled flow** when any section exceeds
  400 kB, and says so in the status line. Scrolled flow skips
  columnisation entirely and the same book renders in full.
- **The override belongs to the book, not the reader.** It is derived on
  every application from the requested settings rather than written into
  them, or opening a huge book would leave the next one scrolled too.
- **Books now open where the words start.** foliate's `goToTextStart()`
  uses the bodymatter landmark and falls back to the first linear
  section, but many EPUBs have no landmarks and mark the cover linear —
  so it lands on the cover, which is a small image on a large empty pane
  and reads as a broken reader. Front matter is tiny next to a chapter,
  so the first content section is found by size — as a **fraction of the
  book's own median section**, because a fixed threshold skipped every
  section of a book whose chapters were 1 kB.

### 10.5 Safari is the browser that matters — test it

**The app was completely broken in Safari and every test passed.** Books
stuck on "Opening…" forever. Chromium was fine, and Chromium was all the
UI suite ran against.

Cause: **WebKit propagates a page's CSP into `blob:` documents.** Every
book section renders in a `blob:` iframe, so `frame-ancestors 'none'`
made the book's own frame refuse to load — *"Refused to load blob:…
because it does not appear in the frame-ancestors directive"* — followed
by `null is not an object (evaluating 'doc.head')`.

- `frame-ancestors` is now `'self'`. Framing by other origins is still
  refused, and `X-Frame-Options: DENY` carries the same protection while
  **not** being inherited by `blob:` documents.
- `style-src` now allows `blob:`. Real books link their stylesheet, and
  foliate serves every resource as a `blob:` URL, so omitting it silently
  stripped each book's typography. Stylesheets cannot execute script,
  which is what keeps `script-src 'self'` meaningful.
- The hostile-EPUB containment test passes **in both engines** after the
  relaxation — checked, not assumed.

> **Rule: the UI suite runs against Chromium AND WebKit.** This is read
> in Safari on a Mac, an iPhone and an iPad, and foliate-js is developed
> against WebKitGTK. Chromium-only testing is how a total failure shipped
> unnoticed.

### 10.4 Two bugs the running app exposed

Both were found by running the service against the real library, not by
tests — worth recording because neither could surface at fixture scale.

- **Books are addressed by index id, never by path.** 44% of the library
  lives in subfolders, so a book's relative path contains slashes, which
  cannot be a single path segment — every one of those books 404'd when
  clicked. Ids also remove the `/position` routing ambiguity and the
  traversal surface entirely. As a bonus the listing no longer re-hashes
  files: the index already stored each content hash.
- **A new FTS table does not backfill itself.** After migration every
  book was "unchanged", so nothing populated `book_fts` and search found
  authors but zero books. `LibraryIndexer` now catches up any book with
  metadata and no search row ("1326 newly searchable" on the first run).
- **One pidfile per configuration.** `run.sh --env` shared a single
  pidfile, so a test config could see — and stop — the live service.

### 10.3 What M2 established

Measured on the real library: **1,326 books indexed in 7.7 s**, re-run
0.3 s hashing 2 files, **zero failures**, 99% covers extracted, 606
authors, 34 series, 258 genres.

- **The old `LibraryScanner` is gone.** Its JSON manifest was superseded
  by the `book` table, which tracks the same facts and more. `--scan` and
  startup both run `LibraryIndexer` now.
- **Incremental by size+mtime.** Unchanged books are never re-hashed, and
  there is a test asserting exactly that (`indexer.hashed == 0`).
- **Three behaviours the real data forced, each with a test:**
  identical copies filed twice are one book; an edited file updates its
  row rather than orphaning it; and duplicates no longer trade paths on
  every run (a no-op scan used to report "2 moved").
- **`[hidden]` needs `display:none!important`.** A class-level
  `display:flex` silently beats the UA stylesheet, so panels marked
  hidden stayed on screen. Caught by a test, not by eye.

### 10.1 What M1 established

- **Position is keyed by content hash, not filename.** Renaming a book keeps
  your place; there is a test for exactly that. M2 introduces `book.id` and
  can add the foreign key.
- **Position is server-side and shared; display settings are per device.**
  Where you are in a book is a fact about the book. How large you want the
  type is a fact about the screen you are reading on, so it lives in
  `localStorage`. The UI says this in as many words.
- **Saves are debounced (800 ms)** because `relocate` fires on every page
  turn, and flushed on `pagehide` so closing a tab mid-chapter loses nothing.
- **A stale CFI must never strand the reader.** `ReaderEngine.open()` falls
  back to the first page if `goTo(cfi)` throws — a book replaced on disk
  would otherwise open blank.
- **foliate REPLACES the book iframe when changing section.** A `Frame`
  reference captured before navigating goes stale, which cost a debugging
  round. `tests/frontend/helpers.js` re-resolves the frame on every poll;
  use those helpers rather than holding a reference.
- **Reopening a finished book does not un-finish it.** The upsert preserves
  `finished`/`abandoned` status; only an explicit status change clears it.

## 11. Conventions

- Match dlna-gateway: vanilla JS, no framework, no build step, one `app.css` with `:root` tokens, one `app.js`
- Palette and type inherit dlna-gateway's tokens (`--bg:#0A1526`, `--amber:#FFC24A`, Syne + DM Mono) so the two apps read as a family — **except inside the reader**, where the book's page gets a neutral reading theme and the app chrome gets out of the way
- foliate-js is a pinned submodule, never edited in place; differences go in `reader.js`
- All SQLite access server-side; the frontend only sees JSON
- Migrations forward-only and numbered
- `Intl.Collator` / `Intl.Segmenter` over hand-rolled string logic — the library is multilingual
- Never modify the user's book files — read-only, always
- Every user-facing string is written from the reader's point of view, not the system's
