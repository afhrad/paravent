// The Markdown editors' drop-down at the cursor (documentation/ADVENTURES.md §5.6).
//
//   {@creature gob    inside a tag — creatures; completing writes the name and the "}"
//   {@ alone          which kinds there are (spell, creature, npc, encounter…), and
//   > [!              the callouts (read, secret, sidebar, variant, handout, info, quote…)
//
// Ctrl+Space opens it on demand: after "{@spell " with no name yet it lists what the
// corpus links to most in the world's books (the world's own NPCs, encounters… by
// name); inside a tag already written it offers names for it — completing replaces the
// name, and a reference's book, keeping the rest of the tag; after Esc it opens again.
// Choosing a kind goes straight on to that list.
//
// A tag alone on its line shows the object itself (the card, the folded entry).
//
// An NPC, encounter or treasure that does not exist yet is offered to create where the
// editor's record is (its textarea's data-scope), and is then named in the text.
//
// An image dropped onto or pasted into an editor with data-upload is stored beside the
// record and written where the cursor was: ![map](media/map.png).
//
// Above each editor, a toolbar: heading, bold and italic (⌘B, ⌘I; pressed again on
// marked text they unmark it), bulleted and numbered lists, a link, read-aloud and
// secret boxes, a reference on its own line (which opens the kinds drop-down), the insert pop-up, an image from a
// file, a table — and, beside a preview, writing in full width (kept per browser).
//
// ↑ ↓ move, Enter or Tab completes, Esc closes. The list comes from /search/suggest:
// the world's books, the reference as short as the world allows ("Fireball", or
// "Fireball|PHB"), and for npc / encounter / treasure / character / chapter the
// world's own.
(function () {
  'use strict';

  var DELAY = 120;
  var INLINE = /\{@([A-Za-z]+) ([^{}|]*)$/;
  var INLINE_KIND = /\{@([A-Za-z]*)$/;
  var CALLOUT_KIND = /^\s*>\s*\[!([A-Za-z]*)$/;
  // Paravent's own tags: their second slot is a label, not a book.
  var LIBRARY = ['npc', 'encounter', 'treasure', 'character', 'chapter'];
  var SLOT = /^[^{}|\n]*/;
  var MIRRORED = [
    'boxSizing', 'width', 'fontFamily', 'fontSize', 'fontWeight', 'fontStyle',
    'letterSpacing', 'lineHeight', 'paddingTop', 'paddingRight', 'paddingBottom',
    'paddingLeft', 'borderTopWidth', 'borderRightWidth', 'borderBottomWidth',
    'borderLeftWidth', 'textTransform', 'wordSpacing', 'tabSize', 'textIndent'
  ];

  function world() {
    var field = document.querySelector('#insert-palette input[name="world"], #palette input[name="world"]');
    return field ? field.value : '';
  }

  // Where the caret is inside the textarea, from a hidden copy of its text.
  function caret(area, position) {
    var style = getComputedStyle(area);
    var copy = document.createElement('div');
    MIRRORED.forEach(function (p) { copy.style[p] = style[p]; });
    copy.style.position = 'absolute';
    copy.style.visibility = 'hidden';
    copy.style.whiteSpace = 'pre-wrap';
    copy.style.wordWrap = 'break-word';
    copy.style.top = '0';
    copy.style.left = '-9999px';
    copy.textContent = area.value.slice(0, position);
    var mark = document.createElement('span');
    mark.textContent = area.value.slice(position) || '.';
    copy.appendChild(mark);
    document.body.appendChild(copy);
    var line = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.4;
    var at = { top: mark.offsetTop - area.scrollTop, left: mark.offsetLeft - area.scrollLeft, height: line };
    document.body.removeChild(copy);
    return at;
  }

  // What is being completed at the cursor: the kind, the words so far and where they start.
  function context(area) {
    var end = area.selectionEnd;
    if (end !== area.selectionStart) return null;
    var before = area.value.slice(0, end);
    var lineStart = before.lastIndexOf('\n') + 1;
    var line = before.slice(lineStart);
    var found = before.match(INLINE);
    if (found) {
      var kind = found[1].toLowerCase(), value = area.value, to = end;
      // Inside a tag already written: its whole name is the query and is replaced, and
      // so is a reference's book; a label after them stays.
      var rest = value.slice(end).match(SLOT)[0];
      var inside = /^[|}]/.test(value.slice(end + rest.length));
      if (inside) {
        to = end + rest.length;
        if (LIBRARY.indexOf(kind) < 0 && value.charAt(to) === '|') {
          to += 1 + value.slice(to + 1).match(SLOT)[0].length;
        }
      }
      return {
        kind: kind, query: inside ? found[2] + rest : found[2],
        from: end - found[2].length, to: to, inside: inside
      };
    }
    // No name yet: the kind itself is being written.
    var kinds = [[CALLOUT_KIND, line, 'callout'], [INLINE_KIND, before, 'inline']];
    for (var i = 0; i < kinds.length; i++) {
      found = kinds[i][1].match(kinds[i][0]);
      if (found) {
        return { choosing: kinds[i][2], query: found[1], from: end - found[1].length, to: end };
      }
    }
    return null;
  }

  function attach(area) {
    var pop = document.createElement('div');
    pop.className = 'ac-pop';
    pop.hidden = true;
    pop.id = 'ac-' + (area.id || Math.random().toString(36).slice(2));
    document.body.appendChild(pop);
    area.setAttribute('aria-autocomplete', 'list');
    area.setAttribute('aria-controls', pop.id);

    var timer = null, controller = null, current = null, selected = 0, quiet = false;

    function items() { return Array.prototype.slice.call(pop.querySelectorAll('.ac-item')); }

    function close() {
      pop.hidden = true;
      current = null;
      area.removeAttribute('aria-activedescendant');
      area.setAttribute('aria-expanded', 'false');
    }

    function select(index) {
      var all = items();
      if (!all.length) return;
      selected = (index + all.length) % all.length;
      all.forEach(function (el, i) { el.setAttribute('aria-selected', i === selected ? 'true' : 'false'); });
      area.setAttribute('aria-activedescendant', all[selected].id);
      all[selected].scrollIntoView({ block: 'nearest' });
    }

    function place() {
      var box = area.getBoundingClientRect();
      var at = caret(area, current.from);
      var left = window.scrollX + box.left + at.left;
      var room = document.documentElement.clientWidth - 16;
      pop.style.top = (window.scrollY + box.top + at.top + at.height + 4) + 'px';
      pop.style.left = Math.max(8, Math.min(left, room - pop.offsetWidth + window.scrollX)) + 'px';
    }

    function show(html) {
      pop.innerHTML = html;
      pop.hidden = false;
      area.setAttribute('aria-expanded', 'true');
      place();
      select(0);
    }

    // ``browse``: asked for (Ctrl+Space, a kind chosen), so open with no name typed too.
    function lookup(browse) {
      browse = browse === true;
      var found = context(area);
      if (!found || (!found.choosing && !found.query.trim() && !browse)) { close(); return; }
      current = found;
      clearTimeout(timer);
      timer = setTimeout(function () {
        if (controller) controller.abort();
        controller = new AbortController();
        var url = found.choosing
          ? '/search/kinds?' + new URLSearchParams({ context: found.choosing, q: found.query })
          : '/search/suggest?' + new URLSearchParams({
            kind: found.kind, q: found.query.trim(), world: world(), scope: area.dataset.scope || '',
            browse: browse ? '1' : ''
          });
        fetch(url, { credentials: 'same-origin', signal: controller.signal })
          .then(function (r) { return r.ok ? r.text() : ''; })
          .then(function (html) {
            if (!html || current !== found) return;
            if (html.indexOf('ac-item') < 0 && !found.query && !browse) { close(); return; }
            show(html);
          })
          .catch(function () { /* aborted, or offline */ });
      }, DELAY);
    }

    // "Create NPC Garaele": made first, where the editor's record is; then named as usual.
    function create(item) {
      var body = new URLSearchParams({
        kind: item.dataset.create, name: item.dataset.complete, scope: area.dataset.scope || ''
      });
      var chosen = current;
      fetch('/gm/editor/create', { method: 'POST', body: body, credentials: 'same-origin' })
        .then(function (r) {
          if (!r.ok) return;
          current = chosen;
          delete item.dataset.create;
          complete(item);
        });
    }

    function complete(item) {
      if (!current || !item) return;
      if (item.dataset.create) { create(item); return; }
      var text = item.dataset.complete;
      if (current.choosing) {
        // The kind is chosen: write it, then go straight on to its names.
        var start = current.from, end = current.to;
        close();
        quiet = true;
        window.ParaventText.replace(area, start, end, text);
        quiet = false;
        lookup(true);
        return;
      }
      if (!current.inside && area.value.charAt(current.to) !== '}') text += '}';
      var from = current.from, to = current.to;
      close();
      quiet = true;
      window.ParaventText.replace(area, from, to, text);
      quiet = false;
    }

    area.addEventListener('input', function () { if (!quiet) lookup(); });
    area.addEventListener('click', function () { if (!pop.hidden) lookup(); });
    area.addEventListener('blur', function () { setTimeout(close, 150); });
    // Ctrl+Space: the list for the tag at the cursor, now. Elsewhere the key is left alone.
    area.addEventListener('keydown', function (e) {
      if (!e.ctrlKey || e.metaKey || e.altKey || (e.code !== 'Space' && e.key !== ' ')) return;
      if (!context(area)) return;
      e.preventDefault();
      lookup(true);
    });
    area.addEventListener('keydown', function (e) {
      if (pop.hidden) return;
      var all = items();
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        if (!all.length) return;
        e.preventDefault();
        select(selected + (e.key === 'ArrowDown' ? 1 : -1));
      } else if ((e.key === 'Enter' || e.key === 'Tab') && all.length && !e.shiftKey) {
        e.preventDefault();
        complete(all[selected]);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        close();
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight' || e.key === 'Home' || e.key === 'End') {
        setTimeout(lookup, 0);
      }
    });
    // A press keeps the focus in the editor, so the caret stays where the words were.
    pop.addEventListener('mousedown', function (e) {
      var item = e.target.closest && e.target.closest('.ac-item');
      if (!item) return;
      e.preventDefault();
      complete(item);
    });
    window.addEventListener('resize', function () { if (!pop.hidden && current) place(); });
  }

  // Images dropped or pasted: a placeholder at the cursor, replaced by the image's
  // Markdown once stored — or taken out again, with the reason, if it is refused.
  function images(area) {
    var url = area.dataset.upload;
    if (!url) return null;

    function files(list) {
      return Array.prototype.filter.call(list || [], function (f) { return /^image\//.test(f.type); });
    }

    function store(file, at) {
      var name = (file.name || 'image').replace(/\.[^.]+$/, '').replace(/[[\]]/g, '') || 'image';
      var marker = '![' + name + ' …]()';
      window.ParaventText.replace(area, at, at, marker);
      var body = new FormData();
      body.append('file', file, file.name || 'image.png');
      return fetch(url, { method: 'POST', body: body, credentials: 'same-origin' })
        .then(function (r) {
          var path = r.headers.get('X-Media-Path');
          return r.ok && path ? { path: path } : r.text().then(function (why) { return { error: why }; });
        })
        .catch(function () { return { error: 'offline' }; })
        .then(function (done) {
          var where = area.value.indexOf(marker);
          if (where < 0) return;
          var text = done.path ? '![' + name + '](' + done.path + ')' : '';
          window.ParaventText.replace(area, where, where + marker.length, text, true);
          if (done.error) window.alert(done.error);
        });
    }

    function take(list, at) {
      var chain = Promise.resolve();
      list.forEach(function (file, i) {
        chain = chain.then(function () { return store(file, i ? area.selectionStart : at); });
      });
    }

    area.addEventListener('paste', function (e) {
      var list = files(e.clipboardData && e.clipboardData.files);
      if (!list.length) return;
      e.preventDefault();
      take(list, area.selectionStart);
    });
    area.addEventListener('dragover', function (e) {
      if (e.dataTransfer && Array.prototype.some.call(e.dataTransfer.items || [], function (i) {
        return i.kind === 'file';
      })) {
        e.preventDefault();
        area.classList.add('is-dropping');
      }
    });
    area.addEventListener('dragleave', function () { area.classList.remove('is-dropping'); });
    area.addEventListener('drop', function (e) {
      area.classList.remove('is-dropping');
      var list = files(e.dataTransfer && e.dataTransfer.files);
      if (!list.length) return;
      e.preventDefault();
      area.focus();
      take(list, area.selectionStart);
    });
    return function (list) { take(files(list), area.selectionStart); };
  }

  function labels() {
    var dialogEl = document.getElementById('insert-palette');
    try { return JSON.parse((dialogEl && dialogEl.dataset.tools) || '{}'); } catch (e) { return {}; }
  }

  function glyph(name) {
    return '<svg class="ic ic-sm" aria-hidden="true" focusable="false"><use href="#i-' + name + '"></use></svg>';
  }

  var WIDE = 'paravent.editor.wide';

  function toolbar(area, upload) {
    var said = labels();
    var bar = document.createElement('div');
    bar.className = 'editor-tools';
    bar.setAttribute('role', 'toolbar');
    bar.setAttribute('aria-label', said.toolbar || '');

    function put(text, from, to) { window.ParaventText.replace(area, from, to, text); }

    // Where the lines the selection touches start and end.
    function lines() {
      var value = area.value;
      var start = value.lastIndexOf('\n', area.selectionStart - 1) + 1;
      var end = value.indexOf('\n', area.selectionEnd);
      return [start, end < 0 ? value.length : end];
    }

    // Bold or italic around the selection — or off again, when the selection already
    // sits between the marks (italic's single * not being half of a bold **).
    function wrap(mark) {
      var s = area.selectionStart, e = area.selectionEnd, v = area.value, n = mark.length;
      var marked = s >= n && v.slice(s - n, s) === mark && v.slice(e, e + n) === mark;
      if (marked && n === 1 && v.charAt(s - 2) === '*' && v.charAt(e + 1) === '*') marked = false;
      if (marked) {
        put(v.slice(s, e), s - n, e + n);
        area.setSelectionRange(s - n, e - n);
        return;
      }
      var inner = v.slice(s, e) || '…';
      put(mark + inner + mark, s, e);
      area.setSelectionRange(s + n, s + n + inner.length);
    }

    // A bulleted ("- ") or numbered ("1. ") list of the lines selected; off again when
    // every line already is one. Empty lines stay empty.
    function list(numbered) {
      var span = lines();
      var rows = area.value.slice(span[0], span[1]).split('\n');
      var mark = numbered ? /^\s*\d+[.)] / : /^\s*[-*+] /;
      var filled = rows.filter(function (r) { return r.trim(); });
      var off = filled.length && filled.every(function (r) { return mark.test(r); });
      var n = 0;
      var next = rows.map(function (r) {
        if (!r.trim()) return r;
        if (off) return r.replace(mark, '');
        var bare = r.replace(/^\s*(?:[-*+]|\d+[.)]) /, '');
        n += 1;
        return (numbered ? n + '. ' : '- ') + bare;
      });
      put(next.join('\n'), span[0], span[1]);
    }

    // [text](https://) with the address selected, ready to be typed over.
    function link() {
      var s = area.selectionStart, e = area.selectionEnd;
      var text = area.value.slice(s, e) || '…';
      var address = /^https?:\/\//.test(text) ? text : 'https://';
      var written = '[' + text + '](' + address + ')';
      put(written, s, e);
      var at = s + text.length + 3;
      area.setSelectionRange(at, at + address.length);
    }

    function heading() {
      var span = lines();
      var line = area.value.slice(span[0], span[1]);
      var level = (line.match(/^(#{1,6}) /) || ['', ''])[1].length;
      var text = line.replace(/^#{1,6} /, '');
      var next = level === 0 ? '# ' : level < 3 ? '#'.repeat(level + 1) + ' ' : '';
      put(next + text, span[0], span[1]);
    }

    function callout(kind) {
      var span = lines();
      var text = area.value.slice(span[0], span[1]);
      var quoted = text.split('\n').map(function (l) { return '> ' + l; }).join('\n');
      put('> [!' + kind + ']\n' + quoted, span[0], span[1]);
    }

    // Text on lines of its own: after the current line, with a blank line between.
    function block(text) {
      var span = lines();
      var before = area.value.slice(span[0], span[1]).trim() ? '\n\n' : '';
      put(before + text, span[1], span[1]);
    }

    var mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
    function add(key, inner, act, shortcut) {
      var button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn btn-sm btn-ghost editor-tool';
      button.title = (said[key] || key) + (shortcut ? ' (' + (mac ? '⌘' : 'Ctrl+') + shortcut + ')' : '');
      button.setAttribute('aria-label', said[key] || key);
      if (shortcut) button.setAttribute('aria-keyshortcuts', (mac ? 'Meta+' : 'Control+') + shortcut);
      button.innerHTML = inner;
      // The editor keeps its selection while a button is pressed.
      button.addEventListener('mousedown', function (e) { e.preventDefault(); });
      button.addEventListener('click', act);
      bar.appendChild(button);
      return button;
    }

    add('heading', '<b>H</b>', heading);
    add('bold', '<b>B</b>', function () { wrap('**'); }, 'B');
    add('italic', '<i>I</i>', function () { wrap('*'); }, 'I');
    add('list', '<span aria-hidden="true">•</span>', function () { list(false); });
    add('numbered', '<span aria-hidden="true">1.</span>', function () { list(true); });
    add('link', '<span aria-hidden="true">[ ]</span>', link);
    add('read', glyph('scroll'), function () { callout('read'); });
    add('secret', glyph('eye-off'), function () { callout('secret'); });
    var fold = add('fold', glyph('book-open'), function () { block('{@'); });
    fold.classList.add('editor-fold');
    area.addEventListener('keydown', function (e) {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;
      var key = e.key.toLowerCase();
      if (key !== 'b' && key !== 'i') return;
      e.preventDefault();
      wrap(key === 'b' ? '**' : '*');
    });
    add('table', '<span aria-hidden="true">▦</span>', function () {
      block('| | |\n|---|---|\n| | |\n');
    });
    if (upload) {
      var chooser = document.createElement('input');
      chooser.type = 'file';
      chooser.accept = 'image/*';
      chooser.multiple = true;
      chooser.hidden = true;
      chooser.addEventListener('change', function () { upload(chooser.files); chooser.value = ''; });
      bar.appendChild(chooser);
      add('image', glyph('upload'), function () { chooser.click(); });
    }
    var panes = area.dataset.preview && area.closest('.editor-panes');
    if (panes) {
      var toggle = add('wide', glyph('arrow-right'), function () { wide(!panes.classList.contains('is-wide')); });
      toggle.classList.add('editor-wide');
      var wide = function (on) {
        panes.classList.toggle('is-wide', on);
        toggle.title = said[on ? 'narrow' : 'wide'] || '';
        toggle.setAttribute('aria-label', toggle.title);
        toggle.setAttribute('aria-pressed', on ? 'true' : 'false');
        try { localStorage.setItem(WIDE, on ? '1' : ''); } catch (e) { /* private window */ }
      };
      var kept = '';
      try { kept = localStorage.getItem(WIDE) || ''; } catch (e) { /* private window */ }
      if (kept) wide(true);
    }
    // The insert pop-up's button (palette.js) belongs in the bar, whichever runs first.
    var before = area.previousElementSibling;
    if (before && before.classList.contains('insert-ref')) bar.insertBefore(before, fold.nextSibling);
    area.parentNode.insertBefore(bar, area);
  }

  function init() {
    document.querySelectorAll('textarea.prose-editor').forEach(function (area) {
      attach(area);
      toolbar(area, images(area));
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
