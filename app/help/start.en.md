# Getting started

From an empty Paravent to your first evening at the table, in the order you need it.

Paravent sits beside your game, not in its place. You still tell the story, roll the dice
you like and decide what happens. It keeps the numbers, the notes and the handouts in
order, so you have more of your attention for the people at the table.

---

## 1. The data you bring

Paravent ships no D&D content. It reads the 5eTools data from a folder you point it at
(`PARAVENT_REFERENCE_DIR`), and never writes there.

Open the settings gear → *5eTools data* and run the check. It lists every file it could
not read and everything that went missing on the way. A clean report means monsters,
spells, items and the rulebooks are all there.

## 2. A world and its books

*Worlds* → **New world**. A world is your campaign: its name, its language, and above
all **the books in play**.

- **Tick the books your table uses.** Choosing *PHB* (2014) or *XPHB* (2024) decides
  the edition. Every reference then resolves to the right printing, and one into a book
  nobody owns shows as "not in your books" instead of borrowing from it.
- Tick nothing, and everything you imported counts. That is fine for a first look.
- **Players** beside a book marks it as one your players may read. The handbooks and
  supplements are ticked for you; the monster books and the adventures are not.

## 3. Your players

Settings gear → *Players* → **New player**: a name to log in with, a password, and a
language. Give each player their login.

The players open the same address on their phones. On an iPhone, *Share* → *Add to Home
Screen* turns it into an app with its own icon. Everyone on the same network, nothing in
the cloud.

## 4. Characters

A player makes their own character: user menu → **New character**. The wizard walks
through species, class, abilities, background, equipment and spells, using only the
books of their world, and explains every number it shows.

Already have a character on paper? *Characters* → **New character** makes an empty file
to fill in by hand; a value the rules cannot work out (a homebrew ring) goes in as an
override.

*Table* is your view of the party: hit points, armor class, conditions, the last roll,
live as the players play. **Everyone up a level** grants a level; each player then
answers only what the new level brings.

## 5. The first chapter

Open your world → **Add adventure** → **Add chapter**. A chapter is plain Markdown:
write it like a note, and Paravent adds the rest.

- `{@creature goblin}` or `{@spell fireball}` becomes a link with a preview. Typing
  `{@` offers the names as you write.
- `> [!read]` starts a box to read aloud, `> [!secret]` one only you see.
- `{@npc Hob Wade}`, `{@place Grey Reed}` or `{@encounter Ambush at the Ferry}` names
  something of yours; the check beside the preview offers to create it.
- Running a published adventure you own? **Start from the book** makes a chapter for
  each of its sections, and your notes go between them.

A box or a picture in a chapter has **Share with players**: it lands in their *Journal*
and stays there.

## 6. The first fight

*Encounters* → **New encounter**, or create one from the chapter. Add monsters through
the search and watch the difficulty change for the party you actually have.

**Run** starts the fight. The players roll initiative on their phones, the tracker keeps
the order, hit points and conditions, and the statblock opens beside it. Whether an
attack hits is always your call. The whole walkthrough is under *How it works* in the
tracker.

## 7. Nice to have

- **Sound:** settings gear → *Sound library* for your rain, taverns and battle music. Open
  the **Speaker** page on the device by the loudspeaker, then play from the desk in the
  top bar.
- **Names:** settings gear → *Names* for naming styles per people, so every innkeeper
  gets a name that fits your world.
- **A look:** *Themes*. Tome looks like the printed books, parchment by day and leather
  at night.
- **Backups:** everything you make lives in one folder, the library. Set
  `PARAVENT_BACKUP_DIR` (and `PARAVENT_BACKUP_AT` for the time) once, and Paravent zips
  it every night; settings gear → *Backups* shows the copies kept and backs up at once.
- **Claude as co-author:** settings gear → *Claude (MCP)* gives your own Claude app a key
  to your worlds, so it can draft an adventure for your group from your books. It stays
  a draft until you have read it and said yes.
- **Translation:** with `PARAVENT_TRANSLATE_API_KEY` (a Claude API key, paid per use)
  Paravent drafts German texts and names for you to confirm. Without it, that is simply
  off.

## If something looks wrong

| You see | It means |
|---|---|
| No monsters or spells | The data folder is wrong; settings gear → *5eTools data* says which |
| A link reads "not in your books" | The world does not declare that book; tick it, or leave it inert |
| A player sees too little | Tick *Players* beside the book in the world |
| Phones cannot connect | Paravent was started without `--lan`, or a firewall blocks the port |
