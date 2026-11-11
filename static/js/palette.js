// The command palette (documentation/DESIGN.md §4).
//
//   ⌘K / Ctrl+K   open or close it, from anywhere — mid-combat included
//   /             open it, when not typing in a field
//   typing        results follow, ~120 ms after the last key
//   ↑ ↓           move the selection;  ↵ opens it (⌘/Ctrl+↵ in a new tab)
//   Esc           close (the native <dialog> does that)
//
// The same enhancement runs on the full /search page, which is also what every form
// submits to without JavaScript. Results are plain links to reference pages.
//
// The Markdown editors' insert pop-up (#insert-palette) is the same search: ⌘K in an
// editor, or its "Insert reference" button, opens it; a result inserts its tag at the
// cursor. Text selected in the editor becomes the query, and stays as the tag's label.
(function () {
  'use strict';

  var DELAY = 120;

  function enhance(form) {
    var input = form.querySelector('.palette-input');
    var dialogEl = form.closest('dialog');
    var timer = null, controller = null, selected = 0;

    function list() { return form.querySelector('.results'); }
    function options() { return Array.prototype.slice.call(form.querySelectorAll('.results .res')); }

    function select(index, scroll) {
      var items = options();
      if (!items.length) { selected = -1; input.removeAttribute('aria-activedescendant'); return; }
      selected = Math.max(0, Math.min(index, items.length - 1));
      items.forEach(function (el, i) { el.setAttribute('aria-selected', i === selected ? 'true' : 'false'); });
      input.setAttribute('aria-activedescendant', items[selected].id);
      if (scroll) items[selected].scrollIntoView({ block: 'nearest' });
    }

    function params() {
      var data = new FormData(form);
      var out = new URLSearchParams();
      data.forEach(function (value, key) { if (value) out.append(key, value); });
      return out;
    }

    function refresh() {
      clearTimeout(timer);
      if (controller) controller.abort();
      controller = new AbortController();
      var query = params();
      // A page-level form keeps its own URL current, so reload and back return to it.
      if (!dialogEl) {
        var shown = new URLSearchParams(query);
        shown.delete('pick');
        var base = form.getAttribute('action') || '/search';
        history.replaceState(null, '', base + (shown.toString() ? '?' + shown : '') + location.hash);
      }
      query.set('partial', '1');
      fetch('/search?' + query, { credentials: 'same-origin', signal: controller.signal })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
        .then(function (html) {
          var current = list();
          var tpl = document.createElement('template');
          tpl.innerHTML = html.trim();
          if (current && tpl.content.firstElementChild) current.replaceWith(tpl.content.firstElementChild);
          select(0, false);
        })
        .catch(function () { /* aborted, or offline: the form still submits */ });
    }

    function later() { clearTimeout(timer); timer = setTimeout(refresh, DELAY); }

    function setAll(on) {
      var field = form.querySelector('input[name="all"]');
      if (on && !field) {
        field = document.createElement('input');
        field.type = 'hidden'; field.name = 'all'; field.value = '1';
        form.appendChild(field);
      } else if (!on && field) {
        field.remove();
      }
    }

    input.addEventListener('input', later);
    form.addEventListener('change', function (e) { if (e.target.name === 'group') refresh(); });
    // An "add" that swaps parts of the page in place (data-pick-swap="#plan #rating"):
    // post in the background, follow the redirect to the page, and take those parts
    // from it, so the page neither reloads nor scrolls and the search stays as typed.
    function pickInPlace(button) {
      var parts = (form.dataset.pickSwap || '').split(/\s+/).filter(Boolean);
      var data = new FormData(form);
      data.append(button.name, button.value);
      button.disabled = true;
      var done = function () {
        button.disabled = false;
        input.focus({ preventScroll: true });
      };
      // One part through htmx itself, so the page's own swap handlers run (the combat
      // tracker keeps its menus and scroll position); several through a plain fetch.
      if (parts.length === 1 && window.htmx) {
        var values = {};
        data.forEach(function (value, key) { values[key] = value; });
        window.htmx.ajax('POST', button.getAttribute('formaction'), {
          source: form, values: values, target: parts[0], select: parts[0], swap: 'outerHTML'
        }).then(done, function () { form.submit(); });
        return;
      }
      fetch(button.getAttribute('formaction'), {
        method: 'POST', body: data, credentials: 'same-origin'
      })
        .then(function (r) { return r.text(); })
        .then(function (html) {
          var fresh = new DOMParser().parseFromString(html, 'text/html');
          parts.forEach(function (sel) {
            var now = document.querySelector(sel), got = fresh.querySelector(sel);
            if (now && got) {
              now.replaceWith(got);
              if (window.htmx) window.htmx.process(got);
            }
          });
        })
        .catch(function () { form.submit(); })
        .finally(done);
    }

    form.addEventListener('submit', function (e) {
      if (form.dataset.pickSwap && e.submitter && e.submitter.classList.contains('res-pick')) {
        e.preventDefault();
        pickInPlace(e.submitter);
        return;
      }
      // Enter in the field: open the selected result, or press it when it is an "add" button.
      if (e.submitter && e.submitter.classList.contains('res')) return;
      var item = options()[selected];
      if (!item) return;
      e.preventDefault();
      // A submit started while this one is still firing is dropped; press it right after.
      if (item.tagName === 'BUTTON') setTimeout(function () { item.click(); }, 0);
      else location.href = item.href;
    });

    input.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        select(selected + (e.key === 'ArrowDown' ? 1 : -1), true);
      } else if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
        var item = options()[selected];
        var href = item && (item.href || item.dataset.href);
        if (href) { e.preventDefault(); window.open(href, '_blank', 'noopener'); }
      }
    });

    // "Show all" and "all imported books" stay inside the palette instead of leaving it.
    form.addEventListener('click', function (e) {
      var link = e.target.closest && e.target.closest('a[data-group], a[data-all]');
      if (!link || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      if (link.dataset.group) {
        var radio = form.querySelector('input[name="group"][value="' + link.dataset.group + '"]');
        if (radio) radio.checked = true;
      }
      if (link.dataset.all) setAll(link.dataset.all === '1');
      refresh();
      input.focus();
    });

    form.addEventListener('pointermove', function (e) {
      var item = e.target.closest && e.target.closest('.res');
      if (item) select(options().indexOf(item), false);
    });

    select(0, false);
    return { input: input, refresh: refresh };
  }

  // A tag with ``label`` in its label slot: {@creature Goblin} → {@creature Goblin||goblins}.
  function labelled(tag, label, slot) {
    if (!label) return tag;
    var body = tag.slice(0, -1);
    var parts = body.split('|');
    while (parts.length < slot) parts.push('');
    parts[slot] = label;
    return parts.join('|') + '}';
  }

  function insertLayer(dialogEl, layer, palette) {
    var target = null, start = 0, end = 0;

    function open(field) {
      target = field;
      start = field.selectionStart; end = field.selectionEnd;
      if (palette && palette.dialog && palette.dialog.open) palette.dialog.close();
      dialogEl.showModal();
      var chosen = field.value.slice(start, end).trim();
      layer.input.value = chosen.length <= 60 ? chosen : '';
      layer.input.focus();
      layer.input.select();
      layer.refresh();
    }

    function put(button) {
      dialogEl.close();
      if (!target) return;
      var words = target.value.slice(start, end).trim();
      var tag = labelled(button.dataset.insert, words, Number(button.dataset.labelSlot || 2));
      window.ParaventText.replace(target, start, end, tag);
    }

    document.querySelectorAll('textarea.prose-editor').forEach(function (area) {
      var trigger = document.createElement('button');
      trigger.type = 'button';
      trigger.className = 'btn btn-sm insert-ref';
      var mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
      var text = dialogEl.dataset.button || 'Insert reference';
      var key = mac ? '⌘K' : 'Ctrl K';
      // In the editor's toolbar only the icon and the key show; the words are its name.
      trigger.setAttribute('aria-label', mac ? text : text.replace('⌘K', key));
      trigger.innerHTML = '<svg class="ic ic-sm" aria-hidden="true" focusable="false"><use href="#i-search"></use></svg>' +
        '<span class="insert-ref-label"></span><span class="kbd insert-ref-key"></span>';
      trigger.querySelector('.insert-ref-label').textContent = text.replace(/\s*\(⌘K\)\s*$/, '');
      trigger.querySelector('.insert-ref-key').textContent = key;
      trigger.title = dialogEl.dataset.buttonTitle || '';
      trigger.addEventListener('click', function () { open(area); });
      // Into the editor's toolbar when complete.js has made it already, else before it.
      var bar = area.previousElementSibling;
      if (bar && bar.classList.contains('editor-tools')) {
        var fold = bar.querySelector('.editor-fold');
        bar.insertBefore(trigger, fold ? fold.nextSibling : null);
      }
      else area.parentNode.insertBefore(trigger, area);
    });
    // ⌘K in an editor inserts rather than navigates; the page's palette stays one away.
    document.addEventListener('keydown', function (e) {
      if (!((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 'k')) return;
      var el = document.activeElement;
      if (!el || !el.matches || !el.matches('textarea.prose-editor')) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      open(el);
    }, true);
    dialogEl.addEventListener('click', function (e) {
      if (e.target === dialogEl) { dialogEl.close(); return; }
      var button = e.target.closest && e.target.closest('.res-insert');
      if (button) { e.preventDefault(); put(button); }
    });
    dialogEl.addEventListener('close', function () {
      if (target && document.activeElement !== target) target.focus();
    });
  }

  function init() {
    // The bar shows ⌘K; everywhere but Apple keyboards the shortcut is Ctrl+K.
    var mac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
    if (!mac) document.querySelectorAll('.search-kbd').forEach(function (k) { k.textContent = 'Ctrl K'; });
    var forms = document.querySelectorAll('.palette-form');
    // Coming back to "#picker" after adding a monster: browsers skip autofocus when the
    // URL has a fragment, so put the cursor back in that search ourselves, ready for more.
    // It has to wait for load: scrolling to the fragment runs the focusing steps for the
    // section, which is not focusable, so focus falls back to the page and the field blurs.
    var landed = location.hash && document.getElementById(location.hash.slice(1));
    var again = landed && landed.querySelector('.palette-input');
    if (again) {
      var refocus = function () {
        setTimeout(function () {
          again.focus({ preventScroll: true });
          again.setSelectionRange(again.value.length, again.value.length);
        }, 0);
      };
      if (document.readyState === 'complete') refocus();
      else window.addEventListener('load', refocus, { once: true });
    }
    var dialogEl = document.getElementById('palette');
    var insertEl = document.getElementById('insert-palette');
    var palette = null, inserting = null;
    forms.forEach(function (form) {
      var enhanced = enhance(form);
      if (form.closest('dialog') === dialogEl) palette = enhanced;
      if (insertEl && form.closest('dialog') === insertEl) inserting = enhanced;
    });
    if (inserting && typeof insertEl.showModal === 'function') {
      insertLayer(insertEl, inserting, { dialog: dialogEl });
    }
    if (!dialogEl) {
      // The search page is the palette already: ⌘K and the bar's field go to its input.
      var own = document.querySelector('.palette-form .palette-input');
      if (!own) return;
      document.addEventListener('keydown', function (e) {
        if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 'k') {
          e.preventDefault(); own.focus(); own.select();
        }
      });
      document.addEventListener('click', function (e) {
        if (e.target.closest && e.target.closest('[data-palette-open]')) { e.preventDefault(); own.focus(); own.select(); }
      });
      return;
    }
    if (!palette || typeof dialogEl.showModal !== 'function') return;

    function open() {
      if (dialogEl.open) return;
      // A preview left open would sit above the dialog's backdrop.
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      dialogEl.showModal();
      palette.input.focus();
      palette.input.select();
    }
    function toggle() { if (dialogEl.open) dialogEl.close(); else open(); }

    document.addEventListener('click', function (e) {
      var trigger = e.target.closest && e.target.closest('[data-palette-open]');
      if (!trigger || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      open();
    });
    // A click on the backdrop lands on the dialog element itself.
    dialogEl.addEventListener('click', function (e) { if (e.target === dialogEl) dialogEl.close(); });

    document.addEventListener('keydown', function (e) {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        toggle();
        return;
      }
      if (e.key === '/' && !dialogEl.open && !e.metaKey && !e.ctrlKey && !e.altKey) {
        var el = document.activeElement;
        var typing = el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
        if (!typing) { e.preventDefault(); open(); }
      }
    });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
