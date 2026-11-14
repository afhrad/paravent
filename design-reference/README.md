# Design Reference

Visual counterpart to [`../documentation/DESIGN.md`](../documentation/DESIGN.md). Seventeen
standalone pages: sixteen drawn before the app existed (September 2026), each now opening with a
dated *As built* note, and one showing the screens as they are built.

**Resuming this work?** Read [`HANDOFF.md`](HANDOFF.md) first — the decisions that still bind,
the ones that changed once the app was built, and the findings that came from measuring real data.

**Open [`index.html`](index.html) in a browser.** No server, no build step, no network beyond
Google Fonts — every page inlines its own CSS and icon sprite so it works straight from the
filesystem.

## Pages

| File | Covers |
| --- | --- |
| [`17-paravent-today.html`](17-paravent-today.html) | **Start here** — the screens as built, and the parts no other page drew |
| [`01-principles.html`](01-principles.html) | The five decisions every other choice follows from |
| [`02-combat-tracker.html`](02-combat-tracker.html) | Gamemaster encounter screen (built with a side panel, not a drawer) |
| [`03-command-palette.html`](03-command-palette.html) | ⌘K search across the whole compendium |
| [`04-player-mobile.html`](04-player-mobile.html) | Character sheet, dice and spells on a phone, as first imagined |
| [`05-wizard.html`](05-wizard.html) | Multi-step flows with persisted state |
| [`06-tag-renderer.html`](06-tag-renderer.html) | How each `{@tag}` renders |
| [`07-icons.html`](07-icons.html) | Lucide icon system, sizing and semantic map |
| [`08-tokens.html`](08-tokens.html) | Colour, type and measurement tokens |
| [`09-scrollbars.html`](09-scrollbars.html) | Themed scrollbars |
| [`10-responsive.html`](10-responsive.html) | The layout across widths |
| [`11-implementation.html`](11-implementation.html) | Mapping onto FastAPI + htpy + HTMX |
| [`12-tooltips.html`](12-tooltips.html) | **Interactive** — hover/tap previews, live dice |
| [`13-chargen-wizard.html`](13-chargen-wizard.html) | Character generation flow — both editions, mobile first |
| [`14-chargen-demo-desktop.html`](14-chargen-demo-desktop.html) | **Historical prototype** — build a wizard, advance it 1→10 |
| [`15-chargen-demo-mobile.html`](15-chargen-demo-mobile.html) | **Historical prototype** — the same engine, phone shell |
| [`16-character-sheet.html`](16-character-sheet.html) | **Interactive** — playing a level 10 wizard, desktop + mobile |

Every page carries a Light / System / Dark toggle; the choice persists across pages via
`localStorage`. Page 17's screenshots follow it.

## Regenerating

`shared/` holds the source of pages 01–11:

```
shared/
├── tokens.css           tokens, base, icons, scrollbars   (= static/css/tokens.css)
├── components.css       component styles                  (= static/css/components.css)
├── icons.svg            44 Lucide symbols (the app's static/icons.svg has 68)
├── split.py             the generator
├── _source-kit.html     monolithic source for pages 01-11
└── _source-tooltips.html  source copy of page 12
```

Pages 01–11 are **generated**. Edit `_source-kit.html`, then:

```bash
python3 design-reference/shared/split.py
```

Do not edit a generated page directly — the next run overwrites it. Pages 12–17 are maintained
by hand; `12-tooltips.html` and `_source-tooltips.html` carry the same content.

## Relationship to the app

These pages are a reference, not app code. The app serves the same `tokens.css` and
`components.css` from `static/css/`, adds `app.css` and `sheet.css`, and builds no CSS: the
Tailwind + DaisyUI plan was dropped on 2026-10-15 (TECHSTACK.md). Themes restyle the app from a
folder each (THEMES.md); Paravent's own look, which these pages show, is the default.
