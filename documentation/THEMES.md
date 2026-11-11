# Themes

**Scope:** how Paravent's whole look is exchanged: what a theme is, what it may change,
how it is installed and chosen, and the rules that keep a theme from someone else safe.

**Date:** 2026-11-04 · **Status:** built (`app/services/themes.py`, `app/routes/themes.py`,
`app/ui/theme_pages.py`); Paravent ships one theme, **Tome**.

> **One sentence:** a theme is a folder of stylesheets, fonts and pictures that Paravent
> loads after its own CSS; it changes how pages look, never what they hold or do.

---

## 1. What a theme can and cannot change

Decided 2026-11-04 with the user. A theme works at two depths:

| Depth | File | Changes | Stable? |
| --- | --- | --- | --- |
| **Tokens** | `tokens.css` | Colours for light and dark, fonts, corners, shadows, textures | Yes: the token names are the contract (§3) |
| **Skin** | `skin.css` | Any CSS rule: frames, rules, statblocks, boxes, spacing | **No**: class names follow Paravent's markup and change with it |

Not possible, deliberately: replacing the HTML or running JavaScript. The markup is Python
(htpy) on the server; exchanging it would mean installing someone's code. A theme is a look.

## 2. The folder

```txt
<id>/                     lower-case letters, digits and "-", at most 40 characters
├── theme.json            required
├── tokens.css            the tokens (§3)
├── skin.css              further rules, loaded after tokens.css
├── icons.svg             optional: a sprite replacing Paravent's icons (§5)
├── preview.jpg           optional: shown on the themes page (.webp and .png too)
├── fonts/                what @font-face points at
└── images/               textures and ornaments the CSS points at
```

`theme.json`:

```json
{
  "name": "Tome",
  "version": "1.0",
  "author": "Paravent",
  "description": "An old book on the table …",
  "paravent": "0.1.109",
  "format": 1,
  "modes": ["light", "dark"],
  "theme_color": {"light": "#58180D", "dark": "#1C150F"},
  "license": "Theme MIT; fonts SIL OFL 1.1"
}
```

| Field | Meaning |
| --- | --- |
| `name` | Required. Shown on the themes page |
| `paravent` | The version it was made for; the page says so when it differs, since a skin may then look wrong |
| `format` | The folder format, `1`. A theme naming a newer one is refused |
| `modes` | `["light", "dark"]`, or one alone: the page is then fixed to it and the light/dark switch goes |
| `theme_color` | The browser's frame and status bar per mode, and the installed app's colour |

Unknown fields are refused, like every other record Paravent writes itself.

**Where themes live.** Paravent ships its own in `app/themes/`; the table's are in
`library/themes/` (backed up with the library). A library theme with the same id replaces the
shipped one, so a shipped theme is changed by copying its folder into the library. Installing
is dropping a folder in; the page reads changes without a restart.

## 3. The tokens

`static/css/tokens.css` is the contract: a theme sets any of its custom properties, plus
these the app adds (`static/css/app.css`):

| Token | Default | Used for |
| --- | --- | --- |
| `--font-display` | `--font-ui` | Headings, titles, statblock names, panel titles |
| `--backdrop` | black at 40 % | Behind a dialog |
| `--role-player`, `--role-gm` | blue, orange | The top edge of the bar: who is at the keyboard |

Every colour in Paravent's CSS comes from a token (checked 2026-11-04: no literal colour is
left outside the token blocks), so a `tokens.css` alone recolours every screen.

Write each mode the way `tokens.css` does: light on `:root`, dark twice, under
`@media (prefers-color-scheme: dark)` guarded by `:root:not([data-theme="light"])` and again
under `:root[data-theme="dark"]` (the reader's own switch).

**Readability is the theme's duty.** Text tokens (`--text`, `--text-2`, `--text-3`,
`--accent-text`, `--*-text`) must clear 4.5:1 against `--bg`, `--bg-sunken` and the three
surfaces, `--on-accent` against `--accent-solid`, each `--*-text` against its `--*-weak`.
`tests/test_themes.py` holds Tome to this; a theme author can run the same check.

Font sizes and spacing are still written per rule (388 font sizes), not tokens. A skin
changes them where it needs to; turning them into tokens is the next step if themes want to.

## 4. Safety

A theme comes from someone else, so:

- **No script.** A theme folder is served only as stylesheets, fonts, pictures and text
  (`MEDIA_TYPES`); anything else, hidden files and paths leaving the folder answer 404.
- **Nothing from elsewhere.** Every page carries a Content Security Policy
  (`CONTENT_POLICY` in `app/main.py`): styles, fonts, pictures, media and connections only
  from this server. A stylesheet's `url(https://…)` or `@import` of another server is refused
  by the browser, and the themes page names the file that tries. This also stops CSS from
  sending what it reads anywhere. Scripts are not yet held by it: the pages' inline scripts
  and htmx would need nonces first.
- **An SVG is a picture.** A theme's files are served with their own policy (`sandbox`, no
  script) and `nosniff`, so an SVG opened on its own runs nothing.
- **Fonts travel with the theme.** Paravent makes no network requests; a theme bundles its
  fonts (woff2) with their licence.

## 5. Icons

`icons.svg` replaces Paravent's sprite only when it defines **every** symbol Paravent uses
(`i-<name>`, the names in `static/icons.svg`). An incomplete sprite is ignored and the page
lists what is missing: an icon missing in the middle of the tracker is worse than the old set.
A new icon in Paravent therefore makes an older theme's sprite incomplete, until it is added.

## 6. Choosing

`/themes`, from the user menu for everyone and from the settings gear for the gamemaster:

- **The table's theme** (`library/table.json`, set by the gamemaster) is what everyone sees,
  the login page included, and colours the installed app.
- **Each reader** may choose their own (`User.theme`): a theme, *Paravent* (Paravent's own look
  over the table's) or *As the table*. Kept on the account, so it follows them across devices.

## 7. Tome

The theme Paravent ships (`app/themes/tome/`): the published books' page. Parchment with a
faint mottle and grain by day, dark leather with gilt by candlelight; the books' red-brown ink
for headings; statblocks with the orange bars and tapered red rules; read-aloud boxes with
their side bars; sidebars with dark rules above and below; striped tables; framed panels; a
gilt rule under each page title; a drop cap where a chapter opens with prose.

Fonts, all SIL Open Font License 1.1, chosen as free relatives of the books' faces:

| Role | Font | Stands in for |
| --- | --- | --- |
| Body | Libre Baskerville | Mrs Eaves (a Baskerville revival) |
| Display | Cinzel | The books' small-capital titles |
| UI, tables | Alegreya Sans | Scala Sans |

Textures are SVG noise (`feTurbulence`), drawn by the browser: no photographs, nothing
under someone else's copyright, about 700 bytes each. The books' own fonts and artwork are
not free to ship and are not used.
