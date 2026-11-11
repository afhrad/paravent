// The combat tracker, live (documentation/COMBAT.md §7). The board's Server-Sent Events
// stream carries the combat's revision on every change — from this screen, another
// gamemaster device, a player's sheet or a hand edit — and the board is refetched when it
// differs from what is shown. Every button already swaps the board through htmx; this
// file only keeps other screens in step and remembers what the swap would lose.
(function () {
  'use strict';

  function board() { return document.getElementById('combat-board'); }

  // A half-typed amount is not thrown away by someone else's change: the refresh waits
  // until the field is left.
  var pending = false;
  function busy() {
    var a = document.activeElement, b = board();
    return a && b && b.contains(a) && a.tagName === 'INPUT' && a.value !== '';
  }
  document.addEventListener('focusout', function () {
    setTimeout(function () { if (pending && !busy()) { pending = false; refresh(); } }, 0);
  });

  function refresh() {
    var b = board(); if (!b || !window.htmx) return;
    if (busy()) { pending = true; return; }
    htmx.ajax('GET', b.dataset.url, { target: '#combat-board', swap: 'outerHTML' });
  }

  // An open menu stays open when the board is swapped, so several conditions can be
  // toggled in a row; so does the log, and the order keeps where it was scrolled to.
  var open = [], logOpen = false, scrolled = 0, picked = [];
  // A save effect's target list: which are open, and the choices the gamemaster has
  // changed by hand — a player's roll arriving must not undo them.
  var folds = [], picks = {};
  document.addEventListener('change', function (e) {
    var s = e.target.closest && e.target.closest('.cb-pending-fold select');
    if (s) s.dataset.picked = '1';
  });
  function scroller() { return document.querySelector('#combat-board .cb-scroll'); }
  // The gamemaster's "what the players know" stays open when the side panel is swapped
  // by its own Reveal or Forget.
  var knownOpen = false;
  document.addEventListener('htmx:beforeSwap', function (e) {
    if (!e.detail.target || e.detail.target.id !== 'combat-side') return;
    knownOpen = !!document.querySelector('#combat-side details.cb-known[open]');
  });
  document.addEventListener('htmx:afterSwap', function (e) {
    if (!knownOpen || !e.detail.target) return;
    var known = document.querySelector('#combat-side details.cb-known');
    if (known) known.open = true;
    knownOpen = false;
  });
  document.addEventListener('htmx:beforeSwap', function (e) {
    if (!e.detail.target || e.detail.target.id !== 'combat-board') return;
    var s = scroller();
    scrolled = s ? s.scrollTop : 0;
    open = Array.prototype.map.call(
      document.querySelectorAll('#combat-board details.cb-menu[open]'),
      function (d) { var row = d.closest('.combatant'); return row ? row.id : null; }
    ).filter(Boolean);
    logOpen = !!document.querySelector('#combat-board details.cb-log[open]');
    picked = Array.prototype.map.call(
      document.querySelectorAll('#combat-board details.pv-pick[open]'),
      function (d) {
        var facts = d.querySelector('details.pv-details');
        return { id: d.dataset.member, facts: !!(facts && facts.open) };
      }
    );
    // Aiming is the menu's answer: it closes.
    var from = e.detail.requestConfig && e.detail.requestConfig.elt;
    if (from && from.closest && from.closest('.pv-aim')) picked = [];
    folds = Array.prototype.map.call(
      document.querySelectorAll('#combat-board details.cb-pending-fold[open]'),
      function (d) { return d.dataset.at; }
    );
    picks = {};
    document.querySelectorAll('#combat-board .cb-pending-fold select[data-picked]').forEach(
      function (s) { picks[s.closest('details').dataset.at + '|' + s.name] = s.value; }
    );
  });
  // Restored on the swap itself, before the browser paints the new board at the top.
  document.addEventListener('htmx:afterSwap', function () {
    var s = scroller();
    if (s && scrolled) s.scrollTop = scrolled;
  });
  // A character's side panel shows HP, slots and conditions: it follows every change
  // of the board, which is also where a change to the character file arrives.
  function refreshSide() {
    var side = document.getElementById('combat-side');
    if (side && side.dataset.live && window.htmx) {
      htmx.ajax('GET', side.dataset.live, { target: '#combat-side', swap: 'outerHTML' });
    }
  }
  document.addEventListener('htmx:afterSettle', function (e) {
    if (e.detail.target && e.detail.target.id === 'combat-board') refreshSide();
  });
  document.addEventListener('htmx:afterSettle', function () {
    var s = scroller();
    if (s && scrolled) { s.scrollTop = scrolled; scrolled = 0; }
    open.forEach(function (id) {
      var menu = document.querySelector('#' + CSS.escape(id) + ' details.cb-menu');
      if (menu) menu.open = true;
    });
    open = [];
    var log = document.querySelector('#combat-board details.cb-log');
    if (log && logOpen) log.open = true;
    // The players' view: a row's menu, and its details, stay open across a refresh.
    picked.forEach(function (kept) {
      var pick = document.querySelector(
        '#combat-board details.pv-pick[data-member="' + CSS.escape(kept.id) + '"]');
      if (!pick) return;
      pick.open = true;
      var facts = pick.querySelector('details.pv-details');
      if (facts && kept.facts) facts.open = true;
    });
    picked = [];
    document.querySelectorAll('#combat-board details.cb-pending-fold').forEach(function (d) {
      if (folds.indexOf(d.dataset.at) >= 0) d.open = true;
      d.querySelectorAll('select').forEach(function (s) {
        var kept = picks[d.dataset.at + '|' + s.name];
        if (kept !== undefined) { s.value = kept; s.dataset.picked = '1'; }
      });
    });
    folds = []; picks = {};
  });

  // Selecting a combatant loads its statblock beside the board; the board then follows,
  // so the selection is marked and kept by every form.
  document.addEventListener('click', function (e) {
    var link = e.target.closest && e.target.closest('a.cb-name');
    var b = board();
    if (!link || !b) return;
    var sel = new URL(link.href, location.href).searchParams.get('sel') || '';
    var url = new URL(b.dataset.url, location.href);
    url.searchParams.set('sel', sel);
    b.dataset.url = url.pathname + url.search;
    history.replaceState(null, '', link.href);
    document.addEventListener('htmx:afterSettle', function once(ev) {
      if (ev.detail.target && ev.detail.target.id === 'combat-side') {
        document.removeEventListener('htmx:afterSettle', once);
        refresh();
      }
    });
  });

  // A creature's Translate opens the editor in a popup over the tracker. Saving there
  // closes it; drafts and saves alike reload the creature (and the board's names).
  function popup() { return document.getElementById('tr-dialog'); }
  document.addEventListener('click', function (e) {
    var dialogEl = popup();
    var open = e.target.closest && e.target.closest('[data-translate]');
    if (open && dialogEl && window.htmx && typeof dialogEl.showModal === 'function') {
      e.preventDefault();
      htmx.ajax('GET', open.dataset.translate, { target: '#tr-dialog-body', swap: 'innerHTML' })
        .then(function () { if (!dialogEl.open) dialogEl.showModal(); });
      return;
    }
    var close = e.target.closest && e.target.closest('[data-dialog-close]');
    if (close && dialogEl) dialogEl.close();
  });
  function reloadSide() {
    var side = document.getElementById('combat-side');
    if (side && side.dataset.url && window.htmx) {
      htmx.ajax('GET', side.dataset.url, { target: '#combat-side', swap: 'outerHTML' });
    }
  }
  document.addEventListener('paravent-translated', function (e) {
    var dialogEl = popup();
    if (e.detail && e.detail.close && dialogEl) dialogEl.close();
    reloadSide();
    refresh();
  });
  // A spell cast or restored: its count of what is left lives in the side panel.
  document.addEventListener('paravent-side', reloadSide);

  function connect() {
    var b = board();
    if (!window.EventSource || !b) return;
    var source = new EventSource(b.dataset.events);
    source.addEventListener('revision', function (e) {
      var current = board();
      if (!e.data || !current) return;
      if (current.dataset.rev !== e.data) refresh();
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', connect);
  else connect();
})();
