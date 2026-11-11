# Paravent Markdown, for writing chapters and notes

Chapters — and the notes of NPCs, encounters and treasure — are written in Paravent
Markdown: CommonMark (tables included) plus the additions below. Raw HTML is shown as
text, never rendered. Write in the **world's language**; tag *targets* stay English.

## References to the books: 5eTools tags

`{@creature Goblin}`, `{@spell Fireball}`, `{@item Potion of Healing}`,
`{@condition frightened}`, `{@skill Perception}`, `{@dice 2d6}`, `{@dc 15}`.

- The name is the **English** entry name, resolved in the world's books. Add the book only
  when the world declares several printings and you mean a specific one:
  `{@spell Fireball|PHB}`.
- A label after two pipes shows other text, e.g. in German prose:
  `{@creature Green Hag||Grüne Vettel}`, `{@condition frightened||verängstigt}`.
- Check every name with `search_reference` / `find_monsters` / `get_reference` first. A
  name the books do not have renders grey ("not in your books").
- Never paste or paraphrase a statblock or a spell's text: the tag shows it.

## The campaign's own objects

`{@npc Name}`, `{@place Name}`, `{@encounter Name}`, `{@treasure Name}`,
`{@character Name}`, `{@chapter Title}` — by the object's name, nearest scope first (chapter, adventure, world).
Slot 2 is a label: `{@npc Barthel Grimm|the reeve}`.

- **Inside a sentence** a tag is a link.
- **Alone on its own line** (a paragraph that is only the tag) it is the object itself:
  `{@encounter Bog Corpses}` shows the encounter card with its creatures and a Run button,
  `{@npc Agnes Holler}` the NPC card, `{@place Kranwitt}` the place's card,
  `{@treasure Hag's Hoard}` the treasure,
  `{@creature Green Hag}` the statblock folded into the text. Put an encounter's card where
  the fight starts, an NPC's where they enter.

A `{@sound Name}` inside a sentence is a button that plays a sound or scene of the sound
desk on the table's speaker: `{@sound Crowded Pub}`. `list_sounds` names them; there are
none in the books, and a name that does not exist shows as missing.

## Callouts

```
> [!read]
> Read-aloud text for the players.

> [!secret]
> What only the gamemaster knows.

> [!sidebar] Running the reeve
> Advice for the gamemaster.

> [!abstract]- Agnes Holler – the accused herbalist
> Folded shut (`-`), opened by a click: good for NPC dossiers.
```

What else the books print:

```
> [!variant] Fast Pursuit
> A variant rule, titled "Variant: Fast Pursuit".

> [!quote]
> Who enters the forest leaves it as someone else.
> — Marshal Dughan

> [!pullquote]
> The banner is not lost. It waits.

> [!flowchart]
> - **Find the trail.** Wisdom (Survival) DC 12.
> - **Follow it.** On a failure, a random encounter.

> [!handout] A crumpled letter
> A letter or note the party finds, to hand to the players.
```

A quote's last line starting with `—` names who said it. `[!gallery]` lays its images side
by side. Also `[!info]`, `[!tip]`, `[!warning]`, `[!danger]`, `[!example]`, `[!question]`,
`[!note]`, `[!success]`, `[!failure]`, `[!todo]`, `[!bug]`; `+` folds one open.

## Headings and links

- `#` is the chapter's first section level (the title comes from the chapter itself).
- `[[#Heading]]` links a heading in the same chapter, `[[#Heading|label]]` with a label,
  `[[Chapter title#Heading]]` one in another chapter of the world.
- A newline is a line break; a blank line starts a paragraph.

## Images

`![Map](media/map.webp)` or `![[map.webp|400]]` — only images that exist in the world's
folder. Do not invent image paths. Effects ride along as words: `![[tavern.jpg|torn sepia]]`
(`torn`, `fade`, `oval`, `sepia`, `aged`, `mono`, `banner`).
