# To do

The working list: corrections found and not yet made, the next steps proposed, and the
questions still open. Nothing is lost between sessions: whatever is proposed or found goes
in here, and leaves when it is built (into PROGRESS.md) or dropped. The longer backlog of
ideas, from the Dungeon Master's Guides and from reviews, is [IDEAS.md](IDEAS.md).
Sizes are rough — **S** a day's change, **M** a few, **L** a feature of its own.

## Next, agreed or recommended

Nothing agreed is waiting; see the proposals below.

## Proposed, not yet chosen

- **Import a Game Master 5e backup** (sample analysed 2026-11-20). One plain XML file
  (`<data><version>33`): campaigns → adventures → encounters (combatants carry a whole
  statblock, or a `uid` pointing at a player character or NPC), notes (plain text with
  line breaks, a few lists and headings), NPCs and items with statblocks, pictures as
  Base64 JPEG (`imageData`, referenced by `uid`), player characters as finished numbers
  only, and the app's own compendium (monsters, spells, items). Map: campaign → world,
  adventure → adventure, notes → chapters, encounter → encounter with monsters matched by
  name in the world's books (labels as "Shown as"; home-made ones → creature designs or a
  list), NPCs → NPCs, items → treasure, pictures → the world's folder; skip the
  compendium (rulebook text, the corpus has it), character references and the saved
  fight state; characters cannot come over (no class or level choices). A report before
  writing. Open questions: a world per campaign or picked ones; a chapter per note or
  per adventure; home-made creatures as designs or a list. The repo carries only the
  reader and an invented test file. M

  The sample (9.3 MB, about 6 MB of it pictures), counted:

  | In the file | Count | In Paravent |
  | --- | --- | --- |
  | `campaign` | 7 | worlds (new, or into an existing one) |
  | `campaign/adventure` | 17 | adventures |
  | `note` (name, text, `expanded`) | 44 on campaigns and adventures, 22 on encounters | chapters; encounter notes |
  | `encounter` (`name`, `round`, `current`, `state`, `archived`) | 96 | encounters (the fight state dropped) |
  | `combatant/monster` with a statblock | 465 (447 also in the app's compendium) | monsters by name in the world's books; `label` → "Shown as" |
  | `combatant/monster` with only a `uid` | 492 to player characters, 5 to NPCs | skipped; links to the imported NPCs |
  | `npc` (statblock, `label`, `enemy`) | 15 | NPCs, statblock by name where the books have it |
  | `item` in adventures and encounters | 14 | treasure; home-made ones into the notes |
  | `imageData` (`uid`, `encoded` JPEG) | 12 pictures, referenced from campaigns and adventures | covers and pictures in the world's folder |
  | `pc` (numbers only: `ac`, `hpMax`, `abilities`, `skill`, …) | 31 | not importable; rebuilt in the wizard |
  | top-level `monster`, `spell`, `item` | 462, 381, 496 | not imported: rulebook text |

  A statblock's fields: `name`, `size` (number), `type` (with the book after a comma,
  "construct, monster manual"), `alignment`, `ac`, `armor`, `hd`, `hpMax`, `speed`,
  `abilities` ("STR,DEX,CON,INT,WIS,CHA"), `savingThrow` and `skill` (`id`/`ability`,
  `modifier`), `immune`, `resist`, `vulnerable`, `conditionImmune`, `senses`, `passive`,
  `languages`, `cr`, `trait`/`action`/`legendary`/`reaction` (`name`, `text`, `attack`),
  `spell`, `slots`.

- **Encounter notes at a glance** — the builder's notes now read folded under their
  editor ("How the notes read"), as on the treasure page; before, they stood under the
  title. Put back above the plan if the gamemaster wants tactics in view while planning.
  Open question to the user. S

## Known compromises

- **The Undo bar hides every other notice on its page** (`main:has(.undo-notice)`); a page
  reached after a delete shows only the delete's notice today, so nothing else is hidden.
  Revisit if a delete ever lands on a page with a notice of its own.
- **Restore works in any world** since the Undo (2026-11-20); Check links still lists only
  the table's world's trash.
- **A trash entry's files outlive it by nothing**: both go after 30 days. Earlier backups
  keep them.
- **Commit 4f9f851** (What's new) lacks `app/news.json`, which came with the rebuild after
  it; that one commit alone fails its test. Pushed history, left as is.
