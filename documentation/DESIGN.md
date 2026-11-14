# Paravent — UI/UX Design

**Visual reference: [`design-reference/index.html`](../design-reference/index.html)** — sixteen
standalone pages, openable straight from the filesystem, and a seventeenth,
[`17-paravent-today.html`](../design-reference/17-paravent-today.html), showing the app as built.
**Resuming this work?** Start with
[`design-reference/HANDOFF.md`](../design-reference/HANDOFF.md). Published copies:
[interface kit](https://claude.ai/code/artifact/ab903338-4dab-477f-ac41-913985686caf) ·
[tooltip prototype](https://claude.ai/code/artifact/c8ad1412-03a2-4a3c-bf78-06c2bddab2ce)

Visual language for Paravent. Styled by **token CSS served as-is**, rendered server-side
via **htpy**, made dynamic with **HTMX**. Direction: neutral-professional, full light/dark
parity — a tool, not a scanned rulebook.

> **As built (2026-11-14).** This document was written on 2026-09-01, before the app existed,
> and planned Tailwind CSS 3 + DaisyUI. The app serves the token CSS instead —
> `static/css/tokens.css`, `components.css`, `app.css` and `sheet.css` — with no build step
> (TECHSTACK.md, "Styling, as built (2026-10-15)"). The look is an **exchangeable theme folder**
> (THEMES.md): Paravent's own look, described here, is the default; **Tome** (parchment, an
> oxblood accent, the OFL fonts Libre Baskerville, Alegreya Sans and Cinzel) is opt-in, for the
> table or for one reader. No font files ship for the default look, so its faces fall back to
> system fonts. The sections below are corrected where the app went another way.

---

## 1. Principles

These resolve later arguments. Each comes from a real constraint in PROJECT.md / TECHSTACK.md
or from how the game is actually played at a table.

1. **Color is information, never decoration.** One accent (indigo) marks interactive and
   selected state. Everything else is semantic: red = damage, green = healing, violet = magic,
   amber = condition. If a color appears, it means something.
2. **The screen is dim and the room is loud.** Body text never below 13px, tap targets never
   below 44px, every changing number monospaced with `tabular-nums` so it does not jitter.
3. **Reference never takes you away.** Looking up a spell mid-turn must not lose your place.
   Cross-references open as a preview — a floating card with a mouse, a bottom sheet on touch —
   and the tracker stays mounted; the full entry is one click further, on its own page.
4. **Two roles, one codebase.** Gamemaster gets density, player gets focus. Same components
   and tokens, different composition and routes.
5. **English is the fallback, always.** German is a second language, not a fork. A missing
   translation renders English with a quiet marker — never an empty field, never a crash.

---

## 2. Tokens

Defined as CSS custom properties in `static/css/tokens.css`, one block per theme mode.
**No component ever contains a theme-conditional color** — components read tokens, tokens carry
the theme.

> **As built (2026-11-14).** There is no `tailwind.config.js` and no DaisyUI theme pair: the
> token file is the stylesheet (§8). The values below are unchanged. `app.css` adds a few tokens
> of its own: `--font-display` (headings, the UI face unless a theme sets one), `--backdrop`
> (behind sheets and dialogs), and `--role-gm` (orange) / `--role-player` (blue), which colour
> the top edge of the bar by role. A theme may add more; Tome adds `--gilt`, `--gilt-weak` and
> `--texture`.

### Neutrals

Grey with a faint blue bias toward the accent, so it reads as chosen rather than default.

| Token | Light | Dark |
| --- | --- | --- |
| `--bg` | `#FFFFFF` | `#0E1013` |
| `--surface` | `#FFFFFF` | `#171A1F` |
| `--surface-2` | `#F7F8FA` | `#1D2127` |
| `--surface-3` | `#EDF0F4` | `#252A31` |
| `--border` | `#E1E5EB` | `#282D35` |
| `--border-strong` | `#CBD2DB` | `#3A414B` |
| `--text` | `#14171C` | `#E9ECF1` |
| `--text-2` | `#545C68` | `#A0A8B4` |
| `--text-3` | `#646C79` | `#949DAB` |

### Semantic

| Token | Light | Dark | Means |
| --- | --- | --- | --- |
| `--accent` | `#4F6BED` | `#7B92F5` | Interactive, selected, active turn. The only non-semantic color. Borders, focus rings, thin marks. |
| `--accent-solid` | `#4560DC` | `#7B92F5` | Accent as a **filled surface** — primary buttons, selected chips. Deeper than `--accent` so white label text clears 4.5:1 with margin. |
| `--dmg` | `#D93A3F` | `#F0656A` | Damage, critical HP, unconscious, destructive actions |
| `--heal` | `#1F8A55` | `#45C083` | Healing, temp HP, advantage, saved state |
| `--magic` | `#8144B8` | `#B678E6` | Spells, slots, concentration, magic items |
| `--cond` | `#B57500` | `#E5A73C` | Conditions, bloodied, warnings, inspiration |

Each has `-weak` (tinted background) and `-text` (contrast-safe foreground) variants.

### Type

| Token | Face | Used for |
| --- | --- | --- |
| `--font-ui` | Inter Tight | Headings, buttons, labels, navigation, creature names |
| `--font-body` | Source Sans 3 | Statblock prose, adventure Markdown, descriptions |
| `--font-mono` | JetBrains Mono | Every number that can change: HP, AC, initiative, dice, modifiers |

### Contrast

Every foreground/background pair in the palette is verified at **WCAG AA 4.5:1** (all text is
small text; nothing here qualifies for the 3:1 large-text exemption). Worst case is
`--text-3` on `--surface-3` at 4.64:1 light and 5.27:1 dark.

> **Rechecked 2026-11-14.** All 18 pairs still pass for the default look, with the same worst
> case. The claim covers Paravent's own palette only; a theme's palette, Tome's included, is
> outside it.

Two rules follow, and both were learned from real failures:

- **`--text-3` must clear 4.5:1 on `--surface-3`**, not just on `--bg`. It is used for eyebrows,
  statblock ability labels, combatant metadata and search subtitles — all small text, often on the
  most-tinted surface. Checking it only against the page background hides the failure.
- **Accent-as-a-line and accent-as-a-fill are different tokens.** A hue with enough contrast for a
  1px border does not necessarily carry white label text. `--accent` is the line; `--accent-solid`
  is the fill.

Re-run the check whenever a neutral or accent value changes — a token that fails here fails in
dozens of places at once, since components never hardcode color.


### Scrollbars

A default scrollbar is the one element that still reads as browser chrome on an otherwise
finished page — and on a dark theme the OS paints a *light* one, which is worse. Ours are themed
but behave exactly like system scrollbars: same drag, same click-track, same wheel. Only the
paint changes.

**`color-scheme` does half the work and is not optional.** Each theme block declares it:

```css
:root                 { color-scheme: light; }
:root[data-theme=dark]{ color-scheme: dark;  }
```

Without it the browser paints every native surface it still owns — scrollbar corner, text caret,
form controls — in the wrong theme, no matter what else is styled.

| Token | Light | Dark | Contrast on `--surface-3` |
| --- | --- | --- | --- |
| `--sb-thumb` | `#98A1B0` | `#5D6776` | 2.28 / 2.52 |
| `--sb-thumb-hover` | `#7C8595` | `#737D8E` | 3.25 / 3.47 |
| `--sb-thumb-active` | `#656E7E` | `#8A94A5` | 4.50 / 4.72 |
| `--sb-size` | 10px vertical, 6px horizontal strips | | |

Measured against `--surface-3`, the most-tinted surface a bar can sit on. **Hover** clears the
3:1 that WCAG 1.4.11 asks of a non-text control; **rest sits deliberately below it**, because a
resting scrollbar should recede rather than compete with content.

Rules:

- **Track transparent, thumb floating.** The track inherits whatever surface it sits on. The
  thumb uses a transparent border plus `background-clip: content-box` to inset itself, so it
  floats clear of the content edge instead of butting against it.
- **Firefox and Chromium need different properties.** `scrollbar-color` / `scrollbar-width` for
  Firefox; `::-webkit-scrollbar*` for Chromium and Safari. Both read the same tokens, so a theme
  switch moves them together.
- **Horizontal strips get a quieter bar.** Filter chips and the wizard step rail are short; a
  10px bar crowds them. They use a 6px thumb that stays invisible until the strip is hovered or
  focused within.
- **Touch keeps its own.** Under `@media (hover: none)` ours is removed entirely — touch
  platforms draw an overlay scrollbar that fades on its own and needs no gutter, and ours would
  sit under a thumb and steal 10px of a narrow screen.

### Non-negotiable measurements

| Value | Rule |
| --- | --- |
| 44px | Minimum tap target on touch surfaces. HP buttons and dice are larger. |
| 13px | Minimum body text. Statblock prose 13.5px, adventure text 15px. |
| `tabular-nums` | Mandatory on every changing number. |
| 5 / 8 / 12px | The only three radii. Chips are the sole exception at 999px. |

(A 236px desktop rail was on this list; the app has no rail, §3.)

---

## 3. Layout

| Width | Navigation | Reference layer | Combat tracker |
| --- | --- | --- | --- |
| `≤ 640px` phone | Top bar with icon-over-label tabs; players: a compact top row and a bottom bar | Bottom sheet (on touch) | Board, side panel stacked below |
| `641–1023px` iPad | Top bar, the sections on their own row up to 760px | Floating card or bottom sheet, by pointer | Board, side panel stacked below |
| `≥ 1024px` desktop | Top bar, sections in line | Floating card with a mouse | Board with a 372px sticky side panel |

The same htpy fragments render at every size. What changes is the shell. The content behind a
preview never unmounts, so an HTMX swap in the tracker keeps working while a reference is open.

> **As built (2026-11-14).** There is **no rail** at any width. Every role has one sticky top
> bar (`app_shell` in `app/ui/layout.py`): the sections — Table, Characters, Encounters, Combat,
> Worlds, Books for the gamemaster; Characters, Books, Journal for a player — then the sound
> desk, the log button with its badge, search, a settings gear (gamemaster only) and the user
> menu. The user menu holds a DE / EN switch (which also sets the units), a sun / moon theme
> switch (the device decides until one is chosen) and dice sounds on / off. At ≤760px the
> sections drop to a row of their own; at ≤640px they become icon-over-label tabs. A player at
> ≤640px gets a compact top row (logo, search, user menu) and a bottom bar with the sections;
> the character sheet and the wizard keep bottom bars of their own.
>
> The breakpoints in the CSS are mostly 640 and 767px, with 760, 860, 900, 1024 and 1180px
> where a screen needs them.
>
> There is **no persistent 372px reference drawer and no 60% sheet**. A reference opens as a
> preview (§7) — a floating card with a mouse, a bottom sheet on touch, chosen by pointer
> capability rather than width — and as a full reference page,
> `/ref/{category}/{source}/{name}`. Only the combat tracker keeps a 372px sticky side panel,
> at ≥1024px, stacked below the board on narrower screens. A link to a heading keeps room for
> the sticky bar: `ui.js` measures the bar into `--bar-h`, which sets the page's
> `scroll-padding-top`.

---

## 4. Core components

### Combatant row

Left-edge stripe encodes HP state: indigo (active turn), amber (bloodied, <50%),
red (critical, <25%), grey (down). **Redundant with the bar and the number by design** —
three encodings so it reads at a glance, at an angle, and to a colorblind reader.

One htpy function, returned inside the full tracker page and with every action.

> **As built (2026-11-14).** The function is `combatant_row` (`app/ui/combat_pages.py`). Its
> actions post to `POST /gm/combat/{id}/c/{cid}/{action}` — `hp` with a mode of `damage`,
> `heal` or `temp`, for one — and return the whole board rather than one row, since a change
> to one combatant can move the order, the round head and the difficulty. The stripe stays;
> the bar is now the **row's background** (`--hp`): green, amber when bloodied, red when
> critical. On the players' view a foe's bar is always full and only its colour shows its
> state. Each row carries a round token (TOKENS.md: the table's own picture, else a token from
> the image folder, else the kind's disc), tinted green for a friend and red for a foe; a side
> pill per group switches friend and foe; a turn group shares one initiative. One line at the
> bottom holds the amount field, **−**, **+**, Temp and a ⋯ menu; a fallen creature gets a
> "Dead" pill with a skull. Statblock actions get Attack / Adv / Dis buttons, and the roll
> waits under "Attacks to resolve" for Hit or Miss (COMBAT.md).

### Command palette (⌘K)

Not a page — a layer summoned from anywhere, including mid-combat. Type-ahead across bestiary,
spells, items and homebrew *at once*; you never choose a category first. Searches English and
German together, falling back to English.

The same searchable-combobox component backs "add a monster to this encounter", "pick a spell
for this NPC" and "link an item into adventure Markdown" — one fragment, one endpoint,
filtered by a `type=` query parameter.

> **Built 2026-10-16** — `app/ui/palette.py`, `static/js/palette.js`, `GET /search`. The filter
> parameter is `group=` (spells, bestiary, items, options, rules, world, books), not `type=`,
> since a group spans several corpus categories (items + base items). There is no Homebrew chip:
> homebrew sits in the groups like any book. Results follow typing about 120 ms after the last
> key. Results are scoped to the page's world and say how many matches sit in books the world
> does not use ("N more in books this world does not use · Search all imported books"). On a
> phone the palette fills the screen; the key hints hide on touch.
>
> **As built (2026-11-14).** One row per entry, its glossary name first and the English name
> muted beneath (the glossary was built 2026-10-19, TRANSLATION.md §4, so German names are
> searched). ↵ opens the reference page, ⌘/Ctrl+↵ opens it in a new tab. There is no ⇧↵ "add
> to encounter": adding is a **picker mode** of the same endpoint (`pick=<encounter, combat or
> treasure id>`), with a + button per row and the group chips hidden. Players see only what
> they may read (PLAYER-VIEW.md). In the Markdown editors the palette runs in **insert mode**,
> a Campaign group first, each result inserting its tag; typing `{@…` opens a drop-down of the
> same results (ADVENTURES.md §5.6).

### Reference drawer / sheet

Every `.ref` is an `hx-get` into `#reference` with `hx-push-url="false"`. Opening a spell
mid-combat never touches history and never re-renders the tracker.

> **As built (2026-11-14).** Never built as such; nothing renders into a `#reference` target.
> The two jobs went elsewhere: a glance is a preview (§7, `static/js/tooltips.js`), and the
> combat tracker shows the selected combatant in its own side panel, `#combat-side` (§3).
> Neither touches history or re-renders the tracker.

### Wizard

Step rail is both progress indicator and navigation; completed steps stay clickable. Each step
POSTs into gamestate JSON and returns the next fragment. Back is a GET of an earlier step — the
data is already stored, so nothing is re-asked. Survives a closed tab by construction.

Below 768px the rail cannot hold eight labelled steps and must not become an undiscoverable
horizontal scroll: it collapses to a **single progress line**, and tapping the step title opens the
full list as a sheet. The character-creation wizard is the worked example —
[CHARGEN-WIZARD.md](CHARGEN-WIZARD.md), page 13 of the design reference.

> **As built (2026-11-14).** The draft is a character file in `library/characters/` with
> `status: "draft"`, saved from the first step. Steps are named routes,
> `/chargen/{id}/{step}`; every change posts the whole form through htmx and gets the form back
> — rail, questions and the derived panel. There are up to nine steps: Species, Class,
> Abilities, Background, Features, Skills, Equipment, Spells, Details (under 2024 rules Class,
> Species, Background, Abilities, then the rest); Features and Spells are left out when they
> ask nothing. On a phone the rail unfolds in place under the progress line rather than in a
> sheet. A changed earlier answer marks the later steps it touches with "!" for review.
> Levelling up and preparing spells reuse the shell without a rail.

### Dice

Advantage is a **three-state segmented control** (disadvantage / normal / advantage), never a
checkbox — the 5e rule has three states and the UI must not imply two.

> **As built (2026-11-14).** Still true on the character sheet, with a **Private** switch
> beside it that keeps a roll between the player and the gamemaster. In the combat tracker the
> three states are three buttons on each statblock attack: Attack, Adv, Dis. Every result goes
> to the shared log, shows as a toast and plays the dice sounds on the device that rolled
> (PROTOCOL.md).

---

## 5. Icons

**Lucide** — ISC licensed, ~1,850 icons, no attribution required. Lucide ships `0.x` versions
(e.g. `0.539.0`); there is no 1.x. Pin an exact version.

Chosen over a fantasy icon set (game-icons.net) deliberately: Paravent is an instrument, and a
consistent 2px stroke reads better at 13px on a phone than a detailed silhouette does. Game
meaning is carried by **color and label**; the icon only says which *category* a thing is.

### Why Lucide specifically

- **A vendored sprite** — the symbols the UI uses, copied into `static/icons.svg` — needs no
  Node, no npm and no network at runtime. This is what TECHSTACK.md's "no build tooling"
  requires. (An earlier draft planned the `python-lucide` package; the sprite made it
  unnecessary.)
- **ISC license**: nothing to credit on screen, but its copyright and permission notice has to
  travel with every copy of the icons. It is in [`NOTICE.md`](../NOTICE.md), which ships with
  the repository, the container image and the website. (Compare: game-icons.net is CC BY 3.0
  and requires naming the individual artist; Remix Icon relicensed in Jan 2026 with usage
  restrictions.)
- Single stroke weight and a 24px grid means every glyph sits on the same optical baseline.

### Rendering contract

Lucide is a **stroke** set. `fill:none; stroke:currentColor` is mandatory — without it every
glyph renders as a black blob.

```css
svg.ic{
  fill:none; stroke:currentColor; stroke-width:2;
  stroke-linecap:round; stroke-linejoin:round;
  width:1em; height:1em; flex-shrink:0;
  vertical-align:-.125em; display:inline-block;
}
```

Icons size in `em` and inherit `currentColor`, so they follow their container's color token
automatically — no per-theme icon rules ever.

### Optical size ramp

Stroke width scales **down** as the icon grows, so weight stays visually constant. A naively
scaled 2px stroke looks heavy when small and spindly when large.

| Class | Size | Stroke | Used for |
| --- | --- | --- | --- |
| `.ic-xs` | 13px | 2.25 | Pills, inline reference links |
| `.ic-sm` | 15px | 2.0 | Buttons, search field |
| `.ic-md` | 18px | 1.85 | Section labels, search result rows |
| `.ic-lg` | 22px | 1.7 | Mobile tab bar |
| `.ic-xl` | 28px | 1.6 | Empty states |

### Where an icon is allowed

| Context | Size | Rule |
| --- | --- | --- |
| Navigation | 16px | **Always.** Icon + label; never icon-only above 768px. |
| Buttons | 15px | **Only when it adds meaning.** "Next turn" gets a play glyph; "Cancel" gets nothing. |
| Condition pills | 12px | **Always.** A DM scans conditions faster by shape than by reading five short words. |
| Reference links | 12px | **Always.** The icon states the category, so link text stays the bare game term. |
| Section labels | 13px @ 62% | Optional, muted — the label still leads. |
| Statblock prose | — | **Never**, except inside a rendered tag. Prose is read, not scanned. |
| Table cells | — | **Never** as decoration. Only as a status glyph in a dedicated status column. |

### Semantic map

Fixed assignments. An icon means **one** thing across the whole app — a skull is always a
creature, never "danger" or "delete".

| Lucide name | Means, always |
| --- | --- |
| `skull` | A creature. Bestiary entry, statblock, `{@creature}`. |
| `sparkles` | A spell. Also inspiration — both are "a resource you spend". |
| `gem` | An item. Magic items use the same glyph in violet. |
| `dices` | Something rollable. The one glyph meaning "this button produces a number". |
| `heart` / `heart-crack` | Hit points / bloodied. |
| `hourglass` | Concentration. Never a generic "loading". |
| `shield-check` | A saving throw or a DC. |
| `target` | An attack roll or ability check. |
| `footprints` | Movement, speed, movement-restricting conditions. |
| `flame` / `snowflake` / `zap` | Fire / cold / lightning damage. **Only these three** get glyphs — inventing a pictograph for "force damage" helps nobody. |
| `languages` | A translation exists, or English is shown as fallback. |
| `vault` | Treasure. |
| `book-open` / `scroll` / `backpack` | Bestiary / spells / inventory. |
| `users` / `users-round` | NPCs / the party. |
| `swords` | Combat, encounters. |
| `globe` / `map` | Worlds / adventures. |

**Navigation exceptions** (`app/ui/nav.py`). The top bar's section glyphs are chosen to tell six
neighbouring items apart, and three of them step outside the map on purpose: **Table** uses
`swords`, **Encounters** `skull` and **Combat** `zap`. They are navigation glyphs, read with their
label, and mean nothing beyond it.

### Names that do not exist — verified against the shipped icon list

- **No `treasure-chest`** or `chest` → use `vault`.
- **No `d20`** or any polyhedral die → `dices` is a six-sided pair. This is the one real gap;
  accept it rather than mixing in a second library for one glyph.
- **No singular `dice`** → `dices`.
- **No `campfire`** → `flame-kindling`.

Deprecated aliases still resolve but will break on a major version. Use the canonical name:

| Do not use | Use |
| --- | --- |
| `alert-triangle` | `triangle-alert` |
| `edit` | `pencil` |
| `user-circle` | `circle-user` |
| `wand-2` | `wand-sparkles` |
| `globe-2` | `globe` |

Also note `speech` is an accessibility pictogram, not a speech bubble — for verbal spell
components use `mic`.

### Full tag→icon map

Every `{@...}` tag in the corpus is catalogued in
[`data/library/tags/TAGS.md`](../data/library/tags/TAGS.md), with
[`5etools_tags.json`](../data/library/tags/5etools_tags.json) as the machine-readable source the
renderer imports. 110 distinct tags, 249,546 occurrences, 45 distinct icons; 40 tags render
without an icon by design.

### Implementation

An `icon(name, size)` htpy helper emits a `<use>` reference into one sprite inlined once per
page. Never a broken-image box.

> **As built (2026-11-14).** The sprite is `static/icons.svg`, 68 symbols: Lucide 0.x glyphs
> (ISC; there is no 1.x) and one of Paravent's own, `i-paravent`, the logo. `app/ui/icons.py`
> inlines it once per page and `<use>`s its symbols. An unknown name **raises everywhere**,
> development and production alike: there is no build step to catch a typo, so it must fail in
> a test. A theme may replace the sprite, but only with a complete one (THEMES.md).

---

## 6. The `{@tag}` renderer

TECHSTACK.md calls this the most valuable part of the import. Visually it needs exactly one
rule: **dashed underline = opens something, boxed monospace chip = rolls something.** The Lucide
glyph in front states the *category*, so link text stays the bare game term — `fireball`, not
"spell: fireball".

Under a non-English UI language the **label** comes from the term glossary while the **target**
stays English — `{@condition charmed}` renders as "Bezaubert" and still opens the `charmed` rule.
5eTools' own slot-3 display override is the same mechanism (`{@creature Riding Horse||Horse}`), so
this is the format working as intended, not a Paravent extension. See TRANSLATION.md §3.

| 5eTools source | Class | Icon | Click behaviour |
| --- | --- | --- | --- |
| `{@spell fireball}` | `.ref.spell` | `sparkles` | Opens the spell's reference page |
| `{@creature goblin}` | `.ref.creature` | `skull` | Opens the statblock's reference page |
| `{@item longsword}` | `.ref.item` | `gem` | Opens the item's reference page |
| `{@condition prone}` | `.ref.cond` | `footprints` | Opens the rule's reference page |
| `{@damage 2d6}` | `.ref.dice` | `dices`, or the damage-type glyph | Rolls, result to the log |
| `{@dc 15}` | `.ref.dice` | `shield-check` | A static chip, "DC 15"; does not roll |
| `{@hit 4}` | `.ref.dice` | `target` | Rolls 1d20+4, result to the log |

> **Built 2026-10-15:** `app/ui/tags.py` renders all of this; links open `/ref/{category}/{source}/{name}`.
> The tag catalogue it reads lives in `data/library/tags/`.
>
> **As built (2026-11-14).** With JavaScript, a click on an entity link first opens a pinned
> preview (§7), whose Open leads to the reference page; without it, the click navigates.
> Nothing offers "add to encounter", "add to treasure" or "apply to selected" from a tag —
> adding happens in the palette's picker mode (§4) and in the tracker. `{@dc}` is a static chip
> ("DC 15", in German "SG 15"). `{@damage}` and `{@hit}` on a character sheet roll into that
> character's log with the sheet's advantage state; elsewhere `static/js/roll.js` has the
> server roll them into the log privately (PROTOCOL.md §8). The renderer also does what this
> table did not foresee:
>
> - **Glossary labels** in the reader's language, the target staying English.
> - **Dice in the language's notation** — "2W6" in German — and the renderer's fixed labels
>   ("Hit:", "Melee Weapon Attack:") in the reader's language (TRANSLATION.md §4).
> - **Metric hints** in prose for a reader in metres: "60 feet (18 m)" (TRANSLATION.md §6).
> - **Entries a player may not read** render as plain text for players (PLAYER-VIEW.md).
> - **Campaign tags** — `{@npc}`, `{@place}`, `{@encounter}`, `{@treasure}`, `{@chapter}`,
>   `{@character}`, `{@sound}` — render as links into the world (ADVENTURES.md).

Unresolvable tags — a reference into a book the user never imported, **or one outside the source
set this world declares** — render as plain text with a dotted grey underline and a tooltip.
**Never a dead link, never a raw brace.** The tool is data-neutral; a missing source is normal,
not an error.

The two cases read differently to the user and their tooltips must say which applies:

| Case | Tooltip says | Action offered |
| --- | --- | --- |
| Source not imported | "*Volo's Guide* is not in your corpus." | None |
| Source imported but not declared for this world | "*Volo's Guide* is not among this world's books." | None |

> **As built (2026-11-14).** Both cases render as grey text with a dotted underline that carries
> its message; neither offers a button. Importing is outside the app (the corpus is read in
> place), and the world's books are set on the world's form.

The second is a deliberate boundary, not a gap — see TECHSTACK.md §6c. A gamemaster running a
PHB+MM campaign *wants* a stray reference to a book nobody owns to stay inert.

---

## 7. Tooltips

Prototype: https://claude.ai/code/artifact/c8ad1412-03a2-4a3c-bf78-06c2bddab2ce

Every entity tag (42 types, 143,053 occurrences) gets a hover preview. The tooltip answers the
question *"what is that again?"* without leaving the sentence you are reading aloud.

### Hover and touch are separate paths

**Mouse hovers. Touch taps. Neither behaviour ever runs on the other device.**

Detection is by input capability, never screen width:

```js
const hoverMQ = matchMedia('(hover: hover) and (pointer: fine)');
```

Width is the wrong test, and gets two real cases backwards: an **iPad in landscape** is 1024px
wide with no pointer to hover with, and a **narrow desktop window** is phone-width with a mouse.
The query is re-evaluated live, so plugging in a mouse enables hover without a reload.

Touch browsers replay a synthetic `mouseenter` after a tap, which would open the tooltip twice.
Use `pointerenter`/`pointerleave` and check `e.pointerType === 'mouse'` — the replayed event is
identifiable and ignored. Plain `mouseenter` cannot distinguish it.

### Interaction contract

| Input | Device | Behaviour | Why |
| --- | --- | --- | --- |
| hover in | mouse only | Opens after **350 ms** | Survives a pointer crossing the word en route elsewhere. |
| hover out | mouse only | Closes after **180 ms** | Grace period to move the pointer *into* the tooltip. |
| hover, one already open | mouse only | Opens **instantly** | Once browsing references, the delay is friction. |
| click | mouse only | **Pins** it | Survives pointer-out so buttons inside are clickable. |
| tap | touch only | Opens a **bottom sheet**, no delay | The only way it opens on touch. A tap is already deliberate. |
| tap again / backdrop | touch only | Closes | A finger covers a small popover; the sheet clears the thumb. |
| `Tab` | both | Opens on focus | Gated on `:focus-visible`, so a tap does not also trigger it. |
| `Esc` | both | Closes, **returns focus to the tag** | Never dumps focus at the top of the page. |
| dice chip | both | **Rolls**, no preview | A rollable has nothing to preview. |

The sheet presentation is a CSS concern and uses the matching query, so it stays in step:

```css
@media (max-width: 640px), (hover: none), (pointer: coarse) { /* sheet */ }
```

On touch the dashed underline is a tag's only affordance — there is no hover to reveal it — so it
renders slightly heavier under `@media (hover: none)`, and hover backgrounds are suppressed.

### Rules

- **Only one tooltip open at a time.** Opening a second closes the first. Reference is a glance;
  reading two things side by side is what the reference page is for.
- **Nested tags do not chain.** Tags inside a tooltip body render dotted and inert. Chained
  popovers are unusable with a mouse — "Open" is the way deeper.
- **Flip, never clip.** Measure against the viewport, flip above the anchor when there is no room
  below, clamp horizontally. Never render partly offscreen.
- **Capped at ~6 lines.** A tooltip you have to scroll has failed at being a glance; that content
  belongs on the reference page. (As built: prose is clamped to 6 lines on the card and 10 on
  the bottom sheet.)
- **Preview is a projection, not a truncation.** `preview_for(entity)` selects per type:
  creature → AC/HP/speed/CR + ability row; spell → level/school/time/range + one sentence;
  condition → the complete rule list (it is short, and truncating a rule is worse than useless);
  item → damage/weight/cost + property.

### Per-type layout

| Type | Leads with | Footer actions |
| --- | --- | --- |
| `creature` | AC, HP, speed, CR + six abilities | Open |
| `spell` | Level, school, casting time, range | Open |
| `item` | Damage or AC, weight, cost | Open |
| `condition` | The rule list, complete | Open |
| `rule` / `action` / `skill` / `sense` | One paragraph | Open |
| unresolved — source not imported | Which book is missing | None |
| unresolved — source not in this world's books | Which book is excluded | None |

> **As built (2026-11-14).** The footer offers Open, plus one button per other printing in the
> world's books ("PHB version", TECHSTACK.md §6c); nothing adds to an encounter or applies a
> condition from a preview. The source sits in the card's header, with the entry's token and its
> glossary name, the English name beneath. Translated text shows with a note on how much of it
> is translated.

### Unresolved references

A tag pointing into a book the user has not imported renders as **plain grey text with a dotted
underline**, and its tooltip explains why. The reference is preserved verbatim, so it resolves
automatically once that source is added. Never a dead link, never a raw brace.

### Implementation

`GET /ref/{category}/{source}/{name}/preview?world=…` returns the fragment (planned as
`/ref/{type}/{slug}/preview`); a player gets 404 for an entry they may not read. The tag renderer
emits a plain link, and `static/js/tooltips.js` fetches the preview itself — there is no
`hx-get` on a tag. Campaign links preview through `GET /library/preview?href=…`.

Preview fragments are pure functions of the compendium **and of the active source set** — the
same `{@spell mind sliver}` resolves to the 2014 or the 2024 spell depending on which books the
world declares (TECHSTACK.md §6c). So the cache key is `(source_set_id, type, slug)`, never the
tag alone; a bare `@lru_cache` on the slug serves one campaign's statblock to another's. Invalidate
on the JSON watcher's reload signal and on any change to the declared set.

Resolution itself is not the expensive part — a keyed lookup is **0.24 µs** against the resident
index. The cache exists to avoid re-rendering the fragment, not to avoid the lookup.
`{@creature goblin}` alone appears 54,753 times.

**Degrades without JavaScript:** every tag is a real link to its full entry. Without JS the
tooltip never opens and the click navigates. The content is always reachable.

> **Built 2026-10-15** — `static/js/tooltips.js` + `GET /ref/{category}/{source}/{name}/preview`
> (`preview_card` in `app/ui/ref_pages.py`). Two details the spec above did not anticipate:
>
> - **A load in flight is cancelled on mouse-out.** Without it a slow preview appears after the
>   pointer has already left — the hover equivalent of a dead click.
> - **Focus handed back by Esc or the backdrop must not reopen the preview.** Returning focus to
>   the reference fires `focusin`, which the keyboard path reads as "open". The script ignores the
>   focus it returns itself.
>
> Fragments are cached in the browser by URL. The URL carries `?world=`, so the cache key
> includes the source set, as required; the server renders each preview fresh.
>
> **As built (2026-11-14).** The timings above hold: open after 350 ms, instantly while one is
> open, close after 180 ms, a click pins. A tap opens the bottom sheet over a backdrop, with a ✕
> that takes focus; the page behind stops scrolling, and the sheet is at most 70vh high. The
> card is at most 360px wide and keeps 8px from the viewport's edge. The cache is a `Map` in
> the browser, keyed by URL; there is no server-side `@lru_cache`. Sheet or card is chosen in
> the script by input capability, not by the `max-width` query shown above.

---

## 8. Implementation notes

- **Token CSS, not utility soup.** Components use the class names of `components.css` and
  `app.css` (`btn`, `chip`, …) and read the tokens; there is no Tailwind config and no DaisyUI.
- **Every component is one htpy function**, callable both standalone (HTMX fragment) and nested
  (full page).
- **The JSON watcher needs a UI, not just a reload.** When a watched file changes, show a quiet
  toast — "Bestiary reloaded, 4 entries changed" — with an action to refresh open pages. Never
  silently swap content a gamemaster is mid-sentence reading aloud.
- **Search spans the whole reference set, and coverage grows in phases.** Categories arrive over
  several releases (TECHSTACK.md §6b), so the palette must render a result type it has no bespoke
  layout for — fall back to name + category + first sentence rather than omitting the row.
- **The source set is a filter on search, not only on resolution.** The palette shows what this
  world can actually use. Entries outside the declared books are excluded by default, with a
  "search all imported books" escape for the moment the gamemaster is deliberately shopping for
  something new. Never make the user wonder why a monster they own does not appear.
- **Declaring books is a setup step, and it must be skippable.** An empty declaration means
  "everything imported" (TECHSTACK.md §6c), so a first-run user sees a working tool. Surface the
  setting where a campaign is created, not behind a modal that blocks the first session.
- **Role is a layout decision, and a permission check.** Gamemaster and player resolve to
  different shells from the session role; the components inside are identical. The role also
  gates what a route will do: a player asking for a gamemaster's route gets 403.

> **As built (2026-11-14).** There is no `base.html`: `layout.document()` and
> `layout.app_shell()` in `app/ui/layout.py` build every page. htmx 2.0.4 is vendored in
> `static/js/` and loaded by the pages that need it; `ui.js`, `sound.js`, `tooltips.js` and
> `palette.js` load on every page. Live updates — sheets, party cards, the tracker, the log —
> arrive over Server-Sent Events (`app/services/events.py`). Every response carries a Content
> Security Policy (`app/main.py`) that keeps scripts, styles, fonts and pictures to this
> server.

---

## 9. Resolved questions

### Multiuser — resolved, multiuser it is

An earlier draft flagged a contradiction: PROJECT.md specified multiuser with gamemaster/player
roles, TECHSTACK.md §7 said single user with no auth. **Reconciled in favour of multiuser**, the
superset — a solo game is one gamemaster with no players. TECHSTACK.md §7 now says so. This
document already designed for that reading, so nothing here changes.

### Where the data lives — resolved, two roots

Reference data (the 5eTools corpus) is **read-only and external**; everything authored in
Paravent lives under `library/`. Two consequences for the UI:

- **The reference layer never offers an edit affordance on a corpus entry.** "Edit" on a monster
  means *copy to homebrew, then edit* — the copy lands in `library/homebrew/` and overrides the
  original by `(name, source)`. Present it as such, never as editing the source.
- **A missing source is a normal state, not an error.** The corpus is user-supplied and may be
  partial, so an unresolved reference is styled as ordinary text with an explanatory tooltip
  (§6, §7), never an error state.

### Translation — resolved, a text-only overlay

Earlier drafts treated this as blocked on a German corpus appearing. It is not. A translation is a
**text-only lookup over the English record** (TRANSLATION.md): structure, mechanics and tags stay
English and canonical; only name and prose are overridden, per field, with English as the fallback.

Three things follow for the UI:

- **Partial translation is a normal state.** A German name with an English body is valid and
  renders that way, marked with the `languages` icon (§5). Never an empty field, never a crash —
  principle 5 already said this; the data model now guarantees it.
- **Tag labels localize; tag targets do not.** `{@spell fireball}` in German prose displays
  "Feuerball" and still opens the English Fireball record, so previews, reference pages, the
  source set and backlinks work identically in both languages.
- **The term glossary alone makes the UI feel German** — search, ⌘K, condition pills and tooltip
  headers — before a single paragraph is translated. It is the first translation deliverable.

Units are a **separate, per-user setting**, not a consequence of language (TRANSLATION.md §6).
