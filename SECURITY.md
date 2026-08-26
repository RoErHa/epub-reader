# Security policy

## The threat model, stated plainly

**This reader is built for one household on a home LAN or a private
tailnet. It is not hardened for the public internet, and it should never
be port-forwarded.**

Everything below follows from that. The whole `/api/*` surface is
**deliberately unauthenticated** and nothing here uses cookies or
sessions. The access control is the network: if you can reach the port,
you can use it, in the same way anyone in the room can pick a book off
the shelf. `EPUB_BIND` therefore defaults to loopback, and widening it
onto a tailnet is a deliberate edit with the reason written beside the
setting.

So **"the API has no authentication" is not a vulnerability report** — it
is the documented design. What follows is what *is*.

## The one that shapes the whole design

**EPUBs can contain JavaScript, and the rendering engine cannot contain
it.** foliate-js says so outright: book resources are served same-origin
as `blob:` URLs and a WebKit bug forces `allow-scripts`, so the iframe
sandbox is not a defence. **A Content Security Policy is the
containment**, which makes `epub_security.py` security-critical rather
than boilerplate.

That test is verified *negatively* — the policy is weakened until the
containment test fails — because a security test that passes for the
wrong reason is worse than none. Anything that lets a book's own script
run is the most serious report this project can receive.

## In scope

Anything that lets a book file, or a peer who can reach the port, do more
than read:

- **A book that executes.** Script from inside an EPUB reaching the page,
  escaping the CSP, or reading anything outside its own document.
- **Reading files that were never meant to be served** — escaping
  `LIBRARY_ROOT` or the cover cache, by path, symlink, or a containment
  check being wrong.
- **Anything a book file can do to the host.** Every format here is
  attacker-controlled data if you ever download a book: EPUB and CBZ are
  zips, MOBI/AZW3/PRC are parsed by hand out of PalmDB records, PDFs go
  through pdfium, and CBR is unpacked by `bsdtar`. Zip-slip, a parser
  crash that is more than a crash, or a path that climbs out of an
  archive are all in scope.
- **Making the reader act for somebody else's page.** State-changing
  requests check `Origin` precisely because CORS withholds the reply and
  not the act; a way around that check is in scope.
- **Writing to the library.** The service is read-only over
  `LIBRARY_ROOT` by design and must never move, rename or modify a book.
- **Injection into the app.** Titles, authors and series come from files
  and from providers and are untrusted; anything that gets them executing
  in the page is in scope.
- **Resource exhaustion a single peer can trigger** — the repacking paths
  for unpacked EPUBs and RAR comics hold data in memory, and the largest
  comic here is 315 MB.
- **Leaking data off the machine** beyond what the README documents.

## Out of scope

- The unauthenticated API, and the consequence of exposing it yourself.
- **DRM.** Books the shop locked are excluded at scan time. Reading them
  is not a feature request this project will take, and a report that we
  *cannot* open them is working as intended.
- Findings that require an attacker who already has a shell on the host.
- The ten 2003-vintage RAR comics libarchive refuses. That is a
  documented limitation, not a bug.

## Reporting

Please report privately, via **GitHub's private vulnerability
reporting**: the repository's **Security** tab → **Report a
vulnerability**. That opens a draft advisory only you and the maintainer
can see.

Please do not open a public issue for something exploitable.

Useful in a report: what an attacker controls (a book file? a page in the
same browser? a peer on the tailnet?), the input that does it, and what
you got back. A `curl` or a fixture EPUB that shows the behaviour is
worth more than a scanner's category name.

**This is a one-person hobby project.** Expect a reply in days rather
than hours, and no bounty. Fixes land with a regression test that is
verified to fail on the unfixed code, and the reasoning goes into
`CLAUDE.md` so it does not get quietly undone later.

## What has already been looked at

A review in August 2026 went over the published surface before this
repository was made public. It found no way to execute code or read
outside the library, and two things worth changing, both since fixed:

| Area | What was found |
|---|---|
| Cross-site requests | State-changing endpoints could be driven from another origin — a bodiless POST is a CORS "simple request", so the reply was withheld but the act was not. `Origin` is now checked on every `POST`/`PUT`/`PATCH`/`DELETE`. |
| Bind address | `.env.example` shipped `0.0.0.0`, so the documented setup published the library to the LAN. It defaults to loopback now. |

What was checked and found sound: CSP containment of scripted EPUBs
(verified by breaking it); path containment in `BookLibrary.resolve` and
the cover route, both anchored with `relative_to`; books addressed by
integer id rather than path; no SQL built from user values, with the
identifiers that *are* interpolated coming from fixed allowlists; every
`innerHTML` in the frontend a static skeleton with untrusted text set via
`textContent`; the one subprocess invoked as a list with the archive path
bound to `-f`; and no write path to `LIBRARY_ROOT` at all.

Regression tests for the two fixes live in `tests/test_security.py`
(`TestCrossSiteRequests`) and `tests/test_config.py`. The CSP
containment tests are in `tests/test_security.py` and
`tests/frontend/reader.spec.js`.
