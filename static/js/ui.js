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

  // -- Room for the top bar ------------------------------------------------------
  // The bar sticks to the top and wraps to two rows on a phone, so a link to a heading
  // would land beneath it. Its height is kept in --bar-h, which the page's
  // scroll-padding reads (app.css); without this file a fixed guess stands.
  var bar = document.querySelector('.shell-bar');
  if (bar) {
    var measure = function () { root.style.setProperty('--bar-h', bar.offsetHeight + 'px'); };
    measure();
    if (window.ResizeObserver) new ResizeObserver(measure).observe(bar);
    else window.addEventListener('resize', measure);
  }

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
  var MENUS = ['details.langmenu', 'details.create-menu', 'details.usermenu', 'details.cb-menu',
    'details.name-roll-menu', 'details.world-menu', 'details.nav-more'];
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

// A number moved by buttons beside it (``.stepper``, ``data-step="-5"``): a DC chosen
// with a finger instead of a keyboard. Kept within the field's min and max.
(function () {
  'use strict';
  document.addEventListener('click', function (e) {
    var btn = e.target.closest && e.target.closest('.stepper [data-step]');
    if (!btn) return;
    var field = btn.closest('.stepper').querySelector('input[type=number]');
    if (!field) return;
    var now = parseInt(field.value, 10);
    var next = (isNaN(now) ? 10 : now) + parseInt(btn.dataset.step, 10);
    if (field.min !== '') next = Math.max(parseInt(field.min, 10), next);
    if (field.max !== '') next = Math.min(parseInt(field.max, 10), next);
    field.value = String(next);
    field.dispatchEvent(new Event('change', { bubbles: true }));
  });
})();

// A title image dropped on or chosen in its panel (app/ui/title_images.py) is sent at once.
(function () {
  'use strict';
  document.querySelectorAll('.title-drop').forEach(function (drop) {
    var input = drop.querySelector('input[type=file]');
    if (!input || !input.form) return;
    var form = input.form;
    form.classList.add('is-live');
    input.addEventListener('change', function () { if (input.files.length) form.requestSubmit(); });
    ['dragenter', 'dragover'].forEach(function (type) {
      drop.addEventListener(type, function (e) { e.preventDefault(); drop.classList.add('is-over'); });
    });
    ['dragleave', 'drop'].forEach(function (type) {
      drop.addEventListener(type, function () { drop.classList.remove('is-over'); });
    });
    drop.addEventListener('drop', function (e) {
      e.preventDefault();
      if (!e.dataTransfer || !e.dataTransfer.files.length) return;
      input.files = e.dataTransfer.files;
      form.requestSubmit();
    });
  });
})();

// A link or button naming a dialog (data-dialog="id") opens it; without the script the
// link reloads the page with the dialog shown. A [data-close] inside closes it. A dialog
// with ``data-src`` (the world's settings) loads its content from there the first time
// it opens; one with ``data-open-now`` opens as the page loads.
(function () {
  'use strict';

  function focusFirst(target) {
    var first = target.querySelector('input:not([type=hidden]), textarea, select');
    if (first) first.focus();
  }

  function show(target) {
    if (target.open) target.close();
    target.showModal();
    var src = target.dataset.src;
    if (src && !target.dataset.loaded) {
      target.dataset.loaded = '1';
      fetch(src, { credentials: 'same-origin' })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
        .then(function (html) {
          var body = target.querySelector('.dialog-body') || target;
          body.innerHTML = html;
          // Scripts in fetched markup do not run: load each the page lacks, then tell
          // those it has that there is new content (as htmx does), e.g. the picture tool.
          body.querySelectorAll('script[src]').forEach(function (old) {
            var src = old.getAttribute('src');
            old.remove();
            if (document.querySelector('script[src="' + src + '"]')) return;
            var s = document.createElement('script');
            s.src = src;
            document.body.appendChild(s);
          });
          document.dispatchEvent(new CustomEvent('htmx:load'));
          focusFirst(target);
        })
        .catch(function () {
          delete target.dataset.loaded;
          window.location.href = target.dataset.fallback || src;
        });
      return;
    }
    focusFirst(target);
  }

  document.addEventListener('click', function (e) {
    var opener = e.target.closest && e.target.closest('[data-dialog]');
    if (opener) {
      var target = document.getElementById(opener.dataset.dialog);
      if (target && typeof target.showModal === 'function') {
        e.preventDefault();
        // Opened from a menu (the world menu's settings): the menu closes behind it.
        var menu = opener.closest('details[open]');
        if (menu) menu.removeAttribute('open');
        if (!target.dataset.fallback) target.dataset.fallback = opener.getAttribute('href') || '';
        show(target);
      }
      return;
    }
    var closer = e.target.closest && e.target.closest('dialog [data-close]');
    if (closer) {
      e.preventDefault();
      closer.closest('dialog').close();
    }
  });

  function openNow() {
    document.querySelectorAll('dialog[data-open-now]').forEach(function (d) {
      if (typeof d.showModal === 'function') show(d);
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', openNow);
  else openNow();
})();

// A picture in a text (``img[data-zoom]``: Markdown, the books' pictures, a handout)
// enlarges on a click or tap, over the page, until a click or tap outside it or Escape.
// Title images, covers and tokens carry no ``data-zoom``; a picture inside a link
// follows the link.
(function () {
  'use strict';
  var box = null;

  function viewer() {
    if (box) return box;
    box = document.createElement('dialog');
    box.className = 'img-zoom';
    box.appendChild(document.createElement('img'));
    box.addEventListener('click', function (e) {
      if (e.target !== box.firstChild) box.close();
    });
    document.body.appendChild(box);
    return box;
  }

  document.addEventListener('click', function (e) {
    var pic = e.target.closest && e.target.closest('img[data-zoom]');
    if (!pic || pic.closest('a[href], .img-zoom')) return;
    e.preventDefault();
    var d = viewer();
    d.firstChild.src = pic.currentSrc || pic.src;
    d.firstChild.alt = pic.alt || '';
    d.showModal();
  });
})();

// A tag or line to paste into an editor (``.copy-snippet``: ``{@npc Hob Wade}``) is
// copied by a click, a tap, Enter or Space, and says so for a moment. The clipboard API
// needs a secure address, which a server on the home network over plain http is not, so
// a hidden field and the older copy command stand in.
(function () {
  'use strict';

  function fallback(text) {
    var field = document.createElement('textarea');
    field.value = text;
    field.setAttribute('readonly', '');
    field.className = 'copy-field';
    document.body.appendChild(field);
    field.select();
    var done = false;
    try { done = document.execCommand('copy'); } catch (err) { done = false; }
    field.remove();
    return done;
  }

  function copy(el) {
    var text = el.textContent;
    var shown = function () {
      el.classList.add('is-copied');
      clearTimeout(el._copied);
      el._copied = setTimeout(function () { el.classList.remove('is-copied'); }, 1400);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(shown, function () { if (fallback(text)) shown(); });
    } else if (fallback(text)) {
      shown();
    }
  }

  document.addEventListener('click', function (e) {
    var el = e.target.closest && e.target.closest('.copy-snippet');
    if (el) copy(el);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    var el = e.target.closest && e.target.closest('.copy-snippet');
    if (!el) return;
    e.preventDefault();
    copy(el);
  });
})();

// A form that takes a while (a world imported or duplicated): once sent, its buttons wait
// and the submit button says what is happening (data-busy), so nobody sends it twice.
(function () {
  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form.matches || !form.matches('form[data-busy]') || e.defaultPrevented) return;
    form.setAttribute('aria-busy', 'true');
    var submit = form.querySelector('button[type=submit]');
    if (submit) {
      var label = submit.querySelector('span') || submit;
      label.textContent = form.dataset.busy;
      submit.classList.add('is-busy');
    }
    // After the browser has taken the form, so the submit button's own value still goes.
    setTimeout(function () {
      form.querySelectorAll('button').forEach(function (b) { b.disabled = true; });
    }, 0);
  });
})();

// Counts beside a menu's links (the world menu's Check links), asked each time the menu
// opens rather than with every page: data-count-src answers a number, 0 hides it.
(function () {
  document.addEventListener('toggle', function (e) {
    var menu = e.target;
    if (!menu.open || !menu.querySelectorAll) return;
    menu.querySelectorAll('[data-count-src]').forEach(function (badge) {
      fetch(badge.dataset.countSrc, { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.text() : ''; })
        .then(function (text) {
          var n = parseInt(text, 10);
          badge.hidden = !(n > 0);
          if (n > 0) badge.textContent = String(n);
        });
    });
  }, true);
})();
