# Paravent

## Overview

Paravent is a Dungeon & Dragon 5e tool to be used in a browser by players and dungeon masters. The dungeon master creates adventures in
a world. Players uses the same software as a PWA on their phones to control their characters

## The data foundation

Paravent works against **two kinds of data, kept strictly apart**:

- **Reference** — the full 5eTools corpus, supplied by the user, read-only. This is what gives
  the tool its content *and its cross-linking*: 5eTools already encodes every reference between
  entries as a `{@...}` tag, so a complete corpus means every link resolves. Paravent reads it in
  place; it is never copied into the project and never written to.
- **Library** — everything created *with* Paravent: worlds, adventures, encounters, player
  characters, combat state, homebrew, media. This is the user's work and the thing worth backing
  up.

The separation is not filing tidiness. Reference data is a replaceable snapshot; library data is
irreplaceable. Keeping them in separate roots makes re-importing the corpus a safe operation.

**Each world declares which books are in play.** Most references in the data name no source —
`{@spell fireball}`, not `{@spell fireball|PHB}` — and two thirds of spell names exist in more
than one book. Declaring the table's books (PHB + MM + the adventure, say) makes every reference
resolve unambiguously, and makes a reference to a book nobody owns stay visibly inert instead of
silently pulling in content from it. Declaring nothing means "use everything imported", so the
tool works before it is configured.

See TECHSTACK.md §3 for the layout, §6b for what is loaded in what order, and §6c for how
references resolve.

## Features

- Multilingual with english as fallback language. German as second language. Translations are a
  **text-only overlay** on the English data, so cross-references keep working at any coverage
  level and a half-translated corpus is a normal state (see TRANSLATION.md)
- **Unit display (feet or metres) is a per-user setting**, set with the language: English
  reads feet, other languages metres
- **Complete reference database** — monsters, spells, items, classes, races, backgrounds, feats,
  treasures, NPCs and rulebooks, loaded from the full corpus rather than a subset
- **Browse and search across everything at once** — one search field over the whole reference
  set, no category chosen first, scoped to the books this world uses
- **"What references this?"** — reverse lookup from any entry to everything that mentions it
- **Tooltips on every reference** — hovering or tapping any `{@...}` tag previews the entry
  without leaving the page you are reading
- Rendering engine of special marked items
- Use markdown to write adventures
- Upload images for adventures, encounters
- Generate custom monsters
- Generate NPCs
- Character generation from the reference data (see CHARGEN.md)
- Dice rolling features as AD&D describes (advantage/disadvantage, etc)
- Combat tracker
- Encounter builder with difficulty calculation
- Popup window library where appropriate with searchable content (monster, spell, item, etc)
- Multiuser mode. Login as (role: gamemaster/player) and preserve state
