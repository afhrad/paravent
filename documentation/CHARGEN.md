# Character Generation in Paravent — Data Analysis

**Scope:** what a character-creation wizard needs, what the imported 5eTools corpus at
`../GameMaster Tool/5etools-data` actually provides, and where the gaps are.

**Date:** 2026-09-14 · **Corpus:** 499 JSON files, both rule editions (`PHB` "classic" 2014 and
`XPHB` "one" 2024) side by side.

> **The UX that consumes this analysis is specified in [CHARGEN-WIZARD.md](CHARGEN-WIZARD.md)**,
> with a visual reference at `design-reference/13-chargen-wizard.html`. This document answers
> *what the data supports*; that one answers *what the player sees*.

> **Language scope:** this analysis covers the **English corpus only**. Paravent remains
> multilingual per PROJECT.md, but character generation is specified, built and validated against
> English data first. Localisation of the wizard is deliberately out of scope here and is not
> treated as a gap below.

---

## 1. Reference: how the established tools do it

Surveyed against D&D Beyond (the reference implementation), Fight Club 5e, Roll20's builder and
Dungeon Master's Vault. Two flow families exist:

| Flow | Tools | Order |
| --- | --- | --- |
| **Class-first** | D&D Beyond (2024 rules) | Class → Species → Background → Abilities → Equipment |
| **Race-first** | Roll20, Fight Club, most 2014-era tools | Race → Class → Abilities → Background → Equipment |

The 2024 PHB restructured creation so that the **background** carries the ability score increases
and a starting feat, while the species carries none. This is not cosmetic — it inverts the
dependency order of the wizard. Since the corpus carries **both** editions, Paravent's wizard must
branch on `edition` (`"classic"` vs `"one"`) rather than pick one order.

The invariant feature set across all four tools:

- Step-by-step wizard with **back-navigation that preserves state** (already on PROGRESS.md as
  "wizard elements with state persistence")
- Live derived-value recomputation (AC, HP, initiative, spell save DC, passive Perception)
- **Choice validation** — a prerequisite that is not met blocks the option rather than warning later
- Filterable pickers for spells/equipment, backed by the compendium
- Level-up as the same engine replayed, not a separate code path

---

## 2. The ten steps, mapped to the dataset

### Step 0 — Ruleset & sources

The user picks edition and which books are legal. Every record carries `source`, and reprints are
linked both ways (`reprintedAs`, `otherSources`). Without this gate the wizard shows the user a
Fighter twice.

> **Available.** `source`, `edition`, `srd`/`srd52`, `basicRules`/`basicRules2024`,
> `reprintedAs` on every entity. `books.json` supplies the source names.

> **Already solved elsewhere — do not rebuild it here.** This *is* the source set the world
> declares (TECHSTACK.md §6c), and it is the same mechanism the tag renderer uses to resolve
> references. The wizard should **read** the active set, not ask again. Measured against the real
> corpus, a declared set plus `reprintedAs` supersession resolves 100% of ambiguous names, so
> "shows the user a Fighter twice" does not happen once the set is in place.
>
> It also means **edition is not a separate question.** `XPHB` in the set means 2024; `PHB` means
> 2014. Read it off the declaration rather than asking the user twice for the same fact.

### Step 1 — Ability scores

Four methods: standard array, point buy, roll 4d6-drop-lowest, manual.

> **Partially available.** The point-buy cost table exists in `variantrules.json` →
> *"Customizing Ability Scores"* as a **rendered table inside a prose entry**, not as a lookup map:
>
> ```json
> {"type": "table", "caption": "Ability Score Point Cost",
>  "colLabels": ["Score", "Cost"], "rows": [[8,0],[9,1],[10,2],[11,3],[12,4],[13,5],[14,7],[15,9]]}
> ```
>
> Machine-readable if you parse it, but 5eTools' own builder hardcodes this. **The standard array
> `[15,14,13,12,10,8]` appears nowhere as data.** So does the 27-point budget — it is a number in a
> sentence. See gap **G1**.

### Step 2 — Species / Race

> **Fully available.** `races.json` — 157 races + 98 subraces. Mechanically typed fields:
> `ability` (2014 ASIs), `size`, `speed`, `darkvision`, `resist`/`immune`/`vulnerable`,
> `languageProficiencies`, `skillProficiencies`, `toolProficiencies`, `weaponProficiencies`,
> `armorProficiencies`, `creatureTypes`, `traitTags`, `age`, `heightAndWeight`, `feats`,
> `additionalSpells`, `lineage`.
>
> Choices are structured, not prose:
> ```json
> "toolProficiencies": [{"choose": {"from": ["smith's tools","brewer's supplies","mason's tools"]}}]
> ```
> `additionalSpells` handles innate casting with level gating and a spellcasting-ability choice —
> the 2024 Elf's Drow/High/Wood options are three named entries in one race record.
>
> ⚠️ 25% of race/subrace records use `_copy` inheritance. **Already solved** — Paravent's
> `_copy` resolver handles all twelve `_mod` modes (PROGRESS.md).

### Step 3 — Class

> **Fully available.** `class/class-*.json`, 15 classes (incl. Artificer, Mystic, Sidekick).
> Per class: `hd`, `proficiency` (saving throws), `startingProficiencies`,
> `startingEquipment`, `multiclassing`, `classFeatures`, `classTableGroups`,
> `optionalfeatureProgression`, `primaryAbility` (2024 only).
>
> Skill choices are structured:
> ```json
> "skills": [{"choose": {"from": ["acrobatics","athletics",...], "count": 2}}]
> ```

### Step 4 — Subclass

> **Fully available.** In the same file as its class — Fighter ships 25 subclasses, 129 subclass
> features. `subclassFeatures` are reference strings (`"Knowledge Domain|Cleric||Knowledge||1"`)
> resolved against the `subclassFeature` array. `additionalSpells.prepared` gives domain/oath/circle
> spells keyed by character level.
>
> ⚠️ The **level at which a subclass is chosen** is not a scalar field. It is encoded as a flag
> inside the feature list:
> ```json
> {"classFeature": "Martial Archetype|Fighter||3", "gainSubclassFeature": true}
> ```
> Derivable, but the wizard must scan `classFeatures` for that flag rather than read a property.

### Step 5 — Background

> **Fully available, and edition-divergent.** `backgrounds.json`, 157 entries.
>
> 2014 (`PHB`): `skillProficiencies`, `languageProficiencies`, `toolProficiencies`,
> `startingEquipment`. Personality traits/ideals/bonds/flaws live in `entries` as rollable tables.
>
> 2024 (`XPHB`): additionally carries `ability` — the ASI moved here — with a **weighted** choice
> encoding the +2/+1 vs +1/+1/+1 split:
> ```json
> "ability": [{"choose": {"weighted": {"from": ["int","wis","cha"], "weights": [2,1]}}},
>             {"choose": {"weighted": {"from": ["int","wis","cha"], "weights": [1,1,1]}}}]
> ```
> …plus `feats` (`{"magic initiate; cleric|xphb": true}`) for the 2024 origin feat.
>
> This is why Step 1 cannot be finalised before Step 5 under 2024 rules.

### Step 6 — Ability score assignment & racial/background increases

Base scores from Step 1 combine with `race.ability` (2014) or `background.ability` (2024).

> **Available**, given Steps 2/5. The `weighted` choose form needs its own resolver in the wizard —
> it is a *distribution to assign*, not a set to pick from.

### Step 7 — Proficiencies, languages, feats

> **Fully available.** `skills.json` (36) maps each skill to its ability — essential for deriving
> modifiers. `languages.json` (186 + 6 scripts), `senses.json` (8), `feats.json` (265 with
> structured `prerequisite`, `ability`, `expertise`, `repeatable`).
>
> Feat prerequisites are machine-checkable:
> ```json
> "prerequisite": [{"ability": [{"str": 13}]}]
> ```
> Prerequisites also exist in `race`, `level`, `spellcasting`, `proficiency` and `other` forms —
> the last being free prose (see **G4**).
>
> `optionalfeatures.json` (213) covers everything the class features *point to*: Fighting Styles
> (`FS:F`/`FS:R`/`FS:P`/`FS:B`, 27), Eldritch Invocations (`EI`, 82), Battle Master Maneuvers
> (`MV:B`, 43), Metamagic (`MM`, 20), Elemental Disciplines (`ED`, 17), Artificer Infusions
> (`AI`, 16), Runes, Arcane Shots, Pact Boons. The `featureType` tag is what joins them to the
> class's `optionalfeatureProgression`.

### Step 8 — Spells

> **Fully available, and unusually well-modelled.**
>
> *Progression* (on the class record): `spellcastingAbility`, `casterProgression`
> (`full`/`half`/`artificer`/`pact`), `cantripProgression`, `spellsKnownProgressionFixed`,
> and `preparedSpells` as an **evaluable formula string**: `"<$level$> + <$int_mod$>"`.
> `classTableGroups[].rowsSpellProgression` gives the full slot table per level.
>
> *Spell list membership* — the critical join — is **not** on the spell. It lives in
> `generated/gendata-spell-source-lookup.json`, keyed by spell name, and correctly models that
> membership is *source-dependent*:
> ```json
> "air bubble": {"class": {"PHB": {"Druid": true, "Ranger": true, ...},
>                          "TCE": {"Artificer": true}},
>                "subclass": {"PHB": {"Fighter": {"PHB": {"Eldritch Knight": {...}}}}}}
> ```
> Any builder that instead filters spells by a `classes` field on the spell will get 2014/2024
> lists wrong. **Use this lookup file.**
>
> *Spells* themselves (`spells/spells-*.json`, 17 files) carry fully typed `level`, `school`,
> `time`, `range`, `components`, `duration`, `savingThrow`, `spellAttack`, `damageInflict`,
> `conditionInflict`, `areaTags`, `scalingLevelDice`, `miscTags` (concentration, ritual).

### Step 9 — Equipment

> **Fully available.** `items-base.json` (230 base items, 26 properties, 67 types, 8 masteries)
> and `items.json` (2.6 MB) plus `magicvariants.json`.
>
> Armour and weapons are mechanically typed — nothing needs parsing from prose:
> ```json
> {"name": "Chain Mail", "type": "HA", "ac": 16, "strength": "13", "stealth": true, "weight": 55, "value": 7500}
> {"name": "Longsword", "type": "M", "weaponCategory": "martial", "property": ["V"],
>  "dmg1": "1d8", "dmgType": "S", "dmg2": "1d10"}
> ```
> (`value` is in copper pieces.)
>
> Starting equipment has a **dual encoding** — prose in `default`/`entries` for display, and
> `defaultData` for the machine:
> ```json
> "defaultData": [{"a": ["chain mail|phb"],
>                  "b": ["leather armor|phb","longbow|phb","arrows (20)|phb"]},
>                 {"a": [{"equipmentType": "weaponMartial"}, "shield|phb"],
>                  "b": [{"equipmentType": "weaponMartial", "quantity": 2}]}]
> ```
> `{"equipmentType": "weaponMartial"}` is a *deferred picker* — the wizard must resolve it into a
> filtered item list. `{"value": 15500}` entries are gold alternatives in copper.
> `goldAlternative` ("`{@dice 5d4 × 10}`") covers the buy-your-own path.
>
> ⚠️ Note the 2014/2024 key casing difference: lowercase `a`/`b` vs uppercase `A`/`B`/`C`. A
> parser matching only one will silently drop options.

### Step 10 — Identity & derived sheet

> **Mostly available.** `names.json` — d100 name tables for 10 ancestries, split by gender/child.
> `life.json` — `lifeClass`, `lifeBackground`, 100 `lifeTrinket` entries (the XGE "This Is Your
> Life" tables). `deities.json` (768 KB) for clerics/paladins.
> `heightAndWeight` on races gives base + modifier dice for rolled physique.
>
> Derived values (AC, HP, initiative, spell save DC, passive scores) are **computed, not stored** —
> correctly so. But the formulas are prose. See **G2**.

---

## 3. Gaps

### G1 — Ability-score generation constants are not data ⚠️ *minor, blocking Step 1*

Standard array `[15,14,13,12,10,8]` exists nowhere. The 27-point budget is prose. The point-buy
cost table is a *rendered table* inside a prose entry.

**Fixed 2026-10-09:** `data/rules/abilityscoregen.json`. The cost table was read out of
`variantrules.json`'s rendered table rather than retyped; the standard array and the 27-point
budget are prose upstream, so they are hand-authored with their provenance recorded in `_meta`.
A test checks the canonical maximum spend (15/15/15/8/8/8 = 27) so a wrong constant fails loudly.

### G2 — Derived-statistic formulas are prose ⚠️ *by design, but must be implemented*

AC calculation (unarmoured, armoured, Unarmoured Defense variants), HP at level 1 vs level-up,
proficiency bonus by level, spell save DC, encumbrance thresholds, carrying capacity — all exist
only as English sentences in `variantrules.json` and class features.

**Fixed 2026-10-15:** `app/services/character.py`. Proficiency bonus, HP, AC (including both
Unarmoured Defense variants — Barbarian adds CON and may use a shield, Monk adds WIS and may
not), saves, skills, passive Perception, spell save DC and attack bonus. Every value carries its
derivation so the UI can explain it.

Hit dice, the skill→ability map and armour AC/type/stealth are **read from the corpus**; only the
formulas themselves are hardcoded, because only they are prose. The `preparedSpells` formula is
evaluated by a restricted grammar (tokens plus `+`/`-` on integers) rather than `eval` — an
unreadable formula returns None instead of a wrong spell count.

Still outstanding: the **multiclass spell-slot table** (G3), genuinely prose-only, deferred until
multiclassing is a feature.

### G3 — Multiclassing prerequisites are duplicated, one copy prose ⚠️ *minor*

`variantrules.json` → *"Multiclassing"* holds the prerequisite table as prose rows
(`["{@class Barbarian}", "Strength 13"]`). But **each class record carries the same rule
structured**:
```json
"multiclassing": {"requirements": {"or": [{"str": 13, "dex": 13}]},
                  "proficienciesGained": {"armor": ["light","medium","shield"], ...}}
```
**Fix:** read `class.multiclassing`, ignore the prose table. Not a real gap — a trap.

> ⚠️ **Corrected 2026-10-18 when building it: the 2024 classes carry no `requirements`.**
> Their `multiclassing` holds only `proficienciesGained` (Monk, Sorcerer and Wizard have
> no `multiclassing` at all). The 2024 rule — 13 in the primary ability of each class —
> is read from `primaryAbility`: a list of alternatives, each needing all of its
> abilities (`[{"str": true}, {"dex": true}]` is STR *or* DEX; `[{"dex": true, "wis":
> true}]` is both). And the 2014 `or` form reads the other way round: inside one `or`
> entry, any one key suffices (`{"or": [{"str": 13, "dex": 13}]}` is STR 13 *or* DEX 13).
> Both shapes are one reader in `app/services/chargen/rules.py`, `multiclass_requirement`.
The *spell-slot* multiclass table, however, is genuinely prose-only → hardcode it alongside G2.

### G4 — `prerequisite.other` is free text ⚠️ *minor*

Most prerequisites are typed. A minority use `{"other": "..."}` with human-readable conditions
that cannot be auto-validated.

**Fix:** display them as a confirmation checkbox the user ticks. D&D Beyond does the same.

### G5 — Subclass-selection level requires inference ⚠️ *cosmetic*

No `subclassLevel` scalar; must be found via `gainSubclassFeature: true` in `classFeatures`.
**Fix:** compute once at load time, cache on the class model.

### G6 — No character/PC model yet ✅ *resolved 2026-10-09*

`app/models/pc.py` holds `PlayerCharacter` plus `Abilities`, `ChosenOption`, `ClassLevel` and
`SpellSelection`, persisted as one file per character in `library/characters/`
(documentation/CHARACTER-FORMAT.md) —
library data, as it must be.

It stores **chosen options, not resolved state**. A choice is kept as the reference the player
picked (`"Wizard|PHB"`), and anything derivable is recomputed. That means a corpus update or a
change to the world's declared books **re-derives** the character instead of leaving a stale
number behind, and back-navigation in the wizard is just reading an earlier field. A test
asserts no derived field (`hp`, `ac`, `spell_save_dc`, …) exists on the model at all.

### G7 — The class data is not loaded yet ✅ *resolved 2026-10-09*

Everything in section 2 above assumes `class`, `subclass`, `classFeature` and `subclassFeature`
are queryable. They now are. Corpus Phase 1 loaded all five class categories (30 / 315 / 677 /
1,397 / 171), both `generated/` lookups, and all 5,984 fluff records — 0 validation failures, 0
unresolved copies.

Three things the wizard can now rely on:

* `ClassEntry.subclass_level` infers the choice level from `gainSubclassFeature` (G5 solved):
  Cleric/Sorcerer/Warlock 1, Druid/Wizard 2, Fighter/Rogue 3.
* `LookupTables.spell_sources` is the correct spell-list join, and reads **both** `class` and
  `classVariant` — 119 spells are reachable only through the latter.
* `FluffStore.for_entry()` supplies the descriptive text shown beside each choice.

What remains blocking is only the rules logic: G1 (ability-gen constants), G2 (derived-stat
formulas), G6 (`models/pc.py`).

---

## 4. Verdict

**The English dataset is sufficient for a complete, rules-accurate character generator.** Every
*content* requirement is met, and met better than expected — the structured
`choose`/`weighted`/`defaultData` encodings mean the wizard can be driven by data rather than by
per-class special cases.

Note the distinction this analysis turns on: the data **exists in the corpus**, but half the
corpus is **not yet loaded by Paravent** (G7). Nothing below is a gap in the source material.

What is otherwise missing is **rules logic, not data**, and it is small and bounded:

| Gap | Effort | Blocking? |
| --- | --- | --- |
| ~~G1 ability-gen constants~~ | ~~~20 lines of JSON~~ | **done 2026-10-09** |
| ~~G2 derived-stat formulas~~ | ~~`services/character.py`~~ | **done 2026-10-15** |
| G3 multiclass slot table | ~15 lines, hardcoded | multiclass only |
| G4 `prerequisite.other` | UI checkbox | no |
| G5 subclass level | 5-line inference | no |
| ~~G6 PC model~~ | ~~`models/pc.py`~~ | **done 2026-10-09** |
| ~~G7 class data not loaded~~ | ~~corpus Phase 1~~ | **done 2026-10-09** |

G1–G6 are rules logic. G7 was the one that actually blocked, and it was a data-layer task rather
than a chargen one; it is now resolved, so the build order below starts at the PC model.

### Build order

0. ~~**Corpus load Phase 1 (G7)**~~ — done. All class categories, both `generated/` lookups and
   every fluff record are in memory, so everything below can now be tested against real data.
1. **`app/services/tags.py`** — the `{@...}` parser. *Already first on PROGRESS.md.* Every step
   above renders text containing tags; nothing displays correctly without it.
2. **`app/models/pc.py`** — the character model: chosen options + resolved state.
3. **`app/services/character.py`** — derived stats (G1, G2, G3).
4. **`app/services/chargen.py`** — the choice engine: walk `choose`/`weighted`/`defaultData`
   uniformly, validate `prerequisite`, keep a back-navigable choice stack.
5. **Wizard UI** — htpy + HTMX fragments, one route per step, state in `library/`.

### Three traps worth pinning down now

1. **Edition branching.** 2014 puts ASIs on the race; 2024 puts them on the background and adds an
   origin feat. Different wizard orders. Handle via `edition`, do not pick a winner.

   **Update:** the *source* of that edition flag is now settled — it comes from the world's
   declared source set (TECHSTACK.md §6c), not from a separate per-character mode. The branching
   itself stands exactly as described; only the question "where does the wizard learn the edition"
   is answered. A table declaring both `PHB` and `XPHB` is legitimate, so the wizard must still
   branch per chosen entry rather than assuming one edition for the whole character.

   One consequence to respect: because 5eTools marks the 2024 books as reprints of the 2014 ones,
   a both-editions table resolves bare references to the **2024** entry. For the *wizard* that is
   not good enough — picking a class is an explicit choice, not a reference lookup. Where both
   editions are declared, the wizard must **offer both** and let the user choose, rather than
   quietly applying supersession. Supersession is the right default for resolving prose; it is the
   wrong default for a choice the user is making deliberately.
2. **Spell lists come from `gendata-spell-source-lookup.json`**, keyed by source. Filtering spells
   any other way produces wrong lists.
3. **`defaultData` key casing** is lowercase `a`/`b` in 2014 and uppercase `A`/`B`/`C` in 2024.
   Match case-insensitively or silently lose starting-equipment options.

---

## Sources

- [Every Character Maker For D&D — StartPlaying](https://startplaying.games/blog/posts/every-character-maker-dnd)
- [D&D Beyond Character Builder](https://www.dndbeyond.com/play/characters/build)
- [Top 10 D&D Character Creator Tools 2026 — CharGen](https://char-gen.com/blogs/top-10-dnd-character-creator-tools)
- [Dungeon Master's Vault 5e Character Builder](https://www.dungeonmastersvault.com/pages/dnd/5e/character-builder)
- [Your Options For D&D 5E Electronic Tools — EN World](https://www.enworld.org/threads/your-options-for-d-d-5e-electronic-tools.663544/)
