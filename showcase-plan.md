# architecture.html → showcase

Written 2026-08-20 for the next session. `architecture.html` exists and is
correct, but it was built as a *utilitarian document* — a maintainer's
reference. The decision is to make it a **showcase**.

Everything below is sequenced. Each phase ends somewhere you could stop.

---

## 0 · What changes, in one line

The reader stops being *me, six months from now* and becomes **a stranger
who found the repo and will give it ninety seconds**. The page's job is no
longer "describe the system accurately" — it already does that — it is
**"earn the next ten minutes."**

Accuracy is not traded away for this. Every number stays measured; what
changes is what leads, what is shown rather than told, and how much the
typography is allowed to carry.

## 1 · Decide first: where is it seen? (10 min, blocking)

A showcase nobody reaches is a document. Three options, not exclusive:

| | Reach | Cost |
| --- | --- | --- |
| Local file only | whoever clones and opens it | none |
| **Linked from README.md** | every visitor to the repo | one line |
| **GitHub Pages on the public repo** | a URL you can send | `Settings → Pages`, root or `/docs` |

**Recommended: both of the bold ones.** Pages is the user's own hosting,
free, and nothing to do with claude.ai. If Pages is on, the page needs to
live where Pages serves from — decide this *before* phase 4, because it
changes relative asset paths for the screenshots.

## 2 · The hero is the thesis (90 min)

The current page opens with a stat strip. Correct, forgettable.

**Open with the thing this project is actually about**: a filename that is
obvious to a person and opaque to a machine, taken apart into facts. Use
the real one from §8.16 — it is measured, it is documented, and it works:

```
Anthony, Piers - Xanth 12 - Man From Mundania (with Nobody Real).pdf
```

resolving, after one restart, into:

```
Man From Mundania  ·  Piers Anthony, Nobody Real  ·  Xanth #12
```

Build it as a **staged reveal on load**: the raw filename, then the four
spans colouring in as each fact is claimed (author / series / index /
co-author), then the resolved book. One sequence, ~1.6 s, `prefers-reduced-motion`
drops straight to the resolved state. No scroll-jacking, no repeat on
re-scroll.

Why this and not a diagram: it is the only thing on the page a
non-programmer also understands, and it states the project's real claim —
*the metadata is the product* — without a sentence of throat-clearing.

## 3 · Make the dependency graph live (2 h)

The layer diagram is the strongest thing already on the page and it is
inert. Make it the **signature interaction**:

- Hover or tap a module chip → its dependencies below and dependents above
  light amber; everything else drops to ~25% opacity.
- The chip shows fan-in already; on focus, also show *what* it imports.
- Keyboard reachable: chips become `<button>`, arrow keys move along a
  layer, Escape clears.

Data is already computed — the depth/fan-in script from this session.
**Re-run it and embed the result as a JSON literal**, do not hand-copy it;
a graph that drifts from the code is worse than no graph. Regenerating it
should be one command.

Deliberately **not** a force-directed hairball. The layering *is* the
finding (7 layers, 39 modules, 0 cycles, the two smallest modules most
depended on). A physics sim would hide it and would look like every other
generated architecture page.

## 4 · Show the app (2 h)

**A showcase for a reader with no picture of the reader is a brochure.**
There are zero screenshots in the repo today.

Capture with Playwright against the real library — the suite already
drives the app, so this is a script, not a new dependency:

| Shot | Viewport | Shows |
| --- | --- | --- |
| Browse, authors drilled in | 1440×900 | three columns, letter bar, covers |
| Book detail | 1440×900 | series, synopsis, Also published |
| The reader, mid-chapter | 810×1080 (iPad 9) | typography controls, one screen |
| Reader with a highlight | 393×852 (iPhone 15) | the panel sheet over the page |

Notes: the three viewports are the ones §10.8 already pins, so they are
the honest ones. Save as PNG under `docs/img/`, keep them under ~300 kB
each, and reference them relatively so Pages and a local open both work.
**Check what is on screen before committing** — these are real book
titles from a real library, which is fine, but it should be a choice.

## 5 · Typography and rhythm (60 min)

The palette does not change — dlna-gateway's tokens are the family
identity and `design.html` commits to them. What changes is **how much the
type is allowed to do**:

- Hero display size up (Syne 800, ~56–64px desktop, clamped), tight
  leading, `text-wrap: balance`.
- A real scale, stated once as tokens and obeyed: currently sizes are
  ad-hoc.
- More air between sections; the current 56px top margin is document
  spacing, not showcase spacing.
- One accent, still amber. If something needs a second colour, it needs
  a different solution.
- Motion budget: **the hero sequence and the graph hover, nothing else.**
  Scattered scroll-reveals are the surest tell of a generated page.

## 6 · §8 "Where it strains" — reframe, do not delete (30 min)

I flagged cutting this and the answer was "showcase". **Recommendation:
keep it, retitled and moved last**, as CLAUDE.md already frames it:

> *Constraints the builder accepted. Not a to-do list. Each was weighed,
> and the cost of removing it judged higher than the cost of keeping it.*

For the audience this page is now aimed at, a page with no limits reads as
marketing and a page that **prices** its limits reads as engineering. Each
row already carries the cost of removal, which is exactly what makes it
read as judgement rather than backlog.

If it still feels wrong tomorrow, cut it — but cut it whole. Half a
limitations section is worse than either.

## 7 · Verify (30 min)

- [ ] Every number re-measured against source and `books.db` — the page
      claims 39 modules, 52 routes, 7 layers, 0 cycles, 2,374 books.
- [ ] HTML parses with no unclosed tags (the parser check from this session).
- [ ] Renders at 1440, 810 and 393; no horizontal body scroll at any of them.
- [ ] **Safari**, not just Chrome — it is the browser this is read in, and
      §10.5 is the standing lesson about testing one engine.
- [ ] `prefers-reduced-motion` kills both animations.
- [ ] Keyboard: every interactive chip focusable with a visible ring.
- [ ] No external requests except Google Fonts.
- [ ] Opens correctly from `file://` *and* from Pages, if Pages is on.

## 8 · Land it

- `git add architecture.html docs/img/` — currently untracked.
- README gets one line linking to it, near the top.
- CLAUDE.md §10 rule 6 says docs are updated in the same commit as code.
  This is a doc-only commit, so the rule is satisfied by keeping
  `design.html`, `CLAUDE.md` and this page consistent with each other.

---

## Traps specific to this page

1. **Don't let it drift into the generated look.** The tokens protect
   against most of it, but the failure modes are: a giant centred hero, an
   emoji per section, numbered `01 / 02 / 03` markers that encode nothing,
   and rounded cards with an accent rail. The existing numbering *does*
   encode reading order, so it stays.
2. **The graph must stay honest.** Regenerate, never hand-edit.
3. **Two documents, one voice.** `design.html` is the design log;
   `architecture.html` is the showcase. They share chrome and palette and
   must not contradict each other on any number.
4. **Half a day, not a day.** Phases 2, 3 and 4 are the showcase. 5 is
   polish. If time runs short, 4 (screenshots) is worth more than 5.
