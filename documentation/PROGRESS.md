# Project Progress Scope

Status legend: **[x]** done · **[~]** partially done · **[ ]** not started

## Data foundation

- [x] **Two-root split** — `reference/` (read-only corpus) and `library/` (everything authored
      in Paravent). Replaced `compendium/` + `homebrew/` + `gamestate/`. Settings are now
      `PARAVENT_REFERENCE_DIR`, `PARAVENT_LIBRARY_DIR`, `PARAVENT_REFERENCE_CACHE_DIR`
      — TECHSTACK.md §3. `homebrew_dir`/`media_dir`/`i18n_dir` are derived from
      `library_dir`, so relocating the library moves everything inside it
- [x] **Reference root is write-protected** — `StoreRegistry.assert_reference_readonly()` runs
      on every `load()` and raises `ReferenceRootViolation` if a writable root lies inside the
      corpus; derived artefacts go to `reference-cache/`
- [x] **An unconfigured corpus is a reported state, not a crash** — `reference_dir` has no
      default, `Settings.reference_configured` says so, and startup logs how to fix it. A fresh
      checkout runs with homebrew only, as a data-neutral tool should
- [x] **Load the full corpus.** Every one of the corpus's 93 top-level keys is accounted for
      (2026-11-12): 89 read, four not content (`languageScript`'s fonts, `linkedLootTables`, the
      card maker's `reducedItemProperty`/`reducedItemType`), each named by the corpus check.
      47 categories + 19 fluff keys load — 21,392 entries
      (3,559 of them the specific magic items made from variants) from 176 files, plus
      5,984 fluff records.
      Phased in TECHSTACK.md §6b:
  - [x] **Phase 1 complete.** `class` (30), `subclass` (315), `classFeature` (677),
        `subclassFeature` (1,397), `legendaryGroup` (171), both `generated/` lookups, and
        all **5,984 fluff records** across 19 categories. Every count matches the raw
        files; 0 validation errors, 0 unresolved copies throughout
  - [x] Phase 2 — psionics, vehicles, facilities, charoptions, cults/boons, magic variants and
        the rest of the item model, encounter tables, loot tables, names/life, decks,
        creature-builder sources, the `cr` table. **Magic variants done 2026-10-19**
        (`app/store/magicvariants.py`): the 214 generic variants load as `magicvariant`
        (book, rarity and text lifted out of `inherits`), and make **3,469 specific items**
        at load (~0.2 s) — "+1 Longsword", "Flame Tongue Greatsword", "Mithral Plate
        Armor" — by their `requires`/`excludes`/`inherits` rules: name prefix/suffix,
        rarity, attunement, bonuses, `{=…}` placeholders filled, `valueExpression` prices.
        A variant applies to base items of its own edition (one with none takes its book's;
        a variant from neither rules set makes each name once, from the 2014 base). The
        generic's `reprintedAs` is pointed at the specific reprint, so a world resolves
        "+1 Longsword" to its own printing. An item the books print under the same name
        and source is kept as printed. Searchable, on reference pages, in treasure, and on
        the sheet: the +1 counts in attack, damage and AC. **Treasure tables
        and rolling done 2026-10-19** (`app/store/loot.py`, `app/services/treasure_roll.py`):
        the DMG's individual and hoard tables, gems, art objects and magic item tables of
        both editions, plus the item groups. A treasure's page rolls individual treasure
        or a hoard for a CR on the world's tables (2024 when declared, else 2014, or as
        chosen) and adds it — coins, items in the world's books — to edit before handing
        out; a fight's summary opens a new treasure ready to roll at its highest CR. Rows
        that offer a choice resolve: one of a list, of a generic variant's items, of an
        item group, or 2024's "a rare item" via a rolled theme. Every item of 1,600 hoards
        per edition resolves. **Random-encounter
        tables done 2026-10-19** (`app/store/encounter_tables.py`,
        `app/services/encounter_roll.py`): the 42 environments of `encounters.json`. The
        builder rolls the environment's table for the party's level (the world's books
        listed first); the row's prose is read for creatures — one of its " or "
        alternatives, each `{@creature}` with the number or `{@dice}` written before it —
        which replace the plan and are rated at once; the row heads the notes, and
        creatures outside the world's books are named there, not invented. A row with no
        creatures is an event. **NPC generator** (2026-10-19, `app/store/npc_tables.py`,
        `app/services/npc_gen.py`): `names.json` by ancestry, gender and culture, the 2024
        guide's name styles, the guides' "Detailed NPCs" tables (2024 where it has them,
        else 2014, each row naming its book) and `life.json`'s trinkets; every part
        rerolls alone, the ticked ones become the name and notes, with a humanoid
        statblock from the world's books. **Creature builder** (2026-10-19,
        `app/store/creature_tables.py`, `app/services/creature_builder.py`,
        `/gm/creatures`): `msbcr.json`, `monsterfeatures.json` and
        `makebrew-creature.json`. A design (`library/creatures.json`) starts blank —
        AC, hit dice, Strength and a slam sized so it plays at its CR (all but CR 0) —
        or from a printed creature, whose numbers are linked to its scores
        (`{@hit <$to_hit__str$>}`, the corpus's own placeholders) where they match, so
        a new CR scales them. The CR it plays at is worked out by the guide's method
        (effective HP and AC, damage per round read from the actions, features by
        5eTools's expressions in a small evaluator, never `eval`); "Fit to CR" sets AC
        and hit dice. Publishing writes every design to
        `library/homebrew/bestiary/bestiary-paravent.json` as source `Homebrew` and
        reloads, adding Homebrew to the books of each design's world. **Over MCP** (2026-11-11, MCP.md §10): Claude drafts
        creatures from a description, a world's setting and a CR (`create_creature`,
        `update_creature`), spells and equipment from the world's books; a draft is
        published only once the gamemaster accepts it. A picture by web address
        (`image_url`) or uploaded in the builder, shown in fights' Details. Claude's creature
        fights in its draft adventures, rated from its statblock, and is accepted with it. **Revisions** (2026-11-12): a draft copy of an accepted creature, written back onto it when the gamemaster accepts it. **Its ready-made texts translated** (2026-11-12, TRANSLATION.md §6 note): traits, actions and monster features as a translatable "book", placeholders held like tags; Claude adds them by name over MCP (`ready_made`). **The long tail**
        (2026-10-19): `psionic` (52), `vehicle` (39), `vehicleUpgrade` (31), `facility`
        (53), `charoption` (44), `cult` (30), `boon` (12), `deck` (34) and `card` (711)
        load as categories — every count matches the files, 0 errors. Searchable (vehicles
        and decks with items, psionics and character options with options, facilities,
        cults and boons with the world), linked from their tags, and each with its own
        block on the reference page: a vehicle's hull, capacity, pace and parts (helm,
        stations, weapons with their attacks), a facility's prerequisite, space, hirelings
        and orders, a cult's goal and signature spells, a discipline's psi modes.
        **Cards are keyed with their deck**: 32 `(name, source)` pairs repeat, and
        `{@card Sun|Deck of Many Things}` names the deck in slot 2 (like `{@deity}`'s
        pantheon). A card has no page: the tag resolves its deck through the world's
        books and links to the card on the deck's page, whose preview shows the card; a
        deck lists its cards, the 13-card variant drawing from the full deck. What a card
        mentions counts for its deck in the backlinks. **Item groups and the books' tables**
        (2026-10-26): `itemGroup` (108, "Artisan's Tools", "Ioun Stone") loads as a
        category that `{@item}` resolves to, its page listing its items in the world's
        books; `generated/gendata-tables.json` adds the 2,234 tables printed in books and
        adventures to `table` (which had 13, and rendered them empty), each page showing
        the table and linking to the heading it sits under in the rulebook chapter, its
        preview the first rows. The corpus check's dead references fell from 91 to 25,
        the rest typos upstream. **The rest of the item model** (2026-10-26):
        `itemEntry` — 122 items' text is a `{#itemEntry Absorbing Tattoo|TCE}` reference
        to shared text with gaps (`{{item.detail1}}`, `{{getFullImmRes item.resist}}`),
        expanded at load from the item or what a variant hands down
        (`app/store/item_entries.py`); `itemProperty` (26) and `itemType` (67) as
        categories keyed by their **abbreviation** (`v|xphb`), which is what items,
        `{@itemProperty}` tags (273), `reprintedAs` and `_copy` name — `ref_name` on the
        model, used by the name index and supersession; the page shows the name. An item
        prints its properties through their own templates ("Versatile (1d10)",
        "Ammunition (Range 150/600 ft.; Arrow)"), each linked, its type linked where the
        type has rules text; the sheet links its attacks' properties (and the Lance's
        `{"uid": "2H|XPHB", "note": …}` is no longer lost). **`lifeClass` and
        `lifeBackground`** (2026-10-26): the wizard's Details step rolls a backstory on
        them (CHARGEN-WIZARD.md §8 step 7). **`itemTypeAdditionalEntries`** (2026-11-12):
        Xanathar's text on gaming sets and musical instruments, shown on the type's page
        and its items' as "More from XGE" where the world has the book. **`encounterShape`** (2026-11-12): the five
        shapes (Boss, Boss with Minions, Duo, Trio, Horde) drive the builder's **Build a
        fight** (`app/services/encounter_gen.py`): a difficulty, a shape (or any) and a
        habitat (or anywhere) give a fight from the world's books — named people left
        out — filled group by group to its share of the XP and rated by the builder's
        own arithmetic, the best of sixty tries kept; it replaces the plan and heads the
        notes. Phase 2 is complete
  - [x] Phase 3 — `book/` and `adventure/` long-form text + the bookref indices, then the
        long tail. **Rulebooks done 2026-10-19** (`PublishedBooks` in
        `app/store/published.py`, read on demand like adventures): `/books` (the world's
        books by group, in the navigation for gamemaster and players), a book's contents,
        a chapter with its headings by depth, anchors and a contents list (folded on a
        phone). Search finds every heading of every book (15,336, the bodies read once,
        ~0.2 s), linking to its anchor; the page and the search share one heading rule.
        Book chapters refer in the backlink index too. **The reference screens** (2026-10-19,
        `ReferenceScreens`): `/screen` shows 5eTools's DM screen and the players' quick
        reference (`generated/bookref-*.json`, made from the 2014 books — a 2024-only
        world is told so) on one page each, with a section list; linked from the Books
        page and as "Rules" from the combat tracker; kept out of search and backlinks,
        whose sections only repeat the books. Wide tables in book text scroll in their
        box on a phone. **The long tail** (2026-11-12): `recipe` (316, the cookbooks)
        and `crochetPattern` (20) as categories with their tags, search and pages — a
        recipe's ingredients with their amounts written in ("1½ teaspoons"), equipment,
        instructions and cook's notes; a pattern's yarn, hooks, notions, gauge and steps
        — their text translatable, the amounts held like tags. `converterSample` is the
        sample text of 5eTools's converter page, not content: the corpus check files it
        with the demo files
        **The last of it** (2026-11-12): `tableGroup` (22, tables printed together under one
        name, such as the DMG's "Treasure Hoard: Challenge 0—4"), the 14 rules 5eTools cuts
        out of book sections (`generated/gendata-variantrules.json`, "Weapon Mastery
        Properties"), and `legendaryGroupTemplate`: a legendary shadow dragon keeps its own
        kind's lair actions and gains the shadow dragon's. **Subrace links** resolve by the
        name 5eTools composes ("Elf (Wood)", "Human (Innistrad; Gavony)"). The corpus check's
        dead references fell from 112 (296 uses) to 8 (13), all upstream:
        a race the data lacks (Goblin (Ixalan)), typos, and two links into the wrong book
- [x] **Corpus check** — 2026-10-26, `app/services/corpus_check.py`: `python -m app.cli
      corpus check` (`--references`, `--strict`, `--json`; exit 1 on an error) and
      `/gm/corpus` under the settings gear, run in the background, with the 5eTools version
      from `changelog.json`. Files, keys, validation with file and error path, counts per
      category, fluff/lookups/tables/book bodies, tags; changes since the last check
      (TECHSTACK.md §8 note). Found and fixed: monster templates never applied (208
      creatures), and a malformed file crashing the fluff and lookup loads
      **The library against a release** (2026-11-02, `app/services/library_check.py`,
      TECHSTACK.md §8 note): translations, glossary, declared books, records and every
      campaign text's links, each finding linking to where it is mended. **Moving a
      translation** (2026-11-02): `/gm/translations` lists those whose entry is gone, the
      likely new one filled in, and Move re-points them, passages unchanged
- [x] **Exclude `foundry-*.json`** from indexing — VTT payloads, not content; they are the sole
      cause of all 676 phantom `item`/`spell` duplicate keys. Filtered in `is_vtt_payload`,
      one rule covering every category present and future
- [x] **Composite keys where `(name, source)` is not unique** — `Subclass`, `ClassFeature` and
      `SubclassFeature` define their own `key`; `key_of` uses it. **Also fixed the `_copy`
      index**, which keyed on `(name, source)` and so read a cross-class copy as a cycle —
      the Artificer's Alchemist (classSource EFA) copying the one with classSource TCE.
      That silently dropped 199 records. `composite_key()` now carries the discriminators
  - [x] Book/adventure index and body files do not share a keyspace: neither is a
        compendium category; `PublishedBooks` and `PublishedAdventures` key the index by
        id and read each body on demand (`app/store/published.py`)
- [x] **`app/store/reference.py`** — the bare-name candidate index, built on every load
      alongside the compendium's exact keyspace. 8,239 distinct names; the composite-key
      feature categories are deliberately excluded (see §6c "two traps")
- [x] **Source-set resolver** (documentation/TECHSTACK.md §6c) — four steps: explicit source →
      filter by the declared set → drop `reprintedAs`-superseded candidates → one survivor, the
      "not in your books" state, or a declaration-order tiebreak flagged `AMBIGUOUS`.
      **Correction:** the earlier "resolves 100%" claim held only for spells/creatures/items.
      Measuring every category found a residue the corpus leaves unlinked — 136 names under a
      2014 set, mostly deities and legendary groups. An entry is always returned, so there are
      no dead links; the gap surfaces as a choice
- [x] **`SourceSet` on `World`** (+ optional `Adventure.source_set` override and
      `effective_source_set()`) — empty means "everything imported", so a fresh install works
      unconfigured. `identity` is the order-independent cache key downstream of resolution;
      `editions` derives the rule edition from the declared books rather than a separate flag
- [x] **Supersession must be visible, not silent** — `Resolution.alternatives` carries the
      declared printings supersession set aside; since 2026-10-21
      `ReferenceService.other_printings` offers them: "PHB version" in the preview, "Also in
      your books: PHB" on the reference page, and "Your world uses: XPHB" on a set-aside one
- [x] **Backlink index** — 2026-10-19, `app/services/backlinks.py`: every entity tag in
      every loaded entry, filed under the entry that contains it (a creature's spellcasting
      counts for that creature), built once per load in ~0.7 s, lazily; a page's question
      takes ~1 ms. Resolved per world when asked (TECHSTACK.md §6c): a bare
      `{@spell fireball}` counts for the printing the world resolves it to, a tag naming its
      book for that book; owners outside the world's books are left out; with no world, a
      bare name counts for every printing. The reference page lists "Referenced by", grouped
      by kind (long groups fold), and — for the gamemaster only — "In your campaign":
      chapters and NPC/treasure notes by their tags, NPC statblocks, treasure items and
      encounter monsters by their references, read live
- [x] **Fluff join** — `app/store/fluff.py`. A parallel keyspace reached through
      `fluff.for_entry(category, model)`, not a searchable category. Keys are discovered by
      the `<thing>Fluff` convention, so a new book needs no code change. 1,310 of 5,984
      records are `_copy` shells, resolved within the fluff keyspace.
      **`subclassFluff` needs `className`/`classSource`** — a subclass name does not say
      whose subclass, and a two-part key joined 0 of 148 records while reporting success.
      Orphan fluff (253 monster, 153 item, 81 race) is **reported, not pruned**: some
      records describe creature groups with no statblock, some are `_copy` parents
- [x] **`generated/` lookups** — `app/store/lookups.py`. Spell-list membership is read from
      **both** `class` and `classVariant`: 119 of 936 records carry only the latter (Tasha's
      adding Mind Sliver to Sorcerer/Warlock/Wizard), so reading `class` alone emptied their
      class lists. `include_variants=False` serves a table that has not adopted the expanded
      lists, and `variant_sources()` says which book added a spell
- [x] **Section model** — the `data` arrays inside book/adventure files. **Adventures done**
      (`app/store/published.py`): read on demand, a typed view over the corpus's own dicts with
      an id index, used by published-adventure chapters. Books (`book/book-*.json`) read the
      same way since 2026-10-19 (`PublishedBooks`), by position: their ids are not unique.
      Both 5eTools layouts are read, the old bare `data` and 2.39's wrapped `bookData`/
      `adventureData` (TECHSTACK.md §8 note)

## Framework Development

- [x] **Base UI stack** — htpy `document()`/`app_shell()` in `app/ui/layout.py`, served with the
      design reference's token CSS (`static/css/`) rather than a Tailwind/DaisyUI build, which
      would need Node (decision recorded under TECHSTACK.md's stack table). Light/System/Dark
      toggle, inlined Lucide sprite with `icon()` raising on unknown names (`app/ui/icons.py`).
      The PWA (2026-10-19): `/manifest.webmanifest` (standalone, start `/`), the icon suite
      in `static/icons/` (favicon, apple-touch-icon 180, 192/512 and a maskable 512), the
      root icon paths iOS asks for, all public before login. No service worker: no offline
      mode, as decided (TECHSTACK.md §7).
      **The top bar remembers where you were** (2026-10-21, `app/ui/nav.py`): each item
      covers a section (Worlds includes its adventures, chapters, NPCs and treasure) and
      links back to the last page read there, its root while you are inside it. A cookie
      written by `NavMemoryMiddleware` from full-page GETs: only pages that loaded, never
      an edit/delete/new step, a 4xx forgets it. Arriving through the nav also restores
      the page's scroll position, and of boxes that scroll on their own such as the
      tracker's order (`data-scroll-keep`; `ui.js`, per tab);
      an address a page changes in place (the tracker's selected combatant, `?sel=`) is
      remembered from the next HTMX request's `HX-Current-URL`.
      **Logging in again carries on where you left off** (2026-10-25,
      `app/services/places.py`): the same memory is kept per user on the server
      (`library/places.json`) with the last page read and each character sheet's tab,
      phone view and spell filter. Login (without a `next`) and `/`, where the
      installed app starts, return to that page, on any device; scroll positions are
      kept in localStorage and restored on that return. A write to `places.json` alone
      reloads only itself, not every collection and tracker
      **A settings gear** (2026-10-21) between search and the user menu, the gamemaster's
      only: pages that set the game up rather than run it. Players moved there from the bar,
      the glossary from the user menu
      **The player on a phone** (2026-10-25): one compact top row — the logo alone (back to the
      characters), search, the user menu — and a bottom bar with the sections; the home page is the
      running fight and the list, its heading left out. The sheet and the wizard keep their own bottom bars.
      **The user menu, simplified** (2026-10-25): one row of icon switches — DE / EN
      (the units follow), sun / moon (no "System": until chosen the device's setting
      shows), dice sounds on / off — then "New character" (moved from the player's bar),
      the creature builder for the gamemaster, log out. **The sheet's dice log** sits in the top bar
      (2026-10-25), in reach from every view: on a phone it opens the log view (the
      bottom bar keeps four), wider it jumps to the log in the right-hand column
- [x] **The log (Protokoll)** — documentation/PROTOCOL.md, designed 2026-10-25 with the
      user's decisions: one append-only log per world (`library/protocol/<world>.jsonl`),
      entries as facts with the dice, the advantage and its reasons, every bonus by source,
      visibility (public / private / gm; monster rolls result only), scope (world,
      adventure, chapter, fight, or global); newest on top; reached from a top-bar button
      on every page, toasts that dismiss themselves; the character and combat logs
      become views of it. Five steps (§9). **Step 1 built 2026-10-25**: the entry, the
      append-only store (`app/store/protocol.py`), bonus sources on the sheet's numbers,
      and the sheet's and the tracker's writes — beside the old logs. **Step 2 built
      2026-10-25**: a log button with a badge in every top bar opens the panel (side on
      wide screens, a sheet from the bottom on a phone): entries newest first with the
      result in a box, the dice and every bonus by source, ADV/DIS with its reasons,
      dividers by round, fight and session, filters (All / Me / This fight / This
      character / Monsters), older pages, live over one stream; read-side visibility
      enforced (players: results only for monsters, no DC/AC, no bookkeeping); dice
      chips on reference pages rolled on the server and logged privately. **Step 3
      built 2026-10-25**: a "Private" switch on the sheet for rolls, the gamemaster's
      chips behind the screen, and Reveal in the panel for anything the players read
      less of (hidden, private, a monster's numbers, a DC). **Step 4 built
      2026-10-25**: toasts — at most three, newest on top, gone after about six
      seconds or by ✕, a tap opens the panel; "mine / all / off" per account in the
      user menu. **Step 5, first half, built 2026-10-25**: the sheet's dice log and the
      tracker's fold read the log; the recap too, uncapped; the old logs are copied in on
      start. **Step 5, second half, built 2026-10-25**: the old `log` fields left the
      character and combat formats; the fight's open attacks and save effects are its
      `pending`, a sheet roll in a fight belongs to the fight, initiative from the sheet,
      players' saves, dice sounds and the party card read the log
- [x] JSON Database handling (CRUD) service — `app/store/jsonfile.py`, `app/store/gamestate.py`
- [x] Generate datamodel (Pydantic) for all 5eTools JSON files — `app/models/`: 43 lossless
      models for the 47 compendium categories (21,392 entries, 0 failures), fluff joined onto
      its entry, and typed readers for the books, adventures, treasure, encounter, name and
      creature-builder files. Every top-level key of the corpus is read or named as not
      content by the corpus check (2026-11-12)
- [x] **Browse and search across the whole reference set** — `app/services/search.py`, one
      ranked query over 24 categories in six groups (spells, bestiary, items, character options,
      rules, world), the best-matching group first; 1–15 ms warm. Scoped to the world's books
      with one printing per entry, the same rule as the resolver; what that hides is counted
      and "search all imported books" shows every printing. Features, subclasses and legendary
      groups are not searched — their names repeat and they are reached through their parent
  - [x] **The ⌘K palette** (DESIGN.md §4) — `app/ui/palette.py`, `static/js/palette.js`. A
        native `<dialog>` on every signed-in page, opened by ⌘K/Ctrl+K, `/` or the bar's
        search field; results follow typing, ↑↓ and ↵ navigate, category chips narrow. Full
        screen on a phone. `GET /search` serves the fragment (`partial=1`) and the same form as
        a page, which is also what works without JavaScript. Scoped to the page's world: the
        sheet, the reference page and the filtered party view pass theirs
  - [x] The "add a monster to this encounter" picker — the same endpoint: `pick=<encounter
        id>` searches `group=bestiary` in the world's books; `pick=<treasure id>` items
- [x] **Normalized projections** — `app/store/projections.py`. `cr` (string, four dict shapes,
      `"Unknown"`, absent), `type` (string, dict, `{"choose": …}`, tags as strings or objects),
      `size`, `environment`, AC, HP, spell level/school/concentration/ritual, item kind/rarity.
      Built once per load, 0.19 s; the reference page now reads CR and type through them too
- [x] **Typed filter API over the projections** — `app/store/query.py`:
      `compendium.monsters().cr_between(3, 7).of_type("dragon").in_books(world.source_set)`,
      plus spells and items. Immutable, lazy, mypy-checked; `in_books` applies the resolver's
      source-set + supersession rule to browsing. Deliberately **not** a string query language
- [x] **Tooltip preview endpoint** — `GET /ref/{category}/{source}/{name}/preview`, per-type
      projections (DESIGN.md §7). The world travels in `?world=`, so the client cache, keyed by
      URL, is keyed by the source set too — never by the name alone
- [x] **Search pop-ups** — 2026-10-26: the ⌘K search as a pop-up that inserts instead of
      opens, in every Markdown editor (ADVENTURES.md §5.6) — the world's books and its own
      NPCs, encounters, treasure and chapters, each result inserting its tag, as short as
      the world allows, with words selected before keeping as its label. The picker
      variant (monsters into an encounter or fight, items into a treasure) was already there.
      **The drop-down while typing** (`{@creature gob`) and **folded
      references** (`{@spell Fireball}` alone on a line, opening to the whole entry) —
      ADVENTURES.md §5.4, §5.6. The drop-down also lists the kinds before a name (`{@`,
      `> [!`) and creates
      an NPC, encounter or treasure named in the text where it is written. **The chapter
      check** beside the preview: references that lead nowhere (with the nearest names),
      missing NPCs/encounters/treasure (create), empty encounters, unknown blocks, missing
      images, unclosed blocks, each with its line (ADVENTURES.md §5.7). **Images dropped
      or pasted** into the editor are stored and written at the cursor (§5.5). **A toolbar**,
      full-width writing, and a preview that follows the cursor (§5.6). **One syntax, `{@…}`**
      (2026-10-26, §5.4): a tag alone on its line is the object — the encounter, NPC,
      treasure or party card, a reference folded open — and `{@character}` names a
      player character; the `:::` embeds are rewritten as such tags on start. **⌘Z undoes every
      insertion**, and **a save from an older text is refused** with both texts shown,
      in chapters, adventures and NPC and treasure notes; ⌘B/⌘I, list and link buttons;
      a `{@character}` card stays live (§5.4, §5.6). **Obsidian's Markdown** (2026-10-26,
      §5.3): its callouts with their aliases, folded with `-`/`+`; `[[#Heading]]` and
      `[[Chapter#Heading]]` links; `![[map.png|300]]` images found by name in the world;
      a newline is a line break; the check names unknown callouts, headings that are not
      there and missing images
- [x] **Login, sessions and roles** — `app/services/auth.py`, `app/routes/auth.py`. bcrypt
      hashing (passlib dropped: unmaintained, broken on bcrypt 5), JWT in an HttpOnly cookie
      naming a server-side `Session` record so logout is real, role guards as dependencies
      (`GamemasterDep`, `PlayerDep`) that return 403 rather than hiding buttons. First run
      opens `/setup` to create the gamemaster, then closes. `python -m app.cli user add|list|passwd`
      manages accounts until the gamemaster screen can
- [x] **Role screens** — `/gm` and `/play`, routed to by role from `/` and after login (or
      to the page last read). `/play` lists the player's own characters and the running
      fight; `/gm` is the gamemaster's table below
  - [x] **The gamemaster's table** at `/gm` — a live party overview: one card per character
        with HP, AC, initiative, passive Perception, spell DC, speed, conditions,
        concentration, inspiration, death saves, slots and the last roll, each card swapped
        the moment its character changes (one SSE stream for the whole party, measured
        ~0.05–0.1 s from a player's button or a hand edit). Filterable by world.
        **The gamemaster acts on the cards**: damage, healing, temporary HP and a conditions
        menu, through the same rules as the sheet (temp HP first, concentration saves, death
        saves); the log names the gamemaster; the player's sheet follows in ~0.3 s
  - [x] **Worlds with declared books** at `/gm/worlds` — create, edit, delete; books picked
        from what the corpus actually holds, titled from `books.json`/`adventures.json`,
        grouped core / supplements / settings / adventures, 2024 books marked. Characters
        are placed in a world from the character list; their references then resolve
        against its books (TECHSTACK.md §6c)
  - [x] **What a player may read** (2026-11-03, documentation/PLAYER-VIEW.md,
        `app/services/player_view.py`): per world the books players may read ("Players" on
        each book of the world form; the handbooks and supplements by default), only player
        material in them (no creatures, magic items, traps, GM tables), and whatever their
        character has. Held on the server: reference pages and previews 404, links to the
        rest are plain text, search leaves it out, `/books` lists only player books, the DM
        screen is the gamemaster's. The gamemaster's "View as player" shows a page as a
        player reads it
  - [x] **The players' journal** (2026-11-05, documentation/JOURNAL.md): a send button on
        every box but a secret and every picture of a chapter hands a frozen copy to the
        characters ticked (pictures copied with their effects); a private log entry per
        character pops up as a toast; `/journal` lists a player's handouts, new until
        opened, and the gamemaster's with recipients, who opened them, and Withdraw
        **The books' own boxes and pictures** too, in chapters of a published adventure,
        the pictures now shown from the image folder (2026-11-05)
  - [x] **Image effects** (2026-11-04, ADVENTURES.md §5.5, `app/services/image_effects.py`):
        `torn`, `fade`, `oval`, `sepia`, `aged`, `mono`, `banner` written beside an image
        (`![[tavern.jpg|torn sepia]]`, `cover: media/x.jpg|fade`), rendered once with
        Pillow into the cache as WebP with transparency, so it fits every theme; the cover's
        effects ticked in the images panel
  - [x] **Tokens** (2026-11-12, documentation/TOKENS.md, `app/services/tokens.py`): a round
        token for every element — the table's own upload (an NPC, character, builder
        creature, place, encounter or treasure; any picture, cropped round), else a
        5eTools token from the image folder (creatures, vehicles, objects), else the
        kind's disc in the theme's colours. In the tracker and the players' fight view
        (a numbered label's number on the token), the encounter builder, the lists and
        party cards, the campaign link previews, and on reference pages and previews;
        the players' Details card, the sheet's fight strip, the recap and the journal.
        **To the table** (2026-11-12): an encounter's tokens printed at their true size
        on a 20/25/30 mm grid, or as a ZIP of PNGs for a virtual tabletop, numbered
        and sized by the creature (`app/services/token_export.py`)
  - [x] **Themes** (2026-11-04, documentation/THEMES.md): the whole look as a folder —
        `theme.json`, `tokens.css`, `skin.css`, fonts, pictures, optionally a complete icon
        sprite — shipped in `app/themes/` or dropped into `library/themes/`; the table's
        theme set by the gamemaster, each reader's own on `/themes`. A Content Security
        Policy on every page keeps styles, fonts and pictures to this server. **Tome**
        ships: parchment and leather, the books' statblocks, read-aloud boxes and rules,
        OFL fonts (Libre Baskerville, Cinzel, Alegreya Sans)
  - [x] **Player management** at `/gm/players` — `app/services/players.py`,
        `app/routes/players.py`. Create, edit (name, language, password reset, active),
        delete with a confirmation page. Players only: a gamemaster account cannot be edited
        or deleted there. Deleting **keeps the player's characters**, unassigned; a password
        reset or deactivation ends the player's sessions
  - [x] **Encounters** at `/gm/encounters` — `app/services/encounters.py`,
        `app/services/difficulty.py`, `app/routes/encounters.py`. Create one in a world, add
        monsters through the ⌘K search in picker mode (creatures from the world's books;
        Enter adds the highlighted one and the cursor stays for the next), change counts,
        and see the difficulty live: 2014 rules (thresholds, multiplier with the party-size
        shift) or 2024 rules (budgets), chosen by the world's books or set per encounter.
        Rated against the characters in the world, or a stated party size and level.
        Monsters are stored as `Name|SOURCE` references, so a corpus update re-rates them.
        **2024 ratings corrected** (2026-11-12): a fight is the lowest budget it fits
        within — the XDMG spends "as much of your XP budget as you can without going
        over" — no longer the highest it reaches, which made High a single number and
        a fight just under Low "Trivial". **Build a fight** to a difficulty, from the
        corpus's encounter shapes (phase 2 note above)
  - [~] **Adventures and chapters** (documentation/ADVENTURES.md) — step 1 done:
        `app/store/prose.py` keeps worlds, adventures and chapters as `.md` files with YAML
        front matter, one folder per world under `library/worlds/`, with the character
        store's rules for hand edits (ids written back, copies re-id'd, a broken edit keeps
        the last good version, moves keep ids). `worlds.json`/`adventures.json` migrate once
        on start. Images: `app/services/media.py` — confined to the world folder, sniffed by
        content, no SVG; `GET /media/{id}/{path}` for the gamemaster. **The Markdown dialect**
        (`app/ui/markdown.py`): CommonMark + tables, raw HTML escaped, 5eTools tags,
        `{@npc}`/`{@encounter}`/`{@treasure}`/`{@chapter}`, `[!read]`/`[!sidebar]`/`[!secret]`
        callouts, `:::` embeds and `:::: at` anchors, confined images, headings with a table
        of contents. **The pages**: world → adventures → chapters, ordered, with covers and
        images; a chapter reads with a table of contents and updates live from any edit; an
        editor with live preview. **NPCs and treasure** with a scope (world, adventure or
        chapter), found nearest first from where a name is written; bosses on the world
        page; treasure items through the ⌘K picker; encounters placed the same way; a rename
        rewrites every `{@npc …}`/`::: npc …` in the world. **Published adventures as a base**:
        "start from the book" makes a chapter per section of any of the 99 adventures; the
        book's text is read from the corpus, the gamemaster's notes woven in after the
        entries they name (`:::: at 00e`), lost anchors kept at the end, the creatures each
        section names one click from an encounter
  - [x] **The combat tracker** (documentation/COMBAT.md) — step 1 done: `Combat` records in
        `combats.json`, run from an encounter; `app/services/combat.py` applies the rules —
        groups of one kind on one initiative, the derived turn order, next/previous with
        rounds, temporary HP, bloodied/critical/down, concentration saves owed and rolled,
        conditions with round counts, split, hide, add, remove, end with XP. Characters
        stay in their files and are changed through the play rules. Step 2 done: the
        tracker at `/gm/combat/{id}` — "Run" from the builder or a chapter's embedded
        encounter, the order with groups, HP and conditions per row, the statblock beside
        it, live between devices. Step 3 done: an initiative rolled on the sheet lands in
        the tracker (the newest roll or typed value wins), and `/gm` shows the running
        fights with who still has to roll. Step 4 done: the player view — a strip above
        the sheet and `/play/combat/{id}` with the order, state words, "Your turn!", built
        from a projection that carries no numbers. Since 2026-10-25 the fight view has no
        heading — the round leads — and a switch beside it, character | fight, which also
        heads the strip on the sheet, so the two are one tap apart. A tap on a row of the players' view picks it as the character's target, offered first for the gamemaster's Hit, or shows its details: what anyone sees of a creature (size and kind, movement, gear, the books' description and picture, or the table's own picture of its kind), never a number; and what the party saw it do, learned from the log per world and kind (its attacks and abilities, what each damage type did to it), and what the gamemaster tells them from the statblock — actions, traits, spells, defences, AC and CR as bands, a note — or makes them forget; a creature's spells are cast from the tracker, spending its slots and daily uses — a save spell waits for targets with the statblock's DC, an attack spell for Hit or Miss — and a condition it is immune to is logged (COMBAT.md §5). Step 5 done: the combat log on the
        board, the summary with XP per defeated monster, finished fights listed on their
        encounter, delete. **Awarding XP** (2026-10-19): once per fight, written to each
        character's `experience`; a level reached is offered to the gamemaster to grant,
        never granted by itself; an XP bar on the sheet. **Lair actions and turn-start
        reminders** (2026-10-19): a lair turn on 20 losing ties, switchable; legendary
        actions, recharge rolls, regeneration and concentration shown as each turn starts.
        **Friend and foe** (2026-10-21, COMBAT.md §3): a side per group, switched from the
        row and chosen when adding; tokens tinted by side, each row's background its hit
        point bar coloured by health (2026-10-25; on the players' view a friend's real
        share, a foe's bar full in its state's colour), a "Dead" skull pill, damage
        controls on their own line; the round head rates the fight live from those still
        standing, allies counted as party members of their CR; XP only from fallen foes;
        the order scrolls under the round head; a character's side panel shows its
        fighting stats from the sheet, live, with a button to the full sheet
        **Statblock attacks** (2026-10-22, `app/services/monster_attacks.py`): every action
        with a `{@hit}` gets Attack / Adv / Dis buttons in the side panel; one press rolls
        to hit and the damage the action's tags name ("plus" adds a roll, "or" ends the
        list), dice doubled on a natural 20, no damage on a natural 1 — one log entry,
        coloured for a crit or fumble, with the dice sounds. It then waits under "Attacks to
        resolve" for **Hit** (on a target, opponents first: damage applied, a monster's
        immunities, resistances and vulnerabilities counted per type) or **Miss**.
        **Save effects**: an action with a DC, save and damage (a breath weapon) rolls its
        damage once; each target is then left out, fails, saves, or has its save rolled —
        full damage, half or none, defences counted, a log line each. A condition a failure
        imposes (Frightened for 1 minute, Restrained) is put on with its timer, condition
        immunity respected; condition-only actions get "Call for saves" (559 in the corpus)
        **Players roll their own saves** on the fight view and the strip above the sheet (no
        DC shown); the roll fills in the gamemaster's choice. **Repeated saves**: a
        condition its creature may shake off ("repeat the save at the end of each of its
        turns") is asked on its turn, on the board and the player's phone; a success ends it
        **Players' sheet attacks** in a running fight roll their damage too and wait for the
        gamemaster's Hit or Miss; **save spells** (Fireball, Hold Person) go to the fight from
        the sheet as save effects with the caster's DC, cast with the slot chosen — upcast
        damage from `{@scaledamage}`. A gamemaster's walkthrough ships in the app
        (`app/help/combat.<lang>.md`, `/help/combat`, "How it works" in the tracker).
        **Weapon mastery** (2024): `itemMastery` loads (8 entries); the sheet links a
        weapon's mastery for characters with Weapon Mastery; after a hit Topple calls a
        CON save against prone, the others are noted in the log (Graze after a miss).
        **Healing from the sheet**: a one-creature healing spell (tagged HL, with dice) heals
        a character of the caster's world — slot spent, `{@scaledice}` upcast, the casting
        modifier where the spell adds it; healing potions have Drink. Group healing ("up to
        six creatures") ticks several characters, one roll for all; Heal's fixed 70 (+10 a
        slot in 2024) too. Mass Heal's divided 700 stays with the sheet's HP buttons.
        **Recap** of a fight or a whole session from the logs — dealt, taken, healed,
        natural 20s and 1s, who went down, the biggest hit, conditions — with a text block for
        the gamemaster's notes. **Conditions bend rolls** (`app/services/condition_rolls.py`):
        Blinded, Frightened, Poisoned, Prone, Restrained give disadvantage on the character's
        attacks (Frightened and Poisoned on checks too, Restrained on DEX saves), Invisible
        advantage; combined with the sheet's switch (one of each cancels), named in the log
        line and above the attacks and skills. Monsters' own conditions too. **Exhaustion by
        edition** (2014 levels; 2024 −2 a level and −5 ft.), on rolls, speed and HP. A target's
        condition shows in the attack's target list, and **Critical** makes a hit a crit. A phone pass of
        the recap, help and healing pages put the purse and item lines into German A phone pass of the sheet and wizard
        (all 390px) put weapon properties and damage types into German
  - [x] **Backups** — 2026-10-26, `app/services/backup.py`: the library (never the
        corpus or the cache) zipped into `PARAVENT_BACKUP_DIR` daily at
        `PARAVENT_BACKUP_AT`, the newest `PARAVENT_BACKUP_KEEP` kept, skipped when nothing
        changed; `python -m app.cli backup run|list`; files owner-only (they hold password
        hashes and the signing key); a folder inside the library or the corpus is refused.
        Docker: `/data/backups` mounted, `TZ` with `tzdata` in the image. **Backups**
        under the settings gear (`/gm/backups`): the folder, the schedule and the next
        run, the backups kept, and "Back up now" for the gamemaster
  - [x] **Login rate limiting** — 2026-10-19, `app/services/ratelimit.py`: per username
        and per client address, the first 5 failures in 15 minutes are free, then each
        further one doubles the wait (2 s, 4 s, …, at most 15 minutes). A waiting attempt
        is refused with 429 and `Retry-After` before the password is checked, and the page
        says how long; unknown names count the same, so nothing reveals which accounts
        exist. First-run setup is limited per address. Counters are in memory; a
        forwarded-for header is not trusted
- [ ] Develop 5eTools crossreferencing methods that also spans multiple languages
      — **unblocked by design** (TRANSLATION.md): tags in translated text resolve through the
      English keyspace, so cross-referencing never waits on a translation existing
- [x] **The `{@tag}` renderer** — `app/ui/tags.py` (in `ui/`, not `services/` as first planned:
      it produces markup). A recursive parser (tags nest) and handlers driven by the 110-tag
      catalogue: entity tags resolve through the world's books into links, or grey text that
      says why they don't; roll tags become chips (on a sheet they roll into the character's
      log through one hidden form, without JavaScript too; elsewhere `roll.js` rolls
      locally); shorthand (`{@h}`, `{@atk}`, the 2024 `{@actSave}` family) and formatting,
      colours mapped to tokens. An entries renderer for named traits, lists, tables (with
      roll ranges), insets and quotes. Measured: all 13,081 corpus entries, 16,994 text
      blocks, render in 8.6 s with 0 errors and 0 raw tags left
- [x] **Reference page** `/ref/{category}/{source}/{name}` — spells with their header, creatures
      as statblocks (every attack, damage, HP formula and ability rollable; standard
      legendary header supplied where the 2014 data omits it), items with type, rarity,
      attunement, weight and value; everything else as text. `?world=` keeps links inside
      the entry in the same books. The character sheet's spell and feature text now renders
      through the same renderer, with an "Open full entry" link
  - [x] Hover/tap previews on references (DESIGN.md §7), `static/js/tooltips.js`. Mouse:
        opens after 350 ms, closes 180 ms after leaving, click pins. Touch: a tap opens a bottom
        sheet and never navigates. Keyboard: Tab opens, Esc closes and returns focus. Mode is
        chosen by `(hover: hover) and (pointer: fine)`, never by width. Inside a preview
        references are text and dice are static. Unresolved references explain themselves in
        the same card instead of a native `title`
  - [x] Previews of the campaign's own links — 2026-10-31, `app/routes/library_preview.py`:
        an NPC, place, encounter, treasure, chapter or character shows a card on hover or
        tap, with Open; found from where the link leads (`GET /library/preview?href=`), so
        every link has one without markup of its own
  - [x] Lair actions and regional effects (from `legendaryGroup`) on statblocks —
        2026-10-19, on the reference page and in the tracker
  - [x] Label substitution for translated tag labels (TRANSLATION.md §3) — 2026-10-19
- [~] **MCP server** (documentation/MCP.md, 2026-10-26) — Claude writes a draft adventure
      for a chosen party in a world. User's decisions: streamable HTTP at `/mcp` behind a
      gamemaster's key, writes only into a draft adventure Claude made, monsters from the
      world's books only. **Step 1 built**: keys at `/gm/mcp` (shown once, kept hashed,
      revoked, failures throttled), the reading tools (worlds, overview, party, search,
      monsters by CR/type/environment, an entry as 5eTools JSON, encounter rating for any
      party, the Markdown guide). **Step 2 built**: the `draft` status, an adventure's
      `party` (chosen in its form; its fights are rated for them) and `drafted_by`; the
      writing tools — a draft adventure, chapters (with the chapter check's problems and
      a version check), NPCs, encounters (rated), treasure, deleting — each refusing
      anything that is not a draft written through MCP. **Step 3 built**: the
      `new_adventure` prompt, the party's budget (with the 2014 *Adventuring Day XP*
      table, now extracted from the DMG), random encounters and rolled treasure; a
      scripted run on a copy of the real library. **Reviewing a draft** (a panel over it:
      the key, the party, each chapter's check, each fight's difficulty and their XP
      against the party's day, Accept / Discard) and **campaign memory**
      (`campaign_history`: adventures and their chapters' status, per day of play the
      fights recapped from the log and the XP and levels gained). **Session notes**
      (2026-10-27, ADVENTURES.md §4b): a note per day of play on the world, started from
      that day's recap, read by Claude in `campaign_history`. **Paravent Markdown**
      (2026-10-28): the dialect's name; one helper renders NPC, treasure, encounter and
      session notes where their object is placed; encounter notes rendered and edited
      like chapters on the builder, and folded under the board in the tracker. **Places**
      (2026-10-28, ADVENTURES.md §4): `{@place Name}` — a record with a scope, tags, a
      picture and notes; its page lists what its notes name; a card alone on a line;
      created from the editor, in the holdings panel, written by Claude. Encounter cards
      in a chapter carry their notes folded. **Instructions for Claude** (2026-10-28,
      MCP.md §5): per world the setting plus one text per kind, handed over in
      `world_overview`; **reskins** — an encounter line shown under the setting's name
      ("Mire Lurker" for a Kuo-toa), rated and run as the book creature. **Deleting with
      contents** (2026-10-29): an adventure's or chapter's delete page offers to remove
      its NPCs, places, encounters and treasure too, with their counts; Claude's own
      draft delete always does. **First full run** (2026-11-02): `new_adventure` over a live
      connection on the real library, every tool in order, "Der Takt unter dem Damm". **Revisions** (MCP.md §9): a draft copy of an accepted
      adventure, its changes shown, written back onto the original when accepted
- [x] Wizard state when going back a step — checked 2026-11-02 (CHARGEN-WIZARD.md §6): answers
      live in the draft's file, a step is a GET of them, and a changed answer marks later
      steps for review without dropping them. Typed text followed by a footer click now
      saves and goes, rather than losing the click to the form's swap
- [x] Develop watcher in ALL JSON files to be re-read if anything changes — `app/store/watcher.py`;
      UI update on change still pending, as there is no UI yet

## Translation (TRANSLATION.md)

- [x] **UI string catalogue** — shipped `app/i18n/en.json` + `de.json`, overridable and
      extensible from `library/i18n/ui/<lang>.json` (TRANSLATION.md §5 note). Per-key English
      fallback, plurals, translatable errors. A language selector on every page, logged in or
      not; the choice is stored on the account and in a cookie. Completeness is tested
- [x] **Term glossary** — 2026-10-19: `library/i18n/<lang>/_glossary.json`,
      `{category: {English: name}}`, read again when the file changes (an import is a file
      dropped in). Named on any reference page by the gamemaster, or on `/gm/glossary`
      (settings gear), which lists the most-linked unnamed entries first (a 0.2 s count of
      every entity tag in the corpus). Conditions and skills take the UI catalogue's names
      without a glossary entry. Search and the ⌘K palette match localized names; titles and
      tooltip headers show them, with the English name beneath; so do the sheet, the party
      cards, the encounter builder, the combat tracker and the players' fight view.
      Paravent ships no names. `python -m app.cli glossary fill de` lists every name
      still to give as `"-"`, which reads as missing everywhere, and `/gm/glossary/open`
      lists them by category, 100 to a page, to type in and save together, with names
      suggested through the machine translation's API key, taken only when saved (2026-10-25)
- [x] **Label substitution in the tag renderer** — 2026-10-19: the target resolves in
      English, the label comes from the glossary (before slot 3). Entity tags only
- [x] **Statblock words** — 2026-10-21, `app/ui/rules_text.py` + `rules.*` in the UI
      catalogue: spell school, level, casting time, range, duration; creature size, type
      (gender agreement), alignment, tags, speed, senses, damage types, condition
      immunities, saves, skills, CR/EP, legendary sentence; on reference pages, previews,
      the sheet and the search palette (TRANSLATION.md §4 note); dice as "2W6" in German; attack, hit,
      save, DC and recharge labels; composed names for made magic items ("Langschwert
      +1"); feature texts on the sheet from their records, class features with an editor
      of their own
- [x] **Translation store + per-passage fallback** — 2026-10-21, `app/services/translations.py`:
      every string of a record's text is a passage addressed by its path, falling back to
      English one by one; `library/i18n/<lang>/<category>.json`, one file per book or
      adventure. Shown on reference pages, statblocks (tracker too), previews, rulebook
      chapters, adventure sections, sheet spells and features, and the creature
      descriptions players read in Details, and the DM screen and quick reference
      and fluff on reference pages, and the wizard's descriptions, subclasses and
      subspecies included (TRANSLATION.md §5 note)
- [x] **Staleness per passage** — each passage keeps the hash of the English it translates;
      changed English still renders, marked; a vanished passage is an orphan, counted on
      `/gm/translations`, never dropped
- [x] **Translation UI** — 2026-10-21: the side-by-side editor with tag chips
      (`/gm/translate/…`, "Translate the text" on each page), and `/gm/translations`:
      coverage per category and book, the most-linked untranslated first, outdated and
      orphaned translations
- [x] **Machine drafts** — 2026-10-21, `app/services/machine_translation.py`: the Claude
      API (`PARAVENT_TRANSLATE_API_KEY`), glossary terms supplied, tags verified, stored as
      drafts and marked until confirmed; per record or in the background for the
      most-linked entries, a rulebook or an adventure. **Drafts to review** and **partly
      translated** records (2026-11-03): each listed, opened at its open passages, confirmed
      or machine-translated again at once; and one run over everything players read
- [x] **Import a 5eTools-format translated corpus** — 2026-11-11,
      `python -m app.cli translations import <folder> <lang>` (`app/services/corpus_import.py`):
      matched on `(category, name, source)` (the English name from `ENG_name` where the
      corpus translates names), entries, descriptions, rulebook chapters and adventure
      sections; a passage kept where its links are the English's; drafts unless
      `--reviewed`, the table's own translations kept unless `--replace`, names into the
      glossary; also from `/gm/translations`, checked first, run in the background (TRANSLATION.md §7 note)

### Units — a separate system, not a language feature

- [x] **Per-user unit preference** for typed fields — 2026-10-19, `app/services/units.py`:
      stored on the account (`User.units`). **Since 2026-10-25 set by the language
      switch** (English feet, otherwise metres; `units_for_language`), the separate
      "Units" menu and `POST /units` gone. Statblock speeds and senses, spell ranges
      (reference page, sheet, tooltip), weapon ranges, item weights, the sheet's and party cards' speed, the wizard's speed
      and darkvision. `Page.u` formats; decimals take the language's separator
- [x] **Use the official German conversion `1 ft = 0.3 m`**, never `1/3.28` — printed German books
      say 18 m for 60 ft, not 18.3 m. Also 1 mile = 1.5 km and 1 lb = 0.5 kg
- [x] Prose units are **left alone**. 39,315 unit mentions sit in untagged prose and `{@area}` is a
      map link, not a distance. Optional render-time annotation only; never a rewrite
      **Metric hints** (2026-11-12, `UnitFormat.prose_hints`): a reader in metres sees
      "60 feet (18 m)" beside each clear mention, the text unchanged (TRANSLATION.md §6 note)

## Player Character

- [x] **Wizard UX specified** — CHARGEN-WIZARD.md + `design-reference/13-chargen-wizard.html`:
      both edition orders, mobile-first shell, per-step data sources, validation and back-navigation
- [x] `app/models/pc.py` — the character record (documentation/CHARGEN.md G6). Stores
      **chosen options, not resolved state**: no HP, AC or DC field exists, so a corpus update
      or a change to the declared books re-derives the sheet instead of corrupting it. A test
      asserts no derived field is even present. Stored as **one file per character** in
      `library/characters/` — see the character sheet entry below
- [x] **The choice engine** — `app/services/chargen/` (CHARGEN-WIZARD.md §8 step 1): what the
      world's books offer, one reader for `choose`/`weighted`/`defaultData`, prerequisites, the
      level plan (subclass, ASIs, optional features, spells) and the steps of a draft, its
      answers kept in `choices` so an earlier change shows what it invalidated
  - [x] **The wizard** — `/chargen/new` from `/play` or `/gm/characters`, a draft saved from
        the first step, one form per step with the derived panel live; Species and Class done
        (CHARGEN-WIZARD.md §8 step 2), Abilities (array, point buy, a logged roll final unless
        the world allows re-rolls, manual) and Background with the 2024 +2/+1 (step 3); the
        other steps on the generic question renderer; Features — increases or feats, class
        options with their text, hit points average or rolled (step 4); Skills with where each
        comes from, Equipment packages worn as they are chosen (step 5); Spells with counts,
        level chips and previews, subclass lists included (step 6); Details, a review with every
        derivation, and Finish into a ready character (step 7). The gaps still open are listed
        at the end of CHARGEN-WIZARD.md §8
- [x] `app/services/character.py` — derived stats (documentation/CHARGEN.md G2): proficiency
      bonus, HP, AC (unarmoured, light/medium/heavy, and both Unarmoured Defense variants),
      saves, skills, passive Perception, spell save DC and attack bonus, plus an evaluator for
      the `preparedSpells` formula. Every value carries its **derivation** (`"Chain Mail 16 +
      no DEX (heavy) + shield +2"`) so the wizard can explain itself rather than just report.
      Hit dice, the skill→ability map and armour AC are **read from the corpus**; only the
      formulas are hardcoded, because only they are prose upstream
  - [x] Multiclass spell slots (G3) — the hardcoded multiclass table in `app/services/sheet.py`,
        used only when more than one class casts; a single caster reads its own class table
- [x] `data/rules/abilityscoregen.json` — standard array, 27-point budget, cost table and the
      roll method (documentation/CHARGEN.md G1). The cost table was read out of
      `variantrules.json`'s rendered table rather than retyped; the array and budget are prose
      upstream, so they are hand-authored with their provenance recorded in `_meta`
- [x] **Character file format** — documentation/CHARACTER-FORMAT.md, `character.schema.json`
      (generated; a test keeps it current) and a worked example. One readable JSON file per
      character, editable by hand: compact `"Name|SOURCE"` references, a `state` block for play,
      and `overrides` for values the rules cannot derive (a Ring of Protection, a homebrew class),
      shown on the sheet as overrides. `app/store/characters.py` — a broken hand edit keeps the
      last valid version and says why; a dropped-in file appears; a deleted one disappears.
      **Ownership is by username** (`"player": "tenewaja"`), readable in the file; renaming a
      player follows into their files, and old `player_id` files are converted on start.
      Second example: `documentation/examples/brunhild-ironvow.json`, a level 8 dwarf paladin
      **Maximum HP** comes from `hit_points` — `average` or `rolled` with one result per level,
      CON added per level — and is written to `state.hp_max` on every change; a long rest
      restores `hp` to it
  - [x] **Features that change numbers** — `app/services/traits.py`, by feature name and
        book: Aura of Protection, Diamond Soul/Disciplined Survivor, Jack of All Trades,
        Remarkable Athlete (2014), Unarmored/Fast Movement, Dwarven Toughness, Draconic
        Resilience, Dance bard defense, Defense, Tough, Alert, Mobile, Speedy, Observant.
        Brunhild no longer needs overrides. Chosen options (fighting styles…) now show on the
        sheet. **Resilient** (2026-10-22): the save of the ability it raised, kept in the
        file's `saving_throws` by the wizard and level-up; a file without it is asked once
        on the sheet. **Worn magic items**: equipped,
        and attuned where required, an item adds its typed saving throw, spell attack, spell
        save DC and ability check bonuses, named in the explanation (Cloak of Protection,
        Wand of the War Mage, Rod of the Pact Keeper, Stone of Good Luck); a worn AC item
        now needs its attunement too. **Damage resistances, immunities, vulnerabilities**
        (2026-10-22): from the species, subspecies, feats and worn (attuned where required)
        items, as their records type them, each titled with its source; on the skills pane
        and the tracker's character panel, and counted when a rolled attack hits the
        character. A choice — a dragonborn's ancestry, Boon of Energy Resistance — is asked
        once on the sheet and kept in `resistances`; **Rage** is a switch on the sheet and in
        the tracker (`state.raging`, ended by a rest) that resists bludgeoning, piercing and
        slashing; **Evasion** applies to Dexterity save effects
- [x] **Character sheet** (design-reference/16) at `/characters/{id}` — three columns on desktop,
      two on iPad, five views on a phone. `app/services/sheet.py` derives everything from the file
      plus the corpus: AC from equipped gear, HP across multiclass levels, slots (own table,
      multiclass table, pact magic, subclass casters), attacks, spells with real data and cantrip
      scaling, class/subclass/species/background features. `app/services/play.py` applies the
      rules behind every button: damage/heal/temp HP, concentration saves, death saves, slots,
      casting, resources, conditions, exhaustion, short and long rests, hit dice, and every roll
      with three-state advantage. The player opens only their own characters; the gamemaster
      opens all of them and manages them at `/gm/characters`. **Carrying** (2026-11-12): carried weight, coins included, against carrying capacity (Powerful Build doubling it) on the gear pane (CHARACTER-FORMAT.md §4). **Deleting** (2026-10-21):
      from either list, after a confirmation that says it cannot be undone (and if the
      character is in a running fight); a player only their own, the gamemaster any
- [x] **Live updates** — `app/services/events.py`: every change, from a button, another screen
      or a hand edit of the file, reaches every open sheet over Server-Sent Events (measured:
      ~60 ms from a button, ~0.2–0.6 s from saving the file). The shared dice log shows the
      gamemaster a player's roll as it happens. HTMX 2.0.4 vendored in `static/js/`
      **Dice sounds** (2026-10-22, `static/js/sound.js`, `app/routes/rolls.py`): the device
      that rolled hears the dice, made with the Web Audio API (no files); a natural 20 adds a
      chime, a natural 1 a "wah-wah". Told by an `HX-Trigger` on the roll's answer — sheet,
      party card, tracker, and the reference page's local chips. On/off per device in the
      user menu. A natural 20 or 1 lights up its row in the sheet's log and the party card
- [x] **First wizard for character generation** — built 2026-10-18 (CHARGEN-WIZARD.md §8): any
      starting level, both rule editions, from a draft to a ready character with its sheet
      **The last gaps** (2026-11-02, §8): an ability increase past 20 refused, here and on level
      up; a 2014 background's personality, ideal, bond and flaw tables, in the translation
      where it has them; a Path of the Giant's and an Arcane Archer's cantrip; height and
      weight rolled into the appearance. Left: the other choices a feat brings
- [x] **The sound desk** — 2026-10-31, documentation/SOUND.md: a library shared by every
      world (`library/sounds/`, in the backups), a slide-over with pads, a speaker page
      on the device by the loudspeaker, layered loops, crossfaded music, scenes,
      `{@sound Name}` in texts and a sound when a fight starts. Tested by the user
      2026-11-02
- [x] **Naming styles** — 2026-11-01, documentation/NAMES.md: a pattern for the full name
      and a definition of each part, so any setting's names can be built; the books' tables
      as styles (in memory), six starters, the table's own in `library/naming_styles.json`;
      the NPC generator through them; `list_naming_styles` and `generate_names` over MCP.
      **The workshop** at `/gm/names`: a form and raw text, thirty samples to reject,
      starters copied; used by the generator,
      the wizard's "Suggest a name" and Claude; **race and culture** replaced the per-world
      choice the same day: a style and an NPC carry them, a fallback serves what has no
      style, the workshop is a race index with search, and MCP has research rules. **Invented sounds** (groups and syllable
      templates) and checks: banned sounds and words, consonant clusters, samples that
      run out or start alike. **Learned names** from pasted examples, **kin** from a parent
      chosen when naming, with each person's name parts kept, and **Claude's drafts**,
      offered nowhere until accepted. **Languages, creatures and places** (2026-11-09):
      a style made for one language comes first for its readers and never by itself for
      others; a creature of an encounter finds its style by its subtype, name or a
      style's alias, and "Shown as" rolls a name with a dice button and a menu of other
      styles; a style may name settlements, waters, landscape or taverns, with nine
      starters and the same dice in a place's name; a group named one by one, in the builder and the tracker. **A world's styles** (2026-11-09, NAMES.md §5b): a world ticks the styles its names come from, nothing ticked being all; every roll and menu in it keeps to them; the workshop keeps to a world and names its peoples no style serves, joining a people's English and translated name and offering the alias, or a draft researched by Claude through the API, for every such people at once if asked (three at a time), and listed for Claude over MCP (`unserved_peoples`); Claude drafts styles for a world and may choose a world's styles
- [x] **Fluff in the wizard** — 2026-11-01, CHARGEN-WIZARD.md §8: a line of what the books
      say on each species, class, subclass and background card, all of it with its picture
      folded above the cards once chosen
- [x] **Starting gold and the shop** — 2026-10-31, CHARGEN-WIZARD.md §4 Equipment: a 2014
      class's `goldAlternative` rolled instead of the packages, logged and final; a shop on
      the step for any purse (the 2024 gold packages too), kept in `choices` and derived
      into the inventory and coins, with a purse that stays in view
- [x] **Levelling up** — CHARGEN-WIZARD.md §9: the gamemaster grants a level (one character or
      the party), the player answers only what it brings — subclass, increase or feat, new
      options and spells, hit points — on any character, wizard-made or hand-written.
      **Multiclassing**: which class gains the level, the open classes by their minimum
      scores (2014 `requirements`, 2024 `primaryAbility`), a new class's reduced
      proficiencies on the sheet and its skill or tool asked, its spells counted as if
      it were alone. A 2024 bard/sorcerer/warlock's prepared list grows on level up;
      one spell, invocation, maneuver or Metamagic option may be replaced, and a 2024 Fighter's Fighting Style. **The other choices a feat brings** (2026-11-09, `app/services/chargen/feat_choices.py`): a feat's maneuvers, invocation, style or Metamagic, Weapon Master's weapons (the file's new `weapons`) and prose choices such as Elemental Adept's damage type, in the wizard and on level-up; the sheet counts species' and feats' armor and weapon training; a repeatable feat taken twice asks its choice and its ability increase each time; the 2024 Weapon Master's weapon kind is chosen on the sheet until a long rest
- [x] **Spells from feats, species and backgrounds** — 2026-10-26,
      `app/services/granted_spells.py`: Magic Initiate (the 2024 Acolyte's, Guide's and
      Sage's origin feat, or taken later), a high elf's cantrip, a tiefling's Hellish
      Rebuke… asked in the wizard and on level-up, kept in `spells.granted`; on the sheet
      each is tagged with its grant and cast with its own ability (save DC, attack,
      healing modifier), and a once-per-rest casting without a slot is a resource
      ("Bless (Magic Initiate)") that Cast spends ("Free") and a rest restores
- [x] **Choices between features** — 2026-10-26, `app/services/feature_options.py`:
      Hunter's Prey, Totem Spirit, Divine Order and the rest, and a subclass spell list
      (Circle of the Land): asked in the wizard and on level-up, kept in the class's
      `options`; the sheet shows only the option taken
- [x] **Preparing spells** — CHARGEN-WIZARD.md §10: `/characters/{id}/prepare` from the
      sheet, from the class list or the spellbook, the count from the class's
      progression or formula (the 2014 half casters' division now read); a subclass's
      always-prepared spells on the sheet, marked and not counted
- [x] **Expertise** — CHARGEN-WIZARD.md §11: asked in the wizard and on level up, from the
      proficiencies held; class features from `data/rules/expertise.json` (prose upstream,
      checked against the corpus), feats from their own `expertise`. A feat taken on level
      up now asks its own skills, tools and languages too

## Tests

- [x] JSON Database handling — `tests/test_jsonfile.py`, `tests/test_gamestate_store.py`
- [x] Reading 5eTools Datamodels — `tests/test_models.py`, `tests/test_copyresolver.py`,
      `tests/test_compendium_store.py`
- [x] Reference root never written — `tests/test_two_roots.py`, 16 tests: derived paths, the
      configured/unconfigured split, the write guard (including library == corpus), change
      routing for homebrew-under-library, and an end-to-end write landing in `library/`
- [x] Full-corpus load: every key read or named, 0 validation failures — the corpus check
      (`python -m app.cli corpus check --strict`, exit 1 on an error) holds the real corpus;
      `tests/test_corpus_check.py` holds its rules (loaded, skipped, unknown keys, validation,
      counts), `tests/test_classes.py` (40 tests) the class group and both lookups
- [x] Resolver: for each measured source set, every name with several printings in its books
      resolves to one entry from them — `tests/test_resolver_corpus.py` (2026-10-21). Six
      names fall back to declaration order, flagged `AMBIGUOUS`, and are listed in the test
      so a new one fails: the Shadow Mastiff and five adamantine ammunitions (TECHSTACK.md §6c)
- [x] Resolver: a reference outside the declared set yields the "not in your books" state, not a
      wrong entry and not an exception — same file, down to the rendered link
- [x] Supersession picks the reprint and still exposes the superseded printing —
      `tests/test_printings.py`
- [x] Projections: `cr`/`type`/`size` coerce correctly from every shape, absent included —
      `tests/test_projections.py`; search and palette — `tests/test_search.py`
- [x] Translation fallback: an untranslated passage renders English and is marked, never
      empty — `tests/test_translations.py`
- [x] Tag label substitution keeps the English target: `{@spell fireball}` in German prose still
      resolves to Fireball and still respects the source set — `tests/test_tags.py`
      (2026-10-22), against the corpus for a 2014 and a 2024 world
- [x] `source_hash` mismatch flags but does not suppress a translation —
      `tests/test_translations.py`
- [x] Unit conversion matches the published German values (60 ft → 18 m, 5 ft → 1,5 m) —
      `tests/test_units.py`
- [x] Encounter difficulty — `tests/test_encounters.py`: the committed tables equal a fresh
      extraction from the books, the DMG's four-goblin example, every multiplier step
- [x] Popup Elements — `tests/test_insert_search.py`
- [x] Login, authentication, role guards — `tests/test_auth.py` (service + CLI),
      `tests/test_auth_routes.py` (setup, login, logout, guards, screens, static assets)
- [x] Character format, store, sheet, play rules, live updates — `tests/test_character_format.py`,
      `test_character_store.py`, `test_sheet.py` (incl. Lyra, multiclass, Warlock, 2014 vs 2024
      Paladin, Eldritch Knight against the real corpus), `test_play.py`,
      `test_dice_text_events.py`, `test_character_routes.py`
- [x] Player management and language switching — `tests/test_players.py`; catalogues and
      negotiation — `tests/test_i18n.py`

2226 tests.

---

## What was built in the data layer

- **Pydantic models for 47 compendium categories** (43 models) plus the library's schema.
  Validated against the full corpus: 21,392 entries, 0 failures.
  The compendium models are deliberately *lossless*: unknown upstream fields are preserved
  rather than dropped, because the 5eTools schema grows with every published book.
- **`_copy` inheritance resolution.** 25% of the bestiary (1,144 entries) is a patch against
  another entry rather than a record. All `_mod` modes that occur in the corpus, the twelve of
  `_copy` and the eight of monster templates, are implemented; an unrecognised mode is reported rather than silently skipped.
- **In-memory compendium store** with ranked substring search across every loaded category.
- **Atomic-write JSON CRUD** for game state, safe under concurrent writes.
- **File watcher** that reloads on any JSON change without a restart.
- **FastAPI app** exposing read endpoints for the compendium.

## Measured against the real corpus

| Metric | Value |
| --- | --- |
| Corpus size | 499 files, ~114 MB, 94 entry keys, 26,918 entries |
| Entry keys loaded | 89 of 93 read, the other four not content (2026-11-12) |
| Entries loaded | 21,392 distinct in 47 categories (3,559 made from magic variants), from 176 files |
| Fluff records | 5,984 from 129 files, 19 categories, 0 failures |
| Class group | 30 classes, 315 subclasses, 677 + 1,397 features — every count matches the files |
| Validation failures | 0 |
| Unresolved `_copy` directives | 0 |
| Unknown `_copy` modes | 0 |
| Cold load time | ~3.8 s (everything: compendium, fluff, lookups, indices, library) |
| Search latency | ~10 ms over every category; 1–15 ms scoped to a world |

Coverage is complete (2026-11-12) and the cold load stays under four seconds. Should it
grow past that, the answer is `reference-cache/` — a parsed snapshot invalidated by corpus
mtime — not a database.

## Translation

**No longer a blocker.** The earlier entry here said cross-language work was blocked until a
German corpus existed. The design in TRANSLATION.md removes that dependency: a translation is a
**text-only lookup over the English record**, and tags in translated text resolve through the
English keyspace. Cross-referencing therefore works at 0% translation coverage and at 100%, with
no code path that waits for a corpus to appear.

Scope reminder: **pure 5eTools format only.** A translated corpus in 5eTools format imports
directly on `(category, name, source)`. Foreign schemas (Foundry VTT and the like) are explicitly
out of scope — they carry no `{@...}` tags and no join key, which is the whole reason the overlay
model exists.

What is still true: Paravent ships no translation, exactly as it ships no 5e content.

## Next, in dependency order

> **Done 2026-10-09:** the two-root split; **all of corpus Phase 1** (5 class categories, both
> `generated/` lookups, the 5,984-record fluff join); and **the reference index + source-set
> resolver** with `SourceSet` on `World`/`Adventure`. 313 tests, 95% coverage.
>
> Four silent bugs fixed on the way — a `_copy` index that dropped 199 records, a spell lookup
> that emptied 119 spells' class lists, a fluff join that reached 0 of 148 subclass records, and
> `{@deity}` parsing that read the pantheon as a source. All reported a clean load; each was only
> visible by checking results against the raw files. One documented claim was also corrected:
> see the resolver entry above.

1. ~~**`app/services/chargen.py`** — the choice engine~~ — **done 2026-10-18** as
   `app/services/chargen/`: walk `choose`/`weighted`/`defaultData`
   uniformly, evaluate prerequisites, keep the back-navigable stack. Two confirmed traps:
   `defaultData` keys are `a`/`b` **and** `A`/`B`/`C` **and** an undocumented `_` in 14 places;
   eight `equipmentType` deferred pickers must expand into filtered item lists.
2. ~~**The wizard UI**~~ — **done 2026-10-18** (CHARGEN-WIZARD.md §8): one form per step,
   htpy fragments, the draft in `library/`.
3. ~~Normalized projections~~ — **done 2026-10-16**, with the typed filter API, search and
   the ⌘K palette. ~~Encounters~~ — **done 2026-10-16**, with difficulty under both rule
   editions. ~~Worlds, adventures and chapters~~ — **done 2026-10-16** (ADVENTURES.md §8
   steps 1–5). ~~The combat tracker~~ — **done 2026-10-18** (COMBAT.md §8, all five
   steps); lair actions and awarding XP wait on the `legendaryGroup` work and on
   experience tracking.
4. ~~The recursive `{@...}` parser~~ — **done 2026-10-15** as `app/ui/tags.py`, with the reference page.
5. ~~Theme + base page~~, ~~icon helper~~, ~~auth~~ — **done 2026-10-15**: token CSS instead of a
   Tailwind build, `app/ui/icons.py`, `app/ui/layout.py`, login/setup/logout and role guards.
6. ~~The gamemaster screen and the character screen~~ — **done 2026-10-15**: the live sheet,
   the live party overview, worlds with declared books. Next on this path: adventures and
   encounters inside a world, then the combat tracker.
7. ~~`combatant_card()` in htpy~~ — **done 2026-10-18** as the tracker's rows (COMBAT.md).
8. ~~`GET /ref/.../preview` — the tooltip fragment endpoint~~ — **done 2026-10-15**, with
   hover/tap previews.
9. ~~Translation~~ — **done**, all six steps of TRANSLATION.md §8 (2026-11-12), in its
   order: UI strings → glossary + label substitution → translation store → translation UI →
   unit preference → metric hints in prose. Steps 1–2 alone already deliver a
   tool that feels German, and nothing in the chain blocks on a complete translation existing.
