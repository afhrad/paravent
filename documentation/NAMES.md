# Names

**Scope:** how Paravent makes names in any setting: for characters, NPCs and the creatures
of an encounter, and for places — settlements, waters, landscape, taverns and buildings.

**Date:** 2026-11-01, refined 2026-11-09. **Status:** built (§7). Decided with the user: one
shared library of styles found by race and culture, not chosen per world; about six starter
styles; a form for normal use with raw text for experts; characters and NPCs first.
**2026-11-09:** a style may be made for one language, which its readers get first; a
creature of an encounter finds its style by itself, and the "Shown as" field rolls a name
with a dice button and a menu of other styles — a group a name for each, and the
tracker names a creature or a whole group; a style may name a kind of place; and a world
may limit its names to the styles it ticks (§5b).

## 1. Two problems

A name has a **structure** (which parts, in what order) and a **sound** (what each part is
made of). A generator of invented sounds cannot write Thorsteinsson, and a table of Celtic
names cannot serve a people that does not exist. So a **naming style** states both: a
pattern for the full name, and how each part in it is made.

Structure differs more than sound does: Norse names compound two elements and name the
parent, Spanish ones carry two surnames, a Nahua calendar name is a number and a day sign,
and a Tamil name may lead with an initial from the father's name.

## 2. A style

```json
{
  "id": "old-norse",
  "name": "Old Norse (saga age)",
  "race": "Human",
  "culture": "Old Norse",
  "summary": "Two elements, then the father's name in the genitive.",
  "pattern": "{given} {father}{suffix}",
  "parts": {
    "given": {"kind": "elements", "first": ["Thor", "Arn"], "second_male": ["stein"],
              "second_female": ["dis"]},
    "father": {"kind": "elements", "first": ["Thor", "Arn"], "second": ["stein"]},
    "suffix": {"kind": "by_gender", "male": "sson", "female": "sdottir"}
  },
  "rewrites": {"bjornsson": "bjarnarson"}
}
```

- **`subject`:** what it names: `person` (the default), or a kind of place —
  `settlement` (cities, towns, villages), `water` (rivers, lakes, seas), `landscape`
  (mountains, forests, regions) or `building` (inns, taverns, shops, keeps). A style names
  one kind.
- **`language`:** the language its names are made for, a code (`de`), or empty for every
  language. Norse, English or Arabic names stand for themselves anywhere; a setting's
  dwarves as its German translation names them sound right only at a German table, so
  that style is marked `de`. How the mark is used: §5.
- **`race`, `culture`:** who it names, free text, either may be empty: how a style is
  found (§5). A setting's own words are fine: Tauren, Shu'halo. A place needs no race;
  its culture says whose places they are (German, Norse).
- **`aliases`:** other names of the race ("Dwarf" for a style of "Zwerg"), so a species
  named otherwise finds it.
- **`sources`:** where its research came from, addresses or a plain note.
- **`pattern`:** `{part}` for a part's value, `{part:initial}` for its first letter. A part
  used twice has one value. A name reads with its spaces tidied.
- **`rewrites`:** literal replacements on the finished name, lower case, for what joining
  pieces breaks (a genitive, a doubled letter).
- **`rejected`:** whole names turned down among the samples, never offered again. A
  rejected Thorstein is that name only; its elements stay in use.
- **`banned`:** letters no name may contain, a sound or a whole word, in any case.
- **`max_cluster`:** the most consonants in a row (accents ignored; an apostrophe, hyphen
  or space ends a run); three of one letter fail it too. 0 checks nothing.
- **Gender** is settled once per name, before any part, so a daughter's name and suffix
  agree. "Any" rolls it, where the style tells the two apart.

## 3. Kinds of part

| Kind | Fields | Built |
|---|---|---|
| `list` | `names`, `female`, `male`: a person's lists; `names` serve anyone | step 1 |
| `elements` | `first` + `second`, `second_female`, `second_male`; never the same word twice | step 1 |
| `by_gender` | `female`, `male`: fixed text | step 1 |
| `sounds` | `groups` (a capital → its sounds, a sound listed twice comes up twice as often), `syllables` (templates: `CV`, `CVn`; other letters stand for themselves), `min_syllables`, `max_syllables`, `ending_female`, `ending_male` | step 3 |
| `learned` | `names`, `female`, `male`, `order` (1 to 4, default 2): a Markov chain over pasted names, at least 10 in each list used; a word the examples hold is tried again | step 4 |
| `kin` | `part`, `fallback`: that part of a chosen parent's name, else this style's `fallback` part, which may stand outside the pattern and cannot be `kin` | step 4 |

A calendar name needs no kind of its own while its number and sign are rolled freely: two
lists. A kind tied to the campaign's date is step 4.

**A person keeps their name's parts** (`given_name`: `text` and `parts`, on NPCs and in
character files) when Paravent made the name. `text` must still equal their name: a
renamed person counts as having none, so their parts cannot speak for a name they no
longer bear. The parent is chosen at the moment of naming, in the generator or the wizard
("Child of", listing the world's people who have parts); **no family link is stored**.

A name where two parts came out the same (Gudvald Gudvaldsson) is rolled again, parent or
no parent; fixed `by_gender` text does not count.

## 4. Where styles come from

| Origin | Key | Lives in | Editable |
|---|---|---|---|
| The books | `Dwarf\|XGE`, `Human\|XGE\|Celtic`, `style:1: Common Names`; `style:` is any of them | Built in memory from `names.json` and the 2024 guide | Never: the corpus is not copied |
| Starters | `starter:old-norse`, `starter:german-settlements` | `data/rules/naming_styles.json`, shipped | Copied first |
| The table's own | `own:<id>` | `library/naming_styles.json`, in the backups | In the workshop or by hand |

Paravent ships no 5e content: starters are hand-written from public naming customs. A
starter names a period or region rather than a whole culture, because "Indian" is not one
system. Six name people; nine name places: settlements (English, German, Norse), waters,
landscape and inns (English and German each). The German ones are marked `de`, so a
German table gets "Eichenhausen" and "Zur Goldenen Gans", an English one "Ashford" and
"The Prancing Goose". German inn names agree with the sign's gender: `by_gender` gives
Zum or Zur, and the sign comes from the matching list.

**Pools.** A key may roll among several styles: `race:Dwarf` among a race's styles of
people, `race:Dwarf|settlement` among its styles of a kind of place, `subject:water`
among every style of that kind. The books' ancestries carry aliases of their own, so a
creature called otherwise finds them: Half-Orc answers to Orc, Gith to Githyanki and
Githzerai. **Their word lists were written from general knowledge and have not been checked
name by name against an onomastic source.** The mechanics come from these sources:

- [Nordic socio-onomastics](https://www.nordicsocioonomastics.org/2023/11/): elements shared in a family.
- [Icelandic names](https://guidetoiceland.is/th/history-culture/10-questions-about-icelandic-names-answered): a parent's name, not a family name.
- [Patronymic](https://myheritage.com/wiki/Patronymic): Spanish surnames, Arabic ibn and bint.
- [Aztec glyph database](https://aztecglyphs.wired-humanities.org/content/ce-coatl-mh519r): number and day sign; one scholar's reading.
- [Indian names](https://www.behindthename.com/glossary/view/indian_names): initials in South India; a weak source, varying by state.

Sound and quality follow conlang practice: [syllable templates first, clusters
manageable, one or two distinctive features](https://www.typingmind.com/skills/jwynia-conlang),
[Markov order 2 or 3, and too little variety repeats the examples](https://pypi.org/project/namemaker/).

## 5. Using it

`Naming` (`app/services/naming.py`) lists styles and makes names; `NpcGenerator` goes
through it. `names(key, gender, count, avoid)` answers distinct names, leaving out those
given, and fewer than asked when a style runs out. A name a style's checks or rejections
refuse is rolled again. `spread=True` starts names with different letters while it can,
then lets them repeat: Claude's `generate_names` uses it, so a cast reads apart.

**Finding a style.** A world has no naming scheme: a style belongs to a race and culture,
and so does a person. `Naming.find(race, culture, edition)` answers, in order:

1. a style of that race **and** culture;
2. a style of the race, any culture (`race:Tauren` rolls among them); a culture asked for
   and not found marks this a fallback;
3. a style of that culture, any race, a fallback if a race was asked for;
4. the books' default for the edition, then the starters, a fallback if anything was
   asked for at all.

**The reader's language.** `find` and every roll take the reader's language: the
gamemaster's or player's interface language, and for Claude the world's `language`.
Styles made for it come before those for every language, and those made for another
language are left out of the search altogether: an English table never gets a German
style by itself, but can still choose it by hand. A pool rolls among the styles of the
best kind it has: made for the language, else for every language, else the rest. Language
outranks origin: a German starter comes before the table's own unmarked style for a German
reader, so mark your own style `de` if it should win.

A race matches a style's `race` or its `aliases`, and **the table's own styles are tried
first at each step**: otherwise the books' Dwarf tables, whose race is exactly "Dwarf",
would always beat a Zwerg style with that alias. As styles belong to no world, an alias
applies in every world. Everything case-insensitive. `FoundStyle.fallback` lets the page and Claude say so instead
of quietly giving a Tauren a Celtic name.

**A creature in an encounter** finds its style by itself, nothing stored per monster
(`Naming.for_creature`, `creature_races`): the name it is shown under (a reskin as
"Murloc"), its subtype tags ("orc" of *humanoid (orc)*), its own name, then each word of
the name, the first that a style serves as race or alias wins; else the default, marked as
a fallback. So an Orc War Chief finds an Orc style through its tag, a Goblin Boss a Goblin
style through its first word, and a style's aliases can take creature names ("Flind" on a
Gnoll style). In the builder, the **"Shown as"** field of each line holds a dice button:
one press rolls a name in the fitting style, in the reader's language, and saves it; a
small arrow opens a menu of every style of people, whose choice rolls instead, and stays
chosen for the next press. Names already in the world (its NPCs, characters) or on another
line are not given twice. **A group** gets a name for each, comma-separated ("Gruk,
Ugrash, Torg", as many as fit the field, starting with different letters):
`EncounterMonster.names` reads such a label as names, `reskin` as one name for the kind,
and a fight started from the plan gives the names out in order, numbering any members
left over ("Goblin 1"). A trailing comma keeps a single name a name ("Gruk,").

**In the combat tracker** a creature's row menu has the same dice in its label field
(`CombatService.roll_names`), and a group's menu has **Name each**, which names every
member of the group differently; both avoid names already in the fight and swap the board
in place. Both buttons submit the field's
form, so they work without JavaScript, and Enter still saves what was typed; with HTMX
the answer is the line alone, swapped in place, so the page neither reloads nor scrolls.

**A place** (its edit form) has the same dice in its name field, with a menu of the
kinds of place, each "any style" first: the dice roll a settlement, the menu anything
else. The rolled name replaces the field in place (HTMX), nothing saved and nothing else
touched; without JavaScript the form comes back with everything else as typed.

**An NPC** has `race` and `culture`, in its form and offered from what exists, and the
generator starts from their style, saying when the default stands in. An NPC with neither
takes those of the style that named it. **A character** has its species: the wizard's
"Suggest a name" uses species and subspecies, or any style chosen there.

**The workshop** (`/gm/names`, settings gear; `app/services/naming_workshop.py`) has to
hold hundreds of styles, so it draws only what was asked for. Its index shows every race
of people with its count and cultures, then the kinds of place, sampling nothing, and what
Claude proposed on top (with "Accept all"). The form sets what a style names and its
language; a style made for one language shows its code beside it. A race, or a search over name, race, culture and summary, lists at most 100
styles with three examples each. A book's style or a starter can be sampled; a starter can
be copied, under its name in the reader's language. The table's own has a page of thirty
samples, each with a reject button, and is written in a form (one blank part row always
waits at the end, and "Try it" samples what the form holds, unsaved) or as raw JSON, where
an `id` is ignored. A form's refusals are in the reader's language; a raw text's structural
errors quote the parser. Samples warn when fewer than thirty different names came out, or
when 40% or more start with one letter.

### 5b. A world's styles

**Decided with the user 2026-11-09**, reversing the 11-01 "no choice per world": a world
may name the styles its names come from, so a World of Warcraft campaign rolls only its
WoW styles and never a Tolkien elf. Race and culture still find the style (§5); the
world's choice only narrows what they find among.

- `World.naming_styles`: keys of styles (`own:…`, `starter:…`, a book's table or
  ancestry), kept in `world.md` as `names: [own:5a2b…, starter:old-norse]`. **Empty means every style**, like an empty book list, so nothing needs
  configuring; keys of styles since deleted are ignored, and if none are left the world
  has every style again.
- `Naming.within(keys)` is a view that sees only those styles: its entries, the race and
  place pools built over them, `find`, `for_creature`, the default and every menu. A key
  outside it is unknown (`NamingError`), so a style from elsewhere is refused, not rolled.
  `StoreRegistry.naming_for(world_id)` hands it out.
- The default, for what no style serves (a wolf), is a human style: the books' human
  table, else "any" of a race that is Human or has the alias Human ("Mensch" with the
  alias), else the first style there is. Give a world's own humans that alias.
- Used wherever a world is known: the NPC generator and the NPC form's races, the wizard's
  "Suggest a name" (the character's world), the encounter builder's "Shown as", the
  tracker's dice, a place's name, and Claude's `generate_names`. The workshop stays the
  whole library, but can keep to a world (below).
- **The screen**: "Names in this world" on the world's page (`/gm/worlds/{id}/names`), a
  checklist like the world's books — people by race, those of no race, then each kind of
  place — with the language and origin of each; a group opens when one of it is ticked.
  Save keeps the ticks; "Use every style" clears them.
- **The workshop in a world** (`/gm/names?world=`, the world menu beside its search): the
  races, kinds of place and search keep to the world's styles, and a panel says how many
  it has chosen and which peoples of its NPCs and characters no style serves yet (their
  names come from the default) — each with a new style of that race.
- **One people, two names**: with the world's language not English, the glossary's races
  (Dwarf → Zwerg) join a people met under both names into one row, named in the world's
  language with the English as an alias, which a new style or Claude's draft takes along.
  When a style of the table's in the world serves the people under its other name, the
  row offers to add the name it was met under as that style's alias, one click.
- **Draft with Claude** (`app/services/style_drafts.py`, with the machine translation's
  key; hidden without one): Claude researches the people with web search
  (`web_search_20260209`) and hands the style to a `draft_naming_style` tool shaped like
  the MCP one, guided by `naming_rules.md` and the parts' JSON; each call is answered with
  samples and their problems, so it revises (at most four drafts, a pause of the search
  loop resumed). It is told the world's name and language, its setting (the instructions
  for Claude), the styles the world uses and the names of that people already in play.
  The run is in the background, one per world and people, kept in memory; the panel
  reloads itself while it runs and then links to the draft, which is `for_world`.
  **Draft all** (2026-11-11, `/gm/names/claude/all`): with two or more peoples still
  without one, one button starts a draft for each, skipping those already drafted or
  drafting and those a style of the table's would serve given an alias (that offer is
  quicker). At most three runs ask Claude at once (`CONCURRENT`); the others wait for a
  slot, their rows saying so, and each row shows its own result.
  **Over MCP** (2026-11-11): `unserved_peoples(world_id)` gives Claude the same list,
  with the alias a style of the table's could take and the drafts already waiting, so it
  drafts styles for a world's peoples while it writes (`draft_naming_style(…, world_id)`).
- **Claude's styles for a world**: `draft_naming_style(…, world_id)` marks the draft
  `for_world`; accepting it adds it to that world's styles when the world limits them (a
  world with every style has it already), and clears the mark. A replacement keeps the
  mark; the raw text never shows it. `set_world_naming_styles(world_id, keys)` lets
  Claude choose a world's styles outright (styles only, not `race:`/`subject:` pools;
  empty for every style), telling the gamemaster what it chose.

## 6. MCP

| Tool | Does |
|---|---|
| `list_naming_styles(race?, culture?, subject?, world_id?)` | Styles with key, name, race, culture, subject, language, kind, summary, three examples; with `world_id` only the world's (§5b) |
| `naming_style_rules` | The research rules below; also the resource `paravent://guide/naming-styles` |
| `generate_names(world_id, style?, gender?, count?, race?, culture?, parent?, subject?)` | Names from the world's styles (§5b) for a race and culture or in a style, or for a kind of place, none already an NPC, character or place of the world, the world's language preferred; `fallback` when nothing served them |
| `draft_naming_style(name, pattern, parts, …, subject?, language?, style_id?, world_id?)` | Proposes a style, answering with samples and what they show is wrong; `style_id` replaces a draft of Claude's; `world_id` adds it to that world's styles once accepted (§5b) |
| `set_world_naming_styles(world_id, keys)` | The styles a world's names come from; empty for every style (§5b) |

`generate_names` also takes `parent`, an NPC or character of the world, and answers with
each name's `parts`, which `create_npc` keeps as `name_parts`, with `race` and `culture`.
Everything but the drafting only reads. Claude writes only into its own drafts (MCP.md), and a style is shared by
every world, so drafting one from a description (step 4) leaves it for the gamemaster to
save.

**From a description** (2026-11-09): the prompt `new_naming_style(description?, category?,
race?, culture?, setting?, language?)` takes the gamemaster's own words ("German names for
the dwarven holds of the Alliance", "harbour taverns of a pirate coast") and a category —
`person` or a kind of place — and opens with that request, then the research rules. With
no category Claude reads it from the description and asks when it cannot tell. It drafts
through `draft_naming_style` with `subject` and `language`; the workshop lists the draft
with what it names and its language.

**Research rules** (`app/mcp/naming_rules.md`, the prompt `new_naming_style`): an LLM may
draft a style from the internet. For places it reads the toponymy instead: elements and
endings, at least 30 names of the kind, how a name is put together, and a language's
agreement with a sign's gender. It looks for an existing style first; reads the people's
customs, at least 20 names per gender; lists in `sources` only pages it actually read, and
says so plainly when it could not search; builds fictional peoples from `learned` parts
and bans the setting's famous characters, so nobody generated is mistaken for one; checks
the samples and refines its draft; and hands over to the gamemaster, who alone accepts.

## 7. Build order

1. **Built 2026-11-01.** The format, `list`, `elements`, `by_gender`, patterns; the books'
   tables as styles; six starters; the NPC generator through `Naming`; the MCP tools.
2. **Built 2026-11-01.** The workshop under the settings gear (form, raw text, 30 samples,
   rejecting samples), copying a starter, a style per species or region in a world, the
   wizard's Details step.
3. **Built 2026-11-01.** `sounds`, and checks: unpronounceable clusters, banned sounds and
   words, names in a group that start alike.
4. **Built 2026-11-01.** `learned`, `kin` with name parts stored on a person, Claude
   drafting a style. A kind tied to the campaign's date (§3) and family links stay unbuilt.
5. **Built 2026-11-01.** Race and culture on styles and NPCs, replacing each world's
   choice; the workshop as a race index with search; the fallback; research rules for MCP.
6. **Built 2026-11-09.** `language` and `subject` on a style; nine starters for places;
   creatures finding their style; the dice and menu in the encounter builder's "Shown as"
   (a group's members named one by one, in the tracker too) and in a place's name; `subject` on the MCP tools, the world's language for Claude.
7. **Built 2026-11-09.** A world's styles (§5b): `World.naming_styles`, `Naming.within`,
   the "Names in this world" screen, every roll and menu of a world held to it; the
   workshop kept to a world with its unserved peoples; Claude's drafts for a world and
   `set_world_naming_styles`.
