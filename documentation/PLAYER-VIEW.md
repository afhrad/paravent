# What a player may read

**Date:** 2026-11-03 · **Status:** decided with the user ("as proposed"), built the same day.

Until now a player read everything in the world's books: the Dungeon Master's Guide, the DM
screen, every Monster Manual statblock through search. That also undercut the tracker's
creature knowledge (COMBAT.md §5), which hands out what the party has learned piece by piece.
The gamemaster reads everything, as before.

## 1. Three layers

A player may read an entry when **its kind is player material and its book is a player
book**, or when **it is their character's own**.

### Player books

Each declared book of a world carries a second tick, **"Players may read"**, on the world's
form. Unless the gamemaster has set the ticks, a world's player books are the player's
handbooks (`PHB`, `XPHB`) and the supplements (`XGE`, `TCE`, `MPMM` … — the picker's
*Supplements* group). The guides, the monster books, the setting books and adventures are not;
the gamemaster ticks a setting book when the players should read it. Adventures never are.

Stored as `player_sources` on the world: absent while the defaults apply, the codes ticked once
the gamemaster saves a choice that differs from them.

A player whose characters all live in one world reads in it: their pages resolve there when
the address names no world, so `{@condition prone}` is the 2014 Prone their books print.
A player reads the player books of every world one of their characters lives in; a player with
no character, those of every world; with no world at all, the defaults over every loaded book.

### Kinds of content

Even in a player book, a player reads only what a player chooses or uses:

| | Kinds |
|---|---|
| Readable | classes, subclasses and their features, species and subspecies, backgrounds, feats, optional features, character options, spells, conditions and statuses, diseases, actions, skills, senses, languages, rules, item properties, types and masteries, mundane items and item groups, deities, tables |
| Hidden | creatures and legendary groups, magic items and magic variants, decks and cards, traps and hazards, objects, vehicles, cults and boons, rewards, facilities, psionics, encounter and loot tables, and anything not listed as readable |

Hidden by default: a category added later stays hidden until it is listed.

### What the character has

A spell, feature, feat, species, class, subclass, background or item in one of the player's
character files is readable whatever its book or kind: a Cloak of Protection from the DMG reads
once it is in the inventory. Treasure handed out is in the inventory, so it reads too. A class
or subclass the character has opens its features.

## 2. Where it is held

On the server, at every door, not only in the menus:

- **Reference pages and previews** (`/ref/…`) answer a hidden entry with the same "not found"
  a missing one gets.
- **Links in text** a player sees: a reference to a hidden entry is plain text, without a link
  or a preview. It does not say "not in your books", which would tell that it exists.
- **Search** and the ⌘K palette find only what the player reads.
- **Rulebooks** (`/books`): a player sees and opens only player books. The **DM screen** is the
  gamemaster's; the players' quick reference stays.
- **A reference page's lists** — "Referenced by", "Also in your books" — leave out what the
  player cannot read.
- Creatures reach players only through the tracker's Details (COMBAT.md §5), unchanged.

**View as player:** the gamemaster's reference pages and rulebooks carry a switch that shows the
page as a player of the world would read it (player books only, no character's own things), to
check what a player sees.

Built in `app/services/player_view.py` (`Reader`), handed to each page as `Page.readable`, and
through `RenderContext.readable` to the tag renderer.
