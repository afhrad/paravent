// Previews for references (documentation/DESIGN.md §7).
//
// Mouse hovers; touch taps. Neither behaviour runs on the other device, and the test
// is input capability, never screen width: an iPad in landscape is wide and cannot
// hover, a narrow desktop window can. The media query is re-checked live, so plugging
// in a mouse switches modes without a reload.
//
//   hover in   mouse   opens after 350 ms (instantly once one is open)
//   hover out  mouse   closes after 180 ms — time to move into the preview
//   click      mouse   pins it; its "Open" goes to the full entry
//   tap        touch   opens a bottom sheet, never navigates; backdrop closes it
//   Tab        both    opens on keyboard focus; Esc closes and returns focus
//
// Without JavaScript every reference is a plain link to its full page. The campaign's
// own links (an NPC, a place, a chapter…) preview the same way, by where they lead.
(function () {
  'use strict';

  var hoverMQ = window.matchMedia('(hover: hover) and (pointer: fine)');
  var OPEN_DELAY = 350, CLOSE_DELAY = 180, MARGIN = 8;
  var cache = new Map();
  var current = null;      // { anchor, el, backdrop, pinned, sheet }
  var openTimer = null, closeTimer = null, loadToken = 0;
  var returning = false;   // focus handed back by close() must not reopen the preview

  function refOf(node) {
    return node && node.closest ? node.closest('a.ref[href^="/ref/"], a.ref.lib[href], .ref.missing') : null;
  }
  function inPreview(node) {
    return !!(node && node.closest && node.closest('.tip, .tip-sheet'));
  }

  // An unresolved reference explains itself in a native title; that would show a second
  // tooltip on hover, and never on touch. Move it to data-reason and show it ourselves.
  function adoptTitles(root) {
    (root || document).querySelectorAll('.ref.missing[title]').forEach(function (el) {
      el.dataset.reason = el.getAttribute('title');
      el.removeAttribute('title');
      el.setAttribute('tabindex', '0');
      el.setAttribute('role', 'button');
    });
  }

  function previewUrl(anchor) {
    var url = new URL(anchor.getAttribute('href'), location.href);
    if (anchor.classList.contains('lib')) {
      return '/library/preview?href=' + encodeURIComponent(url.pathname + url.hash);
    }
    url.pathname = url.pathname.replace(/\/$/, '') + '/preview';
    return url.pathname + url.search;
  }

  function missingCard(anchor) {
    var card = document.createElement('div');
    card.className = 'tip-card tip-missing';
    var head = document.createElement('div');
    head.className = 'tip-head';
    var name = document.createElement('b');
    name.className = 'tip-name';
    name.textContent = anchor.textContent.trim();
    head.appendChild(name);
    var body = document.createElement('p');
    body.className = 'tip-text';
    body.textContent = anchor.dataset.reason || '';
    card.appendChild(head);
    card.appendChild(body);
    return card.outerHTML;
  }

  function load(anchor) {
    if (anchor.classList.contains('missing')) return Promise.resolve(missingCard(anchor));
    var url = previewUrl(anchor);
    if (cache.has(url)) return Promise.resolve(cache.get(url));
    return fetch(url, { credentials: 'same-origin', headers: { Accept: 'text/html' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) { cache.set(url, html); return html; });
  }

  function close(returnFocus) {
    clearTimeout(openTimer); clearTimeout(closeTimer);
    loadToken++;
    if (!current) return;
    var anchor = current.anchor;
    current.el.remove();
    if (current.backdrop) current.backdrop.remove();
    anchor.removeAttribute('aria-describedby');
    anchor.removeAttribute('aria-expanded');
    document.documentElement.classList.remove('tip-sheet-open');
    current = null;
    if (returnFocus && anchor.isConnected) {
      returning = true;
      try { anchor.focus({ preventScroll: true }); } finally { returning = false; }
    }
  }

  function place(el, anchor) {
    var r = anchor.getBoundingClientRect();
    var w = el.offsetWidth, h = el.offsetHeight;
    var left = Math.min(Math.max(MARGIN, r.left), window.innerWidth - w - MARGIN);
    var below = r.bottom + 6;
    // Flip, never clip: above the anchor when there is no room below.
    var top = (below + h > window.innerHeight - MARGIN && r.top - h - 6 > MARGIN) ? r.top - h - 6 : below;
    el.style.left = Math.round(left + window.scrollX) + 'px';
    el.style.top = Math.round(top + window.scrollY) + 'px';
    el.classList.toggle('above', top < r.top);
  }

  function show(anchor, pinned) {
    var sheet = !hoverMQ.matches;
    var token = ++loadToken;
    load(anchor).then(function (html) {
      if (token !== loadToken) return;          // something else was asked for meanwhile
      close(false);
      loadToken = token;
      var el = document.createElement('div');
      el.innerHTML = html;
      el.id = 'tip-preview';
      if (sheet) {
        el.className = 'tip-sheet';
        el.setAttribute('role', 'dialog');
        el.setAttribute('aria-modal', 'true');
        var closer = document.createElement('button');
        closer.type = 'button';
        closer.className = 'tip-close btn btn-ghost btn-sm';
        var card = el.querySelector('.tip-card');
        closer.setAttribute('aria-label', (card && card.dataset.close) || 'Close');
        closer.innerHTML = '<svg class="ic" aria-hidden="true"><use href="#i-x"></use></svg>';
        el.insertBefore(closer, el.firstChild);
        var backdrop = document.createElement('div');
        backdrop.className = 'tip-backdrop';
        document.body.appendChild(backdrop);
        document.body.appendChild(el);
        document.documentElement.classList.add('tip-sheet-open');
        current = { anchor: anchor, el: el, backdrop: backdrop, pinned: true, sheet: true };
        closer.focus({ preventScroll: true });
      } else {
        el.className = 'tip' + (pinned ? ' pinned' : '');
        el.setAttribute('role', 'tooltip');
        document.body.appendChild(el);
        place(el, anchor);
        current = { anchor: anchor, el: el, backdrop: null, pinned: !!pinned, sheet: false };
        el.addEventListener('pointerenter', function () { clearTimeout(closeTimer); });
        el.addEventListener('pointerleave', function (e) {
          if (e.pointerType === 'mouse' && current && !current.pinned) scheduleClose();
        });
      }
      anchor.setAttribute('aria-describedby', 'tip-preview');
      anchor.setAttribute('aria-expanded', 'true');
    }).catch(function () { /* no preview: the link still works */ });
  }

  function scheduleOpen(anchor) {
    clearTimeout(openTimer); clearTimeout(closeTimer);
    var delay = current && !current.sheet ? 0 : OPEN_DELAY;   // browsing: no more waiting
    openTimer = setTimeout(function () { show(anchor, false); }, delay);
  }
  function scheduleClose() {
    clearTimeout(openTimer);
    loadToken++;   // a slower preview still loading must not appear late
    clearTimeout(closeTimer);
    closeTimer = setTimeout(function () { if (current && !current.pinned) close(false); }, CLOSE_DELAY);
  }

  // -- mouse ------------------------------------------------------------------------
  // pointerover/pointerout with pointerType 'mouse': touch browsers replay a synthetic
  // mouseenter after a tap, which plain mouse events could not tell apart.
  document.addEventListener('pointerover', function (e) {
    if (e.pointerType !== 'mouse' || !hoverMQ.matches) return;
    var anchor = refOf(e.target);
    if (!anchor || inPreview(anchor)) return;
    if (current && current.anchor === anchor) { clearTimeout(closeTimer); return; }
    if (current && current.pinned) return;
    scheduleOpen(anchor);
  });
  document.addEventListener('pointerout', function (e) {
    if (e.pointerType !== 'mouse' || !hoverMQ.matches) return;
    var anchor = refOf(e.target);
    if (!anchor || inPreview(anchor)) return;
    if (anchor.contains(e.relatedTarget) || inPreview(e.relatedTarget)) return;
    if (!current) { clearTimeout(openTimer); loadToken++; return; }   // also drop a load in flight
    if (current.anchor === anchor && !current.pinned) scheduleClose();
  });

  // -- click and tap ----------------------------------------------------------------
  document.addEventListener('click', function (e) {
    var anchor = refOf(e.target);
    if (anchor && !inPreview(anchor)) {
      // Ctrl/Cmd/Shift-click and middle clicks keep their browser meaning (a new tab).
      if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      if (current && current.anchor === anchor && !current.sheet) {
        current.pinned = true;
        current.el.classList.add('pinned');
        return;
      }
      clearTimeout(openTimer);
      show(anchor, true);
      return;
    }
    if (!current) return;
    if (e.target.closest && (e.target.closest('.tip-close') || e.target.classList.contains('tip-backdrop'))) {
      close(true);
      return;
    }
    if (!inPreview(e.target)) close(false);
  });

  // -- keyboard ---------------------------------------------------------------------
  document.addEventListener('focusin', function (e) {
    if (returning) return;
    var anchor = refOf(e.target);
    if (!anchor || inPreview(anchor) || !anchor.matches(':focus-visible')) return;
    if (current && current.anchor === anchor) return;
    show(anchor, false);
  });
  document.addEventListener('focusout', function (e) {
    if (!current || current.pinned || current.sheet) return;
    if (refOf(e.target) === current.anchor && !inPreview(e.relatedTarget)) scheduleClose();
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && current) { e.preventDefault(); close(true); return; }
    // An unresolved reference is focusable; Enter or Space opens its explanation.
    var anchor = refOf(e.target);
    if (anchor && anchor.classList.contains('missing') && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      show(anchor, true);
    }
  });

  // -- housekeeping -----------------------------------------------------------------
  hoverMQ.addEventListener('change', function () { close(false); });
  window.addEventListener('resize', function () { if (current && !current.sheet) close(false); });
  document.addEventListener('htmx:beforeSwap', function () { close(false); });
  document.addEventListener('htmx:afterSettle', function (e) { adoptTitles(e.detail.target); });

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { adoptTitles(); });
  else adoptTitles();
})();
