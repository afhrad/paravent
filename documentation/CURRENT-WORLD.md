# The current world

**Scope:** one world the whole table plays in, chosen by the gamemaster; what the navigation,
the pages, characters and players do with it. **Status:** planned and built 2026-11-19 on the branch
`world-redesign`, all six steps (§7).

> **One sentence:** the gamemaster selects a world, and from then on everything in Paravent —
> every list, every page, every character and every player — plays in it.

## 1. The user's decisions (2026-11-19)

| Question | Decision |
| --- | --- |
| Who the selected world applies to | **The whole table.** One current world, chosen by the gamemaster; players follow it (their books, party, fights, journal, log). |
| The "Worlds" menu item | **Gone.** The gamemaster has a world menu: select, create, delete a whole world, and a **settings dialog** for its parameters. |
| The top bar | The current world's links: **Adventures, NPCs, Places, Treasure, Encounters, Combat, Quests, Factions, Journal, Sessions** (plus Table, Characters, Books). |
| Characters | **Belong to no world.** Every character of an active player is in the current world's party. |
| A page of another world (an old link, a search result) | **Opens with a banner** naming its world and a button to switch; nothing switches by itself. |

## 2. The model

- **The table's world** is one small library record (`library/table.json`: `{"world_id": …}`),
  read and written by `TableService` (`registry.table`). It is library data, backed up, set only by
  the gamemaster. Missing or naming a deleted world: the first world by name, or none.
- **Characters** keep no world of their own. `PlayerCharacter.world_id` stays in the model but is
  **derived**: the character store fills it with the table's world when it serves a character and
  never writes it to the file (old files lose the field the next time they are saved). So the ~214
  places that read `pc.world_id` — books for the sheet, the wizard and level-up, the party, the
  clock, rests, travel, the shop, quests, factions, the log — follow the current world without
  each being changed, and switching worlds re-derives every sheet (its cache key holds the world).
  Nothing writes it (2026-11-20: the wizard's new draft, the world delete's loop that
  "moved" characters out and the unused `WorldService.place` went); the reads stay, as they
  name the world a character plays in, which is the table's by construction. **The party of a
  world** is asked in one place, `CharacterStore.of_world(world_id, drafts=…)`: every
  character for the table's world, none for another (2026-11-20; 27 hand-written filters went).
- **Everything else keeps its world.** Adventures, chapters, NPCs, places, treasure, encounters,
  quests, factions, session notes, the clock and the log stay filed under their world exactly as
  now; only *which world is shown* changes. Nothing is migrated.

## 3. The gamemaster's world menu

At the left of the top bar: the current world's name with a chevron (a `<details>` menu, closing on
an outside click like the others):

- the worlds, the current one ticked — choosing one makes it the table's world (POST, back to the
  same section of the new world);
- **New world** — a dialog with its name and books (the rest in settings), selected once made;
- **World settings** — a dialog with today's world form (name, description, books and player
  books, naming styles, calendar, milestone levelling, instructions for Claude, cover);
- **Delete world** — today's confirmation page (with its contents), then the next world is selected.

With no world yet, the menu reads **Create a world**, and pages that need one show an empty state
with that button.

## 4. The navigation

Gamemaster, left to right: world menu · **Table** · Characters · Adventures · NPCs · Places ·
Treasure · Encounters · Combat · Quests · Factions · Journal · Sessions · Books — then sound, log,
search, settings gear, user menu.

Thirteen links do not fit as words: wide screens show icon and word, narrower ones the icon alone
(the word as its title and for screen readers), and what still does not fit moves into a **More**
menu at the bar's end, in order. On a phone the bottom bar keeps Table, Adventures, Encounters,
Combat and **More** (a sheet with the rest). Each item still remembers the last page read in it
(app/ui/nav.py), now per world.

Players: unchanged (Characters, Books, Journal), all of it the current world's.

## 5. Pages

- **New addresses for the current world's lists:** `/gm/adventures`, `/gm/npcs`, `/gm/places`,
  `/gm/treasure` (beside `/gm/encounters`, `/gm/combat`, `/gm/quests`, `/gm/factions`,
  `/journal`, `/gm/sessions`). The old `/gm/worlds/<id>/…` addresses redirect to them when `<id>`
  is the current world, and otherwise show the page with the banner (§1).
- **World filters go:** the party view, encounters, quests, factions, the session page, the
  journal and the clock panel show the current world; their world pickers are removed.
- **The banner:** an object page (NPC, place, treasure, encounter, adventure, chapter, quest …)
  whose world is not the current one says "This belongs to *World*" with **Switch to it**.
- **Characters' world chooser** (`/gm/characters`, the wizard's world step) goes; a new character
  is made with the current world's books.
- **The world page** becomes **Adventures**: the adventures and "start from the book"; its
  description and links move to the settings dialog and the top bar.

## 6. What else follows

- **Players** read the current world's player books, journal, safety tools and fights.
- **The log** stays one file per world: a character's log shows the current world's entries.
- **MCP:** tools keep their explicit world ids; `list_worlds` marks the current one, and a tool
  given no world uses it.
- **Help pages, PROGRESS.md, ADVENTURES.md, the website** describe the world menu instead of the
  Worlds section.

## 7. Steps

1. ~~`TableService` and the world menu (select, new, settings dialog, delete); the current world in
   `Page`.~~ **Built 2026-11-19:** `TableSettings.world_id` beside the table's theme
   (`app/services/table.py`), `Page.world`/`Page.worlds`, the menu and its two dialogs
   (`app/ui/world_menu.py`; the settings dialog loads `GET /gm/world/settings`, the world form
   shared with its page, its pictures still on that page), `POST /gm/world/select`; a new world
   is the table's at once with its settings open. Until step 2 the gamemaster's links show their
   icons only between 761 and 1599 px, so the bar stays one row beside the menu.
2. ~~The navigation: the current world's links, icons and More, the phone's bottom bar, per-world
   memory.~~ **Built 2026-11-19:** `NAV` in `app/ui/nav.py` (a link may also cover a page by
   pattern, `^/gm/worlds/[^/]+/npcs`; `rank` 0–3; `world` sections), the Worlds item gone. The
   bar shows by width: below 761 px Table, Adventures, Encounters, Combat and More, the world
   menu on a line of its own above them; 761–1279 px those and Characters as icons; 1280–1599 px
   also NPCs, places and treasure as icons; 1600–1799 px the first five in words; from 1800 px
   NPCs, places and treasure in words too. More holds exactly the rest. The short addresses
   `/gm/adventures`, `/gm/npcs`, `/gm/places`, `/gm/treasure`, `/gm/sessions` draw the current
   world's page (`app/routes/current_world.py`), or ask for a world when there is none.
   **Per-world memory** is simpler than planned: choosing another world forgets where the
   world's sections were left (`PlaceService.forget_sections`), and choosing it on a page of
   a world section leads to the same section of the new world.
3. ~~The new list addresses, old ones redirecting; the banner on other worlds' pages; the world page
   as Adventures.~~ **Built 2026-11-19.** The old `/gm/worlds/<id>/…` addresses **do not
   redirect** after all: they keep showing their page, with the banner while `<id>` is not the
   table's world — the lists' own links, the redirects after a save or delete and every test
   keep working unchanged. **The banner** needs no page's help: `Page.page_world` is the world
   the address belongs to (`world_of_page`: a world's own pages, adventures, chapters, NPCs,
   places, treasure, encounters, session notes), and the shell shows "This belongs to *World*;
   the table plays in *Current*" with **Switch to *World*** (`world_banner`); switching from a
   page of the world chosen stays on it. **The world page** is headed *Adventures* under the
   world's name, with its cover, description, bosses and adventures; its Edit and Delete and
   its row of links are gone, *Names in this world* and *Instructions for Claude* moved into the
   world menu. **Trails** no longer start at the worlds' list (`crumbs` leaves `/gm/worlds` out).
4. ~~World filters removed from the party view, encounters, quests, factions, session, journal and
   clock.~~ **Built 2026-11-19.** Each page still takes `?world=` (its own forms and fragments
   pass it), but with none named it shows the table's world, where before it showed every world
   or the only one: `WorldService.find_or_current` (quests, factions, the session page), the
   party view and its clock, shop, chase and "in play" panels (`_clock_world`), the journal
   (handouts and party notes, for players too), and the encounters list — the table's world's
   encounters and those planned ahead of any world, a new one made in the table's world. The
   party view's and the encounters' world pickers are gone; the creature builder's new design
   starts in the table's world. The guess at "the world worked in" from the navigation's memory
   (built earlier that day for new encounters) gave way to the table's world.
5. ~~Characters world-free: the derived `world_id`, the chooser and the wizard's world step removed,
   players following the table's world.~~ **Built 2026-11-19.** `CharacterStore.world` (set by
   the registry to the table's world) fills each served character's `world_id`, a copy kept until
   the character or the world changes; `serialize` leaves the field out, so files lose it at their
   next save (a store used without the registry, as in tests, keeps each file's own). The sheet's
   cache already keyed on the character and its books, so a switch re-derives every sheet. The
   character list's world chooser and its route are gone; a new character is made in the table's
   world (the new-character page names it, `/chargen` ignores a world sent); deleting a world
   says the characters stay. **What follows, by design:** every character is in the party, the
   rests, travel, checks and the shop; a character's books are the table's world's (a world
   declaring only the Monster Manual gives a tiefling no handbook resistance); a chapter's
   `{@character}` finds every character. Measured on the demo library: the party view 23 → 27 ms,
   a sheet 36 → 39 ms, the encounters 8.5 → 11 ms, for the whole redesign.
6. ~~MCP defaults, help pages, documentation, website; the full suite and a browser pass on desktop
   and phone in both languages.~~ **Built 2026-11-19.** `list_worlds` marks `current`, and every
   tool taking a `world_id` reads an empty one or `current` as the table's world
   (`app/mcp/common.current_or`; the parameters stay required, so their order is unchanged);
   the server's instructions say so. The start, between-sessions, combat and Claude guides
   describe the world menu and the top bar; ADVENTURES, JOURNAL, NAMES, SETUP, MCP, PROGRESS and
   CHARACTER-FORMAT follow.

## 8. Rounded off (2026-11-19)

- **The settings dialog holds the cover and pictures** too (`images_panel`), its forms coming
  back to the page it was opened on with the dialog open again (`?settings=1`). Scripts in the
  dialog's fetched content do not run by themselves: `ui.js` loads each one the page lacks and
  then announces the new content (`htmx:load`), which starts the picture tool.
- **Players see the world** the table plays in on their home page ("You are playing in …").
- **The worlds' list is gone**: `/gm/worlds` leads to the table's world's Adventures, as does
  deleting a world; with no world, those pages offer *Create a world* and the getting-started
  guide. The translation keys only that list, the pickers and the character chooser used went.
- **Open pages follow a switch** (2026-11-20): choosing another world, making one, or deleting
  the one played in publishes the table's world (`TABLE`, as "<id> <name>") through
  `TableService.announce`; every signed-in page watches `/table/events` and, when the world
  differs from the one it was drawn in (`#world-notice`'s `data-world`), says "The table now
  plays in …" with **Reload** (`static/js/table-world.js`). Nothing reloads by itself: a player
  may be typing. A choice is compared with the one kept, not the world played in, since a new
  world first by name is played in at once when none was ever chosen.

## 9. A world to take along (2026-11-20)

The world menu also offers **Check links** (every broken link of the world, ADVENTURES.md
§5.7). It offers **Export world**, **Import world** and **Duplicate world**
(`app/services/world_transfer.py`, `app/routes/world_transfer.py`).

- **Export** downloads the table's world as one zip (`<name>.paravent.zip`): a manifest
  (`paravent-world.json`, format 1), its folder under `world/` (the world, adventures and
  chapters as Markdown, pictures, `tags.json`, tokens), every record filed under it in
  `records.json` — NPCs, places and treasure by their scope; encounters, Claude's
  instructions, factions, quests, creatures, combats, session notes, checks and handouts by
  `world_id`; the safety tools, what the party learned, party notes, the clock, the chase,
  the session and the shop by their id being the world's; naming styles made for it — its log
  (`protocol.jsonl`) and its handouts' pictures (`handouts/`). Characters stay out: they
  belong to the table.
- **Import** makes a new world of such a file, here or at another table, and the table then
  plays in it. Every id the file holds — the world's, its adventures' and chapters' from
  their front matter, each record's — is given anew and replaced wherever it is written (front
  matter, records naming each other, Markdown links, token file names), so an import never
  clashes, not even with the world it came from; anything else shaped like an id (a
  character's) is left. It takes the campaign; **Bring the play so far** adds the log, clock,
  fights, checks, handouts, party notes, session notes, what the party learned and the shop.
  Without it quests start open with nobody on them and factions with no renown. A creature
  design whose name the table already has is not taken (one statblock per name in the
  homebrew file); the others come unpublished. Refused: a file that is no zip, holds no
  world, comes from a newer format, names paths outside itself, or is larger than the limits
  (10,000 files, 200 MB a file, 4 GB in all); only Markdown, JSON and pictures (checked by
  their content) are written. The dialog opens again saying why.
- **Duplicate** is an export read straight back in under a new name ("… (copy)"), the play
  optional: the same campaign for a second group.
- **A name the table has** gets a number, from an import or a duplicate alike ("Azeroth (2)"),
  so the world menu tells the worlds apart.
- **Tried on the real library** (2026-11-20, on a copy): a world of 18 chapters, 11 MB of
  pictures and 261 log entries exported in 0.3 s and came back in 0.1–0.5 s, imported and
  duplicated, with and without the play: every chapter, picture and token there, no old id
  left, the log in the new world, the chapter check reporting what the original reports once
  the table plays in it (a character link resolves only in the table's world).

