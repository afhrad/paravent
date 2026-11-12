# The combat tracker

**Scope:** running a fight — starting it from a prepared encounter, initiative, turns and
rounds, hit points and conditions for monsters and player characters alike, what the players
see on their phones, and how every screen stays in step.

**Date:** 2026-10-17 · **Status:** all five steps of §8 built (2026-10-17/18). Awarding XP built 2026-10-19 (§6); lair actions and turn-start reminders built 2026-10-19 (§4); friend and foe with a live difficulty built 2026-10-21 (§3).

> **One sentence:** an encounter is the plan, a combat is one run of it; monsters live in the
> combat record, characters stay in their own files, and every change reaches every screen.

Visual reference: `design-reference/02-combat-tracker.html`; the combatant row is specified in
DESIGN.md §4.

---

## 1. Decisions

| Question | Decided |
| --- | --- |
| What players see | **The order, the round and whose turn it is.** A monster shows its name and a state word (unhurt, bloodied, down), never HP or AC. A player's own HP stays on their sheet |
| Who rolls initiative | **Players roll on their sheet**, and the roll lands in the tracker live; the tracker rolls the monsters; the gamemaster can overwrite any number |
| Monsters of one kind | **One initiative per kind** (the DMG default): four goblins act on one count, listed together, each with its own HP and conditions |
| Monster hit points | **The statblock's average**, editable per combatant |

Defaults decided here, revisable when the table says otherwise:

- **One running combat per encounter.** Running it again starts a new combat; a finished
  combat is kept as a record (who fell, how many rounds, the log) until deleted.
- **Ties** go to the higher initiative modifier, then characters before monsters, then the
  gamemaster's drag. No hidden rolls.
- **Everything outside the player view is gamemaster-only, enforced by the route.**

---

## 2. Encounter and combat

```txt
Encounter   the plan      name · scope · monsters (Name|SOURCE × count) · party · rules
 └ Combat   one run       round · turn · combatants · log · started/ended
```

The `Encounter` model carries an unused skeleton of run state (`combatants`, `round`,
`turn_index`, `active`). It moves out into its own record, `Combat`, in `library/combats.json`:

- a plan edited during a fight (a fifth goblin for next time) does not touch the fight;
- the fight writes on every button, the plan rarely — separate files, separate locks;
- a finished fight is history worth keeping, which a field on the encounter cannot be.

The old fields are dropped on load by a before-validator; they were never written with data.

### The Combat record

```txt
Combat
  id · encounter_id · world_id · name          name copied from the encounter at start
  round        0 while initiative is being rolled, then 1, 2, …
  turn         the group whose turn it is (see below)
  combatants   ordered as set; the order on screen is derived
  log          newest first, capped (200 entries)
  started · ended (None while running)
```

### Combatants

One model, two kinds:

| | Monster | Character |
| --- | --- | --- |
| Identity | `ref` (`Goblin|MM`), `label` ("Goblin 2"), `group` | `character_id` |
| HP, temp HP, conditions, concentration, death saves | **in the combat record** | **in the character file**, read live, changed through the play rules |
| Initiative | the group's roll | its own, from the sheet or typed |
| Statblock | the corpus, through the world's books | the character sheet |

**Characters are never copied.** Damage from the tracker is the same action as damage from the
party overview (`PlayService`): temporary HP first, the concentration save rolled and logged,
damage at 0 HP a failed death save. The player's sheet follows within a second, and a hand edit
of the character file shows in the tracker.

A monster's `group` is the kind it was added as; members share one initiative and one turn.
The gamemaster can **split** a member off to a group of its own (it then rolls its own
initiative) — the escape hatch the DMG default needs when one goblin flees.

**Names** (2026-11-09, NAMES.md §5): a planned group whose label lists a name for each
("Snik, Grub") starts with those labels, the rest numbered; in the tracker a dice in a
creature's label field rolls it a name in its kind's style, and **Name each** in a group's
menu names every member differently. Members keep their group and initiative.

**Ad-hoc combatants** (a summoned wolf, a hostile NPC without a statblock) have a label, AC,
HP and initiative, and no `ref`. An NPC from ADVENTURES.md §4 with a statblock is added as a
monster with its NPC's name as the label.

**Hidden** combatants — the ambushers who have not yet appeared — are listed for the
gamemaster, dimmed, and left out of the player view until revealed.

---

## 3. Rules the tracker applies

Monsters get the same rules characters already have, minus what only characters have:

- Damage takes temporary HP first; temporary HP never stacks, the higher value wins.
- **Bloodied** at or below half maximum, **critical** under a quarter, **down** at 0
  (DESIGN.md §4's stripe: indigo active, amber, red, grey).
- Damage to a concentrating monster shows the save it owes — DC 10 or half the damage — with
  a roll button using its CON save; a failure ends concentration. Logged either way.
- A monster at 0 HP is down, not dying: no death saves. It stays in the list (greyed, last in
  its group) until removed — bodies matter to the fiction and to "undo".
- Conditions are the fourteen the sheet knows (exhaustion, a level, stays on the sheet). An optional
  **round count** ("frightened, 2 rounds") ticks down at the end of the creature's turn and
  drops the condition at zero, with a log line.

### Friend and foe (2026-10-21)

Every combatant fights on a side. **Characters are always friends**; a monster or an ad-hoc
combatant is a **foe** unless the gamemaster makes it a friend (`Combatant.ally`) — a hired
guard, a summoned wolf, a charmed ogre.

- **Switching** is the side pill on the row and turns the **whole group**, since its members
  act together; a goblin who turns alone is split off first. Logged ("Goblin ×4 now fights
  as Friend"). Adding mid-fight asks Foe/Friend, foe by default, for picker monsters and
  ad-hoc combatants alike.
- **Shown** as the token colour (green friend, red foe) and the pill; the left stripe
  stays the row's health. **The row's background is its hit point bar** (2026-10-25):
  filled from the left to the share left — green, amber when bloodied (half or less),
  red when critical (under a quarter), empty when down. It replaced the side tint,
  which would have meant the same colours twice. **The players see it too**: a friend's
  bar at its real share (the projection's one number, `SeenCombatant.fill`), a foe's
  always full, coloured by its state word — empty once down — so no foe's hit points
  leak. Every change, from the tracker or a character's sheet, reaches each player's
  board over its stream.
- **Dead**: a monster at 0 HP, or a character with three failed death saves, carries a
  skull "Dead" pill and is struck through. A dying character is down, not dead.
- **XP** comes only from fallen foes; an ally that falls earns nothing.
- **Planned allies** (2026-10-21): the encounter builder's lines carry the same pill. An
  ally line is rated with the party at its CR (and shows no XP), keeps its own line when
  more of that creature are added as foes, and starts the fight as allies.

**The difficulty in the round head** is the encounter's rating (§1 of the builder: 2014 or
2024 arithmetic by the world's books or the encounter's override), re-rated on every
render from **those still standing**:

- **Foes**: monsters with a challenge rating, not down — their XP, and under 2014 rules
  their count for the multiplier. So the marker falls as foes fall.
- **The party**: characters neither dying nor dead, plus standing allies, **an ally
  counted as a party member of a level equal to its challenge rating, at least 1**. The
  DMG gives no rule for allied monsters; this is the common convention, and it moves the
  2014 party-size shift as the side grows. A downed character leaves the party, so the
  marker rises.
- Someone without a challenge rating (ad hoc) counts on neither side. A fight without
  characters rates against the party the encounter states, if any. With every foe down
  it reads "No foes standing".

**Damage resistance, immunity and vulnerability** are shown from the statblock beside the
damage input, not applied automatically: the tracker does not know the damage type unless the
gamemaster says, and a silent halving is worse than a visible reminder. A rolled
statblock attack carries its types, and applying it counts them (below).

### Statblock attacks (2026-10-22)

Every action whose text carries `{@hit N}` gets **Attack**, **Adv** and **Dis** buttons under
it in the side panel (`app/services/monster_attacks.py`). One press rolls the d20 and, unless
it shows a natural 1, the damage the action's `{@damage}` tags name: "plus 7 ({@damage 2d6})
poison damage" adds a roll, "or 8 ({@damage 1d10 + 3}) … with two hands" is an alternative and
ends the list. A natural 20 doubles the dice, not the modifier. The roll is **one log entry**
— "Goblin 1 – Scimitar: 17 to hit, 5 damage" with the dice and damage types beneath — tinted
and marked for a natural 20 or 1, and the device plays the dice sounds (crit and fumble
included). The tags are kept verbatim in a translation, so a German statblock rolls the same.

**Hit or miss.** A rolled attack waits at the top of the board under "Attacks to resolve",
with a target — the attacker's opponents first — and **Hit** / **Miss**: whether the total
beats the target's AC is the gamemaster's call (a hidden bonus, cover, a Shield spell). A hit
applies the damage through the usual rules (temporary HP first, a concentration save owed, a
character's file), and a monster target's statblock counts per damage type: immune none,
resistant half, vulnerable double. Only its unconditional entries — "fire", not "from
nonmagical attacks", which is a judgement. A character target counts its sheet's defences
the same way (species, feats, worn and attuned items). The log entry then reads "… 5 damage → Goblin 2", or "missed". The list is the current round's.

**Save effects** (2026-10-22). An action with a DC, a save ability and damage — no attack
roll — gets **Roll damage** instead: a breath weapon, a spell-like burst. Both editions'
wording is read ("must make a {@dc 21} Dexterity saving throw … half as much damage on a
successful one" and 2024's `{@actSave dex} {@dc 21}` … `{@actSaveSuccess} Half damage`).
**A condition** the save imposes is read too — the first standard condition after the DC
whose wording puts it on the target ("or be {@condition frightened}", "has the … condition",
not "if it isn't incapacitated"), with its duration: "for 1 minute" is 10 rounds, "until the
end of its next turn" 1, anything else no timer. An action whose only consequence is a
condition (Frightful Presence, a spider's Web) gets **Call for saves**; a failure puts the
condition on with its timer, unless the target has it or its statblock is immune.

**Players roll their own saves.** While a save effect waits, the players' fight view and the
strip above each sheet offer "Roll your Dexterity save" — the action and its source named, no
DC (the players' view carries no numbers), "if it reaches you" because the gamemaster says at
the table who is in the area. The roll is the character's own (sheet bonus, the player's log,
the dice sounds) and is kept on the effect; the gamemaster's form shows it beside the name and
pre-selects Saves or Fails against the DC, and **Roll save** uses it instead of rolling again.

**Repeating the save.** "A creature can repeat the saving throw at the end of each of its
turns" (533 actions say so; three say "start") is read with the condition. A target that
fails keeps that save on its tracker row; during its turn the board lists it under "Saves to
repeat" — "Goblin 2: at the end of its turn, repeat the Wisdom save (DC 13) to end
Frightened" — with **Roll**, and a character's player gets the same on their phone. A
success ends the condition, a failure keeps it; either way it is asked once per turn. The
special cases ("at the end of its next turn, becoming petrified on a failure") stay with the
gamemaster.

**Players' attacks.** An attack a player rolls on the sheet while the character is in a
running fight joins it: the weapon's (or attack spell's) damage is rolled with it — dice
doubled on a natural 20, none on a 1 — and noted in the player's log, and the attack waits
under "Attacks to resolve" like a monster's, the monsters offered first as targets (a
character and its allies count as one side). Outside a fight the sheet rolls as before.

**Weapon mastery** (2024). The eight masteries load as a reference category
(`itemMastery`), and a weapon on the sheet shows its mastery as a link for a character with
the Weapon Mastery feature. A player's attack carries it into the fight: after **Hit**,
*Topple* calls a Constitution save (DC 8 + the attack's ability modifier + proficiency)
against prone — rolled at once for a creature, by the player for a character — and *Sap*,
*Vex*, *Slow*, *Push*, *Cleave* and *Nick* are noted in the log for the table; *Graze* is
noted after **Miss**. Which weapons a character has chosen to master is not tracked: the
sheet shows the mastery of every weapon they wield.

**Players' save spells.** While the character is in a running fight, every spell on the sheet
that calls for a save gets **Call for saves**: it joins the fight as a save effect with the
spell's save, the caster's spell save DC, its damage at the slot chosen (a cantrip at the
character's level; a slot dropdown when it can be cast higher, `{@scaledamage}` adding its
dice — Fireball at 5th is 10d6), "half as much on a success",
and a condition read from its text — Hold Person's "at the end of each of its turns, the
target can make another Wisdom saving throw" becomes a repeated save. The gamemaster settles
the targets as for a breath weapon. The button casts the spell too: the slot is spent and
concentration starts, as **Cast** would.

**On a phone** (checked at 390px, 2026-10-22): the waiting attacks and saves sit above the
order's scroll box, each save effect's targets fold under its line and keep both their open
state and hand-made choices when the board refreshes; a long log line no longer widens
the board.
The damage is rolled **once**, as the rules have it, and waits under "Attacks to resolve"
with every combatant listed — opponents first — and a choice each: **—** (not in the area),
**Roll save** (the statblock's save or ability modifier, the character's sheet save),
**Fails**, **Saves**. **Apply** gives a failure the full damage and a success half or none,
counts each target's defences, and logs one line per target ("Goblin 2 fails the
Constitution save (3): 30 damage"). **Evasion** — a rogue's or monk's feature, a creature's
trait — turns a Dexterity save against half damage into none on a success and half on a
failure. A raging barbarian (the Rage switch in the side panel) resists bludgeoning,
piercing and slashing.

---

## 4. Initiative and turns

```txt
start → round 0 "rolling initiative"
          monsters: one d20 + DEX per group, rolled by the tracker
          characters: blank until the player rolls on their sheet (or the GM types it)
        → "Begin" (allowed with blanks; a blank sorts last and is flagged)
round 1, turn 0 → Next → … → last turn → round 2, turn 0 …
```

- **The turn order is derived**, never stored as a list: initiative descending, then
  modifier, then characters first, then a manual `order` the gamemaster sets by dragging
  within a tie. Changing an initiative mid-fight re-sorts and keeps the current turn on the
  same combatant.
- **A sheet's initiative roll lands in the tracker** when that character is in a running
  combat: the play action is unchanged, and the tracker reads the newest initiative entry
  from the log made since the combat started. The player's phone already has the button.
- **Next / Previous turn**; Previous exists because mis-taps happen at the table. A down
  monster group's turn is skipped; a down character's is not (death saves happen on it).
- **Turn start** (built 2026-10-19) shows what is due in a panel above the order, for the
  turn acting now — reminders, with a button where one helps, never automation:
  - legendary actions regained (`legendaryActions`, 3 when unstated);
  - recharge: every action named with `{@recharge N}` (bare: 6) — "Fire Breath recharges on
    5–6" and a d6 button that logs "recharged (6)" or "not recharged (4)";
  - regeneration: a `Regeneration` trait's "regains N hit points at the start of its turn",
    read from the prose (one fixed phrasing across the corpus), offered as a "+N" heal while
    the creature is hurt — whether acid or sunlight stops it is the gamemaster's call;
  - concentration held, by monsters and characters.
- **Lair actions** (built 2026-10-19). A monster whose `legendaryGroup` has `lairActions`
  adds a turn named `lair` on initiative 20, losing ties: after everyone on 20 or more,
  before the rest. Its row reads "Lair · lair actions of Adult Red Dragon"; its turn start
  shows the group's lair actions, with the tag renderer's DC and damage chips. It is
  skipped once every creature of that lair is down, and logged as "Lair: Red Dragon".
  "Lair actions on/off" in the round head turns it off when the fight is not in the
  creature's lair (`Combat.lair`); switched off on the lair's own turn, the turn passes on.
  The 2024 Monster Manual's groups carry no lair actions (its lair is a matter of the
  creature's own traits), so a 2024 dragon has no lair turn. The players' view leaves the
  lair out. The statblock — reference page and tracker alike — now ends with the group's
  lair actions and regional effects.

---

## 5. Screens

### The tracker (gamemaster) — `/gm/combat/{id}`

The design reference's layout: a header with round, whose turn, **Next turn**, **Add**; the
order as combatant rows; the selected combatant's statblock in the drawer (desktop), a sheet
(iPad, phone).

- **`combatant_row(c)`** is one htpy function, returned in the page and alone from every
  action (`POST /gm/combat/{id}/c/{cid}/{action}`) — DESIGN.md §4.
- The row: initiative, a token (initials or "G2"), label, pills (side, dead, conditions,
  concentrating, boss), a metadata line (AC · kind · CR, or death saves), HP as a number and
  bar, and **one compact line at the bottom**: the amount field, **−**, **+**, Temp and the
  ⋯ row menu at its end (moved there 2026-10-21, so the name and pills keep the width). It
  never wraps: 36px targets with a mouse, 44px on touch (DESIGN.md §2), Temp included on a
  phone, where it used to hide in the menu.
- **The order scrolls in its own box** under the round head (2026-10-21), so the round,
  whose turn, the difficulty and **Next** stay in view through a long fight; the board's
  swap after every button keeps the scroll position (`combat.js`), and so does leaving
  through the top bar and coming back (`data-scroll-keep`, `ui.js`).
- A **group** renders as its members in one block with one initiative number.
- The statblock is the reference page's `statblock()`; its rolls roll locally, as elsewhere.
- **A character's side panel** (2026-10-21) is a statblock of its own, read from the derived
  sheet: armor class, hit points and temp HP, speed, initiative, proficiency, the six
  abilities, saves (proficient ones bold), proficient skills, passive Perception,
  conditions, exhaustion, concentration, death saves while dying, attacks, spell DC and
  attack, slots left per level, prepared spells by level (linked, with previews) and class
  resources. A **Sheet** button opens the full character sheet. It reloads after every
  change to the board (`data-live`), so damage, slots and conditions stay current.
- **Add** mid-fight: a monster through the ⌘K picker (bestiary), a character from the world,
  or an ad-hoc combatant. A monster added mid-fight rolls its initiative on entry.
- **End combat** asks once, then shows the summary (§6).
- **The encounter's notes** (2026-10-28) — setup, terrain, tactics, morale, how it ends —
  sit folded under the board as *Encounter notes*, rendered in Paravent Markdown where the
  encounter is placed (ADVENTURES.md §5), so `{@creature}`, `{@npc}` and callouts work as
  in the chapter. Outside the board, so its live swaps leave them be; the gamemaster's
  only, never on the players' view.

### Starting

- **"Run"** on the encounter builder and on every `::: encounter` embed in a chapter.
- Starting fills the combat from the plan: each `EncounterMonster` becomes its group of
  `count` members (labels numbered when count > 1), and **every character in the encounter's
  world** joins, each removable before Begin. An encounter with no world starts with
  monsters only.
- A running combat is linked from its encounter, its chapter, and the `/gm` overview
  ("Combat · round 3"), so a reload or another device finds it.

### The player view — on `/play` and the character sheet

When a combat is running in the world of a player's character: a strip on the sheet and a
page `/play/combat/{id}` with the round, the order and whose turn it is. **"Your turn"** is
emphasised on that player's phone. Monsters appear as label + state word; hidden ones not at
all; no AC, no HP numbers, no statblocks. The route builds this view from a separate
projection, so a field added to the tracker never leaks into it by accident.

**Tapping a row** (2026-10-25) opens two choices. **Target** sets the character's target, a
friend or a foe (`Combatant.target`, `POST /play/combat/{id}/target`); a hidden creature
can't be picked. The row then shows "Your target" and the sheet's strip "Target: Goblin 2".
On the tracker, the character's row names its target, and the gamemaster's Hit list for
that character's attack offers it first. Nothing is logged. **Details** shows the side,
the state word and the conditions for now; more will follow. An open menu stays open
when the board refreshes, and closes once a target is picked.

### What players know about a creature (designed 2026-10-25)

Details on a foe (and on a creature fighting with the party) is what the characters could
know — by looking, by having watched it, or because the gamemaster told them. Decided
with the user:

**1. By sight**, from the moment it is visible:

- its label as the gamemaster set it ("Hooded figure" stays that), how hurt it looks, its
  conditions — as today;
- size and kind from the statblock ("Large monstrosity"), in the reader's language;
- visible gear and movement read from the statblock: the armour named in its AC
  (`"from": ["leather armor", "shield"]`), the weapons of its attacks, and its speeds
  ("flies 60 ft." in the reader's units) — never a number that is not seen (no AC, no HP);
- a picture and the books' description (`monsterFluff`). **The corpus here carries no
  image files** (5eTools ships them separately): a picture shows when the image folder is
  present, otherwise only the text. **The table's own picture** takes its place where the
  gamemaster set one (2026-10-25, `app/services/creature_pictures.py`): per world and kind
  of creature, uploaded from the tracker's side panel ("Picture · your own / from the books
  / none"), kept in the world's folder as `media/creatures/<name>--<source>.<ext>` (a fight
  outside any world: the library's `media/creatures/`), with the world media's rules
  (an image type its bytes confirm, no SVG, 20 MB); served to the players at
  `/play/combat/{id}/c/{cid}/picture`, never for a hidden combatant. A creature of the creature
  builder has its picture set on the builder page or by Claude from a web address
  (2026-11-11, MCP.md §10), under the creature it publishes as (`Name|Homebrew`); one of
  a design without a world lies in the library's folder and serves every world's fights. The description is the
  first paragraph, in the reader's language where the gamemaster translated it (the
  tracker's **Description** button; TRANSLATION.md §5), passage by passage, else English.

**2. Observed**, filled in automatically from the log as it happens:

- attacks and abilities it used ("Scimitar — slashing", "Fire Breath — Dexterity save"),
  never their bonus or DC;
- spells it cast, by name;
- how it took damage: each damage type that hit it, with what its statblock does to it —
  "took half from fire", "unharmed by poison", "took double from radiant". The verdict
  entries carry the types; the defence is read from the statblock when the fact is learned;
- conditions it shrugged off (a condition immunity the tracker respected).

**3. Revealed by the gamemaster**, per fact: on the tracker's side panel each creature
lists what could be revealed — every action, trait, resistance, immunity, vulnerability,
sense, language, an approximate AC ("about 15") and CR band — with a tick per fact, and a
free note. For a lore check, Identify, or simply telling the table.

**Memory: per creature kind, per world.** Knowledge is kept in
`library/knowledge.json`, `{world id: {"Goblin|MM": {fact: {how, at}}}}`, where a fact is
an id (`action:Scimitar`, `resist:fire`, `spell:Fireball`, `ac`, `note`), and `how` is
`observed` or `revealed`. The next goblin fight in the world starts with what the party
learned. A creature whose label the gamemaster changed still counts for its kind; a
combatant without a statblock has only sight and the note. The gamemaster can forget a
fact again.

**Never on a player's phone**: exact AC, hit points, CR, ability scores, saving throw and
skill bonuses, unused actions and traits, legendary and lair actions not announced, and
anything about a hidden combatant.

**Build order.** (1) Details as a panel with sight. (2) The knowledge store and what is
observed from the log. (3) The gamemaster's reveals on the tracker, and forgetting.

**Step 1 built 2026-10-25** (`app/services/creature_sight.py`). Details is fetched when
opened (`GET /play/combat/{id}/c/{cid}/details`): side, state and conditions, then size
and kind, movement, gear and the description (its first paragraph, drawn by the tag
renderer since 2026-11-12: links only to what the player may read, metric hints for a
reader in metres). Gear is the 2024 `gear` list, or for 2014
the armour named in the AC and the attacks named after a base item (a goblin's Scimitar
and Shortbow, never a dragon's Bite). Pictures are served from `/play/img/…`, read in
place from `PARAVENT_IMAGE_DIR` or `img/` next to the corpus, and confined to it. A hidden
combatant answers 404.

**Step 2 built 2026-10-25** (`app/services/knowledge.py`). Every log entry the players may
read is looked at as it is appended. A creature's rolled attack teaches its name and
damage types; a save effect its name, save and types. A Hit on a creature, or its failed
save against an effect (or a success that still took damage), teaches each damage type
that landed and what the statblock did to it: "Fire: half damage", "Poison: unharmed".
Kept in `library/knowledge.json` per world and kind; a write there alone reloads only that
file, as for `places.json`. Details lists it under "Seen in fights". A hidden creature,
as actor or target, teaches nothing.

**Step 3 built 2026-10-25.** Above a creature's statblock on the tracker, "What the players
know" lists every fact of its kind with how it was learned (seen, or told) and a Forget
button, then what the statblock could still tell them: each action, bonus action,
reaction and legendary action, each trait (with its text), each damage immunity,
resistance and vulnerability, each condition immunity, the AC as a band (easy, average,
hard, very hard to hit: up to 12, 13–15, 16–18, 19+) and the CR as one (minor up to ½,
moderate to 4, dangerous to 10, deadly to 16, legendary above), with a note for the
players. What is left to tell is folded by kind — actions, spells, traits, damage,
condition immunities, armour and threat — each with its count, all open when there are
eight or fewer. Ticked facts are revealed at once (`POST /gm/combat/{id}/known/{cid}`); a told
fact is never overwritten by what is seen later, and Forget drops either kind. Players
read it in Details under "What you know". Actions are named one way everywhere
(`action_name`): the tracker's log reads "Fire Breath", no longer "Fire Breath 5".

**Spells and condition immunities** (2026-10-25). A spellcaster's side panel has **Cast**,
a button per spell of its statblock (at will, per day, by level; `monster_spells`): the log reads
"Cast Fireball" and the party learns it. **Cast does the spell** where it can be rolled,
like a player's save spell: the spell's record is resolved in the world's books, cast at
the slot of the list it is in (2014's "Level 3" list, 2024's "(level 4 version)", else
its own level), a cantrip at the caster level the block states ("a 9th-level
spellcaster"). A **save spell** becomes a save effect with the block's DC and its damage
and condition (Fireball: 8d6 fire, Dexterity for half), waiting for its targets; an
**attack spell** rolls to hit with the block's bonus and waits for Hit or Miss (Fire
Bolt: 2d10 fire at caster level 9). A 2024 block without its own DC uses the one before
it. Anything else (Shield, Misty Step), or a spell outside the world's books, is only
logged; its effect is the gamemaster's. A spell rolled this way is learned as the spell,
with its damage types and save, not as an action. A save effect's condition that a creature's statblock is immune to
is logged ("Immune to Poisoned") and learned. Action and trait names read in the reader's
language on both lists of facts: from the creature's translation, else the glossary's
weapon name ("Scimitar" → "Krummsäbel"). A save's line comes first in the log, then
what it caused: the damage, then the condition or the immunity. Spells are offered to
reveal as well.

**Slots and daily uses** (2026-10-25). Cast spends what the spell uses: a slot of its
list's level (shared by that level's spells, as many as the statblock gives; a warlock's
pact slots the same way), a "3/day each" use of its own, or a "3/day" use its list
shares; per rest and per long rest count like per day. Each spell's button says what is
left ("Fireball 2 left"); a spell with nothing left is greyed out ("used up") and a cast of it
refused. A cast or a restore reloads the side panel, so the counts follow at once. Kept on the combatant (`Combatant.spent`), so each mage of a group has
its own, and a new fight starts full. **Restore spells**, shown once something is
spent, gives everything back (a mistaken cast). At will and cantrips are never counted;
casting from a higher slot is not offered.

**The row menu, simplified** (2026-10-25): aiming is a button with the character's name
(the target icon says what it does, "Target" its title); Details is the "i" alone, only
on creatures — a character's row says all there is; and Details no longer repeats the
side, state and conditions the row shows.

---

## 6. Ending, the log

The **combat log** records turns, damage, healing, conditions, saves and who did each, newest
first, with the gamemaster or player named. Since 2026-10-25 it is the fight's view of the
central log (documentation/PROTOCOL.md): the record keeps no log of its own, only `pending` —
the attacks waiting for Hit or Miss and the save effects waiting for their targets, which leave
it as they are resolved.

**Ending** stamps `ended`, and the summary shows rounds fought, who fell, and **XP**: the sum
of the defeated monsters' XP (the same table the difficulty rating uses), split among the
characters who took part.

**Awarding XP** (decided and built 2026-10-19: *XP suggests levels, the gamemaster grants
them*). The summary offers "Award to N characters" with the share prefilled and editable;
awarding writes it to each character's `experience` once (`awarded_xp`, `awarded_at` on the
combat; a second award is refused) and logs "+N XP · <fight>" for each character and "N XP
awarded" for the fight, in the log. A character whose file has no `experience` yet starts at their level's
threshold, so a hand-made level 10 is not ten levels behind.

The thresholds are the Player's Handbook's *Character Advancement* table, read out of the
rendered book table into `data/rules/encounterdifficulty.json` (`advancement`) by
`python -m app.cli rules`, like the encounter tables; the 2024 table is the same. When a
character's XP reaches a level beyond those played and granted, nothing is granted: the
summary says "Level 11 reached" with "Grant a level" beside it (returning to the summary),
the party card on `/gm` shows "L11", and the gamemaster's strip on the sheet says so. The
sheet shows everyone an XP bar to the next threshold (`app/services/experience.py`).

**Treasure** (2026-10-19): an ended fight in a world offers "Roll treasure", which makes a
treasure in that world and opens it with the roll form at a hoard of the fight's highest
challenge rating (PROGRESS.md, corpus phase 2).

---

### Conditions and rolls (2026-10-22)

A creature's **own** conditions bend its d20 rolls (`app/services/condition_rolls.py`):
Blinded, Frightened, Poisoned, Prone and Restrained give disadvantage on its attacks
(Frightened and Poisoned on ability checks too, Restrained on Dexterity saves), Invisible
advantage; advantage and disadvantage cancel. **Exhaustion** follows the character's edition
(its first class's book): 2014 — disadvantage on checks from level 1, on attacks and saves
from 3, speed halved from 2 and 0 at 5, maximum HP halved from 4; 2024 — −2 on every d20 roll
and −5 ft. speed a level. A **target's** condition is not applied to a roll made before the
target is known: the target list names it and what it means ("Paralyzed (advantage; a
critical within 5 ft.)"), and **Critical** makes a hit a critical — the attack keeps its
damage dice, which are rolled once more.

### The recap (2026-10-22)

**Recap** on a fight's summary (`/gm/combat/{id}/recap`, `app/services/recap.py`) is read
from its log: per combatant the damage dealt (an applied attack's damage, a save effect's on
each target), taken (what the tracker applied) and healed, natural 20s and 1s, who went down;
the biggest hit; the conditions, with the save effect that imposed them. A text block holds
the same for the gamemaster's session notes. **The whole session** (`/gm/recap?world&day`)
recaps every fight of the world begun that day together. Rolls made only on a sheet (outside
the tracker) are not in a fight's log and so not in the recap.

## 7. Live updates

- The combats collection publishes every change on the combat's id (the tracker on another
  device, the player view) and on a `*combat` key per world for the "a combat started" strip.
- A character change already publishes on the character id; the tracker subscribes to the
  ids of its characters as well, so a player's own damage or initiative roll re-renders their
  row.
- The message is the revision, as everywhere; the client fetches the fragment it lacks.
- **Two gamemaster devices** (laptop and iPad) are a normal case: every action is a mutate on
  the record under its file lock, so two taps never lose each other.

---

## 8. Build order

1. ~~**Model, store, rules.**~~ — **done 2026-10-17**: `Combat`, `Combatant`,
   `ConditionTimer`, `CombatLogEntry` in `app/models/gamestate.py`; `library/combats.json`;
   `app/services/combat.py` (`registry.combats`); `tests/test_combat.py` against the corpus.
   As built:
   - `Combat.turn` stores the **group id**, not a position: the order is derived, so a
     changed initiative re-sorts without moving the turn to someone else.
   - Characters are reached through `CharacterSide`; `SheetCharacters` implements it over
     the character service, so the tracker's damage and conditions are play actions with
     the gamemaster named in the sheet's log. Their HP and conditions are never copied.
   - **2024 initiative**: `{"proficiency": n}` adds n × the CR's proficiency bonus to DEX
     (the XMM Adult Red Dragon gets +12, as printed); `{"advantageMode": "adv"}` rolls
     with advantage. A concentration save uses the statblock's CON save, else CON.
   - A manual reorder only applies within a **true tie** (same count, bonus and kind).
   - A refused change leaves the record untouched (changes are applied to a copy).
   - Monster rolls at start are not logged one by one; the start says it.
2. ~~**The tracker page.**~~ — **done 2026-10-17**: `app/routes/combat.py`,
   `app/ui/combat_pages.py`, `static/js/combat.js`; "Combat" in the navigation lists running
   and finished fights. "Run" on the encounter builder and on every `::: encounter` embed
   (or the way into the fight already running). As built:
   - **The board is the unit swapped**, not one row: a changed initiative re-sorts the
     order, so every button returns `#combat-board` whole. Without JavaScript every form
     posts and redirects back, errors carried as `?error=<key>`.
   - Live: the combat publishes its revision on its id; a character in a running combat
     also publishes there as `"<combat>|<character>"`, so a player's own change re-renders
     the tracker. A second device followed "Next turn" in 0.9 s. A half-typed amount
     holds the refresh until the field is left; an open row menu stays open.
   - A group reads "Goblin ×4" in the turn pill. Characters without initiative show a
     dashed amber field and are named under the round head.
   - Rarer actions (conditions with rounds, concentration, edit, act alone, hide, remove)
     sit in a row menu.
   - The side panel shows the selected creature's statblock in three ability columns.
   - Hand edits of `combats.json` reach open trackers through the watcher.
3. ~~**Characters in the fight.**~~ — **done 2026-10-18**. Rows reading the character
   files and actions through `PlayService` came with steps 1–2; this step added:
   - **The sheet's initiative roll lands in the tracker.** Every character change asks
     `CombatService.take_initiative`, which takes the newest `initiative` entry in the
     character's log made after the start, less `ROLL_GRACE` (5 minutes — the table rolls
     when the gamemaster calls for it, often before "Run" is pressed), and never one from
     before the end of the character's previous fight. **The newest wins**: a value the
     gamemaster types or rolls is stamped (`Combatant.initiative_at`), so an older sheet
     roll does not overwrite it and a newer one does. The combat log names the player.
     Measured: the tracker showed a phone's roll in 0.2 s.
   - A change the tracker itself causes (its damage on a character) is not a roll to
     take, and is skipped without touching the combat again — it already holds the lock.
   - **The party overview at `/gm`** shows each running fight above the cards: a link,
     the round or "rolling initiative", whose turn, and who has not rolled yet. Combat
     changes reach it on the party stream as `"*combats <id> <revision>"`.
4. ~~**The player view.**~~ — **done 2026-10-18**: `app/routes/play_combat.py`,
   `app/ui/play_combat_pages.py`, `static/js/combat-strip.js`. As built:
   - **Its own projection**: `CombatService.player_view` builds `PlayerView` /
     `SeenTurn` / `SeenCombatant` — label, state word, conditions, and which character
     it is. Nothing else exists on it; a test pins the fields, so adding one is a
     deliberate act. Hidden combatants are dropped, and a turn with only hidden members
     disappears.
   - **Who may look**: a player sees a combat one of their characters is in; any other
     id answers 404, like an id that names nothing. The gamemaster sees every one.
   - `/play/combat/{id}`: the round, a banner "Your turn!" or whose turn it is, the order
     with the player's own character marked "You", and a reminder to roll while their
     initiative is missing. Live through the combat's own stream (`combat.js`).
   - **The strip above the sheet**, always present so a fight starting while the sheet
     is open appears: the fight, its round, "Your turn!", and a "Roll initiative" button
     while the roll is missing. Combats publish their revision under
     `combat_key(character_id)` for each character in them, which the strip watches.
   - `/play` lists the fights the player's characters are in.
   - Measured: a turn change reaches the phone in 0.15–0.4 s.
5. ~~**Log and summary.**~~ — **done 2026-10-18**. As built:
   - **The log is part of the board**, folded under the order with its newest entry
     showing; open, it lists the last 60 entries (the record keeps 200) with the round,
     who did it and when. `combat.js` keeps it open across live swaps. Each entry is
     stored as kind + subject + amount + detail and phrased in the reader's language
     (`log_text`); a test renders every kind in English and German.
   - A group's turn is logged as "Goblin ×4", as the tracker names it.
   - **The summary** of an ended fight: rounds, each defeated monster with its XP, the
     total and the share per character, the characters who took part, and any of them
     down at the end. Awarding the XP is §6.
   - **The encounter builder** lists the fights run from it ("Fought before"): when,
     rounds, XP.
   - **A finished fight can be deleted** (asked once); a running one must be ended first.
