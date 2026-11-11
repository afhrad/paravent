// Small progressive enhancements. Every control works without this file; it only
// makes them nicer.
(function () {
  var root = document.documentElement;
  root.classList.add('js');  // lets CSS hide no-JS fallbacks such as submit buttons

  // -- Editing a textarea the way typing does ---------------------------------------
  // Text a script puts into a textarea (value = …, setRangeText) is not in the
  // browser's undo history, so ⌘Z cannot take an insertion back. execCommand
  // ('insertText') is the one edit that history records; setRangeText where it is
  // missing. The caret ends after the new text, or — with keepCaret, for text that
  // arrives later (an uploaded image) — stays where the writer has it, and so does
  // the focus. One input event either way.
  function replaceText(area, from, to, text, keepCaret) {
    if (from === to && !text) return;
    var focused = document.activeElement;
    var start = area.selectionStart, end = area.selectionEnd;
    area.focus();
    area.setSelectionRange(from, to);
    var done = false;
    try { done = document.execCommand('insertText', false, text); } catch (e) { done = false; }
    if (!done) {
      area.setRangeText(text, from, to, 'end');
      area.dispatchEvent(new Event('input', { bubbles: true }));
    }
    if (!keepCaret) return;
    var shift = text.length - (to - from);
    var moved = function (p) { return p >= to ? p + shift : p > from ? from + text.length : p; };
    area.setSelectionRange(moved(start), moved(end));
    if (focused && focused !== area && focused.focus) focused.focus();
  }
  window.ParaventText = { replace: replaceText };

  // -- Light / Dark --------------------------------------------------------------
  // The stored choice is applied before paint by an inline script in <head>; this
  // wires the buttons and reflects the state. Until a choice is made the device's own
  // setting applies, and the toggle shows it. Storage may be unavailable (private
  // mode, blocked site data) — the toggle then works for the current page only.
  var darkQuery = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
  function currentTheme() {
    var t = root.getAttribute('data-theme');
    if (t === 'light' || t === 'dark') return t;
    return darkQuery && darkQuery.matches ? 'dark' : 'light';
  }

  function reflectTheme() {
    var value = currentTheme();
    document.querySelectorAll('.themetoggle button[data-set]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.set === value));
    });
  }

  document.querySelectorAll('.themetoggle button[data-set]').forEach(function (b) {
    b.addEventListener('click', function () {
      var value = b.dataset.set;
      root.setAttribute('data-theme', value);
      try { localStorage.setItem('pv-theme', value); } catch (e) {}
      reflectTheme();
    });
  });
  if (darkQuery && darkQuery.addEventListener) darkQuery.addEventListener('change', reflectTheme);
  reflectTheme();

  // -- Scroll positions for the navigation's remembered pages --------------------
  // The nav links each section to where it was left (app/ui/nav.py); this brings
  // back how far down that page was. Only on arriving through the nav, or when the
  // server sends someone back to the page they left after logging in again (the
  // one-use paravent_resume cookie, app/routes/auth.py): a link to the same chapter
  // from anywhere else opens it at the top, as a link should. Positions are kept in
  // localStorage, so they outlive the tab and a closed app; without storage the pages
  // simply open at the top.
  // Boxes that scroll on their own (the tracker's order) carry data-scroll-keep with a
  // name, and are kept beside the page's own position.
  var SCROLLS = 'pv-scrolls';
  var SCROLLS_KEPT = 40;
  var ARRIVING = 'pv-scroll-restore';
  var RESUMED = 'paravent_resume=1';
  var here = location.pathname + location.search;

  function store(key, value) {
    try {
      if (value === null) sessionStorage.removeItem(key);
      else sessionStorage.setItem(key, value);
    } catch (e) {}
  }

  function stored(key) {
    try { return sessionStorage.getItem(key); } catch (e) { return null; }
  }

  function scrolls() {
    try { return JSON.parse(localStorage.getItem(SCROLLS)) || {}; } catch (e) { return {}; }
  }

  function boxes() { return document.querySelectorAll('[data-scroll-keep]'); }

  // Saved under the address shown when leaving: a page may change it in place (the
  // tracker's ?sel=), and the nav returns to that address.
  function saveScroll() {
    var kept = { y: Math.round(window.scrollY), boxes: {}, at: Date.now() };
    boxes().forEach(function (el) {
      if (el.scrollTop > 0) kept.boxes[el.dataset.scrollKeep] = Math.round(el.scrollTop);
    });
    var all = scrolls();
    all[location.pathname + location.search] = kept;
    // Only the most recent pages: the store is small and shared by the whole site.
    var urls = Object.keys(all).sort(function (a, b) { return (all[b].at || 0) - (all[a].at || 0); });
    urls.slice(SCROLLS_KEPT).forEach(function (u) { delete all[u]; });
    try { localStorage.setItem(SCROLLS, JSON.stringify(all)); } catch (e) {}
  }

  function savedScroll() {
    var kept = scrolls()[here];
    return kept && typeof kept.y === 'number' ? kept : null;
  }
  window.addEventListener('pagehide', saveScroll);
  // iOS may drop a backgrounded PWA without a pagehide.
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') saveScroll();
  });

  document.querySelectorAll('.shell-nav a').forEach(function (a) {
    a.addEventListener('click', function () { store(ARRIVING, a.getAttribute('href')); });
  });

  var arriving = stored(ARRIVING);
  store(ARRIVING, null);  // one use, whether or not this is the page it named
  var resumed = document.cookie.split('; ').indexOf(RESUMED) >= 0;
  if (resumed) document.cookie = 'paravent_resume=; Max-Age=0; Path=/; SameSite=Lax';
  var kept = (arriving === here || resumed) && !location.hash ? savedScroll() : null;
  if (kept) {
    var y = kept.y > 0 ? kept.y : 0;
    var restoreBoxes = function () {
      boxes().forEach(function (el) {
        var top = (kept.boxes || {})[el.dataset.scrollKeep];
        if (top > 0) el.scrollTop = top;
      });
    };
    if (y) window.scrollTo(0, y);
    restoreBoxes();
    // Images loading late can still push the page; try once more unless the
    // reader has scrolled in the meantime.
    var placed = window.scrollY;
    window.addEventListener('load', function () {
      if (y && window.scrollY === placed) window.scrollTo(0, y);
      restoreBoxes();
    }, { once: true });
  }

  // -- Dropdown menus (<details>) ------------------------------------------------
  // A <details> menu opens and closes on its own; what it lacks is closing when you
  // click elsewhere or press Escape, which every menu is expected to do.
  var MENUS = ['details.langmenu', 'details.create-menu', 'details.usermenu', 'details.cb-menu'];
  var OPEN = MENUS.map(function (m) { return m + '[open]'; }).join(', ');

  function closeMenus(except) {
    document.querySelectorAll(OPEN).forEach(function (d) {
      if (d !== except) d.removeAttribute('open');
    });
  }

  function closeOutside(e) {
    closeMenus(e.target.closest ? e.target.closest(MENUS.join(', ')) : null);
  }

  document.addEventListener('click', closeOutside);

  // iOS sends no click for a tap on plain page areas, so a tap also counts: a touch
  // that did not move. A touch that scrolls the page leaves the menu open.
  var touch = null;
  document.addEventListener('pointerdown', function (e) {
    touch = e.pointerType === 'mouse' ? null : { x: e.clientX, y: e.clientY };
  });
  document.addEventListener('pointerup', function (e) {
    if (touch && Math.abs(e.clientX - touch.x) < 10 && Math.abs(e.clientY - touch.y) < 10) closeOutside(e);
    touch = null;
  });
  document.addEventListener('pointercancel', function () { touch = null; });

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    var open = document.querySelector(OPEN);
    if (!open) return;
    open.removeAttribute('open');
    var s = open.querySelector('summary');
    if (s) s.focus();
  });
})();
