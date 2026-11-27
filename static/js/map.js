// A map in its viewer (documentation/MAPS.md): Leaflet in its flat-picture mode, a large
// map from its tiles, a small one as one picture; where it was left kept per user; the
// ruler; and the line drawn for the scale. Everything is measured in the original's
// pixels, by the scale's feet per pixel.
(function () {
  'use strict';

  var el = document.getElementById('map-stage');
  if (!el || !window.L) return;

  var W = +el.dataset.width, H = +el.dataset.height, Z = +el.dataset.zoom;
  var words = JSON.parse(el.dataset.words || '{}');
  var feetPerPx = parseFloat(el.dataset.feet) || 0;
  var long = !!el.dataset.long, metric = !!el.dataset.metric;
  var lang = el.dataset.lang || 'en';
  var mapId = el.dataset.map;
  var readout = document.querySelector('[data-map-readout]');

  var map = L.map(el, {
    crs: L.CRS.Simple,
    zoomSnap: 0.25,
    zoomDelta: 0.5,
    wheelPxPerZoomLevel: 90,
    attributionControl: false,
    maxZoom: Z + 2,
    minZoom: -6
  });

  // The original's pixels and the map's coordinates.
  function toLatLng(x, y) { return map.unproject([x, y], Z); }
  function toPixels(latlng) { var p = map.project(latlng, Z); return [p.x, p.y]; }

  var bounds = L.latLngBounds(toLatLng(0, H), toLatLng(W, 0));
  if (el.dataset.tiles) {
    L.tileLayer(el.dataset.tiles + '/{z}/{x}/{y}.webp?d=' + el.dataset.digest, {
      bounds: bounds,
      tileSize: 256,
      minNativeZoom: 0,
      maxNativeZoom: Z,
      minZoom: -6,
      maxZoom: Z + 2,
      noWrap: true,
      keepBuffer: 3
    }).addTo(map);
  } else {
    L.imageOverlay(el.dataset.image, bounds).addTo(map);
  }
  map.setMinZoom(Math.min(map.getBoundsZoom(bounds), 0) - 1);
  map.setMaxBounds(bounds.pad(0.25));

  var shown = el.dataset.view ? JSON.parse(el.dataset.view) : null;
  if (shown) map.setView(toLatLng(shown.x, shown.y), shown.zoom, { animate: false });
  else map.fitBounds(bounds, { animate: false });

  // -- where it was left (app/services/maps.py keep_view) ------------------------------

  var saveTimer = null;
  function report(beacon) {
    var centre = toPixels(map.getCenter());
    var data = new FormData();
    data.append('x', centre[0].toFixed(1));
    data.append('y', centre[1].toFixed(1));
    data.append('zoom', map.getZoom().toFixed(2));
    var url = '/gm/maps/' + mapId + '/view';
    if (beacon && navigator.sendBeacon) navigator.sendBeacon(url, data);
    else fetch(url, { method: 'POST', body: data, credentials: 'same-origin' });
  }
  map.on('moveend', function () {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () { saveTimer = null; report(false); }, 700);
  });
  window.addEventListener('pagehide', function () {
    if (saveTimer) { clearTimeout(saveTimer); report(true); }
  });

  // -- distances --------------------------------------------------------------------

  function number(value) {
    var digits = value >= 100 ? 0 : value >= 10 ? 1 : 2;
    return new Intl.NumberFormat(lang, { maximumFractionDigits: digits, useGrouping: false })
      .format(value);
  }

  // A distance in the reader's units, by Paravent's own factors: 1 mile = 1.5 km,
  // 1 ft = 0.3 m (app/services/units.py).
  function distance(feet) {
    if (long) {
      var miles = feet / 5280;
      return metric ? number(miles * 1.5) + ' km' : number(miles) + ' mi.';
    }
    return metric ? number(feet * 0.3) + ' m' : number(feet) + ' ft.';
  }

  function fill(template, values) {
    return String(template || '').replace(/\{(\w+)\}/g, function (all, key) {
      return key in values ? values[key] : all;
    });
  }

  // How long the way takes on foot at each pace: 2, 3 and 4 miles an hour, eight
  // travelling hours to the day.
  function travel(feet) {
    var miles = feet / 5280;
    return [['slow', 2], ['normal', 3], ['fast', 4]].map(function (pace) {
      var hours = miles / pace[1];
      var time;
      if (hours < 8) {
        time = fill(words.hours, { n: number(Math.max(hours, 0.1)) });
      } else {
        var days = Math.round(hours / 8 * 10) / 10;
        time = fill(days === 1 ? words.days.one : words.days.other, { n: number(days) });
      }
      return (words.pace[pace[0]] || pace[0]) + ' ' + time;
    }).join(' · ');
  }

  function pathFeet(points) {
    var total = 0;
    for (var i = 1; i < points.length; i++) {
      var a = toPixels(points[i - 1]), b = toPixels(points[i]);
      total += Math.hypot(b[0] - a[0], b[1] - a[1]);
    }
    return total * feetPerPx;
  }

  function say(text) { if (readout) readout.textContent = text; }

  // -- the tools: the ruler, and the line of the scale ----------------------------------

  var mode = '';
  var points = [];
  var layer = L.layerGroup().addTo(map);
  var line = null, trail = null;
  var rulerButton = document.querySelector('[data-map-tool="ruler"]');

  function clearDrawing() {
    layer.clearLayers();
    points = [];
    line = trail = null;
  }

  function setMode(next) {
    mode = next;
    el.classList.toggle('map-drawing', !!mode);
    if (mode) map.doubleClickZoom.disable(); else map.doubleClickZoom.enable();
    if (rulerButton) rulerButton.setAttribute('aria-pressed', mode === 'ruler' ? 'true' : 'false');
  }

  function showRuler(extra) {
    var all = extra ? points.concat([extra]) : points;
    if (all.length < 2) { say(words.start || ''); return; }
    var feet = pathFeet(all);
    var text = fill(words.total, { distance: distance(feet) });
    if (long) text += ' · ' + travel(feet);
    say(text);
  }

  function addPoint(latlng) {
    points.push(latlng);
    L.circleMarker(latlng, { radius: 4, className: 'map-ruler-point', interactive: false })
      .addTo(layer);
    if (!line) line = L.polyline(points, { className: 'map-ruler-line', interactive: false }).addTo(layer);
    else line.setLatLngs(points);
  }

  function endRuler() {
    setMode('');
    if (trail) { layer.removeLayer(trail); trail = null; }
    if (points.length < 2) { clearDrawing(); say(''); }
  }

  if (rulerButton) {
    rulerButton.addEventListener('click', function () {
      if (mode === 'ruler') { endRuler(); return; }
      clearDrawing();
      setMode('ruler');
      say(words.start || '');
    });
  }

  // The scale's line: two taps on the map, then back to the dialog with it.
  var dialog = document.getElementById('tool-scale');
  function drawn(a, b) {
    var form = dialog && dialog.querySelector('.map-scale-form');
    if (!form) return;
    var pa = toPixels(a), pb = toPixels(b);
    form.elements.x1.value = pa[0].toFixed(1);
    form.elements.y1.value = pa[1].toFixed(1);
    form.elements.x2.value = pb[0].toFixed(1);
    form.elements.y2.value = pb[1].toFixed(1);
    var radio = form.querySelector('input[name="way"][value="line"]');
    if (radio) radio.checked = true;
    var state = form.querySelector('[data-line-state]');
    if (state) state.textContent = words.line_drawn || '';
    if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open', '');
    var amount = form.elements.amount;
    if (amount) amount.focus();
  }
  if (dialog) {
    dialog.addEventListener('click', function (e) {
      var draw = e.target.closest ? e.target.closest('[data-map-draw]') : null;
      if (!draw) return;
      e.preventDefault();
      dialog.close();
      clearDrawing();
      setMode('line');
      say(words.line_start || '');
    });
  }

  map.on('click', function (e) {
    if (mode === 'ruler') {
      addPoint(e.latlng);
      showRuler();
    } else if (mode === 'line') {
      addPoint(e.latlng);
      if (points.length === 1) { say(words.line_end || ''); return; }
      var a = points[0], b = points[1];
      setMode('');
      say('');
      drawn(a, b);
    }
  });

  map.on('mousemove', function (e) {
    if (!mode || !points.length) return;
    var last = points[points.length - 1];
    if (!trail) {
      trail = L.polyline([last, e.latlng], { className: 'map-ruler-trail', interactive: false })
        .addTo(layer);
    } else {
      trail.setLatLngs([last, e.latlng]);
    }
    if (mode === 'ruler') showRuler(e.latlng);
  });

  map.on('dblclick', function () { if (mode === 'ruler') endRuler(); });

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || !mode) return;
    if (mode === 'ruler') endRuler();
    else { setMode(''); clearDrawing(); say(''); }
  });
})();
