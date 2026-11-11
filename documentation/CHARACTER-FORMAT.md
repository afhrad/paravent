# The character file format

**Scope:** how a Paravent character is stored, so that it can be written by the server and
edited by hand with equal confidence — and what happens on screen when the file changes.

**Date:** 2026-10-15 · **Format version:** 1 · **Status:** built.

- Model (the specification in code): `app/models/pc.py`
- JSON Schema, for editors: [`character.schema.json`](character.schema.json) — regenerate with
  `python -m app.cli schema`; a test fails if it falls out of step with the model
- Worked example: [`examples/lyra-mooncaller.json`](examples/lyra-mooncaller.json) — the level 10
  evocation wizard from `design-reference/16-character-sheet.html`
- Second example: [`examples/brunhild-ironvow.json`](examples/brunhild-ironvow.json) — a level 8
  mountain dwarf paladin belonging to the player `tenewaja`, with rolled hit points and the
  Defense fighting style
- Third example: [`examples/varis-thornwood.json`](examples/varis-thornwood.json) — a level 8
  wood elf Champion fighter (Outlander), not yet assigned to a player; the Defense style,
  Remarkable Athlete and the Alert feat are all derived, so he needs no overrides

> **One sentence:** the file stores **what was chosen and what has happened at the table**; every
> number derivable from that — AC, maximum HP, bonuses, spell slots — is computed on display, and
> any of them can be stated by hand in `overrides`, visibly.

---

## 1. Where files live

```txt
library/characters/
├── lyra-mooncaller-3f9a1c2b7d4e.json     written by the server: <name>-<id>.json
└── bob.json                               dropped in by hand: the filename becomes the id
```

- **One file per character.** Opening, copying, backing up or deleting a character is a file
  operation. (An earlier design kept all characters in one `characters.json`; that made a hand
  edit to one character an edit inside everyone's.)
- **Library data** (TECHSTACK.md §3): the user's own work, never under the reference root.
- The filename is chosen once, at creation. Renaming the character does not rename the file.
- A file without an `id` takes its filename as the id. Two files with the same `id` are both
  reported; the first one read wins and the other is ignored until fixed.

## 2. Live updates

Every open screen showing a character updates itself when the character changes — whoever or
whatever changed it:

| Change made by | Reaches open screens |
| --- | --- |
| A button on any sheet (player's phone, gamemaster's iPad) | immediately |
| Saving the file in an editor | within about half a second (the file watcher's debounce) |
| `python -m app.cli …`, a script, a restore from backup | the same, through the watcher |

The browser keeps one Server-Sent Events connection per open sheet. The server sends only the
character's **revision** (a short hash of its content); the browser fetches the fresh sheet when
the revision differs from what it shows. The sheet's header shows a **Live** dot while the
connection is up. Tabs, the spell-level filter, the advantage setting and open spell
descriptions survive an update; a number being typed is not overwritten mid-entry.

**A mistake in a hand edit does not break the sheet.** Invalid JSON or an invalid value keeps the
last valid version on screen with a banner naming the problem — `state.hp: Input should be a valid
integer`, `Not valid JSON: Expecting ',' delimiter (line 12, column 5)`. Fixing and saving the
file clears it. The gamemaster's character list shows which files have errors.

## 3. The file, field by field

All fields are optional except where noted; omitted fields take their defaults. Unknown fields
are **rejected** — this is library data, and an unknown field is a typo, not data to preserve.

### References

Wherever the file points into the 5eTools corpus, it uses the compact form **`"Name|SOURCE"`**:
`"Fireball|PHB"`, `"Wizard|PHB"`, `"Elf|PHB"`. The source may be left out (`"Arcana"`), and then
resolves against the world's declared books (TECHSTACK.md §6c) — but giving it is better: two
thirds of spell names exist in more than one book. Free text is fine where nothing in the corpus
is meant: `{"item": "Letter from a dead colleague"}`.

### Identity and choices

| Field | Example | Notes |
| --- | --- | --- |
| `$schema` | `"../character.schema.json"` | optional; lets editors validate as you type |
| `format` | `1` | format version |
| `id` | `"3f9a1c2b7d4e"` | stable; defaults to the filename |
| `name` | `"Lyra Mooncaller"` | |
| `given_name` | `null` | the parts of a name Paravent made, `{"text": …, "parts": {…}}`, read by a child's name; ignored once `text` is no longer `name` (NAMES.md §3) |
| `player` | `"tenewaja"` or `null` | who plays it, by **username**. A name with no account yet waits: the character appears for that player once the account exists. Renaming a player updates their files. (Files written before 2026-10-15 used `player_id`, an account id; they are still read, and rewritten to the username on server start.) |
| `world_id` | world id or `null` | its world's declared books govern reference resolution |
| `status` | `"draft"` · `"ready"` · `"retired"` | |
| `pending_levels` | `0` | levels the gamemaster has granted and the player has not taken yet; while above 0 the sheet offers "Level up" (CHARGEN-WIZARD.md §9) |
| `experience` | `null` | experience points, or `null` when the table does not track them. The first XP awarded after a fight starts it at the character's level threshold (COMBAT.md §6); XP never grants a level by itself |
| `wizard_step`, `choices` | `"skills"`, `{"class": ["Fighter|XPHB"], "class.skills.0": ["athletics", "perception"]}` | the creation wizard's place and answers, one list per question. While the wizard runs, the lists below (skills, feats, increases, spells…) are written from these answers; keeping them says which question each came from, so changing an earlier answer shows what it made invalid. `{}` for a character written by hand — nothing reads it then |
| `species`, `subspecies`, `background` | `"Elf|PHB"`, `"High|PHB"`, `"Sage|PHB"` | |
| `classes` | `[{"class": "Wizard|PHB", "level": 10, "subclass": "Evocation|PHB"}]` | a list: multiclassing is more entries. `options` holds the class's choices between features by the feature offering them — `{"Hunter's Prey": "Colossus Slayer"}`, `{"Divine Order": "Protector"}` — and under `"spells"` a subclass spell list chosen from several (`"Coast"`); the sheet then lists only the option taken |
| `hit_points` | `{"method": "rolled", "per_level": [10, 7, 4, 9, 6, 8, 5, 10]}` | how HP was gained — see below |
| `abilities` | `{"str": 8, "dex": 14, …}` | **base** scores, before any bonus |
| `ability_improvements` | `[{"int": 2}, {"int": 2}]` | every *chosen* increase: ASIs, a 2024 background's +2/+1, "+1 to two of your choice" |
| `skills`, `expertise` | `["Arcana", "History"]` | proficiencies the character has, whatever granted them |
| `saving_throws` | `["con"]` | saving throw proficiencies beyond the first class's — the ability a Resilient feat chose. Written by the wizard and level-up; empty otherwise |
| `resistances` | `["fire"]` | damage resistances picked where the species or a feat offers a choice — a dragonborn's ancestry, Boon of Energy Resistance. The sheet asks for any not yet picked |
| `tools`, `languages` | `["Thieves' Tools"]`, `["Common", "Elvish"]` | |
| `weapons` | `["Longsword|PHB"]` | Weapons a feat let the player choose (Weapon Master). Training a class, species or feat grants outright is derived, not stored |
| `feats`, `optional_features` | `["Alert|PHB"]`, `["Cantrip Formulas|TCE"]` | an optional class feature from another book appears only when listed here |
| `spells` | `{"cantrips": […], "known": […], "prepared": […]}` | see below |
| `inventory` | `[{"item": "Quarterstaff|PHB", "quantity": 1, "equipped": true, "attuned": false, "notes": ""}]` | equipped armour, shields and protective items set AC; equipped weapons become attacks |
| `currency` | `{"cp": 0, "sp": 14, "ep": 0, "gp": 230, "pp": 2}` | |
| `resources` | `[{"name": "Arcane Recovery", "max": 1, "recharge": "long"}]` | limited-use features; `recharge` is `short`, `long` or `none` |
| `pronouns`, `appearance`, `backstory`, `notes` | text | |

**Fixed species bonuses** — a 2014 elf's +2 DEX — are read from the corpus and are *not* listed in
`ability_improvements`. Only choices are.

**Hit points.** `method` is `average` (the default: full hit die at first level, the
average `die / 2 + 1` at every later level) or `rolled`, with `per_level` holding each level's
hit-die result in the order taken — **without** CON. The maximum is those values plus the CON
modifier × character level, so raising CON raises every level at once, as the rules say. A level
with no entry yet counts as the average. Rolled hit points need no corpus data, so a homebrew
class gets a correct maximum too. Brunhild's file shows the rolled form: `59 + CON 3 × 8 = 83`.

**Spells.** `cantrips` are always castable. A spellbook caster lists the book in `known` and
today's selection in `prepared` (which must be a subset of `known`). A known-spells caster lists
`known` only. A caster who prepares from the whole class list lists `prepared` only. The
spells a subclass always has prepared (a domain's, an oath's) need not be listed: the sheet adds
them, marked "Always", and does not count them against the number that may be prepared. A file
that lists them anyway is read the same way. A 2024 bard, sorcerer or warlock keeps its spells in
`prepared`; that list changes on level up rather than after a rest. A character with a
spellbook who also prepares from a class list (a wizard who is a cleric too) records each
class's prepared spells under `choices["prepared.Wizard"]`, `choices["prepared.Cleric"]`;
spells recorded there may be prepared without being in the book.

**Granted spells** (`spells.granted`) are those a feat, species or background gives — Magic
Initiate's two cantrips and level-1 spell, a high elf's cantrip — each block cast with its
own ability, not the class's:

```json
"granted": [
  {"source": "feat:Magic Initiate|XPHB", "list": "Cleric Spells", "ability": "wis",
   "spells": ["Guidance|XPHB", "Sacred Flame|XPHB", "Bless|XPHB"]}
]
```

`source` is `feat:<name>|<source>`, `species`, `subspecies` or `background`; `list` the
block chosen where the record offers several; `ability` where the player picks it; `spells`
the spells picked. Spells a grant gives outright (a tiefling's Thaumaturgy, its Hellish
Rebuke from 3rd level) are not listed: the sheet adds them at the character's level, as it
adds a domain's spells. A spell castable without a slot once per rest gets a resource of
its own, counted in `state.resources_used` ("Bless (Magic Initiate)": 1) and restored by
the rest.

### `state` — what play changes

| Field | Example | Meaning |
| --- | --- | --- |
| `hp` | `41` | current HP. `null` means "at full"; the server writes the number on the next change |
| `hp_max` | `62` | **written by the server** on every change — the maximum as computed, so the file reads "41 of 62". Not an input: change `hit_points` or `overrides.hp_max` instead. Used only as a fallback when nothing else gives a maximum |
| `temp_hp` | `5` | |
| `hit_dice_used` | `{"d6": 2}` | per die size |
| `slots_used` | `{"1": 1, "3": 1}` | per spell level, `"1"`–`"9"` |
| `pact_slots_used` | `0` | warlock pact magic |
| `resources_used` | `{"Arcane Recovery": 1}` | by resource name |
| `conditions` | `["prone"]` | any of the 14 conditions, lowercase |
| `exhaustion` | `0`–`6` | |
| `concentration` | `"Haste|PHB"` or `null` | |
| `inspiration` | `true` / `false` | |
| `raging` | `true` / `false` | a barbarian's Rage: resistance to bludgeoning, piercing and slashing while on; a rest ends it |
| `mastery_kind`, `mastery_set` | `"Dagger"`, `true` | Weapon Master (2024): the kind of weapon whose mastery is used, chosen on the sheet; `mastery_set` holds it until a long rest |
| `death_saves` | `{"successes": 0, "failures": 0}` | |

### `overrides` — stating what cannot be derived

Each value is derived from the rules and the corpus unless stated here. An override is shown on
the sheet with a marker and its note, never silently mixed in:

```json
"overrides": {
  "ac":      {"value": 17, "note": "Ring of Protection +1, Cloak of Protection +1"},
  "hp_max":  {"value": 71, "note": "rolled hit points"},
  "speed":   35
}
```

Worn magic items need no override for what the corpus types: an equipped item — attuned, if
it requires attunement — adds its AC, saving throw, spell attack, spell save DC and ability
check bonuses itself (a Cloak of Protection, a +1 wand, a Stone of Good Luck), named in the
number's explanation.

`15` alone is shorthand for `{"value": 15}`. The keys that can be overridden:

| Key | |
| --- | --- |
| `ac`, `hp_max`, `speed`, `initiative`, `passive_perception`, `proficiency_bonus` | |
| `spell_save_dc`, `spell_attack` | |
| `ability.<str…cha>` | the **total** score — a Belt of Giant Strength |
| `save.<str…cha>` | a saving throw bonus |
| `skill.<name>` | a skill bonus: `skill.sleight of hand` |
| `slots.<1…9>` | spell slots of a level — homebrew casters |
| `pact_slots`, `pact_slot_level` | |

This is also how a character works **without** corpus data — a homebrew class, or a character
copied off paper: give the values that cannot be derived, and the sheet shows them.

### `log` — gone since 2026-10-25

Rolls and notable actions are no longer kept in the character file: they go to the log
(documentation/PROTOCOL.md), one append-only file per world, with the dice, the advantage and
every bonus by its source. An older file's `log` is still read — and copied into the log once,
on start — but never written back; the next save drops it.

## 4. What the sheet derives

Read from the corpus: class hit dice, saving-throw proficiencies, spellcasting ability, slot
progression (the class's own table for a single caster; the multiclass table across several;
Warlock pact slots from the class table), subclass casting (Eldritch Knight), species speed and
fixed bonuses, skill→ability, armour and weapon statistics, spell details including cantrip
scaling, and class, subclass, species and background features.

Features that change numbers, recognised by name **and source book** wherever the character
has them (`app/services/traits.py`): Aura of Protection (+CHA, min +1, to every save), Diamond
Soul / Disciplined Survivor (proficiency in every save), Jack of All Trades and the 2014
Remarkable Athlete (half proficiency on unproficient checks and initiative), Unarmored Movement
and Fast Movement (speed, with their armour conditions), Dwarven Toughness, Draconic Resilience
(HP, and its 2014 or 2024 unarmoured AC), the Dance bard's Unarmored Defense, the Defense
fighting style (a 2014 option or a 2024 feat: +1 AC in armour), and the feats Tough, Alert,
Mobile, Speedy and Observant (2014). Each shows in the value's explanation; an override wins.
Anything else that changes a number is still stated as an override.

Hardcoded because they are prose upstream (CHARGEN.md G2/G3): the proficiency bonus, the
AC/DC formulas, Unarmored Defense, and the multiclass spell-slot table.

Rules applied by the sheet's buttons (`app/services/play.py`): temporary HP absorbs damage first
and never stacks; damage while concentrating rolls the Constitution save (DC 10 or half the
damage) and ends concentration on a failure; damage at 0 HP is a failed death save; healing from
0 resets death saves; a long rest sets `hp` back to the maximum; a short rest restores short-rest resources and pact slots; a long rest
restores HP, slots, all resources, half the hit dice and one level of exhaustion.

## 5. Not yet

- Units are always feet; the per-user unit setting (TRANSLATION.md §6) is not applied yet.
- Spell, feature and condition **text** is the English corpus text; translating reference text is
  the overlay system of TRANSLATION.md, not built yet. Labels — abilities, skills, condition
  names — are translated.
- Carrying capacity and encumbrance are not computed.
