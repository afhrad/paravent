// Character sheet behaviour. The sheet works without this file (every control is a
// form, every tab a link); this makes it instant and live:
//
//  * tabs, phone views, the spell-level filter and the advantage state switch without
//    a request, survive every update, and ride along with every action; the server
//    keeps the first three per user, so the sheet opens on them after a new login
//  * a live connection (Server-Sent Events) reloads #sheet whenever the character
//    changes — another screen, the gamemaster, or a hand edit of the file
//  * an update never closes an open spell description, and never wipes a number
//    someone is in the middle of typing
(function () {
  'use strict';

  var store = {
    get: function (k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } },
    set: function (k, v) { try { sessionStorage.setItem(k, v); } catch (e) {} }
  };

  function sheet() { return document.getElementById('sheet'); }
  function cid() { var s = sheet(); return s ? s.dataset.character : null; }
  function key(name) { return 'pv-sheet-' + cid() + '-' + name; }

  var openDetails = [];
  var pendingRefresh = false;

  // -- view state ------------------------------------------------------------------

  function setTab(tab) {
    var s = sheet(); if (!s || !tab) return;
    if (!s.querySelector('[data-tab-link="' + tab + '"]')) return;
    s.dataset.tab = tab;
    s.querySelectorAll('[data-tab-link]').forEach(function (a) {
      if (a.dataset.tabLink === tab) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
    store.set(key('tab'), tab);
  }

  function setView(view) {
    var s = sheet(); if (!s || !view) return;
    s.dataset.m = view;
    s.querySelectorAll('[data-view-link]').forEach(function (a) {
      if (a.dataset.viewLink === view) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
    });
    store.set(key('view'), view);
  }

  function setFilter(level) {
    var s = sheet(); if (!s) return;
    s.querySelectorAll('.fchip').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.level === level));
    });
    s.querySelectorAll('.cs-spell[data-level]').forEach(function (row) {
      row.hidden = level !== '' && row.dataset.level !== level;
    });
    store.set(key('filter'), level);
  }

  function advantage() { return store.get(key('adv')) || '0'; }
  function setAdvantage(value) {
    store.set(key('adv'), value);
    var s = sheet(); if (!s) return;
    s.querySelectorAll('.advset [data-adv]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b.dataset.adv === value));
    });
  }

  // Private rolls: only the roller and the gamemaster read them in the log.
  function privately() { return store.get(key('private')) === '1' ? '1' : '0'; }
  function setPrivately(value) {
    store.set(key('private'), value);
    var s = sheet(); if (!s) return;
    s.querySelectorAll('[data-private]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(value === '1'));
    });
  }

  function restore() {
    var params = new URLSearchParams(location.search);
    setTab(params.get('tab') || store.get(key('tab')) || (sheet() && sheet().dataset.tab));
    setView(params.get('view') || store.get(key('view')) || (sheet() && sheet().dataset.m));
    var kept = store.get(key('filter'));
    setFilter(kept !== null ? kept : (sheet() && sheet().dataset.filter) || '');
    setAdvantage(advantage());
    setPrivately(privately());
    var s = sheet(); if (!s) return;
    openDetails.forEach(function (k) {
      var d = s.querySelector('details[data-key="' + CSS.escape(k) + '"]');
      if (d) d.open = true;
    });
  }

  function remember() {
    var s = sheet(); if (!s) return;
    openDetails = Array.prototype.map.call(s.querySelectorAll('details[data-key][open]'), function (d) {
      return d.dataset.key;
    });
  }

  // Tell the server how the sheet is shown now (app/services/places.py). A beacon
  // outlives the page, so a choice made just before leaving is kept too.
  function report() {
    var s = sheet(); if (!s) return;
    var data = new FormData();
    data.append('tab', s.dataset.tab || '');
    data.append('view', s.dataset.m || '');
    data.append('filter', store.get(key('filter')) || '');
    var url = '/characters/' + s.dataset.character + '/view';
    if (navigator.sendBeacon) navigator.sendBeacon(url, data);
    else if (window.fetch) fetch(url, { method: 'POST', body: data, credentials: 'same-origin' });
  }

  // -- clicks ------------------------------------------------------------------------

  document.addEventListener('click', function (e) {
    var t = e.target.closest ? e.target : null; if (!t) return;
    var link = t.closest('[data-tab-link]');
    if (link) { e.preventDefault(); setTab(link.dataset.tabLink); report(); return; }
    var view = t.closest('[data-view-link]');
    if (view) {
      e.preventDefault(); setView(view.dataset.viewLink); window.scrollTo(0, 0); report(); return;
    }
    var chip = t.closest('.fchip');
    if (chip) { setFilter(chip.dataset.level); report(); return; }
    var adv = t.closest('.advset [data-adv]');
    if (adv) { setAdvantage(adv.dataset.adv); return; }
    if (t.closest('[data-private]')) setPrivately(privately() === '1' ? '0' : '1');
  });

  // -- htmx integration ---------------------------------------------------------------

  document.addEventListener('htmx:configRequest', function (e) {
    var s = sheet(); if (!s || !s.contains(e.detail.elt)) return;
    e.detail.parameters.adv = advantage();
    e.detail.parameters.private = privately();
    e.detail.parameters.tab = s.dataset.tab || '';
    e.detail.parameters.view = s.dataset.m || '';
  });

  document.addEventListener('htmx:beforeSwap', function (e) {
    if (e.detail.target && e.detail.target.id === 'sheet') remember();
  });

  document.addEventListener('htmx:afterSettle', function (e) {
    if (e.detail.target && e.detail.target.id === 'sheet') restore();
    else if (sheet() && !sheet().isConnected) restore();
  });
  document.addEventListener('htmx:load', function (e) {
    if (e.detail.elt && e.detail.elt.id === 'sheet') restore();
  });

  // -- live updates -------------------------------------------------------------------

  function typing() {
    var a = document.activeElement, s = sheet();
    return a && s && s.contains(a) && a.tagName === 'INPUT' && a.value !== '';
  }

  function refresh() {
    var s = sheet(); if (!s || !window.htmx) return;
    if (typing()) { pendingRefresh = true; return; }
    pendingRefresh = false;
    var q = new URLSearchParams({ tab: s.dataset.tab || '', view: s.dataset.m || '' });
    htmx.ajax('GET', '/characters/' + s.dataset.character + '/sheet?' + q.toString(),
              { target: '#sheet', swap: 'outerHTML' });
  }

  document.addEventListener('focusout', function () {
    if (pendingRefresh) setTimeout(function () { if (!typing()) refresh(); }, 0);
  });

  function indicator(state) {
    var el = document.querySelector('[data-live-indicator]');
    if (!el) return;
    el.classList.toggle('on', state === 'on');
    el.classList.toggle('off', state === 'off');
  }

  function connect() {
    var id = cid();
    if (!id || !window.EventSource) return;
    var source = new EventSource('/characters/' + id + '/events');
    source.addEventListener('open', function () { indicator('on'); });
    source.addEventListener('error', function () { indicator('off'); });
    source.addEventListener('revision', function (e) {
      indicator('on');
      var s = sheet();
      if (s && e.data !== s.dataset.rev) refresh();
    });
    // The indicator lives inside #sheet, so re-apply it after every swap.
    document.addEventListener('htmx:afterSettle', function () {
      indicator(source.readyState === 1 ? 'on' : 'off');
    });
  }

  function start() { restore(); connect(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
