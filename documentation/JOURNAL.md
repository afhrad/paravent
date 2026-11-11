# The players' journal

**Scope:** what the gamemaster hands to the players — a letter, a note, a read-aloud text,
a picture — and where the players read it.

**Date:** 2026-11-05 · **Status:** built (`app/services/handouts.py`, `app/routes/journal.py`,
`app/ui/journal_pages.py`). Replaces the "Player journal: later" of ADVENTURES.md §1.

> **One sentence:** a share button on every box and picture of a chapter hands a frozen
> copy of it to the characters the gamemaster ticks; their players read it in the journal.

---

## 1. Decisions (the user's, 2026-11-04)

| Question | Decided |
| --- | --- |
| Who receives | **Characters** of the world. A player reads what any of their characters received |
| After an edit of the chapter | **A frozen copy**: players keep what was handed over; later edits and new secrets never reach them. Sharing again hands over the new text |
| What has a share button | **Every box** of a chapter's text but `[!secret]` (read-aloud, handout, sidebar, variant, quote …) and **every picture**. Not chosen: NPC and place cards, free messages, whole chapters |
| How players learn of it | **A live toast** and the journal, where it stays marked *new* until opened |

A message from the gamemaster is a box: write it as `> [!handout] Title` in the chapter
and share it.

## 2. Sharing

On a chapter's page (not in the editor's preview) each box but a secret and each picture
carries a send button (`share` in `MarkdownContext`). It opens `/gm/share`: the box or
picture as the players will read it, a title (the box's own, else empty), and the world's
characters in play, all ticked — drafts in the wizard and retired characters are not
offered. **Share** makes the handout and opens it, saying to whom it went.

A box is named by **its own lines** (`callout_key`), not by its position, so the same box
reads the same in a chapter's own text and in a book section's woven notes, and a box
moved in the text keeps its button. `callouts(text)` finds them with the same parser the
page renders with.

**The copy** (`Handout` in `library/handouts.json`):

- a box keeps its kind and its Markdown as it read then; a `[!secret]` nested in it is
  taken out;
- every picture the box shows is copied to `library/handouts/<id>/` and named there by
  its file name; a picture keeps its effects (`map.png|torn`, ADVENTURES.md §5.5);
- the characters it went to, who shared it, and the usernames that opened it.

**A published adventure's own text** (2026-11-05): in a chapter based on a book, its
read-aloud boxes, sidebars, variants and quotes carry the button too, and so do its
pictures, now shown from the 5eTools image folder when it holds them (a placeholder
otherwise, without a button). A book box is named by its id, else by its content
(`book_key`), and found in the section **as it reads in the gamemaster's language**; the
handout keeps that entry as 5eTools JSON (`Handout.entry`) and the players read it through
the entries renderer, in the world's books. A book picture is copied from the image folder.

**The toast**: sharing writes one log entry per character (`EntryKind.HANDOUT`, private,
the character as subject, the title as note), which the log's own rules pop up on that
player's screen and file in their log (PROTOCOL.md §6).

## 3. Reading

`/journal` — in a player's bottom bar and top bar as **Journal**; for the gamemaster on
each world's page and under *Worlds* — lists what the reader may read, newest first. A
player's list marks what they have not opened; the gamemaster's names who each went to.

`/journal/<id>` shows the handout as its players read it:

- in its world's books, and only with links a player may open (PLAYER-VIEW.md);
- **campaign links as plain words** (`plain` in `MarkdownContext`): an NPC, a place or a
  chapter the players have no page for is its name, never a link to the gamemaster's
  pages nor the "not found" mark; a card alone on its line shows nothing;
- its pictures from its own folder, `/journal/<id>/img/<name>`, readable by those who may
  read the handout, rendered with their effects (`?fx=`).

Opening it marks it read for that player. The gamemaster also sees to whom it went and who
opened it, can return to the chapter, and **withdraw** it: gone from every journal, its
pictures with it.

## 4. Not built (yet)

- NPC and place cards, free messages and whole chapters (offered, not chosen).
- Changing a handout's recipients after sharing (withdraw and share again).
