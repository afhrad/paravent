# Character Generation Wizard — UX Specification

**Scope:** the step-by-step flow that guides a player through creating a character, on a phone
first and a desktop second.

**Companion documents.** [CHARGEN.md](CHARGEN.md) establishes *what the data supports* and where
the gaps are. This document specifies *what the player sees and does*. [DESIGN.md](DESIGN.md) §2
owns the tokens, §4 the components. Visual reference:
[`design-reference/13-chargen-wizard.html`](../design-reference/13-chargen-wizard.html).

**Date:** 2026-09-14, decisions 2026-10-18 · **Status:** built (§8 steps 1–7, 2026-10-18). What is not yet asked is listed at the end of §8.

---

## 0. Decisions (2026-10-18)

| Question | Decision |
| --- | --- |
| Who starts a character | **Both.** A player taps "New character" on `/play` and picks a world; the gamemaster can start one for a player from `/gm/characters`. The world fixes the books, so the options. |
| Starting level | **Any level, 1–20, asked with the class.** The wizard walks every level's choices — subclass, Ability Score Improvements or feats, fighting styles and other optional features, hit points, spells for higher slots. One class at creation; a second class comes with level-up later. |
| Rolled ability scores | **Final by default.** A roll is logged and kept; a per-world setting lets the gamemaster allow re-rolls. |
| Starting equipment | **The class and background packages first** (A/B choices, "any martial weapon" pickers). A 2014 class may roll gold instead; any purse can be spent in the shop. |

Because a character above level 1 has choices no level-1 flow has, the step list grows one
step, **Features**, between the abilities/background pair and Skills (§2): Ability Score
Improvements or feats, fighting styles and other class options, hit points. It is skipped
when the class and level ask nothing — a level-1 wizard — as a non-caster skips Spells.
(Planned as "Levels"; renamed when building, because a level-1 fighter already chooses a
fighting style there.)

---

## 1. The five rules this flow follows

1. **Never ask twice.** Back-navigation is a GET of a stored step. Everything already answered
   stays answered; a closed tab loses nothing (DESIGN.md §4).
2. **Block the impossible, never warn about it afterwards.** A prerequisite that is not met
   disables the option and says why on the option itself. No "you can't do that" after the fact.
3. **Show the consequence while choosing, not after.** Every pick updates the derived panel —
   HP, AC, saves, spell DC — before it is committed. The player learns the system by using it.
4. **The data drives the step, not a per-class branch.** `choose`, `weighted` and `defaultData`
   are walked by one engine (CHARGEN.md §4 build order). A new book must not require new UI code.
5. **A partly built character is a real, saved thing.** It has a name in the list, it resumes,
   and it is never in an invalid persisted state — only an incomplete one.

---

## 2. Two orders, one engine

The 2024 rules moved ability increases and a starting feat onto the **background**, which inverts
the dependency order. The corpus carries both editions, so the wizard branches — it does not pick
a winner (CHARGEN.md trap #1).

**Edition is not asked.** It follows from the world's declared source set (TECHSTACK.md §6c):
`XPHB` present means 2024, `PHB` means 2014. Where a table declares both, the wizard offers both
and the *chosen class* fixes the order — supersession must not silently decide this for the
player (CHARGEN.md §3, G7 note).

| # | 2014 · `PHB` | 2024 · `XPHB` | Why it moved |
| --- | --- | --- | --- |
| 1 | Race | **Class** | 2024 leads with the role you play |
| 2 | Class | **Species** | |
| 3 | **Abilities** | **Background** | 2024 background carries the ASI + origin feat |
| 4 | **Background** | **Abilities** | so it must be known before scores are final |
| 5 | Features *(when the class asks)* | Features *(when the class asks)* | |
| 6 | Skills & proficiencies | Skills & proficiencies | |
| 7 | Equipment | Equipment | |
| 8 | Spells *(casters only)* | Spells *(casters only)* | |
| 9 | Details & review | Details & review | |

Steps 5–9 are identical. Only the first four reorder. Features is **skipped entirely** when
the class asks nothing and Spells for a non-caster, rather than shown as empty steps.

---

## 3. The shell

### Desktop ≥1024px

```txt
┌──────────────────────────────────────────────────────────┐
│ ① Race ✓   ② Class ✓   ③ Abilities   ④ Background   …    │  step rail
├────────────────────────────────────┬─────────────────────┤
│                                    │  Torvald             │
│   step content                     │  Dwarf Fighter 1     │
│   (pickers, tables, inputs)        │  ───────────────     │
│                                    │  HP    12            │  derived panel
│                                    │  AC     16           │  (sticky)
│                                    │  STR +3  DEX +1 …    │
├────────────────────────────────────┴─────────────────────┤
│ ← Back to Class      Continue to Background →   ● Saved   │
└──────────────────────────────────────────────────────────┘
```

The **derived panel is the teaching surface.** It recomputes live on every pick and marks what
just changed. A player who watches AC go 10 → 16 when they take chain mail has learned something
the rulebook took a paragraph to say.

### Phone <768px — the primary target

The rail cannot hold eight labelled steps at 375px, and must not become a horizontal scroll the
player has to discover. It collapses to a **single progress line**:

```txt
┌─────────────────────────────────┐
│ ‹  Step 3 of 8 · Abilities      │   tap the title → step sheet
│ ▓▓▓▓▓▓▓▓▓░░░░░░░░░░░░░░░░░░░░░ │
├─────────────────────────────────┤
│  Standard array                 │
│  15 14 13 12 10 8               │   full-width stacked picks
│  ─────────────────────────────  │
│  Point buy                      │
│  27 points                      │
├─────────────────────────────────┤
│  Dwarf Fighter · HP 12 · AC 16  │   collapsed derived bar,
│                            ⌃    │   tap to expand as a sheet
├─────────────────────────────────┤
│  ←            Continue       →  │   sticky, 44px, thumb-reachable
└─────────────────────────────────┘
```

- **Tapping the step title opens a sheet** listing all steps, completed ones tappable. This is the
  rail, on demand — the same information, not a reduced one.
- **The derived panel becomes a one-line bar** pinned above the footer, expanding to a sheet on
  tap. It stays visible because consequence-while-choosing (rule 3) is exactly as valuable on a
  phone.
- **The footer is sticky and thumb-reachable.** 44px minimum, per DESIGN.md §2.
- Pickers stack full-width; the desktop `.cardgrid` is a single column.

### iPad 768–1023px

The rail keeps numbers and drops labels for incomplete steps; content and derived panel sit side
by side as on desktop. This is the likeliest device for a session-zero at the table.

---

## 4. The steps

Each step below gives its **source** (what corpus data drives it), its **choices**, and its
**validation**. Every list is filtered by the world's source set before the player sees it.

### Step · Race / Species

> **Source:** `races.json` — 157 races + 98 subraces, `_copy` resolved.

Searchable, filterable list. Each card shows size, speed, darkvision and — under 2014 — the
ability bonuses, because in that edition they are the main differentiator.

Choosing a race with subraces reveals the subrace picker inline, not as a new step: it is the same
decision continued. Structured choices (`skillProficiencies`, `toolProficiencies`,
`languageProficiencies`) render as pickers driven by the `choose` block.

**2024:** species carry **no** ability bonuses. The card must not show an empty slot where 2014
shows "+2 CON" — it shows the trait list instead, or a player will think data is missing.

### Step · Class

> **Source:** `class/class-*.json` — 15 classes. Requires corpus Phase 1 (CHARGEN.md G7).

Card per class: hit die, saving throws, primary ability, a one-line role. Choosing it shows
level-1 features immediately, so the choice is concrete rather than a name.

**Subclass is not a separate step at level 1** for most classes. It appears only when the class
grants it at level 1 (Cleric, Sorcerer, Warlock). The trigger is `gainSubclassFeature: true` in
`classFeatures`, computed at load (CHARGEN.md G5) — never hardcoded per class.

### Step · Abilities

> **Source:** `data/rules/abilityscoregen.json` (hand-authored — CHARGEN.md G1; the standard array
> and point-buy budget exist nowhere in the corpus).

Four methods, as four `.pick` cards: **standard array**, **point buy**, **roll 4d6 drop lowest**,
**enter manually**.

- **Standard array** — drag or tap-assign six fixed values to six abilities. Each value is used
  once; the UI enforces that rather than validating it afterwards.
- **Point buy** — a live budget. Cost table parsed from `variantrules.json`'s rendered table, or
  hand-authored alongside G1. The remaining-points counter is `--font-mono` with `tabular-nums`,
  and going negative is impossible, not flagged.
- **Roll** — uses the shared dice service. Rolls are **logged and not re-rollable** by default;
  a gamemaster setting permits re-rolls. The die is a real commitment or it is theatre.
- **Manual** — plain inputs, for imported characters.

The racial (2014) or background (2024) increases are shown as a **separate, labelled column**, so
the player always sees base vs bonus vs total rather than one opaque number.

**The `weighted` choose is its own widget** (CHARGEN.md §2 step 6): 2024's
`{"weighted": {"from": ["int","wis","cha"], "weights": [2,1]}}` is *a distribution to assign*, not
a set to pick from. It renders as "+2 to ___, +1 to ___", not as a multi-select.

### Step · Background

> **Source:** `backgrounds.json` — 157 entries.

2014: skills, tools, languages, equipment, plus the personality/ideal/bond/flaw tables from
`entries` — each with a "roll" affordance beside it (they are rollable tables and the corpus
models them as such).

2024: additionally the **ability increases** (the `weighted` widget above) and the **origin feat**
from `feats`. Under 2024 this step is where a character actually becomes distinct, so it is given
more room than its 2014 counterpart.

### Step · Skills & proficiencies

> **Source:** `skills.json` (36, each mapped to its ability), class `startingProficiencies`, race
> and background grants.

One list, showing for each skill: its ability, its modifier, and **where the proficiency came
from** — class, race, background, or still choosable. Overlap is the common confusion in 5e
character creation; naming the source at the point of choice removes it.

Choices are driven by the `choose` blocks (Fighter: 2 from a list of 8). Already-granted skills
are visible but not selectable, with the source named.

### Step · Equipment

> **Source:** class `startingEquipment.defaultData`, background equipment, `items-base.json`,
> `items.json`.

The A/B choice sets render as radio groups. Two corpus traps handled here:

- **`{"equipmentType": "weaponMartial"}` (or `equipmentTypes`, one of several kinds: the 2024 Monk) is a deferred picker** — it must expand into a filtered,
  searchable item list, not render as literal text.
- **Key casing differs by edition** — lowercase `a`/`b` in 2014, uppercase `A`/`B`/`C` in 2024.
  Match case-insensitively or silently lose options (CHARGEN.md trap #3).

"Gold instead" uses `goldAlternative` and replaces the class's and the background's packages;
only the 2014 classes print one, because the 2024 classes carry gold as a package of their own
(`B: [{"value": 5000}]`). The roll is logged and final unless the world allows re-rolls, and a
roll made for another class stops counting. Whichever way, a **shop** follows on the same step:
every priced mundane item of the world's books, one printing per name (the class's edition
first), by shelf, with a purse that stays in view. Spending past the purse is impossible; a
purse that later shrinks below what was bought sends the step to review. Answers stay in
`choices` (`equipment.gold`: copper and class; `equipment.shop`: one reference per item bought),
so the inventory and coins are derived again like every other list, and leaving the packages
for gold and coming back finds them as they were. A second suit of armour or shield goes in the
pack. Left out of the shop: vehicles, trade goods, treasure, anything with a rarity (so no
Potion of Healing). `value` is in copper throughout; display converts.

AC updates live in the derived panel as armour is chosen — the clearest instance of rule 3.

### Step · Spells *(casters only — skipped otherwise)*

> **Source:** `generated/gendata-spell-source-lookup.json` for list membership; `spells/*.json`
> for the spells. **Never filter by a `classes` field on the spell** — it produces wrong lists
> (CHARGEN.md trap #2).

Two counters at the top — cantrips known, spells known/prepared — each from the class's
`cantripProgression` / `spellsKnownProgressionFixed`, or from the `preparedSpells` formula
(`"<$level$> + <$int_mod$>"`), which needs a small expression evaluator (CHARGEN.md G2).

Spells filter by level, school, casting time, concentration and ritual. Each row previews on tap
using the standard tooltip (DESIGN.md §7) — the same component as everywhere else.

### Step · Details & review

> **Source:** `names.json` (d100 tables for 10 ancestries), `life.json`, `heightAndWeight` on the
> race, `deities.json` for divine casters.

Name (with a roll button), pronouns, appearance, height and weight (rollable from the race's base
+ modifier dice), personality, backstory.

Then the **full sheet review**: every derived value with its derivation shown on tap — "AC 16 =
chain mail 16, no DEX bonus (heavy)". This is the last chance to catch a mistake, and the first
time the character exists as a whole.

**Finish** marks the character file in `library/characters/` as ready (CHARACTER-FORMAT.md).

---

## 5. Validation, prerequisites and honesty

- **Typed prerequisites are enforced.** `{"ability": [{"str": 13}]}` disables the option and
  states the reason inline.
- **`prerequisite.other` is free prose and cannot be machine-checked** (CHARGEN.md G4). It renders
  as a confirmation checkbox the player ticks — the same thing D&D Beyond does. Never silently
  allow it, never silently block it.
- **An unresolvable reference is never a dead end.** If an option references a book outside the
  source set, it renders with the "not in your books" state and its tooltip offers to add the book
  (DESIGN.md §6). The wizard does not pretend the option is absent.
- **Nothing is validated only at the end.** A step cannot be left in a state its own rules reject;
  Continue is disabled with the reason on the button's tooltip, not on submit.

---

## 6. State, persistence and going back

Each step is `POST /chargen/{id}/step/{n}` returning the next fragment; Back is
`GET /chargen/{id}/step/{n-1}`. The draft lives in its own file in `library/characters/` with
`status: "draft"` from the first step onward.

**Changing an earlier answer invalidates what depended on it — visibly.** Changing class after
picking skills does not silently drop them: the affected steps are marked "needs review" in the
rail, and the player is told what became invalid and why. Silent data loss during a back-navigation
is the single worst failure this flow can have.

The choice stack is kept as *chosen options*, not as resolved state, so a re-resolve after a corpus
update or a source-set change re-derives the character rather than corrupting it (CHARGEN.md §4,
`models/pc.py`).

---

## 7. Accessibility

- The rail is a `<nav>` with `aria-current="step"`; completed steps are real buttons.
- Every picker is a labelled radio/checkbox group — `.pick[aria-pressed]` in the reference kit —
  not a div with a click handler.
- The derived panel is an `aria-live="polite"` region so a screen reader hears AC change.
- 44px targets, `tabular-nums` on every number, full keyboard traversal (DESIGN.md §2).
- Dice results are announced, not only animated.

---

## 8. Build order

Done before this plan: `app/models/pc.py` (the draft model, CHARGEN.md G6),
`app/services/character.py` and `sheet.py` (derived stats, G1–G3), corpus Phase 1.

1. ~~**The choice engine**~~ — **done 2026-10-18**, as built below. Option
   lists filtered by the world's books (species with subspecies, classes, subclasses,
   backgrounds, feats, skills, languages, tools); one walker for `choose`, `weighted`,
   `defaultData` (any key casing) and `equipmentType`; typed prerequisites checked,
   `other` prerequisites returned for confirmation. **The level plan**: for a class and a
   level, what each level grants and asks — the subclass level, Ability Score
   Improvement levels, optional-feature counts, cantrips and spells known or prepared,
   the highest spell level. The steps for an edition, which are complete, and which an
   earlier change invalidated.

   As built — `app/services/chargen/` (`tests/test_chargen.py`):
   - **Answers live in `choices`** on the character, one list per question id
     (`"class"`, `"level"`, `"class.skills.0"`, `"asi.4"`, `"featprog.Fighting Style"`,
     `"equipment.class.0"`, `"spells.known"`…). `Planner.apply` writes the character's
     own lists from them; the sheet then derives as for any file. An answer that no longer
     fits is **kept** and its step reads "review", with the reason (CHARACTER-FORMAT.md).
   - `catalogue.py` offers the declared books **without** supersession (a choice, not a
     reference), species and backgrounds in the class's edition when both are declared;
     class spell lists from the generated lookup; deferred equipment pickers expanded.
   - `blocks.py` reads every `choose` / `any…` / `weighted` / `anyFromCategory` block; a
     list of several blocks becomes a "which variant" question first (the 2024
     background's +2/+1 or +1/+1/+1).
   - `prereq.py` checks level, class level, abilities (any alternative), species,
     background, feat, feature, proficiency, spellcasting, spell, exclusive category;
     prose and campaign conditions come back to be confirmed.
   - The level plan reads `classFeatures` (subclass level, Ability Score Improvement
     levels), `optionalfeatureProgression` (lists and `{level: total}` forms),
     `featProgression` (the 2024 fighting style is a feat), and the spell progressions:
     cantrips, spells known, a spellbook (`spellsKnownProgressionFixed`, summed), prepared
     by progression or by the `preparedSpells` formula, the highest slot from the derived
     sheet.
   - Measured: a plan takes ~0.03 s.
   - **Not yet asked** (when this step was built; all since done, see the end of this
     section): the choices a feat brings beyond abilities and proficiencies (Magic
     Initiate's spells), subclass features with options, a species' own spells. Multiclassing is asked when
     levelling up (§9).
2. ~~**The shell, Species and Class.**~~ — **done 2026-10-18**: `app/routes/chargen.py`,
   `app/ui/chargen_pages.py`, `app/services/chargen/drafts.py`, `static/js/chargen.js`.
   As built:
   - **"New character"** on `/play` and on `/gm/characters` opens `/chargen/new`: the
     world (required once any world exists), and for the gamemaster the player. The draft
     is a character file from that moment; `/play` links a draft back into the wizard,
     and `/chargen/{id}` resumes at the step it was left on.
   - **One form per step.** Every change posts it through htmx and the whole form comes
     back — rail, questions and derived panel — so a pick shows its consequence at once
     (a card click to the subrace question: 0.11 s). Focus and a typed filter survive.
     Without JavaScript, Save and Continue post it normally.
   - **Continue is disabled until the step is done**, with the reason beside it; a
     step an earlier change broke shows "!" in the rail and the reason at the question.
   - **Typing, then pressing the footer** (2026-11-02, `chargen.js`): typed text saves when
     its field loses focus, and that save swaps the form, which lost the click on Back. A
     press with unsaved text now keeps the focus; Back saves first and then leaves.
   - Answers of the same step that a change removes (the subrace of a species no longer
     chosen) are dropped at once; answers to other steps are kept for review.
   - **What the books say** (2026-11-01, `Catalogue.about`): each species, subspecies,
     class, subclass and background card ends with the start of its fluff, in the reader's
     language where translated; the chosen one's whole text and picture are folded above
     the cards ("About Wizard"). The card's line is plain text: a card is a label tapped
     to choose it, and cannot hold links. The folded text is drawn by the tag renderer
     (2026-11-12): its references are links (a player's only to what they may read) and a
     reader in metres sees the metric hints. A subrace's fluff is filed as a race,
     `Dwarf (Hill)`.
   - Species, subspecies, classes and subclasses are **cards** (size, speed,
     darkvision, the 2014 bonuses or the 2024 traits; hit die, saves, caster); the level
     is a select; the class's features through that level are listed. **Every other
     question has one generic renderer** — radios or checkboxes, a filter over long
     lists, a grant shown as "already from Elf" and disabled, an unmet prerequisite with
     its reason — so all nine steps already work; the later build steps give them their
     own screens where they need more (point buy, rolling, the shop, spell filters).
   - On a phone the rail folds into "Step 2 of 9 · Class" with a progress bar that
     opens the list; the derived panel becomes a sticky line (HP · AC · speed).
3. ~~**Abilities and Background.**~~ — **done 2026-10-18**. As built:
   - **The four methods as cards.** *Standard array*: a select per ability with the
     six values. *Point buy*: −/+ per ability and "points left"; a step that would
     overspend or leave 8–15 is not offered (the button is disabled, and the server
     ignores it), so going over is impossible rather than flagged. *Roll*: six times
     4d6 dropping the lowest, logged on the character with every die; then assigned
     like the array. Rolling again is refused unless the world allows it — a new world
     setting, `rerolls: true` in `world.md`, a checkbox on the world form. *Manual*:
     number inputs.
   - **Base, bonus and total** in a table below, with the modifier; the bonus is the
     derived score minus the base, so it includes every increase from any source.
   - **Background cards** list skills, tools, which abilities a 2024 background
     raises and its feat, or a 2014 background's feature. The 2024 increase is first
     "+2/+1 or +1/+1/+1", then **one select per weight** ("+2 [CON] +1 [INT]") — a
     distribution, not a set. Its slots keep their places, so an empty +2 slot does
     not turn the +1 into a +2.
   - An answer still being given (one of two skills, one slot of three) is **open**,
     not a problem; only a wrong answer reads "review".
   - Answers posted together with the answer that brings their question (a
     background and its +2/+1) are all kept.
   - The 2014 personality, ideal, bond and flaw tables move to Details (step 7).
4. ~~**Features.**~~ — **done 2026-10-18**. As built:
   - The class's features through the starting level, listed by level.
   - **Each Ability Score Improvement**: two "+1" selects (the same ability twice is
     +2) or a feat select; a feat replaces the increase, and the ability selects grey
     out. Feats whose prerequisite is unmet are listed disabled with the reason. A
     chosen feat's own increase (Resilient's +1) is asked below.
   - **Optional features and feat progressions** (2014 fighting styles, invocations,
     metamagic; the 2024 Fighting Style and Epic Boon feats) list each option with the
     first sentence of its own text; unmet prerequisites are disabled with the reason.
     A spell *filter* in a prerequisite ("a Warlock cantrip that deals damage") is
     shown to confirm, in the corpus's words, rather than failed.
   - **Hit points**: average (with "d10: 10 at level 1, then 6 per level + CON") or
     rolled — one button rolls every level after the first, the first taking the full
     die, logged, final unless the world allows rerolls. Chosen but not yet rolled is
     open.
   - **Not checked**: an increase past 20.
5. ~~**Skills & proficiencies, Equipment.**~~ — **done 2026-10-18**. As built:
   - **Skills**: one table of all eighteen with ability, bonus and **where the
     proficiency comes from** (class, species, background, feat — or the question
     that chose it), the class's armour and weapon training below; then the languages
     and tools given, and the open choices, where anything already given is disabled
     and says by whom.
   - **Equipment**: each class and background choice as cards listing what the set
     holds ("Chain Mail" / "Leather Armor, Longbow, Arrows (20)"), what is always
     given, and a select per item for a deferred picker ("Any martial weapon" ×2).
     "Take the packages" puts them on the character, with the coins; armour, shields
     and weapons are **worn**, so AC changes while choosing (10 → 16 with chain mail).
     The same item twice is one line with its quantity.
   - Pickers leave out the DMG's renaissance, modern and futuristic firearms
     (`age` on the item): they are not starting gear.
   - Expertise is asked in this step since 2026-10-19 (§11).
   - **Gold instead and the shop**: built 2026-10-31 (§4 Equipment).
6. ~~**Spells.**~~ — **done 2026-10-18**. As built:
   - Cantrips, then spells known or the spellbook, then prepared spells, each with a
     running count ("3 / 3") — once full, the other boxes wait. The counts come from the
     class (or, for an Eldritch Knight or Arcane Trickster, the subclass) progression;
     prepared by progression or by the `preparedSpells` formula over the derived scores.
   - **The list** is the class's from the spell-source lookup, plus the subclass's own
     entries — the Eldritch Knight's list, the Hexblade's expanded spells — up to the
     highest slot the derived sheet gives (pact slot level for a warlock).
   - A spellbook caster prepares from the book chosen above; a prepared caster from the
     whole list.
   - Each spell shows its level, school, C (concentration) and R (ritual); level chips and
     the name filter narrow the list; **the name is the standard reference** — hover or
     tap shows the spell's preview (DESIGN.md §7), without leaving the step.
   - The catalogue keeps an index per category, so a list of hundreds of spells renders
     without a scan per option.
7. ~~**Details & review, finish.**~~ — **done 2026-10-18**. As built:
   - Name (required), pronouns, appearance, backstory.
   - **Roll your backstory** (2026-10-26, `app/services/chargen/backstory.py`):
     *Xanathar's* "This Is Your Life" tables (`life.json`) for the class and background
     chosen — why you became one, and the class's own tables (a barbarian's totem, a
     fighter's heraldic sign) — matched by name, so 2024 classes roll on them too.
     Offered only when the world's books include XGE. A roll (one table, or "Roll all")
     saves what was typed, then adds a line to the backstory, which stays the player's
     to edit.
   - **The review**: the derived sheet — HP, AC, initiative, speed, passive Perception,
     spell DC, the six scores with saving-throw proficiency marked — each number opening
     to **its derivation** ("Chain Mail 16 + no DEX (heavy) + Defense +1"); then skills,
     languages, tools, feats, spells and equipment. Steps still open are listed as links.
   - **Finish** replaces Continue on the last step and is enabled only when every step is
     done (and refused by the server otherwise). It sets `status: ready`, starts the
     character at full hit points, keeps the answers in `choices`, and opens the sheet;
     the draft is then no longer reachable in the wizard.
   - Found by walking a whole character: the 2024 Soldier's gaming set picker had no
     options (`setGaming` was unknown), which would have left the step unfinishable — a
     test now walks every starting-equipment picker of every 2014 and 2024 class and
     background, and a picker with no options is not asked. And **2024 characters know
     Common and two standard languages**, a rule stated only in the PHB's prose, now a
     grant and a question on the Skills step.

**Not yet asked** — the remaining gaps, each its own later piece of work:
- ~~The spells a feat or species brings~~ — **done 2026-10-26**
  (`app/services/granted_spells.py`): `additionalSpells` of feats, species, lineages and
  backgrounds, one reader. The Spells step asks a record's list where it offers several
  (a 2024 background's Magic Initiate version fixes it; a 2024 elf's lineage answers it),
  its ability where the player picks, and its spells, filtered by level, class list (in
  the record's own rules), school, ritual and spell attack; a fighter with Magic Initiate
  gets a Spells step for it. Level-up asks the same for a feat taken. Kept in
  `spells.granted` (CHARACTER-FORMAT.md); fixed spells (a tiefling's) come by level.
- ~~Subclass features with options~~ — **done 2026-10-26**
  (`app/services/feature_options.py`): a feature's counted "options" block (Hunter's Prey,
  Totem Spirit and its later aspects, the 2024 Divine Order, Primal Order and Elemental
  Fury) is a question on the Features step, each option with its first sentences; a
  subclass spell list to choose (Circle of the Land, Divine Soul, the Genie's patron)
  too. Level-up asks those of the new level. Kept in the class's `options`; the sheet
  lists only the option taken (before, every option showed as had) and prepares the
  chosen list's spells. The either/or cantrips of the Path of the Giant and the Arcane
  Archer (nameless `additionalSpells` blocks) are asked the same way, kept as the class's
  `options.cantrip` (2026-11-02). Their casting ability is only in prose and not read:
  neither spell rolls.
- ~~The other choices a feat brings beyond abilities, proficiencies and spells~~ —
  **done 2026-11-09** (`app/services/chargen/feat_choices.py`), asked on the Features
  step and on level-up alike: a feat's `optionalfeatureProgression` (Martial Adept's two
  maneuvers, Eldritch Adept's invocation, Fighting Initiate's style, Metamagic Adept's
  two options), kept with the other options; `weaponProficiencies` with a `choose`
  (Weapon Master's four simple or martial weapons), kept in the file's new `weapons`; and
  the choices only the prose names, from `data/rules/feat_choices.json` (Elemental
  Adept's damage type, Strike of the Giants' giant), kept in `choices` and shown beside
  the feat on the sheet. The sheet now counts armor and weapon training a species or feat
  grants outright too (a high elf's longsword, Heavily Armored), and the chosen weapons.
  **A feat taken twice** (2026-11-09): only a repeatable feat (`repeatable` on its record,
  or the rules file's `repeatable` where only the text says so — the 2014 Elemental
  Adept) is kept twice and offered again on level-up; its prose choice is asked once per
  taking, another value each time, and the sheet shows each taking with its own. Its
  ability increase is asked per taking too (2026-11-11): the wizard's later takings have
  their own question (`feat.<feat>.ability2`, `feat_ability_id`), titled "Elemental Adept
  (2)", and each raises a score; level-up always asked each feat it adds on its own.
  **Weapon Master (2024)**: its weapon kind is a play-time choice, made on the sheet's
  Actions pane (`PlayState.mastery_kind`): any simple or martial weapon with a mastery the
  character is proficient with, kept until a long rest opens it again. The sheet and the
  tracker then use that weapon's mastery (`Sheet.masters`), as they use every weapon's
  for a character with Weapon Mastery.
- ~~The 2014 personality, ideal, bond and flaw tables~~ — **done 2026-11-02**: the Details
  step rolls on the tables of the chosen background's own record, in any world that has the
  background; "Roll all" gives two different traits. A 2024 background prints none. A row
  the background's translation holds comes in the reader's language.
- ~~Rolling a name~~ — done as "Suggest a name" (NAMES.md).
- ~~Rolling height and weight~~ — **done 2026-11-02**: from `heightAndWeight` of the
  subspecies, else the species (46 records; no 2024 species has it), a line added to the
  appearance in the reader's units.
- ~~An ability increase past 20~~ — **refused 2026-11-02**, in the wizard and on level-up:
  an Ability Score Improvement or a feat's increase, up to the block's `max` where it has one
  (an Epic Boon's 30). Species and background increases and the scores themselves are not
  held to it.

---

## 9. Levelling up

**Decided 2026-10-18: the gamemaster grants, the player chooses.** Nobody levels by
accident or ahead of the table.

- **The grant.** `pending_levels` on the character (CHARACTER-FORMAT.md) counts levels
  granted and not yet taken. The gamemaster grants one from the sheet ("Grant a level",
  "Withdraw"), or one to every ready character the party overview shows ("Everyone up
  a level"); the card on `/gm` marks a pending level. A draft is not levelled.
- **The player** sees "Level up to N" on the sheet the moment it is granted (the sheet's
  live stream carries it; measured 0.26 s) and opens `/characters/{id}/level-up`.
- **The page asks only what the new level brings**, found by comparing the class's level
  plan at N and N + 1 (`app/services/chargen/levelup.py`) — the same plan creation reads,
  so no class is special-cased:
  - the subclass, at the level it is chosen;
  - an Ability Score Improvement or a feat (and a chosen feat's own increase);
  - the growth of optional features and feat progressions (two more invocations at
    Warlock 5), never offering one already taken;
  - new cantrips and spells — the growth in spells known, two for a wizard's book — up to
    the spell level the new level reaches, never one already known;
  - hit points: the average, or one roll of the hit die for a character whose hit points
    are rolled (logged; final unless the world allows rerolls).
  The new level's class and subclass features are listed, and the derived panel shows
  the character **at the new level** while choosing. The question renderers are the
  wizard's own.
- **Taking the level** (refused until every question is answered): the class goes up;
  the answers change the character's own lists, so it works for a character written by
  hand as well as one from the wizard; current hit points rise by the gain; the log says
  "Wizard 11 · +6 HP"; and the answers are kept as `levels.<N>.<question>` in `choices`,
  `N` the character level, with `levels.<N>.class` naming the class that gained it.
  For rolled hit points, levels without a result are written out as the average first,
  so the new roll lands on the new level.
- **Multiclassing** (built 2026-10-18) is the first question when another class is open:
  "Which class gains this level?", the character's classes first, preselected with the
  first one, then new ones.
  - **Which classes are open**: those of the first class's rule edition, not yet held,
    whose minimum scores the character meets (`rules.multiclass_requirement`). 2014
    classes state them in `multiclassing.requirements` — plain keys all apply, an `or`
    group is any one of its keys (the Fighter's STR 13 *or* DEX 13). 2024 classes state
    none there; the rule is 13 in the primary ability, read from `primaryAbility`
    (alternatives, each needing all of its abilities: the Monk's DEX *and* WIS). Under
    2014 the character must also meet the minimums of every class they already have;
    if not, no class opens and the page says which one holds them. The classes the
    scores keep out are listed with what they need.
  - **A new class starts at its level 1** with the reduced proficiencies of
    `multiclassing.proficienciesGained`: armor and weapons appear on the sheet (which
    now adds a later class's gained proficiencies to the first class's starting ones),
    a skill or tool to choose is asked (a Rogue's one skill), granted tools are written.
    No saving throws, no equipment. Hit points are the new class's die: the average, or
    a roll.
  - **Spells count as if the class were alone** (PHB ch. 6): a Paladin 5 taking a Wizard
    level chooses a first-level spellbook of 1st-level spells, although the shared
    multiclass slots reach 2nd level. The level plan reads a sheet of that class alone.
  - **Switching class drops the answers of the other one**; once the hit die is rolled
    for a class, the class stays (unless the world allows rerolls), so switching cannot
    buy a second roll.
- **Spells that change on level up** (built 2026-10-19):
  - A 2024 bard, sorcerer or warlock keeps its spells as a prepared list that changes on
    level up (`preparedSpellsChange: "level"`): the growth of that list is asked, like a
    known caster's new spells. (Before, only *known* counts were compared, and these
    classes gained no spells on level up.)
  - **Replace a spell** — optional, two selects ("Replace … with …"): for a known-spells
    caster (`spellsKnownProgression`: the 2014 bard, ranger, sorcerer, warlock, an
    Eldritch Knight) and for the 2024 classes above, with a spell of a level the class
    can cast. Left empty, nothing changes; half answered, the other half is owed.
  - **Replace an invocation** — the same, for a class with an `EI` progression, the new
    invocation checked against its prerequisites at the new level.
- **A new feat's own proficiencies** (built 2026-10-19): a feat taken on level up asks its
  skills, tools and languages (Skill Expert's skill), as a new class's do, never offering
  one already held; and its Expertise (§11).
- **Replace a cantrip** (built 2026-10-19) — the 2024 rule: a class with cantrips
  (`cantripProgression`, 2024 edition) may replace one of the character's cantrips with
  another from its list when it gains a level; optional, like the spell swap.
- **Replace a maneuver, a Metamagic option or the Fighting Style** (built 2026-11-09):
  the invocation swap covers every option kind a level adds that may be traded
  (`SWAPPABLE_OPTIONS`: invocations, Battle Master maneuvers — "each time you learn new
  maneuvers" — and Metamagic), the first the level grows; and a 2024 Fighter may replace
  its Fighting Style feat with another on any Fighter level (`swap.style.*`). 2014
  fighting styles are not traded: the rules allow it only through Tasha's optional
  feature.
- **A new feat's other choices** (built 2026-11-09): its maneuvers, weapons and prose
  choice, as in the wizard (§8, `feat_choices.py`); options already held are not offered.

---

## 10. Preparing spells

**Built 2026-10-19.** A caster who prepares after a long rest chooses on
`/characters/{id}/prepare`, linked from the sheet's spells pane ("6 of 8 prepared" —
amber while fewer are prepared than allowed).

- **Who prepares there**: a class whose `preparedSpellsChange` is `restLong` (the cleric,
  druid, paladin and artificer of both editions, the 2024 ranger and wizard), and the
  2014 wizard, which states no change but has a spellbook. The sheet's `preparing` lists
  them (`app/services/sheet.py`).
- **How many**: `preparedSpellsProgression` at the class's level (2024), or the
  `preparedSpells` formula (2014). The 2014 half casters' formula divides —
  `"<$level$> / 2 + <$cha_mod$>"` — which neither earlier evaluator read, so a 2014
  paladin had no count at all; `character.prepared_spells` now evaluates `+ - * /`,
  rounding down, at least 1, and the planner shares it.
- **From what**: a wizard from the spellbook (`known`); everyone else from the class's
  list through the spell-source lookup, up to the highest spell level the class reaches
  on its own (a multiclassed character prepares each class's spells as if it were alone).
- **Always prepared**: a subclass's `additionalSpells[].prepared` up to the class level
  is added to the sheet's spells, marked "Always", left out of the list to choose from and
  out of the count. A subclass with several named blocks (the Circle of the Land's
  terrains) is a choice the player has not made, and is not guessed.
- **The page** is the creation wizard's spell question: level chips, a filter, previews,
  a running count that disables further boxes at the limit. Each tick is saved as it is
  made, so the sheet and the party card follow; past the limit or off the list is refused
  with a message.
- **More than one class that prepares** (built 2026-10-19): each class is its own
  question — "Prepared spells · Wizard" from the book, "Prepared spells · Cleric" from
  the list — to its own limit. `spells.prepared` holds them all; which class prepared
  which is recorded in `choices["prepared.<Class>"]`, and the file's rule that prepared
  spells come from the book admits the spells recorded there (CHARACTER-FORMAT.md).
- **Not yet**: nothing ties preparing to a long rest: the table decides.

---

## 11. Expertise

**Built 2026-10-19.** Expertise doubles the proficiency bonus for chosen skills (and, for
the 2014 rogue, thieves' tools). The sheet already doubled `expertise`; nothing asked for it.

- **Which class features grant it is prose upstream** ("choose two of your skill
  proficiencies"), so it is a hand-authored rules file, `data/rules/expertise.json`, like
  the ability-score constants: the Rogue at 1 and 6 (2014: thieves' tools allowed), the
  Bard at 3 and 10 (2014) or 2 and 9 (2024), the 2024 Ranger's Deft Explorer (one) and
  Expertise at 9, the 2024 Wizard's Scholar (one of Arcana, History, Investigation,
  Medicine, Nature, Religion). A test checks every row against the corpus's class
  features. The TCE ranger's optional Deft Explorer is not in it: optional class
  features from another book are not offered.
- **Feats carry it structured** — `expertise: [{"anyProficientSkill": 1}]` to choose,
  `[{"intimidation": true}]` given — and are read from the corpus.
- **Where it is asked**: in the wizard's Skills step, after the proficiencies it depends
  on, for every grant up to the starting level; on level up for the new level's grants
  and new feats. One reader, `Planner.expertise_picks`.
- **The options** are the proficiencies held — as answered so far, a new class's or a new
  feat's included — less Expertise already held or chosen by an earlier question (a
  Rogue's sixth level cannot pick what the first did). A question never asks for more
  than it can offer; one with nothing to offer is not asked.
- **Stored** in `expertise` as before; on level up recorded as
  `levels.<N>.expertise.<class>.<level>`.

