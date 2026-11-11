// Roll chips outside a character sheet — on the reference page there is no character
// whose log the roll belongs to. The server rolls it and puts it in the log, private to
// the roller and the gamemaster (documentation/PROTOCOL.md §8); without a connection it
// is rolled here and only shown. Expressions are the chips' own: "1d20+4", "2d6 + 1d4 - 1".
(function () {
  'use strict';

  var TERM = /([+-]?)\s*(?:(\d*)d(\d+)|(\d+))/gi;

  function roll(expression) {
    var total = 0, parts = [], match, natural = null, d20s = 0;
    TERM.lastIndex = 0;
    while ((match = TERM.exec(expression)) !== null) {
      if (match[0] === '') { TERM.lastIndex++; continue; }
      var sign = match[1] === '-' ? -1 : 1;
      if (match[3]) {
        var count = parseInt(match[2] || '1', 10), faces = parseInt(match[3], 10), rolls = [];
        for (var i = 0; i < Math.min(count, 100); i++) {
          var r = 1 + Math.floor(Math.random() * faces);
          rolls.push(r); total += sign * r;
          if (faces === 20) { d20s++; natural = r; }
        }
        parts.push((sign < 0 ? '−' : '') + count + 'd' + faces + '[' + rolls.join(',') + ']');
      } else {
        total += sign * parseInt(match[4], 10);
        parts.push((sign < 0 ? '− ' : '+ ') + match[4]);
      }
    }
    // One d20 in the expression: its natural 20 or 1 is a critical or a fumble.
    var outcome = d20s === 1 ? (natural === 20 ? 'crit' : natural === 1 ? 'fumble' : '') : '';
    return { total: total, detail: parts.join(' '), outcome: outcome };
  }

  function show(label, result) {
    var out = document.querySelector('.ref-roll-result');
    if (out) {
      out.hidden = false;
      out.textContent = label + ' → ' + result.detail + ' = ' + result.total;
      out.classList.toggle('crit', result.outcome === 'crit');
      out.classList.toggle('fumble', result.outcome === 'fumble');
    }
    if (window.ParaventSound) window.ParaventSound.roll(result.outcome);
  }

  // The server's roll, logged in the page's world; null when it cannot be reached.
  function logged(expression, label) {
    if (!window.fetch || !window.FormData) return Promise.resolve(null);
    var data = new FormData();
    var heading = document.querySelector('h1');
    data.append('expr', expression);
    data.append('label', heading ? heading.textContent.trim() + ' · ' + label : label);
    data.append('world', new URLSearchParams(location.search).get('world') || '');
    return fetch('/log/roll', { method: 'POST', body: data, credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
  }

  document.addEventListener('click', function (e) {
    var chip = e.target.closest && e.target.closest('[data-roll]');
    if (chip) {
      var label = chip.textContent.trim();
      logged(chip.dataset.roll, label).then(function (result) {
        show(label, result || roll(chip.dataset.roll));
      });
      return;
    }
    var back = e.target.closest && e.target.closest('.js-back');
    if (back) {
      if (history.length > 1) history.back(); else location.href = '/';
    }
  });
})();
