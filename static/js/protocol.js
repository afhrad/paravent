// The log panel (documentation/PROTOCOL.md §5). The top-bar button is a link to /log,
// which works without this file; here it opens the panel instead, fetches what the
// server lets this reader see, follows every append over one event stream, and keeps a
// badge with what arrived since the reader last looked (remembered on this device).
(function () {
  'use strict';

  var panel = document.getElementById('log-panel');
  var button = document.querySelector('.shell-log');
  if (!panel || !button || typeof panel.showModal !== 'function' || !window.fetch) return;

  var content = panel.querySelector('.logpanel-content');
  var badge = button.querySelector('.log-badge');
  var SEEN = 'pv-log-seen';
  var query = panel.dataset.query || '';
  var paged = false;  // older rows loaded: a live refresh would throw them away

  function stored(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
  function store(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* none */ } }

  function get(url) {
    return fetch(url, { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); });
  }

  function load() {
    return get('/log/panel?' + query).then(function (html) {
      content.innerHTML = html;
      paged = false;
      var body = content.querySelector('.logpanel-body');
      if (body && body.dataset.newest) store(SEEN, body.dataset.newest);
      showBadge(0);
    }).catch(function () { /* keep what is shown */ });
  }

  function older(rowsQuery) {
    get('/log/panel?' + rowsQuery).then(function (html) {
      var holder = document.createElement('div');
      holder.innerHTML = html;
      var list = content.querySelector('.log-list');
      var more = content.querySelector('.log-more');
      if (more) more.remove();
      var rows = holder.querySelector('.log-list');
      if (list && rows) while (rows.firstChild) list.appendChild(rows.firstChild);
      paged = true;
    });
  }

  function showBadge(count) {
    if (!badge) return;
    badge.textContent = count > 99 ? '99+' : String(count);
    badge.hidden = !count;
  }

  function refreshBadge() {
    if (panel.open) return;
    var since = stored(SEEN) || '';
    get('/log/badge?since=' + encodeURIComponent(since)).then(function (text) {
      showBadge(parseInt(text, 10) || 0);
    }).catch(function () { /* the badge waits for the next change */ });
  }

  button.addEventListener('click', function (e) {
    if (e.metaKey || e.ctrlKey || e.shiftKey) return;  // a new tab: the page
    e.preventDefault();
    panel.showModal();
    load();
  });

  panel.addEventListener('click', function (e) {
    if (e.target === panel) { panel.close(); return; }
    var close = e.target.closest('[data-log-close]');
    if (close) { panel.close(); return; }
    var chip = e.target.closest('[data-log-query]');
    if (chip) { e.preventDefault(); query = chip.dataset.logQuery; load(); return; }
    var more = e.target.closest('[data-log-older]');
    if (more) { e.preventDefault(); older(more.dataset.logOlder); }
  });

  // "The whole log" under a slice on the page opens the panel on the same filter.
  document.addEventListener('click', function (e) {
    var whole = e.target.closest && e.target.closest('[data-log-open]');
    if (!whole) return;
    e.preventDefault();
    query = whole.getAttribute('href').split('?')[1] || '';
    panel.showModal();
    load();
  });

  // The gamemaster's Reveal: posted, then the panel shows what the table now reads.
  panel.addEventListener('submit', function (e) {
    var revealForm = e.target.closest('[data-log-reveal]');
    if (!revealForm) return;
    e.preventDefault();
    fetch(revealForm.action, { method: 'POST', body: new FormData(revealForm), credentials: 'same-origin' })
      .then(function () { load(); });
  });

  // -- toasts (PROTOCOL.md §6) -----------------------------------------------------
  // What arrives after the page was made pops up, by the reader's setting: at most
  // three, newest on top, gone after a while, sooner by ✕; the timer pauses while the
  // pointer or focus is on one. A tap elsewhere on it opens the panel.
  var layer = document.getElementById('log-toasts');
  var since = layer ? layer.dataset.since : '';
  var asking = false, again = false;
  var SHOWN_FOR = 6000, MOST = 3;

  function dismiss(card) { if (card.parentNode) card.parentNode.removeChild(card); }

  function keep(card) {
    var left = SHOWN_FOR, started = Date.now(), timer = setTimeout(function () { dismiss(card); }, left);
    function pause() { clearTimeout(timer); left -= Date.now() - started; }
    function resume() {
      started = Date.now();
      timer = setTimeout(function () { dismiss(card); }, Math.max(1500, left));
    }
    card.addEventListener('mouseenter', pause);
    card.addEventListener('mouseleave', resume);
    card.addEventListener('focusin', pause);
    card.addEventListener('focusout', resume);
  }

  function toasts() {
    if (!layer || layer.dataset.mode === 'off' || !since) return;
    if (asking) { again = true; return; }
    asking = true;
    get('/log/toasts?since=' + encodeURIComponent(since)).then(function (html) {
      var holder = document.createElement('div');
      holder.innerHTML = html;
      var batch = holder.firstElementChild;
      if (!batch) return;
      if (batch.dataset.newest) since = batch.dataset.newest;
      if (panel.open) return;  // the panel shows them already
      Array.prototype.slice.call(batch.children).reverse().forEach(function (card) {
        layer.insertBefore(card, layer.firstChild);
        keep(card);
      });
      while (layer.children.length > MOST) dismiss(layer.lastElementChild);
    }).catch(function () { /* the next change asks again */ }).then(function () {
      asking = false;
      if (again) { again = false; toasts(); }
    });
  }

  if (layer) {
    layer.addEventListener('click', function (e) {
      var card = e.target.closest('.log-toast');
      if (!card) return;
      dismiss(card);
      if (!e.target.closest('[data-toast-close]')) { panel.showModal(); load(); }
    });
    layer.addEventListener('keydown', function (e) {
      var card = e.target.closest('.log-toast');
      if (card && e.key === 'Escape') dismiss(card);
    });
  }

  if (window.EventSource) {
    var source = new EventSource('/log/events');
    source.addEventListener('revision', function (e) {
      if (!e.data) return;  // the stream's opening line, before anything new
      if (panel.open) { if (!paged) load(); } else refreshBadge();
      toasts();
      // Slices of the log on the page (the sheet's dice log, the tracker's) refetch.
      document.body.dispatchEvent(new CustomEvent('paravent-log'));
    });
  }
  refreshBadge();
})();
