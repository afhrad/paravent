# To do

The working list: corrections found and not yet made, the next steps proposed, and the
questions still open. Nothing is lost between sessions: whatever is proposed or found goes
in here, and leaves when it is built (into PROGRESS.md) or dropped. The longer backlog of
ideas, from the Dungeon Master's Guides and from reviews, is [IDEAS.md](IDEAS.md).
Sizes are rough — **S** a day's change, **M** a few, **L** a feature of its own.

## Next, agreed or recommended

- **Maps** (agreed 2026-11-27, spec [MAPS.md](MAPS.md); gamemaster only, world and
  adventure maps, an adventure's sticking to it; steps 1–3 (the maps, pins and maps
  within maps, layers and names) built 2026-11-27). Each step usable without the next:
  4. **Roads and travel (M):** roads with terrain stretches, their lengths, *Travel*
     filling the travel dialog, the party's position on the map.

## Proposed, not yet chosen

Paused 2026-11-22 while the user playtests; their findings come first.

Nothing proposed is waiting.

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
