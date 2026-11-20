# To do

The working list: corrections found and not yet made, the next steps proposed, and the
questions still open. Nothing is lost between sessions: whatever is proposed or found goes
in here, and leaves when it is built (into PROGRESS.md) or dropped. The longer backlog of
ideas, from the Dungeon Master's Guides and from reviews, is [IDEAS.md](IDEAS.md).
Sizes are rough — **S** a day's change, **M** a few, **L** a feature of its own.

## Not yet committed

- **One banner after a delete** (2026-11-20): the Undo bar says "Deleted." itself and the
  page's own "deleted" notice is hidden while it shows (`trash.undo_hint`, one CSS rule
  `main:has(.undo-notice)`). Built and checked; waiting for "commit and push".

## Next, agreed or recommended

- **The creature builder** (audit 2026-11-20). Still the narrow page width, so its long
  form is a slim column; action texts are edited in a box with tiny type. To check: the
  statblock preview looked cut off after "Armor Class", and the floating Save/Publish bar
  over the Armor Class labels (perhaps only the full-page screenshot). M
- **Quests** (audit 2026-11-20). *New quest* is a big form always open at the foot of the
  list, where other lists have a **New …** dialog; its labels are a large serif face, not
  the small bold labels of other forms. S

## Proposed, not yet chosen

- **Setting names in the repository** (check 2026-11-20). The repo should hold SRD material
  only. About 30 files still name published settings (factions, calendars, places,
  characters). Most are tests that check names in the user's own corpus, or code reading a
  setting's calendar from it: references to user data, which may stay. Each is to be
  judged; example text and invented fixtures get names of our own. Largest:
  `tests/test_glossary.py`, `tests/test_reference.py`, `tests/test_factions.py` (its
  corpus checks of the guide's example factions), `tests/test_published.py`,
  `tests/test_calendar.py`, `app/services/calendar.py`, `documentation/TECHSTACK.md`. M
- **Restore deleted worlds** — a world's folder goes to `library/.trash/` (erased after 30
  days) but nothing lists it; a short "Deleted worlds" list in the world menu, each with
  Restore. S
- **Quests and factions into the trash** — deleting either is still final; a quest's steps
  and rewards take a while to type again. S
- **Try the trash on a copy of the real library** — delete a chapter and an adventure with
  their contents, restore them, compare the files and front matter; the file moves are the
  riskiest part of the trash work. A check, not a change. S
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
