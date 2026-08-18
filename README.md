# epub-reader

A browser-based e-book library and reader for your own collection.
Browse, search and read from a Mac, an iPad or a phone, with your place
in each book kept on the server so every device agrees.

Built as a sibling to a personal DLNA gateway: FastAPI, SQLite and a
vanilla front end with no build step. Rendering is
[foliate-js](https://github.com/johnfactotum/foliate-js) (MIT), pinned as
a submodule.

**Status:** works and is in daily use on a library of ~2,200 books, but it
is a personal project rather than a product.

## What it reads

**EPUB, MOBI, AZW3, PRC, PDF and CBZ** — including EPUBs that were never
zipped and are stored as directories, which are zipped on the way out so
nothing downstream needs to know. Only **CBR** stays out: it is RAR, and
nothing in the stack reads RAR.

Three rendering models sit behind those six: reflowed text, rasterised
pages (PDF, via pdf.js), and images page by page (CBZ) — so the
typography controls apply to the first and not the other two.

**Books the shop locked are excluded**, not listed as titles that fail:
50 here are Apple Books purchases whose every document is encrypted, and
no reader outside Apple Books can open them. Encrypted *fonts* are a
different thing entirely and stay — 8 books here obfuscate their fonts,
the Earthsea novels among them, and they read perfectly.

## What it does

- **Browse** by Titles, Authors, Series, Genres, Years or Shelves, with a
  letter bar and drill-down.
- **Search** across titles, authors, series and genres (SQLite FTS5, with
  diacritics folded — `le carre` finds *le Carré*).
- **Read** with four themes, adjustable type, paginated or scrolled flow,
  and a table of contents. On a phone or an iPad the reader takes the
  whole screen, and an **Aa** button brings the panels in over the page.
- **Highlight and annotate** — four colours, notes, and a Highlights
  panel that jumps to the passage. Shared across devices, like your place.
- **Shelve from the list** — make a shelf, throw one away, and put a book
  on or take it off without opening it.
- **Mark a book read** — from the list or from its detail page, and it
  moves from *Reading now* to *Finished*, keeping your place. Books you
  finished years ago on something else can say so too.
- **Remembers your place** server-side, keyed by content hash, so
  renaming or moving a file does not lose it.
- **Shelves** you make, plus *Reading now* and *Finished*, which are
  derived from reading state rather than maintained by hand — so nothing
  puts a book on one, and the way off is to say you have finished it, or
  stopped.
- Indexes ~2,200 books in about 7 seconds, and re-scans in under a second.

## Tidying a library nobody catalogued

A collection assembled over thirty years carries other people's filing.
Every pass below is **dry-run by default**, **database-only**, and
reversible. **No book file is ever renamed, moved, written to or
deleted** — the library is opened read-only.

```sh
./run.sh repair             # check authors and titles, change nothing
./run.sh repair --apply     # ...and fix what it can prove
./run.sh dedupe [--apply]   # one row per work, spares kept and restorable
./run.sh series [--apply]   # read series from filenames
./run.sh covers [--apply]   # a cover for books showing a lettered tile
./run.sh covers --online    # ...and ask a provider for the rest, slowly
./run.sh forget [--apply]   # remove books whose files are gone
./run.sh refresh [--max N]  # re-ask about the oldest author data
```

**Covers come from the book's own first page**, but only where that page
*is* a cover — one image covering it, with no text over it. A title page
is a worse tile than the letter it would replace. That is 144 of this
library's PDFs; the audit that followed found 14 title pages among 159,
and `DELETE /api/books/{id}/cover` is how you say so. What you drop stays
dropped.

For the books whose files hold no image at all, `--online` asks Open
Library — **one request a minute**, so a full sweep is an overnight job
rather than a ten-minute one, and it is resumable. Each book is asked
about once ever, whether or not there was an answer. About 70% get an
offer and roughly three in four of those are the right book, so this
trades a few wrong covers for several hundred right ones; a wrong one
goes with the same endpoint.

A scan **notices** a deletion but never acts on one: the row is marked
missing and kept, so a vanished book leaves the shelves at once while
your place in it survives an unplugged drive. `forget` is the deliberate
step, and it refuses when most of the library is missing — that is a
disk that is not mounted, not a library you emptied.

Where nothing automatic can help, you can say so yourself.
`PUT /api/books/{id}/authors` fixes a file that is confidently wrong
about its own author, and `PUT /api/books/{id}/series` files a book no
rule could place — *The Light of All That Falls* is book three of the
Licanius Trilogy, and neither its file nor its name says so. What you
say is locked against every future scan.

What that found here: 607 authors became 576 (misspellings and spacing);
193 books filed under their own title got a real author; 386 titles that
were really filenames were corrected; 289 spare copies are now one row
each; 297 books were filed under 80 series; and 155 series exist where
there had been 39.

Everything a provider says is cached, so an author is looked up **once,
ever**. A weekly launchd job re-asks about the ten oldest, which walks
the whole library in about forty weeks without ever looking like a bulk
sweep — Open Library allows 3 requests/second to a client that
identifies itself, and asks that its APIs not be used as a bulk backend.

## Requirements

Python 3.12+, Node (for the tests only), and a folder of books. Nothing
is ever written to your books.

## Setup

```sh
git clone --recurse-submodules <this repo>
cd epub-reader
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

cp .env.example .env          # then set LIBRARY_ROOT
./run.sh start                # scans the library, then serves on :8770
```

`./run.sh` also takes `stop`, `restart`, `status` and `scan`, and
`start --foreground` to watch it run. Every start rescans, so books
dropped into the library folder are picked up without a separate step.

**All configuration lives in `.env`** — ports, paths, TLS certificates,
request pacing and any API keys. Nothing is hardcoded and there is no
second config file. `.env` is gitignored; `.env.example` documents every
key.

### Trying it without a library

```sh
.venv/bin/python tools/make_test_library.py   # writes .test-library/ and .env.test
./run.sh --env .env.test start
```

That builds a handful of synthetic books — one of each format, and
deliberately awkward ones: a book whose whole text is a single huge
section, one with a comma in its internal filenames, one stored as a
directory, and an EPUB carrying hostile JavaScript. Every one of those
broke something real.

### Serving over TLS

Set `TAILSCALE_CERT_HOST`, and either put `<host>.crt` and `<host>.key`
next to the `.env` file or point `TLS_CERT_FILE` / `TLS_KEY_FILE` at
them. A missing certificate is reported plainly rather than guessed at.

## Tests

```sh
.venv/bin/python -m pytest    # code
npx playwright test           # UI, against Chromium and WebKit
```

The UI suite runs against **both engines on purpose**: this is mostly
read in Safari, and a Chromium-only suite once stayed green while books
failed to open in Safari entirely. It also runs serially — reading
position and highlights are shared server-side by design, so parallel
tests would race over real application state. No test touches the
network: the fetcher is injected and refuses.

## Security

EPUBs can contain JavaScript, and foliate-js states plainly that iframe
sandboxing cannot contain it. A Content Security Policy blocking all
non-`self` scripts is therefore mandatory and ships by default. The test
suite proves the containment by rendering a hostile EPUB carrying inline
and external scripts and asserting neither runs — and that test is itself
verified by weakening the policy and watching it fail.

PDF support needed no relaxation of that policy: pdf.js runs its parser
in a Web Worker, and a same-origin worker is already allowed. A test
watches the console for refusals while a PDF opens, so if that stops
being true it is a failing test rather than a blank page.

## Design notes

`CLAUDE.md` and `design.html` carry the full design, the decisions behind
it, and what a real 2,200-book library taught us — including several
defects in the rendering engine, and a few of our own, that no synthetic
fixture would have produced.

## Licence

MIT — see [LICENSE](LICENSE).

Vendored and pinned as a submodule:
[foliate-js](https://github.com/johnfactotum/foliate-js) (MIT), which in
turn vendors zip.js (BSD-3), fflate (MIT) and PDF.js (Apache 2.0).
