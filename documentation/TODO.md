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

Proposed 2026-11-27, after pins to every kind:

1. **A toast to the players when a map is shown to them (S)** (MAPS.md §12): "The
   gamemaster shows you *Azeroth*", opening `/maps/<id>`, like a shared handout.
2. **The party's trail (M)** (MAPS.md §12): each move of the party's position kept with
   its date, drawn as a dotted line on the gamemaster's map and, if wanted, the players'.
3. **A monthly budget for the Claude API (S)**: `PARAVENT_TRANSLATE_BUDGET` in dollars;
   the translations page warns as a month nears it and a run stops once it is spent.

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
