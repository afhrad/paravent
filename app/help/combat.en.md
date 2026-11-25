# Running a fight

A gamemaster's walkthrough of the combat tracker, in the order things happen at the table.

The tracker does the arithmetic and remembers everything. **It never decides for you:**
whether an attack hits, who is in the area of a breath weapon, or how a conditional
resistance applies stays your call, and nothing touches hit points until you say so.

---

## 1. Before the fight

1. **Build the encounter** (*Encounters* → *New encounter*, in the table's world): add monsters through
   the search, mark each line **Foe** or **Friend**. The difficulty updates as you go.
2. **Run** it — from the encounter, or from its embed in a chapter. The characters of the
   world join; monsters start at average hit points, one initiative per kind.
3. **Initiative.** The tracker rolls for the monsters. Players roll on their own sheet (the
   strip above it says *Roll initiative*); their result lands in the order by itself, and the
   board shows who is still missing. Type a value into a row to override anything.
4. **Begin** — round 1, and the first in the order is up.

## 2. A monster's turn

Select the monster (tap its name): its statblock opens beside the board — below it on a
phone. Every action you can roll has buttons under it.

**An attack** (*Scimitar*, *Bite*): **Attack**, **Adv**, **Dis**. One press rolls to hit and
the damage together; a natural 20 doubles the damage dice, a natural 1 rolls none. The roll
appears under **Attacks to resolve** at the top of the board:

> Goblin 1 – Scimitar: 17 to hit, 5 damage — [target ▾] **Hit** · **Miss**

Pick the target (the attacker's opponents come first), compare with its AC, press **Hit**
or **Miss**. A target's condition shows in the list with what it means for the attack —
*Paralyzed (advantage; a critical within 5 ft.)*, *Prone (advantage within 5 ft., else
disadvantage)* — and **Critical** applies a hit as a critical, the damage dice rolled once
more. A hit applies the damage: temporary HP first, a concentration save if it was
concentrating, and the target's resistances, immunities and vulnerabilities by damage type.

**A save effect** (a breath weapon, *Thunderwave*): **Roll damage** rolls it once. A pure
condition (*Frightful Presence*, a spider's *Web*) says **Call for saves** instead. The
effect waits under *Attacks to resolve*; open **Choose targets** and set each creature:

- **—** not in the area (the default)
- **Roll save** — rolled with its statblock or sheet bonus
- **Fails** / **Saves** — when it was rolled at the table

Then **Apply**: full damage on a failure, half (or none) on a success, resistances counted,
and the condition put on with its duration ("for 1 minute" is 10 rounds). Creatures immune
to the condition, or that already have it, are skipped. One line per target goes into the
log.

**Players roll their own saves.** While a save effect waits, every player sees *Roll your
Dexterity save* on their phone — without the DC — and taps it if the effect reaches them.
Their total shows next to their name in your list, with **Saves** or **Fails** already
chosen. You still decide who was in the area and press Apply.

**Recharge, legendary and lair actions** are reminded as each turn starts; a recharge has
its own roll button there.

## 3. A player's turn

Players act on their own sheet:

- **Attacks** — rolled on the sheet; in a running fight the damage is rolled with them
  (doubled on a natural 20) and the attack appears under *Attacks to resolve* for your
  **Hit** or **Miss**, the monsters offered first. A 2024 character's **weapon mastery**
  follows the hit: *Topple* rolls the creature's Constitution save against prone at once,
  *Sap*, *Vex*, *Slow*, *Push*, *Cleave* and *Nick* are noted in the log, *Graze* on a miss.
- **Spells that call for a save** — *Call for saves* on the spell (with the slot level
  picked if it can be cast higher: Fireball at 5th is 10d6). It casts the spell and sends it
  to you as a save effect with their spell save DC; settle it as above.
- **Healing** — a healing spell (*Cure Wounds*, *Healing Word*, *Mass Cure Wounds*, *Heal*)
  has a **Heal** button with a choice of characters from the world — several for a group
  spell: the slot is spent, the dice
  rolled (upcast too) and the hit points land on that character's sheet. A healing potion
  in the inventory has **Drink**.
- **Barbarians** switch **Rage** on the sheet (or you do, in their side panel): bludgeoning,
  piercing and slashing damage is halved while it lasts. **Evasion** is applied by itself.
- **Hit points, conditions, death saves** change on their sheet and show on your board
  live — and the other way round when you damage them from the tracker.

Tap a character in the order to see their fighting stats beside the board: AC, saves,
resistances, slots, conditions — with a button to the full sheet.

## 4. Conditions that end on a save

Many conditions say "repeat the saving throw at the end of each of its turns". When such a
creature's turn comes, the board lists it under **Saves to repeat**:

> Goblin 2: at the end of its turn, repeat the Wisdom save (DC 13) to end Frightened — **Roll**

For a character, the player gets the same button on their phone. A success ends the
condition; a failure keeps it until the creature's next turn. Conditions with a round count
end by themselves.

## 5. Shapes and concentration

- **Wild Shape** — a druid takes a form on their sheet (*Actions*). Their row gets a pill
  naming the beast, and their side panel shows the beast's actions with **Attack / Adv /
  Dis**, rolled like a monster's. Under the 2014 rules the form has hit points of its own
  that take damage first; under the 2024 rules it gives temporary hit points. A form casts
  no spells, except Beast Spells from level 18 and the 2024 Circle of the Moon's circle
  spells.
- **Polymorph and True Polymorph** — **Polymorph…** in a row's ⋯ menu turns anyone,
  character or monster, into a creature the world's books allow: no stronger than its
  challenge rating (a character's level), and a beast for Polymorph. Pick the rules (2014:
  the shape has its own hit points; 2024: temporary hit points) and who concentrates on it.
  A caster can do the same from their sheet while concentrating on the spell.
  **Shapechange** works the same for its caster alone (any creature but a construct or
  undead, up to the caster's level), and the caster can still cast in the shape. The row then
  fights as the shape: its statblock, attacks and AC. **Revert** in the ⋯ menu ends it by
  hand. A player can end only a shape their own character cast; one an enemy cast holds
  until its caster's concentration ends, or you end it.
- **Concentration holds the shape.** When the caster's concentration ends — dropped, a
  failed save, a new spell, 0 hit points, or Incapacitated, Paralyzed, Petrified, Stunned
  or Unconscious — every shape the spell held ends with it, logged. A shape that ends by
  itself (its hit points or temporary hit points gone) ends the spell, so its caster stops
  concentrating.
- What the party sees a creature in a shape do counts for the shape in **What the players
  know**.
- **Summoned creatures** — a summoning spell (*Summon Beast*, *Summon Fey*, *Find Steed* …)
  has **Summon** in its cast form on the caster's sheet during a fight, with the kind (Air,
  Land, Water). The creature joins beside the caster, scaled to the slot: AC, hit points,
  attacks at the caster's spell attack, save DCs at the caster's. Its statblock has the
  usual Attack buttons. It vanishes when the caster's concentration ends.

## 5b. Optional rules

In **World settings**, under *Optional combat rules*, you can tick the 2014 guide's variants.
A fight keeps the rules it started with.

<!-- feature: massive_damage -->
- **Massive damage**: a hit of half a creature's maximum hit points or more calls a DC 15
  Constitution save, rolled at once. On a failure the *System Shock* table is rolled and
  carried out: a drop to 0, or stunned until the end of the creature's next turn.
<!-- /feature -->
<!-- feature: injuries -->
- **Lingering injuries**: when a character takes a critical hit or drops to 0, a line above
  the order asks whether to roll one. The injury stays on the character's sheet until you
  end it there.
<!-- /feature -->
<!-- feature: morale -->
- **Morale**: a group that has lost half its number, or a lone creature at half its hit
  points, shows a reminder at its turn: **Roll** its DC 10 Wisdom save, or **Not needed**.
<!-- /feature -->
<!-- feature: mobs -->
- **Mob attacks**: under each attack of a group with two or more standing members, **Mob**
  takes a target and how many attack. The guide's table says how many hit, and their damage
  is applied.
<!-- /feature -->
<!-- feature: side_initiative -->
- **Side initiative**: each side rolls one d20 at the start, and everyone on the higher side
  acts first. Changing a member's initiative changes its whole side's.
<!-- /feature -->

## 5c. Objects and doors

Under **Add to the fight**, add a door from the 2024 guide (its AC, hit points and the DC to
force it open) or an object of a substance and size by the 2014 guide. It stands at the end of
the order, never takes a turn, earns no XP and takes no poison or psychic damage. Attack it and
damage it like a creature; at 0 hit points it is **Broken**. The door's DC is shown where the
initiative would be, for a Strength check to force it.

Siege equipment from your books (a ballista, a ram, a cauldron of boiling oil) joins the same
way, on a side you choose. Select it and its panel shows its attacks: its crew fire it on their
turns with **Attack**, and the hit waits under *Attacks to resolve* like a monster's.

## 6. Ending

**End combat** shows the summary: XP per defeated foe (allies earn none). **Award to …
characters** writes it into each character's file once; a level reached is *offered* to you to grant, never
granted by itself. The fight stays listed on its encounter, with its full log.

**Recap** (on the summary) tells the fight back: who dealt, took and healed how much,
natural 20s and 1s, who went down, the biggest hit and the conditions — with a text block
to copy into your notes for next session's "previously on…". After several fights in one
evening, **The whole session** puts them together.

---

## Good to know

- **A right-click on a row** (a long press on a tablet) opens its ⋯ menu. The same works
  on party cards, list rows and the links in your chapters: a menu of what can be done
  there — open, run, duplicate, move up, delete with Undo, or create a missing link right
  where it stands.
- **The log** (folded under the order) keeps every roll with its dice. Natural 20s and 1s
  are marked green and red there, on the sheet and on your party cards.
- **Dice sounds** play on the device that rolled — rattle, a chime for a natural 20, a
  "wah-wah" for a 1. Each device switches them in the user menu (*Dice sounds: On / Off*).
- **On a phone** the board scrolls under the round head; the statblock is below it.
- **What the tracker does not judge:** cover and hidden bonuses (you compare to AC),
  conditional resistances ("from nonmagical attacks"), special save rules ("becomes
  petrified on a second failure"), upcast damage for monsters, and the 24-hour immunity
  after Frightful Presence. Adjust by hand — the damage field on each row takes any number.
