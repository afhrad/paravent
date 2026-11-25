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

- **Per-world homebrew** — every world with Homebrew among its books sees every world's
  designs (creatures, items, spells, backgrounds), as one source `Homebrew` holds them all.
  Wanted by the user (2026-11-25), not yet built: it needs either a source per world with
  every `Name|Homebrew` reference in the library rewritten (encounters, NPC statblocks,
  treasure, characters' gear and spells, fights, shops, followers, chapter tags), or a
  world filter in resolution, search and the wizard, which today know only a world's
  books. A migration either way; plan it before building. M–L

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
