# Stack decision – Paravent (D&D 5e)

Recommended stack:

| Layer | Recommendation | Reason |
| --- | --- | --- |
| Backend | **FastAPI + htpy** | server rendering, async, Pydantic, no build tooling |
| Interaction | **HTMX** (+ minimal Alpine.js) | dynamics without an SPA, "little JavaScript" as required |
| Styling | **Tailwind CSS v3** | utility-first, basis for the components |
| Components | **DaisyUI** | built for HTMX |
| Data storage | **JSON only, two roots** — `reference/` read-only, `library/` read-write | no database daemon that can fail; JSON hand-editable |
| Media | **Files on disk** (`library/media/`) | images/sounds/maps never in DB/JSON, only referenced |
| Data | **A corpus in the 5eTools format, read in place** (user brings the files) | the tool ships no data; which data a table uses is up to whoever runs it |
| Operation | **Local network only, multiuser, no offline** | auth + roles; no cloud, no offline sync |
| iPad | **PWA** (installable, touch-optimized) | tablet use at the table |
| iPhone | **Mobile** for players | |

> **Styling, as built (2026-10-15).** The Tailwind + DaisyUI row conflicts with "no build
> tooling": DaisyUI is an npm plugin, and Tailwind's standalone binary cannot load it without
> Node. Decided: the app serves the **token CSS from the design reference** as-is —
> `design-reference/shared/tokens.css` + `components.css` copied to `static/css/`, plus
> `static/css/app.css` for what the app adds. Same tokens, same contrast-checked palette, zero
> build step. A Tailwind/DaisyUI migration stays possible later; it would change class names,
> not tokens. Fonts fall back to system faces — the app makes no network requests.

**The most important findings:**

- **Data: the tool is data-neutral.** Paravent ships **no** rules data. It reads data in the 5eTools JSON format from a folder whoever runs it points it at; which data that is, and the right to use it, is theirs. Details in section 8.

The established pattern ([reference](https://blakecrosley.com/guides/fastapi-htmx)):

- **Full-page routes** deliver a complete document that inherits from `base.html`.
- **Fragment routes** deliver only an HTML snippet that HTMX hooks into the DOM (`hx-get`, `hx-post`, `hx-swap`).
- **The same** htpy engine renders both – one template set, no API/frontend split.

Example flow "adjust HP in combat":

```txt
[iPad] click "-7 HP"
   └─ hx-post="/combat/{id}/damage"  →  FastAPI
        └─ JSON update
        └─ renders partial _combatant_card.html
   └─ HTMX swaps back only this card
```

No page reload, no JSON parsing in the client, no duplicated logic.

---

## 3. Data storage: two roots, JSON only

Deliberately **no** server database (no MongoDB daemon that can fail).

The foundation rests on one distinction, and every path decision follows from it:

| Root | Contains | Written by |
| --- | --- | --- |
| **`reference/`** | The imported 5eTools corpus, plus artefacts derived from it | **Never the app.** Read-only at runtime. |
| **`library/`** | Everything created *with* Paravent — worlds, adventures, characters, encounters, combat, homebrew, media | The app, constantly. |

Reference data is somebody else's, arrives as a snapshot, and is replaced wholesale when the
user re-imports. Library data is the user's, is the thing worth backing up, and must survive a
corpus swap untouched. Mixing the two in one tree makes "delete the corpus and re-import" a
dangerous operation; separating them makes it a `rm -rf` of one directory.

### Reference → the 5eTools corpus, external and read-only

The corpus is **not copied into the repository.** `PARAVENT_REFERENCE_DIR` points at wherever
the user keeps it; the server reads it in place. Paravent ships no 5e content (section 8), so
there is nothing to vendor, and a 114 MB tree of someone else's JSON does not belong in the
project's version control.

- **Loaded into RAM on server start**, search/filter in Python over dicts/lists — at this data
  volume a linear scan is **millisecond**-scale and no index is needed.
- **The upstream layout is read as-is**, not normalised onto disk first. The 5eTools file and
  key layout *is* the on-disk schema; an import step would add a build phase and break lossless
  round-tripping when upstream adds a field.
- **Validated against Pydantic models** on load. Compendium models are deliberately *lossless*:
  unknown upstream fields are preserved, because the schema grows with every published book.
- **Read-only is enforced, not merely intended.** No store writes under `reference/`. Anything
  the app derives from the corpus (tag catalogue, caches, computed indices) is a build artefact
  and lives in `reference-cache/` under the project, never next to the source.

The **whole** corpus is in scope — all 94 entry keys, not the 24 the first pass loaded. Half the
corpus by entry count sat unloaded: classes and subclasses (which character generation depends on
entirely), every `*Fluff` record (descriptions and images the tooltips need), the `generated/`
lookup tables, plus psionics, vehicles, decks, loot tables, names and life tables. See section 6b
for the phased load order.

### Library → the user's own content

Campaigns, adventures, encounters, NPCs/PCs, treasure and above all the **combat tracker**
(initiative, HP, conditions) are written **live and frequently**. These stay JSON as well, one
file per collection under `library/`:

- **One file per collection** (`users.json`, `worlds.json`, `encounters.json`, …), each a JSON
  object keyed by record id so a single-record update is a dict assignment, not a rewrite of an
  array, and the file stays diffable.
- **Atomic writes.** Every save goes to a temp file in the same directory, is fsynced, then
  renamed over the target. A crash mid-combat therefore leaves either the old file or the new
  one, never a truncated one. Writes are serialised per file so two concurrent HP updates cannot
  lose each other.
- **Strict Pydantic models.** Unlike the imported corpus, these schemas are ours, so unknown
  fields are rejected rather than preserved.
- **Backed up by Paravent itself** (2026-10-26, `app/services/backup.py`): zip files of
  this root only into `PARAVENT_BACKUP_DIR` at `PARAVENT_BACKUP_AT`, keeping
  `PARAVENT_BACKUP_KEEP`; each file read under its writer's lock, skipped when the
  library's fingerprint (path, mtime, size per file) is unchanged. The backup folder is
  one of the write roots, so the reference guard refuses one inside the corpus.
- **Homebrew lives here, not in reference.** It is authored content, uses the 5eTools *format* so
  it can be a `_copy` target and share the compendium keyspace, but it is the user's work and
  belongs on the backed-up side of the line. It loads after the corpus, so a homebrew entry with
  the same `(name, source)` overrides an imported one.

> An earlier draft named SQLite here. That contradicted the "JSON only, no database daemon"
> decision at the head of this section; JSON with atomic writes covers the durability need at
> this scale without adding a dependency.

> An earlier draft also split storage as `compendium/` + `homebrew/` + `gamestate/` under one
> `data/` root. That conflated three different lifetimes — replaceable snapshot, authored
> content, live state — and put homebrew on the same side of the fence as the disposable corpus.
> The `reference/` + `library/` split is the reconciliation.

### Media → file system

Images, sounds, maps, tokens **never** go into JSON – only as files under `library/media/`,
referenced by ID/path. FastAPI serves them as static files. Media is authored content, so it sits
in `library/`, backed up with everything else the user made.

**Architecture tip:** use Pydantic models as a shared layer for JSON loading – one validation model.

---

## 6b. Corpus coverage: what gets loaded, in what order

**The target is the whole corpus.** Measured against the reference corpus (499 files, ~114 MB),
the data splits into **94 entry keys / 26,918 array rows**. The first implementation pass loaded
24 of those keys, reading 140 of 499 files for 10,491 distinct entries — roughly half the corpus
by row count, and the missing half includes the parts the planned features depend on most.

> **Done 2026-11-12.** Every one of the 93 top-level keys of the 2.39 release is accounted
> for: 89 are read (47 compendium categories, 19 fluff keys, and the book, adventure,
> treasure, encounter, NPC and creature-builder loaders), and four are not content —
> `languageScript` (font files), `linkedLootTables` (which loot table a variant sits on;
> the treasure rolls read the tables), and the card maker's `reducedItemProperty` and
> `reducedItemType`. The corpus check lists them as such (`NOT_CONTENT_KEYS`), and
> `PLANNED_KEYS` is empty. Of `generated/`, the variant rules and both table keys
> (`table`, `tableGroup`) load; the maps index, the old site addresses and the
> navigation index are not used.

(Entry counts below are corpus rows. The store keys entries by `(name, source)`, so reprints
collapse — which is why 13,669 rows in the loaded keys become 10,491 stored entries.)

Loading everything is what makes cross-linking work. A `{@class Fighter}` tag that resolves to
nothing is a dead reference, and DESIGN.md §6 is explicit that there are never dead links — so
coverage is not a nice-to-have, it is what the tag renderer and tooltip layer stand on.

### Phase 1 — unblocks character generation and tooltips

| Key(s) | Entries | Why first |
| --- | --- | --- |
| `class`, `subclass`, `classFeature`, `subclassFeature` | 3,008 | CHARGEN.md depends on these **entirely**. Nothing in the wizard works without them. One file per class under `class/`, each holding all four keys. |
| all `*Fluff` keys (`monsterFluff`, `itemFluff`, `raceFluff`, …) | 5,984 | Descriptions and images. DESIGN.md §7 tooltips show prose that lives only here — a statblock alone cannot fill a preview. |
| `generated/gendata-spell-source-lookup.json` | lookup | **The** spell-list join. CHARGEN.md trap #2: filtering spells any other way produces wrong class lists. |
| `generated/gendata-subclass-lookup.json` | lookup | Resolves subclass references per source. |
| `legendaryGroup` | 171 | Lair and regional actions. A legendary monster's statblock is incomplete without its group. |

### Phase 2 — content the gamemaster browses and drops into a session

| Key(s) | Entries | Purpose |
| --- | --- | --- |
| `psionic`, `vehicle`, `vehicleUpgrade`, `facility`, `charoption`, `cult`, `boon` | 262 | Whole categories invisible to search until loaded (done 2026-10-19). |
| `magicvariant`, `itemGroup`, `itemProperty`, `itemType`, `itemMastery`, `itemEntry` | 455 | Completes the item model — variants are how "+1 longsword" exists at all. |
| `encounter`, `encounterShape` | 47 | Random-encounter tables for the encounter builder, and the shapes its "Build a fight" fills to a difficulty (2026-11-12). |
| `individual`, `hoard`, `dragon`, `gems`, `artObjects`, `magicItems`, `dragonMundaneItems` | 100 | Treasure tables. PROJECT.md lists treasures as a first-class feature. |
| `name`, `lifeClass`, `lifeBackground`, `lifeTrinket` | 135 | NPC and character generation flavour (CHARGEN.md step 10). |
| `deck`, `card` | 745 | Deck of Many Things and friends. A card is keyed `(name, source, set)` and reached through its deck, like a feature through its class (done 2026-10-19). |
| `monsterfeatures`, `makebrewCreatureTrait`, `makebrewCreatureAction` | 345 | Source material for the creature builder/scaler. |
| `cr` | 34 | The DMG's *Monster Statistics by CR* (`msbcr.json`): proficiency, AC, HP, attack, damage and DC per CR — creature-builder data. **Not** the XP table (see note below). |

> **Corrected 2026-10-16 when building encounters.** The `cr` key carries no XP. The
> difficulty calculator's four tables — XP by CR, the 2014 thresholds and multipliers, the
> 2024 budgets — exist only as rendered tables in `book/book-dmg.json` and
> `book/book-xdmg.json`. They are read out of those tables by `python -m app.cli rules` into
> `data/rules/encounterdifficulty.json` (`app/store/rulestables.py`), the same treatment as
> the ability-score constants (CHARGEN.md G1); a test re-extracts and compares.
>
> **Changed 2026-11-11 for publication.** That file now holds only the tables that are also
> in the System Reference Documents, whose licence lets Paravent pass them on (NOTICE.md):
> XP by CR and character advancement (SRD 5.1) and the 2024 budgets (SRD 5.2.1). The 2014
> thresholds, multipliers and adventuring day are in no SRD, so they are no longer
> committed: `rulestables.classic` reads them from the user's own `book/book-dmg.json` when
> a fight is rated, and again whenever that file changes. A corpus without the 2014 guide
> has no 2014 rating; the encounter page names the missing book and the 2024 rules still
> rate.

### Phase 3 — long-form text

| Key(s) | Entries | Purpose |
| --- | --- | --- |
| `book` + `book/book-*.json` | 122 + 61 files | Rulebooks as browsable, searchable, cross-linked text. PROJECT.md asks for a rulebook database. |
| `adventure` + `adventure/adventure-*.json` | 198 + 99 files | Published adventures, same treatment. |
| `generated/bookref-*` | — | The DM-screen and quick-reference indices 5eTools builds over that text. |
| `recipe`, `crochetPattern`, `converterSample` | 353 | The genuine long tail. Recipes and crochet patterns load as categories (2026-11-12); the converter's samples are not content. |

Root-level files are **the index**, the subdirectory files are **the body**: `adventures.json`
lists the 99 adventures with cover, level range and table of contents, while
`adventure/adventure-lmop.json` holds the actual sections. Both are needed — the index to browse,
the body to read.

### Two structural notes

- **Fluff is a parallel keyspace, not a field.** `monsterFluff` is keyed by the same
  `(name, source)` as `monster` and lives in a sibling file. Join at load time and hang it off the
  entry; do not model it as a separate searchable category, because nobody searches for fluff.
- **`data` (1,230 entries across 162 files) is not a category.** It is the section array inside
  every book and adventure file. It needs a recursive section model, not a row in the category
  table.
- **Exclude `foundry-*.json` and `spells/foundry.json` entirely.** These are Foundry VTT
  integration payloads, not content. Indexing them manufactures 676 phantom duplicate keys —
  every `item` and `spell` "collision" in the corpus traces to them and to nothing else.
- **`(name, source)` is not a unique key for every category.** Class and subclass features
  legitimately repeat: *Additional Maneuvers* (Battle Master, PHB) exists at **levels 7, 10 and
  15**. Those categories need `level` and `className`/`subclassShortName` in the key; the
  book/adventure index files and their body files must not share a keyspace either. Excluding
  foundry files and keying these categories properly leaves the keyspace clean.

---

## 6c. Reference resolution: the source set

The whole corpus stays resident in RAM, so *finding* an entry is never the problem — a keyed
lookup measures **0.24 µs** and a substring scan over all 18,858 names takes **1.1 ms**. The real
problem is knowing **which** entry a reference means.

### The problem: most tags do not name a source

5eTools tags address a target as `name` or `name|SOURCE`, and the source is usually **absent**:

| Tag | References with no source |
| --- | --- |
| `{@skill …}` | 90% |
| `{@condition …}` | 89% |
| `{@spell …}` | 87% |
| `{@background …}` / `{@class …}` | 68% |
| `{@creature …}` | 44% |

Bare names are genuinely ambiguous: **379 of 557 spell names (68%)** and **686 creature names**
exist in two or more sources. Usually that is the edition split — `mind sliver` is in both `TCE`
and `XPHB`; `violet fungus` is in four books. A global "prefer the newest printing" rule would
silently hand a 2014 table its 2024 statblock.

### The solution: the adventure declares its books

**A world declares the set of sources legal at this table; an adventure may narrow it.** The set
is `(source code, edition)` pairs — `PHB` 2014, `XPHB` 2024, `MM`, `MPMM`, and so on.

Resolution is then deterministic, in four steps:

```txt
resolve(category, name, source?) →
  1. explicit source on the tag      → exact keyed lookup, done
  2. filter candidates to the active source set
  3. drop candidates superseded by `reprintedAs` within that set
  4. one survivor   → resolve
     none           → the "not in your books" state (DESIGN.md §6)
     several        → the first in declaration order, flagged `AMBIGUOUS` with every
                      candidate attached, so the UI offers the choice
```

Step 4's last branch exists because the corpus does not always record a reprint link. The user
declared their books in an order, so the earlier-listed one wins — a visible, explainable
fallback rather than a silent global rule. **An entry is always returned**: a corpus gap becomes
a choice, never a dead reference.

**Step 3 is what makes it airtight, and it costs nothing** — the corpus already carries the
reprint graph both ways:

```json
{"name": "Allip", "source": "MTF",  "reprintedAs": ["Allip|MPMM"]}
{"name": "Allip", "source": "MPMM", "otherSources": [{"source": "MTF", "page": 116}]}
```

So the rule is *drop any candidate that declares it was reprinted as another candidate you also
hold.* No hand-maintained book-precedence table, and it stays correct when new books are added.

### Measured

For **spells, creatures and items** — the categories that carry almost all tag traffic —
steps 1–3 resolve every ambiguous name:

| Declared source set | Names still ambiguous | Unresolved after `reprintedAs` |
| --- | --- | --- |
| 2014 core + expansions | 237 creatures | **0** |
| 2024 core only | 0 | **0** |
| Mixed (2024 core + 2014 expansions) | 19 spells, 12 creatures, 44 items | **0** |

The 237-creature residue in the 2014 row was a single pattern — MPMM reprinting MTF (127) and
VGM (110) — and step 3 clears all of it.

> **Re-measured 2026-10-21** (`tests/test_resolver_corpus.py`, which holds these claims
> against the corpus): with magic variants now made into items, two links the corpus does
> not record leave six names to step 4's declaration-order choice. VGM's *Shadow Mastiff*
> names MPMM's *Shadow Mastiff Alpha* as its reprint while MPMM prints a plain Shadow
> Mastiff too (2014 and mixed sets); and XGE's *Adamantine Ammunition* and the 2024 DMG's
> *Adamantine Weapon* both make the arrow, bolt, firearm bullet, needle and sling bullet,
> with no reprint link between the variants (mixed set). Every name still returns an entry
> from the declared books.

> ⚠️ **Corrected 2026-10-09 after implementing it.** An earlier draft of this section claimed
> steps 1–3 resolve *everything*. They do not. Measuring **all** categories rather than three
> shows a residue where the corpus simply records no reprint link, even though one exists in
> print:
>
> | Declared source set | Ambiguous names | Single answer | Needs a choice |
> | --- | --- | --- | --- |
> | unrestricted | 2,275 | 1,954 | 321 |
> | 2014 core + expansions | 378 | 242 | 136 |
> | 2024 core only | 0 | 0 | 0 |
> | both editions | 1,758 | 1,698 | 60 |
>
> The residue is concentrated in **deities** (105 under a 2014 set) and **legendary groups**
> (17), where 5eTools reprints an entry without pointing back. Hence step 4 below; the original
> "no tiebreak heuristic is needed" was true only of the three categories first measured.

> ⚠️ **But read what it resolves *to*.** 5eTools models the 2024 books as reprints of the 2014
> ones: `{"name": "Fireball", "source": "PHB", "reprintedAs": ["Fireball|XPHB"]}`. So step 3
> treats XPHB as superseding PHB, and a both-editions table silently gets the **2024** spell.
>
> That is the right default — a group that owns both books is playing the newer one — but it is a
> **decision, not a neutral fact**, and it must be visible. Two requirements follow:
>
> - The resolved entry always shows its source, so the gamemaster can see *which* fireball they
>   got (DESIGN.md §7 already leads every preview with source).
> - When supersession discarded a candidate the set also contains, the tooltip offers the
>   superseded printing as a secondary action ("PHB version"). Never silently drop a book the
>   user explicitly declared.
>
> A table that wants the 2014 rules should declare only the 2014 books — which is the normal case
> and needs no special handling.

### Three consequences

**Zero survivors is a feature, not a failure.** Running *Curse of Strahd* with `PHB`, `MM` and
`CoS` declared means a stray `{@creature mind flayer|VGM}` renders as the designed "source not
imported" state rather than quietly pulling in a monster from a book nobody at the table owns.
The source set is a **correctness boundary**, not merely a disambiguator.

**Edition stops being a global mode.** It is a *consequence* of the declared books: `XPHB` in the
set means 2024 rules, `PHB` means 2014. Character generation reads edition from the same
declaration instead of branching on a separate flag — this retires CHARGEN.md's trap #1.

**Resolution is context-dependent, so caches must be keyed by it.** The same
`{@spell mind sliver}` resolves differently in two adventures. Anything memoised downstream —
tooltip fragments above all — must include the source-set identity in its cache key
(DESIGN.md §7).

### Two traps the implementation hit

**`{@deity}` puts the pantheon where every other tag puts the source.**
`{@deity Bahamut|Nonhuman|PHB}` is `name|pantheon|source`, so a parser shared with
`{@creature}` looks for a book called "Nonhuman" and resolves nothing. `{@card Sun|Deck of Many
Things|DMG}` does the same with the deck (found 2026-10-19, when decks were loaded). Verified
across the corpus: these two are the only entity tags that shift the source along.
TRANSLATION.md §3 records the same trap for label substitution.

**Feature categories are not addressable by bare name.** `classFeature`, `subclassFeature` and
`subclass` repeat names legitimately — "Potent Spellcasting" exists for several classes at
several levels. Indexing them by name manufactures ambiguity the data does not have (328
subclass features, 102 class features), and nothing in the corpus references a feature by name
alone; they are reached through their parent class. The bare-name index therefore skips them,
which is also why it holds 8,239 names rather than 9,659.

**Item properties and types are addressed by code, not name.** Items list
`"property": ["V|XPHB"]` and `"type": "M|XPHB"`, tags read `{@itemProperty V|XPHB|Versatile}`,
and their `reprintedAs` and `_copy` name the code as well. Their key is
`abbreviation|source`, and a model's `ref_name` (the abbreviation here, the name everywhere
else) is what the bare-name index and supersession use (found 2026-10-26).

### Defaults and placement

- The set lives on the **world**, because a campaign normally has one book list; an adventure
  carries an optional override for the case where one module is deliberately narrower.
- **An empty declaration means "everything imported."** The tool must work before anyone has
  configured anything; a new user with a fresh corpus gets every entry resolvable, and narrows
  later if they want the correctness boundary.
- The set is **library** data (it is the user's decision, per world), never reference data.

---

## 7. iPad / touch / the table

The concept names **iPad** as the target device for the DM and **iPhone** for players. **Decided:
operation on the local network only, no offline mode, multiuser.**

> **Reconciled 2026-09-01.** An earlier draft of this section said "single user, no auth". That
> contradicted PROJECT.md (multiuser, gamemaster/player roles, phone PWA for players) and
> PROGRESS.md (login/session/role management as a deliverable). The **multiuser reading is
> authoritative** — it is the superset, and a solo game is simply one gamemaster with no players.

- **Hosting:** a local server on the home network (laptop/mini-PC/NAS as host). The iPad accesses `http://<host>:<port>` via Wi-Fi. No internet, no cloud, no app store needed.
- **PWA** (Progressive Web App): installable on the iPad home screen, fullscreen, own icon – feels like an app.
  **As built (2026-10-19):** `/manifest.webmanifest` from `app/main.py` (`display: standalone`,
  theme `#4560DC`), the icon suite in `static/icons/` (its README lists the files), and the
  root paths browsers and iOS request on their own (`/favicon.ico`, `/favicon.svg`,
  `/apple-touch-icon.png` and `-precomposed`) — all public, so the icon shows before login.
  The head carries Apple's own tags (`apple-mobile-web-app-capable`, `-title`,
  `-status-bar-style: default`), which iOS still reads instead of the manifest. No service
  worker, deliberately: there is no offline mode.
- **Touch-first layout:** large tap targets, initiative list and HP controls operable with the thumb; statblocks in columns/sheets.
- **No offline mode** – deliberately omitted. HTMX needs a continuous connection to the local server; that's unproblematic on the home network and greatly simplifies the architecture.
- **Multiuser** – login with a role of gamemaster or player, session persistence, and state
  shared between connected clients. Auth is JWT with bcrypt-hashed passwords; sessions live in the
  game-state store like any other record. Keep the backend and game-state model lean.
  **As built (2026-10-15):** `app/services/auth.py`. The JWT sits in an HttpOnly, SameSite=Lax
  cookie and names a `Session` record in `library/sessions.json`; a token is honoured only
  while that record exists, so logout and password changes end access server-side. The
  signing key is `PARAVENT_SECRET_KEY` or, if unset, generated once into
  `library/.secret_key` (0600). The first visit with no users opens `/setup`, which creates
  the gamemaster and then closes; further accounts via `python -m app.cli user add`.
  passlib was dropped for `bcrypt` directly — passlib is unmaintained and fails on bcrypt 5.
  **Failed logins slow down** (2026-10-19, `app/services/ratelimit.py`): per username and per
  client address, after 5 failures in 15 minutes each further one doubles the wait, up to
  15 minutes; a waiting attempt gets 429 and `Retry-After` without its password being checked.
  The client address is the connection's: behind a reverse proxy every login would share the
  proxy's address, so forwarding headers would have to be trusted deliberately first.
- **Roles:** the gamemaster authors worlds, adventures and encounters and drives the combat
  tracker; a player controls their own characters. The role gates what a route will do, not
  merely what the UI shows.

---

## 8. 5e data source: the user's corpus in the 5eTools format (decided)

**Decision:** Paravent is a tool, not a library of content. It ships **no** game data and reads everything it shows (spells, monsters, items, classes, species, features, rules text) from a corpus in the 5eTools JSON format that whoever runs it provides.

- **Data-neutral tool.** No repository, release or container image of Paravent contains rulebook data. Which data a table uses is the decision of whoever runs it, as with a word processor and the books on one's shelf.
- **The data is the user's responsibility.** Paravent provides the loading mechanism for the format. Obtaining the data, and having the right to use it, is up to the user. The corpus is read in place: never copied into the project, never written to, never passed on.
- **Why the whole format and not an SRD dataset.** The System Reference Documents cover too little for the feature set (character creation across the books, published adventures, the full bestiary), so the loader targets the whole 5eTools schema rather than an SRD-only source.
- **What the repository does carry from others** is listed in [`NOTICE.md`](../NOTICE.md): short passages and three numbers tables of the System Reference Documents (CC BY 4.0), and nothing of the rulebooks beyond them. Publisher's prose, tables that are in no SRD and pictures stay out of the repository, its tests, its documentation and the website's screenshots.

### Technical load strategy

5e.tools uses its own, well-documented **JSON schema** (one file per category, e.g.
`spells/spells-phb.json`, `bestiary/bestiary-mm.json`). It includes a **tagging syntax** in the
texts, e.g. `{@spell fireball}`, `{@damage 2d6}`, `{@dc 15}`, `{@creature goblin}`.

There is deliberately **no conversion step that writes to disk.** The corpus is read in place from
`PARAVENT_REFERENCE_DIR` on every start; "import" means discover, resolve and validate into RAM.
An offline normalisation pass would have to be re-run after every upstream update, and would
silently drop whatever the target schema had not anticipated.

1. **Discover** every file under the reference root and map its keys onto categories.
2. **Resolve `_copy`** inheritance *before* validation — a shell entry often lacks fields the
   model requires, so validating first would reject a quarter of the bestiary.
3. **Validate** against Pydantic models that are **lossless**: unknown upstream fields are
   preserved, not dropped. The 5eTools schema grows with every published book, and a leaner
   target schema would quietly discard the new field until someone noticed.
4. **Join fluff** onto its entry by `(name, source)`.
5. **Render tags** at display time — the `{@...}` syntax → HTML with cross-references (clicking
   `{@spell fireball}` opens the spell). This is the most involved but most valuable part, and it
   is a render-time concern, not a load-time one: the stored text keeps its tags verbatim so a tag
   pointing at a not-yet-imported source resolves automatically once that source appears.

> **Checking a corpus (2026-10-26).** Because every step collects what it cannot use instead
> of failing, a load can look clean while losing data. `python -m app.cli corpus check` and
> the gamemaster's **5eTools data** page (`/gm/corpus`, settings gear) read the corpus again,
> independently (`app/services/corpus_check.py`): unparseable files, rows that fail
> validation (file, entry and Pydantic error path), unresolved `_copy`, unknown `_mod` modes
> and templates, rows in the files against entries in the store per category (every
> difference explained or an error), every top-level key matched to the loader that reads it
> (unknown keys are new upstream), fluff, lookups, tables and book bodies, and optionally every
> tag. The report is kept in `reference-cache/corpus-check.json` and compared with the
> previous one. **The version** is the newest entry of the corpus's `changelog.json` (5eTools
> ships no version file), shown on that page and logged at startup.
>
> Its first run found two silent bugs: **monster templates were never applied** —
> `bestiary/template.json` was not read and the resolver looked for `_mod` where templates
> keep `apply._root`/`apply._mod`, so 208 creatures (Cat Skeleton, every Reduced-Threat
> monster) lacked their template; the eight modes templates use (`addSenses`, `scalarAddHit`,
> `scalarAddDc`, `scalarAddProp`, `scalarMultProp`, `prefixSuffixStringProp`, `maxSize`,
> `scalarMultXp`) are now implemented. And **one malformed JSON file anywhere crashed the
> fluff and lookup loads**; they now skip it, as the compendium did.
>
> **A whole 5eTools release (2026-11-02).** `PARAVENT_REFERENCE_DIR` may name the release
> itself: without a `books.json` of its own but with one in `data/`, `data/` is the root. Since
> 2.39.0 a book's and an adventure's body is wrapped, `{"bookData": [{"id", "source", "data":
> […]}]}` (`adventureData` likewise); `body_sections` reads both layouts.
>
> **Holding the library against a new release (2026-11-02).** Every check also runs
> `app/services/library_check.py`, read-only, because the library addresses the corpus by name,
> source and position and nothing fails when those change. Translations: passages whose English
> changed or went, records whose entry, chapter or book is gone (with the likely new name), tags
> of the English they lack, dead links of their own; glossary names of gone entries; books a
> world or adventure declares or is based on; encounters, NPCs, treasure, characters and running
> fights naming a gone entry, chapters on a gone book section, notes anchored to a gone id; and
> every campaign text through the chapter check's own code, reporting only links. These are
> warnings (a glossary name a note): the corpus still reads. Each links to where it is mended,
> and the next check lists how each kind moved. The optional reference pass now reads
> descriptions, rulebooks and adventures too, not only entries (dead references 25 → 129).

---


---

## 9. Recommended overall architecture

```txt
paravent/
├── app/
│   ├── main.py                 # FastAPI entry point (local host)
│   ├── config.py               # pydantic-settings; PARAVENT_-prefixed env vars
│   ├── models/                 # Pydantic models (reference + library)
│   │   ├── base.py             # lossless base model for imported records
│   │   ├── values.py           # polymorphic field types (ac, cr, hp, speed, type)
│   │   ├── compendium.py       # Spell, Monster, Item, Class, Race, Feature, Rule, fluff…
│   │   ├── pc.py               # player character: chosen options + resolved state
│   │   └── gamestate.py        # worlds, adventures, encounters, combat, users, sessions
│   ├── store/
│   │   ├── jsonfile.py         # atomic writes, per-path locking
│   │   ├── copyresolver.py     # `_copy` inheritance
│   │   ├── compendium.py       # load reference JSON, in-memory search/filter
│   │   ├── reference.py        # the resident index + resolver (§6c), backlinks
│   │   ├── library.py          # the user's own content, persisted per mutation
│   │   ├── watcher.py          # reload on any JSON change
│   │   └── registry.py         # store lifecycle
│   ├── routes/                 # full pages + HTMX fragments
│   ├── services/               # tags, dice, encounter difficulty (CR/XP), creature scaler
│   │                           # + i18n: glossary, fallback resolution, unit formatting
│   └── importers/
│       └── fivetools.py        # corpus discovery + validation (incl. the {@...} tag renderer)
├── data/
│   ├── reference-cache/        # derived indices; disposable, rebuilt on demand
│   └── library/                # EVERYTHING THE USER MADE — back this up
│       ├── tags/               # ships with the project, moved here 2026-11-02
│       │   ├── TAGS.md         # the 110-tag catalogue
│       │   ├── 5etools_tags.json # machine-readable tag table the renderer imports
│       │   └── scan_tags.py    # regenerates the catalogue from the corpus
│       ├── worlds.json         # incl. the declared source set (§6c)
│       ├── adventures.json
│       ├── encounters.json
│       ├── characters/         # one JSON file per character (CHARACTER-FORMAT.md)
│       ├── combat.json
│       ├── users.json / sessions.json
│       ├── homebrew/           # own content in 5eTools format; overrides the corpus
│       ├── media/              # images, sounds, maps, tokens (referenced only)
│       └── i18n/               # translations (TRANSLATION.md) — text-only overlays
│           ├── ui/             # UI string catalogues per language
│           └── de/             # _glossary.json + one file per category
├── static/
│   ├── css/                    # Tailwind output
│   ├── daisyui/                # DaisyUI components
│   └── js/                     # HTMX + Alpine (locally hosted, no CDN needed)
└── pyproject.toml

<PARAVENT_REFERENCE_DIR>/        # OUTSIDE the project — the user's 5eTools corpus, read-only
├── bestiary/                    # bestiary-*.json + fluff-bestiary-*.json
├── spells/                      # spells-*.json + fluff-spells-*.json
├── class/                       # class-*.json — class, subclass, classFeature, subclassFeature
├── book/ adventure/             # long-form source text, one file per publication
├── generated/                   # gendata-* lookups, incl. spell-source-lookup
└── *.json                       # items, races, feats, backgrounds, … 63 root files
```

### The world → adventure → module hierarchy

PROJECT.md's content structure is a **record hierarchy inside `library/`**, not a directory tree.
Each level is a record carrying a parent id, so a query is a filter rather than a directory walk,
and an NPC can be promoted from module scope to world scope by changing one field:

```txt
World ─┬─ NPC · Treasure · POI                    (world scope)
       └─ Adventure ─┬─ NPC · Treasure · POI      (adventure scope)
                     └─ Module ── Encounter · NPC · Treasure · POI   (module scope)
```

> **Superseded in detail by ADVENTURES.md (2026-10-16):** the third level is a *chapter*, prose
> lives in `.md` files inside a folder per world (media included), and objects carry a `Scope`.
> The scope rule below stands.

Scope is a lookup rule: resolving an NPC from a module searches module → adventure → world, so a
recurring villain is defined once at world level and referenced everywhere below it.

## 10. Feature coverage versus Lions Den "Game Master 5e"

- **Combat tracker** → `combat.py` + HTMX partials (initiative, HP, conditions, statblock view), game state in `gamestate/*.json`
- **Encounter builder/analyzer** → `services/encounter.py` (CR→XP, difficulty thresholds, random enemies)
- **Creature builder/scaler** → `services/creature.py` (enter stats → generate the rest)
- **DM screen / rules reference** → compendium search (in-memory), customizable tables, external links
- **Campaign manager** → `campaign.py`, adventures as **Markdown** (as wished in the concept)
- **Dice roller** → `services/dice.py` (e.g. `1d20 + 6d9 + CON`), hooked up via Alpine
- **Compendium** → full 5e.tools import + own homebrew import
- **Import (Fight Club / 5e.tools / own content)** → `importers/` with file upload

---

### Sources

- FastAPI + HTMX: [blakecrosley.com/guides/fastapi-htmx](https://blakecrosley.com/guides/fastapi-htmx)
- License-compliant data: [Open5e](https://open5e.com/), [dnd5eapi.co](https://www.dnd5eapi.co/), [CC-SRD](https://github.com/Tabyltop/CC-SRD), [BTMorton/dnd-5e-srd](https://github.com/BTMorton/dnd-5e-srd)
