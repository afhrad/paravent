# 5eTools Tag Catalogue

Complete inventory of every `{@...}` tag in the corpus, with the Lucide icon each renders.
Machine-readable companion: [`5etools_tags.json`](5etools_tags.json). Regenerate with
[`scan_tags.py`](scan_tags.py).

The examples in the JSON are short on purpose, at most eight words: they show how a tag is
written, not what the books say. This catalogue is public, so no sentence of a rulebook
belongs in it; the examples of free-text tags (`{@note …}`, `{@comic …}`) are our own words.

## Scan result

| | |
| --- | --- |
| Source | `5etools-data/` |
| Files scanned | **499** JSON files |
| Bytes scanned | **109,857,630** |
| Distinct tags | **110** |
| Total occurrences | **249,546** |

Distribution is steeply Pareto — **8 tags cover 65%** of all occurrences. Implement those first;
they carry almost all the visible value.

| Category | Occurrences | Share | Handler |
| --- | ---: | ---: | --- |
| Entity reference | 143,053 | 57.3% | Opens the reference drawer |
| Rollable | 49,230 | 19.7% | Produces a number |
| Navigation | 28,967 | 11.6% | Jumps to book/adventure/area |
| Inline formatting | 14,858 | 6.0% | Emphasis only, no icon |
| Statblock shorthand | 12,232 | 4.9% | Fixed text expansion |
| Comic layout | 674 | 0.3% | Adventure-specific blocks |
| External / asset | 514 | 0.2% | Links, images, audio |

## Payload grammar

Every tag is `{@name payload}`, payload split on `|`:

```
{@tag name}                      → resolve "name", display "name"
{@tag name|source}               → disambiguate by source book
{@tag name|source|display}       → override the visible text
{@creature troll||trolls}        → empty source, plural display  ← very common
```

**Tags nest.** `{@footnote text|{@note inner}}` and `{@comic {@5etoolsImg ...}}` both occur.
The parser must be recursive, not a flat regex.

Slot 3 is a display override used heavily for pluralization and inflection — `{@creature troll||trolls}`.
**Always render slot 3 when present**, or prose reads wrong.

---

## 1. Entity references — 143,053 uses (57.3%)

Dashed underline + category icon. Click opens the reference drawer; the tracker never re-renders.

| Tag | Uses | Icon | Renders / behaviour |
| --- | ---: | --- | --- |
| `{@creature}` | 54,753 | `skull` | Bestiary entry |
| `{@spell}` | 25,118 | `sparkles` | Spell |
| `{@item}` | 19,666 | `gem` | Item; magic items same glyph in violet |
| `{@condition}` | 11,634 | `footprints` | Condition rule |
| `{@skill}` | 10,756 | `target` | Skill check |
| `{@variantrule}` | 5,758 | `book-open` | Rules gloss |
| `{@action}` | 1,990 | `zap` | Combat action |
| `{@filter}` | 1,502 | `filter` | Pre-filtered compendium list |
| `{@quickref}` | 1,459 | `book-open` | Quick reference rule |
| `{@deity}` | 1,138 | `landmark` | Deity |
| `{@sense}` | 1,057 | `eye` | Darkvision, truesight, tremorsense |
| `{@class}` | 1,039 | `shield` | Class / subclass link |
| `{@background}` | 970 | `scroll` | Background |
| `{@status}` | 897 | `hourglass` | Status e.g. concentration |
| `{@feat}` | 845 | `star` | Feat |
| `{@table}` | 838 | `table` | Random / reference table |
| `{@card}` | 609 | `layers` | Deck card e.g. Tarokka |
| `{@race}` | 489 | `users-round` | Species / race |
| `{@vehicle}` | 309 | `ship` | Vehicle |
| `{@reward}` | 276 | `award` | Boon / charm / blessing |
| `{@itemProperty}` | 273 | `tag` | Weapon/armor property e.g. finesse |
| `{@language}` | 236 | `languages` | Language |
| `{@hazard}` | 224 | `triangle-alert` | Hazard |
| `{@optfeature}` | 172 | `star` | Optional feature / invocation |
| `{@recipe}` | 170 | `utensils-crossed` | Recipe |
| `{@classFeature}` | 147 | `star` | Class feature |
| `{@deck}` | 135 | `layers` | Card deck |
| `{@itemMastery}` | 108 | `swords` | Weapon mastery property |
| `{@facility}` | 87 | `house` | Bastion facility |
| `{@disease}` | 85 | `biohazard` | Disease |
| `{@subclassFeature}` | 69 | `star` | Subclass feature |
| `{@object}` | 57 | `shapes` | Interactable object |
| `{@trap}` | 54 | `triangle-alert` | Trap |
| `{@cult}` | 36 | `landmark` | Cult |
| `{@vehupgrade}` | 35 | `ship` | Vehicle upgrade |
| `{@charoption}` | 28 | `circle-user` | Character option |
| `{@subclass}` | 14 | `shield` | Subclass |
| `{@boon}` | 11 | `award` | Demonic boon |
| `{@legroup}` | 3 | `crown` | Legendary group |
| `{@psionic}` | 3 | `brain-circuit` | Psionic discipline |
| `{@creatureFluff}` | 2 | `skull` | Creature lore variant |
| `{@legendaryGroup}` | 1 | `crown` | Legendary group (alt spelling) |

---

## 2. Rollable — 49,230 uses (19.7%)

Boxed monospace chip. Click rolls immediately and writes to the log, honouring the current
advantage state.

| Tag | Uses | Icon | Renders / behaviour |
| --- | ---: | --- | --- |
| `{@dc}` | 15,462 | `shield-check` | Fixed save DC |
| `{@dice}` | 14,254 | `dices` | Plain dice expression |
| `{@damage}` | 11,645 | `dices` | Damage roll; icon swaps to damage type when known |
| `{@hit}` | 6,212 | `target` | Attack bonus, rolls 1d20+N |
| `{@recharge}` | 923 | `refresh-cw` | Recharge N-6 |
| `{@chance}` | 413 | `percent` | Percentage chance |
| `{@scaledamage}` | 180 | `dices` | Damage scaled by level/slot |
| `{@hitYourSpellAttack}` | 64 | `target` | Resolves to caster's spell attack mod |
| `{@skillCheck}` | 28 | `target` | Skill check with bonus |
| `{@scaledice}` | 17 | `dices` | Dice scaled by level/slot |
| `{@ability}` | 11 | `dumbbell` | Ability score + modifier |
| `{@d20}` | 10 | `dices` | Explicit d20 roll |
| `{@coinflip}` | 5 | `circle-dot` | Coin flip |
| `{@dcYourSpellSave}` | 3 | `shield-check` | Resolves to caster's spell save DC |
| `{@savingThrow}` | 2 | `shield-check` | Saving throw with bonus |
| `{@autodice}` | 1 | `dices` | Auto-rolled expression |

`{@damage}` takes the damage-type glyph when the type is known from context — `flame` for fire,
`snowflake` for cold, `zap` for lightning — otherwise `dices`.

---

## 3. Navigation — 28,967 uses (11.6%)

| Tag | Uses | Icon | Renders / behaviour |
| --- | ---: | --- | --- |
| `{@area}` | 13,494 | `map-pin` | Adventure map area cross-ref |
| `{@book}` | 9,264 | `book-open` | Source book chapter |
| `{@adventure}` | 6,209 | `map` | Adventure chapter |

`{@area}` (13,494 uses) is adventure-internal: it points at a keyed map location such as *area 3ec*.
It resolves **within the current adventure only** — never globally.

---

## 4. Statblock shorthand — 12,232 uses (4.9%)

No payload, no icon: these *are* the label. They expand to fixed text and must be bold-run styled
exactly as a statblock expects.

| Tag | Uses | Expands to |
| --- | ---: | --- |
| `{@h}` | 5,468 | *Hit:* |
| `{@atk rw}` | 4,623 | *Ranged Weapon Attack:* (`mw`/`rw`/`ms`/`rs`) |
| `{@atkr m}` | 857 | 2024-edition attack range shorthand |
| `{@actSaveFail}` | 443 | *Failure:* |
| `{@actSave dex}` | 425 | *Dexterity Saving Throw:* |
| `{@actSaveSuccess}` | 203 | *Success:* |
| `{@actSaveSuccessOrFail}` | 60 | *Failure or Success:* |
| `{@actTrigger}` | 59 | *Trigger:* |
| `{@actResponse}` | 59 | *Response:* |
| `{@hom}` | 32 | *Hit or Miss:* |
| `{@m}` | 2 | *Miss:* |
| `{@actSaveFailBy 5}` | 1 | *Failure by 5 or more:* |

`{@h}` alone is 5,468 uses. Missing it breaks every attack line in the bestiary.

---

## 5. Inline formatting — 14,858 uses (6.0%)

**Never carries an icon.** Prose is read, not scanned.

| Tag | Uses | Icon | Renders / behaviour |
| --- | ---: | --- | --- |
| `{@i}` | 7,478 | — | italic |
| `{@b}` | 6,659 | — | bold |
| `{@note}` | 306 | `info` | Aside / callout box |
| `{@color}` | 104 | — | explicit hex — MUST be remapped to a token |
| `{@italic}` | 60 | — | italic (long form) |
| `{@bold}` | 59 | — | bold (long form) |
| `{@unit}` | 45 | — | Pluralizing unit, e.g. egg/eggs |
| `{@footnote}` | 36 | `asterisk` | Footnote marker + body |
| `{@s}` | 31 | — | strike |
| `{@style}` | 25 | — | named style e.g. small-caps |
| `{@font}` | 11 | — | custom @font-face, e.g. Draconic script |
| `{@highlight}` | 11 | — | highlighted run |
| `{@u}` | 7 | — | underline |
| `{@tip}` | 6 | `info` | Hover tooltip |
| `{@kbd}` | 6 | — | keyboard key |
| `{@homebrew}` | 3 | `flask-conical` | Homebrew delta vs official |
| `{@sup}` | 2 | — | superscript |
| `{@code}` | 2 | — | monospace |
| `{@underline}` | 1 | — | underline (long form) |
| `{@underlineDouble}` | 1 | — | double underline |
| `{@u2}` | 1 | — | underline shorthand |
| `{@strike}` | 1 | — | strike (long form) |
| `{@strikeDouble}` | 1 | — | double strike |
| `{@s2}` | 1 | — | strike shorthand |
| `{@sub}` | 1 | — | subscript |

Two need care:

- **`{@color text|#ff0000}`** (104 uses) hardcodes a hex value. **Remap to the nearest semantic
  token** — raw hex breaks dark mode, which is exactly the bug documentation/DESIGN.md §2 forbids.
- **`{@font TEXT|path.woff2}`** (11 uses) loads a script face such as Iokharik for Draconic.
  Self-host the woff2; never fetch from a CDN.

---

## 6. External and assets — 514 uses

| Tag | Uses | Icon | Renders / behaviour |
| --- | ---: | --- | --- |
| `{@5etools}` | 211 | `external-link` | Link into 5etools UI — rewrite to our own route |
| `{@link}` | 176 | `external-link` | Arbitrary external URL |
| `{@loader}` | 2 | `download` | Homebrew loader link — disable |
| `{@5etoolsImg}` | 123 | `image` | Image/PDF asset |
| `{@5etoolsAudio}` | 2 | `volume-2` | Audio asset |

`{@5etools ...|items.html}` links into the *5etools web UI*, which we do not run. **Rewrite these
to our own routes**, or render as plain text — never as a dead link.

---

## 7. Comic layout — 674 uses

| Tag | Uses | Icon | Renders / behaviour |
| --- | ---: | --- | --- |
| `{@comic}` | 448 | — | Comic panel |
| `{@comicH3}` | 70 | — | Comic heading 3 |
| `{@comicH1}` | 62 | — | Comic heading 1 |
| `{@comicNote}` | 59 | — | Comic note |
| `{@comicH4}` | 31 | — | Comic heading 4 |
| `{@comicH2}` | 4 | — | Comic heading 2 |
| `{@crochet}` | 18 | — | Crochet pattern (one book) |

Confined to a few adventures. Render as styled blocks; safe to defer.

---

## Implementation order

| Phase | Tags | Cumulative coverage |
| --- | --- | --- |
| 1 | `creature` `spell` `item` `dc` `dice` `damage` `condition` `skill` | **65.4%** |
| 2 | `+ book` `area` `hit` `adventure` `variantrule` `h` `atk` `i` `b` | **90.2%** |
| 3 | remaining entity + roll tags | ~98% |
| 4 | comic, `font`, `crochet`, `loader` | 100% |

An unresolvable tag — a reference into a book the user never imported — renders as **plain text
with a dotted grey underline and a tooltip**. Never a dead link, never a raw brace. The tool is
data-neutral; a missing source is normal, not an error.

---

## Note on the German corpus

There is currently no German tag corpus to scan.

> **Superseded 2026-09-14.** An earlier version of this section treated that as a blocker, and
> pointed at Foundry VTT files as the only candidate source. Both points are obsolete: VTT formats
> are out of scope, and cross-language resolution no longer waits on a translation existing. A
> translation is a **text-only overlay** whose tags resolve through the English keyspace, so
> cross-referencing works at 0% and at 100% coverage alike. See
> [`../../documentation/TRANSLATION.md`](../../documentation/TRANSLATION.md).
