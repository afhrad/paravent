# Design Reference

Visual counterpart to [`../documentation/DESIGN.md`](../documentation/DESIGN.md). Twelve standalone pages, one per part of
the interface.

**Resuming this work?** Read [`HANDOFF.md`](HANDOFF.md) first — it carries the decisions, the
findings that came from measuring real data, and the two open questions that still need a human
answer.

**Open [`index.html`](index.html) in a browser.** No server, no build step, no network beyond
Google Fonts — every page inlines its own CSS and icon sprite so it works straight from the
filesystem.

## Pages

| File | Covers |
| --- | --- |
| [`01-principles.html`](01-principles.html) | The five decisions every other choice follows from |
| [`02-combat-tracker.html`](02-combat-tracker.html) | Gamemaster encounter screen + reference drawer |
| [`03-command-palette.html`](03-command-palette.html) | ⌘K search across the whole compendium |
| [`04-player-mobile.html`](04-player-mobile.html) | Character sheet, dice and spells on a phone |
| [`05-wizard.html`](05-wizard.html) | Multi-step flows with persisted state |
| [`06-tag-renderer.html`](06-tag-renderer.html) | How each 5eTools `{@tag}` renders |
| [`07-icons.html`](07-icons.html) | Lucide icon system, sizing and semantic map |
| [`08-tokens.html`](08-tokens.html) | Color, type and measurement tokens |
| [`09-scrollbars.html`](09-scrollbars.html) | Themed scrollbars |
| [`10-responsive.html`](10-responsive.html) | One layout at three widths |
| [`11-implementation.html`](11-implementation.html) | Mapping onto FastAPI + htpy + HTMX |
| [`12-tooltips.html`](12-tooltips.html) | **Interactive** — hover/tap previews, live dice |
| [`13-chargen-wizard.html`](13-chargen-wizard.html) | Character generation flow — both editions, mobile first |
| [`14-chargen-demo-desktop.html`](14-chargen-demo-desktop.html) | **Interactive** — build a wizard, advance it 1→10 |
| [`15-chargen-demo-mobile.html`](15-chargen-demo-mobile.html) | **Interactive** — the same engine, phone shell |
| [`16-character-sheet.html`](16-character-sheet.html) | **Interactive** — playing a level 10 wizard, desktop + mobile |

Every page carries a Light / System / Dark toggle; the choice persists across pages via
`localStorage`.

## Regenerating

`shared/` holds the source of truth:

```
shared/
├── tokens.css           tokens, base, icons, scrollbars
├── components.css       component styles
├── icons.svg            44 Lucide symbols
├── split.py             the generator
├── _source-kit.html     monolithic source for pages 01-11
└── _source-tooltips.html  source for page 12
```

Pages `12`–`16` are maintained directly, not generated from the kit — each carries its own markup and behavioural JavaScript.

Pages 01–11 are **generated**. Edit `_source-kit.html`, then:

```bash
python3 shared/split.py
```

Do not edit a generated page directly — the next run overwrites it. `12-tooltips.html` is
maintained from `_source-tooltips.html` because it carries its own behavioural JavaScript.

## Relationship to the app

These pages are a reference, not app code. They use hand-written CSS with the same token names
the app will use, so the tokens are visible and arguable in isolation. The production styling
comes from Tailwind + DaisyUI themes (`paravent-light` / `paravent-dark`) built from the same
values — see [`../documentation/DESIGN.md`](../documentation/DESIGN.md) §2.
