# epub-reader

A browser-based e-book library and reader, built in the shape of `dlna-gateway-2.0`: **Browse · Search · Read**.

EPUB first, MOBI/AZW3 shortly after, PDF eventually.

**Status: M3 in progress — search works; shelves and the remaining sort options are next. Running against the real 1,300-book library.** EPUB only for now (§2.1). All seven decisions settled (§9). The reader has contents, themes, typography controls and server-side position. Next action is M2 (§8).

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

## 2.1 Scope: EPUB only, for now

The library also holds PDFs, CBZ and CBR, but **only `.epub` is listed or
served** until the reader is finished. PDF is a different rendering model
and foliate's adapter is self-described as a proof-of-concept; CBR is RAR,
which nothing in the stack reads. Listing a format we cannot render well
only promises what we do not deliver. MOBI/AZW3 come next — same engine.

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

Validated against `~/Books`: 34 GB, 1,327 EPUB files, 585 PDFs, 309 CBZ,
295 CBR. Everything below is measured, not assumed.

**Two structural surprises, both of which M2 must handle:**

| Finding | Count | Consequence |
| --- | --- | --- |
| Unpacked EPUBs stored as **directories** (`Drive.epub/`, iTunes exports with `iTunesMetadata.plist`) | 262 | Invisible today — `BookLibrary` lists files only. foliate-js *can* read unpacked directories. |
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
  missing_since INTEGER                    -- vanished; row never auto-deleted
);

CREATE TABLE metadata (
  book_id          INTEGER PRIMARY KEY REFERENCES book(id) ON DELETE CASCADE,
  title            TEXT,
  subtitle         TEXT,
  title_sort       TEXT,     -- precomputed, see §7
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
  color      TEXT,
  created_at INTEGER NOT NULL
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
| M3 | Search (FTS5), sorting, series, shelves | ~3 days |
| M4 | Book detail: synopsis, About the author, Also published | ~1 week |
| M5 | Enrichment: providers, scoring, BISAC genre map, review queue | 2–3 weeks |
| M6 | Annotations; then MOBI/AZW3 (largely free); PDF as a separate track | — |

M0 existed to de-risk the two things that could have invalidated the plan: the engine rendering correctly, and CSP not breaking it. **Both are now settled facts** — see §3.1.

## 9. Decisions — settled

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
epub_index.py       LibraryIndexer, IndexReport, CoverStore
epub_browse.py      BrowseQueries — all browse SQL, indexed ORDER BY only
epub_search.py      SearchIndex, SearchQueries — FTS5, grouped results
static/browse.js    Browser — mode strip, letter bar, drill-down
epub_db.py          Database, Migration, MigrationRunner — forward-only
epub_reading.py     ReadingState, ReadingStateStore — position, server-side
static/settings.js  ReaderSettings — display prefs, per device
static/toc.js       TableOfContents
run.sh              start / stop / restart / status / scan
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
