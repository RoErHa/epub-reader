# epub-reader

A browser-based e-book library and reader, built in the shape of `dlna-gateway-2.0`: **Browse · Search · Read**.

EPUB first, MOBI/AZW3 shortly after, PDF eventually.

**Status: M0–M12 complete except M9's provider half. Running against the real library: 2,364 books on the shelves in seven formats (2,374 indexed — ten comics libarchive refuses are reported as failed, not shelved), 288 duplicate copies folded away, 235 series, 91% of books carrying cover art.** All seven decisions settled (§9).

The reader has contents, themes, typography controls, server-side position, and highlights with notes — reachable on a phone and an iPad as well as a Mac. The library reads **EPUB, MOBI, AZW3, PRC, PDF, CBZ and CBR**, including EPUBs stored as directories and comics packed with RAR. Browsing is by author, title, series, genre, **format**, year or shelf. Shelves are made, filled and thrown away from the browse list, and a book can be marked read there or on its detail page.

**Eleven maintenance passes** tidy what thirty years of other people's filing left behind — all dry-run by default, none of them touching a file, and the ones that cost nothing now run on every scan, so **dropping a book into `LIBRARY_ROOT` and restarting really is the whole workflow** (§8.16).

**Next: §12.1 and §12.2** — the names that are clear to a person and not to the code. §12 lists everything open, in the order it is worth doing.

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
| launchd label | `com.example.epub-reader`, or whatever `LAUNCHD_LABEL` says in `.env` — the label is configuration, not a constant, so a public repository names nobody |
| Port | **8770** — clear of dlna-gateway's 8765 and localfs's 8200 |
| Database | `books.db` in the project directory |
| Entry point | `epub_gateway.py` |
| TLS | same Tailscale cert host as dlna-gateway, read from `.env` |

Carried over unchanged: one `app.css` with `:root` tokens, one `app.js` of plain functions, launchd plist, `.env` config, PWA manifest + service worker, rotating `gateway.log`.

## 2.0 Books nobody can open

**50 books in the library are Apple Books purchases**, encrypted with FairPlay: every document inside is ciphertext and the key lives in an Apple ID. Nothing here or anywhere else can open them, so they are **excluded at scan time** rather than listed as titles that fail.

> **Encrypted fonts are not DRM.** `META-INF/encryption.xml` is also how fonts are obfuscated, which is ordinary and readable — 8 books here do it, the Earthsea novels among them. The difference is *what* is encrypted: documents mean DRM, fonts mean typography. Excluding on the presence of the file alone would have lost 8 books that work perfectly.

A locked book is **not a failure** — nothing is wrong with the file or with us — so it is counted separately and says so: `50 locked by their shop`. It is recognised before the book is hashed or stored, or every scan would hash fifty books it is about to throw away.

Two references to a `book` row have **no cascade**, and both took a scan down in turn once books started being deleted: `book.duplicate_of`, where a hidden copy points at the copy that was kept, and `author_work.book_id`, where "Also published" links a work to the copy you own. Neither should vanish with the book — the hidden copy comes back into view, and the work stays on its author's shelf as one you no longer own.

## 2.1 Scope: what the reader can open

**EPUB, MOBI, AZW3, PRC, PDF, CBZ and CBR.** RAR comics are read through
libarchive, which macOS ships as `/usr/bin/bsdtar`, and repacked as a zip
on the way out (§8.15). Two files named `.cbz` turned out to
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
library is 2,381 books now, and every one of those formats is read —
§2.1, §8.15.)

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
| Formats | EPUB / MOBI / PDF / Comics → books of that kind |
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
| Say who wrote a book | `PUT /api/books/{id}/authors` | `epub_credits.py` |
| Say where it sits in a series | `PUT /api/books/{id}/series` | `epub_credits.py` |
| Remove books whose files are gone | `./run.sh forget [--apply]` | `epub_forget.py` |
| Series from filenames | `./run.sh series [--apply]` | `epub_series.py` |
| Author, series and title from one filename | `./run.sh names [--apply]` | `epub_filenames.py` |
| A co-author written into the title | `./run.sh coauthors [--apply]` | `epub_coauthors.py` |
| A title that was cut off | `./run.sh fragments [--apply]` | `epub_fragments.py` |
| What the scanner wrote into the title | `./run.sh tags [--apply]` | `epub_tags.py` |
| All three together | `./run.sh repair [--apply]` | `epub_maintenance.py` |
| Weekly author refresh | `./run.sh refresh [--max N]` | `epub_refresh.py` |
| A cover for books showing a letter | `./run.sh covers [--apply]` | `epub_covers.py` |
| ...and asking a provider for the rest | `./run.sh covers --online [--apply]` | `epub_covers.py` |
| Say a cover is wrong | `DELETE /api/books/{id}/cover` | `epub_server.py` |

**Merging authors is destructive, so the bar is high.** A group merges only when the names are identical once spacing and punctuation are removed, when Open Library resolves both to the same author, when one spelling is *unknown to Open Library* while a near-identical one is well known — the signature of a typo — or when **everything the other name is credited with is in the keeper's bibliography**.

That last rule is the strongest of the four, because it asks what the books *are* rather than how the name is spelled, and it is the only one that settled the Donaldsons: Open Library holds a **second record** for "Stephen Donaldson" (38 works beside the canonical 126), and "Stephan Donaldson" scores 0.87 against "Stephen R. Donaldson" — just under the typo bar. But all four of their books are Thomas Covenant novels, every one in the keeper's works list. One shared title would prove nothing, since two writers can name a book the same, so it asks for all of them. That last rule is what separates `Issac Asimov` from `Isaac Asimov` while leaving `Stephen King` and `Stephen Hawking` alone: both of those are real people, and the provider knows both. Whatever is refused is **reported**, never silently dropped — and `POST /api/authors/merge` is how a reader who knows better overrides the refusal. That is what settled `Anne Rice / Ann Rice`, `Arthur Clarke / Arthur C. Clarke`, and the four spellings of `L. E. Modesitt Jr.`: Open Library holds two records for each of the first two, and none of their books overlap in the library, so nothing could prove it. A hand merge records provenance exactly like an automatic one, so a rescan does not undo it.

The spelling that survives is not simply the one with the most books — that kept `DIANE CAREY` over `Diane Carey` and `Raymond E Feist` over `Raymond E. Feist`. Open Library supplies the spelling, but only when its answer is the same name and better formed: it holds `J.k. Rowling`, which is not an improvement on anything.

**Some files simply lie, and confidently.** `Star Wars - Refugee` carries `Dave Taylor` as its `dc:creator`; the book is by Sean Williams and Shane Dix. `Edge of Victory - Rebirth` carries `David Taylor`; it is Greg Keyes. Nothing automatic reaches that: the file is not damaged, the title is not a filename, and the two wrong names look exactly like one author who needs merging — which is how they surfaced, as the last of three look-alike pairs left for a human. So a reader can say who wrote a book, and it is kept as theirs and **locked**, because the file will go on saying Dave Taylor for ever.

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

> **And the book count itself was the last one to learn it.** `authors` and `genres` were fixed here, `formats()` in §8.15 — while `books` went on counting rows, so the status line said **2,374** where every list showed **2,364**. The ten are the 2003-vintage CBRs libarchive refuses (§8.15): a `book` row, no `metadata` row, and every list joins metadata and cannot show them. They are not lost — `failed` is where they are reported, which is the number that should carry them. Fixed by the same join the other three already had, and `series` gained the `duplicate_of` filter its three siblings had and it did not.

### 6.13 A scan notices a deletion; it does not act on one

A vanished book is marked `missing_since` and kept — §5 says it in as many words, "vanished; row never auto-deleted". That is why it leaves the shelves at once while your place in it survives, and it is what makes an unplugged drive a nuisance rather than a disaster. It is also the wrong answer for a book deleted on purpose, so `./run.sh forget` is a separate act. It says what would be lost before losing it, since highlights and reading positions go with the book.

> **A whole library that has vanished has not been deleted.** It is on a disk that is not mounted. Above half the library missing, forgetting refuses and says why; `--force` insists.

Two references to a `book` row have **no cascade** and both took a scan down in turn once books started being removed: `book.duplicate_of`, where a hidden copy points at the copy that was kept, and `author_work.book_id`, where "Also published" links a work to the copy you own. Neither should vanish with the book.

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

`com.example.epub-reader.refresh.plist` runs that every Sunday at 04:12 — not on the hour and not at midnight, where every other cron job lives. Ten authors is thirty requests, some fifteen seconds of traffic, and a full round of 395 authors in about forty weeks.

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
| 🔄 M9 | Series from filenames ✅; hand-filing what no filename reaches ✅; series from a provider still open | in progress |
| ✅ M10 | Shelves from the browse pane: add and remove a book without opening it | done |
| ✅ M11 | The name is the metadata: author, series and title read from one filename; a co-author freed from a title; the scanner's notes taken off; a truncated title finished by a provider | done |
| ✅ M12 | Comics packed with RAR; browsing by format; a scan that reads what it finds | done |

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
| 2 | **Separate service** | Own port (8770), own plist (`com.example.epub-reader`), own `books.db`. No coupling to dlna-gateway beyond shared conventions and TLS cert host. |
| 3 | **No Calibre interaction at all** | No `metadata.db` import, no `ebook-convert`, no library-folder reading. `origin` enum is `file/provider/user` only. All metadata is built here from providers and embedded file data. |
| 4 | **Auto-apply at 0.90** | Below it, candidates go to the review queue rather than being written. |
| 5 | **Standard genre subset** | ~40-term BISAC subset in `genres.tsv`, with `genre_map.tsv` translating provider strings. Unmapped values logged, not guessed. |
| 6 | **Novels and novellas only** | `author_work.work_type` filters the Also published list; everything fetched is stored, `unknown` is hidden but reachable via `show all`. |
| 7 | **Server-side reading position** | One shared position per book across all devices, last write wins. No per-device state, no sync conflict UI. |

### 8.6 M8: the same book, twice

A library assembled over thirty years holds the same work more than once: a better scan downloaded years later, a MOBI kept beside the EPUB, a copy somebody unzipped. **199 works, 234 spare copies** — and then filing by series found 53 more that titles alone could not see.

> **No file is ever deleted.** Dedupe means **one row per work on screen**: a spare keeps its row and its path, `duplicate_of` records which copy it belongs to, and `POST /api/books/restore-duplicates` shows them all again. Deleting is the reader's business, and the plan names every path.

Which copy to keep, and what decided this library: **the copy you have been reading** (12 works — your place and highlights hang off one copy); **the format that reads best**, `epub > azw3 > mobi > prc > pdf` (30); **the copy with a cover** (155); then fuller metadata, then size.

> **Apply, then audit.** The rule looked right on a dry run, and the one fault in 235 appeared only by asking a different question afterwards: *which hidden copies have filenames that disagree?* Two different Roman histories had been shown as one book, because a scanner wrote the same list of works into both. Two PDFs now need their filenames to agree as well — and agreement is not similarity: `Dune.pdf` and `Dune (retail).pdf` score barely higher than those two did. What tells a duplicate apart is that one name is **the other plus extras**. Auditing again months later found the second fault, which was in the title key itself rather than in the rule — see §8.10.

### 8.7 M8: comics

A CBZ is a zip of images and nothing else. **286 indexed**, and only one in 120 carries a `ComicInfo.xml` — so the filename is the metadata, and these filenames are full of the scanner's notes: `Zootopia 001 (2025) (5 covers) (digital) (Salem-Empire).cbz`.

Everything in brackets is the scanner talking, and the year among it is the **edition**. The issue number is kept because it orders a run: `Shadowman 004` is the fourth Shadowman. That took the library from 39 series to 77. The cover is the first page, sorted, because scanners pad their filenames and `002` really does come before `010`.

Two files named `.cbz` are RAR inside, and the error says which rather than "not a comic archive" — the reader can act on the first.

**A gap this found:** `page_count` had been in the schema since M2 and in no code path at all — `BookMetadata` had no such field, so the page counts M7 read out of PDFs were dropped on the floor.

### 8.8 M9: series, read from the filenames

135 books of 2,323 had a series, every one from a `calibre:series` tag inside the file. §6 said series would come from providers; that was never built. The filenames know, and need no provider: **297 books filed under 80 series**, from `Cussler, Clive - Dirk Pitt 01 - Pacific Vortex`.

> **A series of one is a misread.** "10 is Better Than 01 - Heather Jarman" is a book *called* that, not book 1 of "is Better Than". A name is believed only when **two books by the same author agree on it**, which threw out every false positive the real library produced. An author's own name is refused, and a four-digit number is a year rather than a position.

**Then series found duplicates that titles could not.** Filing revealed 46 positions claimed by two books — `Enders Game 4 - Children of the Mind.epub` beside the same file with the author appended. One author cannot have two fourth books in one series.

What it cannot reach: `Chapterhouse Dune` carries no marker, so Frank Herbert's own novels are still loose, while Brian Herbert's are filed — those files were named by someone who cared. That is what §8.10 is for, and the Dune Chronicles are now filed 1–8 by hand.

### 8.10 M9, continued: saying it yourself

Three books stayed wrong after every automatic pass, and each was wrong in a way no rule could reach: **The Light of All That Falls** is book three of the Licanius Trilogy, its file says nothing, and its filename carries no number. So `PUT /api/books/{id}/series` joins `PUT /api/books/{id}/authors` — and, like it, what you say is `origin = 'user'`, locked, and never touched again by a scan or a pass.

That is the whole of hand-editing today: **who wrote it, and where it sits in a series**. Title, synopsis and genres are still provider-or-file only; `title` is deliberately outside even the enrichment writable set (§10.13), because the file's title is what you see in your own folders.

**Using it found two real faults, both of which had been silently wrong for a while:**

> **A subtitle is part of the title when you are telling two books apart.** `normalise_title` strips everything after a colon — right for *asking* a provider, catastrophic for *comparing*: it made `Dune: House Harkonnen` into `Dune`, and since both authors are "Herbert", **Frank Herbert's novel was hidden behind Brian Herbert's**. Dedupe now compares on a title that keeps its subtitle. Auditing all 289 hidden copies afterwards found **16 wrongly hidden books**, all of the same shape, and restored them.

> **A wrong position is as good as a wrong title for hiding a book.** With that fixed, dedupe still wanted to hide *Interview with the Vampire* behind *The Vampire Lestat*. It was right that two books cannot both be Vampire Chronicles #1 — the *file* was wrong: Lestat's own OPF carries `calibre:series_index = 1`. The rule found a real fault and blamed the wrong book, which is exactly the case hand-filing exists for.

Filed by hand so far: the Licanius Trilogy 1–3, the six original Dune
novels plus Anderson's two, the first two Vampire Chronicles, and — added
2026-08-19 — Geodyssey #1 (*Isle of Women*, whose filename misspells the
series, so nothing could corroborate it) and **Bio of a Space Tyrant
1–4 and 6**.

That last one is a **series split by two filing conventions**, and it is
the shape to watch for. The four EPUBs named their series `Tyrant`, the
PDF was hand-filed as `Bio of a Space Tyrant`, and both names are honest:
nothing automatic chooses between a shorthand and the published name.
The full name wins because that is what the books are called, and the
shorthand's series row is left behind rather than deleted — every listing
joins through to a present book, so an empty series cannot show (§6.8).
#5 is genuinely missing rather than misfiled: *Statesman* is not in the
library, which is the gap-finder working.

### 8.18 No authentication, and the two things that follow from it

This is a personal service for one household, reached over a tailnet. It
has **no authentication** and nothing here uses cookies, which is a
decision rather than an omission — but two consequences of it were being
left to luck.

> **A service with no authentication acts for whoever can reach the
> port.** So `EPUB_BIND` now **defaults to `127.0.0.1`** rather than
> `0.0.0.0`. Widening it is a deliberate edit onto a network you trust,
> with the reason written beside the setting. The old default meant that
> copying `.env.example` and filling in `LIBRARY_ROOT` — the whole setup,
> as the README describes it — published the library to the LAN.

The second is sharper, because no amount of binding carefully fixes it:

> **CORS withholds the reply, never the act.** A cross-site request here
> cannot be *authenticated*, there being nothing to authenticate — and it
> does not need to be. `POST /api/books/forget-missing?apply=true&force=true`
> drops reading positions and highlights. A bodiless POST is a CORS
> "simple request": it is sent with **no preflight**, and the same-origin
> policy hides only the response from the calling page. The request still
> arrives, and is still acted on. Any site open in a browser on the same
> machine could have driven every maintenance endpoint.

`SameOriginPolicy` refuses a state-changing method whose `Origin` is not
ours. Three details it turns on:

- **Reading is never refused.** A `GET` changes nothing, and the
  same-origin policy already stops a foreign page reading the answer.
- **No `Origin` at all is allowed.** curl, `run.sh` and the test suite
  send none, and a browser always sends one on these methods — so absence
  is a non-browser caller, not the case being defended against. Refusing
  it would break every script to guard against a browser.
- **`Origin: null` is refused**, along with everything else that is not
  us: a sandboxed frame splits to an empty netloc and matches no host.

It is registered **inside** the headers middleware — Starlette runs the
last-added outermost — so a 403 still carries the security headers every
other response does.

### 8.17 A shelf label is not a name, and dedupe found out

Running `dedupe` proposed nine works, and **the ninth would have hidden a
book**: *The Serpent's Shadow* behind *The Gates of Sleep*. Both are
Mercedes Lackey's Elemental Masters and both are real novels.

Two separate faults, one on top of the other, and the first was mine.

> **A name that gives only a shelf and a number is not a name.** One of
> those EPUBs calls itself `Lackey, Mercedes - Elemental Masters 02` in
> its own OPF — a series and a position and no book at all — while the
> file on disk is `… - The Gates Of Sleep.epub`. §8.16 had made
> `file_title` the source *because* it does not change between runs, and
> that is right; but preferring it blindly took a shelf label as a title.
> The three names for a book — what the file calls itself, what the
> library shows, and the file on disk — are now tried **in order of
> trust, skipping any that names no book**. Eight titles came right,
> among them `Violets Are Blue` (from `Alex Cross 07`) and `Prince of
> Dogs`.

That was not enough, because dedupe never looked at the title:

> **A wrong position hides a book as surely as a wrong title.** §8.10
> said so about *Interview with the Vampire*, and here it is again: the
> key is `(series, index, author)`, both **files** claim `02`, and one
> author cannot have two second books. The rule found a real fault and
> could only blame the wrong book. *The Serpent's Shadow* is Elemental
> Masters #1; filed by hand, `origin = 'user'`, and the ninth group
> disappeared.

**Applied: 8 spare copies hidden of 8 works** — two Farseer novels and a
Nora Roberts filed twice, and five books held as both an EPUB and a PDF,
where the EPUB is the book and the PDF a picture of one. No file was
deleted; `POST /api/books/restore-duplicates` shows them all again.

The library: **2,364 books on the shelves, 288 hidden, 577 authors, 235 series** — of 2,374 indexed, the ten refused comics being counted as failed rather than shown (§6.8).

**A measurement of my own that was worthless.** I twice reported a count
of "titles that are only a shelf label" — 31, then 45 — from a regex
matching anything ending in a number. It counts `Fahrenheit 451`,
`Collective Hindsight Book 1` and `Archaic 002`, which are titles. The
eight books that actually changed are the measurement; the number never
was.

### 8.16 A book dropped in is a book filed

§6.6 said dropping books into `LIBRARY_ROOT` and restarting was the whole
workflow. It was half true. `REPAIR_ON_SCAN` runs the three passes that
**ask Open Library**, and is off by default for exactly that reason —
while everything built since (§8.13a–d) is local, free, and **ran on
nothing at all**. A book added yesterday browsed as
`Anthony, Piers - Xanth 12 - Man From Mundania`, credited to nobody,
exactly as the first forty-five did.

> **What costs nothing may be done for you; what costs a request may
> not.** `TIDY_ON_SCAN` is **on unless turned off**: these passes read
> names, write to our own database, reach no provider, and keep what the
> file said in `file_title`, so nothing they do is one-way.
> `REPAIR_ON_SCAN` and `fragments` stay opt-in.

`tidy()` runs them in §6.6's order — **authors, then what is asked of an
author, then what depends on nobody**: `names` (author, series, title),
`coauthors`, `tags`. Both `./run.sh scan` and the server's own startup
scan do it, so a launchd start behaves exactly like `./run.sh restart`.

**Proved on the real library**: a PDF dropped in as
`Anthony, Piers - Xanth 12 - Man From Mundania (with Nobody Real).pdf`,
carrying `harry` as its author, came back after one restart as **Man From
Mundania · Piers Anthony, Nobody Real · Xanth #12**.

**Two ways passes can fail to settle, and both were live:**

> **A title a provider CONFIRMED outranks one read off a filename.**
> §8.13c had repaired `The Caterpillars Questi` to *The Caterpillar's
> Question*; the next scan read the broken name again and put the
> fragment back, whereupon `coauthors` saw the bracket it had just
> restored and rewrote the title a second time. Within `provider`,
> `source = 'openlibrary'` is what a catalogue asserts and
> `source = 'filename'` is what we inferred — so the derived one yields.

> **Passes must settle, not take turns.** `names` derives a title from
> `file_title`, which is the file's own words, co-author bracket and all;
> `coauthors` takes the bracket off. So every restart put
> "(with Roberto Fuentes)" back and took it off again — **13 books
> retitled and 13 co-authors "freed" on a scan where nothing had
> changed**. Now both read the same source and each writes only the facts
> it owns: `names` strips the bracket from the title it writes, and
> `coauthors` finds its people in `file_title` and renames only if the
> current title still carries one. Three scans in a row now report
> *nothing new to read*.

The idempotence test that existed used a book with no co-author, which is
why it never saw this. There is one with a co-author now.

**And it finished a job that was on ice:** the two P. N. Elrod books that
§8.13b had to defer — a co-author needs a first author to join — were
credited by `names` and freed by `coauthors` in the same pass, without
anybody running either.

### 8.15 Comics packed with RAR

**295 CBR files, every one invisible.** §2.1 excluded them because "it is
RAR, which nothing in the stack reads" — and that had quietly stopped
being true: **macOS ships `/usr/bin/bsdtar`, which is libarchive, and
libarchive reads RAR.** No new dependency, nothing to install, nothing
proprietary.

> **A comic packed with RAR is packed again as a zip on the way out.**
> §8.5 already does this for an unpacked EPUB directory, and the reader,
> the positions and the highlights carry on knowing nothing about it. A
> 3.5 MB comic is repacked and served in **23 ms**.

Three things the real archives decided, none of which a fixture would
have shown:

> **Listing is cheap and extracting is not.** `-tv` reads headers and
> decompresses nothing. So a cover asks for **one entry**, not for the
> archive — the largest comic here is 315 MB, and holding all of it to
> make a thumbnail is not a trade worth making. Reading all 295 for
> metadata takes **4 seconds**.

> **Extracting to stdout, never to disk.** No temporary directory, no
> permissions, no path that might climb out of one. `-tv` gives every
> entry's size in archive order and `-xO` gives every entry's bytes in
> the same order, so the stream splits exactly — checked byte for byte
> against a 315 MB comic. And if the two ever disagree the split is a
> guess, so it is refused rather than handing a reader half of one page
> stuck to half of the next.

> **libarchive reads most RAR, not all.** **10 of 205 are refused** with
> *"Prefix found: Inappropriate file type or format"*, and all ten are
> one run of 2003-vintage Marvel Premiere archives. A book that cannot be
> unpacked is reported and skipped like any other unreadable file (§3.2);
> it does not take a scan down.

**It reads RAR and cannot write it**, and nor can anything else here, so
there is no `RarBuilder` beside `EpubBuilder` and `CbzBuilder` and there
cannot be. The **process** is the injected boundary: the tests fake what
bsdtar writes, so what they cover is our reading of it — the listing
parser and the size split, which is where a bug would live. The tool
itself is measured against the real library, as the fetcher is (§10.11).

**Measured: 295 files, 205 distinct comics.** The other 90 are
byte-identical copies filed in two places, which §10.3 folds into one
book without being asked. 195 are on the shelves, 10 carry their error.
**Comics went from 286 to 481**, and the library from 2,176 books to
2,381.

> **What the file *is*, never what it is called.** Some `.cbr` files here
> are zips and §2.1 recorded `.cbz` files that were RAR; both are read by
> sniffing the magic, so neither is a special case any more — the error
> message that existed to explain the mismatch is gone, because there is
> nothing left to explain.

**A count that disagrees with its own list, again.** The Comics group
said **491** and the shelf showed **481**: the ten unreadable archives
have a `book` row and no metadata, so anything counting rows counts them
and every list joins metadata and cannot. §6.8 settled this for the
status line; `formats()` now joins through as well.

### 8.14 Browsing by what a book is

A sixth grouping beside Authors, Titles, Series, Genres and Years: six
formats are indexed and **four are worth browsing by**, because `prc` and
`azw3` are the same Palm container as `mobi` (§8.3), and somebody after a
comic does not care whether the pictures are zipped or RAR'd.

| Group | Formats | Books |
| --- | --- | --- |
| EPUB | `epub` | 1,326 |
| MOBI | `mobi`, `azw3`, `prc` | 53 |
| PDF | `pdf` | 511 |
| Comics | `cbz`, `cbr` | 481 |

**A group nobody owns is not listed** — an empty shelf is worse than no
shelf. Comics covered 286 books when it was built and 481 the day
CBR came in (§8.15), which is the point of naming the group after what
a reader wants rather than after a container.

> **Being in the DOM is not being reachable.** The eighth button
> overflowed the library column — 330 px on a Mac *by rule* (§10.9) — and
> `flex:1` with `white-space:nowrap` does not shrink text, it overflows.
> So **Review** ended up underneath the reader pane, which swallowed
> every click on it. The suite went from 2.2 minutes to 11 as sixteen
> review tests each waited thirty seconds to click a button that was
> there and untouchable, and the existing test passed throughout because
> it only asked whether the modes were *listed*. The strip wraps now, and
> a test clicks every mode and checks the right one went active.

### 8.13d What the scanner wrote into the title

`Blue Moon (v2.2)`, `Enemies of the Roman Order (not ocr)`,
`Myth-Nomers (uc)`. A version stamp, a note that a scan never went
through OCR, a bare format — none of these is part of a book's name. **17
titles carried one**, and they are exactly what the library-wide `names`
run left behind, deliberately: that pass tidies what it was asked to and
no more (§8.13a).

> **A closed vocabulary, never "strip any bracket".** The same 66
> bracketed titles hold `(The Murderbot Diaries)`, `(Osprey, Men at Arms
> #121)`, `(PhD, 1998)`, `(Collins Shorts, Book 1)` and `(Mysterious
> Creatures)` — every one part of the book or of its shelf. Guessing
> would cost more than it cleared. Known values only, the rule §6.1
> follows for genres and §8.13a for file extensions.

Two shapes the real titles forced: **two brackets with no space between
them**, `Rome and Her Enemies (Osprey)(not ocr)`, where only the
scanner's may go; and **a title is never emptied** — a book called
`(v1.0)` is absurd, but a book called nothing falls out of every list
that sorts by title.

**Measured: 18 cleaned, then 6 more once underscores were added.**
`./run.sh tags`.

> **Underscores stand in for spaces, where they have replaced them.**
> `Hamilton,_Laurell_K._-_Anita_Blake_04_-_The_Lunatic_Cafe` is an
> ordinary name from a filesystem that never learned about spaces, and
> read as it stands it parses as nothing at all — so that book kept a
> scanner called "Lee Hughes" as its author and its whole filename as its
> title. Put back, it is **The Lunatic Cafe · Laurell K. Hamilton · Anita
> Blake #4**. Only where they *outnumber* the spaces, though: one
> underscore among real words is somebody's punctuation, and
> `Chthon_Phthor Omnibus` keeps it.

The same normalisation runs wherever a name is read — `SeriesFromNames`
and `CreditsFromNames` both — because a series hidden inside
`Anita_Blake_04` matches nothing either.

**Three faults this uncovered, all mine, none of which a test had
caught:**

> **A pass must read the same source on every run.** `CreditsFromNames`
> took its facts from the stored title when that looked like a filename,
> and from the path otherwise. But once the pass has written a title the
> title is a real title, so a *second* run fell through to the path — and
> `Roberts, Nora - A World Apart - Emily & Jane.epub` is **longer** than
> the file's own name, a scanner having signed it. `A World Apart` became
> `A World Apart - Emily & Jane`, and would have grown again on the next
> run. **`file_title` is what the file said and does not change between
> runs**; nothing else here can be relied on not to. 35 books had already
> drifted, and re-running with the fix restored every one, because the
> original was still in `file_title` — which is the whole reason it is
> kept (§6.3).

> **Two passes can undo each other for ever.** `tags` took `(v2.1)` off a
> title; `names` derived that title afresh from `file_title`, which is
> the file's own words, tags and all, and put it straight back. A title
> `names` writes now goes through §8.13d on its way out, and the pair
> settle: two runs in a row report nothing to do.

> **The candidate query has to match the whole rule.** `tags` looked for
> a bracket, so the underscore half could never fire on a title without
> one. And `_` is a LIKE wildcard: asking for it needs an `ESCAPE`, or
> the query matches every title in the library.

**The tests passed the first time they were run, which is not the same as
passing.** They were written before the module but not *run* before it,
so §10's remedy applied: the rule was temporarily disabled and 12 of the
23 failed, which is what makes the other 11 worth having.

**A bug this found in `run.sh`:** the usage block was `sed -n '3,38p'`, a
fixed line range, so every command added pushed the last one off the end
— `fragments` fell out of the help the moment `tags` was added, silently.
It now reads to the closing rule rather than to a number somebody has to
remember, and a test asserts that **every** command appears.

### 8.13c A title that was cut off, finished by a provider

**§12's tier 1, now built.** `Anthony, Piers - The Caterpillars Questi.pdf` was
truncated years ago by somebody's filesystem, and no amount of matching
fixes a fragment — Open Library holds *The Caterpillar's Question*, and
`The Caterpillars Questi` matches nothing at all, for ever. The damage is
always at the **end**, so trimming back to the last complete word turns a
dead query live.

> **There is no clever damage detector, and there should not be.** A word
> cut mid-syllable cannot be told from an invented one without a
> dictionary, and this library is full of Xanth, Chthon and Phthor. So the
> provider *is* the detector: ask about the title as it stands, and only
> if nothing comes back is there anything to repair. One request per book
> — which is why the pass takes a scope.

> **The answer must COMPLETE our fragment, word for word.** Everything
> before our last word must match exactly, and our last word must be the
> beginning of theirs. A rule that can only ever *lengthen* a name cannot
> quietly swap in a different book.

> **A fragment needs a whole word beside it.** With one word there is
> nothing holding the answer to the book: `The Ring` would cheerfully
> become *The Rings of Saturn*, a different book by a different author.
> Written as a rule where the rule lives, though `trimmed()` refuses a
> one-word title anyway.

Two guards the real answers forced. An apostrophe **joins** rather than
separates — the catalogue's `caterpillar's` and the filename's
`caterpillars` are one word, and splitting on it made three words against
two. And Open Library's answer was *"The Bamboo Bloodbath (#3 in the
violent new series featuring Jason Striker, Master of Martial Arts)"*, so
`TitlePolish` takes the marketing off before anything is written.

**Every question and every answer is cached** in `provider_query` — the
table §12.1 asked for. §6.6 made an author "asked once, ever" by stamping
`author.fetched_at`, but that records asking about a *person*, and this
asks about a *string*. A silence is an answer and is kept like any other:
122 questions, 98 answered, 24 silences, and the second run asked nothing.

**Measured: 1 title finished, and the other refused — correctly.**
`Bamboo Bloodbath & Ninj` names *two* novels and Open Library holds one of
them, so their answer is **shorter** than what we hold. The cache shows
exactly that: the trimmed question found *The Bamboo Bloodbath* and the
rule threw it away. Completing a fragment is not replacing it with half
of itself, and that book wants §12.4's editor.

The silences are their own finding. Four of them are Bio of a Space
Tyrant books still titled `Anthony, Piers - Tyrant 1 - Refugee`, because
§8.13a was run with `--format pdf` and these are not PDFs — `./run.sh
names --author "Piers Anthony"` is what they want.

### 8.13b A co-author written into the title

**§12's tier 0, and it needed nothing but a rule.** No provider, no key,
no network. `Dead Morn (with Roberto Fuentes)` is a book by two people
whose filename had room for one, so the second went in brackets and every
pass since has read it as part of the book's name.

Measured first, over the whole library rather than the two examples that
prompted it: **13 books carry it** — nine of Piers Anthony's, two of
P. N. Elrod's, and one each from Robert Asprin and Andre Norton. Every
other parenthetical in the library is the scanner talking (`(not ocr)`,
`(v2.1)`, `(txt)`, `(9781982156176)`, `(1946)`, `(Book 1)`), and the word
`with` is what tells them apart.

> **A co-author is a *second* author, so there must be a first.** The two
> Elrod PDFs credit nobody, because their filenames name him and
> `epub_pdf` reads only the Info dictionary. Crediting Nigel Bennett
> alone would promote the collaborator to author, which is worse than the
> bracket. They are left for `./run.sh names` — §6.6's order again.

> **Only the `with` bracket goes.** `Elvenblood (With Mercedes Lackey)
> (v1.0)` keeps its version tag. It is somebody else's rubbish to clear,
> and a pass that tidies more than it was asked to is one nobody can
> predict.

**Measured: 11 titles freed, 12 people credited, 2 deferred.**
`./run.sh coauthors`.

**And it uncovered a bug that had been there since M2.** §5 says credit
order matters, which is why `book_author` carries a `position` — and the
browse query asked for it and did not get it:

> **`ORDER BY` belongs *inside* the aggregate.** Outside it, it orders
> the single row the aggregate returns, which is no order at all.
> `GROUP_CONCAT` kept whatever the join handed it, which was `author_id`
> order — so **Mercedes Lackey came out ahead of Andre Norton on her own
> collaboration**, because she was already in this library and her row is
> older than his. It went unseen for as long as it did because almost
> every book has exactly one author; it surfaced the day a pass began
> crediting second ones.

The first version of the test **passed against the broken query**, because
a fixture that creates authors in credit order cannot fail. It only
became a real test once it built the collaborator *first*, so her id is
lower — which is the actual library, and the only shape that fails.

### 8.13a Three facts written as one name

`Anthony, Piers - Xanth 08 - Crewel Lye` browsed as a single run-on
title, credited to nobody — or to `harry`, which is whoever ran the
scanner. 45 of Piers Anthony's PDFs looked like that, and the cause was
one missing step rather than three.

§8.4 made the filename lead for PDFs, but only for the **title**:
`epub_pdf` reads the author from the Info dictionary alone, so 35 of the
45 had no author at all. §6.6 runs its passes **authors → books →
titles** because everything else is asked *of* an author — and
`SeriesFromNames` says so in as many words, refusing a name when
`not row["author_sort"]`, because there is nobody to agree with. So the
Xanth novels could never have become a series either. The first domino
had never fallen.

The name says all three things and needs no provider. `epub_filenames.py`
reads it, narrow by construction:

> **A file that named itself is not this pass's business.** It acts only
> where the title the database holds *is* the filename, which is the mark
> of a file that said nothing. An EPUB whose OPF gives a real title is
> left alone even when its filename would parse — `Anthony, Piers -
> Cluster 2 - Chaining The Lady.epub` already knows it is *Chaining the
> Lady*.

> **The author leads, or nothing happens.** `Surname, First - …` at the
> head is the shape this library uses, and the comma is what makes it a
> name rather than two words that happen to lead. A filename with its
> author at the *end* — `10 is Better Than 01 - Heather Jarman` — is a
> book called that, and is refused. `AuthorHint` (§8.13) already reads
> names this way for cover matching; the difference is that here it is
> written down, which is why it is narrower.

> **The series is not judged here.** `SeriesFromNames` already decides
> that, and its rule — two books by one author must agree — is what keeps
> "is Better Than" out of the library. This pass credits the author first
> so that rule can finally run, then asks it, then reads the title in the
> light of its answer. Two implementations of one judgement is how they
> come to disagree.

Without a series believed, the words are left whole rather than cut at a
guess: `Aton 01 - Chthon` stays as it is, because a number nobody has
corroborated is not evidence of anything.

**Measured: 45 books credited, 17 filed under six series, 45 retitled** —
Xanth, Mode, Aton, ChroMagic, Jason Striker and Geodyssey. Every one of
the 45 was then read, and the audit found two faults, both of them in
rules that already existed:

> **A book already on the shelf is corroboration too.** `SeriesFromNames`
> counted agreement only among *unfiled* books, because `_books()` asks
> for `series_id IS NULL`. Geodyssey 02 and 04 had been filed by an
> earlier run, so when 03 arrived it was the only unfiled book claiming
> the name and was refused as a series of one — with two of its own
> siblings already filed under it. Agreement is about the library, not
> about this run.

> **A title this pass wrote is still its own to refine.** `Hope of Earth`
> then got its series and kept its run-on title, because it no longer
> looked like a filename and so fell out of the pass's own scope. A book
> alone on its shelf keeps its number; when a sibling turns up, that
> number becomes the shelf talking. Without this the first answer stood
> for ever. The same run reported `nothing to read` while filing a book,
> because the summary described only the first of its three steps.

Two books nothing could corroborate were filed by hand, which is what
`PUT /api/books/{id}/series` is for (§8.10): `Geodessey 01 - Isle of
Women` is a **misspelling** of Geodyssey, so that spelling had one book
and nothing to agree with it, and `Bio of a Space Tyrant 06` is the only
book of its series in the library. **All 45 now read as three facts**,
24 of them in seven series.

Filing them by hand exposed the last fault, and it is the same shape as
the other two — a rule that could not see past the run it was in:

> **Once a person has said what the series is, the words in front of its
> number are the shelf, however the file spells them.** Hand-filing left
> *Isle of Women* still titled `Geodessey 01 - Isle of Women` — the shelf
> talking twice and disagreeing with itself — because the trim demanded
> that the filename spell the series the way the library does. It cannot:
> the misspelling is precisely why nothing corroborated it and why a
> person had to file it at all. **The number is what ties the words to
> the shelf**, so it must agree; a filename saying `01` under a book
> filed at #4 keeps its words, since nothing then connects them.

**Two guards were too narrow, and running the pass across all of one
author's formats found both.**

> **"The title *is* the filename" was only ever a proxy** for "the file
> said nothing about itself". Four Bio of a Space Tyrant EPUBs carry
> `Anthony, Piers - Tyrant 1 - Refugee` in their own OPF while the file
> on disk has the author appended a *second* time — so the two were not
> equal and the pass walked past them. The file did speak; what it said
> was a filename. A stored title leading with `Surname, First - ` came
> out of somebody's folder wherever it now sits, and **the tidier of the
> two names wins**: reading the file instead would have left "Refugee -
> Anthony, Piers".

> **A title never ends in `.txt`.** 12 books here do — `Volk.doc`,
> `Running with the Demon.txt`, `Nanny Ogg's Cookbook.htm` — the mark of
> a library converted from one format to another by somebody who kept the
> old name. A closed list of known extensions, so `Mr. Norrell` is safe.

Across all of Piers Anthony's formats that took **7 more titles**, filed
the four Tyrant novels 1–4, and gave *Currant Events* its Xanth #28 —
the last of those only because agreement now counts books already on the
shelf (§8.13a), since every other Xanth book was filed in the earlier
run.

**And it split a series.** The four EPUBs are filed under **Tyrant**,
from their filenames, while the PDF hand-filed earlier is under **Bio of
a Space Tyrant**. Both names are honest and they are one series; nothing
automatic can choose between a shorthand and a full name, so it is
`PUT /api/books/{id}/series` again — §12.4's editor by another route.

### The library-wide run, and the four names that were not names

Run across everything on 2026-08-19: **265 books credited, 145 filed
under a series, 284 retitled.** The library went from 158 series to
**189** and from 623 books credited to nobody to a few dozen.

**The dry run earned its keep before a single row was written.** Of 268
proposals, 207 filled a blank and **61 replaced an existing author** —
and reading those 61 found four filenames the parser had misread, each a
different way for a comma to turn up in something that is not a name:

| Filename | Would have credited |
| --- | --- |
| `e-Book - Feist, Raymond E. - Krondor The Assassins` | "Raymond E. e-Book - Feist" |
| `Cryptozoology, A Guide to (Mysterious Creatures) - George M. Eberhart` | "A Guide to (Mysterious Creatures) Cryptozoology" |
| `Jakubowski, Maxim (ed.) - New Crimes` | "Maxim (ed.) Jakubowski" |
| `Pratchett, Terry & Gaiman, Neil - Good Omens` | "Terry & Gaiman, Neil Pratchett" |

> **A surname may be hyphenated; a *spaced* dash is a separator.**
> `e-Book - ` is somebody's prefix, not a name.

> **Given names are short, capitalised, and have no brackets in them.**
> That refuses a title which merely follows a comma, and `(ed.)` with it
> — §5 has a `role` column for editors, and until something fills it,
> saying nothing beats saying the wrong thing.

> **Two people can share one filename.** `Pratchett, Terry & Gaiman,
> Neil` is read as both, in the order given, because §5 says credit
> order matters.

**Then it made work for the dedupe pass, which is the system behaving.**
Filenames spell people differently — `C.S Lewis`, `JRR Tolkien`,
`Laurell K Hamilton`, and one flat typo, `Nora Robers`. That left 6
spelling clashes and a misspelling; `POST /api/authors/dedupe` merged all
7 without being asked twice, six as "identical once spacing is ignored"
and the typo as "confirmed by Open Library". It still refused
`Stephen King`/`Stephen Hawking`, which is the point of it.

**What the pass cannot reach**, counted afterwards: **31 titles that are
only a shelf label** — `Alex Cross 07`, `Elemental Masters 02` — where
the filename names a position and no book at all; and 17 carrying a scanner's version tag — since
cleaned by §8.13d, which is the rule this pass deliberately left
unwritten. The shelf labels still need a provider or a person.

**The same shape is waiting on 241 more books across 49 authors** — 235
PDFs and 6 PRCs — which is what `./run.sh names` reports library-wide.
They are not done, because a pass this sharp should be aimed rather than
loosed, and because each author is worth an audit of its own: 13 books
are still credited to `harry`.

The pass is scoped for that reason:
`./run.sh names --author "Piers Anthony" --format pdf --apply`.
A scope narrows *which* books are read, never how they are judged —
agreement is still counted within it, so asking about one author's PDFs
cannot borrow corroboration from books the caller did not ask about.

### 8.9 M10: shelving without opening

Putting a book on a shelf used to mean opening it first — the only way in was the reader's **＋ Shelf** button. But shelving is something you do *while browsing*: you recognise a book in the list and file it. So the offer is on the row.

Every book row carries **＋**, which opens the same sheet the reader uses — the same sheet, not a second one, since two ways to edit one thing is how they end up disagreeing. **Looking at a shelf** adds **−** to each row, which takes the book off *that* shelf.

- **A button cannot live inside a button.** The row has been a `<button>` since M2, so the controls sit beside it in a `.book-item` wrapper, and every existing test that clicks `.book-row` still means what it did.
- **The controls are always visible**, with a 44 pt target on touch: hover is unreachable on an iPad, which is where most of this library is read.
- **Only a stored shelf offers −.** "Reading now" and "Finished" are consequences of reading rather than lists (§10.9).

Tests pin the point of it: **＋** must not open the book, and clicking the row must still show it.

### 8.11 M10, continued: finishing a book, and leaving a shelf

Shelving from the list (§8.9) left three things you still could not do: say you had finished a book, throw away a shelf, or get a book off **Reading now** — where anything opened once stayed for ever.

> **You cannot take a book off a consequence; you change the fact.** A smart shelf is a view of reading state (§10.9), so the row controls there edit the state and the shelf follows: **✓** marks a book read and it moves to Finished, **−** says you are not reading it any more. There is still no route that *puts* a book on a smart shelf — the asymmetry is the design, not an omission.

Marking a book read **keeps your place**. Finishing is not forgetting: the CFI, the fraction and the highlights all belong to the book, and only the status changes. Taking a book off Reading now keeps it too, which is what makes the control safe enough to offer on a row.

The same gesture lives on the book detail, as a **toggle** rather than a one-way door — a book marked read by mistake, or picked up again, has to be able to say so. It is also the only way to mark a book you never opened here, which is most of what a thirty-year library contains.

**Deleting a shelf asks twice.** One tap turns **−** into **Delete?**, the second does it, and an unanswered question goes back to being an offer after five seconds. A shelf cannot be un-deleted and a stray tap on a phone should not cost you a list you built. The books are not the shelf's to take: `ShelfStore.delete` removes the shelf alone. Smart shelves carry no such control, because there is no list there to delete.

**Naming a shelf is the app asking, not the browser.** `window.prompt` blocks the page, cannot be styled, and on iOS reads as Safari asking — the reasons §8.2 gave for the note composer, and they apply here too. `NameComposer` is that composer generalised: Enter keeps the name, Escape or tapping away gives up, and an empty name is giving up rather than a shelf called "". A shelf made from the sheet while holding a book is a shelf **meant** for that book, so it is created and chosen in one gesture.

**A bug this uncovered:** `＋ New shelf` was hidden on some render paths and not others, so leaving Shelves for Titles left the button behind, offering to make a shelf on a screen with no shelves on it. Its visibility is now decided once, on every path.

### 8.12 Covers: page one, when page one is the cover

555 books of 2,179 showed a lettered tile, and **514 of them were PDFs — every PDF in the library.** M7 skipped this on the grounds that rasterising a page is a heavy dependency for a proof-of-concept (§8.4); at 514 blank books that reasoning expired. pdfium is BSD, ships as a wheel with the binary in it, and needs no network — so a cover costs nothing but the reading of one page.

> **The question is not "is this a scan?" but "is page one a cover?"** A cover is one raster image covering the page with no text over it. A title page is text. That rule keeps the text-born books whose first page is a proper cover image — *Rebel Angels 2*, *Fires of the Faithful* — and it is what page one *is*, not what the file is.

Measured before writing any of it: of 514 PDFs, **34% open on a full-page image and 61% are text-born**, which is why this only ever closes part of the gap. The detection reads page one's object list and costs about a second for the whole library; only the books that qualify are rendered.

**Apply, then audit — and the audit was the point.** 159 books took a cover; all 159 were then rendered as contact sheets and looked at. **145 were genuine cover art and 14 were not**: twelve academic title pages (Routledge's, with *"Also available as a printed book"* across the foot), Neil Gaiman's *A Study in Emerald*, which is a facsimile Victorian newspaper page, and a page of *Calvin and Hobbes* strips. **91% right, and the 9% could only be seen.**

> **A wrong cover is worse than no cover, so saying so is a feature.** `DELETE /api/books/{id}/cover` drops the picture and records `origin = 'user'` on the `cover` field — the same rule as a corrected author, so no later pass fetches another. That is what the 14 went through, rather than a script.

A pixel rule was tried and rejected: pale-and-grey would have dropped 16 title pages but also Stephen King's *On Writing*, whose real cover is white with handwriting on it. Nothing separates a white cover from a white title page except looking, so the code does not pretend otherwise.

`./run.sh covers` reports, `--apply` writes, and a library without `pypdfium2` installed simply indexes as before — the import is where it is used.

### 8.13 Covers: asking a provider for the rest

396 books were left showing a letter — 356 text-born PDFs whose first page is a title page, 26 EPUBs and 15 PRCs whose files carry no image at all. Their own files have nothing to give, so the only remaining source is a provider.

**Measured before building it**, on 40 random cover-less books: **70% get an offer from Open Library**, and roughly three in four of those offers are the right book. A later dry run over 20 more matched 12. So this closes over half of what is left — and gets some of them wrong, which shapes everything below.

> **The enrichment score is the wrong instrument here.** It refuses to redistribute weight over fields we do not have (§10.12), and these books have no ISBN, no year and mostly no author — so a *correct* match scores 0.19. `CoverMatch` asks the only question a picture needs: **do the titles agree** once the filename scaffolding is off both of them. `Vampire Files 02 - Lifeblood` matches *Lifeblood*; `Mute` does not match *God on Mute*.

**249 of these books name their author in the filename and nowhere else** — `epub_pdf` reads the author from the Info dictionary only, so `Anthony, Piers - Mute.pdf` has no author at all in the database. `AuthorHint` reads it back off the filename for matching: surname-first at the head is taken as written, and a name at the *end* only counts when the library already knows that surname, or "The Eagle and the Wolves" acquires a Mr Wolves. It is a hint and nothing more — never written to the database, because deciding who wrote a book is what §6.3's passes are for.

> **Asked once, ever.** A miss is recorded exactly like a hit (`openlibrary-none`), so the next run does not ask again — §6.6's rule, and at one request a minute asking twice is somebody's whole afternoon. A lookup that *failed* is not recorded, so an outage costs nothing but time.

**The pace is its own, and much slower than the service's.** `PROVIDER_MAX_RPS` governs browsing-shaped traffic; a sweep of 400 lookups in one sitting is closer to bulk, which Open Library asks people to do from the data dumps instead. So `COVER_FETCH_INTERVAL` (default **60 seconds**) paces the search API alone, and the pictures — static files on a different host with a published allowance of about 100 every five minutes — keep the ordinary pace. That makes a full sweep an overnight job rather than a ten-minute one, and it is resumable: stop it whenever, and the next run picks up where it left off.

Open Library answers a cover it does not have with **a one-pixel image rather than a 404**, so anything under 100 px is refused and recorded as a miss.

**Measured over the whole library: 396 asked, 210 covers, 53%.** With the pdfium pass that took covers from 1,624 books to 1,978 of 2,179 — **90%**.

**The audit found 15 wrong in 210 — 93% right.** Two were the wrong book: *Hadrian's Wall* (ours is Osprey's, the cover is a 1922 book of the same name) and **Star Wars: Edge of Victory I — Conquest, which matched Jude Deveraux's *The Conquest***, because the scaffolding-stripper left the word `conquest` and nothing else. The other thirteen were the right book wearing the wrong picture: title pages, two blank Oz placeholders with the title typed on them, and a maroon library binding with a barcode. All went out through `DELETE /api/books/{id}/cover`.

> **An author is corroboration, not a veto.** The library holds *The Art of War* by **Sunzi**; Open Library files it under **Sun Tzu**. Both are that man, and refusing on the spelling cost a cover for nothing. The author now *ranks* answers — a candidate whose author agrees beats one whose does not, and the best is taken rather than the first — and a title-only match is recorded as `openlibrary-title`, so an audit can look hardest at exactly the ones that were least sure. The title remains the guard: `Mute` still does not match *God on Mute*.

That change made the 186 misses worth asking about again, which is what a recorded miss is for — it can be cleared deliberately when the rule that produced it changes.

## 9. Decisions — settled

| # | Decision | Consequence |
| --- | --- | --- |
| 1 | **Same stack as dlna-gateway** | FastAPI + hypercorn + SQLite/FTS5 + vanilla static SPA. No framework, no build step, no desktop shell. |
| 2 | **Separate service** | Own port (8770), own plist (`com.example.epub-reader`), own `books.db`. No coupling to dlna-gateway beyond shared conventions and TLS cert host. |
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
./run.sh names --author "Piers Anthony" --format pdf   # read the filenames
./run.sh start --foreground     # run in this terminal, Ctrl-C to quit

.venv/bin/python -m pytest      # code tests
npx playwright test             # UI tests (regenerates its own fixture library)
```

**`start` and `restart` always scan the library first**, so books dropped
into `LIBRARY_ROOT` are picked up without a separate step. The scan also
runs inside the server at startup, so a launchd-managed start behaves the
same as a scripted one.

`run.sh` is launchd-aware: when `com.example.epub-reader` is loaded it drives
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
epub_security.py    ContentSecurityPolicy, SecurityHeaders, SameOriginPolicy
                    — what may run, and who may ask for a change
epub_server.py      EpubGateway, BookLibrary — the app, and the read-only
                    library it serves
epub_routes.py      Services, Routes, App/Browse/Shelf/Reading routes
epub_maintenance_routes.py  MaintenanceRoutes — §6.3's passes over HTTP
epub_library.py     LibraryScanner, ScanManifest, ScanReport, BookFile
epub_names.py       PersonName, title_sort_key — sort keys, computed at ingest
epub_metadata.py    EpubMetadataReader, BookMetadata, MetadataError
epub_mobi.py        MobiMetadataReader — PalmDB/EXTH, for MOBI/AZW3/PRC
epub_pdf.py         PdfMetadataReader — the Info dict, when it is worth
                    having; PdfCoverRenderer — page one, when it is a cover
epub_covers.py      CoverBackfill — a cover from the book's own first page;
                    ProviderCovers, CoverMatch, AuthorHint — and from
                    Open Library for the files that hold no image
epub_cbz.py         CbzMetadataReader — comics: a filename and a first page,
                    zip or RAR alike
epub_rar.py         RarArchive, repack — comics packed with RAR, read
                    through libarchive and repacked as a zip on the way out
epub_series.py      SeriesFromNames — series read from filenames
epub_filenames.py   CreditsFromNames — author, series and title, read from
                    one filename, in §6.6's order
epub_coauthors.py   CoAuthorsInTitles — the second author somebody wrote
                    into the title, because a file has one name
epub_tags.py        ScannerTags — (v2.1), (not ocr), (uc): a closed
                    vocabulary, never any bracket
epub_fragments.py   TitlesFromFragments — a title cut off at the end, and
                    the provider that can finish it; every question and
                    answer cached in provider_query
epub_credits.py     BookCredits — who wrote it and where in a series,
                    when neither the file nor its name says
epub_forget.py      ForgetMissing — removing books whose files are gone
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
epub_provenance.py  Provenance, Candidates — who said so, and whether we
                    may overwrite them; the one place §5's rule lives
epub_db.py          Database, Migration, MigrationRunner — forward-only
epub_reading.py     ReadingState, ReadingStateStore — position, server-side
static/settings.js  ReaderSettings — display prefs, per device
static/annotations.js Annotations — the selection offer and the marks panel
epub_annotations.py AnnotationStore — highlights and notes, shared
static/toc.js       TableOfContents
static/composer.js  NameComposer — asking for a name in the app, not the
                    browser; shelves are named here
run.sh              start / stop / restart / status / scan / repair / refresh
com.example.epub-reader.refresh.plist   the weekly author refresh
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
- **A work's language is not the edition's.** Accepting six reviewed
  candidates made David Baldacci Vietnamese and Brandon Sanderson Polish.
  Open Library lists *every* language a work was ever published in, and
  the code took the first. It is now believed only when the work exists
  in exactly one language — and the codes are mapped rather than
  truncated, since MARC's `swe` is `sv`, not `sw` (which is Swahili).
  `und`, `mul` and `zxx` are not languages at all.

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
Getting a book *off* one is a different question, and §8.11 answers it:
you change the reading state, and the shelf follows.

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

### 10.15 A method that was 56% of its module

`EpubGateway._register_routes` was **530 lines holding 57 nested
functions** — more than half of a 945-line file, and the largest thing in
the codebase was not a class at all, which §10.1 says everything here
should be. No route could be tested without standing up the whole app,
and nobody could read it in one sitting.

Five classes now, grouped by what they are about rather than by what
FastAPI needs: the app itself, browse, shelves, reading, and §6.3's
maintenance passes.

> **Reaching into another object's privates only moves the problem.** The
> routes needed nineteen of the gateway's attributes, and a route class
> saying `gateway._browse` would be the same coupling spread over more
> files. `Services` is those collaborators, named and public, handed over
> once.

| | before | after |
| --- | --- | --- |
| `epub_server.py` | 945 | **398** |
| `epub_routes.py` | — | 536 |
| `epub_maintenance_routes.py` | — | 163 |

**Three lessons from doing it mechanically**, because 51 routes are not
moved by hand:

> **`ast.get_source_segment` does not include decorators.** `node.lineno`
> points at the `def`, so a first pass moved 51 route bodies and left
> every `@app.get(…)` behind. Everything registered nothing and answered
> 404 — including `/healthz`, which is how it was noticed at once rather
> than subtly.

> **A regex does not know a string from an identifier.** Rewriting
> `shelves` → `self.services.shelves` over the source text rewrote it
> inside `"/api/shelves"` too, and four URLs quietly moved. The rewrite
> is done over **tokens** now, editing by position so that spacing,
> comments and docstrings survive exactly — `tokenize.untokenize` does
> not preserve them, it reflows the lot.

> **A split that leaves one module too big has moved the problem, not
> solved it.** The first cut produced a 686-line `epub_routes.py`;
> maintenance came out into its own file, and every module is now inside
> the ~400 the review asked for except `epub_index` and `epub_gateway`.

### 10.13a A linter, and the 116 things it found

This project had none. Running ruff cold on a finished codebase found
**116 findings and no bugs**, which is the point twice over: none of it
was wrong, and nothing had been checking. The tell was **eleven *unused*
`# noqa` comments** — suppressions written for a linter that had never
existed.

`ruff.toml` picks rules rather than taking a default, at ruff's own line
length of 88: asking for 79 flagged 436 lines, almost all of them in
tests, which is churn rather than improvement. `RUF001–003` are off,
because the prose here uses en-dashes and typographic quotes on purpose
and "ambiguous unicode" is about homoglyphs in identifiers, not
punctuation in English.

> **Two false positives are suppressed with the reason, never blankly.**
> `sqlite3.Row` has no `__contains__`, so `field in row.keys()` is not
> optional; and `ScannerTags.strip` is our own classmethod, not
> `str.strip`.

**One finding was a bug waiting to happen.** `epub_index` rebound `image`
inside its own `with`, so the RGB conversion was assigned and thrown
away — a cover with an alpha channel could never have been saved as
JPEG. And renaming the loop variables in `epub_genres` and `epub_pdf`
the same way would silently have dropped a `.strip()` in each; caught by
reading what the rest of the block still used, which is the whole risk
of a mechanical tidy-up.

**Enforced, not hoped for**: `tests/test_lint.py` runs ruff and fails on
anything, and ruff is in `requirements-dev.txt`. A tidy-up nobody
enforces lasts until the next commit.

### 10.14 One place for the rule everything depends on

§5's provenance rule is the invariant the whole design rests on — and it
was written **eleven times**. Every pass hand-rolled its own
`INSERT INTO field_provenance … ON CONFLICT`, and eight carried their own
copy of the guard that reads it back.

> **A rule that must be copied into N places will eventually be in
> N−1.** Not a tidiness complaint: a rule added one afternoon — a title a
> provider *confirmed* outranks one *derived* from a filename — went into
> three of those eight by hand, and the pass it was missing from spent a
> scan undoing the other two's work (§8.16).

`Provenance` writes it and `Candidates` reads it. The guard exists
**twice on purpose** — as a method for one book, as a SQL clause for a
pass selecting thousands — because a pass that asked per book would run
one query per candidate. Two forms of one rule is exactly how they come
to disagree, so a test drives both over every combination of origin,
source and lock and asserts they never do.

**Two guards were deliberately left alone**, because they are different
questions wearing the same shape:

- `ProviderCovers` asks *"have we already been here?"*, not *"may I
  write?"*. A recorded miss (§8.13) blocks a second question while
  leaving the field perfectly writable.
- `TitleFromWorks` refuses any title a provider has set, including one
  derived from a filename — stricter than the shared guard, because
  asking a bibliography about a book somebody has already answered for
  is a request spent on nothing.

Saying so in a comment is the point: an exception nobody wrote down is
indistinguishable from an oversight.

## 11. Conventions

- Match dlna-gateway: vanilla JS, no framework, no build step, one `app.css` with `:root` tokens, one `app.js`
- Palette and type inherit dlna-gateway's tokens (`--bg:#0A1526`, `--amber:#FFC24A`, Syne + DM Mono) so the two apps read as a family — **except inside the reader**, where the book's page gets a neutral reading theme and the app chrome gets out of the way
- foliate-js is a pinned submodule, never edited in place; differences go in `reader.js`
- All SQLite access server-side; the frontend only sees JSON
- Migrations forward-only and numbered
- **Track current SQLite, and use it.** `Database.NEEDS` is checked on
  the way in and says what to do if it is not met. Writing round old
  versions costs clarity for nothing — the workaround is always less
  obvious than the feature, which is how `GROUP_CONCAT`'s ordering came
  to be silently wrong (§8.13b). This supersedes §10.9's "the same on
  any SQLite"; the `COALESCE` there still works and is left alone.
- `Intl.Collator` / `Intl.Segmenter` over hand-rolled string logic — the library is multilingual
- Never modify the user's book files — read-only, always
- Every user-facing string is written from the reader's point of view, not the system's

## 12. Open actions

Measured against the real library on 2026-08-19. **What is done is
listed first and briefly** — the reasoning behind each lives in §8, where
it belongs. What follows it is genuinely unfinished, left exactly as it
was written. What closes the section is not a to-do list at all: those
are limits that were weighed and accepted.

### Done

- **Cover art.** From the book's own first page where that page is a
  cover, and from a provider for the rest. **91% of books carry one.**
- **A co-author written into the title** is read as a second author, by
  a rule that costs nothing.
- **A title cut off by a filesystem** is finished by asking a provider
  for the trimmed form, and only accepted when the answer *completes*
  what we hold.
- **A filename read as three facts** — author, series and title — across
  the library, each run aimed at one author and read before it was
  applied.
- **What the scanner wrote into the title** — version stamps, `not ocr`,
  underscores standing in for spaces — is taken off by a closed
  vocabulary.
- **Comics packed with RAR** are read and served, which brought in
  several hundred books that had been invisible.
- **Browsing by format**, grouped as a reader thinks of them rather than
  as the disk stores them.
- **A scan reads what it finds.** The passes that cost nothing run on
  every start, so dropping a book in and restarting is the whole
  workflow.
- **Duplicate copies folded to one row per work**, audited each time and
  reversible; no file was ever deleted.
- **A linter, a single place for the provenance rule, and the routes
  split into classes** — the three findings of a review, taken in order.

### Still open

### 1. The names that are clear to a person and not to the code

**This is the next piece of work.** Piers Anthony's filenames were
obvious to a reader and opaque to the code, and closing that gap is the
job. It is deliberately **not** a general enrichment sweep: `Dune - Frank
Herbert` needs nothing from anybody, and asking about it would be
spending an afternoon confirming what we already hold.

> **The gate comes first, and most books never pass it.** A name goes to
> a provider only when nothing here can already read it. Three kinds of
> certainty, all local and all free: one side of the name is an author we
> already hold (573 of them); the shape is one §8.13a already reads; or
> the file's own metadata agrees with its name. Only what survives all
> three is asked about.

Then the inversion that makes the rest work:

> **The filename stops being the answer and becomes a hypothesis.**
> §8.13a believes one rigid shape outright, which is why it needs a comma
> at the head, why it fires on 241 books and no others, and why
> `10 is Better Than 01 - Heather Jarman` had to be *refused* rather than
> understood. The new pass cuts a name every plausible way, asks Open
> Library which reading is real, and keeps the one that comes back
> corroborated.

1. **Split into candidates, not into an answer.** Both orderings of a
   two-part name, every separator, with and without a leading number —
   `Heather Jarman - 10 is Better Than 01` yields the reading §8.13a
   cannot reach.
2. **Anchor on whichever end confirms.** Ask whether the candidate author
   exists, or whether the candidate title does. One confirmed end is the
   anchor; a name where neither end confirms is left alone and said so.
3. **Find the other end through the anchor.** A known author's works, not
   a global title search — §6.3's rule, and the reason titles worked at
   all: "Second Chance" matches hundreds of books, but *this author's*
   Second Chance is one question with one answer.
4. **Score into the bands that already exist.** Decision 4's ≥0.90 /
   0.60–0.90 and `epub_book_enrich.py`'s queue, which already stores why
   it scored that and which fields would change. A pipeline choosing
   between competing readings is precisely what a review queue is for,
   and a second one would only end up disagreeing with the first.

> **Here a provider's answer wins, because nothing else is offering.**
> Everywhere else the file leads and enrichment fills silence (§6.4). The
> gate has already established that neither the file nor its name can be
> read, so a confirmed reading is the only evidence in the room. That
> also settles `title`: it stays outside the enrichment writable set
> (§10.13) for every book whose name we *can* read, and is writable for
> the ones we cannot.

> **Every question asked is written down, hits and misses alike.**
> Several hypotheses per book is where §6.6's "asked once, ever" stops
> being enough — `author.fetched_at` records that we asked about a
> *person*, and this asks about a *string*. It needs a query → answer
> cache keyed on the question, or the second run costs what the first
> did.

**Measure before building it**, as §8.13 did for covers and §8.12 for
first pages: 30 real unreadable filenames through the splitter, counting
how many anchor at all. If the answer is 20% this is a different piece of
work, and better to know that on the first morning.

### 2. Names that are damaged: the open web

Tiers 0 and 1 are built — a co-author freed from a title (§8.13b), and a
truncated title finished by trimming it back to a whole word and asking a
provider (§8.13c). **Tier 2 is not**, and the gate on it still holds:

> A catalogue can only match what it holds; a search engine indexes the
> long tail, which is why searching a raw string answers in one go where
> a catalogue returns nothing. It needs **a real search API, never
> scraping** — a key in `.env` beside `GOOGLE_BOOKS_API_KEY` — reads
> **result titles and snippets only**, applies tier 1's prefix rule plus
> the author appearing in the same result, and requires two independent
> results to agree before writing anything. One query per string, ever.

**Do not build it until the free tiers have been measured against what is
left.** They were, and they left very little: the population tier 2 could
reach is now a handful of books, and it is the only part of this that
costs money.

### 3. Fetch each matched work's own record

§10.13 recorded this and left it undone. Open Library's *search* results
carry title, author, year, ISBN, language and a cover but **no synopsis
and no publisher**, so auto-apply writes almost nothing:

| | of 2,364 shown |
| --- | --- |
| no synopsis | 2,097 |
| no genre | 2,058 |
| no first-published year | 1,769 |

One extra request per book fixes all three — and it is the same call that
confirms a candidate in §2 and repairs a fragment in §3, so they are one
piece of work rather than three.

### 4. Editing a book's metadata

**Series is not coming from a provider.** §6 said it would and it was
never built; Open Library's search results are thin enough (§10.13) that
its series data is unproven, and nothing measured here says it can tell
Xanth #8 from Xanth #9. The answer is the reader's own hand.

Hand-editing today is **authors and series only** (§8.10). It should be
the whole record — title, series and number, synopsis, genre — because
that is the only thing that can reach a book the machinery cannot: the
truncated `Bamboo Bloodbath & Ninj`, the clipped `The Caterpillars
Questi`, a series nobody else has heard of. It costs no requests, and the
machinery for it already exists: what a person says is `origin = 'user'`,
locked, and no pass overrules it (§6.4).

**1,434 books have no series.** Filenames gave 297 of them one and
hand-filing reached those that could be proved; most of the rest either
genuinely stand alone or need a person. `PUT /api/books/{id}/series` is
the answer today, one book at a time.


### Constraints the builder accepted

Not a to-do list. Each of these was weighed, and the cost of removing it
judged higher than the cost of keeping it. They are written down so that
a later reader can tell a decision from an oversight.

- **Hand-editing is authors and series only.** Title, synopsis and
  genres stay provider-or-file; `title` is outside even the enrichment
  writable set, because the file's title is what you see in your own
  folders. §12's editor would change this deliberately.
- **9% of books have no cover.** Their own files hold no image and no
  provider matched them. A wrong cover is worse than none, so the
  lettered tile stays.
- **57% genre-map coverage.** The rest is genuine junk — `Rand al'Thor
  (Fictitious character)`, `wierook32` — logged and never guessed
  (decision 5).
- **Ten comics cannot be opened.** libarchive refuses one run of
  2003-vintage RAR archives. Another unrar would reach them, at the cost
  of a dependency that is not already on the machine.
- **foliate-js is pinned at `78914ae`** and has never been updated, while
  upstream declares itself unstable. Updating is a deliberate act with
  the whole UI suite behind it (§3).
- **No Calibre interaction of any kind** (decision 3), **no format
  conversion, no device sync, no DRM** (§1). Each is a product of its
  own.
- **There is no authentication.** It is a personal service for one
  household on a tailnet, and adding accounts would be a product of its
  own. What follows from it is handled rather than hoped about: the bind
  address defaults to loopback, and a state-changing request from
  somebody else's page is refused (§8.18). Reachability is the whole
  access control, which is why widening `EPUB_BIND` is a deliberate act.
- **The test database accumulates.** `.test-library/books.db` survives
  between runs, so uniquely-named shelves pile up. Harmless until a test
  counts globally, which one did.
