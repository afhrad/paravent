// The strip of running fights above a character sheet (documentation/COMBAT.md §5).
// Its Server-Sent Events stream carries a message whenever one of the character's
// fights changes — one starting, a new round, someone else's turn — and the strip is
// refetched. Without JavaScript the strip is simply what the page showed when loaded.
(function () {
  'use strict';

  function strip() { return document.getElementById('combat-strip'); }

  function refresh() {
    var s = strip(); if (!s || !window.htmx) return;
    htmx.ajax('GET', s.dataset.url, { target: '#combat-strip', swap: 'outerHTML' });
  }

  function connect() {
    var s = strip();
    if (!window.EventSource || !s) return;
    var source = new EventSource(s.dataset.events);
    source.addEventListener('revision', function (e) { if (e.data) refresh(); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', connect);
  else connect();
})();
