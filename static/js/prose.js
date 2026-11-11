// Chapters: the page follows edits made anywhere, and the editor previews as you type.
//
// * #chapter-live carries the chapter's revision and its event stream. When the file
//   changes — saved here, on another screen, or in a text editor — the text is fetched
//   again and swapped in, keeping the reader's place.
// * A textarea with data-preview posts its text there after a pause and shows the
//   result in #preview. Nothing is saved until the form is submitted.
// * The preview follows the cursor: it shows the heading the cursor is under (or, in a
//   text without headings, the same share of the way down).
// * #check (data-check) lists what in the text leads nowhere, refreshed with the
//   preview: a line number goes there, a suggestion replaces the reference on its line,
//   "Create" makes the NPC, encounter or treasure where the chapter is.
(function () {
  'use strict';

  function follow() {
    var root = document.getElementById('chapter-live');
    if (!root || !window.EventSource || !root.dataset.live) return;
    var source = new EventSource(root.dataset.live);
    source.addEventListener('revision', function (e) {
      var current = document.getElementById('chapter-live');
      if (!current || !e.data || e.data === current.dataset.rev) return;
      fetch(current.dataset.live.replace(/\/events$/, '/live'), { credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.text() : null; })
        .then(function (html) {
          if (!html) return;
          var y = window.scrollY;
          var holder = document.createElement('div');
          holder.innerHTML = html;
          var fresh = holder.firstElementChild;
          if (fresh) { current.replaceWith(fresh); window.scrollTo(0, y); }
        });
    });
  }

  function preview() {
    var area = document.querySelector('textarea[data-preview]');
    var out = document.getElementById('preview');
    if (!area || !out) return;
    var timer = null, token = 0, dirty = false, submitting = false;

    var check = document.getElementById('check');

    function render() {
      var mine = ++token;
      var body = new URLSearchParams({ body: area.value });
      fetch(area.dataset.preview, { method: 'POST', body: body, credentials: 'same-origin' })
        .then(function (r) { return r.ok ? r.text() : null; })
        .then(function (html) { if (html !== null && mine === token) out.innerHTML = html; });
      if (check && check.dataset.check) {
        fetch(check.dataset.check, { method: 'POST', body: new URLSearchParams({ body: area.value }), credentials: 'same-origin' })
          .then(function (r) { return r.ok ? r.text() : null; })
          .then(function (html) { if (html !== null && mine === token) check.innerHTML = html; });
      }
    }

    // Where line ``n`` (from 1) starts and ends in the text.
    function lineSpan(n) {
      var start = 0;
      for (var i = 1; i < n; i++) {
        var next = area.value.indexOf('\n', start);
        if (next < 0) break;
        start = next + 1;
      }
      var end = area.value.indexOf('\n', start);
      return [start, end < 0 ? area.value.length : end];
    }

    if (check) {
      check.addEventListener('click', function (e) {
        var el = e.target.closest && e.target.closest('button');
        if (!el) return;
        if (el.classList.contains('check-line')) {
          var at = lineSpan(Number(el.dataset.line));
          area.focus();
          area.setSelectionRange(at[0], at[1]);
          // Bring the selected line into view: the browser scrolls to the caret on blur/focus.
          var lines = area.value.slice(0, at[0]).split('\n').length - 1;
          var height = parseFloat(getComputedStyle(area).lineHeight) || 20;
          area.scrollTop = Math.max(0, lines * height - area.clientHeight / 3);
        } else if (el.classList.contains('check-fix')) {
          var span = lineSpan(Number(el.dataset.line));
          var line = area.value.slice(span[0], span[1]);
          var where = line.indexOf(el.dataset.from);
          if (where < 0) return;
          var from = span[0] + where;
          window.ParaventText.replace(area, from, from + el.dataset.from.length, el.dataset.to);
        } else if (el.classList.contains('check-create')) {
          var body = new URLSearchParams({ kind: el.dataset.kind, name: el.dataset.name, scope: area.dataset.scope || '' });
          el.disabled = true;
          fetch('/gm/editor/create', { method: 'POST', body: body, credentials: 'same-origin' })
            .then(function () { render(); });
        }
      });
    }

    area.addEventListener('input', function () {
      dirty = true;
      clearTimeout(timer);
      timer = setTimeout(render, 350);
    });

    // The preview shows where the cursor is: the heading above it, by count.
    var pane = out.closest('.preview-pane');
    var following = null;
    function follow() {
      if (!pane || pane.scrollHeight <= pane.clientHeight) return;
      var before = area.value.slice(0, area.selectionStart).split('\n');
      var fenced = false, count = 0;
      before.forEach(function (line) {
        if (/^ {0,3}(`{3,}|~{3,})/.test(line)) fenced = !fenced;
        else if (!fenced && /^#{1,6}\s/.test(line)) count += 1;
      });
      // The text's own headings only: a folded statblock has headings of its own.
      var headings = out.querySelectorAll(':scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > h5, :scope > h6');
      var top;
      if (headings.length && count) {
        var target = headings[Math.min(count, headings.length) - 1];
        top = target.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop - 8;
      } else if (headings.length) {
        top = 0;
      } else {
        var share = before.length / Math.max(1, area.value.split('\n').length);
        top = share * (pane.scrollHeight - pane.clientHeight);
      }
      pane.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
    }
    function soon() { clearTimeout(following); following = setTimeout(follow, 150); }
    ['keyup', 'click'].forEach(function (name) { area.addEventListener(name, soon); });
    area.addEventListener('keydown', function (e) {
      // ⌘S / Ctrl+S saves rather than offering to save the web page.
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        submitting = true;
        area.form.requestSubmit();
      }
    });
    area.form.addEventListener('submit', function () { submitting = true; });
    window.addEventListener('beforeunload', function (e) {
      if (dirty && !submitting) { e.preventDefault(); e.returnValue = ''; }
    });
  }

  function start() { follow(); preview(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
