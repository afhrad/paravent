// The translation editor (documentation/TRANSLATION.md §7). Every control works
// without this file; it only makes the tags easy to place and the form light to send.
(function () {
  'use strict';

  // A tag chip inserts its tag at the cursor of its passage's field, so a tag comes
  // from the English text and is never typed or guessed from the translation (§3).
  document.addEventListener('click', function (e) {
    var chip = e.target.closest && e.target.closest('.tr-chip[data-insert]');
    if (!chip) return;
    var field = document.getElementById(chip.dataset.for);
    if (!field) return;
    var tag = chip.dataset.insert;
    var start = field.selectionStart, end = field.selectionEnd;
    if (typeof start !== 'number') { start = end = field.value.length; }
    field.value = field.value.slice(0, start) + tag + field.value.slice(end);
    field.focus();
    field.selectionStart = field.selectionEnd = start + tag.length;
    field.dispatchEvent(new Event('input', { bubbles: true }));
  });

  // Rows show whether they were edited; only edited passages are sent, so a long
  // chapter posts a few fields rather than hundreds (the server ignores the rest too).
  document.addEventListener('input', function (e) {
    var field = e.target;
    if (!field.matches || !field.matches('.tr-de textarea')) return;
    var row = field.closest('.tr-row');
    if (row) row.classList.toggle('is-edited', field.value !== (field.dataset.original || ''));
  });

  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form.matches || !form.matches('.tr-form')) return;
    form.querySelectorAll('.tr-de textarea').forEach(function (field) {
      if (field.value === (field.dataset.original || '')) field.disabled = true;
    });
  });
})();
