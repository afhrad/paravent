// Dice sounds and the players' cues, made in the browser with the Web Audio API — no sound
// files, no network.
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

  // -- the players' cues (app/ui/cues.py) ----------------------------------------------
  // An element carrying data-cue="<sound>|<key>" is heard the first time its key appears
  // after the page loaded: never for what was there at load, never twice, and of several
  // arriving together only the most urgent. The sounds share the dice's switch.

  var room = null;

  // A small room, so bells and the gong ring a little: a generated impulse.
  function reverb(c) {
    if (room) return room;
    room = c.createConvolver();
    var len = Math.floor(c.sampleRate * 1.6), ir = c.createBuffer(2, len, c.sampleRate);
    for (var ch = 0; ch < 2; ch++) {
      var d = ir.getChannelData(ch);
      for (var i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 3.2);
    }
    room.buffer = ir;
    var wet = c.createGain(); wet.gain.value = 0.22;
    room.connect(wet); wet.connect(c.destination);
    return room;
  }

  function send(c, node, share) {
    node.connect(c.destination);
    if (share) { var s = c.createGain(); s.gain.value = share; node.connect(s); s.connect(reverb(c)); }
  }

  function noise(c, seconds) {
    var len = Math.floor(c.sampleRate * seconds), b = c.createBuffer(1, len, c.sampleRate), d = b.getChannelData(0);
    for (var i = 0; i < len; i++) d[i] = Math.random() * 2 - 1;
    var src = c.createBufferSource(); src.buffer = b; return src;
  }

  function env(gain, t, attack, peak, decay) {
    gain.gain.setValueAtTime(0.0001, t);
    gain.gain.exponentialRampToValueAtTime(peak, t + attack);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + attack + decay);
  }

  // A bell: a fundamental and inharmonic partials that fade faster than it does.
  function bell(c, t, f, peak, decay) {
    [[1, 1], [2.76, 0.42], [5.4, 0.2], [8.93, 0.08]].forEach(function (p, i) {
      var o = c.createOscillator(), g = c.createGain();
      o.type = 'sine'; o.frequency.value = f * p[0];
      env(g, t, 0.004, peak * p[1], decay / (1 + i * 0.9));
      o.connect(g); send(c, g, 0.6);
      o.start(t); o.stop(t + decay + 0.1);
    });
  }

  // Paper unfolding: crackles of band-passed noise, swept upward, over a soft hiss.
  function rustle(c, t, length) {
    var hiss = noise(c, length), hf = c.createBiquadFilter(), hg = c.createGain();
    hf.type = 'bandpass'; hf.Q.value = 0.8;
    hf.frequency.setValueAtTime(2200, t); hf.frequency.exponentialRampToValueAtTime(5200, t + length);
    hg.gain.setValueAtTime(0.0001, t);
    hg.gain.exponentialRampToValueAtTime(0.09, t + length * 0.35);
    hg.gain.exponentialRampToValueAtTime(0.0001, t + length);
    hiss.connect(hf); hf.connect(hg); send(c, hg, 0.1); hiss.start(t);
    for (var i = 0, n = 14; i < n; i++) {
      var at = t + (i / n) * length * 0.9 + Math.random() * 0.012;
      var crack = noise(c, 0.018), f = c.createBiquadFilter(), g = c.createGain();
      f.type = 'bandpass'; f.Q.value = 3; f.frequency.value = 2500 + Math.random() * 3500;
      env(g, at, 0.002, 0.12 + Math.random() * 0.18, 0.016);
      crack.connect(f); f.connect(g); send(c, g, 0.1); crack.start(at);
    }
  }

  // Brass-ish: a sawtooth whose filter opens as the note starts, with a little vibrato.
  function brass(c, t, f, length, peak, bright) {
    var o = c.createOscillator(), lf = c.createBiquadFilter(), g = c.createGain();
    var vib = c.createOscillator(), vg = c.createGain();
    o.type = 'sawtooth'; o.frequency.value = f;
    vib.frequency.value = 5.5; vg.gain.value = f * 0.006; vib.connect(vg); vg.connect(o.frequency);
    lf.type = 'lowpass'; lf.Q.value = 1.2;
    lf.frequency.setValueAtTime(f * 1.2, t);
    lf.frequency.linearRampToValueAtTime(f * (bright || 4), t + 0.06);
    lf.frequency.exponentialRampToValueAtTime(f * 2, t + length);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(peak, t + 0.045);
    g.gain.setValueAtTime(peak * 0.85, t + length * 0.7);
    g.gain.exponentialRampToValueAtTime(0.0001, t + length);
    o.connect(lf); lf.connect(g); send(c, g, 0.35);
    o.start(t); vib.start(t); o.stop(t + length + 0.05); vib.stop(t + length + 0.05);
  }

  var CUES = {
    // A handout: paper unfolding, then two bells rising a fifth.
    handout: function (c, t) {
      rustle(c, t, 0.28);
      bell(c, t + 0.3, 659.25, 0.28, 1.1);
      bell(c, t + 0.46, 987.77, 0.24, 1.4);
    },
    // Your turn: one low drum hit with a short gong tail.
    turn: function (c, t) {
      var o = c.createOscillator(), g = c.createGain();
      o.type = 'sine';
      o.frequency.setValueAtTime(120, t); o.frequency.exponentialRampToValueAtTime(48, t + 0.4);
      env(g, t, 0.004, 0.9, 0.5);
      o.connect(g); send(c, g, 0.2); o.start(t); o.stop(t + 0.6);
      var hit = noise(c, 0.05), hf = c.createBiquadFilter(), hg = c.createGain();
      hf.type = 'lowpass'; hf.frequency.value = 900;
      env(hg, t, 0.002, 0.35, 0.05);
      hit.connect(hf); hf.connect(hg); send(c, hg, 0.1); hit.start(t);
      [[176, 0.12], [263.5, 0.08], [371, 0.06], [498, 0.04], [612, 0.025]].forEach(function (p) {
        var go = c.createOscillator(), gg = c.createGain();
        go.type = 'sine'; go.frequency.value = p[0];
        go.frequency.linearRampToValueAtTime(p[0] * 0.995, t + 1.8);
        gg.gain.setValueAtTime(0.0001, t + 0.03);
        gg.gain.exponentialRampToValueAtTime(p[1], t + 0.18);
        gg.gain.exponentialRampToValueAtTime(0.0001, t + 1.9);
        go.connect(gg); send(c, gg, 0.7); go.start(t + 0.03); go.stop(t + 2);
      });
    },
    // A check or save called for you: a brief horn call, two quick notes.
    check: function (c, t) {
      brass(c, t, 392.0, 0.17, 0.16);
      brass(c, t + 0.2, 587.33, 0.42, 0.18);
    },
    // A level granted: three notes rising to a held top note, with a glint.
    level: function (c, t) {
      brass(c, t, 523.25, 0.14, 0.15, 5);
      brass(c, t + 0.15, 659.25, 0.14, 0.15, 5);
      brass(c, t + 0.3, 783.99, 0.14, 0.15, 5);
      brass(c, t + 0.45, 1046.5, 0.7, 0.17, 5);
      brass(c, t + 0.45, 523.25, 0.7, 0.09, 3);
      bell(c, t + 0.47, 2093, 0.05, 1.2);
    },
    // A shop opens, a chase begins: a single soft bell.
    bell: function (c, t) { bell(c, t, 880, 0.2, 1.6); },
    // The X-card, for the gamemaster: a tick the table should not notice.
    xcard: function (c, t) {
      var tick = noise(c, 0.006), f = c.createBiquadFilter(), g = c.createGain();
      f.type = 'bandpass'; f.frequency.value = 3200; f.Q.value = 4;
      env(g, t, 0.001, 0.12, 0.006);
      tick.connect(f); f.connect(g); g.connect(c.destination); tick.start(t);
    }
  };
  var URGENCY = ['turn', 'check', 'level', 'handout', 'bell', 'xcard'];

  function cue(name) {
    if (!enabled() || !CUES[name]) return;
    var c = audio();
    if (c) CUES[name](c, c.currentTime + 0.03);
    // Said aloud for whoever listens (a test, a page of its own).
    document.dispatchEvent(new CustomEvent('paravent-cue', { detail: { sound: name } }));
  }

  var heard = {};
  var waiting = [];
  var queued = false;
  // What fills in just after the page loads (the fight strip, a first live update) is
  // what the page came with too.
  var quietUntil = Date.now() + 1500;

  function collect(root, sound) {
    var found = [];
    if (root.nodeType !== 1) return found;
    if (root.hasAttribute('data-cue')) found.push(root);
    var inner = root.querySelectorAll('[data-cue]');
    for (var i = 0; i < inner.length; i++) found.push(inner[i]);
    found.forEach(function (el) {
      var value = el.getAttribute('data-cue');
      if (heard[value]) return;
      heard[value] = true;
      if (sound && Date.now() > quietUntil) waiting.push(value.split('|')[0]);
    });
  }

  function flush() {
    queued = false;
    var best = null;
    waiting.forEach(function (name) {
      if (URGENCY.indexOf(name) >= 0 && (best === null || URGENCY.indexOf(name) < URGENCY.indexOf(best))) best = name;
    });
    waiting = [];
    if (best) cue(best);
  }

  collect(document.documentElement, false);  // what the page came with is not news
  if (window.MutationObserver) {
    new MutationObserver(function (records) {
      records.forEach(function (r) {
        for (var i = 0; i < r.addedNodes.length; i++) collect(r.addedNodes[i], true);
        if (r.type === 'attributes') collect(r.target, true);
      });
      if (waiting.length && !queued) { queued = true; setTimeout(flush, 60); }
    }).observe(document.documentElement, {
      childList: true, subtree: true, attributes: true, attributeFilter: ['data-cue']
    });
  }

  window.ParaventSound = { roll: play, cue: cue };
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
