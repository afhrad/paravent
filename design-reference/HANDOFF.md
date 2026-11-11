# Design Session Handoff

Written 2026-09-01. Everything needed to pick this work up cold.

Read this first, then [`../documentation/DESIGN.md`](../documentation/DESIGN.md) for the full spec and
[`index.html`](index.html) for what it looks like.

---

## What was asked, in order

1. Design a modern D&D 5e UI — mobile and desktop, popups, searchable comboboxes, reference
   windows, DaisyUI + Tailwind 3, deliberately *not* rulebook-styled.
2. Pick a readable icon library; prefix tags with icons.
3. Scan the real 5eTools corpus for every renderable tag and map icons to them.
4. Build a clickable tooltip prototype for every underlined tag type.
5. Make tooltips hover-only on desktop, touch-only on mobile.
6. Redesign scrollbars to follow the theme.
7. Split the designs into a `design-reference/` folder.

## Two decisions the user made explicitly

- **Visual direction: "Neutral pro (light + dark)."** Chosen over a "dark arcane" option and a
  "faction color-coded" option. Notion/Linear-like neutrals, full light+dark parity, accent
  reserved for state.
- **Icon library: Lucide only.** Chosen over a Lucide + game-icons.net split. This means game
  semantics are carried by **color and label**, not pictographs — a deliberate constraint, not
  an oversight. Do not add a second icon library without asking.

---

## Deliverables

| Path | What it is |
| --- | --- |
| [`../documentation/DESIGN.md`](../documentation/DESIGN.md) | The written spec. 9 sections. Source of truth. |
| [`index.html`](index.html) | Visual reference, 12 standalone pages |
| [`12-tooltips.html`](12-tooltips.html) | Interactive prototype — real hover/tap/keyboard behaviour |
| [`../data/reference/TAGS.md`](../data/reference/TAGS.md) | All 110 5eTools tags, categorised, icon-mapped |
| [`../data/reference/5etools_tags.json`](../data/reference/5etools_tags.json) | Machine-readable; the renderer imports this |
| [`../data/reference/scan_tags.py`](../data/reference/scan_tags.py) | Regenerates the catalogue from the corpus |

Published copies (same content, shareable):
[interface kit](https://claude.ai/code/artifact/ab903338-4dab-477f-ac41-913985686caf) ·
[tooltip prototype](https://claude.ai/code/artifact/c8ad1412-03a2-4a3c-bf78-06c2bddab2ce)

---

## Findings that came from real data, not assumption

These were measured or verified. Do not re-derive them; do not contradict them without
re-checking.

### The tag corpus

Scanned `../../GameMaster Tool/5etools-data`: **499 JSON files, 109,857,630 bytes, 110 distinct
tags, 249,546 occurrences.**

- **8 tags cover 65.4%** of all occurrences: `creature` (54,753), `spell` (25,118), `item`
  (19,666), `dc` (15,462), `dice` (14,254), `area` (13,494), `damage` (11,645), `condition`
  (11,634). Build these first.
- **Tags nest.** `{@footnote text|{@note inner}}` occurs. The parser must be recursive; a flat
  regex will silently corrupt output.
- **Slot 3 is a display override**, used heavily for inflection: `{@creature troll||trolls}`
  means *empty source, plural display*. Always render slot 3 when present.
- **`{@h}` is 5,468 uses with no payload** — it expands to *"Hit:"*. Twelve such shorthand tags
  total 12,232 uses. Missing `{@h}` breaks every attack line in the bestiary.
- **`{@color text|#ff0000}`** (104 uses) hardcodes hex. Remap to the nearest semantic token —
  raw hex breaks dark mode.

### Icons

- Lucide ships **`0.x` versions** (current ~`0.539.0`). **There is no 1.x.** An earlier draft of
  DESIGN.md said "1.39.0"; that was wrong and is corrected.
- **Does not exist in Lucide:** `treasure-chest`, `chest`, `d20`, singular `dice`, `campfire`.
  Substitutes in use: `vault`, `dices`, `flame-kindling`.
- **Deprecated aliases** that still resolve but will break on a major — use the canonical name:
  `alert-triangle`→`triangle-alert`, `edit`→`pencil`, `user-circle`→`circle-user`,
  `wand-2`→`wand-sparkles`, `globe-2`→`globe`.
- All 45 icon names in the semantic map were verified against the shipped icon list.
- `python-lucide` on PyPI returns raw SVG strings from a bundled SQLite index — no Node, no npm,
  no network at runtime. This is why Lucide fits a "no build tooling" stack.

### Contrast

A comment on the published artifact ("Bad contrast") surfaced a **systemic** failure, not a
local one. The flagged button was fine; the token behind it was not.

- `--text-3` was failing **4.5:1 on every surface in both themes** (2.97–4.21:1). It carries
  eyebrows, ability labels, combatant metadata, search subtitles, captions.
- Fixed to `#646C79` light / `#949DAB` dark → **4.64–6.96:1** everywhere.
- Added **`--accent-solid`** (`#4560DC`): accent-as-a-fill needs more depth than
  accent-as-a-line. White on `#4F6BED` was 4.51:1 — passing but with no margin. Now 5.30:1.
- **All 18 palette pairs now pass 4.5:1 in both themes.** Re-run the check when a token changes.

### Scrollbars

- The root cause of "system-looking" scrollbars was a **missing `color-scheme` declaration**, not
  missing thumb styling. Without it the browser paints light native scrollbars on a dark page
  regardless of what else you style.
- Thumb ramp is deliberate: **rest sits below 3:1** (it should recede), **hover clears 3:1**
  (WCAG 1.4.11 for a non-text control), measured against `--surface-3`, the worst surface.

---

## Things that are true and easy to get wrong

- **Hover and touch never share a code path.** Detection is
  `matchMedia('(hover: hover) and (pointer: fine)')` — *capability, not screen width*. Width gets
  two real cases backwards: an iPad in landscape (1024px, no hover) and a narrow desktop window
  (phone-width, has mouse). Do not "simplify" this back to a breakpoint.
- **Touch browsers replay a synthetic `mouseenter` after a tap.** Listeners use
  `pointerenter`/`pointerleave` and check `e.pointerType === 'mouse'` so the replay is ignored.
  Plain mouse events cannot distinguish it.
- **Pages 01–11 are generated.** Edit `shared/_source-kit.html` and run `python3
  shared/split.py`. Editing a generated page directly gets overwritten. Verified idempotent.
- **`12-tooltips.html` is maintained separately** from `shared/_source-tooltips.html` — it carries
  its own behavioural JS rather than the shared demo script.
- **Components never hardcode a color.** Every value comes from a token defined in all three
  theme blocks (bare `:root`, the `prefers-color-scheme` media query, and `[data-theme="dark"]`).
  A color defined only inside a media block renders one theme's text on the other's ground.

---

## Open questions

### 1. Multiuser contradiction — RESOLVED 2026-09-01

Resolved in favour of **multiuser**, the superset: login with a gamemaster/player role, session
persistence, state shared between clients. TECHSTACK.md §7 now says this. The design already
assumed it, so nothing in these pages changes.

### 2. Translation — RESOLVED 2026-09-14, no longer blocked

Earlier drafts said cross-language work was blocked until a German corpus appeared, and pointed at
Foundry VTT files as the only candidate. **Both points are superseded.**

Foundry and other VTT formats are **out of scope** — Paravent works with pure 5eTools data only.
And the dependency is gone: a translation is a **text-only overlay** on the English record
(TRANSLATION.md), so tags in translated prose resolve through the English keyspace. Cross-
referencing works at 0% coverage and at 100%, with no code path waiting on a corpus.

The design in DESIGN.md holds unchanged; it simply no longer needs validating against a
translation that does not exist yet.

---

## Not yet built

> **Stale as of 2026-09-14.** `app/` now exists — models, stores, watcher and read endpoints are
> built (see PROGRESS.md). The UI items below are still accurate and still unbuilt. Two things
> changed underneath them:
>
> - **Data now lives in two roots.** Reference (the 5eTools corpus) is external and read-only;
>   everything authored in Paravent is under `library/`. Paths in these pages that say
>   `compendium/`, `homebrew/` or `gamestate/` predate that split.
> - **The corpus is only half loaded.** 24 of 94 entry keys. Classes, subclasses and all fluff
>   are not in memory yet, which matters for any page previewing them (TECHSTACK.md §6b).

Nearest next steps, roughly in dependency order:

1. **`tailwind.config.js` + DaisyUI theme pair** (`paravent-light` / `paravent-dark`) generated
   from DESIGN.md §2 tokens. Currently the reference pages use hand-written CSS with the same
   token *names*, so the mapping is specified but not written.
2. **`app/ui/icons.py`** — `icon(name, size)` helper wrapping `python-lucide` behind
   `@lru_cache`, emitting `<use>` into one sprite in `base.html`. Unknown name should raise in
   dev, render nothing in prod.
3. **`app/services/tags.py`** — the recursive `{@...}` parser plus a handler table driven by
   `data/reference/5etools_tags.json`. Phase 1 = the 8 tags covering 65%.
4. **`GET /ref/{type}/{slug}/preview`** — the tooltip fragment endpoint. Layouts and timings are
   specified in DESIGN.md §7; the endpoint does not exist.
5. **`base.html` + `combatant_card()`** in htpy — the first real components.

---

## How to resume

> Read `design-reference/HANDOFF.md`, then `DESIGN.md`. Continue with <task>.

The handoff carries the decisions and their reasons; DESIGN.md carries the spec; the HTML pages
carry the visual intent. All three are in the repo — nothing important lives only in the
conversation.
