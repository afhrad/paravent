# Notices

Paravent is an independent open-source project. It is not affiliated with, endorsed or
sponsored by Wizards of the Coast or the 5eTools project. *Dungeons & Dragons* and *D&D* are
trademarks of Wizards of the Coast LLC. All other names and trademarks belong to their
respective owners.

**Paravent ships no rulebook content.** The spells, creatures, items, rules text and
adventures it works with are brought by whoever runs it and are never part of this
repository, a release or a container image (see the README, "Data"). `data/rules/` holds a
handful of plain figures and names the code needs and no data source offers in structured
form, and no text: three numbers tables of the System Reference Documents (below), the
point-buy costs, and which feature grants which choice. Tables that are in no SRD, such
as the 2014 encounter thresholds, are not carried: Paravent reads them from the corpus of
whoever runs it.

This file lists the material from others that the repository does contain, and the terms each
comes under. Everything not listed here is Paravent's own, released under the MIT License
([`LICENSE`](LICENSE)).

## System Reference Document

Short passages of the System Reference Documents appear in the repository: as sample text
in the pages of `design-reference/` (spells, conditions, a statblock), as a few sentences in
the interface texts (`app/i18n/`), in comments and test fixtures that quote the rule they
handle, and in the website's screenshots (`website/assets/img/`), which show four SRD
creatures. `data/rules/encounterdifficulty.json` holds three of their tables as numbers:
experience points by challenge rating and character advancement (SRD 5.1) and the XP budget
per character (SRD 5.2.1).

This work includes material taken from the System Reference Document 5.1 ("SRD 5.1") by
Wizards of the Coast LLC and available at
https://dnd.wizards.com/resources/systems-reference-document. The SRD 5.1 is licensed under
the Creative Commons Attribution 4.0 International License available at
https://creativecommons.org/licenses/by/4.0/legalcode.

This work includes material from the System Reference Document 5.2.1 ("SRD 5.2.1") by
Wizards of the Coast LLC, available at https://www.dndbeyond.com/srd. The SRD 5.2.1 is
licensed under the Creative Commons Attribution 4.0 International License, available at
https://creativecommons.org/licenses/by/4.0/legalcode.

Some of these passages are shortened or translated; the German screenshots show the four
creatures in German.

## Lucide icons

The icons of the interface are from [Lucide](https://lucide.dev). They are in
`static/icons.svg` (inlined into every page of the app), in the sprites inside the pages of
`design-reference/`, and in the pages of `website/`, which `website/build.py` fills from the
app's sprite. The symbol `i-paravent` is Paravent's own.

```text
ISC License

Copyright (c) 2026 Lucide Icons and Contributors

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

---

The following Lucide icons are derived from the Feather project:

airplay, alert-circle, alert-octagon, alert-triangle, aperture, arrow-down-circle, arrow-down-left, arrow-down-right, arrow-down, arrow-left-circle, arrow-left, arrow-right-circle, arrow-right, arrow-up-circle, arrow-up-left, arrow-up-right, arrow-up, at-sign, calendar, cast, check, chevron-down, chevron-left, chevron-right, chevron-up, chevrons-down, chevrons-left, chevrons-right, chevrons-up, circle, clipboard, clock, code, columns, command, compass, corner-down-left, corner-down-right, corner-left-down, corner-left-up, corner-right-down, corner-right-up, corner-up-left, corner-up-right, crosshair, database, divide-circle, divide-square, dollar-sign, download, external-link, feather, frown, hash, headphones, help-circle, info, italic, key, layout, life-buoy, link-2, link, loader, lock, log-in, log-out, maximize, meh, minimize, minimize-2, minus-circle, minus-square, minus, monitor, moon, more-horizontal, more-vertical, move, music, navigation-2, navigation, octagon, pause-circle, percent, plus-circle, plus-square, plus, power, radio, rss, search, server, share, shopping-bag, sidebar, smartphone, smile, square, table-2, tablet, target, terminal, trash-2, trash, triangle, tv, type, upload, x-circle, x-octagon, x-square, x, zoom-in, zoom-out

The MIT License (MIT) (for the icons listed above)

Copyright (c) 2013-present Cole Bemis

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Fonts

The Tome theme (`app/themes/tome/fonts/`, copied to `website/assets/css/tome/fonts/`) comes
with three typefaces, each under the SIL Open Font License 1.1. The licence of each lies
beside its files:

| Typeface | Copyright | Licence |
| --- | --- | --- |
| Alegreya Sans | 2013 The Alegreya Sans Project Authors | `OFL-alegreyasans.txt` |
| Cinzel | 2020 The Cinzel Project Authors | `OFL-cinzel.txt` |
| Libre Baskerville | 2012 The Libre Baskerville Project Authors, with Reserved Font Name Libre Baskerville | `OFL-librebaskerville.txt` |

## htmx

`static/js/htmx.min.js` is [htmx](https://htmx.org) 2.0.4, unchanged.

```text
Zero-Clause BSD
=============

Permission to use, copy, modify, and/or distribute this software for
any purpose with or without fee is hereby granted.

THE SOFTWARE IS PROVIDED “AS IS” AND THE AUTHOR DISCLAIMS ALL
WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES
OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE
FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY
DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN
AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT
OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

## 5eTools

Paravent reads data in the format of the [5eTools](https://5e.tools) project and contains
none of that data. Three things in the repository come from 5eTools' own files: the catalogue
of its `{@tag}` syntax in `data/library/tags/` (tag names, counts and short examples of how
each is written), the default attack template in `app/services/creature_builder.py`, and a
challenge-rating expression quoted in `tests/test_creature_builder.py`.

```text
MIT License

Copyright (c) 2017 TheGiddyLimit and contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
