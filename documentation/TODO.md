# To do

The working list: corrections found and not yet made, the next steps proposed, and the
questions still open. Nothing is lost between sessions: whatever is proposed or found goes
in here, and leaves when it is built (into PROGRESS.md) or dropped. The longer backlog of
ideas, from the Dungeon Master's Guides and from reviews, is [IDEAS.md](IDEAS.md).
Sizes are rough — **S** a day's change, **M** a few, **L** a feature of its own.

## Next, agreed or recommended

Nothing agreed is waiting; see the proposals below.

## Proposed, not yet chosen

Paused 2026-11-22 while the user playtests; their findings come first.

- **Random checks in a dungeon** — an encounter table and an interval (every 10 minutes,
  every hour) while exploring; moving the clock rolls the checks, one that comes up becomes
  an encounter in one press, like the road's. S
- **Hazards by level** — falling, collapse, fire, wild magic: damage and DCs scaled to the
  party, sprung with "Spring it" as traps are (saves on the sheets, defences counted). S
- **Divine intervention** — a pantheon per world from the books' deities; the intervention
  roll on a cleric's sheet (2014 percentage, 2024 feature), logged. S
- **Running costs** — a business, stronghold or garrison charged by the clock, like
  lifestyle and wages, with what the purse lacks logged as owed. S
- **Proficiency dice** — the 2014 variant: a die instead of the flat proficiency bonus on
  every roll the sheet makes, as a world switch. S
- **Sentient items and artifacts** — a sentient item's mind, purpose and conflicts, an
  artifact's properties, rolled on the guide's tables. M
- **Random dungeon generator** (2014) — passages, doors and chambers rolled into a
  chapter's skeleton. L
- **Encounter notes at a glance** — the builder's notes now read folded under their
  editor ("How the notes read"), as on the treasure page; before, they stood under the
  title. Put back above the plan if the gamemaster wants tactics in view while planning.
  Open question to the user. S
- **Villagers from a Game Master 5e backup** — a campaign's townsfolk with a statblock come
  in as builder designs, like its monsters; they could be plain NPCs with a book statblock
  instead. Open question to the user. S
- **An unidentified +1 weapon** works as its base item, so its bonus does not count until
  identified. The rules count it from the start; showing it would hint at what it is.
  Open question to the user. S

## Known compromises

- **The Undo bar hides every other notice on its page** (`main:has(.undo-notice)`). Since
  a delete from a context menu returns to the page it was asked from (2026-11-21), that
  page's own notice, if it shows one, waits until the next load.
  Revisit if a delete ever lands on a page with a notice of its own.
- **Restore works in any world** since the Undo (2026-11-20); Check links still lists only
  the table's world's trash.
- **A trash entry's files outlive it by nothing**: both go after 30 days. Earlier backups
  keep them.
- **Commit 4f9f851** (What's new) lacks `app/news.json`, which came with the rebuild after
  it; that one commit alone fails its test. Pushed history, left as is.
