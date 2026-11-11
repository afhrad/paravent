// Dice sounds, made in the browser with the Web Audio API — no sound files, no network.
// A roll answered by the server carries an "HX-Trigger: paravent-roll" header with its
// outcome; htmx turns that into an event here. Every roll rattles; a natural 20 adds a
// rising chime, a natural 1 a falling "wah-wah". Sounds are a setting of this device
// (the user menu, stored like the theme), on by default.
(function () {
  'use strict';

  var KEY = 'pv-sound';
  var ctx = null;

  function enabled() {
    try { return localStorage.getItem(KEY) !== 'off'; } catch (e) { return true; }
  }

  function audio() {
    if (!ctx) {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      ctx = new Ctx();
    }
    if (ctx.state === 'suspended') ctx.resume();
    return ctx;
  }

  // Browsers (iOS above all) only start audio inside a tap: the first one unlocks it,
  // so a roll whose answer arrives later can still be heard.
  function unlock() {
    var c = audio();
    if (c) {
      var buffer = c.createBuffer(1, 1, c.sampleRate);
      var src = c.createBufferSource();
      src.buffer = buffer; src.connect(c.destination); src.start(0);
    }
    document.removeEventListener('pointerdown', unlock, true);
    document.removeEventListener('keydown', unlock, true);
  }
  document.addEventListener('pointerdown', unlock, true);
  document.addEventListener('keydown', unlock, true);

  // One die striking the table: a short burst of noise through a band-pass filter.
  function clack(c, t, gain) {
    var length = Math.floor(c.sampleRate * 0.045);
    var buffer = c.createBuffer(1, length, c.sampleRate), data = buffer.getChannelData(0);
    for (var i = 0; i < length; i++) data[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / length, 3);
    var src = c.createBufferSource(); src.buffer = buffer;
    var filter = c.createBiquadFilter();
    filter.type = 'bandpass'; filter.frequency.value = 1600 + Math.random() * 2400; filter.Q.value = 2.5;
    var level = c.createGain(); level.gain.value = gain;
    src.connect(filter); filter.connect(level); level.connect(c.destination);
    src.start(t);
  }

  function rattle(c, t) {
    var hits = 6 + Math.floor(Math.random() * 4);
    for (var i = 0; i < hits; i++) {
      clack(c, t, 0.55 * (1 - i / hits) + 0.12);
      t += 0.03 + Math.random() * 0.06 * (1 + i / hits);
    }
    return t;
  }

  function tone(c, t, freq, duration, type, gain, endFreq, cutoff) {
    var osc = c.createOscillator(), level = c.createGain(), out = level;
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t);
    if (endFreq) osc.frequency.exponentialRampToValueAtTime(endFreq, t + duration);
    level.gain.setValueAtTime(0.0001, t);
    level.gain.exponentialRampToValueAtTime(gain, t + 0.02);
    level.gain.exponentialRampToValueAtTime(0.0001, t + duration);
    osc.connect(level);
    if (cutoff) {
      var filter = c.createBiquadFilter();
      filter.type = 'lowpass'; filter.frequency.value = cutoff;
      level.connect(filter); out = filter;
    }
    out.connect(c.destination);
    osc.start(t); osc.stop(t + duration + 0.05);
  }

  function chime(c, t) {  // C major, rising, with a bright top note
    [523.25, 659.25, 783.99, 1046.5].forEach(function (f, i) {
      tone(c, t + i * 0.09, f, 0.55, 'triangle', 0.22);
    });
    tone(c, t + 0.36, 1567.98, 0.8, 'sine', 0.1);
  }

  function wahWah(c, t) {  // the sad trombone
    [392, 369.99, 349.23].forEach(function (f, i) {
      tone(c, t + i * 0.3, f, 0.28, 'sawtooth', 0.14, f * 0.97, 1400);
    });
    tone(c, t + 0.9, 329.63, 1.0, 'sawtooth', 0.14, 246.94, 1400);
  }

  function play(outcome) {
    if (!enabled()) return;
    var c = audio();
    if (!c) return;
    var end = rattle(c, c.currentTime + 0.02);
    if (outcome === 'crit') chime(c, end + 0.05);
    else if (outcome === 'fumble') wahWah(c, end + 0.1);
  }

  window.ParaventSound = { roll: play };
  document.addEventListener('paravent-roll', function (e) {
    play(e.detail && e.detail.outcome);
  });

  // The on/off switch in the user menu.
  function mark() {
    var on = enabled();
    document.querySelectorAll('.soundtoggle button[data-sound]').forEach(function (b) {
      b.setAttribute('aria-pressed', String((b.dataset.sound === 'on') === on));
    });
  }
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('.soundtoggle button[data-sound]');
    if (!b) return;
    try { localStorage.setItem(KEY, b.dataset.sound); } catch (err) {}
    mark();
    if (b.dataset.sound === 'on') play('');
  });
  mark();
})();
