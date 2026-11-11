# The log (Protokoll)

**Scope:** one central record of what happened at the table — every roll and every notable
action, by players, the gamemaster and monsters — who may see each entry, and how it is
read on every screen, a player's phone above all.

**Date:** 2026-10-25 · **Status:** designed, decisions taken (§1); all five steps of §9
built 2026-10-25. The log is the only record of what happened: character and combat files
keep none of their own.

> **One sentence:** everything that happens is appended once to its world's log, stored as
> language-neutral facts with the dice, the advantage and every bonus's source; each screen
> shows a filtered, visibility-checked view of it, and a toast says what just happened.

It replaces two logs: the character's `log` (50 entries in the character file) and the
combat's `log` (COMBAT.md §6). Both become views of this one (§8).

---

## 1. Decisions (2026-10-25, the user's)

| Question | Decided |
| --- | --- |
| Storage | **Append only.** An entry is written once and never changed or deleted; a correction, a reveal or a clear is a new entry (§3) |
| Popups | **Toasts** with a dismiss button, gone by themselves after a while (§6) |
| Monster rolls | **Result only** for players: "hit, 9 slashing", never the dice, the bonus or the total to hit (§4) |
| Order | **Newest on top**, everywhere |
| Combat log | **A view of this log.** The fight keeps only what is still open (an attack waiting for Hit or Miss); everything resolved lives here, and the recap reads it (§8) |

## 2. The entry

Language-neutral facts, phrased by each screen in its reader's language — the rule the
character log already follows (`kind` + `subject`, never an English sentence).

| Field | Type | Example |
| --- | --- | --- |
| `id` | str | `"e7Qk…"`, unique, so a later entry can refer to it |
| `at` | datetime, UTC | shown in the viewer's local time |
| `actor` | username | `"tenewaja"` — who pressed the button |
| `subject` | ref | `"character:<id>"`, `"combatant:<combat id>/<id>"`, `"gm"` — on whose behalf |
| `target` | ref \| None | whom it hit, healed or affected |
| `kind` | enum | `check` `save` `skill` `initiative` `attack` `effect` (a save effect such as a breath weapon) `damage` `heal` `temp_hp` `cast` `concentration` `death_save` `hit_die` `rest` `condition` `turn` `round` `combat_start` `combat_end` `combat` (the fight's bookkeeping: added, hidden, split …) `custom`, and the bookkeeping kinds `reveal` `clear` `correct` |
| `what` | str | `"save:dex"`, `"skill:insight"`, `"attack:Longsword\|PHB"`, `"cast:Fireball\|PHB:3"` |
| `roll` | Roll \| None | below |
| `damage_rolls` | [Roll] | an attack's or effect's damage, one roll per part |
| `outcome` | enum | `""` `crit` `fumble` `success` `failure` `hit` `miss` |
| `against` | int \| None | the DC or AC it was rolled against |
| `amount` | int \| None | damage, healing, temporary HP |
| `types` | {str: int} | damage by type: `{"slashing": 5, "poison": 7}` |
| `visibility` | enum | `public` · `private` (the actor and the gamemaster) · `gm` (§4) |
| `scope` | Scope | below |
| `refers_to` | id \| None | for `reveal`, `clear` and `correct` |

**Roll** — the dice as data, not a string:

```json
{"expression": "1d20+6", "dice": [{"faces": 20, "results": [14, 7], "kept": [14]}],
 "natural": 14, "total": 20,
 "mode": "advantage", "reasons": ["sheet", "condition:invisible"],
 "bonuses": [{"source": "ability:dex", "value": 3},
             {"source": "proficiency", "value": 2},
             {"source": "item:Cloak of Protection|DMG", "value": 1}]}
```

- `mode` is the advantage that **applied**, `reasons` why: the sheet's switch, a condition
  (COMBAT.md §6, `condition_rolls.py`), an exhaustion penalty (a bonus with
  `source: "exhaustion"`). One advantage and one disadvantage cancel: `mode` is then
  `normal`, with both reasons kept, so the log can say why there was no second die.
- `bonuses` sum to `total − kept dice`. Sources are keys, not words, so they phrase in any
  language and link: `ability:<key>`, `proficiency`, `expertise`, `item:<ref>`,
  `feature:<ref>`, `feat:<ref>`, `spell:<ref>` (Bless), `condition:<key>`, `exhaustion`,
  `override:<key>`, `other`. This needs the sheet's `Derivation` to carry its parts
  as `(source, value)` pairs beside the readable text it has today — step 1's one
  change outside the log.

**Scope:** `world` (id or `None`), `adventure`, `chapter`, `combat`, `round`. A roll with
no world — a dice chip on a reference page opened without one — is **global**.

## 3. Storage: append only

- **One file per world**, `library/protocol/<world id>.jsonl`, and `library/protocol/global.jsonl`.
  JSON Lines: one entry per line, appended under the file's lock, flushed and fsynced. A
  roll adds about 400 bytes; nothing is ever rewritten.
  This is a deliberate exception to "one JSON object per collection" (TECHSTACK.md §3):
  rewriting a growing file on every roll is the cost that rule never had to pay.
- **A crash mid-append** leaves at most one torn last line. The reader skips and reports
  it; every complete line before it stands.
- **Nothing is changed in place:**
  - *Reveal* (a gamemaster's hidden roll shown to the table) appends `kind: reveal`,
    `refers_to` the roll; readers treat the roll as public from then on.
  - *Clear* (the sheet's "Clear" button today) appends `kind: clear` for a subject; that
    subject's views start after it. The file keeps everything, so the recap still can.
  - *Correct* (an entry made in error) appends `kind: correct`; the original shows struck
    through, not gone.
- **Reading** is from the end: the resident index holds each world's entries since start,
  loaded lazily, so a view asks for "the newest 50 visible to this user with this filter"
  and pages further back on scroll. The watcher reloads a file a hand edit changed.
- **Library data, backed up with the rest.** A deleted world's file stays; it is the
  history of play.

## 4. Visibility

Decided per entry when it is written, checked on every read — the server filters, so an
entry a player may not see never reaches their phone, not even hidden in the page.

| Who rolls | Default | Can be |
| --- | --- | --- |
| A player | `public` | `private` — a roll only they and the gamemaster see (Insight, a hidden Perception) |
| The gamemaster, a custom roll | `gm` | revealed |
| A monster | **result only** | revealed in full |

**Result only** is one entry with two faces. The gamemaster sees everything; a player sees
what happened, never how close it was:

| | Gamemaster | Player |
| --- | --- | --- |
| Monster attack | `Goblin 2 → Lyra · Scimitar 17 vs AC 15 · hit · 9 slashing` + dice | `Goblin 2 → Lyra · Scimitar · hit · 9 slashing` |
| Monster save | `Goblin 2 · DEX save 8 vs DC 15 · failure` | `Goblin 2 · DEX save · failure` |
| A player's save vs a DC | `Lyra · DEX save 20 vs DC 15 · success` | `Lyra · DEX save 20 · success` |
| A hidden monster | everything | nothing |

So the DC and AC are the gamemaster's, a player's own total is theirs, and a monster's
numbers stay behind the screen. A combatant hidden on the tracker (COMBAT.md §3) writes
`gm` entries until it is shown.

## 5. Where it is read

- **A log button in the top bar of every signed-in page** (the sheet's dice button,
  generalized), with a badge counting what arrived since the viewer last looked.
- **Desktop and iPad:** a panel on the right, over the page or pinned beside it (the
  tracker has room for it pinned).
- **Phone:** a full-height sheet sliding up from the bottom, closed by ✕ or a swipe down.
  The sheet's "Log" view and the tracker's log fold become this panel, pre-filtered.

**An entry, newest on top:**

```txt
┌──────────────────────────────────────┐
│ Log                [All|Me|Fight]  ✕ │
├──────── Round 3 · Goblin Ambush ─────┤
│ 20:14  Lyra                    🔒     │
│ ┌────┐ Dexterity save          ADV   │
│ │ 20 │ d20 14 (7) + DEX 3 + Prof 2   │
│ └────┘ + Cloak 1          Success    │
├──────────────────────────────────────┤
│ 20:13  Goblin 2 → Lyra               │
│ ┌────┐ Scimitar · hit                │
│ │  9 │ slashing                      │
│ └────┘                               │
├──────────────────────────────────────┤
│ 20:12  Brunhild               NAT 20 │
│ ┌────┐ Longsword                     │
│ │ 24 │ critical!                     │
│ └────┘                               │
└──────────────────────────────────────┘
```

- **The result in a box on the left**, large, so a column of numbers reads at a glance;
  green border on a natural 20, red on a natural 1 — the colours the sheet and the dice
  sounds already use.
- Line 1: time and who, each character in the colour of their token; `→` and the target.
- Line 2: what, and an ADV/DIS chip; the chip names its reasons on tap.
- Line 3: the breakdown in words — the dropped die in brackets, each bonus by its source
  (items, feats and spells link to their entries). A tap on the entry opens all of it.
- **Dividers** for rounds, fights and sessions (a gap of more than three hours starts a
  new session), so the log reads as a story of the evening.
- **Filters** as chips. Player: *All*, *Me*, *This fight*. Gamemaster: also per character,
  *Monsters*, *Hidden*.
- **Marks:** a lock on private entries, an eye-off on `gm` entries with a *Reveal* button
  for the gamemaster, a strike-through on corrected ones.
- Large tap targets (44 px), the numbers in the mono face, nothing that scrolls sideways.

## 6. Toasts

What just happened, without stopping play.

- A card above the bottom bar on a phone, bottom right on wider screens; the result large,
  what and who beside it, styled like its entry.
- **Gone by itself after about 6 seconds**, sooner by its **✕ button** (44 px, labelled).
  The timer pauses while the pointer is over it or focus is in it. A tap elsewhere on the
  toast opens the panel at that entry.
- At most three stacked, newest on top; a fourth pushes the oldest out.
- `aria-live="polite"`; no slide animation under `prefers-reduced-motion`.
- **Who gets one:**
  - a player — their own rolls and what hits, heals or conditions their character;
  - the gamemaster — every player roll, since the gamemaster answers them.
  - Everything else only raises the badge.
- A per-user setting in the user menu: toasts for *mine* / *all I may see* / *off*.
- The device that rolled hears the dice (`sound.js`) as today; a toast never plays a sound
  of its own.

## 7. Live

One server-sent stream per signed-in page, keyed by the world (and `global`) as the other
streams are: it carries only a revision, never an entry. On a change the page asks for
its toasts and its panel's newest entries, and the server filters both for that user — so
the stream itself can leak nothing.

## 8. What becomes of the old logs

- **The character's `log`** (CHARACTER-FORMAT.md) becomes the view "subject is this
  character". The field is read once on start into the world's log (marked `migrated`,
  scope from the character's world), then dropped from the file format with a version
  step. The party cards' "last roll" reads the log too.
- **The combat's `log`**: what is still open stays on the `Combat` record as state — an
  attack waiting for Hit or Miss, a save effect waiting for targets, the saves players
  rolled for it. The moment it resolves it is appended here with all its facts (target,
  damage by type as applied, the condition imposed), and the tracker's log fold shows the
  view "this fight". Old fights' logs migrate once, like the characters'.
- **The recap** (COMBAT.md §6) reads this log by fight, or by session and world.
- **Rolls on reference pages** (`roll.js`, today unlogged) are posted and logged —
  in the world the page is scoped to, otherwise global, `private` by default.

## 9. Build order

1. **The entry and the store.** Models, the append-only store with its reader and index,
   structured `Derivation` parts, and every writer (sheet actions, the tracker, the party
   cards) appending — beside the old logs, which still drive the screens.
   **Built 2026-10-25:** `app/models/protocol.py`, `app/store/protocol.py`,
   `app/services/protocol.py`; the sheet's `Derivation` and `Value` carry `sources`.
   The play rules and the tracker collect entries during a change and append them once
   it is stored. What the sheet logs: every roll (with its dice, advantage, reasons and
   sources), damage, healing and temporary HP, conditions, casts, rests, hit dice, death
   saves. What the tracker logs: every line of its own log, the d20 and damage rolls
   with them, and a monster attack's Hit or Miss as a second entry that `refers_to` the
   roll (whose id is fixed from the fight and the moment: `<combat id>-<time>`). When the
   tracker acts on a character, the sheet logs only what the tracker does not (a
   concentration save the damage called for, a player's own save), in the tracker's
   fight and round. A monster's bonus is `statblock`, a damage expression's number
   `modifier`; the example characters' every roll bonus is fully sourced (a test).
2. **The panel.** The top-bar button and badge on every page, the panel (side on wide
   screens, bottom sheet on a phone), dividers, filters, paging, live updates; and the
   reference pages' dice chips, which roll in the browser today, posted and logged
   (moved here from step 1: they need a route, and nothing could show them before the
   panel).
   **Built 2026-10-25:** `app/services/protocol_view.py` reads (who may see what, the
   filters, reveal/correct/clear applied), `app/ui/protocol_pages.py` phrases, and
   `app/routes/protocol.py` serves `/log`, `/log/panel`, `/log/badge`, `/log/events` and
   `POST /log/roll`; `static/js/protocol.js` opens the panel, pages, follows the stream
   and keeps the badge (what arrived since the panel was last opened, per device).
   **What reading already enforces**, since the panel would leak otherwise: a player
   reads their characters' worlds and the global log, public entries and private ones
   that are theirs, a monster's results without its dice, bonus or the roll before the
   verdict, never the fight's bookkeeping, never a DC or AC. The panel opens on the
   page's fight (tracker, players' fight view) or the sheet's character. On the sheet
   the panel replaces the phone's "Log" view as the way to the dice. Dice chips roll on
   the server, logged private in the page's `?world=` (one the reader plays in) or
   globally; chips in adventure chapters and other pages without `?world=` go global.
3. **Visibility.** Private rolls, the gamemaster's hidden rolls with Reveal, result-only
   monster rolls, hidden combatants.
   **Built 2026-10-25:** a "Private" switch beside the sheet's advantage buttons
   (remembered per character and device, like the advantage) makes the sheet's rolls —
   checks, saves, skills, attacks, damage, death saves, hit dice — `private`; damage
   taken and conditions stay public. The gamemaster's dice chips are `gm`, a player's
   `private`. In the panel the gamemaster sees, on every entry the players read less
   of (not public, a monster's numbers, a DC or AC), a mark and **Reveal**
   (`POST /log/reveal`), which appends a `reveal`; from then on the players read that
   entry in full, numbers and DC included. A combatant hidden on the tracker writes `gm`
   entries (step 1); unhiding it does not reveal what it did while hidden.
4. **Toasts**, with the per-user setting.
   **Built 2026-10-25:** `GET /log/toasts?since=` answers the cards for what arrived
   after the page was made (at most three), filtered by visibility and the reader's
   `User.toasts` — "mine" (a player: their own rolls and what befalls their characters;
   the gamemaster: every roll made for a character), "all" (everything the reader may
   read but turns, rounds and the fight's bookkeeping) or "off". The setting is three
   icons in the user menu (bell, people, bell crossed out), posted to `POST /log/toasts`.
   `protocol.js` asks on every append, shows nothing while the panel is open, and keeps
   each toast about six seconds (paused under the pointer or focus, Escape or ✕ to
   dismiss; a tap elsewhere opens the panel).
5. **The switch-over.** The sheet's log and the tracker's fold become the panel; the recap
   reads the log; the old logs migrate once and leave the character and combat formats.
   **First half built 2026-10-25:** the sheet's dice log and the tracker's log fold are
   slices of the log (`/log/panel?…&embed=1`: the newest 15, "The whole log" opens the
   panel), fetched with their fragment and on every append; the sheet's "Clear" appends a
   `clear`. The recap reads each fight's entries (`ProtocolReader.fight`), uncapped — an
   attack's damage is the verdict's, after resistances. On every start
   `copy_old_logs` (`app/services/protocol_migration.py`) appends, marked `migrated`, what
   the old logs hold from before the log began (older than its first entry for that
   character or fight), a resolved attack as roll and verdict.
   **Second half built 2026-10-25:** the old `log` fields left the formats — read from an
   older file (and copied in), never written; the character schema no longer has them.
   What they drove now has its own state or reads the log:
   - the fight's `pending` holds the attacks waiting for Hit or Miss and the save effects
     waiting for targets (an older record's open ones move there on reading);
   - a roll made on the sheet while the character fights belongs to that fight — its id,
     round and **world** (`fight_of`), so the fight's slice and recap hold it; a player's
     attack is that entry, its verdict refers to it, the tracker does not log it again;
   - an initiative rolled on a sheet reaches the tracker when the log hears of it
     (`origin: "sheet"`), not from the character file; a player's own save, the dice
     sounds and the party card's last roll read the log;
   - XP awards, levels taken and the wizard's rolled scores and hit points are entries
     of their own kinds (`experience`, `level_up`, `ability_scores`, `hit_points`), the
     award's reason as `note`. Weapon mastery notes are entries for the table, not
     bookkeeping.

Each step is usable alone; until step 5 nothing that works today changes.

## 10. Traps to keep in mind

- **Phrase at read time, never at write time.** Players at one table read different
  languages; an entry with an English sentence in it is a bug.
- **A bonus without a source is a bug**, as an unknown field is in library data. `other`
  exists for the honest unknown, not as a default.
- **Filtering is the server's job.** No entry outside the viewer's visibility is ever
  rendered into a page, a fragment or a toast and hidden by CSS.
- **Time is UTC in the file.** Session dividers and "20:14" are the viewer's local time.
- **Two worlds, two files.** A character moved to another world writes to its new world
  from then on; its earlier entries stay where they happened.
