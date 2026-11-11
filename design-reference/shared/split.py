"""Split the monolithic kit into standalone design-reference pages.

Each output page inlines the shared foundation so it opens correctly from
the filesystem with no server and no build step.
"""

import pathlib
import re

SRC = pathlib.Path(__file__).parent  # shared/
OUT = SRC.parent  # design-reference/

src = (SRC / "_source-kit.html").read_text()
css = re.search(r"<style>(.*?)</style>", src, re.S).group(1)
sprite = re.search(r'(<svg style="display:none".*?</svg>)', src, re.S).group(1)
body = re.search(r"<div class=\"doc\">(.*)</div>\s*<script>", src, re.S).group(1)
script = re.search(r"<script>(.*?)</script>", src, re.S).group(1)

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    "family=Inter+Tight:wght@400;500;600;700&family=Source+Sans+3:wght@400;500;600"
    '&family=JetBrains+Mono:wght@400;500;700&display=swap">'
)

# Section id -> (start marker, filename, title, one-line summary)
SECTIONS = [
    (
        "1. PRINCIPLES",
        "01-principles.html",
        "Principles",
        "The five decisions every other choice follows from.",
    ),
    (
        "2. COMBAT TRACKER",
        "02-combat-tracker.html",
        "Combat Tracker",
        "The gamemaster's live encounter screen, with the reference drawer.",
    ),
    (
        "3. COMMAND PALETTE",
        "03-command-palette.html",
        "Command Palette",
        "One search across the whole compendium, summoned from anywhere.",
    ),
    (
        "4. MOBILE",
        "04-player-mobile.html",
        "Player on Mobile",
        "Character sheet, dice and spell reference on a phone.",
    ),
    (
        "5. WIZARD",
        "05-wizard.html",
        "Wizard",
        "Multi-step flows with server-side state that survives a closed tab.",
    ),
    (
        "6. TAG RENDERER",
        "06-tag-renderer.html",
        "Tag Renderer",
        "How each 5eTools {@tag} renders and what clicking it does.",
    ),
    (
        "6b. ICONS",
        "07-icons.html",
        "Icons",
        "The Lucide icon system: sizing, placement rules, semantic map.",
    ),
    (
        "7. TOKENS",
        "08-tokens.html",
        "Tokens",
        "Color, type and measurement tokens, with contrast figures.",
    ),
    (
        "7b. SCROLLBARS",
        "09-scrollbars.html",
        "Scrollbars",
        "Themed scrollbars that behave like system ones.",
    ),
    (
        "8. RESPONSIVE",
        "10-responsive.html",
        "Responsive",
        "One layout at three widths, and what changes between them.",
    ),
    (
        "9. BUILD",
        "11-implementation.html",
        "Implementation",
        "How the design maps onto FastAPI, htpy and HTMX.",
    ),
]

# 12-tooltips.html and 13-chargen-wizard.html are maintained separately (each carries
# its own markup rather than living in the kit), but every generated page must still
# link to them.
EXTRA_NAV = [
    ("12-tooltips.html", "Tooltips"),
    ("13-chargen-wizard.html", "Character Generation"),
    ("14-chargen-demo-desktop.html", "Demo · Desktop"),
    ("15-chargen-demo-mobile.html", "Demo · Mobile"),
    ("16-character-sheet.html", "Character Sheet"),
]


def cut(name):
    """Return the HTML of one section, by its comment marker."""
    pat = re.compile(r"<!-- ═+ " + re.escape(name) + r" ═+ -->(.*?)(?=<!-- ═|\Z)", re.S)
    m = pat.search(body)
    if not m:
        raise SystemExit(f"section not found: {name}")
    return m.group(1).strip()


NAV_TPL = """<nav class="dr-nav" aria-label="Design reference">
  <a class="dr-home" href="index.html">Paravent design reference</a>
  <div class="dr-links">{links}</div>
</nav>"""


def nav(current):
    out = []
    for _, fn, title, _ in SECTIONS:
        cls = ' class="here" aria-current="page"' if fn == current else ""
        out.append(f'<a href="{fn}"{cls}>{title}</a>')
    for fn, title in EXTRA_NAV:
        cls = ' class="here" aria-current="page"' if fn == current else ""
        out.append(f'<a href="{fn}"{cls}>{title}</a>')
    return NAV_TPL.format(links="".join(out))


NAV_CSS = """
/* ── design-reference chrome (not part of the app) ── */
.dr-nav{
  position:sticky; top:0; z-index:50; background:var(--surface);
  border-bottom:1px solid var(--border); padding:9px 24px;
  display:flex; align-items:center; gap:18px; flex-wrap:wrap;
  box-shadow:var(--shadow-sm);
}
.dr-home{
  font-family:var(--font-ui); font-size:13px; font-weight:600; color:var(--text);
  text-decoration:none; white-space:nowrap;
}
.dr-links{ display:flex; gap:3px; overflow-x:auto; scrollbar-width:none; }
.dr-links::-webkit-scrollbar{ height:0; }
.dr-links a{
  font-family:var(--font-ui); font-size:12.5px; font-weight:500; color:var(--text-2);
  text-decoration:none; padding:5px 9px; border-radius:var(--r-sm); white-space:nowrap;
}
.dr-links a:hover{ background:var(--surface-3); color:var(--text); }
.dr-links a.here{ background:var(--accent-weak); color:var(--accent-text); font-weight:600; }
.dr-foot{
  max-width:1180px; margin:0 auto; padding:60px 24px 40px;
  border-top:1px solid var(--border); display:flex; justify-content:space-between;
  gap:16px; flex-wrap:wrap; font-size:12.5px; color:var(--text-3);
}
.dr-foot a{ color:var(--accent-text); }
"""

THEME_JS = """
(function(){
  var root=document.documentElement, b=document.querySelectorAll('.themetoggle button');
  b.forEach(function(x){ x.addEventListener('click',function(){
    var v=x.dataset.set;
    if(v==='system') root.removeAttribute('data-theme'); else root.setAttribute('data-theme',v);
    b.forEach(function(o){ o.setAttribute('aria-pressed',String(o===x)); });
    try{ localStorage.setItem('pv-theme',v); }catch(e){}
  });});
  try{ var s=localStorage.getItem('pv-theme');
    if(s){ var t=document.querySelector('.themetoggle button[data-set="'+s+'"]'); if(t) t.click(); }
  }catch(e){}
  document.querySelectorAll('.chip, .pick, .advset button').forEach(function(el){
    el.addEventListener('click',function(){
      var g=el.closest('.filterbar, .cardgrid, .advset'); if(!g) return;
      g.querySelectorAll('[aria-pressed]').forEach(function(o){ o.setAttribute('aria-pressed','false'); });
      el.setAttribute('aria-pressed','true');
    });
  });
  document.querySelectorAll('.res').forEach(function(el){
    el.addEventListener('click',function(){
      document.querySelectorAll('.res').forEach(function(o){ o.setAttribute('aria-selected','false'); });
      el.setAttribute('aria-selected','true');
    });
  });
})();
"""

HEAD_MAST = """<header class="masthead" style="padding:34px 0 26px;">
  <div class="mast-row">
    <span class="eyebrow">{eyebrow}</span>
    <div class="themetoggle" role="group" aria-label="Theme">
      <button type="button" data-set="light" aria-pressed="false">Light</button>
      <button type="button" data-set="system" aria-pressed="true">System</button>
      <button type="button" data-set="dark" aria-pressed="false">Dark</button>
    </div>
  </div>
  <h1 style="font-size:clamp(26px,4vw,34px); margin:8px 0 10px;">{title}</h1>
  <p class="lede" style="font-size:15.5px;">{summary}</p>
</header>"""


def page(title, eyebrow, summary, inner, current, extra_css="", extra_js=""):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Paravent</title>
{FONTS}
<style>
{css}
{NAV_CSS}{extra_css}
</style>
</head>
<body>
{sprite}
{nav(current)}
<div class="doc">
{HEAD_MAST.format(eyebrow=eyebrow, title=title, summary=summary)}
{inner}
</div>
<footer class="dr-foot">
  <span>Paravent design reference · see <a href="../documentation/DESIGN.md">DESIGN.md</a> for the written spec</span>
  <span><a href="index.html">All sections</a></span>
</footer>
<script>{THEME_JS}{extra_js}</script>
</body>
</html>
"""


written = []
for idx, (marker, fn, title, summary) in enumerate(SECTIONS, 1):
    inner = cut(marker)
    # strip the section's own eyebrow/h2/intro - the masthead carries them now
    inner = re.sub(r'<span class="eyebrow">[^<]*</span>\s*', "", inner, count=1)
    inner = re.sub(r"<h2[^>]*>.*?</h2>\s*", "", inner, count=1, flags=re.S)
    inner = re.sub(r'<p class="intro">(.*?)</p>\s*', "", inner, count=1, flags=re.S)
    html = page(title, f"{idx:02d} · Design reference", summary, inner, fn)
    (OUT / fn).write_text(html)
    written.append((fn, title, summary, len(html)))

for fn, title, _, n in written:
    print(f"{fn:<26}{title:<22}{n:>8,} bytes")
