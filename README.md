# epub-reader

A browser-based e-book library and reader for your own EPUB collection.
Browse, search and read from a Mac, an iPad or a phone, with your place
in each book kept on the server so every device agrees.

Built as a sibling to a personal DLNA gateway: FastAPI, SQLite and a
vanilla front end with no build step. Rendering is
[foliate-js](https://github.com/johnfactotum/foliate-js) (MIT), pinned as
a submodule.

**Status:** works and is in daily use, but it is a personal project
rather than a product. EPUB only for now — PDF is a different rendering
model and CBR is RAR, so listing them would promise what it cannot
deliver.

## What it does

- **Browse** by Titles, Authors, Series, Genres, Years or Shelves, with a
  letter bar and drill-down.
- **Search** across titles, authors, series and genres (SQLite FTS5, with
  diacritics folded — `le carre` finds *le Carré*).
- **Read** with four themes, adjustable type, paginated or scrolled flow,
  and a table of contents.
- **Remembers your place** server-side, keyed by content hash, so
  renaming or moving a file does not lose it.
- **Shelves** you make, plus *Reading now* and *Finished*, which are
  derived from reading state rather than maintained by hand.
- Indexes ~1,300 books in about 8 seconds, and re-scans in under a second.

## Requirements

Python 3.12+, Node (for the tests only), and a folder of EPUBs. Nothing
is ever written to your books — the library is opened read-only.

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

**All configuration lives in `.env`** — ports, paths, TLS certificates
and any API keys. Nothing is hardcoded and there is no second config
file. `.env` is gitignored; `.env.example` documents every key.

### Trying it without a library

```sh
.venv/bin/python tools/make_test_library.py   # writes .test-library/ and .env.test
./run.sh --env .env.test start
```

That builds a handful of synthetic EPUBs — including deliberately awkward
ones: a book whose whole text is a single huge section, and one with a
comma in its internal filenames. Both used to break the reader.

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
position is shared server-side by design, so parallel tests would race
over real application state.

## Security

EPUBs can contain JavaScript, and foliate-js states plainly that iframe
sandboxing cannot contain it. A Content Security Policy blocking all
non-`self` scripts is therefore mandatory and ships by default. The test
suite proves the containment by rendering a hostile EPUB carrying inline
and external scripts and asserting neither runs — and that test is itself
verified by weakening the policy and watching it fail.

## Design notes

`CLAUDE.md` and `design.html` carry the full design, the decisions behind
it, and what a real 1,300-book library taught us — including four defects
in the rendering engine that no synthetic fixture would have produced.

## Licence

MIT — see [LICENSE](LICENSE).

Vendored and pinned as a submodule:
[foliate-js](https://github.com/johnfactotum/foliate-js) (MIT), which in
turn vendors zip.js (BSD-3), fflate (MIT) and PDF.js (Apache 2.0).
