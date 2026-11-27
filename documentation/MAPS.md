# Maps

**Scope:** the gamemaster's own maps of a world and its adventures: uploading them, zooming
and dragging through them however large they are, the scale every map carries, measuring
on them, the pins that tie a map to the world's places, people and stories, the layers of
names and notes laid over the picture, and the roads whose length becomes travel time.

**Date:** 2026-11-27 · **Status:** built, all four steps, 2026-11-27 (the maps; pins and
maps within maps; layers and names; roads and travel); the four later ideas built the same
day (the hex grid §8b, the players' view §9, a map as a picture §9b, Claude's pins §9c).

> **One sentence:** a map is a picture with a scale; everything on it — pins, names, notes,
> roads, the ruler — is stored in the picture's own pixels, so it holds at every zoom and survives
> re-tiling.

---

## 1. Decisions

| Question | Decided (2026-11-27, the user) |
| --- | --- |
| Who sees maps | **The gamemaster, and the players what is shown to them** (2026-11-27, the user asked for the later ideas built): a map the gamemaster shows the players, the layers marked for them (§9) |
| What maps belong to | **A world or an adventure** (a `Scope` without a chapter). A world's maps are its atlas; an adventure's are its dungeon levels, towns and wilderness |
| An adventure's maps | **Stick to the adventure, never to the world** (the user, 2026-11-27): listed with it, copied, exported, moved and deleted with it, never moved up to the world (§3) |
| Layers | **Yes** (the user, 2026-11-27): everything laid over the picture sits on a layer — places, town names, notes, roads — shown or hidden a layer at a time (§7) |
| Large maps | **Sliced into tiles and loaded as viewed**, as online maps do (§4) |
| Scale | **Required for measuring.** A map without one can be viewed and pinned; the ruler and roads ask for the scale first (§5) |
| Measuring | A **ruler** that is not kept, and **roads** that are, with terrain, whose length feeds the existing travel (§8) |
| Battle maps, tokens, fog of war | **No.** Paravent is not a VTT (the user, 2026-11-12). A map here is for working out the world; a hex grid for a hex crawl is (§8b) |

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
| `grid` | `MapGrid` or none: the hex grid (§8b) |
| `players` | whether the players of its world see it (§9) |
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
Replacing the image with a picture of another size takes it for the same map at another
resolution (decided while building, 2026-11-27): the scale, the pins (and later labels and
routes) and the frames of the maps lying on it are stretched onto it, without asking.

**Coordinates** are pixels of the original, `x` right and `y` down, as floats. Nothing stored
depends on the tiles, the zoom or the screen.

---

## 3. Where maps are

- **The current world's navigation** gets *Maps* (CURRENT-WORLD.md): the world's own
  maps, as cards with a thumbnail, a scale badge or "no scale".
- **An adventure's page** lists the adventure's maps beside its places; they are not in
  the world's list.
- **Every page a pin can point at** shows *On the map* (2026-11-27, `app/ui/map_links.py`):
  a place's, an NPC's, an encounter's, a treasure's, a character's (the gamemaster's
  sheet) and a handout's, and a line under each quest and faction. Every pin naming it,
  linked to its map centred there (§6), then *Pin on a map* with each map of the world that
  may take it (`MapService.pinnable`: the world's own first). That opens the map with
  `?pin_for=kind:id`: the pin tool on, the status saying "Tap where Old Wen goes", and after
  the tap the dialog with it chosen and alone in the list, so *Pin it* is one press. A
  `pin_for` the map may not show is ignored.
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
limit becomes `PARAVENT_MAP_MAX_PIXELS` (default 250 MP, about 16,000 × 16,000; the
shipped `docker-compose.yaml` sets 700 MP, about 26,000 × 26,000, 2–3 GB to cut), and an upload
past it is refused with its size and the limit. The file itself may be up to 200 MB, a world
package's largest file, so every map a table holds can be exported; the upload is streamed
to disk, never held in memory. If real maps need more, libvips (`pyvips`,
which tiles without holding the whole picture) is the way, as an optional dependency; not
before it is needed.

**Tiles are disposable.** They are made from the original, so they live outside the library
and outside the backups: `PARAVENT_MAP_CACHE_DIR` (default `map-cache/` beside the library
folder, a write root the reference guard covers; under Docker the named volume
`paravent-maps` at `/data/map-cache`, so a rebuilt container keeps it), `<map id>/<digest>/{z}/{x}/{y}.webp`, with
`thumb.webp`, the whole map in 256 px, for the lists. A tile's address carries the digest, so
browsers keep tiles for good. A new digest is a new folder and the old one goes. Thousands
of small files would otherwise swell every backup.

**A cache gone** (a restored backup, a new server, the folder deleted) is cut again by
itself (2026-11-27): opening the map starts it, and so does a list asking for its
thumbnail or the viewer asking for a tile, so the *Maps* lists fill in as they are shown.
A cutting that failed is tried again only by opening the map, which says why it failed,
never by every list view. **All at once:** `python -m app.cli maps tiles` cuts every large
map without tiles, one after another, with its progress (`--dry-run` names them,
`--force` cuts every one again); under Docker
`docker compose exec paravent python -m app.cli maps tiles`. It may run beside the
server: each cutting writes a part of its own and puts it in place whole.

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

`MapPin`: `id`, `x`, `y`, `kind`, `target` (an id), `name` (the target's when pinned), an
optional `label` (shown instead of the target's name; a note's title) and a note's `note`;
`layer` comes with step 3. The target is one of:

| Kind | Points at |
| --- | --- |
| `place` | a place (`Location`) |
| `npc`, `encounter`, `treasure` | those objects |
| `adventure`, `chapter` | where a story happens |
| `map` | another map: the city on the world map opens the city's map |
| `character` | a player character (they play in the table's world, so on any map) |
| `quest`, `faction` | the world's quests and factions; the pin opens their list there |
| `handout`, `session` | a handout given out, a session note: where it was found, what happened here |
| `reference` | an entry of the world's books or the table's homebrew, `category\|name\|source`: a creature's lair, where an item lies, a deity's temple |
| `note` | nothing: the label and a line of Markdown, for what deserves no record |

**Every kind of object** (2026-11-27, at the user's word): characters, quests, factions,
handouts, session notes and entries of the books joined the first seven. The dialog lists
the world's own by kind, filtered as before, and **searches the world's books** below them
(*Or from the books*, the ⌘K search over the declared books, homebrew included, twenty
at most). An entry's pin shows the entry's name and opens its reference page; it is grey
once the corpus has it no more. Sounds, combats and the shop are not pinned: they have no
place in the world.

**No shop pins** (decided while building): a world has one shop, opened from the clock, with
no place of its own; a place's notes say where it stands.

**Pins point by id**, not by name: a rename changes nothing, and the pin shows the new name.
A pin whose target is gone (deleted, in the trash, left behind by a world copy) stays grey,
names what it pointed at, and comes back to life when the target is restored.

**Drawn as the element's round token** (TOKENS.md): a place's picture, an NPC's portrait, the
kind's disc otherwise; small at a distance, the name beside it when zoomed in. A tap opens a
card: the name, a line of the target's notes, *Open*, and for a `map` pin *Go to map*;
there its label (and a note's text) is changed and the pin removed. Names show from three
zoom levels short of the original's resolution. A world map may point at everything of its world,
the places, NPCs, encounters and treasure of every adventure included (the user,
2026-11-27: most places live in an adventure), named in the pin dialog with their
adventure; an adventure's map only at the world's own and that adventure's, as it travels
with the adventure (§3). Opened at a pin (`?pin=`), the map centres on it and
opens its card.

**Placing a pin**: *Pin* in the map's toolbar, then a tap on the map, then the target from a
search over what it may point at (above), or *New place here*, which creates the place (in the map's world, or its adventure)
and pins it in one go, or a note. Dragging moves a pin;
its card removes it.

**A map within a map.** A map may lie on another: `parent` and `frame`, a rectangle in the
parent's pixels, drawn on the parent as an outline with the map's name, opening it. The map
shows its parents as a path above it ("Faerûn › Sword Coast › Waterdeep"). A `map` pin and a
frame are two ways to the same thing; a frame says how far the map reaches. When both maps
have a scale and the frame is set, the form says if they disagree by more than a tenth
("this frame makes the city 4 miles across, its own scale 12"), and corrects neither.
*Lay a map on it* (the larger map's toolbar) chooses the map and draws its rectangle with
two taps at opposite corners; the outline's card opens the map or takes it off. A map copied
to another world alone loses the map it lay on.
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
an eye to show or hide it, a lock against moving things by accident, its count, and under
its ⋯ its name, whether a newcomer sees it, *Show only from this zoom in* (the zoom the map
stands at) or *Show at every zoom*, up and down, and delete. **As built:** up and down
buttons rather than dragging, which works the same with a finger, a mouse and a keyboard;
the panel stands beside the map on a wide screen and folds beneath it on a narrow one. **Shown and hidden is remembered per user** with the view (§4), so hiding the notes
on the iPad does not hide them on the laptop; the layer's `visible` is the start for a user
who has not chosen. The toolbar's tools (pin, name, road) add to the *current* layer, chosen
in the panel; a locked layer cannot be the current one. With none chosen, a pin goes on
*Places* (a note on *Notes*) and a name on *Names*. The current layer is remembered by the
browser, per map; a layer is a group and a pane of its own, so the order is the drawing
order. A map from before layers gets the four on first opening, its pins put on *Places*
and *Notes*.

**Names written on the map** (`MapLabel`): `id`, `layer`, `x`, `y`, `text`, `style` and
`size`, and `angle` in degrees for a river or a mountain range written along it. Styles are
a short fixed list read from the theme (THEMES.md), so labels match Paravent and not a font
picker: *settlement* (upright), *region* (spaced capitals), *water* (italic), *feature*
(small). **The size is in the picture's pixels**, so a name grows and shrinks with the map
as if printed on it; below a few screen pixels it is not drawn. A label is plain text, not
a link: what a place is, its pin says. *Name* in the toolbar, a tap, the text, with a size
filled in that reads at about 16 px at the zoom it was tapped at (a region larger, a feature
smaller); dragging moves it, its card changes the words, style, size, layer and turn (a
slider, turning it as it moves; no handle on the map) or removes it. A sharper picture
stretches names with everything else.

**Notes** are `note` pins (§6) on the *Notes* layer, or on any: a label and Markdown, shown
in the card. They are what the gamemaster wants to remember at that spot ("the bridge is
out", "the bandits' lookout") without making a record of it.

---

## 8. Measuring and roads

**The ruler** (the map's toolbar): tap, tap, tap, or drag (2026-11-27, after the user found
a drag moved the map instead: while the ruler, the scale's line or a frame is drawn, the map
does not pan, a drag draws from where it began, or from the ruler's last point, and the
arrow keys and the zoom still move the map); each stretch and the total shown beside the
line, with the time on foot at the three paces (2, 3, 4 mph; 8 travelling hours to the day),
"212 miles · normal pace 9 days". Double tap or *Done* ends it; nothing is kept. Without a
scale the ruler asks for one first.

**Roads are kept.** `MapRoute`: `id`, `layer` (*Roads* unless chosen), `name`, `kind` (road,
trail, river, sea route, or "" across country), its `points`, and `stretches`
(`RouteStretch`: the point it begins at, its **terrain** (the rows of the 2024 *Travel
Terrain* table that `app/services/travel.py` already reads: Arctic, Coastal, Desert, Forest,
Grassland, Hill, Mountain, Swamp, Underdark, Urban, Waterborne) and whether it is difficult).
A stretch runs to the next one's first point, so consecutive stretches share their end
points, and a road from the city through the forest into the hills is three stretches;
neighbours alike are folded into one. A road, trail, river or sea route is a path: nobody
has to navigate on it, as travel already treats a path; across country the navigator rolls.

**Drawn as lines** coloured by kind (a difficult stretch thicker), the name on its middle
stretch when zoomed in. Editing: *Road* in the toolbar, then taps or drags for its points,
the terrain and difficulty chosen in a bar above the map as it goes (each segment takes the
choice made when it was drawn), *Done* or a double tap, then its name and kind. Its card
says its kind, length and terrains, and offers *Travel* and *Move its points*; folded under
*Edit*, its name, kind, layer and each stretch's terrain and difficulty, and *Remove road*.
*Move its points* shows them to drag; a point's card starts a new stretch there or takes the
point out. **As built:** a pin is not snapped to; instead a pin standing on one of the
road's points names that point as a stop.

**Lengths are computed, never typed:** a stretch's length is its points' distances times the
scale, shown per stretch and in total, in the reader's units. Changing the scale changes them.

**Travel along a road.** A road's card offers *Travel* (when the world plays with the road
and the map has a scale): from one of its points to another, in either direction, or from
where the party stands on it. The dialog is the clock's travel form without how long, the
terrain or difficult ground, which the road gives. **As built:** rather than a route inside
one plan, `TravelService.travel_route` walks the way a stretch of terrain at a time
(`Leg`), each through `travel` itself with its minutes (`Plan.minutes`) worked out from that
terrain's pace (`speed`: the 2024 table's fastest pace, difficult ground and altitude
halving, a vessel's own speed), so every rule of the road applies per stretch unchanged.
A stretch ending with the day still has its night when more road follows (`onward`), and
the next does not roll the weather again (`resumed`). The stretches are joined into one
journey, kept on the clock and logged once; an encounter stops the rest. More than thirty
days on the road is refused.

**Where the party is.** A stretch of travel that stops partway (an encounter, the day's end)
records where on the route: `Journey` gains `map_id`, `route_id`, `route_at` and
`route_to` (miles from the route's first point). The map shows the party there as a marker,
and the road's *Travel* starts from it, towards where it was going. The map says how far
the party went and whether an encounter stopped it. Only one
party position per world, the clock's; nothing else on the map moves by itself.

---

## 8b. The hex grid

Built 2026-11-27. **A hex grid over the whole map, sized by its scale**, for a hex crawl:
`MapGrid` keeps how far one hex is across, **flat side to flat side** as the books measure a
hex ("6 miles", "5 feet"), in the unit typed (`amount`, `unit`, Paravent's factors, §5);
whether the hexes stand on a flat side (columns) or a corner (rows); and one hex's centre
(`x`, `y`, the original's pixels), so it lines up with a grid printed on the picture. Its
size in pixels follows the scale (`MapService.hex_px`): a new scale resizes the grid, a
sharper picture moves its centre with everything else. A map needs its scale first; a hex
smaller than 8 px or larger than the map is refused.

*Hex grid* in the toolbar (once there is a scale): the size, which way the hexes stand,
*Line it up on a hex* (the dialog closes for a tap on a printed hex's centre and opens again
with it), *Take the grid away*. **Drawn on a canvas** above the picture and beneath every
layer, clipped to the picture, only the hexes in view and only while a hex is at least ten
screen pixels across; it hides during a zoom and is drawn again after it. It is shown or
hidden **per user like a layer** (the layer panel's last row, kept as `grid` among the user's
layers in `Place.map_layers`), and players see it too. **The ruler counts hexes** with the
distance: "113 mi · 19 hexes · normal pace 4.7 days".

---

## 9. The players' view

Built 2026-11-27. **The gamemaster shows a map to the players**: *Show to players* beside the
map's name (`WorldMap.players`), and the players of the table's world find it under **Maps**
in their navigation (`/maps`, `app/routes/player_maps.py`), the world's own maps first, then
the adventures'. *Players see it* hides it again. The gamemaster's list marks shown maps
with *Players*.

**What players see is chosen by layer.** Each layer has *Players see it* (`MapLayer.players`,
under its ⋯; a figure beside its name in the panel). A new map starts with *Names* and
*Roads* for the players and *Places* and *Notes* not, as decided ("the players see *Names*
and *Roads*, never *Notes*"); the gamemaster ticks a layer when its pins may be seen. On a
layer the players see:

- **pins** as their kind's disc (never a portrait, whose picture is the gamemaster's) and the
  name of what they point at; their card names it and nothing more. A note shows its title
  and text. A pin to another map leads there only when that map is shown to them; a pin
  whose target is gone is left out. **Never** the target's own notes or a link to it, nor
  a pin to a handout or a session note (whom a handout went to, what the gamemaster noted
  of a day), nor to a quest hidden from them.
- **names** written on the map, as the gamemaster sees them, not to be opened;
- **roads** with their kind, length and terrains; no *Travel*, no stops (a stop is named by a
  pin that may lie on a layer they do not see).

The gamemaster's notes of the map are not shown. **Maps within maps**: a frame shows only
when the map lying there is shown to them, and the path above a map stops at the first map
not shown. **The party's position** on a road is shown, as on the gamemaster's map. The
players have **the ruler** (with the scale), the hex grid, and their own shown and hidden
layers and view (as the gamemaster's, §4, §7), nothing to change.

**Held on the server**: a player reads a map only while it is shown and of the table's world
(CURRENT-WORLD.md: characters play in the table's world); anything else, its tiles and its
picture included, is "not found". The page is built from what they may see, so nothing on a
hidden layer reaches the browser. The gamemaster may open `/maps/<id>` to see a map as the
players do. **Not built:** a trail of where the party went, a toast when a map is shown.

---

## 9b. A map as a picture

Built 2026-11-27. *Picture* in the toolbar draws the map as **one PNG**, from the original
(`app/services/map_picture.py`, Pillow): **the part in view** (sent by the viewer in the
original's pixels) or **the whole map**, at most 2,400 px on its long side, with what lies on
the layers ticked (to begin with those the players see) and, if ticked, the hex grid. Roads
in their kind's colour, names in their style, size and turn, pins as a disc of their kind's
colour with the name beside it; marks and roads drawn larger on a larger picture, so they
read the same. **Download** saves it; **Share with the characters** makes it a handout
(JOURNAL.md): a frozen picture in the ticked characters' journals, the toast and the log
as for every handout (`HandoutService.share_picture`), titled with the map's name unless
another is typed. A handout made from a map belongs to no chapter.

---

## 9c. Claude's pins

Built 2026-11-27 (MCP.md §4, `app/mcp/maps.py`). Claude cannot see a picture; it reads what
words say of a map. **`list_maps(world_id | adventure_id)`**: each map's size in pixels, its
scale (miles a pixel), its layers, the names written on it with where, its pins and its
roads, and whether Claude may pin on it. **`pin_on_map(map_id, kind, name | target_id,
at_label | x, y, note)`**: on a map of **a draft Claude wrote** only, as every writing tool;
what to pin by its name (a place, NPC, encounter or treasure the map may show, a chapter,
another map, a character, a quest, a faction; refused with the names it may show; a
handout, session note or entry of the books by its `target_id`) and where, **on a name the gamemaster
wrote on the map** ("Mirewick", refused with the names written) or at pixels. A note takes a
title and text. **`remove_map_pin`** takes one off again. The gamemaster uploads the map to
the draft and writes its names; the procedure Claude is given ends by pinning the draft's
places on its maps. The world's maps are read, never changed.

---

## 10. What stays out

- **No battle map, no tokens of combatants, no fog of war** (§1); the hex grid is for
  travel (§8b).
- **No drawing tools** beyond pins, names and roads: regions, borders and coastlines belong
  in the picture, made in a drawing program.
- **No projection maths** (§5).
- **No map from the corpus**: the books' maps stay where they are (ADVENTURES.md, *Maps*);
  a gamemaster who wants to pin one uploads a copy of it as their own.

The maps a gamemaster uploads are theirs to use, as the corpus is (TECHSTACK.md §8);
Paravent ships none.

---

## 11. Build order

1. ~~**Maps (L).**~~ Built 2026-11-27. The record, upload into the scope's `media/maps/`, tiling in the background
   with the pixel limit, the viewer (one picture or tiles), the scale form with both ways,
   the ruler with paces, the remembered view, *Maps* in the navigation and on the adventure
   page, context menus, trash, world copy and move.
2. ~~**Pins (M).**~~ Built 2026-11-27. Every target kind, tokens, the card, *New place here*, grey pins of gone
   targets, *On the map* on a place's page, maps within maps with frames and the path.
3. ~~**Layers and names (M).**~~ Built 2026-11-27. Layers with the panel, visibility per user, locks and
   `from_zoom`; labels with styles, size and angle; notes on their layer. Pins made in
   step 2 move onto *Places* and *Notes*.
4. ~~**Roads and travel (M).**~~ Built 2026-11-27. Routes with stretches and terrain, their lengths, *Travel*
   filling the travel dialog, `Plan` walking a route, the party's position.

Each step is usable without the next.

5. ~~**The later ideas.**~~ Built 2026-11-27, at the user's word: the hex grid (§8b), the
   players' view (§9), a map as a picture (§9b), Claude's pins (§9c).

---

## 12. Later, not decided

- **A trail of where the party went**, on the gamemaster's and the players' map.
- **A toast** to the players when a map is shown to them.
