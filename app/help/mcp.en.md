# Writing adventures with Claude

Paravent has an **MCP server**: Claude can read your worlds, the characters and the books,
and write a new adventure into Paravent — chapters, NPCs, balanced fights and treasure —
as a **draft** you review before it is played.

This page shows how to connect **Claude Desktop** and **Claude Code**, how to ask for an
adventure, and what to do when something does not connect.

---

## 1. What Claude may do

- **Read** every world, its books, adventures, NPCs and treasure, the characters, the
  books' creatures, spells and items, and what happened at the table (fights, experience,
  levels).
- **Write only into drafts it made itself.** Claude creates a new adventure with the
  status *Draft* and fills it. It never changes or deletes anything else — not your
  adventures, not your NPCs, not a draft you made by hand.
- **Monsters come from the world's books only**, and every fight is rated by Paravent's
  own difficulty maths for the adventure's characters.
- When you **accept** a draft it becomes *Planned*, and from then on Claude can only read
  it.

---

## 2. Make a key

1. Open the **settings gear → Claude (MCP)**.
2. Give the key a name that says where it is used ("Claude Desktop, laptop") and click
   **Make key**.
3. The key is shown **once**, together with ready-made settings for Claude Code and
   Claude Desktop. Copy them now; Paravent keeps only a fingerprint of the key.

A key acts as you, the gamemaster. Make one key per computer, so you can revoke one
without the others. **Revoke** on the same page cuts it off at once.

---

## 3. Claude Desktop

Claude Desktop's **custom connectors** (*Settings → Connectors → Add custom connector*)
do **not** work for Paravent: they connect from Anthropic's servers on the internet, and
those cannot reach a Paravent on your home network.

Instead, Claude Desktop starts a small **bridge on your computer**, `mcp-remote`, which
talks to Paravent over your network and to Claude Desktop locally.

### What you need

- **Node.js 18 or newer** on the computer with Claude Desktop
  ([nodejs.org](https://nodejs.org), the LTS version). Check in a terminal:
  `node --version`.
- Paravent reachable from that computer — open `http://<your-server>:8000` in its browser.

### Set it up

1. In Claude Desktop open **Settings → Developer → Edit Config**. This opens
   `claude_desktop_config.json`:
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
2. Add Paravent under `mcpServers` — the key page shows this block with your address and
   key already filled in:

   ```json
   {
     "mcpServers": {
       "paravent": {
         "command": "npx",
         "args": [
           "-y",
           "mcp-remote",
           "http://192.168.1.20:8000/mcp",
           "--allow-http",
           "--header",
           "Authorization:${PARAVENT_KEY}"
         ],
         "env": {
           "PARAVENT_KEY": "Bearer pv_your-key-here"
         }
       }
     }
   }
   ```

   If the file already has other servers, add `"paravent": {…}` next to them inside the
   same `mcpServers`.
3. **Quit Claude Desktop completely and start it again.**
4. In a new chat, the tools menu below the message field now lists **paravent** with its
   tools. *Settings → Developer* shows it as running.

Notes on the settings:

- `--allow-http` is needed because Paravent on your network speaks plain `http`. Without
  it the bridge refuses any address that is not `https` or `localhost`.
- The key is in `env`, and the header is written without a space after the colon, on
  purpose: Claude Desktop on Windows breaks arguments that contain spaces.
- `-y` lets `npx` fetch `mcp-remote` the first time without asking.

---

## 4. Claude Code

One command in a terminal on the computer with Claude Code (the key page shows it filled
in):

```bash
claude mcp add --transport http paravent http://192.168.1.20:8000/mcp \
  --header "Authorization: Bearer pv_your-key-here"
```

Check with `claude mcp list`; in a session, `/mcp` shows the server and its tools.

---

## 5. Asking for an adventure

The easiest start is Paravent's prompt **new_adventure**:

- **Claude Code:** type `/mcp__paravent__new_adventure` and give the world, the
  characters, the number of sessions and an idea.
- **Claude Desktop:** prompts of connected servers are offered in the **+** menu below the
  message field — or simply write the request yourself.

A request written yourself works just as well:

> Write a two-session adventure in the Sunken Marches for Tenewaja and Varis Thornwood: folk horror
> in the fens, an innocent accused, a hag behind it. Pick up what happened last session.

What Claude does, in order: it reads the world, the characters and what happened at the
table so far — your **session notes** first (world page → *Session notes*: a few lines per
day of play on what happened, whom the party met, what is still open); plans the story and how much fighting the party can take; creates the
draft with its NPCs, fights (adjusting each until its difficulty fits) and treasure;
writes the chapters; checks every chapter for references that lead nowhere and mends
them; and finishes with a short report.

**Language:** Claude writes in the **world's language** — set it on the world's page
(*Edit*). Names of creatures, spells and items stay English inside the tags; Paravent
shows them translated where the glossary has a name.

You can watch it happen: open the world in Paravent while Claude works — the draft and
its chapters appear and update live.

---

## 5b. Telling Claude about your world

On the world's page, **Instructions for Claude** holds what Claude reads before it writes:

- **The setting** — which world this is and when: lands, cities, peoples, factions, and
  how its races and creatures map onto 5e. "This world is the Sunken Marches, river country
  where the old dam broke; the towns live on ferries …" gives Claude what it needs, and for
  a published setting its name alone lets Claude use what it knows of it.
- **One text per kind** — adventures, chapters, NPCs, encounters, treasure, places: how
  long, how hard, what to name, what to avoid.

Creatures the setting has but 5e lacks become **reskins**: a book creature under the
setting's name — a "Mire Lurker" running a Kuo-toa's numbers. You can set one yourself on any
encounter line (*Shown as*).

## 6. Reviewing the draft

A draft opens with a purple panel at the top:

- the key that wrote it and the characters it is for;
- each chapter with its check — *all references lead somewhere* or the number of
  problems;
- each fight with its difficulty, and all fights' experience together against the
  party's adventuring day, with a warning when it is more than one day;
- how many NPCs and treasures it holds.

Read the chapters, change whatever you like (Claude cannot overwrite your edits — a
change it tries on a text you edited is refused, and it gets your version back), then:

- **Accept draft** — the adventure is *Planned*; Claude reads it from now on, never
  writes;
- **Discard draft** — it goes to the trash like any deleted adventure.

You can also ask Claude to revise the draft while it is still one: "make the second fight
easier", "give the reeve a daughter".

---

## 7. When it does not work

| What you see | What it means | What to do |
| --- | --- | --- |
| Claude Desktop does not list *paravent* | The config was not read, or the bridge did not start | Check the JSON (a missing comma breaks the whole file), quit and restart Claude Desktop. *Settings → Developer* shows the server's state and its log |
| "npx: command not found" / the server fails at once | Node.js is missing or Claude Desktop does not find it | Install Node.js 18+. If it is installed somewhere unusual, put the full path to `npx` in `"command"` |
| The bridge refuses the address because it is not HTTPS | `--allow-http` is missing | Add it to `args` |
| 401 / "a Paravent MCP token is needed" | The key is wrong, revoked, or the header is malformed | Make a new key; check `Bearer ` in front of it |
| 429 / "too many failed attempts" | Too many wrong keys from this computer | Wait a few minutes, then fix the key |
| Connection refused / timeout | Paravent is not reachable at that address | Open the same address in the browser; check host, port and the server's firewall |
| "… is not a draft written through Paravent's MCP server" | Claude tried to change something that is not its draft | Intended — ask it to create a new adventure, or to revise its own draft |
| Chapters come out in English | The world's language is English | Set the world's language and ask again |

---

## 8. Security

- On your network the key travels as plain `http`. Anyone who can read that traffic can
  read the key — keep Paravent on a network you trust, or put it behind HTTPS.
- A key can do what the MCP server allows and nothing more: read, and write into its own
  drafts. It cannot log in to Paravent's pages, change players, or delete your work.
- Revoke a key you no longer use — on the key page, *Revoke*.
