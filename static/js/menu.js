// Context menus (documentation/CONTEXT-MENUS.md).
//
// An element with a menu carries data-menu (its menu's address); the gamemaster's
// campaign links and missing links have one by their href. It opens on:
//
//   right-click      mouse, a trackpad's two-finger click   a popover at the pointer
//   long press       touch, 450 ms without moving            a bottom sheet on a phone,
//                                                            the popover on a tablet
//   ContextMenu key  or Shift+F10 on the focused element     the popover beside it
//   ⋯                [data-menu-open] inside the element     the popover under it
//
// Shift + right-click leaves the browser's own menu. Text fields and editors keep theirs.
// The menu is fetched from the server for the reader and drawn as it comes: its items are
// links, forms that post, or dialogs to fetch ([data-menu-dialog]). An element with
// data-menu-local opens the menu it holds itself (the tracker's ⋯ menu, a <details>).
// Without JavaScript none of this runs; the ⋯ leads to the object's page instead.
(function () {
  'use strict';

  var PRESS = 450, SLOP = 8, MARGIN = 8;
  var coarse = window.matchMedia('(pointer: coarse)');
  var cache = new Map();
  var current = null;        // { el, back, target }
  var press = null;          // { timer, x, y, target }
  var swallowClick = false;  // the click a long press leaves behind
  var openedAt = 0;

  function gamemaster() {
    return !!document.querySelector('.role-gm-shell');
  }

  function targetOf(node) {
    if (!node || !node.closest) return null;
    if (node.closest('input, textarea, select, [contenteditable], .ctx-menu, .ctx-sheet, dialog.ctx-dialog')) return null;
    var el = node.closest('[data-menu], [data-menu-local]');
    if (el) return el;
    // A list row hands a right-click or long press anywhere on it to its ⋯ menu.
    var row = node.closest('.list-row');
    if (row) {
      el = row.querySelector('[data-menu]');
      if (el) return el;
    }
    if (gamemaster()) {
      el = node.closest('a.ref.lib[href], a.ref.missing.creatable[href]');
      if (el && !el.closest('.tip, .tip-sheet, .prose-preview-off')) return el;
    }
    return null;
  }

  function urlOf(target) {
    var url = target.getAttribute('data-menu');
    if (!url) {
      if (!target.matches('a[href]')) return null;
      url = '/menu/link?href=' + encodeURIComponent(target.getAttribute('href'));
    }
    var next = location.pathname + location.search;
    return url + (url.indexOf('?') < 0 ? '?' : '&') + 'next=' + encodeURIComponent(next);
  }

  function close(returnFocus) {
    if (!current) return;
    var target = current.target;
    current.el.remove();
    if (current.back) current.back.remove();
    if (target) (target.closest('.list-row') || target).classList.remove('ctx-target');
    current = null;
    if (returnFocus && target && target.focus) target.focus({ preventScroll: true });
  }

  function items(menu) {
    return Array.prototype.slice.call(menu.querySelectorAll('[role="menuitem"]'));
  }

  function place(menu, at, target) {
    var w = menu.offsetWidth, h = menu.offsetHeight;
    var x = at ? at.x : target.getBoundingClientRect().left;
    var y = at ? at.y : target.getBoundingClientRect().bottom + 4;
    if (x + w > innerWidth - MARGIN) x = Math.max(MARGIN, innerWidth - w - MARGIN);
    if (y + h > innerHeight - MARGIN) {
      var above = (at ? at.y : target.getBoundingClientRect().top) - h - 4;
      y = above >= MARGIN ? above : Math.max(MARGIN, innerHeight - h - MARGIN);
    }
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
  }

  function show(html, target, at, sheet) {
    close(false);
    var holder = document.createElement('div');
    holder.innerHTML = html.trim();
    var menu = holder.firstElementChild;
    if (!menu) return;
    var back = null;
    if (sheet) {
      back = document.createElement('div');
      back.className = 'ctx-back';
      back.addEventListener('click', function () { close(false); });
      menu.classList.add('ctx-sheet');
      var grab = document.createElement('div');
      grab.className = 'ctx-grab';
      menu.insertBefore(grab, menu.firstChild);
      document.body.appendChild(back);
    }
    document.body.appendChild(menu);
    if (!sheet) place(menu, at, target);
    if (window.htmx) window.htmx.process(menu);
    (target.closest('.list-row') || target).classList.add('ctx-target');
    current = { el: menu, back: back, target: target };
    openedAt = Date.now();
    var first = items(menu)[0];
    if (first) first.focus({ preventScroll: true });
    if (sheet && navigator.vibrate) { try { navigator.vibrate(10); } catch (e) { /* none */ } }
  }

  function openLocal(target) {
    var box = target.querySelector('details.cb-menu, details[data-local-menu]');
    if (!box) return false;
    close(false);
    box.open = true;
    var first = box.querySelector('button, a, summary');
    if (first) first.focus({ preventScroll: true });
    return true;
  }

  function open(target, at, viaTouch) {
    if (target.hasAttribute('data-menu-local')) return openLocal(target);
    var url = urlOf(target);
    if (!url) return false;
    var sheet = viaTouch && coarse.matches && innerWidth < 700;
    var cached = cache.get(url);
    if (cached) { show(cached, target, at, sheet); return true; }
    fetch(url, { headers: { 'HX-Request': 'true' }, credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) {
        if (!html) return;
        cache.set(url, html);
        show(html, target, at, sheet);
      })
      .catch(function () { /* the menu is a shortcut: nothing lost */ });
    return true;
  }

  // -- the dialog a menu item opens (a confirmation) ----------------------------------
  function dialog(url) {
    var next = location.pathname + location.search;
    url += (url.indexOf('?') < 0 ? '?' : '&') + 'dialog=1&next=' + encodeURIComponent(next);
    fetch(url, { headers: { 'HX-Request': 'true' }, credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.text() : null; })
      .then(function (html) {
        if (!html) return;
        var d = document.createElement('dialog');
        d.className = 'ctx-dialog';
        d.innerHTML = html;
        d.addEventListener('close', function () { d.remove(); });
        d.addEventListener('click', function (e) {
          if (e.target === d || (e.target.closest && e.target.closest('[data-close]'))) {
            e.preventDefault();
            d.close();
          }
        });
        document.body.appendChild(d);
        if (window.htmx) window.htmx.process(d);
        d.showModal();
      });
  }

  // -- mouse and trackpad --------------------------------------------------------------
  document.addEventListener('contextmenu', function (e) {
    if (e.shiftKey) return;
    if (Date.now() - openedAt < 700 && current) { e.preventDefault(); return; }  // after a long press
    var target = targetOf(e.target);
    if (!target) return;
    if (open(target, { x: e.clientX, y: e.clientY }, false)) e.preventDefault();
  });

  // -- touch: the long press -------------------------------------------------------------
  function cancelPress() {
    if (press) { clearTimeout(press.timer); press = null; }
  }
  document.addEventListener('pointerdown', function (e) {
    if (e.pointerType === 'mouse') return;
    var target = targetOf(e.target);
    if (!target) return;
    cancelPress();
    press = {
      x: e.clientX, y: e.clientY, target: target,
      timer: setTimeout(function () {
        var p = press;
        press = null;
        if (!p) return;
        swallowClick = true;
        open(p.target, { x: p.x, y: p.y }, true);
      }, PRESS),
    };
  }, { passive: true });
  document.addEventListener('pointermove', function (e) {
    if (press && (Math.abs(e.clientX - press.x) > SLOP || Math.abs(e.clientY - press.y) > SLOP)) cancelPress();
  }, { passive: true });
  ['pointerup', 'pointercancel', 'scroll'].forEach(function (name) {
    document.addEventListener(name, cancelPress, { passive: true, capture: true });
  });
  // The tap a long press ends with must not also follow the link or open a preview.
  window.addEventListener('click', function (e) {
    if (swallowClick) {
      swallowClick = false;
      if (!(e.target.closest && e.target.closest('.ctx-menu'))) {
        e.preventDefault();
        e.stopPropagation();
      }
    }
  }, true);

  // -- the ⋯, the items, closing --------------------------------------------------------
  document.addEventListener('click', function (e) {
    var more = e.target.closest && e.target.closest('[data-menu-open]');
    if (more) {
      var target = more.closest('[data-menu]');
      if (target) {
        e.preventDefault();
        if (current && current.target === target) { close(true); return; }
        var r = more.getBoundingClientRect();
        open(target, { x: r.left, y: r.bottom + 4 }, coarse.matches);
      }
      return;
    }
    var item = e.target.closest && e.target.closest('.ctx-menu [data-menu-dialog]');
    if (item) {
      e.preventDefault();
      var url = item.getAttribute('data-menu-dialog');
      close(false);
      dialog(url);
      return;
    }
    if (current && !(e.target.closest && e.target.closest('.ctx-menu'))) close(false);
    else if (current && e.target.closest && e.target.closest('.ctx-menu a, .ctx-menu button[type="submit"]')) {
      setTimeout(function () { close(false); }, 0);
    }
  });
  window.addEventListener('resize', function () { close(false); });
  document.addEventListener('scroll', function () {
    if (current && !current.back) close(false);
  }, { passive: true, capture: true });
  // A live update that swaps the element away (the sheet, a party card) takes its menu
  // with it; one elsewhere on the page leaves it open.
  document.addEventListener('htmx:afterSwap', function () {
    if (current && !document.body.contains(current.target)) close(false);
  });

  // -- keyboard --------------------------------------------------------------------------
  document.addEventListener('keydown', function (e) {
    if (current) {
      var list = items(current.el);
      var at = list.indexOf(document.activeElement);
      if (e.key === 'Escape') { e.preventDefault(); close(true); return; }
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        var step = e.key === 'ArrowDown' ? 1 : -1;
        var next = list[(at + step + list.length) % list.length];
        if (next) next.focus();
        return;
      }
      if (e.key === 'Home' || e.key === 'End') {
        e.preventDefault();
        (e.key === 'Home' ? list[0] : list[list.length - 1]).focus();
        return;
      }
      if (e.key === 'Tab') { close(false); return; }
    }
    if (e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey)) {
      var target = targetOf(document.activeElement);
      if (target && open(target, null, false)) e.preventDefault();
    }
  });
})();
