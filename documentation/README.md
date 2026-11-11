# Paravent documentation

Written specs. The visual counterpart is [`../design-reference/`](../design-reference/);
`CLAUDE.md` stays at the repository root because the agent loads it from there.

## Read in this order

| # | Document | Covers |
| --- | --- | --- |
| 1 | [PROJECT.md](PROJECT.md) | What Paravent is, the data foundation, the feature list |
| 2 | [TECHSTACK.md](TECHSTACK.md) | Stack decisions. §3 the two data roots · §6b corpus coverage · §6c reference resolution |
| 3 | [DESIGN.md](DESIGN.md) | UI spec — tokens, layout, components, the `{@tag}` renderer, tooltips |
| 4 | [PROGRESS.md](PROGRESS.md) | What is built, what is next, measured against the real corpus |

## By topic

**Character creation** — [CHARGEN.md](CHARGEN.md) answers *what the dataset supports* and
where the gaps are; [CHARGEN-WIZARD.md](CHARGEN-WIZARD.md) answers *what the player sees*.
Read them in that order. [CHARACTER-FORMAT.md](CHARACTER-FORMAT.md) specifies the character
file that both produce and the sheet reads — and that can be edited by hand.

**Multilingual** — [TRANSLATION.md](TRANSLATION.md). Three separate systems: UI strings,
reference text as a lossless overlay, and units as a per-user setting.

## Conventions

- Decisions carry the reasoning that produced them, and superseded drafts are marked
  rather than deleted — a reversed decision is worth knowing about.
- Numbers come from measuring the real corpus, not from estimates. Where a figure appears,
  it was counted.
- Section references are written as `TECHSTACK.md §6c` and are stable; the numbered
  headings exist so other documents and code comments can cite them.
