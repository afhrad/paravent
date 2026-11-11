// The character creation wizard (documentation/CHARGEN-WIZARD.md §3). The wizard works
// without this file — every step is a form with Save and Continue. This adds:
//
//  * a filter over long option lists, kept across the live re-render
//  * focus kept on the control that was changed when the form comes back from htmx
//  * the step list folded to one progress line on a phone
//  * a press on the footer that survives the save of text just typed
(function () {
  'use strict';

  // A list narrows by its text filter and, for spells, by the level chips; what is
  // chosen always stays visible.
  function applyFilter(input) {
    var id = input.dataset.filter;
    var list = document.querySelector('[data-options="' + CSS.escape(id) + '"]');
    if (!list) return;
    var q = input.value.trim().toLowerCase();
    var level = list.dataset.level || '';
    Array.prototype.forEach.call(list.children, function (el) {
      var name = el.dataset.name || '';
      var hit = (q === '' || name.indexOf(q) !== -1) && (level === '' || el.dataset.level === level);
      el.hidden = !hit && !el.querySelector('input:checked');
    });
  }
  document.addEventListener('input', function (e) {
    if (e.target.matches && e.target.matches('[data-filter]')) applyFilter(e.target);
  });
  document.addEventListener('click', function (e) {
    var chip = e.target.closest && e.target.closest('[data-level-chip]');
    if (!chip) return;
    var question = chip.closest('.cg-question');
    var list = question && question.querySelector('[data-options]');
    var input = question && question.querySelector('[data-filter]');
    if (!list || !input) return;
    list.dataset.level = chip.dataset.levelChip;
    question.querySelectorAll('[data-level-chip]').forEach(function (c) {
      c.setAttribute('aria-pressed', String(c === chip));
    });
    applyFilter(input);
  });

  // "Choose 3": once three are ticked the others wait, rather than a fourth being
  // refused after the fact (CHARGEN-WIZARD.md §1 rule 2).
  function limit(root) {
    (root || document).querySelectorAll('[data-max]').forEach(function (list) {
      var max = parseInt(list.dataset.max, 10);
      var boxes = list.querySelectorAll('input[type=checkbox]');
      var ticked = list.querySelectorAll('input[type=checkbox]:checked').length;
      boxes.forEach(function (box) {
        if (box.checked) return;
        if (box.dataset.blocked === undefined) box.dataset.blocked = box.disabled ? '1' : '';
        box.disabled = box.dataset.blocked === '1' || ticked >= max;
      });
    });
  }
  document.addEventListener('change', function (e) {
    if (e.target.matches && e.target.matches('[data-max] input[type=checkbox]')) limit();
  });

  var focused = null, filters = {};
  document.addEventListener('htmx:beforeSwap', function (e) {
    if (!e.detail.target || e.detail.target.id !== 'wizard') return;
    focused = document.activeElement && document.activeElement.id;
    filters = {};
    document.querySelectorAll('#wizard [data-filter]').forEach(function (f) {
      var list = document.querySelector('[data-options="' + CSS.escape(f.dataset.filter) + '"]');
      if (f.value || (list && list.dataset.level)) {
        filters[f.dataset.filter] = { q: f.value, level: list ? list.dataset.level || '' : '' };
      }
    });
  });
  document.addEventListener('htmx:afterSettle', function (e) {
    if (!e.detail.target || e.detail.target.id !== 'wizard') return;
    Object.keys(filters).forEach(function (id) {
      var f = document.querySelector('#wizard [data-filter="' + CSS.escape(id) + '"]');
      var list = document.querySelector('[data-options="' + CSS.escape(id) + '"]');
      if (f) {
        f.value = filters[id].q;
        if (list) {
          list.dataset.level = filters[id].level;
          var q = f.closest('.cg-question');
          if (q) q.querySelectorAll('[data-level-chip]').forEach(function (c) {
            c.setAttribute('aria-pressed', String(c.dataset.levelChip === filters[id].level));
          });
        }
        applyFilter(f);
      }
    });
    if (focused) {
      var el = document.getElementById(focused);
      if (el) el.focus({ preventScroll: true });
    }
    fold();
    limit();
  });

  function fold() {
    if (!window.matchMedia('(max-width: 767px)').matches) return;
    document.querySelectorAll('.cg-rail-toggle[open]').forEach(function (d) { d.open = false; });
  }
  function ready() { fold(); limit(); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ready);
  else ready();

  // Typed text saves when its field loses focus, and that save swaps the whole form. A
  // press on the footer would blur the field, so the swap replaces the button under the
  // mouse and the click is lost; a press on a roll would race it, and the save's answer
  // could put back the form without the roll. So a press with unsaved text keeps the
  // focus, and Back saves before it leaves; the other buttons send the form, text and all.
  function unsaved() {
    var field = document.activeElement;
    if (!field || !field.matches('#wizard input[type=text], #wizard textarea')) return false;
    return field.value !== field.defaultValue;
  }

  document.addEventListener('mousedown', function (e) {
    if (e.target.closest('#wizard .cg-foot a, #wizard .cg-foot button, #wizard button[hx-post]') && unsaved()) {
      e.preventDefault();
    }
  });

  document.addEventListener('click', function (e) {
    var link = e.target.closest('#wizard .cg-foot a');
    if (!link || !unsaved()) return;
    e.preventDefault();
    var wizard = document.getElementById('wizard');
    fetch(wizard.action, { method: 'POST', body: new FormData(wizard) }).finally(function () {
      location.href = link.href;
    });
  });
})();
