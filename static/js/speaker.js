// The speaker (documentation/SOUND.md): the one page that makes sound. It follows the
// desk's state over an event stream and mixes with the Web Audio API: effects and short
// loops are decoded into memory, music and long files stream through audio elements.
(function () {
  'use strict';

  var root = document.getElementById('sound-speaker');
  if (!root) return;

  var FADE = 1.5;
  var POOL = 6;  // iOS lets only elements started by a tap play later
  var ctx = null;
  var master = null;
  var voices = {};  // id → a sounding loop or the music
  var buffers = {};
  var elements = [];
  var lastShot = null;

  var startButton = root.querySelector('[data-speaker-start]');
  var stateLine = root.querySelector('[data-speaker-state]');
  var now = root.querySelector('[data-speaker-now]');
  var awakeHint = root.querySelector('[data-speaker-awake]');

  function silentWav() {
    var samples = 2205;
    var bytes = new Uint8Array(44 + samples * 2);
    var view = new DataView(bytes.buffer);
    function text(at, s) { for (var i = 0; i < s.length; i++) bytes[at + i] = s.charCodeAt(i); }
    text(0, 'RIFF'); view.setUint32(4, 36 + samples * 2, true); text(8, 'WAVEfmt ');
    view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, 22050, true); view.setUint32(28, 44100, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    text(36, 'data'); view.setUint32(40, samples * 2, true);
    return URL.createObjectURL(new Blob([bytes], { type: 'audio/wav' }));
  }

  function blessElements() {
    var silence = silentWav();
    for (var i = 0; i < POOL; i++) {
      var el = new Audio();
      el.preload = 'auto';
      el.src = silence;
      var played = el.play();
      if (played && played.catch) played.catch(function () { /* tried; a later play may fail */ });
      elements.push(el);
    }
  }

  function takeElement() {
    var el = elements.pop();
    if (el && !el.node) {
      el.node = ctx.createMediaElementSource(el);
    }
    return el;
  }

  function buffer(url) {
    if (!buffers[url]) {
      buffers[url] = fetch(url, { credentials: 'same-origin' })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.arrayBuffer(); })
        .then(function (data) {
          return new Promise(function (resolve, reject) { ctx.decodeAudioData(data, resolve, reject); });
        });
      buffers[url].catch(function () { delete buffers[url]; });
    }
    return buffers[url];
  }

  function play(item, loop, fade) {
    var voice = { item: item, gain: ctx.createGain(), dead: false, end: null };
    voice.gain.gain.setValueAtTime(fade ? 0 : item.volume, ctx.currentTime);
    if (fade) voice.gain.gain.linearRampToValueAtTime(item.volume, ctx.currentTime + fade);
    voice.gain.connect(master);
    if (item.streamed) {
      var el = takeElement();
      if (!el) return voice;  // every element is busy: this one stays silent
      el.src = item.url;
      el.loop = loop;
      el.node.connect(voice.gain);
      el.play().catch(function () { showStart(); });
      voice.end = function () { el.pause(); el.node.disconnect(); elements.push(el); };
    } else {
      buffer(item.url).then(function (decoded) {
        if (voice.dead) return;
        var node = ctx.createBufferSource();
        node.buffer = decoded;
        node.loop = loop;
        node.connect(voice.gain);
        node.start();
        voice.end = function () { node.stop(); node.disconnect(); };
      }).catch(function () { /* a missing file stays silent; the others play on */ });
    }
    return voice;
  }

  function fadeOut(voice) {
    voice.dead = true;
    var t = ctx.currentTime;
    voice.gain.gain.cancelScheduledValues(t);
    voice.gain.gain.setValueAtTime(voice.gain.gain.value, t);
    voice.gain.gain.linearRampToValueAtTime(0, t + FADE);
    setTimeout(function () {
      if (voice.end) voice.end();
      voice.gain.disconnect();
    }, FADE * 1000 + 100);
  }

  function shoot(item) {
    var voice = play(item, false, 0);
    if (item.streamed) setTimeout(function () { fadeOut(voice); }, 60000);
  }

  function apply(state) {
    master.gain.linearRampToValueAtTime(state.master, ctx.currentTime + 0.1);
    var wanted = {};
    state.loops.forEach(function (x) { wanted[x.id] = x; });
    if (state.music) wanted[state.music.id] = state.music;
    Object.keys(voices).forEach(function (id) {
      if (!wanted[id]) { fadeOut(voices[id]); delete voices[id]; }
    });
    Object.keys(wanted).forEach(function (id) {
      var item = wanted[id];
      if (voices[id]) {
        voices[id].gain.gain.linearRampToValueAtTime(item.volume, ctx.currentTime + 0.1);
      } else {
        voices[id] = play(item, true, FADE);
      }
    });
    if (lastShot === null) lastShot = state.shot;
    state.shots.forEach(function (shot) {
      if (shot.n > lastShot) { lastShot = shot.n; shoot(shot); }
    });
    now.textContent = '';
    Object.keys(wanted).forEach(function (id) {
      var row = document.createElement('li');
      row.textContent = wanted[id].name;
      now.appendChild(row);
    });
  }

  function showStart() { startButton.hidden = false; }

  function keepAwake() {
    if (!navigator.wakeLock) { awakeHint.hidden = false; return; }
    navigator.wakeLock.request('screen').catch(function () { awakeHint.hidden = false; });
  }

  function start() {
    var Ctx = window.AudioContext || window.webkitAudioContext;
    if (!ctx) {
      ctx = new Ctx();
      master = ctx.createGain();
      master.connect(ctx.destination);
      blessElements();
      var source = new EventSource('/gm/sound/events?speaker=1');
      source.addEventListener('revision', function (e) {
        if (e.data) apply(JSON.parse(e.data));
      });
    }
    ctx.resume();
    keepAwake();
    startButton.hidden = true;
    stateLine.textContent = stateLine.dataset.on;
    stateLine.hidden = false;
  }

  startButton.addEventListener('click', start);

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState !== 'visible' || !ctx) return;
    keepAwake();
    if (ctx.state !== 'running') ctx.resume().catch(showStart);
  });
})();
