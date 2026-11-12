# Translation in Paravent

**Scope:** how Paravent renders in more than one language — the UI, the 5eTools reference data,
and units — and how a translation gets authored, stored and kept honest as the corpus changes.

**Date:** 2026-09-14 · **Status:** UI strings built (§5 note); the glossary and label
substitution built 2026-10-19 (§4 note); translated text, the editor, machine drafts
and the coverage page built 2026-10-21 (§5, §7 notes); importing a translated corpus built
2026-11-11 (§7 note). Still design: prose unit annotation.

> **One sentence:** the English record stays canonical and structural; a translation is a
> **text-only lookup layered over it**, never a replacement entity.

---

## 1. Three systems, deliberately separate

These get conflated constantly. They have different data, different lifecycles and different
owners, and merging any two of them creates work that cannot be undone later.

| System | Translates | Source | Ships when |
| --- | --- | --- | --- |
| **UI strings** | Buttons, labels, errors, nav | Authored by us | Immediately — independent of everything else |
| **Reference text** | Spell/monster/item names and prose | Authored per language, over English records | Incrementally, entry by entry |
| **Units** | ft ↔ m, lb ↔ kg | Computed, or carried by a translation | Independent of language (see §6) |

**Units are not a language feature.** A German group may play in feet; an English group may
prefer metres. Tying the two would be a one-way door.

---

## 2. Principle: structure is English, text is translatable

Everything mechanical stays in the English record and is never duplicated into a translation:

```txt
Reference (English)   structure · mechanics · relationships · tags · source · _copy
                      ── canonical, read-only, never modified by translation ──
        ▲
        │  field-level override, text only
        │
Translation lookup    name · entries · flavour prose
```

Resolution is per field, not per record:

```python
value = translation[lang].get(field) or english[field]
```

Three consequences worth stating plainly:

- **Partial translation is a normal state**, not a degraded one. A spell with a German name and an
  untranslated body is valid and renders that way — German heading, English text, fallback marked.
- **A translation can never break mechanics.** It carries no AC, no damage dice, no `_copy`, no
  source. A bad translation produces bad prose, never a wrong statblock.
- **Deleting every translation is always safe.** The tool falls back to English in full.

This is also why translations are not modelled as homebrew. Homebrew *overrides* an entry in the
same keyspace and may change mechanics; a translation only re-skins one.

---

## 3. Tags are what make this work

Translated text uses the **standard 5eTools `{@...}` syntax**. This is the load-bearing decision:
a tag in German prose resolves through the **English** keyspace, so the source set (TECHSTACK.md
§6c), `reprintedAs` supersession and the backlink index all keep working untouched.

Display text comes from the glossary, not from the tag target. 5eTools already has the mechanism —
**slot 3 is a display override**:

```txt
{@condition charmed||bezaubert}    → links to `charmed`, displays "bezaubert"
{@creature Riding Horse||Horse}    → the corpus's own use of the same trick
```

Verified against the corpus: **33,537 three-slot tags**, and slot 3 is display text for all 19
entity tag types (`creature` 13,442 · `item` 3,628 · `variantrule` 1,665 · `status`, `race`,
`action`, `class`, `spell`, `condition`, …).

So the renderer needs one addition, not a rewrite:

```txt
render({@spell fireball}, lang=de):
  1. resolve "fireball" in ENGLISH   → the Fireball record (unchanged, §6c rules)
  2. label = glossary[de][spell]["Fireball"] or slot-3 or the English name
  3. emit the link with the localized label
```

**Reference integrity and translation coverage become independent.** An untranslated spell still
links correctly from translated prose, and a translated name shows everywhere the entry is
mentioned — including inside entries nobody has translated.

> ⚠️ **Two traps.** Slot 3 is positional and **not** display text for every tag: `{@area X|id|x}`
> and `{@book ch|src|page}` use those slots for other things, and `{@deity Bahamut|Nonhuman|PHB}`
> puts the pantheon in slot 2 (`{@card Sun|Deck of Many Things|DMG}` the deck), moving display
> text to slot 4. Apply label substitution only to the entity tags listed above.
> And **never machine-generate tags by pattern-matching translated prose** — a glossary word in
> non-technical use ("der Trank macht unsichtbar" vs the *invisible* condition) would be silently
> mis-tagged. Tags come from the translator or the source, never from a regex over finished text.

---

## 4. The term glossary — build this first

A flat `(category, english_name) → localized_name` table. Small, high value, and translatable
long before any body prose.

```json
{ "condition": { "Charmed": "Bezaubert", "Prone": "Liegend" },
  "creature":  { "Goblin": "Goblin", "Owlbear": "Eulenbär" },
  "spell":     { "Fireball": "Feuerball" } }
```

The glossary alone makes bilingual:

- search and the ⌘K command palette (DESIGN.md §4 — already specified to search both languages)
- combat-tracker condition pills
- tooltip headers and reference-drawer titles
- every tag label in otherwise-English prose

**It is therefore the first translation deliverable**, ahead of any translated body text. A user
with only a glossary gets a usefully German-feeling tool.

---

> **Built 2026-10-19.** `app/services/glossary.py`: one file per language,
> `library/i18n/<lang>/_glossary.json`, `{category: {English name: localized}}` keyed by
> the compendium category (`monster`, not the tag name `creature`). It is re-read when its
> modification time changes, so importing names is dropping a file in that format.
> `Translator.term(category, name)` answers for the page's language; English has none.
> **Conditions and skills** fall back to the UI catalogue (`condition.prone` → "Liegend"),
> so the most-linked terms in the corpus read German with an empty glossary.
>
> **`"-"` is a name still to give** (2026-10-25). `python -m app.cli glossary fill de`
> adds every entry name of the corpus the file lacks as `"-"` (magic items made from a
> variant left out: their names are composed), so the file is the complete list to
> translate by hand; names already there, given or `"-"`, are kept. Anything without a
> letter or digit (`-`, `—`) is a placeholder and **reads as missing everywhere**: labels
> fall back to English, search and the machine drafts ignore it, the glossary page
> offers the entry as unnamed. Clearing a name writes `"-"` back, so the key stays.
> **Names to give** (`/gm/glossary/open`, linked from the glossary page as "7141 names
> still to give"): every `"-"` of the file, by category with its count, searchable by
> the English name, 100 to a page in one form; the English name opens the entry in a
> new tab. Saving writes the names typed (an empty field stays `"-"`) in one write and
> returns to the same page, where the next names have moved up.
> **What players read** (2026-11-03): a switch on that page keeps the list, its counts and
> the suggestions to the names a player of any world meets (documentation/PLAYER-VIEW.md).
> **Suggested names** (`app/services/name_suggestions.py`): with the machine translation's
> API key set, "Get suggestions" sends the open names in view to Claude — a category at a
> time, 150 per request, three at once — with each entry's book and a sample of the
> table's own names for that kind, held to a JSON schema. Answers are kept apart in
> `_glossary_suggestions.json`, never in the glossary: an entry stays `"-"` until the
> gamemaster takes its suggestion (one, or every empty field of the page) or types a name,
> and the suggestion is forgotten once a name is saved. Progress shows on the page.
>
> - **Naming**: the gamemaster names an entry on its reference page ("Name in other
>   languages"), or on `/gm/glossary` (settings gear): per language, the most-linked entries
>   without a name first — every entity tag in the corpus counted once, ~0.2 s, so Mage
>   Hand (487 links) comes before a spell nobody cites — and the names given, editable.
> - **Shown**: tag labels (§3: the link still resolves in English; the glossary name comes
>   before slot 3), reference-page and statblock titles and tooltip headers with the
>   English name beneath, search and the ⌘K palette ("feuerb" finds Fireball, shown as
>   Feuerball / Fireball).
> - **The table's screens** (2026-10-19, `app/ui/names.py`): the character sheet (spells,
>   items and attacks, feats and options, the species · class · background line;
>   since 2026-10-22 languages, tools, armor and weapon training — fixed words in the UI
>   catalogue, a named weapon by its item — and where each feature comes from), the
>   party cards, the encounter builder, the combat tracker (rows, turn names, the lair,
>   turn-start reminders), the players' fight view and the character wizard (2026-10-22:
>   species, lineages, classes, subclasses and backgrounds on their cards with the English
>   name beneath; feats, options, spells, skills, tools and languages in its lists; either
>   name filters; the skills table with German ability abbreviations, what is granted and
>   by whom, the equipment packages and pickers with coins as GM/SM/KM, and the review). The English name is the element's
>   title. Records keep English: a combatant labelled "Owlbear 1" shows as "Eulenbär 1",
>   and a label the gamemaster renamed is shown as written. The fight's log keeps the
>   names it was written with.

> **Statblock words (2026-10-21).** What a statblock says *about* a spell or creature is
> data, not prose, so it is phrased from the data through a fixed vocabulary in the UI
> catalogue (`rules.*` in `app/i18n/<lang>.json`), not through the glossary:
> `app/ui/rules_text.py`. Schools of magic, casting times and durations ("1 Aktion",
> "10 Minuten", "Unmittelbar", "Bis der Zauber gebannt wird"), ranges and areas
> ("Selbst (Kegel von 4,5 m)"), the spell level ("Grad 3"), size, type and alignment
> with the size agreeing with the type's gender ("Kleiner Humanoider (Goblinoid),
> neutral böse", "Große Monstrosität", "jede böse Gesinnung"), subtype tags, movement
> modes, senses, damage types, condition immunities, saves and skills, the challenge
> rating with EP in the language's number format, "natürliche Rüstung", the
> legendary-action sentence, and the search palette's RK / TP / HG. Species, classes and
> languages in those lines come from the glossary. Distances follow the reader's unit
> setting, never the language. Types and alignments follow the German monster list's
> words (Tier, Schlick, Humanoider, gesinnungslos, jede); the standard languages the
> German SRD's table (Gemeinsprache, Zwergisch, Halblingisch …).
> **Dice** are written as the language does: "2W6 + 3" in German (`rules.dice_letter`),
> on roll chips, hit point formulas, weapon and spell damage, hit dice and the roll log.
> Only the text changes; a chip rolls and titles its formula as written ("2d6+3").
> **Labels the renderer prints itself** — "Melee Weapon Attack:", "Hit:", "Strength
> Saving Throw:", "DC 15", "(Recharge 5–6)" — come from the same vocabulary
> ("Nahkampf-Waffenangriff:", "Treffer:", "Rettungswurf auf Stärke:", "SG 15",
> "(Aufladung 5–6)"). Translations keep these tags verbatim, so a translated passage reads
> in one language; untranslated prose around them stays English.
>
> **Made magic items** ("+1 Longsword", "Flame Tongue Greatsword") are not records, so the
> glossary cannot list them: `app/services/item_names.py` composes their name from the
> glossary's base item and variant — "Langschwert +1", "Flammenzunge (Zweihandschwert)" —
> or leaves the English when either is missing.
>
> **Features on the character sheet** read their translation from the record they live in:
> feats and options from their own record, class and subclass features from theirs (keyed
> by the corpus's composite key, so the same name at two levels stays apart; editor at
> `/gm/translate/feature/<category>/<key>`), species and background traits from the
> section of the species or background record they are. The gamemaster finds a
> "Translate the text" link on each.

## 5. Storage, keys and staleness

### Where

```txt
library/i18n/
├── ui/                  de.json, en.json — UI strings
└── de/
    ├── _glossary.json   the term table (§4)
    ├── spell.json
    ├── monster.json
    └── …
```

Translations are **library** data — authored by the user, backed up, never written under the
reference root. They live outside any single world, because a translation is about the corpus, not
about one campaign.

> **UI strings, as built (2026-10-15).** The UI catalogues the project authors **ship with the
> code**, in `app/i18n/<lang>.json` (English and German). `library/` is gitignored, so strings we
> write could not ship from there. `library/i18n/ui/<lang>.json` is an **override layer**: it
> replaces individual shipped strings, or adds a whole language the code does not ship — a
> `fr.json` there makes French appear in the selector, falling back to English per key. Built in
> `app/services/i18n.py`; reloaded by the file watcher.
>
> - Flat keys (`login.submit`); `{placeholder}` fields; counts as `{"one": …, "other": …}`.
>   `_meta.name` is the language's own name, shown in the selector.
> - **Language preference, strongest first:** the logged-in user's `language`, the
>   `paravent_lang` cookie (a choice made before logging in), `Accept-Language`, English.
>   Switching while logged in saves to the account, so it follows the user across devices.
> - **The selector is on every page**, logged in or not, as a `<details>` menu of submit
>   buttons — no JavaScript required.
> - **Errors are keys, not sentences.** `AuthError("error.username_taken", username=…)` renders
>   in the reader's language; `str()` of it renders English for logs and the CLI.
> - Tests assert every shipped catalogue has exactly English's keys and placeholders, and that
>   every literal key used in `app/` exists.

### The key

`(category, english_name, source)` — the same triple the reference index uses, so a translation
addresses exactly one entry and inherits the resolution rules already defined.

### Staleness: store a checksum

Each translation entry stores a hash of the **English source text it was made from**:

```json
{ "key": ["spell", "Fireball", "PHB"],
  "source_hash": "sha256:1f3a…",
  "name": "Feuerball",
  "entries": ["Ein greller Lichtblitz … {@damage 8d6} Feuerschaden …"] }
```

On load, compare against the current English text. A mismatch does not invalidate the translation —
it **flags** it: the entry still renders, marked as "translated from an older version". Without
this, a corpus update silently leaves German text describing rules that changed, and nothing
anywhere would say so. Cheap to compute, and the difference between stale translations being
visible and being invisible.

An upstream **rename** orphans a translation, exactly as it orphans a `_copy` target. The orphan is
reported in the load report, never silently dropped, and the UI offers to re-point it.

> **As built (2026-11-02):** the corpus check (`/gm/corpus`, `corpus check`) reports them,
> `TranslationCoverage.audit`: stale, orphaned and lost records, chapters and books included,
> with the name the entry probably goes by now. `/gm/translations` lists the lost ones, its box
> holding that name, and **Move** puts the record there (`TranslationCoverage.move`): every
> passage as it is, so whatever differs from the new English shows as outdated or orphaned. A
> record whose entry still exists stays, and one already on the new entry is replaced only when
> the gamemaster ticks that. Nothing moves by itself.

> **Built 2026-10-21: per passage, not per field.** `app/services/translations.py`. A
> record's text is cut into **passages** — every string under its text fields
> (`entries`, `entriesHigherLevel`, `trait`, `action`, … and inside them `name`,
> `items`, `rows`, `caption`, …; a `statblock` node's name is a reference and never
> text) — each addressed by its path in the English record (`entries/0`,
> `entriesHigherLevel/0/entries/0`). The store keeps one entry per record:
>
> ```json
> library/i18n/de/spell.json
> {"Fireball|PHB": {"passages": {
>   "entries/0": {"text": "Ein greller Lichtstreif …", "en": "1f3a9c…", "draft": true,
>                 "by": "dm", "at": "2026-10-21T14:02:11+00:00"}}}}
> ```
>
> `en` is the `source_hash` of that passage alone, so one changed sentence upstream
> flags one passage, not the entry. Fallback is per passage, which makes a
> half-translated spell (its text German, its "At Higher Levels" English) normal.
> **Rulebook chapters** (`book/<id>.json`, keyed `<id>|<chapter index>`) and **published
> adventure sections** (`adventure/<id>.json`, keyed `<id>|<section id>`) also translate
> their own names; a translated heading keeps the English anchor search links to.
> The overlay is applied where text is drawn — reference pages and statblocks (the
> combat tracker's too), previews, rulebook chapters, adventure sections, and spells on
> the character sheet — and says what the reader sees: a draft, a translation of older
> English, partly translated, or not translated. Features on the sheet translate
> through their records (§4 note). **Creature descriptions** (2026-10-25): a monster's
> fluff, which players read in the tracker's Details, is stored as category
> `monsterFluff` under the monster's key (`library/i18n/de/monsterFluff.json`,
> `Goblin|MM`), passages addressed in its `entries`; editor at
> `/gm/translate/fluff/monster/<source>/<name>`. **The reference screens** (2026-10-31):
> `screen/dm.json` and `screen/quick.json`, keyed `<dm|quick>|<chapter index>`, the chapter's
> name stored as its passage though the corpus has it only in the contents; editor at
> `/gm/translate/screen/<dm|quick>/<n>`, one "Translate the text" per chapter, machine drafts
> and counts on `/gm/translations`. **All fluff** (2026-10-31): reference pages show it beneath the rules, with its picture,
> stored as `<category>Fluff` like a creature's; editor at `/gm/translate/fluff/<category>/…`.
> **In the wizard** (2026-11-02): what the books say about a species, subspecies, class,
> subclass or background reads translated on its card and in its folded panel, where the
> gamemaster finds "Translate the text", returning to the step. A subspecies's text is filed
> as a race, under the name 5eTools composes (`Dwarf (Hill)`). A subclass's is keyed with
> its class like the subclass itself (`Alchemist|TCE|Artificer|TCE`), as a name does not say
> whose subclass it is; editor at `/gm/translate/fluff/subclass/<source>/<name>/<class>/<class
> source>`. Only 21 of the corpus's 148 subclass fluff records carry text.

---

## 6. Units

Genuinely separate from language, and split by where the unit lives.

### Typed fields — convert freely

`speed: {"walk": 30}`, `range: {"distance": {"type": "feet", "amount": 60}}`. These are structured,
so conversion is a render-time formatting choice driven by a **per-user setting**, not a data
change. The DM can read feet while a player reads metres at the same table.

**Use the official D&D German conversion, `1 ft = 0.3 m`** — not `1/3.28`:

| Feet | Metres | | Feet | Metres |
| --- | --- | --- | --- | --- |
| 5 | 1.50 | | 60 | 18 |
| 10 | 3 | | 90 | 27 |
| 30 | 9 | | 120 | 36 |

The published German books round this way. Converting "correctly" at 3.28 produces 18.3 m where
every German player expects 18 m.

> **Built 2026-10-19.** `app/services/units.py`, `UnitFormat` via `Page.u`; the choice is
> saved on the account.
> **Changed 2026-10-25 by the user's decision:** the separate "Units" menu is gone; the
> language switch sets the units with it — English feet, any other language metres
> (`units_for_language`). The stored per-user field and the typed-fields-only rule stay.
> Converted: statblock speeds and senses, spell ranges and areas, weapon ranges, item weights, the sheet's, party cards' and
> wizard's speed, darkvision. 1 mile = 1.5 km and 1 lb = 0.5 kg, as the German books print
> them. `UnitFormat.text` converts only values Paravent formats from typed fields
> ("Self (15 ft cone)", a sense "darkvision 60 ft."), never entry prose.
> **Typed in, too** (2026-11-11): the creature builder's speeds and senses are shown and
> entered in the reader's units and kept in feet (`feet_from`, `text_in_feet`: back by the
> same 0.3, on the 5 ft. grid, so 9 m is 30 ft.). Senses are named in the reader's words
> ("Dunkelsicht 18 m", kept as "darkvision 60 ft."). What the builder writes itself (the
> attack button, a blank creature's slam and Multiattack: `creatures.text.*`) is in the
> creature's world's language and units, the reader's for a creature of no world
> (`Wording`). **The corpus's ready-made texts** (2026-11-12): the builder's 231 traits,
> 89 actions and 25 monster features translate like a book of their own
> (`library/i18n/<lang>/builder/creatures.json`, keyed `creatures|<trait|action|feature>|
> <name>`, the name a passage too), listed on `/gm/translations/builder` (linked from the
> overview's books and from the builder), in the editor at
> `/gm/translate/builder/<kind>/<name>`, and drafted by the machine as "The creature
> builder's texts". The builder's placeholders (`<$to_hit__str$>`, `<$short_name$>`) are
> held like tags: offered as chips, a lost one reported, a machine draft that changes one
> thrown away. A creature of a world in that language gets the translation, the name
> placeholders replaced by its name, distances converted; what is untranslated stays
> English. The picker shows the reader's names ("Biss (Cloaker)").

### Prose — leave it alone

**39,315 unit mentions sit in untagged prose** across the corpus — 21,058 "N feet", 9,979 "N ft.",
5,794 "N-foot", plus miles, pounds, inches and gallons. There is no machine-readable hook: `{@area}`
is a *map-area link*, not a distance.

Rewriting these by regex is where this goes wrong — "a 10-foot pole", possessives and spelled-out
numbers do not survive it. So:

- **Do not rewrite prose.** Optionally annotate unambiguous forms at render time (a parenthetical or
  hover showing the metric equivalent), which is reversible and never corrupts the source.
- **A translation carries whatever units its translator wrote.** If the German text says "6 m", that
  is simply the translated text; no conversion is applied to it. The unit setting governs *typed
  fields* and *annotations*, never translated prose.

---

## 7. The translation workflow

Translation is a **first-class feature of the tool**, not an offline import step. It runs in the UI.

1. **Pick a target.** Any reference entry has a "translate" action — from the drawer, from search,
   or from a coverage view listing what is untranslated, ranked by how often it is referenced (the
   backlink index gives this for free: translate what people actually hit).
2. **Edit side by side.** English on the left, translation on the right. Tags in the English text
   are shown as chips; the editor inserts the same tag into the translation rather than making the
   translator type `{@...}` by hand. This is what keeps §3's "never regex-generate tags" honest.
3. **Glossary-assisted.** Known terms are proposed inline from §4, so "charmed" consistently becomes
   "bezaubert" across every entry, and a glossary change surfaces everywhere it was used.
4. **Save** writes one entry to `library/i18n/<lang>/<category>.json` with its `source_hash`.
5. **Machine assistance is allowed, but never silent.** An external translation service may prefill
   the target field; the result is stored as `status: "draft"` until a human confirms it, and draft
   entries render with a marker. Auto-translated text that nobody has read must never be
   indistinguishable from a reviewed translation.

> **Built 2026-10-21.** The editor at `/gm/translate/ref/<category>/<source>/<name>`,
> `/gm/translate/book/<id>/<n>` and `/gm/translate/adventure/<id>/<section>`, linked
> as "Translate the text" from each page (gamemaster only): every passage in a row,
> the English rendered beside a field, the English passage's tags as chips that insert
> themselves at the cursor, "only open passages" for long chapters. A saved change is
> reviewed; a draft is confirmed by its "reviewed" tick or "Confirm all drafts" — text
> merely sent back unchanged is never taken as a review. A passage that lost a tag of
> the English says so ("1 tag missing"), but is kept: it may be deliberate.
>
> **Leaving the editor** (2026-11-01): it remembers the page it was opened from
> (`back`), through its language and filter links too, the overview included, and "Save
> and return" goes there; a record with no page of its own returns to the overview. Saving
> and switching view swap the page in place (htmx), so they add nothing to the browser's
> history and one Back leaves; without JavaScript each is a page, as before.
>
> **Machine drafts** (`app/services/machine_translation.py`) through the Claude API,
> on only when `PARAVENT_TRANSLATE_API_KEY` is set (model `PARAVENT_TRANSLATE_MODEL`,
> default Claude Opus 5.5 at low effort). Untranslated passages go in batches (40
> passages / ~12,000 characters, four at a time) with the glossary terms each batch
> mentions; the answer is held to a JSON schema; a passage whose tags differ in any way
> from the English is thrown away and stays English. A declined batch is re-run on
> Anthropic's recommended fallback model; a failed one leaves its passages untouched.
> From the editor ("Machine-translate the rest") or, for many records, from
> `/gm/translations` (settings gear): the most-linked untranslated entries, a whole
> rulebook or a whole adventure, run in the background with progress shown. That page
> also counts what each category and book covers, ranks the untranslated by how often
> the corpus links to them, and lists outdated and orphaned translations.
>
> Setting up the key, choosing a model, and what it costs: README.md, *Machine
> translation (optional)*.
>
> **Drafts to review** (2026-11-03, `/gm/translations/drafts`, linked from the overview while
> any wait): every record holding drafts nobody has confirmed, by category, with how many
> and who drafted them when. Each opens its editor at the passages still to read and returns
> to the list; a record whose entry is gone stays under *Translations of what is gone*.
> A button there confirms every draft listed at once, unread, at the gamemaster's word.
> **Partly translated** (2026-11-03, `/gm/translations/unfinished`, linked from the overview
> while any wait): every record with passages still English — most often ones the machine gave
> back with a tag changed, which are never stored — by category, with how many are open. Each
> opens its editor at those passages; one button (and "Partly translated, the rest again" in
> the machine menu) runs the machine over all of them once more, in the background.
> **What players read** (2026-11-03, the machine menu's `players` scope): every record a
> player of any world reads (documentation/PLAYER-VIEW.md) that still has English passages —
> the player kinds in the player books, class and subclass features, and what the books say
> about species, classes, subclasses and backgrounds as the wizard shows it — in one run.
> Names stay with the glossary (a PHB deity has no text, only a name).
> **Confirm all drafts** saves, confirms and leaves, as "Save and return" does (in the
> tracker's popup it closes it).
>
> **From the combat tracker** (2026-10-25): a creature the gamemaster's language does not
> name yet, or whose text has open passages, shows **Translate** above its statblock
> with what is missing ("name missing · 6 passages open"). It opens the editor as a
> popup over the tracker (`?embed=1`): the name for the glossary, every passage with
> its tag chips, machine drafts, "confirm all drafts", and a link to the full editor.
> Saving closes the popup and reloads the creature and the board, which then show the
> new name and text; drafts and confirmations keep the popup open and reload beneath it.
> The popup's full editor opens in the same tab and returns to the tracker.
> Beside it, **Description** opens the same popup for the books' description of the
> creature — what the players read in Details — while passages of it are open.

### Importing a translated corpus

If a full 5eTools-format corpus exists in another language, it imports directly: same schema, same
tags, matched on `(category, name, source)`. That is the easy path, and the format Paravent should
ask for.

> **Built 2026-11-11.** `python -m app.cli translations import <folder> <lang>`
> (`app/services/corpus_import.py`), with `--dry-run`, `--reviewed` and `--replace`. The
> folder is laid out as 5eTools (a whole release's `data/` is found as for the English).
> Read: every compendium category by its own files and key, descriptions (`fluff-*.json`,
> stored as `<category>Fluff`), rulebook chapters (`book/book-<id>.json`, by position) and
> published adventures' sections (`adventure/adventure-<id>.json`, by section id), the last
> two only when the file has as many sections as the English — else the book is named and
> left out. A record is matched by `translation_key` with the English name taken from
> `ENG_name` (and `ENG_className`, `ENG_subclassShortName`) where the corpus translates
> names, as translated 5eTools corpora do. Per passage, by the editor's paths: taken where
> the text differs from the English and **its links point at the same entries** (the tag
> without its display slot, formatting tags such as `{@b}` aside), so a translator's
> `{@spell fireball||Feuerball}` passes and a changed target stays English, counted. Taken
> passages are **drafts** by `import` (confirmed on *Drafts to review*), or reviewed with
> `--reviewed`; a passage the table translated already is kept unless `--replace`. A
> translated entry name goes into the glossary where the entry has no name yet (any name
> with `--replace`). Written per file in one go (`TranslationStore.save_many`); a dry run
> says the same counts, the table's own passages among the kept. Nothing is written
> under either corpus.
>
> **From the app** (2026-11-11): *Import a translated corpus* on `/gm/translations` takes a
> folder on the server (under Docker, one mounted into the container) and the same two
> choices. **Check first** runs the dry run, **Import** writes; either runs in the
> background off the event loop (`CorpusImports`, one at a time), the panel naming its
> phase and then the table per kind, and after a check **Import now** with the same
> settings. It refuses a folder that is not one, holds no 5eTools corpus, or is (or lies
> in, or holds) the English corpus or the library. Anything *not* in 5eTools format is out of scope — converting a foreign schema costs more
than it returns and reintroduces every problem this design avoids.

---

## 8. Build order

1. **UI strings** — independent of everything else, ships first.
2. **Glossary** (§4) + label substitution in the tag renderer (§3).
3. **Translation storage + fallback resolution** (§5), including `source_hash`.
4. **The translation UI** (§7) — read-only coverage view first, then the editor.
5. **Unit preference** for typed fields (§6).
6. **Prose unit annotation** — last, optional, and only if it proves wanted.

Steps 1 and 2 together already deliver a tool that feels German. Everything after that is
incremental coverage, which is the point: **no step blocks on a complete translation existing.**

---

## 9. What this design deliberately rejects

| Rejected | Why |
| --- | --- |
| German entries as *replacement* records | Loses every cross-reference; mechanics could drift from English |
| Translating into a different schema (VTT formats) | Costs more than it returns; no tags, no join key |
| Auto-matching translations by structural fingerprint | Tested on real data: only ~11% unique matches — not viable unattended |
| Regex-tagging translated prose | Silently mis-tags non-technical uses of glossary words |
| Tying units to language | A German table may play in feet; the two are independent settings |
| Rewriting the 39,315 prose unit mentions | Not safely reversible; breaks idioms like "a 10-foot pole" |
