# Maps

**Scope:** the gamemaster's own maps of a world and its adventures: uploading them, zooming
and dragging through them however large they are, the scale every map carries, measuring
on them, the pins that tie a map to the world's places, people and stories, the layers of
names and notes laid over the picture, and the roads whose length becomes travel time.

**Date:** 2026-11-27 · **Status:** step 1, the maps themselves, built 2026-11-27; steps 2–4 specified. Steps in
§10.

> **One sentence:** a map is a picture with a scale; everything on it — pins, names, notes,
> roads, the ruler — is stored in the picture's own pixels, so it holds at every zoom and survives
> re-tiling.

---

## 1. Decisions

| Question | Decided (2026-11-27, the user) |
| --- | --- |
| Who sees maps | **The gamemaster only, for now.** Routes refuse players; no player view, no reveal. A later player view (§11) is not ruled out |
| What maps belong to | **A world or an adventure** (a `Scope` without a chapter). A world's maps are its atlas; an adventure's are its dungeon levels, towns and wilderness |
| An adventure's maps | **Stick to the adventure, never to the world** (the user, 2026-11-27): listed with it, copied, exported, moved and deleted with it, never moved up to the world (§3) |
| Layers | **Yes** (the user, 2026-11-27): everything laid over the picture sits on a layer — places, town names, notes, roads — shown or hidden a layer at a time (§7) |
| Large maps | **Sliced into tiles and loaded as viewed**, as online maps do (§4) |
| Scale | **Required for measuring.** A map without one can be viewed and pinned; the ruler and roads ask for the scale first (§5) |
| Measuring | A **ruler** that is not kept, and **roads** that are, with terrain, whose length feeds the existing travel (§8) |
| Battle maps, grids, tokens, fog of war | **No.** Paravent is not a VTT (the user, 2026-11-12). A map here is for working out the world |

---

## 2. The record

`library/maps.json`, the model `WorldMap` (`Map` would shadow the builtin), one entry per map:

| Field | Holds |
| --- | --- |
| `id`, `name` | as every record; the name is the gamemaster's |
| `scope` | `Scope` with `world_id` and optionally `adventure_id`; never `chapter_id` |
| `image` | the original's path, relative to the scope's folder: `media/maps/<id>.<ext>` |
| `width`, `height` | the original's size in pixels, read at upload |
| `digest` | the original's content hash; names its tiles (§4) |
| `scale` | `MapScale` or none (§5) |
| `parent` | the id of the map this one lies on, or none, and `frame`: its rectangle there (§6) |
| `layers` | `MapLayer` list, in drawing order (§7) |
| `pins` | `MapPin` list (§6) |
| `labels` | `MapLabel` list: names written on the map (§7) |
| `routes` | `MapRoute` list (§8) |
| `notes` | Paravent Markdown, as a place's |

Layers, pins, labels and routes live inside their map, not in collections of their own: they mean nothing
without it, they are copied, moved and deleted with it, and a map's file entry stays small.

**The original is kept as uploaded** (JPEG, PNG or WebP, never re-encoded) in the scope's
`media/maps/`, so it is in the world's folder, its backups, its export and its copy like
every other picture (ADVENTURES.md §3); an adventure's map in the adventure's folder.
Replacing the image keeps pins, labels and routes where they are in pixels; a picture of a
different size asks first whether to stretch them onto it.

**Coordinates** are pixels of the original, `x` right and `y` down, as floats. Nothing stored
depends on the tiles, the zoom or the screen.

---

## 3. Where maps are

- **The current world's navigation** gets *Maps* (CURRENT-WORLD.md): the world's own
  maps, as cards with a thumbnail, a scale badge or "no scale".
- **An adventure's page** lists the adventure's maps beside its places; they are not in
  the world's list.
- **A place's page** shows *On the map*: every pin naming it, linked to its map centred
  there (§6).
- **⌘K** does not list maps, as it lists no NPCs or places either outside the editors'
  insert pop-up; a map is reached from *Maps* or its adventure.
- Context menus as everywhere (CONTEXT-MENUS.md): open, rename, replace the image, delete.
- **An adventure's maps stick to it.** They are never moved up to the world: deleting an
  adventure deletes its maps with it, whatever is chosen for its NPCs and places (all into
  the trash together, restored together); copying, exporting or moving the adventure to
  another world takes them along. A world's map stays the world's. Where a map belongs is
  chosen when it is made and not changed after.
- **Deleting** goes to the trash for 30 days, as every object (`app/services/trash.py`).
  **World copy and move** carry maps like places; a map's `parent` and its pins to objects
  that did not travel stay, shown as gone (§6).

---

## 4. Viewing: tiles for large maps

**Leaflet 1.9**, vendored in `static/js/leaflet/` with its licence (BSD-2), no CDN, in its
flat-picture mode (`L.CRS.Simple`) rather than earth geography. It brings dragging, wheel and
pinch zoom, markers, lines and tile loading, and works on the iPad. `static/js/map.js` holds
Paravent's part: the ruler, editing pins and routes, saving the view.

**Two ways to show a picture, one viewer:**

- **Up to 4,096 px on the long side** the original is shown as one picture
  (`L.imageOverlay`). No tiles, nothing to wait for.
- **Larger** maps are cut into a pyramid of 256 px WebP tiles: the deepest level at the
  original's resolution, each level above half of it, down to the level where the whole map
  fits one tile. Leaflet loads only the tiles in view (`/gm/maps/<id>/tiles/{z}/{x}/{y}.webp`).
  Two levels of zoom past the original are allowed, scaled up, for placing pins precisely.

**Tiling runs once, in the background**, after an upload or a replaced image: a job like the
corpus import (`MapTiles`, one map at a time, off the event loop), its progress on the map's
page, which opens as soon as tiles exist. Pillow cuts the levels by halving the picture each
time, so the peak is about one and a third the decoded original.

**Memory is the limit, not the file size.** A decoded picture takes three to four bytes per
pixel: 200 MP is 600–800 MB. Pillow refuses large pictures by default as a safeguard; the
limit becomes `PARAVENT_MAP_MAX_PIXELS` (default 250 MP, about 16,000 × 16,000), and an upload
past it is refused with its size and the limit. The file itself may be up to 200 MB, a world
package's largest file, so every map a table holds can be exported; the upload is streamed
to disk, never held in memory. If real maps need more, libvips (`pyvips`,
which tiles without holding the whole picture) is the way, as an optional dependency; not
before it is needed.

**Tiles are disposable.** They are made from the original, so they live outside the library
and outside the backups: `PARAVENT_MAP_CACHE_DIR` (default `map-cache/` beside the library
folder, a write root the reference guard covers), `<map id>/<digest>/{z}/{x}/{y}.webp`, with
`thumb.webp`, the whole map in 256 px, for the lists. A tile's address carries the digest, so
browsers keep tiles for good. A new digest is a new folder and the old one goes; a
missing folder (a restored backup, a new server) is tiled again on first view. Thousands of
small files would otherwise swell every backup.

**The view is remembered per user and map**: centre and zoom, in the user's `Place` record
(`library/places.json`, which already keeps where a user left off), saved a moment after
the map stops moving. Opening a map from a pin or a place centres there instead.

---

## 5. The scale

`MapScale` stores **feet per pixel of the original** (`feet_per_px`), the one number every
measurement needs, and how it was given, so the form shows it again:

| Way | The gamemaster | Typical |
| --- | --- | --- |
| **A line** | draws a line on the map and types its length: across the scale bar, or across one hex (flat side to flat side) | most maps |
| **The width** | types how far the map is across | a map without a scale bar |

The length is typed in miles, km, feet or m and kept as given (`amount`, `unit`, and the
line's two points). Converted by Paravent's own factors (TRANSLATION.md §6): 1 mile = 1.5 km,
1 ft = 0.3 m, 1 mile = 5,280 ft, so "100 km" on a map is 66⅔ miles, as the German books count.

**Shown in the reader's units** (feet or metres by the language, as everywhere): a map
whose scale was given in miles or km measures in miles or km, one given in feet or m in feet
or m. A ruler on a world map says "212 miles" or "318 km", one in a town "450 ft" or "135 m".

**One scale per map** ignores the bending of a projected earth. Right for drawn fantasy
maps; a Mercator-style map measures wrong towards its edges, which the scale form says.

---

## 6. Pins and maps within maps

`MapPin`: `id`, `layer`, `x`, `y`, `target` and an optional `label` (shown instead of the
target's name). The target is one of:

| Kind | Points at |
| --- | --- |
| `place` | a place (`Location`) |
| `npc`, `encounter`, `treasure`, `shop` | those objects |
| `adventure`, `chapter` | where a story happens |
| `map` | another map: the city on the world map opens the city's map |
| `note` | nothing: the label and a line of Markdown, for what deserves no record |

**Pins point by id**, not by name: a rename changes nothing, and the pin shows the new name.
A pin whose target is gone (deleted, in the trash, left behind by a world copy) stays grey,
names what it pointed at, and comes back to life when the target is restored.

**Drawn as the element's round token** (TOKENS.md): a place's picture, an NPC's portrait, the
kind's disc otherwise; small at a distance, the name beside it when zoomed in. A tap opens a
card: the name, a line of the target's notes, *Open*, and for a `map` pin *Go to map*.

**Placing a pin**: *Pin* in the map's toolbar, then a tap on the map, then the target from a
search over the scope's objects (the world's and, on an adventure's map, the adventure's
too), onto the layer chosen in the toolbar,
or *New place here*, which creates the place and pins it in one go. Dragging moves a pin;
its card removes it.

**A map within a map.** A map may lie on another: `parent` and `frame`, a rectangle in the
parent's pixels, drawn on the parent as an outline with the map's name, opening it. The map
shows its parents as a path above it ("Faerûn › Sword Coast › Waterdeep"). A `map` pin and a
frame are two ways to the same thing; a frame says how far the map reaches. When both maps
have a scale and the frame is set, the form says if they disagree by more than a tenth
("this frame makes the city 4 miles across, its own scale 12"), and corrects neither.
An adventure's map may lie on a world map: the world map then shows its frame with the
adventure's name, and loses it when the adventure is deleted. A world map never lies on an
adventure's.

---

## 7. Layers, names and notes

**A layer** (`MapLayer`): `id`, `name`, `visible` (the default for a newcomer), `locked`
and `from_zoom` (shown only from that zoom in, so a world map's village names do not crowd
it at a distance; none = always). Every pin, label and route sits on exactly one layer;
the order of `layers` is the drawing order, the last on top.

**A new map starts with four**, named in the gamemaster's language and theirs to rename,
reorder or delete: *Places* (pins to places and maps), *Names* (labels), *Roads* (routes),
*Notes* (note pins and the gamemaster's reminders). Pins to NPCs, encounters and treasure go
on *Places* unless another layer is chosen. Deleting a layer that holds something asks
whether to move its contents to another layer or delete them with it.

**The layer panel** (a folding panel beside the map, a sheet on the phone): each layer with
an eye to show or hide it, a lock against moving things by accident, its count, and drag to
reorder. **Shown and hidden is remembered per user** with the view (§4), so hiding the notes
on the iPad does not hide them on the laptop; the layer's `visible` is the start for a user
who has not chosen. The toolbar's tools (pin, name, road) add to the *current* layer, chosen
in the panel; a locked layer cannot be the current one.

**Names written on the map** (`MapLabel`): `id`, `layer`, `x`, `y`, `text`, `style` and
`size`, and `angle` in degrees for a river or a mountain range written along it. Styles are
a short fixed list read from the theme (THEMES.md), so labels match Paravent and not a font
picker: *settlement* (upright), *region* (spaced capitals), *water* (italic), *feature*
(small). **The size is in the picture's pixels**, so a name grows and shrinks with the map
as if printed on it; below a few screen pixels it is not drawn. A label is plain text, not
a link: what a place is, its pin says. *Name* in the toolbar, a tap, the text; dragging
moves it, a handle turns it, its card changes or removes it.

**Notes** are `note` pins (§6) on the *Notes* layer, or on any: a label and Markdown, shown
in the card. They are what the gamemaster wants to remember at that spot ("the bridge is
out", "the bandits' lookout") without making a record of it.

---

## 8. Measuring and roads

**The ruler** (the map's toolbar): tap, tap, tap; each stretch and the total shown beside the
line, with the time on foot at the three paces (2, 3, 4 mph; 8 travelling hours to the day),
"212 miles · normal pace 9 days". Double tap or *Done* ends it; nothing is kept. Without a
scale the ruler asks for one first.

**Roads are kept.** `MapRoute`: `id`, `layer` (*Roads* unless chosen), `name`, `kind` (road, trail, river, sea route) and
`stretches`, each a line of points with its **terrain** (the rows of the 2024 *Travel
Terrain* table that `app/services/travel.py` already reads: Arctic, Coastal, Desert, Forest,
Grassland, Hill, Mountain, Swamp, Underdark, Urban, Waterborne) and whether it is difficult.
Consecutive stretches share their end points, so a road from the city through the forest
into the hills is three stretches. A road or trail is a path: nobody has to navigate on it,
as travel already treats a path; across country (a route of no kind) the navigator rolls.

**Drawn as lines** coloured by kind, the name along them when zoomed in. Editing: *Road*
in the toolbar, tap the points, choose a terrain; a point dragged moves; *Split here* starts a
new stretch at a point; a pin can be snapped to as a start or an end.

**Lengths are computed, never typed:** a stretch's length is its points' distances times the
scale, shown per stretch and in total, in the reader's units. Changing the scale changes them.

**Travel along a road.** A road's card offers *Travel*: from one of its points or pins to
another, in either direction. It opens the existing travel dialog (`TravelService.travel`)
with the stretches filled in, so the party walks them in order, each at its terrain's pace
(the 2024 *Travel Terrain* table's fastest pace, difficult terrain halving, as travel reads
now), and the clock, rations, weather, survival checks and encounters run as they do today.
For this, `Plan` gains an optional route: the list of `(miles, terrain, difficult)` the party
walks, from which travel computes the hours and changes terrain at each border, instead of
one terrain for the whole stretch.

**Where the party is.** A stretch of travel that stops partway (an encounter, the day's end)
records where on the route: `Journey` gains `map_id`, `route_id` and `at` (miles from the
start). The map shows the party there as a marker, and *Travel on* starts from it. Only one
party position per world, the clock's; nothing else on the map moves by itself.

---

## 9. What stays out

- **No grid, no battle map, no tokens of combatants, no fog of war** (§1).
- **No drawing tools** beyond pins, names and roads: regions, borders and coastlines belong
  in the picture, made in a drawing program.
- **No player view yet** (§11).
- **No projection maths** (§5).
- **No map from the corpus**: the books' maps stay where they are (ADVENTURES.md, *Maps*);
  a gamemaster who wants to pin one uploads a copy of it as their own.

The maps a gamemaster uploads are theirs to use, as the corpus is (TECHSTACK.md §8);
Paravent ships none.

---

## 10. Build order

1. ~~**Maps (L).**~~ Built 2026-11-27. The record, upload into the scope's `media/maps/`, tiling in the background
   with the pixel limit, the viewer (one picture or tiles), the scale form with both ways,
   the ruler with paces, the remembered view, *Maps* in the navigation and on the adventure
   page, context menus, trash, world copy and move.
2. **Pins (M).** Every target kind, tokens, the card, *New place here*, grey pins of gone
   targets, *On the map* on a place's page, maps within maps with frames and the path.
3. **Layers and names (M).** Layers with the panel, visibility per user, locks and
   `from_zoom`; labels with styles, size and angle; notes on their layer. Pins made in
   step 2 move onto *Places* and *Notes*.
4. **Roads and travel (M).** Routes with stretches and terrain, their lengths, *Travel*
   filling the travel dialog, `Plan` walking a route, the party's position.

Each step is usable without the next.

---

## 11. Later, not decided

- **A player view**: maps and pins the gamemaster reveals, per world, as creature knowledge
  is revealed — a layer is the natural thing to reveal ("the players see *Names* and
  *Roads*, never *Notes*"); perhaps a "travelled" trail of where the party went.
- **Claude placing pins** on the maps of its draft adventures (MCP.md), by place name.
- **Exporting a map with its pins** as a picture for a handout (JOURNAL.md).
- **A hex grid drawn over a map** from its scale, for hex crawls.
