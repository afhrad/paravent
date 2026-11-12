# Tokens

**Scope:** the round token every element of Paravent shows — a creature, an NPC, a player
character, an item, a place, an encounter, a treasure, any reference entry — so its kind is
seen at a glance at the table. A token is an **extension**: an element's picture (a
creature's, a place's, a cover) stays what it is and where it is.

**Decided 2026-11-12 by the user:** the table's own upload first, then a 5eTools token,
then Paravent's default disc for the kind; own tokens are uploaded as any picture and cropped
round; tokens show in the combat tracker and the players' fight view, the encounter builder,
the lists and cards, and on reference pages and their previews.

## 1. Where a token comes from

In this order, the first that exists:

1. **The table's own token** — for an NPC, a place or a creature, **made from its own
   picture** (one picture for both, the user's decision of 2026-11-12): the NPC's portrait,
   the place's picture, a builder creature's picture, or the picture the table gave a book
   creature in the tracker. Made again whenever the picture changes, placed where it was
   last set. A player character, an encounter or a treasure has no picture of its own and
   takes an uploaded token (§3).
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

## 3. The table's own tokens

Set on the element's page (the NPC's, place's, encounter's, treasure's, character's
sheet, a builder creature's), as any image the world's media accepts: dropped on the panel
or chosen. For an NPC, a place or a builder creature the picture chosen **becomes its
picture too** (portrait, place picture, creature picture), and **Adjust** reopens the
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

## 4. Code

`app/services/tokens.py` resolves a token (`Tokens.of_*`), crops uploads and finds files;
`app/ui/tokens.py` draws one (`token(page, tok, size)`): an `<img>` or the default disc,
with an optional badge.
