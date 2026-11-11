# Running a fight

A gamemaster's walkthrough of the combat tracker, in the order things happen at the table.

The tracker does the arithmetic and remembers everything. **It never decides for you:**
whether an attack hits, who is in the area of a breath weapon, or how a conditional
resistance applies stays your call, and nothing touches hit points until you say so.

---

## 1. Before the fight

1. **Build the encounter** (*Encounters* → a world → *New encounter*): add monsters through
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

## 5. Ending

**End combat** shows the summary: XP per defeated foe (allies earn none). **Award to …
characters** writes it into each character's file once; a level reached is *offered* to you to grant, never
granted by itself. The fight stays listed on its encounter, with its full log.

**Recap** (on the summary) tells the fight back: who dealt, took and healed how much,
natural 20s and 1s, who went down, the biggest hit and the conditions — with a text block
to copy into your notes for next session's "previously on…". After several fights in one
evening, **The whole session** puts them together.

---

## Good to know

- **The log** (folded under the order) keeps every roll with its dice. Natural 20s and 1s
  are marked green and red there, on the sheet and on your party cards.
- **Dice sounds** play on the device that rolled — rattle, a chime for a natural 20, a
  "wah-wah" for a 1. Each device switches them in the user menu (*Dice sounds: On / Off*).
- **On a phone** the board scrolls under the round head; the statblock is below it.
- **What the tracker does not judge:** cover and hidden bonuses (you compare to AC),
  conditional resistances ("from nonmagical attacks"), special save rules ("becomes
  petrified on a second failure"), upcast damage for monsters, and the 24-hour immunity
  after Frightful Presence. Adjust by hand — the damage field on each row takes any number.
