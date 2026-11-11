# Worlds, adventures and chapters

**Scope:** how the gamemaster's own content is structured, stored and rendered — the world,
its adventures, their chapters, the objects placed in them (NPCs, encounters, treasure,
images), the Markdown dialect the prose is written in, and how an adventure can sit on top of
a published one from the corpus.

**Date:** 2026-10-16 · **Status:** steps 1–5 of §8 built. Next: the combat tracker (step 6), specified in COMBAT.md.

> **One sentence:** prose is Markdown files you can open in any editor; objects are records
> with their own editors; the prose *places* objects where they happen in the story.

---

## 1. Decisions

| Question | Decided |
| --- | --- |
| Where prose lives | **`.md` files with YAML front matter**, one per world, adventure and chapter, inside a folder per world |
| Published adventures | **Yes** — an adventure may be based on one from the corpus; its text is read in place and the gamemaster's notes, encounters and treasure are layered over it (§7) |
| Player journal (revealed handouts, met NPCs) | **Built 2026-11-05** for boxes and pictures shared from chapters (JOURNAL.md); everything else here stays gamemaster-only, enforced by the route, not by CSS |
| Locations | **Headings and maps for now.** A location object can follow when the content asks for one |

---

## 2. The hierarchy

```txt
World          cover · description · books · bosses, factions, places
 └ Adventure   cover · summary · level range · status · ordered chapters
    └ Chapter  cover · Markdown body (its sections are its headings) · status
```

- **Sections are headings, not records.** This is how the published adventures are built — a
  chapter's table of contents in `adventures.json` is its list of headers. A fourth record
  level would only get in the way of writing.
- **Order is a field**, `order` in the front matter, not the file name. Reordering in the app
  rewrites one number; it never renames a file you may have open in your editor.
- TECHSTACK.md §9 calls the third level "module". It is **chapter** from here on.

---

## 3. Files on disk

```txt
library/worlds/
└── sunken-marches/                   folder name is a slug; identity is the `id`
    ├── world.md
    ├── media/                        covers, portraits, maps — referenced relatively
    │   ├── cover.webp
    │   └── maren.webp
    └── adventures/
        └── drowned-bell/
            ├── adventure.md
            ├── media/
            │   └── ferry-map.webp
            └── chapters/
                ├── media/            a chapter's own images (paths are relative
                │   └── landing.webp  to the .md file, so editors preview them too)
                ├── the-ferry-at-grey-reed.md
                └── grey-reed.md
```

**A world folder is self-contained**: its prose, its images, everything a reader of the text
needs. It can be zipped, backed up or handed to another gamemaster as one directory. (This
moves media out of a global `library/media/`, as TECHSTACK.md §3 first planned, into the
world it belongs to.)

### Front matter

```yaml
# world.md
---
id: 772beec73c24
name: The Sunken Marches
language: en
cover: media/cover.webp
books: [PHB, MM, DMG, XGE, TCE]
---
The Sunken Marches are …
```

```yaml
# adventure.md
---
id: 3b91c0d2e4f5
name: The Drowned Bell
order: 1
status: running            # draft | planned | running | finished
level: {start: 1, end: 5}
cover: media/cover.webp
based_on: null             # optional — the source code of a published adventure (§7)
books: null                # optional narrowing of the world's books
party: [lyra-mooncaller]   # optional — who it is written for; fights are rated for them
drafted_by: 1f2e3d4c5b6a   # set when Claude drafted it through MCP (MCP.md §6)
---
```

`draft` is an adventure Claude wrote through the MCP server and the gamemaster has not
reviewed yet; giving it any other status ends Claude's write access (MCP.md §6). `party`
is chosen in the adventure's form ("nobody ticked" is the whole world): the encounters
placed in the adventure are rated against those characters, which lets two groups share
a world.

```yaml
# chapters/the-ferry-at-grey-reed.md
---
id: 9a0e4471bc12
title: The Ferry at Grey Reed
order: 1
status: planned
cover: media/ambush.webp
section: 00c               # only with based_on: the book section this chapter shows
---
```

Front matter is validated by **strict** Pydantic models — it is library data, so an unknown
key is an error, reported, not ignored.

### The store's rules — the same as the character store's

- **A dropped-in file appears.** A file with no `id` is given one, written back into its front
  matter.
- **A broken hand edit keeps the last valid version** and says why (CHARACTER-FORMAT.md
  established this for characters). A YAML typo never makes a chapter vanish mid-session.
- **A deleted file disappears**; a moved folder is the same record, because identity is the
  `id`, not the path.
- **The app rewrites front matter, never your body text** — with one exception: renaming an
  object rewrites references to it (§5), as renaming a player follows into their characters.
- Atomic writes, the file watcher, live updates over SSE: the existing machinery, unchanged.
- **`worlds.json` migrates on start** into `world.md` files, once, like the old `player_id`
  conversion. `WorldService` keeps its interface; only what backs it changes.

---

## 4. Objects

Structured records with their own editors, kept in JSON collections as now
(`encounters.json`, `npcs.json`, `treasures.json`). Each carries a **scope**:

```python
class Scope(GameStateModel):
    world_id: str
    adventure_id: str | None = None
    chapter_id: str | None = None
```

A boss defined at world scope is visible in every adventure; promoting a chapter's NPC to the
world clears one field. **Lookup is nearest first**: chapter → adventure → world, so a
chapter-local "Guard Captain" shadows the world's — then the rest of the world, so a name
written in another chapter still links.

| Object | Holds |
| --- | --- |
| **NPC** | name · roles (`boss`, `villain`, `ally`, `patron`, free tags) · statblock reference `Name\|SRC` or none · portrait (a media path) · notes in Markdown |
| **Encounter** | exists already; `world_id` becomes a `Scope` |
| **Treasure** | name · items as `Name\|SRC` with counts · coins (cp/sp/ep/gp/pp) · notes. "Given to" a character comes later |
| **Place** | name · tags (village, dungeon, inn…) · picture (a media path) · notes in Paravent Markdown. Written `{@place Name}`; built 2026-10-28 (`library/locations.json`, the model `Location` — `Place` is already where a user left off) |
| **Media** | not a record — a file in a `media/` folder, uploaded through the app or dropped in |

"Bosses" on the world are NPCs at world scope with the `boss` role; the world page lists them.

**Deleting an adventure or a chapter** (2026-10-29) asks what happens to what is placed
in it, with the counts: *keep* (the default — NPCs, places, encounters and treasure move
up to the world, or a chapter's to its adventure) or *delete them too*, which cleans up
generated content. Fights already run stay as history; a session note that named the
adventure keeps its text and loses the name. Claude deleting its own draft always takes
the contents with it, so nothing generated is left in the world.

**Who and what is at a place, its notes say.** A place keeps no lists of its own: the
`{@npc}`, `{@encounter}` and `{@treasure}` its notes name are its page's *Here*, each
resolved nearest first from where the place is placed (grey when nothing has the name).
So a place is written once, in prose, and nothing is kept twice. Its pages are a world's
*Places* (world page), the place itself, and its form with a picture; a rename rewrites
`{@place Old}` in every text of the world; the panel of what an adventure or chapter
holds has a *Places* column; the editor creates one where `{@place New}` is written, and
the chapter check offers that too. Claude writes them into drafts (`create_place`,
`update_place`, MCP.md §4).

---

## 4b. Session notes

**Built 2026-10-27** (`app/services/session_notes.py`, `/gm/worlds/{id}/sessions`). What
happened at the table, one note per day of play, in the gamemaster's words — the story
beside what the log already knows (the fights, experience, levels).

- A note has its **day**, optionally the **adventure** played, and **text** in this
  dialect: `{@npc …}` links, and resolves in the adventure's scope when one is named.
  Kept in `library/session_notes.json`.
- **A new note for a day with fights starts from their recap** (`recap_text`, the same
  lines the recap page offers) — who fought, who was defeated, the biggest hit — to edit
  into prose.
- The world page links them ("3 session notes"); the list reads newest first, each note
  rendered; editing has the same version check as chapters.
- **Claude reads them** through `campaign_history` (MCP.md §4) as each day's `notes`,
  before the fights; the MCP tools never write them. The `new_adventure` prompt reads
  them first.

This is not the *journal* §1 reserves for the players' view of revealed content; that
is still to come.

## 5. Paravent Markdown

**Named 2026-10-28.** The dialect below is **Paravent Markdown**: CommonMark plus what a
campaign needs. It is not only for chapters — every text the gamemaster writes is
rendered in it, as if written where its object is placed: adventures and worlds, NPC and
treasure notes, **encounter notes** (on the builder, with the same editor, and folded
under the board in the combat tracker), and session notes (§4b). One renderer, `app/ui/markdown.py`; one helper for a placed text,
`app/routes/paravent_markdown.py` (`paravent_markdown(page, registry, scope, text)`).

**CommonMark + GFM tables**, parsed with `markdown-it-py` and the `container` plugin from
`mdit-py-plugins`, plus three additions. **Raw HTML is off** — escaped, not rendered.

### 5.1 5eTools tags work as they are

```markdown
Four {@creature goblin|MM}s hide in the thicket (DC 10 {@skill Perception}).
```

Rendered by `app/ui/tags.py` exactly as reference text is: resolved against the adventure's
books, hover/tap previews, roll chips. Tags are recognised by an **inline rule that runs
before emphasis**, so `{@spell hunter's mark}` or an underscore inside a tag is never
read as Markdown.

### 5.2 Your own objects, in the same syntax

```markdown
{@npc Maren Holt}                  {@npc Maren Holt|Maren}
{@encounter Ambush at the Ferry}   {@treasure Old Gnasher's toll box}
{@character Brunhild Ironvow}      {@chapter The Ferry at Grey Reed}
{@place Grey Reed}
```

`{@character}` (2026-10-26) names a player character placed in the world and links to
the sheet.

- **No `{@adventure}` of our own.** 5eTools already has that tag — a link into a published
  adventure, `{@adventure Title|SOURCE|1}`, used 6,207 times in the corpus with the source
  in slot 2 — and slot 2 is where ours put the label. It keeps its 5eTools meaning in Markdown
  too. (Checked against the catalogue: `npc`, `encounter`, `treasure` and `chapter` are free.)

- **Arity: `{@kind name|label}`** — these are Paravent's tags, not 5eTools', and have no
  source slot. Recorded here because it is the same kind of trap as `{@deity}` (TECHSTACK.md
  §6c): a shared parser must know that slot 2 is the label for these kinds.
- Resolved **by name, nearest scope first**. None found is the designed inert state ("not in
  this world"), never an error. Previews come from the same tooltip layer.
- **References are by name** because you type them in an editor. Renaming an object in the app
  rewrites `{@npc Old Name…}` in every `.md` file of its scope.

### 5.3 Callouts — what the published adventures use most

The corpus's adventures hold **7,193 read-aloud boxes** and **1,653 sidebars**; those are the
two callouts. Syntax is GitHub's alert form, so the files render sensibly anywhere else too.

```markdown
> [!read]
> Fog lies on the river like wet wool. Somewhere out on the water a bell tolls…

> [!sidebar] Running Old Gnasher
> Old Gnasher wants the toll box, not a fight to the death…

> [!secret]
> The bell is real: the old chapel tower lies under the river.
```

`[!secret]` means nothing yet — everything is gamemaster-only — but is reserved now: when the
journal arrived (JOURNAL.md), a secret block is **never** sent to a player, even inside a shared box.

**Everything else the books print** (2026-11-04). Measured over every book and adventure of
the corpus, the boxes besides read-aloud text (7,609) and sidebars (2,447) are quotes (571, 48
of them pull quotes), flowcharts (17, with 115 steps), variant rules (126, in the rules
files) and galleries (1,180). Each has its callout, and the books' own render the same way
(`app/ui/tags.py`), so a theme styles both at once:

```markdown
> [!variant] Fast Pursuit           ← titled "Variant: Fast Pursuit", as the books print it
> …

> [!quote]                          ← an epigraph: untitled unless a title is written;
> Who enters the forest…              its last line starting with — (or --) names the
> — Marshal Dughan                    speaker, on its own line or paragraph

> [!pullquote]                      ← large, between rules
> [!flowchart]                      ← its list items are the steps, an arrow between each
> [!gallery]                        ← its images side by side
```

And one Paravent adds: **`> [!handout] Title`** (alias `letter`), a letter or note the party
finds, set apart like a leaf of its own. Aliases: `flow` is a flowchart.

**Obsidian's callouts too** (2026-10-26). Adventures written in Obsidian use its callouts, so
they render as there: `note`, `abstract`, `info`, `todo`, `tip`, `success`, `question`,
`warning`, `failure`, `danger`, `bug`, `example`, `quote`, with Obsidian's aliases (`summary`
and `tldr` are an abstract, `error` a danger, `cite` a quote, `hint`/`important` a tip,
`caution`/`attention` a warning, `check`/`done` a success, `help`/`faq` a question,
`fail`/`missing` a failure). Each has its icon and colour; with no title written it is titled
with its name ("Gefahr"). The title reads Markdown and tags. An unknown kind is a note titled
as written, as in Obsidian, and the chapter check names it.

```markdown
> [!abstract]- Agnes Holler – the accused herbalist      ← folded shut, the title to open it
> **Role:** innocent, the village's real protection.
> **Wants:** the juniper hung again, *tonight*.

> [!danger]+ The Nettle Crone – Green Hag                ← folded, open to begin with
```

`-` after the kind folds the callout shut (a `<details>`), `+` folds it open.

**Links to headings** (2026-10-26), Obsidian's form: `[[#Appendix B – Scaling]]` links a
heading of this text, `[[#River Troll (Troll)|River Troll]]` with a label (in a table cell
too: its pipe is not a cell boundary), `[[Grey Reed]]` and `[[Grey Reed#The inn]]` another
chapter of the world. A heading's anchor is its text in lower case, every run of other
characters a hyphen. A link to a heading or chapter that is not there is grey and says so,
and the chapter check lists it.

**A newline is a line break**, as Obsidian writes Markdown: a statblock's
`**Senses** …` / `**Languages** …` lines, or a callout's `**Role:**` / `**Wants:**` lines,
stay one to a line. A blank line still starts a paragraph. The shipped help pages, which
are hard-wrapped, render without (`MarkdownContext.breaks`).

### 5.4 Embeds — a tag alone on its line

**Decided 2026-10-26: one syntax, `{@…}`.** A tag inside a sentence is a link; a tag that
is the **whole paragraph** is the object itself:

```markdown
The goblins wait in the reeds. {@encounter Ambush at the Ferry} starts when…   ← a link

{@encounter Ambush at the Ferry}                                               ← the card

{@npc Maren Holt}

{@treasure Goblin pouches}

{@character Brunhild Ironvow}

{@creature Goblin|MM}
```

- `{@encounter}` shows the **live** encounter card with its difficulty and Run;
  `{@npc}` the NPC card with portrait and statblock; `{@treasure}` the item and coin list;
  `{@character}` the party card from the gamemaster's table (HP, AC, conditions, slots),
  kept live over the same event stream (`party.js`) and with the same buttons.
- **Any reference folds into the text**: `{@spell Fireball}`, `{@creature Goblin|MM}`,
  `{@item Potion of Healing}` render as one row (icon, name in the reader's language,
  kind, book, a link to the reference page) that opens to the whole entry as its
  reference page shows it, dice rollable, closed until opened. It resolves through the
  world's books like any tag.
- **In a list item or a table cell a tag is always a link** — `- {@creature Goblin}` is a
  list. Two tags on a line, `{@chapter}` and roll tags stay inline too.
- A name that resolves to nothing renders as the link would, in its inert state, so a
  typo is visible.

**The older `:::` blocks** (`::: encounter Ambush at the Ferry` … `:::`, `::: statblock …`,
`::: spell Fireball open`) still render, with the text inside as a caption, but nothing
writes them any more: on start, Paravent rewrites every one in the world's texts and the
notes of NPCs, treasure and encounters as a tag on its own line — `statblock` as
`{@creature}`, the caption as the next paragraph, `open` dropped
(`embeds_to_tags`, `AdventureService.rewrite_all_texts`). **`:::: at 00e`** is not an
embed: it places notes inside a published adventure's section (§7) and stays.


**Cards carry their notes folded** (2026-10-28): an encounter's card shows its notes —
tactics, terrain, morale — and a place's card its notes, each folded until opened,
rendered in Paravent Markdown with links only, so a card never unfolds cards (a place's
notes may name the place itself).

### 5.5 Images and headings

- `![The ferry landing](media/ferry-map.webp)` — **relative to the `.md` file**, confined to
  the world folder (a path that escapes it is refused, never served). Served through an
  authenticated route, not as static files, so the journal can later gate a map.
- **Obsidian's embeds** (2026-10-26): `![[ferry-map.webp]]`, `![[map.png|300]]` (width,
  or `300x200`), `![[map.png|The landing|300]]` (alt text). A name is tried as written,
  then in the record's `media/`, then — a bare file name, as Obsidian finds it anywhere in
  the vault — the first file of that name in the record's folder, then anywhere in the
  world folder (`media.find`), with the same confinement. A file not found is the missing
  image, in the text and in the chapter check. `![[Chapter]]` or `![[#Heading]]`, which
  Obsidian embeds as a note, is a link here.
- **Image effects** (2026-11-04, `app/services/image_effects.py`): a picture is named with
  the effects it wears, and Paravent shows a rendering of it instead of the original,
  which is never changed. Rendered once with Pillow, kept in
  `reference-cache/images/` as WebP with transparency (so an edge melts into whatever
  lies beneath: one rendering fits every theme and mode), made again when the picture
  changes; a large picture is made at most 1600 px on its longest side.

  | Effect | What it does |
  | --- | --- |
  | `torn` (`ripped`) | Ripped edges and torn-off corners, browned along the tear |
  | `fade` (`soft`, `vignette`) | Edges that dissolve into the page |
  | `oval` | A portrait's oval, softly cut |
  | `sepia` | Brown ink on old paper |
  | `aged` (`old`) | A faded, grainy print with darker corners |
  | `mono` (`bw`) | Black and white |
  | `banner` | Cut to a title image's wide shape (10:3), centred |

  **The notation is the same everywhere:** a part made only of effect words, beside the
  size and the alt text — `![[tavern.jpg|torn sepia]]`, `![[tavern.jpg|The inn|fade|400]]`,
  Markdown's alt text `![The inn|torn](media/tavern.jpg)` (Obsidian's `![alt|300](…)`
  form), and **a title image** in front matter, `cover: media/tavern.jpg|torn fade`, which
  the images panel sets with a row of ticks under the cover. A cover with effects is cut to
  the banner first, so the page shows the edges the effects drew. The crop runs first,
  then the colour, then the edges; a tear is the same each time for the same picture, as
  it is seeded by the file. The URL asks for it: `/media/<id>/<path>?fx=torn,fade`.
- The title comes from the front matter. `#` in the body is the first section level; the
  page derives a table of contents and anchors from the headings.
- **Dropping or pasting an image** into the chapter's or adventure's editor stores it in the
  record's `media/` folder (`POST /gm/media/{id}/inline`, built 2026-10-26) and writes
  `![name](media/name.png)` at the cursor — a placeholder while it uploads, taken out
  again with the reason when the file is refused (not an image, too large).

### 5.6 Writing references: the insert pop-up

Nobody should type `{@creature goblin|MM}` from memory. In every Markdown editor — an
adventure's description, chapters, NPC and treasure notes — **⌘K** (Ctrl K) or the
**Insert reference** button opens the search as a pop-up (`app/ui/palette.py`,
`app/ui/insert.py`, built 2026-10-26). It searches the world's books like the ⌘K
palette, plus a **Campaign** group of the world's own NPCs, encounters, treasure and
chapters, listed first. Enter inserts the highlighted result's tag at the cursor and the
preview follows; ⌘↵ opens the entry in a new tab instead.

- The tag is **as short as the world allows**: `{@creature Goblin}` when the world's books
  resolve the bare name to that very entry, else with its book — `{@spell Fireball|PHB}`
  in a world declaring both Player's Handbooks, where the bare name means 2024's.
- **While typing**, a drop-down opens at the cursor (`static/js/complete.js`,
  `/search/suggest`): `{@creature gob` lists the creatures whose name holds "gob",
  `{@npc Sil` the world's NPCs, `{@character Br` its characters. ↑↓ move, Enter or Tab
  completes — the reference as short as the world allows, with its `}` — and Esc closes it.
- **Ctrl+Space opens it on demand** (2026-10-26): after `{@spell ` with no name yet it
  lists the spells of the world's books the corpus links to most (`registry.link_counts`,
  `SearchService.browse`; for `npc`, `encounter`, `treasure`, `character` and `chapter`
  the world's own, by name); inside a tag already written — the cursor anywhere in
  `{@creature Gob|lin|MM|the goblins}` — it searches the whole name and completing
  replaces the name and a reference's book, keeping the label; after Esc it opens again.
  Elsewhere Ctrl+Space is left to the system (on a Mac it may switch the input source).
  Choosing a kind from the `{@` list goes straight on to that list.
- **Before a name**, the same drop-down lists the kinds: `{@` what a tag may name
  (creature, spell, item, condition, npc, encounter, treasure, character, chapter, then
  every other entity tag), `> [!` the callouts (read, secret, sidebar, then Obsidian's: info, note, abstract,
  quote, tip, warning, danger…). Choosing one
  writes it and goes on to its names (`/search/kinds`).
- **Creating in place**: an NPC, encounter or treasure that nothing has the name of yet is
  offered — "Create NPC “Garaele”, new, in this chapter" — and created where the editor's
  record is (a chapter, an adventure, an NPC's or treasure's own scope;
  `POST /gm/editor/create`), then named in the text. A name already there is not made twice.
- **A toolbar** above each editor (2026-10-26): heading (cycling `#`, `##`, `###`, none),
  bold and italic (also ⌘B / ⌘I; pressed on marked text they unmark it), a bulleted and a
  numbered list of the lines selected (off again when they already are one), a link
  (`[text](https://)` with the address selected), read-aloud and secret boxes around the
  lines selected, a reference on its
  own line (writes `{@` on a line of its own, which opens the kinds drop-down), the insert pop-up
  (⌘K), a table, an image from a file — and, in the chapter editor, **full-width writing**
  without the preview, kept per browser. The chapter's preview **follows the cursor**: it
  scrolls to the heading the cursor is under, or the same share of the way down in a text
  without headings.
- **⌘Z undoes what the editor wrote** (2026-10-26): every insertion — a completion, the
  insert pop-up, the toolbar, a chapter-check fix, a dropped image — goes through one
  helper (`ParaventText.replace` in `static/js/ui.js`) that edits with
  `execCommand('insertText')`, the one edit the browser's undo history records
  (`setRangeText` where it is missing). An uploaded image's line replaces its placeholder
  without moving the writer's cursor.
- **A save never overwrites unseen changes** (2026-10-26): the chapter and adventure
  editors, and the notes of an NPC or a treasure, carry a fingerprint of the text they opened (`base`, `text_version`). When the
  text on disk has changed since — a hand edit of the `.md` file, another tab, the iPad —
  nothing is written: the editor comes back (409) with the typed text and, beside it, the
  text saved now; saving again replaces it deliberately. Only the text counts, so a new
  title or order elsewhere is no clash, and a save that says what the file says already
  goes through.
- **Words selected before ⌘K** become the query and stay as the link's text:
  "the goblins" turns into `{@creature Goblin||the goblins}`, "Maren" into
  `{@npc Maren Holt|Maren}`.

### 5.7 Checking a chapter before play

The chapter editor shows, above the preview and refreshed with it, **what in the text
leads nowhere** (`app/ui/prose_check.py`, `POST /gm/chapters/{id}/check`, built
2026-10-26). The text is read line by line, code left out, and judged by the same
callbacks the page renders with, so the check and the page cannot disagree:

| Finding | Mend |
| --- | --- |
| A `{@…}` tag no book has | the three nearest names the world's books do have, one press replacing it on its line |
| … in a book the world leaves out (`{@spell Fireball|XPHB}` in a 2014 world) | — declare the book, or name another |
| `{@npc …}`, `{@encounter …}`, `{@treasure …}` naming nothing in the world | "Create" — made where the chapter is |
| `{@character …}` naming nobody placed in the world | — place the character in the world |
| An encounter alone on its line without creatures, an image that is not there; in older texts a `:::` block of an unknown kind or never closed | — |

Each finding names its line; a press selects it in the editor.

### Rejected

| Rejected | Why |
| --- | --- |
| Raw HTML | Lets prose break the page and, later, leak into player views |
| `[[wiki links]]` alongside `{@npc}` | Two syntaxes for one thing; tags already have previews and scope |
| Defining an encounter's monsters inside Markdown | It could not be rated, edited or run; the object owns its data |
| Order by file-name prefix (`01-…`) | Reordering would rename files open in your editor |

---

## 6. Rendering

```txt
.md file → front matter (strict model) → markdown-it tokens
        → tags (tags.py, with the scope and the books) + embeds (live objects) → htpy
```

- **Books:** the adventure's `books`, else the world's — `Adventure.effective_source_set()`
  as now — **plus `based_on`'s own source**, implicitly: a published adventure's text names
  creatures that exist only in it, `{@creature Name|SOURCE}` with the adventure's own
  source, and those must resolve.
- **Cache key:** file mtime + source-set identity + language. Embeds are rendered fresh — they
  are live objects.
- A changed file updates the open chapter page over SSE, as a hand-edited character does.

---

## 7. Published adventures as a base

The corpus holds **99 adventures**: an index (`adventures.json` — cover, level range, table of
contents) and a body per adventure (`adventure/adventure-<source>.json`, a `data` array of
sections). **Every section and named entry carries a stable `id`** — one of the shorter
adventures has 11 sections and 306 entries with ids (`"00e"` is the entry of one of its
encounters). Those ids are the anchors.

### Starting from a book

"New adventure from a published one" creates `adventure.md` with `based_on:` set to the book's source code, and one
chapter file per book section, each with `section:` set and an empty body. Nothing of the
book is copied — the text is read from the corpus every time, as all reference data is.

### The overlay

A based chapter renders the **book's section** through the entries renderer, with the
gamemaster's own Markdown layered in:

```markdown
---
title: The Road South
section: 00c
---
Notes here, before any anchor, show at the top of the chapter.

:::: at 00e
::: encounter Ambush on the Road
:::
Two of the goblins are deserters — they parley if hurt.
::::
```

(An outer container needs a longer fence than the ones inside it — `::::` around `:::` —
or the first `:::` closes it. That is markdown-it's rule, and the editor's insert actions
must follow it.)

- `::: at <id>` places its content **after** that book entry. The chapter page shows each
  anchored block beside or below its entry, visibly marked as the gamemaster's.
- **An anchor that no longer exists** (a corpus update renumbered the book) is shown at the end
  of the chapter under "Notes whose place in the book was not found" — reported, never dropped,
  the same rule as an orphaned translation.
- The book's `insetReadaloud` and `inset` render as the `[!read]` and `[!sidebar]` callouts, so
  the gamemaster's text and the book's look the same.
- **Images:** shown from the 5eTools image folder (`PARAVENT_IMAGE_DIR`) when it holds them,
  each with its share button (JOURNAL.md); a captioned placeholder otherwise.
- A helper worth building with it: "creatures in this section" — every `{@creature}` the
  section mentions, each one click from an encounter.

### What it needs from the data layer

The **section model** (PROGRESS.md, corpus Phase 3) for `adventure` bodies: a recursive model
for `data`, keyed by entry `id`, and **loaded on demand per adventure** rather than at start,
so 99 bodies never touch cold-load time. `book` bodies use the same model later.

---

## 8. Build order

1. ~~**The prose store**~~ — **done 2026-10-16**: `app/store/prose.py` (worlds, adventures,
   chapters behind the same `store.worlds`/`.adventures`/`.chapters` interface as before),
   `app/services/media.py` + `GET /media/{id}/{path}`. Deleting moves a file or folder to
   `library/.trash/`. Bodies are held without their surrounding whitespace. Measured on the
   dev server: a hand edit to `world.md` reaches the page in about a second.
2. ~~**The Markdown dialect**~~ — **done 2026-10-16**: `app/ui/markdown.py`, markdown-it-py
   + the `container` plugin. `render_markdown(text, MarkdownContext)` returns the HTML and the
   table of contents; what names mean comes in as callbacks (`link`, `embed`, `media`), so
   the services in steps 3–4 plug in without touching the renderer. No cache yet — rendering
   is fast, and embeds are live anyway.
3. ~~**Adventure and chapter pages**~~ — **done 2026-10-16**: `app/routes/adventures.py`,
   `app/ui/adventure_pages.py`, `app/services/adventures.py`, `static/js/prose.js`. The world
   page (`/gm/worlds/{id}`; the form moved to `/edit`) lists adventures; an adventure lists
   chapters; both reorder with ↑/↓. A chapter reads with a table of contents and follows edits
   made anywhere over SSE (a hand edit reached an open page in under a second). The editor
   previews as you type, ⌘S saves, and works without JavaScript through a preview button.
   Images: upload, use as cover, copy the line to paste. `::: encounter` shows the live
   encounter with its rating; `::: statblock` the full statblock, folded.
   **Found on the way:** GFM tables split cells at every pipe *before* inline parsing, so
   `| {@creature wolf||Three wolves} |` broke the row. Pipes inside tags are escaped first
   (code left alone) and unescaped by the tag rule.
4. ~~**NPCs and treasure**~~ — **done 2026-10-16**: `app/services/npcs.py`,
   `app/services/scope.py`, `app/routes/cast.py`, `app/ui/cast_pages.py`; records in
   `npcs.json` and `treasures.json`. Treasure items are added through the ⌘K picker, as
   monsters are to an encounter. Encounters carry the same scope as flat fields
   (`world_id`/`adventure_id`/`chapter_id`) because an encounter may belong to no world.
   Adventure and chapter pages list what they hold, each with the line to paste, and create
   new objects already placed there. Three decisions made while building it:
   - **Lookup ends with "anywhere in the world".** Nearest scope wins, but a chapter's NPC
     mentioned from another chapter still links rather than going dead.
   - **Deleting an adventure or chapter lifts what it held** to the parent scope; the text
     goes to the trash, the NPCs and treasure it placed stay.
   - **A portrait lives in its scope record's `media/`**, so moving an NPC to another scope
     drops a portrait that would no longer resolve (the file stays).
5. ~~**Published adventures**~~ — **done 2026-10-16**: `app/store/published.py` (the index, and
   bodies read on demand into a cache of six, cleared on a corpus reload — a short adventure 3 ms, a long
   one 20 ms), `app/ui/book_pages.py`, "Start from the book" on the world page. As built:
   - The section model is a **thin typed view over the corpus's dicts** (sections, an id
     index, the named places), not a Pydantic tree: the reference side is lossless and
     nothing here validates or rewrites it.
   - `based_on` stores the index **id**, which names the body file; the **source** the
     book's tags cite is looked up from the index (they differ for `MOT-NSS` → `MOT`).
   - Named entries render as headings, each with a "+" that opens the editor with
     `:::: at <id>` ready; the contents list shows the book's top two levels.
   - The entries renderer got one generic addition, `RenderContext.block_hook`, which is how
     notes are woven in, images become placeholders and `statblock` entries become links or
     folded statblocks.
   - "Creatures in this section" adds a creature to one of the chapter's encounters, or to
     a new one named after the chapter.
6. Then **the combat tracker**, started from an embedded encounter — see COMBAT.md.
