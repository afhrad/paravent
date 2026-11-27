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

Proposed 2026-11-27, after the trail:

1. **Move the party anywhere on a map (S–M).** The party's marker and the trail follow
   only journeys along a drawn road. *Party here* (a tap) would place it off-road, for
   wilderness or a ship at sea, and a move between two spots would add a straight leg.
2. **Find on the map (S).** A search field over the map's pins, names and roads that
   centres on the one chosen, for a large world map.
3. **The journeys in the session note (S).** *End the session* would list the legs of
   the trail walked since the session started, with where they went.
4. **Explored hexes (M).** On a map with a hex grid, mark hexes as explored (by hand, or
   along the trail) and show the players only those as clear. It touches "not a VTT", so
   it needs the user's word first.

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
