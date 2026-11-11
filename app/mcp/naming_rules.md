# Researching a naming style

Paravent keeps names as **naming styles**. A style names people of a race and culture, or
one kind of place: settlements, waters, landscape (mountains, forests, regions) or
buildings (inns, taverns, shops, keeps). Whether from a published setting or history, a
style is researched before it is drafted. Your draft reaches the gamemaster's workshop and
makes no names until they accept it.

The gamemaster may ask in plain words ("German names for the dwarven holds of the
Alliance", "taverns of a pirate harbour"). Read from it what the style names, whose names
they are and in which language they should sound; ask when it does not say and it matters.

## 1. Look first

Call `list_naming_styles` with the race, or with `subject` for a kind of place. If a style
already serves what was asked, say so and stop, unless the gamemaster asked for another one.

## 2. Research

If you can search the web, do, and read what you find:

- Look for the people's own naming customs: encyclopedias of the setting, fan wikis,
  sourcebooks, for history onomastic references.
- Collect at least 20 real names per gender, and family or clan names if they have them.
- Find out the structure: how many names a person has, what each means, whether a parent
  or clan is named, how gender shows, titles left aside.
- Note the sounds: which letters and clusters recur, which never appear, how names end.

**For places** look for the toponymy instead: the elements place names are built from and
what they mean (German -dorf, -hausen, -bach; English -ton, -ford, -bury; Norse -vik,
-heim), at least 30 real or canonical names of that kind, and how a name is put together:
one compound word, an article and a phrase ("The Prancing Pony", "Zur Goldenen Gans"),
or a generic word beside it ("River …", "Mount …"). Places have no gender, but a language's
grammar may agree with the sign's gender, as German inns do: give the article as
`by_gender` and the signs as a `list` with `female` and `male`.

**Be honest about the research.** Every address in `sources` is a page you actually read
in this conversation. Never invent an address or cite a page from memory. If you cannot
search, say so to the gamemaster, write from what you know, and put a plain note in
`sources`: "general knowledge, not researched". Do not present a guess as a custom; say
in the summary what is uncertain. A people's real customs deserve the same care: name a
period or region rather than a whole culture, and do not caricature.

## 3. Build the style

- `race` as the setting says it ("Tauren", "Blood Elf"), `culture` the group within it
  ("Sin'dorei", "Shu'halo"); leave `culture` empty only if the race has one custom. A
  style of places may leave `race` empty and name its `culture` (German, Norse), or
  name the race whose places they are (Dwarf for dwarven holds).
- `aliases`: other names of the race, above all the books' species name ("Dwarf" for
  "Zwerg", "Elf" for "Blood Elf" only if every elf at this table is one). An alias makes
  this style serve that species ahead of the books' tables, in every world that uses it.
- `world_id`: the world it is for. A world may limit its names to some styles; once
  accepted, the style joins that world's.
- `language`: empty when the names stand for themselves in any language, as Norse,
  English or Arabic names do; a code ("de") when they are made for one, such as a
  setting's dwarves as its German translation names them. Such a style comes first for
  that language's tables and is never picked by itself for others.
- `subject`: `person`, or a kind of place a style may name instead: `settlement`,
  `water`, `landscape` or `building` (inns and taverns). A place needs no race; its
  `culture` says whose places they are.
- `pattern` follows the structure you found; each `{part}` is defined in `parts`.
- Choose each part's kind by what the source gives:
  - `learned`: new names from pasted examples, at least 10 in each list. The usual choice
    for a fictional people; `order` 2 invents more, 3 stays closer.
  - `elements`: names that compound two parts, when you found the elements.
  - `sounds`: only when examples are too few to learn from.
  - `list`: for family or clan names, and real historical names that are meant to repeat.
  - `by_gender`: fixed text such as son or daughter.
  - `kin`: a parent's name, with a `fallback` part for a person with no parent chosen.
- **Do not hand out a setting's famous characters.** Put the examples of a published
  setting into `learned`, never `list`, and put the famous names in `banned`, so that
  no generated person is mistaken for them. Names and lore of a published setting stay
  its owner's, and this style is for the table's private use.
- `banned` also takes words the samples spell by accident. `max_cluster` 3 suits most
  languages; raise it for a people whose names truly stack consonants.

## 4. Check

`draft_naming_style` answers with twenty samples and any problems. Read them aloud in your
head: would a reader believe them? Would a table tell them apart? If not, change the style
and call it again with `style_id`, which replaces your draft. Two or three rounds is
normal. If most samples start alike, or the style runs out, fix that before moving on.

## 5. Hand over

Tell the gamemaster what it names, the race or culture, three good samples, what you were
unsure of and the sources. They accept it in the workshop (Names, under the settings gear).
Use it only once they have: `generate_names` falls back to the books' default until then,
and says so with `fallback`; for places pass its `subject`.
