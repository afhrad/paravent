# Design Handoff

Written 2026-09-01, brought up to date 2026-11-14. Everything needed to pick the design work
up cold.

Read this first, then [`17-paravent-today.html`](17-paravent-today.html) for what Paravent looks
like now, and [`../documentation/DESIGN.md`](../documentation/DESIGN.md) for the written spec.

---

## Where things stand

The design came before the app. Pages 01–16 were drawn in September 2026; the app was then
built from them between October and November and diverged where building taught something.
**The app is now the source of truth for how a screen looks.** The pages keep the reasoning,
and each opens with a dated *As built* note saying what changed. Page 17 shows the real screens.

| Page | Status |
| --- | --- |
| 01 Principles, 08 Tokens, 09 Scrollbars | Hold. Colour values, contrast and scrollbars are what the app serves. |
| 06 Tag renderer, 12 Tooltips | The rules and the interaction contract hold; what a link leads to changed (a preview, then the reference page; no drawer, no in-place actions). |
| 03 Command palette | Built as drawn, plus world scoping, picker and insert modes. |
| 02 Combat tracker, 04 Player on mobile, 16 Character sheet | Built differently in detail: no rail, no drawer, no dice screen; tokens, hit point bars as row backgrounds, statblock attacks with Hit/Miss, the shared log. |
| 05 Wizard, 13 Character generation | The engine and its rules hold; nine steps, a shop, the books' descriptions on the cards. |
| 07 Icons, 10 Responsive, 11 Implementation | Corrected in place: no python-lucide, no rail, no Tailwind. |
| 14, 15 Demos | **Historical prototypes.** Their idea holds, their screens do not. Cut to SRD content. |
| 17 Paravent today | The screens as built, from the website's screenshots. |

---

## The decisions that still bind

- **Visual direction: "Neutral pro (light + dark)."** Chosen over a "dark arcane" and a "faction
  colour-coded" option. Neutrals, full light/dark parity, accent reserved for state. This is
  Paravent's own look and the default. Themes (below) may restyle a table, never the rules of
  the default.
- **Icon library: Lucide only.** Game semantics ride on **colour and label**, not pictographs —
  a deliberate constraint. Do not add a second icon library without asking. The one glyph of our
  own is the logo.
- **Multiuser with roles** (resolved 2026-09-01): a gamemaster and players, one codebase, the role
  choosing the composition and guarding the routes (403).
- **Translation is a text-only overlay** (resolved 2026-09-14) on the English records; a German
  reader sees glossary names with the English beneath, and nothing waits for a translated corpus.
- **No battle maps or grid play.** Paravent is not a virtual tabletop; exporting tokens to one is
  fine.

## Decisions that changed when the app was built

| Designed | Built | Where it is recorded |
| --- | --- | --- |
| Tailwind 3 + DaisyUI themes `paravent-light`/`-dark` | The token CSS served as-is: `static/css/tokens.css` and `components.css` (the files in `shared/`), `app.css`, `sheet.css`. No build step. | TECHSTACK.md, "Styling, as built (2026-10-15)" |
| python-lucide at runtime | A vendored sprite, `static/icons.svg` (67 Lucide 0.x glyphs and the logo), inlined once per page by `app/ui/icons.py`; unknown names raise, always | DESIGN.md §5 |
| A 236px rail, a 56px icon rail, a bottom tab bar | A sticky top bar for every role; tabs at 640px; a bottom bar for players on a phone | DESIGN.md §3, page 10 |
| A reference drawer (desktop) or sheet (phone) | Previews (a card with a mouse, a bottom sheet on touch) and full reference pages; the tracker keeps a side panel | DESIGN.md §3, §7 |
| Light / System / Dark buttons | Sun / moon in the user menu; the device decides until a reader chooses | page 01 note |
| Initials avatars | Round tokens: own picture, else one from the image folder, else the kind's disc | TOKENS.md |
| A dice screen, per-screen logs, a watcher toast | Rolls from the rows and chips, one shared log with toasts and dice sounds | PROTOCOL.md |
| One look | Exchangeable theme folders; Paravent's own look by default, **Tome** shipped | THEMES.md |
| System fonts named Inter Tight, Source Sans 3, JetBrains Mono | No font files ship with the default look (no network requests); faces fall back to the system's. Tome brings OFL fonts. | TECHSTACK.md |

---

## Findings that came from real data, not assumption

Measured or verified; do not contradict them without re-checking.

### The tag corpus

Scanned at the time: **499 JSON files, 109,857,630 bytes, 110 distinct tags, 249,546
occurrences.** The catalogue is `data/library/tags/` (`TAGS.md`, `5etools_tags.json`,
`scan_tags.py`), moved there from `data/reference/` on 2026-11-02.

- **8 tags cover 65.4%** of all occurrences: `creature`, `spell`, `item`, `dc`, `dice`, `area`,
  `damage`, `condition`.
- **Tags nest** (`{@footnote text|{@note inner}}`): the parser is recursive (`app/ui/tags.py`).
- **Slot 3 is a display override** (`{@creature troll||trolls}`), and the glossary's name comes
  before it for a translated reader. Not for every tag: `{@area}`, `{@book}`, `{@deity}` and
  `{@card}` use their slots differently.
- **`{@h}` and the other shorthand tags** (12,232 uses) expand to labels in the reader's language.
- **`{@color text|#ff0000}`** is remapped to the nearest semantic token; raw hex breaks dark mode.

### Icons

- Lucide ships **`0.x` versions; there is no 1.x.** An early draft said "1.39.0"; that was wrong
  and is corrected everywhere.
- **Not in Lucide:** `treasure-chest`, `chest`, `d20`, singular `dice`, `campfire`. Use `vault`,
  `dices`, `flame-kindling`.
- **Deprecated aliases** — use the canonical name: `alert-triangle`→`triangle-alert`,
  `edit`→`pencil`, `user-circle`→`circle-user`, `wand-2`→`wand-sparkles`, `globe-2`→`globe`.
- **The navigation departs from the semantic map** on purpose: Combat is `zap`, Encounters
  `skull`, the Table `swords` (`app/ui/nav.py`).

### Contrast

- `--text-3` once failed 4.5:1 on every surface; fixed to `#646C79` light / `#949DAB` dark.
- **`--accent-solid`** (`#4560DC`) carries accent as a fill: white on it is 5.30:1.
- **All 18 palette pairs pass 4.5:1 in both modes** of the default look, re-computed
  2026-11-14 (worst: `--text-3` on `--surface-3`, 4.64 light / 5.27 dark). The role colours
  (`--role-gm`, `--role-player`) are marks, held to 3:1. Tome's palette is outside this claim.

### Scrollbars

- The root cause of system-looking scrollbars was a **missing `color-scheme`**, not thumb styling.
- Thumb ramp: rest below 3:1 (it recedes), hover above 3:1 against `--surface-3`.

---

## Things that are true and easy to get wrong

- **Hover and touch never share a code path.** `matchMedia('(hover: hover) and (pointer: fine)')`
  — capability, not width. An iPad in landscape has no hover; a narrow desktop window has one.
  `static/js/tooltips.js` decides by it alone.
- **Touch browsers replay a synthetic `mouseenter` after a tap.** Listen to `pointerenter` /
  `pointerleave` and check `e.pointerType === 'mouse'`.
- **Components never hardcode a colour.** Every value is a token defined in all three theme blocks
  (bare `:root`, the `prefers-color-scheme` query, `[data-theme="dark"]`). A theme overrides
  tokens, so a hardcoded colour is also one no theme can reach.
- **The sticky top bar wraps on a phone.** Anything that scrolls to an anchor must leave room for
  it: the page's scroll padding reads `--bar-h`, which `ui.js` measures.
- **Public repo.** No pictures, prose or tables of the rulebooks beyond the System Reference
  Documents in these pages, the docs or the website's screenshots (NOTICE.md).

---

## How the pages are made

- **Pages 01–11 are generated.** Edit `shared/_source-kit.html`, then run
  `python3 design-reference/shared/split.py`. A generated page edited directly is overwritten.
- **Pages 12–17 are kept by hand.** `12-tooltips.html` and `shared/_source-tooltips.html` are the
  same content; keep them in step.
- **`shared/tokens.css` and `shared/components.css` are the app's files** (`static/css/`). When a
  token changes in the app, copy it here and re-run `split.py`.
- **Page 17's pictures** are the website's (`website/assets/img/screens/`, made by
  `website/shots.py` from a demo table of SRD creatures and our own texts). New screenshots there
  update page 17 without touching it.

## How to resume

> Read `design-reference/HANDOFF.md`, then page 17 and `DESIGN.md`. Continue with <task>.

A design change now starts from the built screen (page 17, or the app itself) and ends in the
app's code and CSS; update the page's *As built* note or page 17 when a screen changes visibly.
