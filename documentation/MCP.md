# Paravent's MCP server — Claude writes an adventure

**Scope:** an MCP server inside Paravent, so Claude (Claude Code, on any machine in the
network) can write a **new adventure for a chosen party in a world**: the story as chapters,
the NPCs, balanced encounters and treasure — stored as Paravent's own records, exactly as
if the gamemaster had made them, ready to review, edit and run.

**Date:** 2026-10-26 · **Status:** all three steps built (§8); a run from Claude Code itself is the gamemaster's.

> **One sentence:** Paravent supplies the facts and the arithmetic — the world, the party,
> the books, the difficulty — and Claude supplies the story; whatever Claude writes lands in
> a **draft adventure** the gamemaster reviews before it runs.

---

## 1. Decisions (the user's, 2026-10-26)

| Question | Decision |
| --- | --- |
| Where Claude connects | **HTTP inside Paravent**: streamable HTTP at `/mcp` on Paravent's own port, a bearer token the gamemaster creates. Claude Code on any machine of the network connects to the deployed server. Not claude.ai in the browser (it needs a public HTTPS address); not stdio |
| What Claude may write | **A draft adventure only.** Claude creates a new adventure with status *draft* and may change only what lies in it — its chapters, NPCs, encounters, treasure. It never edits or deletes anything else. The gamemaster reviews and sets it *planned*, which ends Claude's write access |
| Where monsters come from | **The world's books only.** Every creature is a reference into the declared books (TECHSTACK.md §6c), rated by Paravent's own difficulty maths for the chosen party. No homebrew designs. **Revised 2026-10-28: reskins allowed** — a line keeps a book creature's numbers under the setting's name ("Mire Lurker" running a Kuo-toa) |
| Instructions for Claude (2026-10-28) | **Per world, the setting plus one text per kind** (adventure, chapter, NPC, encounter, treasure, place); not per object, no global defaults. The setting may be the gamemaster's own or a published one |

---

## 2. What a session looks like

The gamemaster, in Claude Code:

> Write a three-session adventure for Lyra, Brunhild and Tamsin in *World of Wirtshaus*:
> folk horror in the fens, an innocent accused, a hag behind it.

Claude, through the tools (§4):

1. **Reads the table:** the world (its books, language, description, adventures so far and
   their NPCs), the party (levels, classes, AC, hit points, what they are good at), the
   Markdown dialect.
2. **Plans** the story and how much fighting it holds (§5).
3. **Creates the draft adventure** with the party attached, then **NPCs** (a statblock from
   the books where one fits), **encounters** (creatures from the books, each rated for this
   party until it sits where the story wants it), **treasure** (items from the books, or
   rolled on the books' tables).
4. **Writes the chapters** in Paravent's Markdown: read-aloud callouts, `{@npc …}`,
   encounters and treasure alone on their line, `{@creature …}` and `{@spell …}` references.
5. **Checks every chapter** with Paravent's chapter check and mends what leads nowhere.

The gamemaster watches it appear: every write goes through the same services as the pages,
so an open adventure page updates live. At the end Claude reports what it made; the
gamemaster reads, edits, and sets the adventure *planned*.

---

## 3. Connection and access

- **Endpoint:** `POST /mcp` (streamable HTTP, stateless, JSON responses), mounted into the
  FastAPI app; the SDK is `mcp` 2.x (`MCPServer`, formerly FastMCP).
- **Tokens** (`/gm/mcp`, under the settings gear, gamemaster only): *New token* with a name
  ("Claude Code, laptop"), shown **once**, stored as a hash in `library/mcp_tokens.json`;
  listed with when it was last used; revoked with one click. A token acts **as the
  gamemaster who made it** — never as a player.
- **No token, a wrong one or a revoked one is a 401**, before any tool runs. Failed attempts
  count in the login rate limiter (`app/services/ratelimit.py`) per client address.
- **The page shows the command to paste:**

  ```bash
  claude mcp add --transport http paravent http://<host>:8000/mcp \
    --header "Authorization: Bearer <token>"
  ```

- **Claude Desktop** (2026-10-26): its *custom connectors* connect from Anthropic's
  cloud and cannot reach a server on a home network, so Desktop runs the `mcp-remote`
  bridge locally from `claude_desktop_config.json` (`--allow-http`, the key in `env`,
  no space in the header argument for Windows). The key page shows that block filled in
  beside the Claude Code command, and the gamemaster's guide — connecting, asking,
  reviewing, troubleshooting, security — ships in the app at `/help/mcp`
  (`app/help/mcp.en.md`, `mcp.de.md`), linked from the key page.
- **Host checking:** the SDK's DNS-rebinding guard expects `127.0.0.1`; Paravent is reached
  by its LAN name, and the bearer token is what guards it, so the guard is configured from
  the request's own host rather than a fixed list.

---

## 4. The tools

Every tool answers with structured data (Pydantic models, so Claude gets a schema) and short,
plain text. Names and ids are what Claude passes back; ids are Paravent's record ids.

### Reading

| Tool | Gives |
| --- | --- |
| `list_worlds` | Each world: id, name, language, its books (codes and titles, 2014/2024), how many characters and adventures |
| `world_overview(world_id)` | The world's description; its adventures (name, status, summary, level range); its world-level NPCs and treasure by name and role; the bosses |
| `party(world_id, character_ids?)` | Per character: name, species, classes and levels, background, AC, max HP, speed, saves, skill bonuses, passive Perception, languages, notable features and spells by name, the player's notes on who they are. The whole world's characters when no ids are given |
| `search_reference(world_id, query, category?)` | The ⌘K search over the world's books: name, category, book, a one-line gist |
| `find_monsters(world_id, cr_min?, cr_max?, type?, environment?, name?, limit?)` | Creatures in the world's books by the typed filters (TECHSTACK.md: `cr_between`, `of_type`, `in_books`): name, book, CR, XP, type, size, AC, HP, environments |
| `get_reference(world_id, category, name, source?)` | One entry resolved through the world's books, as text: a creature's statblock, a spell, an item. "Not in your books" when it is not |
| `rate_encounter(world_id, monsters, character_ids?, party_size?, party_level?)` | The rating without saving anything: XP, the multiplier (2014) or budget (2024), every band's threshold, the difficulty reached — under the rules the world's books imply |
| `party_budget(world_id, character_ids?)` | One encounter's XP per difficulty for the party and, under 2014 rules, the DMG's *Adventuring Day XP* summed over it (2024 sets no daily budget) |
| `list_naming_styles` | The naming styles, with three examples each; `world_id` for a world's own (NAMES.md §5b, §6) |
| `generate_names(world_id, style?, gender?, count?, race?, culture?, parent?)` | Names for a race and culture or in a style, none already an NPC or character of the world; `fallback` when nothing served them (NAMES.md §5) |
| `unserved_peoples(world_id)` | The peoples of the world's NPCs and characters no naming style of the world serves, as the workshop lists them (English and translated names joined), each with a style that would serve it given an alias, the table's styles for a kind of it ("Night Elf" or "Nachtelf" for "Elf"), and Claude's drafts for it still waiting to be accepted (NAMES.md §5b) |
| `naming_style_rules` | How to research names for a people or a kind of place (NAMES.md §6); the prompt `new_naming_style` starts it from the gamemaster's description and a category |
| `random_encounter(world_id, environment, level)` | A roll on the books' random-encounter tables, its creatures resolved in the world's books (`app/services/encounter_roll.py`) |
| `campaign_history(world_id, sessions?)` | What happened so far: each adventure with its chapters' status and party; per day of play, newest first, the gamemaster's **session notes** (ADVENTURES.md §4b), the fights run (encounter, adventure and chapter, rounds, the defeated, XP, who took part, who went down, the biggest hit — the recap read from the log) and the experience and levels gained. The prompt has Claude read it first, so a new adventure follows on from the table |
| `markdown_guide()` | The dialect (ADVENTURES.md §5) as Claude needs it: tags, embeds alone on a line, callouts, heading links, images; also a resource, `paravent://guide/markdown` |
| `read_adventure(adventure_id)` | An adventure Claude may read — any — with its chapters' text, NPCs, encounters (rated) and treasure |

### Writing — into a draft only

| Tool | Does |
| --- | --- |
| `create_adventure(world_id, name, summary, character_ids, level_start, level_end)` | A new adventure, **status draft**, the party attached (§6) |
| `write_chapter(adventure_id, title, body, chapter_id?, base?, order?)` | Creates a chapter, or replaces one's text; `base` is the version Claude last read, so a gamemaster's edit in the meantime is refused with both texts (ADVENTURES.md, edit conflicts) rather than overwritten. Answers with the chapter check's problems (§4, last row) |
| `create_npc(adventure_id, name, roles, notes, statblock?, chapter_id?)` / `update_npc` | An NPC in the adventure (or one chapter); `statblock` a creature from the world's books |
| `create_encounter(adventure_id, name, monsters, notes, chapter_id?)` / `update_encounter` | Monsters as `{name, source?, count, ally}`, each resolved in the world's books or refused with the nearest names; answers with the rating for the adventure's party |
| `create_place(adventure_id, name, notes, tags?, chapter_id?)` / `update_place` | A place of the draft, `{@place Name}`; its notes name who and what is there |
| `create_treasure(adventure_id, name, items, coins, notes, chapter_id?)` / `update_treasure` | Items from the world's books, coins |
| `roll_treasure(treasure_id, kind, cr)` | Individual treasure or a hoard on the books' tables, added to a treasure of the draft |
| `delete(kind, id)` | Removes something **Claude made in a draft**; nothing else |
| `start_revision(adventure_id)` | A draft copy of an adventure Claude wrote and the gamemaster accepted (§9) |
| `revision_changes(adventure_id)` | What a revision changes against the original |
| `draft_naming_style(name, pattern, parts, …, world_id?)` | Proposes a naming style: a write outside a draft adventure, but inert, offered nowhere until the gamemaster accepts it; with `world_id` it then joins that world's styles (NAMES.md §5b, §6) |
| `set_world_naming_styles(world_id, keys)` | Chooses the naming styles a world's names come from (empty: every style). The other write outside a draft adventure, asked for by the user 2026-11-09; it changes only that list (NAMES.md §5b) |
| `creature_guide()` | How to design a creature: based on the books or blank, placeholders, spellcasting, equipment, what changes the CR (§10) |
| `create_creature(world_id, name, cr, based_on?, …)` / `update_creature(design_id, …)` / `start_creature_revision(creature)` / `ready_made_texts(world_id, kind?, query?)` | A creature in the creature builder from a description, as **Claude's draft**, left out of a publish until the gamemaster accepts it; each answer is the CR it plays at, its statblock and what its text names that the world's books lack (§10) |
| `check_adventure(adventure_id)` | The same check over every chapter and the notes of the adventure's NPCs, places, encounters and treasure, naming where each problem is. Called last, once everything exists: a chapter or note may name something written later |
| `list_sounds()` | The sound desk's scenes and sounds, for `{@sound Name}` (SOUND.md) |
| `check_chapter(chapter_id)` | The chapter check (ADVENTURES.md §5.7): references that lead nowhere with the nearest names, missing NPCs/encounters/treasure, empty encounters, unknown callouts, missing headings and images |

### A prompt

`new_adventure(world, party, sessions, tone)` (with no `sessions`, as many as the world's
instructions name, else one) — the MCP prompt Claude Code offers as
`/mcp__paravent__new_adventure`: the procedure of §2 and the rules of §5, filled in with the
world and party, so the gamemaster starts with one line.

---

## 5. Rules Claude is given

Stated in the server's instructions and the prompt — Paravent enforces what it can (§6):

- **The gamemaster's instructions come first** (built 2026-10-28,
  `app/services/world_instructions.py`, `library/claude_instructions.json`). A world's
  *Instructions for Claude* page (world page → button) holds the **setting** — which world
  this is, its era, lands, cities, peoples, factions, how its races and creatures map onto
  5e — and one text per kind. `world_overview` hands over the fields that say something;
  the server instructions and the prompt tell Claude to follow them and to draw on what it
  knows of the named setting. Claude knows a well-known published setting; the
  brief is what tells it that *this* world is that one, and which part of it.
- **Reskins** (2026-10-28): a creature the setting has and 5e lacks is a book creature
  under the setting's name — `label` on an encounter's monster (`EncounterMonster.label`).
  It is rated, run and statted as the book creature; the builder shows *Shown as* on each
  line, a fight numbers the label ("Mire Lurker 1"), cards and the players see it. What players
  learn in a creature's Details is still the book creature's description.

- **Language:** everything the players or gamemaster read is written in the **world's
  language** (`World.language`); tag targets stay English (`{@creature Green Hag}`), and
  their labels may be localized (`{@creature Green Hag||Grüne Vettel}`).
- **References, never invented rules text:** creatures, spells and items are tags into the
  world's books. Claude does not paste or paraphrase statblocks; the tag shows them.
- **Balance through the tools:** every encounter is rated with `rate_encounter` for the
  adventure's party before it is created, and its notes say what difficulty was aimed at and
  why. Over an adventuring day, the encounters' XP stays within the books' daily budget
  (2014: the DMG's adventuring-day XP; 2024: the sum of the encounters' budgets) — Paravent's
  rating carries the numbers, Claude does the planning.
- **The objects carry the facts, the chapter the story:** an NPC's notes hold who they are,
  what they want, what they know, how they speak; the chapter names them with `{@npc}` and
  shows the card where they enter. An encounter's notes hold tactics, terrain and how it ends.
- **Read-aloud text** in `> [!read]` (or `[!quote]` titled), gamemaster secrets in
  `> [!secret]`, NPC dossiers folded (`> [!abstract]- Name`).
- **Finish with the check:** every chapter `check_chapter`-clean, or the remaining problems
  named in Claude's report.

---

## 6. What the model gains

- **`Status` gains `"draft"`** — "Entwurf" / "Draft", a pill on the adventure; the
  gamemaster sets it like any status. Only a draft is writable through MCP.
- **`Adventure.drafted_by: str | None`** — the token's id that created it. Claude may write
  into an adventure only when it is a draft **and** was drafted through MCP; a draft the
  gamemaster made by hand stays theirs.
- **`Adventure.party: list[str] | None`** — the characters it is written for. An encounter
  in the adventure is then rated against **them**, not every character of the world
  (`EncounterService._party`); the adventure's form lets the gamemaster change it. Useful
  without MCP too: two groups in one world.
- **Scope does the rest.** Everything Claude creates is scoped to the draft adventure or one
  of its chapters (`Scope.adventure_id`, `Encounter.adventure_id`), which is how "only what
  it made" is checked: a write names an object, the object's scope must be a draft drafted
  through MCP. World-level objects are read, never written.

Nothing is deleted when the adventure leaves draft; it simply stops being Claude's.

**Reviewing a draft** (built 2026-10-26). A draft drafted through MCP opens with a panel
over its chapters: the key that wrote it, the characters it is for, each chapter with its
check's problems (or "all references lead somewhere"), each fight with its difficulty,
their adjusted XP together against the party's adventuring day (2014) — with a warning
when they exceed it — the NPCs and treasure counted, and **Accept draft** (it becomes
planned) or **Discard draft** (the delete page).

> **Fixed 2026-10-26:** `party` and `drafted_by` were kept on the record but not written
> to `adventure.md`, so a restart turned a draft into one nobody could write — or review.
> Both are front matter now; a test reloads the store from disk.

---

## 7. Where it lives

- `app/mcp/server.py` — the `MCPServer`, its tools, the prompt and the guide resource; thin,
  as routes are: each tool calls the services.
- `app/mcp/auth.py` — tokens (`McpTokenService`), the bearer middleware.
- `app/mcp/models.py` — the tools' answer models.
- `app/routes/mcp_tokens.py`, `app/ui/mcp_pages.py` — the token page.
- Mounted in `app/main.py`; the session manager runs in the lifespan.

---

## 8. Build order

1. **The server and reading.** The dependency, `/mcp` mounted with token auth, the token
   page; `list_worlds`, `world_overview`, `party`, `search_reference`, `find_monsters`,
   `get_reference`, `rate_encounter`, `markdown_guide`. Tested with the SDK's own client
   against the app, and once by hand from Claude Code.
   **Built 2026-10-26** (`app/mcp/`, `app/services/mcp_tokens.py`, `/gm/mcp`): `mcp` 2.3;
   the SDK's `/mcp` route is taken into Paravent's router behind `BearerGuard`, so there
   is no redirect to `/mcp/`; its session manager runs in the app's lifespan (tests run
   it themselves: it runs once per app, inside one task). `rate_encounter` uses
   `EncounterService.rate_plan`, the builder's own rating. `get_reference` answers with
   the entry's 5eTools JSON, which Claude reads natively. Checked over real HTTP against
   a second server with a scratch library; not yet from Claude Code itself.
2. **The draft.** `Status` draft, `drafted_by`, `Adventure.party` (and encounters rated
   against it); `create_adventure`, `write_chapter`, NPC/encounter/treasure tools,
   `check_chapter`, `read_adventure`, `delete`, the write guard.
   **Built 2026-10-26** (`app/mcp/drafts.py`): the guard is one rule — an object is
   writable when its scope's adventure is a draft with `drafted_by` set — checked by
   every tool before it writes, and a refused write changes nothing (a statblock or a
   creature not in the books is refused before anything is made). Creating an NPC,
   encounter or treasure takes its whole content at once; updating takes only what
   changes, with `base` for NPC and treasure notes and for chapters, so an edit the
   gamemaster made meanwhile comes back as a conflict with the current text. Encounters
   answer with their rating for the adventure's party; `write_chapter` with the chapter
   check. Treasure items not in the books are left out and named, not refused.
3. **The craft.** The `new_adventure` prompt, `random_encounter`, `roll_treasure`, the
   instructions of §5; an end-to-end run on a real world and party; what it teaches folded
   back into the guide.
   **Built 2026-10-26**: the prompt (`/mcp__paravent__new_adventure world characters
   sessions idea`), `party_budget` — the 2014 *Adventuring Day XP* table is now read
   out of the DMG with the other encounter tables (`python -m app.cli rules`) —
   `random_encounter` (any environment's table; its creatures must be in the world's
   books) and `roll_treasure` (the world's edition's tables, into a draft's treasure).
   **A run on a copy of the real library** (scripted through the SDK client, as Claude
   would call it): the world's party read correctly, a draft for Tenewaja and Varis with
   two NPCs, two encounters rated for those two, a rolled CR 5 hoard and a chapter whose
   check came back clean; the chapter page shows every card, callout and folded
   statblock. Not yet: Claude Code itself writing one.

---

## 9. Revising an accepted adventure

**Decided 2026-11-02:** a revision happens on a draft copy, and accepting it **replaces** the
original rather than leaving two side by side. **Built 2026-11-02**, `app/services/revisions.py`.

- **`start_revision`** copies an adventure Claude wrote (`drafted_by` set; not a draft, which
  needs no copy) with its chapters, NPCs, places, encounters and treasure into a new draft,
  or answers with the copy already open. Adventures the gamemaster wrote by hand are refused.
  Every draft tool then works on the copy as it stands.
- **The copy remembers** in `Adventure`: `revision_of`, `origins` (copy's record → original's)
  and `baseline` (a fingerprint of each original as it stood).
- **Renames stay in the copy.** `rewrite_references` is told the adventure; in a revision it
  rewrites only there, and a rename elsewhere leaves revisions alone.
- **The review panel** on the copy lists each record changed, new or removed, its text's
  difference folded. The original shows that a revision waits.
- **Accepting** writes only `FIELDS` back, so ids, portraits, pictures, chapter status and
  covers stay, and with them fights run and links; new chapters and objects are created, what
  the copy dropped is removed; the copy goes.
- **The gamemaster wins.** A record they changed since the copy was made keeps their version
  and is marked; so does anything else they wrote. A removed fight that has been run stays.
- **Discarding** deletes the copy with its records; the original was never touched.
- Pictures are not copied: a revision changes words, not media.

## 10. Creatures

Asked for by the user 2026-11-11: "if I want a lich at difficulty rating 5, it should do so
and also select appropriate spells and equipment." `app/mcp/creatures.py` over the creature
builder (`app/services/creature_builder.py`).

- **`create_creature`** starts a design in a world at a challenge rating, blank or
  `based_on` a creature of the world's books (resolved through its declared books, as an
  encounter's creatures are), and takes every field of the design: size, type, alignment,
  armour and what gives it, hit dice, speeds, scores (merged, so one score can change),
  saves, skills, senses, languages, defences, the five kinds of blocks, the guide's monster
  `features`, `damage_per_round` for damage the text cannot tell (spells), and `fit`, which
  sets armour class and hit dice to the CR table's last. **`update_creature`** changes a
  draft the same way; a block list given replaces that list.
- **Ready-made texts** (2026-11-12): `ready_made_texts(world_id, kind?, query?)` lists the
  builder's traits, actions and monster features (at most 40) in the world's language
  where the table translated them (TRANSLATION.md §6 note), and `ready_made` on both tools
  adds them by name ("Bite (Cloaker)", or a title only one has) after the block lists, as
  the builder's own picker does: translated, named and in the world's units. A feature
  also counts towards the CR, unless `features` names it already.
- **Claude's drafts:** a design made over MCP carries `drafted_by`, the key that wrote it.
  The builder marks it ("Claude's draft") and offers **Accept**; `CreatureService.publish`
  leaves every draft out, so nothing unreviewed reaches `library/homebrew/`. Claude changes
  only drafts; the gamemaster's designs are refused.
- **Revisions** (2026-11-12): `start_creature_revision(creature)` (an id or a name) makes a
  draft copy of an accepted design, `revision_of` its id, under the same name (the only
  name two designs may share; `named` and so encounters and `{@creature}` find the
  original). Claude changes the copy with `update_creature`, but not its name, world or
  picture. The original stays published until the gamemaster opens the copy (marked
  "Revision", with the fields it changes, `CreatureService.changes`) and accepts it:
  `accept_revision` writes the changed fields onto the original (its id, name, picture and
  encounters kept), deletes the copy and publishes. Deleting the copy discards it; deleting
  the original deletes its copy.
- **The answer is the builder's working:** the CR it plays at by the guide's method with
  its defensive and offensive CR and an `advice` when they are more than a step from the
  target, hit points with their formula, armour class, damage per round, attack and DC, the
  statblock with its numbers filled in, and every `{@spell}`, `{@item}` or `{@creature}`
  its text names that the world's books lack, with the nearest names (the chapter check's
  own `notes_problems`).
- **Asking first** (2026-11-11): when a request does not say the creature's role in the
  story, how it fights, or whether it may carry a magic item, and the world's instructions
  do not settle it, Claude asks the gamemaster in the chat, with a suggestion for each,
  before drafting; what it decided unasked it names at handover. Questions go through the
  conversation, not MCP elicitation, so they work in every client.
- **A picture** (2026-11-11): `image_url` on either tool, a web address the gamemaster or
  Claude gives. Paravent fetches it once, then (`creature_pictures.fetch`: `http`/`https`
  only, redirects followed, 20 s, at most 20 MB, kept only when its bytes are PNG, JPEG,
  WebP, GIF or AVIF), before anything else changes, so a refused address leaves the draft
  as it was; an empty `image_url` removes the picture. It is stored as the world's picture
  of the creature the design publishes as (`Name|Homebrew`, COMBAT.md §5), so players see
  it in a fight's Details; a rename or another world takes it along, and a design of no
  world keeps it in the library's folder, serving every world. The answer's `picture` is
  where the gamemaster sees it. The builder shows it beside the estimate, to upload or
  remove by hand too.
- **In a draft adventure** (2026-11-11): an encounter's creature may be one of the world's
  own designs not yet published — Claude's draft above all — named as it is
  (`{"name": "Moorhulk"}`, source empty or `Homebrew`), after the world's books. It is
  planned as `Moorhulk|Homebrew` and rated from the statblock the design will publish
  (`EncounterService` asks `CreatureService.statblock_named` for what the compendium lacks).
  The draft's review lists those creatures, and **accepting the adventure accepts them and
  publishes** (`CreatureService.accept_named`), so its fights can run; a revision accepted
  does the same.
- **Units and language** (2026-11-11): `speed` and `senses` are structured, in feet and
  English as the data keeps them; the builder shows and reads them in each reader's units
  (a German gamemaster types 9 m for 30 ft., `feet_from`/`text_in_feet` in
  `app/services/units.py`, the books' 0.3 on a 5 ft. grid). The text Claude writes is in
  the world's language and units, and so is the slam a blank creature starts with
  (2026-11-12, `CreatureService.create`'s `Wording`); senses may be typed in the
  reader's words ("Dunkelsicht 18 m"). A creature of the builder (source `Homebrew`) is
  written in the table's own language, so nothing offers to translate it: no name to
  give on its page, no "not translated" note, no Translate in the tracker
  (`table_written` in `app/ui/translated.py`). In an encounter it opens in the builder.
- **`creature_guide`** (`app/mcp/creature_guide.md`) holds the rules Claude follows: start
  from a close book creature, the placeholders, a Spellcasting trait with spells of the
  world's books at a caster level of about the CR plus two to four, equipment in the armour
  line and the attacks, at most one magic item and only as the treasure instructions allow.
