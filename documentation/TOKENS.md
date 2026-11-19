# Tokens

**Scope:** the round token every element of Paravent shows — a creature, an NPC, a player
character, an item, a place, an encounter, a treasure, any reference entry — so its kind is
seen at a glance at the table. A token is an **extension**: an element's picture (a
creature's, a place's, a cover) stays what it is and where it is.

**Decided 2026-11-18 by the user:** a token of the table's own is only for what stands on a
table — an NPC, a creature, a player character — and treasure. Places, encounters, quests
and factions take a **title image** instead (§6), as worlds, adventures and chapters have
their cover; in lists they show their kind's disc, or a small square of their title image.

**Decided 2026-11-12 by the user:** the table's own upload first, then a 5eTools token,
then Paravent's default disc for the kind; own tokens are uploaded as any picture and cropped
round; tokens show in the combat tracker and the players' fight view, the encounter builder,
the lists and cards, and on reference pages and their previews.

## 1. Where a token comes from

In this order, the first that exists:

1. **The table's own token** — for an NPC or a creature, **made from its own picture**
   (one picture for both, the user's decision of 2026-11-12): the NPC's portrait, a builder
   creature's picture, or the picture the table gave a book creature in the tracker. Made
   again whenever the picture changes, placed where it was last set. A player character or
   a treasure has no picture of its own and takes an uploaded token (§3).
2. **A 5eTools token** — for a creature, a vehicle or an object, from the 5eTools image
   folder (`PARAVENT_IMAGE_DIR`, read in place, never copied): `bestiary/tokens/<SOURCE>/
   <Name>.webp`, `vehicles/tokens/…`, `objects/tokens/…`, the name as 5eTools writes it
   (ASCII, without double quotes). A record's `token` (`{"name", "source"}`) points at
   another creature's token, as 5eTools does; `tokenUrl`/`tokenHref` are not followed. The
   files are round already (560×560, transparent corners); they are 5eTools art, so like the
   corpus they are the user's to bring, and Paravent ships none.
3. **The kind's empty frame** — the kind's ring (§1a) with the kind's icon in its opening,
   the opening filled in the kind's colour from the theme's tokens.

### 1a. Frames (2026-11-12, the user's request)

Every kind has an empty token frame, `static/tokens/frame-<kind>.png` (512 px): a bevelled
ring in the kind's colour, its centre see-through. They are drawn by
`python scripts/token_frames.py` from `FRAME_COLOURS` in `app/services/tokens.py`; the
ring's inner edge is `FRAME_INNER` (0.82) of the radius. An uploaded picture is placed in
its kind's frame: scaled so its shorter side fills the opening, times the zoom, the chosen
point at the centre, cut round to the opening, the ring on top — and saved so, one file.
5eTools tokens keep their own ring.

| Kind | Icon | Colours |
| --- | --- | --- |
| creature | skull | `--dmg` |
| npc | circle-user | `--accent` |
| character | shield | `--heal` |
| item | gem | `--magic` |
| place | map | `--cond` |
| encounter | swords | `--dmg` |
| treasure | coins | `--cond` |
| spell | wand-sparkles | `--magic` |
| vehicle | footprints | `--accent` |
| object | vault | `--accent` |
| anything else | book-open | neutral |

## 2. Where tokens show

- **Combat tracker** — each row of the order (a group's members keep their own row's
  token), and the players' fight view. A numbered label ("Goblin 2") keeps its number as a
  badge on the token.
- **Encounter builder** — each planned line.
- **Lists and cards** — NPCs, places, encounters, treasure, characters, the party cards,
  and the hover previews of campaign links.
- **Reference pages** — beside an entry's name, and in its hover preview.
- **The players' side and the record** (2026-11-12) — the Details card of a creature in
  the players' fight view (its token beside what is seen), the strip above the sheet
  (whose turn it is, the character's target), the recap's fighters, and the journal (the
  recipients when sharing, and on the gamemaster's list who each handout went to).
- **In place of the picture itself** (2026-11-12, the user's request): an NPC's portrait
  and a builder creature's picture are shown as their token wherever
  Paravent shows them — the head of its page (large), its card in a chapter, its edit
  form. The full picture is what the token is made from, and what players see of a
  creature in a fight's Details.

## 3. The table's own tokens

Set only where the element is edited (2026-11-19, the user's request: a page that reads
shows no dialog that changes it): an NPC's edit form (with *Clear the portrait*), a
treasure's Settings, a builder creature's builder, and the *Change the token* fold on
a character's sheet. Any image the world's media accepts: dropped on the panel or chosen. For
an NPC or a builder creature the picture chosen **becomes its picture too** (portrait,
creature picture), and **Adjust** reopens the
dialog with the picture it has, where it sits, to place it anew without uploading
(`GET /tokens/<world>/<kind>/<id>/picture` serves it to the dialog). Where it sits is kept
in `<id>.json` beside the token, with the picture it was made from. **The dialog** (`static/js/token_editor.js`) shows it under the kind's frame,
cut to the opening as it will be saved: drag to move it, the slider or mouse wheel to zoom
(1–4, the server takes up to 8). Save sends the picture with `x`, `y` (the point of the
picture at the centre, 0–1) and `zoom`; the server places it the same way (§1a). Without
the script the form still uploads, centred. Stored as WebP:
`media/tokens/<kind>/<id>.webp` in the element's world folder (a character or builder
creature of no world: the library's `media/tokens/`). Removing it returns to §1's next
source. Served at `/tokens/<kind>/<id>`, the address carrying the file's version.

## 4. Taking them to the table

**Tokens** on an encounter's builder (2026-11-12, the user's request) opens its tokens:
one piece per creature, numbered as the tracker numbers a group ("Goblin 2"), a group
with names of its own by those names, the world's characters first when "With the party"
is ticked. Each takes the squares of its size (Tiny and Small one, Large two, Huge three,
Gargantuan four; the larger of "Medium or Small").

- **Print** lays them out at their true size, the square chosen as 20, 25 (the one-inch
  battle-map grid) or 30 mm; the print styles leave only the tokens on the paper. The
  print dialog's scale must be 100 %.
- **Download for a virtual tabletop** sends a ZIP of PNGs, 140, 200, 256 or 280 pixels per
  square, each file named for its piece (`Goblin 2.png`).

Each piece is drawn by `app/services/token_export.py`: its token's file (the table's own,
or 5eTools's; `Tokens.path_of` finds it from the address), else its kind's empty frame
with the name's first letter in the opening, the number on a white badge at its edge.
Nothing is stored; pieces are drawn when asked for.

## 5. Code

`app/services/tokens.py` resolves a token (`Tokens.of_*`), crops uploads and finds files;
`app/ui/tokens.py` draws one (`token(page, tok, size)`): an `<img>` or the default disc,
with an optional badge.

## 6. Title images (2026-11-18)

Places, encounters, quests and factions are not set on a table, so they carry no token of
their own: they have a **title image**, drawn as worlds, adventures and chapters draw their
cover — at the head of a place's page and of an encounter's builder, as a banner at the top
of a quest's and a faction's panel — and as a small square beside them in lists and in a
chapter's place and encounter cards; without one their kind's disc stands there.

**The panel** (`app/ui/title_images.py`): the image dialog (ADVENTURES.md §5.5, *Frames
and the image dialog*, 2026-11-19) — a picture dropped, chosen or picked from the world's
pictures, framed in a shape (10:3 by default) and dressed with effects, the picture itself
never cut — and *Remove the title image* takes it away. On a
place's edit form, an encounter's Settings, and in a quest's and a faction's Edit
fold: only where the element is edited (2026-11-19), never on the page that reads it. `POST /gm/title-image/<kind>/<id>` (`app/services/title_images.py`) stores it: a
place's picture beside its scope's record as before (`Location.picture`), an encounter's,
quest's or faction's in its world's `media/` folder (`cover`); an encounter of no world
has none. The file is never deleted. Tokens once uploaded for a place or an encounter stay
on disk and are no longer shown.
