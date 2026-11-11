# paravent.org

The project's website: one static landing page (`index.html`) and the imprint and
privacy page (`legal.html`). HTML, CSS and a little JavaScript; no framework, no build
tool, no requests to other servers. **This folder runs as it is on any web server** —
nothing outside it is needed.

## Files

| Path | What it is |
| --- | --- |
| `content/en.json`, `content/de.json` | **All the text**, one file per language — this is where the site is written |
| `templates/index.html`, `templates/legal.html` | The pages' structure, with a placeholder for every text |
| `index.html`, `legal.html`, `de/index.html`, `de/legal.html` | The pages, **written by `build.py`** from a template and a text — never edit them; they are committed so the folder runs as it is |
| `assets/img/flags/` | The two flags of the language switch (SVG; emoji flags do not show on Windows) |
| `assets/css/paravent/` | `tokens.css` and `components.css`, **copied from the app** (`static/css/`) by `build.py` — never edit here |
| `assets/css/tome/` | The **Tome** theme the site wears — its tokens, fonts (SIL OFL, licences beside them) and parchment/leather textures, copied from `app/themes/tome/` by `build.py` |
| `assets/css/site.css` | The site's own layer: layout, the folding screen in the hero, reveals, blend effects, and at its end the Tome dressing (Cinzel titles, gilt rules, framed parchment leaves, drop cap) |
| `assets/js/site.js` | Theme switch, reading progress, the folding screen (closed on arrival, springing open, opening fully with the scroll), the headline's rotating words, screenshots enlarged on a click (a click outside, the × or Esc closes), reveals, active section, phone menu |
| `assets/img/` | The logo (copied from the app), the flags, `screens/<lang>/` (light) and `screens/<lang>/dark/` with the screenshots `shots.py` takes, and `share-<lang>.jpg`, the picture a shared link shows |
| `notice.txt` | The licences of what the site carries from others (Lucide icons, fonts, SRD material): the repository's `NOTICE.md`, **copied by `build.py`** and linked from the legal page; never edit here |
| `build.py` | Write, sync, update and pack (below); not shipped, nor are `content/`, `templates/` and `shots.py` |

## Building

```sh
python website/build.py            # sync from the app + update the pages, in place
python website/build.py --dist     # … and website/dist/paravent-website-<version>.zip
python website/build.py --check    # exit 1 when the pages are behind the app
```

`build.py` (standard library only):

0. **Write** — fills each template with its language's text: `templates/index.html` +
   `content/en.json` → `index.html`, + `content/de.json` → `de/index.html`, and the same
   for `legal.html`. A text the template asks for but the file lacks stops the build and
   names it; so does a file that is not valid JSON, with its line. Beside them it writes
   `sitemap.xml` (every page with its other language, dated by the last commit to
   `content/` or `templates/`) and `robots.txt`, which names the sitemap.
1. **Sync** — copies the design tokens, components, the Tome theme, logo and favicons from the app, and
   writes the icons the pages use (`<use href="#i-name"/>`) from `static/icons.svg` into
   each page between `<!-- icons:start -->` and `<!-- icons:end -->`. Use any icon of the
   app's sprite by name; the next build brings it in.
2. **Update**: "What's new" from the git history (commit subjects are written for a
   reader; merges, bumps, the release commit "Paravent 0.1.x" and the like are left out),
   followed by the hand-written items of `updates.pinned` in the content files (the first
   public release), and the facts strip: version, number of tests, date. Each lives
   between `<!-- name:start/end -->` markers.
3. **Pack** — `dist/site/` and the zip, without `build.py`, this README and dotfiles.
   Before zipping, the copy is checked as a visitor meets it: every link, picture and
   `url()` leads to a file, every `#anchor` to an element, every picture has an `alt`
   (empty for decoration), no id appears twice and every tag closes. A problem is
   printed with its page and **no zip is written** (exit 1); pictures over 400 KB and
   other files over 1.5 MB are only named, as worth a second look.

The zip is what goes to the server: unpack it into the web root of paravent.org.
Because every build reads the newest commits, **a deploy always carries the latest
development news** without anyone editing the page.

## Writing the text

Edit `content/en.json` and `content/de.json`, then run `python website/build.py`.

```json
{
  "features": {                                 ← a part of the page
    "title": "Everything a session needs, on one screen.",   ← one text of it
    "items": [                                  ← a list of entries
      {
        "title": "Combat tracker",              ← the entry's title
        "icon": "swords",                       ← its settings
        "text": "Initiative, groups, hit points …"   ← its text
      }
    ]
  }
}
```

- Both files have the **same keys**; only the texts differ. A new entry (a feature, a
  principle, a screenshot) is a new object in the list of both.
- **A text** is a string. **A list of strings** is paragraphs where the page shows
  paragraphs (`why.text`, `legal.text`, the legal page's sections) and lines where it
  shows lines (`legal_page.address`); an entry's `text` may be either.
- In a text: `*italic*`, `**bold**`, `==highlighted==` (the hero's gold ink),
  `[link](url)`, `[[placeholder]]` (marked yellow, to fill in; none is left open at the moment).
- `icon` is any icon of the app's sprite (`static/icons.svg`, without `i-`); the next
  build brings it in. Screenshots: `kind` (`wide`, `window` or `phone`), `file`, `alt`.
- **How an evening goes** is `evening.steps`, one moment each: `time`, `title`, `text`
  (what Paravent does), `table` (what stays at the table, after `evening.table`), and a
  screenshot (`kind` `window` or `phone`, `file`, `alt`; `align: "right"` shows the right
  side of a wide picture). Its pictures come from `shots.py` like the gallery's.
- **Questions** are `faq.items`: `q` and `a` (a text or a list of paragraphs), each a
  question that folds open. Add one to both files; the order is the order shown.
- **The headline's last words** are `hero.title_words`: shown one after the other, the
  **last entry is where it stops** (and what shows without script). `hero.title_prefix`
  comes before them; when it is empty, each entry is a whole phrase ("in your browser",
  "on Linux"; German "im Browser", "unter Linux", "überall" — each needs its own
  preposition). The template's `{{…|lines}}` hands such a list over, `{{…|last}}` its
  last entry.
- Keys starting with `_` (`_about`, `_marks`, `_comment`) are notes for whoever edits,
  ignored by the build. JSON has no other comments.

The templates use `{{section.field}}` for a text, `{{…|p}}` for paragraphs, `{{…|attr}}`
for attributes, `{{#list}}…{{/list}}` once per entry and `{{?key=value}}…{{/key=value}}`
when a value matches; only needed when the structure changes.

## Languages

English at `/`, German at `/de/`, linked with `hreflang`. The flags in the top bar switch
between the same page in the other language, **at the same place**: the section in
view and how far into it go along (session storage) and are restored on the other page,
whose sections share the ids. The choice is remembered (local storage). On a
first visit, a browser whose first language is German is sent from `/` to `/de/`; once a
flag was clicked, the start page no longer follows the browser. "What's new" stays English
on both (the commits are), with German dates and numbers on the German page.

## Screenshots

`website/shots.py` takes them, in English and German and each in the light and the dark
mode, into `assets/img/screens/<lang>/` and `assets/img/screens/<lang>/dark/`. The page
shows the pictures of the mode it is in, and changes them with the theme switch; a dark
picture that is missing falls back to the light one.

```sh
uv run --with playwright python website/shots.py   # both languages, both modes
# --lang de for one language, --mode dark for one mode
python website/build.py --dist
```

For each language it builds a throwaway demo table in a temporary folder, every word of
the table's own written in that language (`TEXTS` in `shots.py`): the world *The Sunken
Marches* / *Die Versunkenen Marschen*, the chapter *The Ferry at Grey Reed* / *Die Fähre
bei Grauried* with its read-aloud box and secret, the encounter *Ambush at the Ferry* /
*Hinterhalt an der Fähre* (goblins, a bugbear, wolves and a troll, rated Hard), the three
example characters from `documentation/examples/`, a fight in round 2 on Lyra's turn made
of real attacks, a handout of the read-aloud box in Lyra's journal, a draft character and
nine quiet sounds on the sound desk. It starts Paravent on it in the Tome theme and
photographs the tracker and the sound desk beside the chapter (wide), the chapter, the
encounter, the character wizard, the names workshop and the fight's recap (window), and
Lyra's sheet, fight view and handout on a phone. It also takes
`assets/img/share-<lang>.jpg` (1200 × 630, the tracker in light), the `og:image` a link to
the site shows in chats and social networks. Only creatures of the **SRD 5.1** appear, and
every text of the world is our own: the site is public.
The SRD's CC BY 4.0 attribution is under the screenshots and on the legal page. Your own
library is never written, and read for two things only: its glossary's German names and
its German text of those four SRD creatures, so the German shots name and describe them in
German. The game master's translation prompts and the sound
desk's "no speaker connected" are hidden in the pictures, and so is the line each class
card of the wizard quotes from its book: that sentence is the publisher's prose and not in
the SRD, so no picture shows it. Keep it that way when adding a screen: a public picture
shows SRD material and our own words, nothing else from a rulebook.

To show other screens, add an entry to `screens.items` in both content files
(`title`, `kind`, `file`, `icon`, `alt`, `text`) and a `snap(...)` line in `shots.py`.

## Keeping it current

The features, philosophy and tech sections are written by hand in `content/`. When a
feature changes what Paravent is — a new area, not a fix — add or adjust its entry in both
`content/en.json` and `content/de.json` (CLAUDE.md asks Claude to do this with such a commit).
