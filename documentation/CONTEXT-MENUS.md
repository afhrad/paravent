# Context menus

**Status:** built 2026-11-21, the rest 2026-11-22 (§10 says what). The decisions of §9 were
taken as proposed.

A context menu gathers the actions on one thing where it is: a right-click on a tracker
row, a party card, an NPC in a chapter, an item on the sheet. It is a shortcut, never the
only way: every item in a menu is also reachable from the object's own page or a visible
button. Menus are built on the server like every other piece of the UI, so a menu offers
only what the reader's role may do, and the routes check it again.

---

## 1. Principles

- **A shortcut, never the only path.** Whatever a menu offers is also on the object's page,
  in its form or behind a visible **⋯** button. A long press cannot be seen, so nothing may
  depend on it.
- **Only where several actions meet.** An element gets a menu when it carries three or
  more actions that otherwise take a page change. Text, editors and form fields keep the
  browser's own menu (copy, paste, spelling).
- **One definition, two doors.** A kind's menu is defined once in Python and drawn both as
  the right-click / long-press menu and as the **⋯** drop-down beside the element.
- **The server decides.** The menu is a fragment fetched from the server for the reader:
  a player's menu on a sheet item is shorter than the gamemaster's; the routes behind it
  enforce the role regardless.
- **The browser's menu stays reachable.** Shift + right-click opens the browser's own menu
  (open in a new tab, copy link). Links in a menu that lead somewhere also offer "Open in a
  new tab".

## 2. Where

| Element | Items | Who |
| --- | --- | --- |
| **Tracker row** | damage / heal, conditions, side, hide / show, split the group, shape (Polymorph), open the sheet or statblock, remove | gamemaster — today's ⋯ menu, opened by right-click too |
| **Party card** | damage / heal, condition, call for a check, give a gift, grant a level, open the sheet, downtime | gamemaster |
| **Campaign link or card in a chapter** (NPC, place, encounter, treasure) | open, edit, run (an encounter), rename, share with players, move up to the adventure / world, delete | gamemaster |
| **Missing link** (the dashed + link) | create in this chapter / adventure / world, "it probably means …", link to an existing one | gamemaster |
| **Sheet item** | equip / unequip, attune, use a charge, cast from it, sell, give to …, drop | player (own) and gamemaster; identify / curse for the gamemaster |
| **Sheet spell** | cast at a level, prepare / unprepare, open the full entry | player (own) and gamemaster |
| **List row** (adventures, chapters, NPCs, places, treasure, encounters, quests, factions, session notes, sounds, creatures) | open, edit, duplicate, new … here (adventure, chapter), move, delete | gamemaster |
| **Map** (a row of *Maps* or an adventure's, the ⋯ on its page; MAPS.md) | open, open in a new tab, edit, copy or move to another world (a world's map only), delete with Undo | gamemaster |
| **Picture in a chapter** | share with players, frame and effects, open the original, where it is used | gamemaster |
| **Log entry** | reveal to players, only this character, only this fight | gamemaster (filters for players too) |

Not on 5eTools reference links: nothing adds a creature or item from a tag (DESIGN.md §6),
and the preview already offers the other printing. At most "Open in a new tab".

## 3. Input

| Input | Opens with | Shows as |
| --- | --- | --- |
| Mouse | right-click | a popover at the pointer, kept inside the window |
| Trackpad (also on an iPad) | two-finger click: the same `contextmenu` event | the popover |
| Touch | **long press**, about 450 ms, cancelled if the finger moves more than 8 px or lifts | a **bottom sheet** on a phone (as the reference previews on touch, DESIGN.md §7), the popover on a tablet |
| Any | the **⋯** button beside the element | the same menu as a drop-down |
| Keyboard | the ContextMenu key or Shift + F10 on the focused element | the popover beside the element |

- iOS Safari never fires `contextmenu` on a long press, so the script times the press
  itself (`pointerdown` … `pointerup`), on every touch browser alike.
- Elements with a menu set `-webkit-touch-callout: none` and `user-select: none` on touch,
  so a long press does not start a text selection or the link callout.
- Android gets a short vibration (`navigator.vibrate(10)`) when the menu opens.
- The tap keeps its meaning: a tap on a tracker row still selects it, a tap on a reference
  still opens its preview. Only the long press opens the menu.
- Which shape is chosen by `(pointer: coarse)` and width, never by user agent, as the
  previews do.
- The menu is `role="menu"` with `menuitem`s; arrow keys move, Enter acts, Esc closes and
  returns focus to the element. The bottom sheet closes by tapping outside, by Esc or by a
  downward swipe.

## 4. How it is built

```txt
<li class="combatant" data-menu="/menu/combatant/<combat>/<id>"> … <button data-menu-open>⋯</button>
        │  right-click · long press · ContextMenu key · ⋯
        ▼
static/js/menu.js ── GET /menu/<kind>/<id> (cached per page, HX-Request) ──► app/routes/menus.py
        ▲                                                                     │ looks up the
        └──────────── the fragment: <div class="ctx-menu" role="menu"> ◄──────┘ kind's builder
```

- **`app/ui/context_menu.py`** (new): `MenuItem` (label, icon, and one of: `href`, `post`
  with its fields, `dialog` URL to open, `tool` key of a page's dialog; `danger`;
  `disabled` with a reason), `MenuGroup`, `menu_fragment(page, items)` for the popover or
  sheet, `menu_button(page, url, label)` for the ⋯, and `data_menu(url)` for the element.
- **The builders** live beside their pages: `combatant_menu` in `combat_pages.py`,
  `npc_menu` in `cast_pages.py`, `item_menu` in `sheet_pages.py`, …, each returning items
  for the page's reader.
- **`app/routes/menus.py`** (new): `GET /menu/{kind}/{id…}`, which loads the record, checks
  the reader may see it (404 otherwise, as everywhere) and returns the fragment.
- **`static/js/menu.js`** (new, small): opens and places the menu, the long-press timer,
  keyboard, closing; `htmx.process` on the fragment so its buttons post through htmx and
  swap what they change. Without the script there is no menu, and the ⋯ button falls back
  to the object's page.
- **CSS** in `static/css/app.css`: `.ctx-menu` from the user menu's panel (surface, border,
  large corner, shadow), rows 40 px (44 px on touch), a danger item in `--dmg-text`,
  separators; the bottom sheet from `.tip-sheet`.
- **The tracker's ⋯ menu** (`combat_pages.py`, `.cb-menu`) becomes the first builder; its
  forms and their htmx swaps stay as they are.

## 5. Creating and deleting with menus

### Today

- **Create**: every list has a **New …** button that opens a dialog (`tool_dialog`,
  `?tool=<key>` without the script; DESIGN.md §4 "Making something new"). A missing link in
  a chapter offers to create what it names (ADVENTURES.md §5.2).
- **Delete**: on the object's own page or editor, a **Delete** link leads to a
  confirmation page (`confirm_page`); a chapter, adventure or world asks there how much
  goes with it. The trashed kinds (NPC, place, treasure, encounter, quest, faction,
  chapter, adventure, world; `app/services/trash.py`) then wait 30 days under *Recently
  deleted*, and the list the delete lands on shows **Undo** (`undo_notice`). Characters,
  players, fights, creature designs, naming styles, sounds and session notes are deleted
  for good, after the confirmation.

### With menus

| Case | From the menu | Why |
| --- | --- | --- |
| A trashed kind without contents: NPC, place, treasure, encounter, quest, faction | **Delete** acts at once. The row leaves in place, and the **Undo** bar appears at the top of the page as today | Undo is safer and quicker than a question: nothing is lost for 30 days |
| A trashed kind with contents: chapter, adventure, world | **Delete…** opens the confirmation as a **dialog**, with the same choice of what goes with it; then the list and **Undo** | The choice must be made, and a page change for it is a detour |
| A kind deleted for good: character, player, fight, creature design, naming style, sound, session note | **Delete…** opens the confirmation dialog, saying it cannot be undone; never one click | Nothing to undo with |
| Create in a container | An adventure row or chapter card offers **New NPC / place / encounter / treasure here**, which opens the existing New dialog with its place filled in | The dialog already asks where; the menu only answers it |
| Create from a missing link | **Create in this chapter / adventure / world**, the likely meant names, **Link to an existing one** | Today's click dialog, its choices as menu items |
| Copy | **Duplicate** on a list row: the same record as "Name (copy)", in the same place, opened | New: a service method per kind |
| Move | **Move up to the adventure / world** on a placed NPC, place, treasure or encounter | Today's holdings rule ("taken out of a chapter, up to its adventure"), offered where the object is |

The confirmation pages stay: they are the path without the script, and the object's own
page keeps its **Delete** link.

## 6. What changes where

| Where | Change |
| --- | --- |
| `app/ui/context_menu.py` | new: `MenuItem`, `MenuGroup`, `menu_fragment`, `menu_button`, `data_menu` |
| `app/routes/menus.py`, `app/main.py` | new: `GET /menu/{kind}/{id}`, registered |
| `static/js/menu.js`, `app/ui/layout.py` | new script, loaded on signed-in pages like `tooltips.js` |
| `static/css/app.css` | `.ctx-menu`, `.ctx-sheet`, `[data-menu]` touch rules |
| `app/ui/library_links.py` | `confirm_page` gets a dialog form (`confirm_dialog`) built from the same parts; the delete routes answer an htmx request with it |
| delete routes (`cast.py`, `locations.py`, `encounters.py`, `quests.py`, `factions.py`) | answer an htmx POST with the row removed (`hx-swap-oob="delete"`) and the Undo bar placed out of band, instead of a redirect; without htmx the redirect stays |
| `adventures.py`, `gm.py` (worlds) | `GET …/delete` answers htmx with `confirm_dialog` |
| list pages (`cast_pages.py`, `location_pages.py`, `encounter_pages.py`, `adventure_pages.py`, `quest_pages.py`, `faction_pages.py`, …) | rows get `data-menu` and a ⋯ button; an `#undo-slot` for the bar; the builders |
| `combat_pages.py` | `.cb-menu` built from `combatant_menu`, opened by right-click too |
| `gm_pages.py` | party cards get `data-menu`; `party_card_menu` |
| `app/ui/markdown.py`, `adventure_pages.py` | campaign links and cards, missing links and pictures get `data-menu` (gamemaster only) |
| `sheet_pages.py` | items and spells get `data-menu` and a ⋯; the item sheet already has the forms |
| services (`npcs.py`, `locations.py`, `treasure.py`, `encounters.py`) | `duplicate(id)` |
| i18n | `menu.*` labels in both catalogues |
| tests | each builder's items per role; the menu route's 404s; the htmx delete's out-of-band answer; a script-less fallback |

## 7. Steps

1. The system: `context_menu.py`, the route, `menu.js`, the CSS, and **tracker rows** (today's
   ⋯ menu) and **party cards**.
2. **List rows** with Delete and Undo in place, confirmation dialogs, Duplicate.
3. **Chapter links, cards, missing links and pictures.**
4. **The sheet's items and spells** (players too), and log entries.

## 8. Mock-ups

Made 2026-11-21 by injecting the proposed menus into real pages of the website's demo
world (Tome theme), not from built code. They were handed to the user for review and are
not kept in the repository:

| Picture | Shows |
| --- | --- |
| `ctx-01-npc-list.png` | right-click on an NPC row: open, edit, duplicate, move, delete |
| `ctx-02-undo.png` | after **Delete**: the row gone, the Undo bar at the top |
| `ctx-03-chapter-link.png` | right-click on an NPC link in a chapter |
| `ctx-04-missing-link.png` | the menu of a missing (dashed) link: where to create it |
| `ctx-05-tracker.png` | the tracker row's menu by right-click |
| `ctx-06-party-card.png` | a party card's menu |
| `ctx-07-delete-dialog.png` | **Delete…** on an adventure: the confirmation as a dialog |
| `ctx-08-phone-item.png` | a long press on a sheet item on a phone: the bottom sheet |

## 9. Decisions (2026-11-21)

Taken as proposed when the user asked for the menus to be built:

- **Delete without a question** for the trashed kinds without contents, relying on Undo;
  a confirmation dialog for those with contents and those deleted for good.
- **The long press** is 450 ms.
- **Duplicate** for NPCs, places, encounters and treasure.
- **Players' menus** on their own sheet's items and spells only.

## 10. As built

- `app/ui/context_menu.py` (`MenuItem`, `MenuGroup`, `menu_fragment`, `menu_button`,
  `row_menu`, `data_menu`), `app/routes/menus.py` (`/menu/link`, `/menu/party/{id}`,
  `/menu/item/{id}/{n}`, `/menu/spell/{id}`, `POST /menu/duplicate`, `POST /menu/move`),
  `static/js/menu.js` on every signed-in page, `app/services/copies.py`.
- **List rows** of NPCs, places, treasure, encounters, adventures and chapters: the trash
  icon became the **⋯**, and a right-click or long press anywhere on the row opens it.
  **Delete** of an NPC, place, treasure or encounter acts at once and returns to the page
  it was asked from with **Undo** (`back_with_undo` in `app/routes/auth.py`; a `next` that
  names the gone object's page or another site falls back to its list). **Delete…** of a
  chapter or adventure opens `confirm_page` as a dialog (`?dialog=1`, its form carrying
  `next`). **Duplicate** copies the record in place ("(copy)", numbered). **Move up**
  uses the holdings rule.
- **Campaign links and missing links** in every text the gamemaster reads: by their href,
  no markup of their own. A missing link offers the chapter, adventure or world to create
  it in (`POST /gm/editor/new`), what it probably meant and the old page's choices.
- **New … here** from a chapter's or adventure's menu opens the lists' own New dialogs
  with `?place=` (`preset_place` in `app/ui/library_links.py`, a scope or nothing).
- **Party cards** (`data-menu` and a ⋯ in the head) and **tracker rows** (`data-menu-local`:
  the right-click opens the row's own ⋯ menu).
- **The sheet's items and spells**, for the player and the gamemaster: the row's actions
  through htmx onto `#sheet`, the full entry, casting at each open level, preparing.
- A menu stays open through live updates elsewhere and closes when its element is swapped
  away.

**The rest (2026-11-22):**

- **Lists deleted for good** — session notes, creature designs, sounds, naming styles (the
  table's own, in the workshop) and both character lists (the gamemaster's and a player's
  `/play`): the trash icon became the **⋯**, whose **Delete …** opens the confirmation as a
  dialog saying "for good". Without the script the ⋯ leads to the confirmation page. A row
  that is no `.list-row` takes a right-click through `data-menu-row`. `/menu/link` knows
  `/gm/sessions/…`, `/gm/creatures/…` and `/gm/names/…`; `/menu/character/{id}` serves a
  player their own character's pages, `/menu/sound/{id}` plays a sound on the speaker or
  here. The creature and character confirmations are `confirm_page` now (`eyebrow`).
- **Pictures in a chapter** (`/menu/image?share=…&src=…`, from the share button's
  address): share with the players, the original in a new tab, **Frame and effects …**
  (the chapter's editor), and the other places of the world that show it
  (`MediaUses`), the chapter itself left out.
- **Log entries** (`/menu/log/{entry}`, `?reveal=1` while the players do not read it in
  full): **Reveal**, **Only <character>** for its subject and target, their sheets, **Only
  this fight**, **The whole log** — as the reader may (a player only their own
  characters). In the log panel the menu sits inside the open dialog, and its items carry
  the panel's own `data-log-query` and `data-log-reveal`, so the panel filters in place
  (`MenuItem.data`).
- **Give to … and Drop …** on a sheet item (not one whose curse binds its bearer):
  `GET /characters/{id}/hand?index=…[&drop=1]` asks to whom (the party's other ready
  characters, `CharacterService.companions`) and how many of a stack, a dialog from the
  menu (`dialog_or_page`); `POST` hands it over (`CharacterService.hand`: the internal
  play actions `part` on the giver, `take` on the recipient). It arrives neither worn nor
  attuned, joins a stack of the same plain thing, and keeps its charges, scroll, secret
  and curse otherwise; the form names the item it was drawn for, so another in its place
  is refused. One log entry, the giver's: `give:<item>*<count>` with the recipient as its
  target, or `drop:<item>*<count>`; an unidentified item is logged as `?`.
