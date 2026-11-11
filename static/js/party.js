// The gamemaster's party overview, live. One Server-Sent Events stream carries every
// character change as "<id> <revision>"; the card for that character is refetched and
// swapped. A character that appears or disappears refetches the whole grid; a combat's
// change ("*combats <id> <revision>") refetches the strip of running fights.
//
// A chapter showing a character's card ({@character} alone on its line) listens too:
// its cards are refreshed the same way, in the world the chapter's embed names.
(function () {
  'use strict';

  function grid() { return document.getElementById('party'); }

  function indicator(state) {
    var el = document.querySelector('[data-live-indicator]');
    if (!el) return;
    el.classList.toggle('on', state === 'on');
    el.classList.toggle('off', state === 'off');
  }

  function refreshCombats() {
    var c = document.getElementById('party-combats'); if (!c || !window.htmx) return;
    htmx.ajax('GET', c.dataset.url, { target: '#party-combats', swap: 'outerHTML' });
  }

  function refreshGrid() {
    var g = grid(); if (!g || !window.htmx) return;
    htmx.ajax('GET', g.dataset.url, { target: '#party', swap: 'outerHTML' });
  }

  // A card being worked on — a number half typed — is refreshed once the field is left.
  var pending = {};
  function busy(id) {
    var a = document.activeElement;
    var card = document.getElementById('pc-' + id);
    return a && card && card.contains(a) && a.tagName === 'INPUT' && a.value !== '';
  }
  document.addEventListener('focusout', function () {
    setTimeout(function () {
      Object.keys(pending).forEach(function (id) {
        if (!busy(id)) { delete pending[id]; refreshCard(id); }
      });
    }, 0);
  });

  // An open conditions menu stays open when its card is swapped, so several
  // conditions can be toggled in a row.
  var openMenus = {};
  document.addEventListener('htmx:beforeSwap', function (e) {
    var target = e.detail.target;
    if (!target || !target.classList || !target.classList.contains('pc-card')) return;
    var menu = target.querySelector('details.pc-conds');
    openMenus[target.id] = !!(menu && menu.open);
  });
  document.addEventListener('htmx:afterSettle', function () {
    Object.keys(openMenus).forEach(function (cardId) {
      var menu = document.querySelector('#' + CSS.escape(cardId) + ' details.pc-conds');
      if (menu && openMenus[cardId]) menu.open = true;
      delete openMenus[cardId];
    });
  });

  // The world a card is shown in: the overview's filter, or a chapter's embed's.
  function worldOf(card) {
    var g = grid();
    if (g) return new URL(g.dataset.url, location.href).searchParams.get('world') || '';
    var holder = card && card.closest('[data-party-world]');
    return holder ? holder.dataset.partyWorld : '';
  }

  function refreshCard(id) {
    if (!window.htmx) return;
    if (busy(id)) { pending[id] = true; return; }
    var world = worldOf(document.getElementById('pc-' + id));
    var url = '/gm/party/' + encodeURIComponent(id) + '/card' + (world ? '?world=' + encodeURIComponent(world) : '');
    htmx.ajax('GET', url, { target: '#pc-' + CSS.escape(id), swap: 'outerHTML' });
  }

  function connect() {
    if (!window.EventSource || !(grid() || document.querySelector('.pc-card'))) return;
    var source = new EventSource('/gm/events');
    source.addEventListener('open', function () { indicator('on'); });
    source.addEventListener('error', function () { indicator('off'); });
    source.addEventListener('revision', function (e) {
      indicator('on');
      if (!e.data) return; // the stream's opening message carries no change
      var parts = e.data.split(' ');
      if (parts[0] === '*combats') { refreshCombats(); return; }
      var id = parts[0], rev = parts[1] || '';
      var card = document.getElementById('pc-' + id);
      if (!rev || !card) { if (grid()) refreshGrid(); return; } // deleted, or new / elsewhere
      if (card.dataset.rev !== rev) refreshCard(id);
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', connect);
  else connect();
})();
