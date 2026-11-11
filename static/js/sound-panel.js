// The sound desk's slide-over (documentation/SOUND.md). The top-bar button is a link to
// the speaker page, which works without this file; here it opens the panel. The panel
// plays nothing: it says what should sound, and a speaker page does the playing. The
// event stream is open only while the panel is, so idle pages hold no connection.
(function () {
  'use strict';

  var panel = document.getElementById('sound-panel');
  var opener = document.querySelector('.shell-sound');
  if (!panel || !window.fetch) return;

  var content = panel.querySelector('.logpanel-content');
  var source = null;
  var sliding = false;  // a slider in the hand is not moved by the state coming back

  function post(path, fields) {
    var body = new URLSearchParams(fields || {});
    return fetch('/gm/sound/' + path, { method: 'POST', credentials: 'same-origin', body: body });
  }

  function load() {
    return fetch('/gm/sound/panel', { credentials: 'same-origin' })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (html) { content.innerHTML = html; });
  }

  function layerRow(item, label) {
    var row = document.createElement('label');
    row.className = 'sd-layer';
    var name = document.createElement('span');
    name.textContent = item.name;
    var slider = document.createElement('input');
    slider.type = 'range';
    slider.min = '0';
    slider.max = '1';
    slider.step = '0.01';
    slider.value = item.volume;
    slider.dataset.layer = item.id;
    slider.setAttribute('aria-label', label + ': ' + item.name);
    row.appendChild(name);
    row.appendChild(slider);
    return row;
  }

  function show(state) {
    var on = {};
    state.loops.forEach(function (x) { on[x.id] = true; });
    if (state.music) on[state.music.id] = true;
    content.querySelectorAll('[data-sound-id]').forEach(function (pad) {
      var live = !!on[pad.dataset.soundId];
      pad.classList.toggle('is-on', live);
      pad.setAttribute('aria-pressed', live ? 'true' : 'false');
    });
    var status = content.querySelector('[data-sound-status]');
    if (status) status.classList.toggle('is-ready', state.speakers > 0);
    var master = content.querySelector('[data-sound-master]');
    if (master && !sliding) master.value = state.master;
    var mix = content.querySelector('[data-sound-mix]');
    if (mix && !sliding) {
      mix.textContent = '';
      state.loops.concat(state.music ? [state.music] : []).forEach(function (item) {
        mix.appendChild(layerRow(item, mix.dataset.label));
      });
    }
  }

  function connect() {
    if (source) return;
    source = new EventSource('/gm/sound/events');
    source.addEventListener('revision', function (e) {
      if (e.data) show(JSON.parse(e.data));
    });
  }

  function disconnect() {
    if (source) source.close();
    source = null;
  }

  function open() {
    if (panel.open) { close(); return; }
    panel.show();
    load().then(connect).catch(function () { /* the link still leads to the page */ });
  }

  function close() {
    panel.close();
    disconnect();
  }

  if (opener) {
    opener.addEventListener('click', function (e) {
      e.preventDefault();
      open();
    });
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && panel.open) close();
  });

  // Anywhere on the page: a {@sound Tavern} in a chapter.
  document.addEventListener('click', function (e) {
    var tag = e.target.closest ? e.target.closest('[data-sound-trigger]') : null;
    if (tag) post('trigger', { name: tag.dataset.soundTrigger });
  });

  panel.addEventListener('click', function (e) {
    var hit = e.target.closest('button');
    if (!hit) return;
    if (hit.hasAttribute('data-sound-close')) close();
    else if (hit.dataset.soundId) post('press/' + hit.dataset.soundId);
    else if (hit.dataset.sceneId) post('scene/' + hit.dataset.sceneId);
    else if (hit.dataset.sceneDelete) post('scenes/' + hit.dataset.sceneDelete + '/delete').then(load);
    else if (hit.hasAttribute('data-sound-stop')) post('stop');
  });

  panel.addEventListener('input', function (e) {
    var t = e.target;
    if (t.hasAttribute('data-sound-search')) {
      var wanted = t.value.trim().toLowerCase();
      content.querySelectorAll('[data-name]').forEach(function (x) {
        x.hidden = wanted !== '' && x.dataset.name.indexOf(wanted) < 0;
      });
      return;
    }
    if (t.type !== 'range') return;
    sliding = true;
    if (t.hasAttribute('data-sound-master')) post('master', { value: t.value });
    else if (t.dataset.layer) post('volume', { id: t.dataset.layer, value: t.value });
  });

  panel.addEventListener('change', function () { sliding = false; });

  panel.addEventListener('submit', function (e) {
    e.preventDefault();
    var field = e.target.querySelector('input[name=name]');
    post('scenes', { name: field.value }).then(function (r) {
      if (r.ok) { field.value = ''; return load(); }
      return r.text().then(function (reason) { field.setCustomValidity(reason); field.reportValidity(); field.setCustomValidity(''); });
    });
  });
})();
