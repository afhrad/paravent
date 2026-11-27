// A map in its viewer (documentation/MAPS.md): Leaflet in its flat-picture mode, a large
// map from its tiles, a small one as one picture; where it was left kept per user; the
// ruler; and the line drawn for the scale. Everything is measured in the original's
// pixels, by the scale's feet per pixel. Pins point at the world's places, people and
// stories, and other maps lie on this one as frames.
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
    var pinTool = document.querySelector('[data-map-tool="pin"]');
    if (pinTool) pinTool.setAttribute('aria-pressed', mode === 'pin' ? 'true' : 'false');
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

  // -- pins and the maps lying on this one (documentation/MAPS.md §6) ----------------

  var pins = JSON.parse(el.dataset.pins || '[]');
  var frames = JSON.parse(el.dataset.frames || '[]');
  var pinLayer = L.layerGroup().addTo(map);
  var markers = {};

  function post(url, fields) {
    var data = new FormData();
    Object.keys(fields || {}).forEach(function (k) { data.append(k, fields[k]); });
    return fetch(url, { method: 'POST', body: data, credentials: 'same-origin' });
  }

  function node(tag, attrs, children) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') n.textContent = attrs[k];
      else n.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }

  function shownName(pin) { return pin.label || pin.name; }

  function pinIcon(pin) {
    var name = node('span', { 'class': 'map-pin-name', text: shownName(pin) });
    var holder = node('span', { 'class': 'map-pin-token' });
    holder.innerHTML = pin.token;  // the server's own markup of the element's token
    var wrap = node('span', {}, [holder, name]);
    return L.divIcon({
      className: 'map-pin map-pin-' + pin.kind + (pin.gone ? ' map-pin-gone' : ''),
      html: wrap.innerHTML,
      iconSize: [28, 28],
      iconAnchor: [14, 14],
      popupAnchor: [0, -14]
    });
  }

  function pinCard(pin, marker) {
    var base = '/gm/maps/' + mapId + '/pins/' + pin.id;
    var kind = (words.kinds || {})[pin.kind] || pin.kind;
    var links = node('p', { 'class': 'map-card-links' }, [
      pin.url ? node('a', { href: pin.url, 'class': 'btn btn-sm', text: words.open }) : null,
      pin.map_url ? node('a', { href: pin.map_url, 'class': 'btn btn-sm', text: words.go_to_map }) : null
    ]);
    var labelInput = node('input', { type: 'text', name: 'label', value: pin.label || '',
      maxlength: '120', 'aria-label': words.label, placeholder: pin.name || words.label });
    var noteInput = pin.kind === 'note'
      ? node('textarea', { name: 'note', rows: '3', 'aria-label': words.note_text }) : null;
    if (noteInput) noteInput.value = pin.note || '';
    var save = node('button', { type: 'submit', 'class': 'btn btn-sm btn-primary', text: words.save });
    var remove = node('button', { type: 'button', 'class': 'btn btn-sm btn-ghost btn-danger', text: words.remove });
    var form = node('form', { 'class': 'map-card-form' }, [
      labelInput, noteInput, node('span', { 'class': 'map-card-actions' }, [save, remove])
    ]);
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      post(base, { label: labelInput.value, note: noteInput ? noteInput.value : '' }).then(function (r) {
        if (!r.ok) return;
        pin.label = labelInput.value.trim();
        if (noteInput) { pin.note = noteInput.value; pin.excerpt = noteInput.value.split('\n')[0]; }
        marker.setIcon(pinIcon(pin));
        marker.setPopupContent(pinCard(pin, marker));
        marker.closePopup();
      });
    });
    remove.addEventListener('click', function () {
      post(base + '/delete').then(function (r) {
        if (r.ok) { pinLayer.removeLayer(marker); delete markers[pin.id]; }
      });
    });
    return node('div', { 'class': 'map-card' }, [
      node('span', { 'class': 'eyebrow', text: kind + (pin.gone ? ' · ' + words.gone : '') }),
      node('b', { 'class': 'map-card-title', text: shownName(pin) }),
      pin.label && pin.name && pin.label !== pin.name
        ? node('span', { 'class': 'muted', text: pin.name }) : null,
      pin.excerpt ? node('p', { 'class': 'map-card-text', text: pin.excerpt }) : null,
      links,
      form
    ]);
  }

  function addPin(pin) {
    var marker = L.marker(toLatLng(pin.x, pin.y), {
      icon: pinIcon(pin), draggable: true, keyboard: true, title: shownName(pin), riseOnHover: true
    });
    marker.bindPopup(function () { return pinCard(pin, marker); }, { minWidth: 220, maxWidth: 300 });
    marker.on('dragend', function () {
      var at = toPixels(marker.getLatLng());
      pin.x = at[0]; pin.y = at[1];
      post('/gm/maps/' + mapId + '/pins/' + pin.id + '/move', { x: at[0].toFixed(1), y: at[1].toFixed(1) });
    });
    marker.addTo(pinLayer);
    markers[pin.id] = marker;
  }
  pins.forEach(addPin);

  frames.forEach(function (f) {
    var rect = L.rectangle(
      L.latLngBounds(toLatLng(f.x, f.y + f.height), toLatLng(f.x + f.width, f.y)),
      { className: 'map-frame', weight: 2 }
    ).addTo(map);
    rect.bindTooltip(f.name, { permanent: true, direction: 'center', className: 'map-frame-name' });
    var open = node('a', { href: f.url, 'class': 'btn btn-sm btn-primary', text: words.go_to_map });
    var unframe = node('form', { method: 'post', action: '/gm/maps/' + f.id + '/frame/clear' }, [
      node('input', { type: 'hidden', name: 'next', value: location.pathname }),
      node('button', { type: 'submit', 'class': 'btn btn-sm btn-ghost btn-danger', text: words.unframe })
    ]);
    var card = L.popup().setContent(node('div', { 'class': 'map-card' }, [
      node('b', { 'class': 'map-card-title', text: f.name }),
      node('p', { 'class': 'map-card-links' }, [open]),
      unframe
    ]));
    // While a tool is in hand, a tap inside a frame is a tap on the map.
    rect.on('click', function (e) {
      L.DomEvent.stop(e);
      if (mode) tap(e.latlng); else card.setLatLng(e.latlng).openOn(map);
    });
  });

  // The names beside the pins show once the map is zoomed in.
  function labels() { el.classList.toggle('map-show-names', map.getZoom() >= Z - 3); }
  map.on('zoomend', labels);
  labels();

  var focus = el.dataset.focus && markers[el.dataset.focus];
  if (focus) {
    map.setView(focus.getLatLng(), Math.max(map.getZoom(), Z - 2), { animate: false });
    focus.openPopup();
  }

  // The pin tool: a tap, then the dialog with the spot filled in.
  var pinButton = document.querySelector('[data-map-tool="pin"]');
  var pinDialog = document.getElementById('tool-pin');
  if (pinButton) {
    pinButton.addEventListener('click', function () {
      if (mode === 'pin') { setMode(''); say(''); return; }
      clearDrawing();
      setMode('pin');
      pinButton.setAttribute('aria-pressed', 'true');
      say(words.pin_place || '');
    });
  }
  function placePin(latlng) {
    var form = pinDialog && pinDialog.querySelector('.map-pin-form');
    if (!form) return;
    var at = toPixels(latlng);
    form.elements.x.value = at[0].toFixed(1);
    form.elements.y.value = at[1].toFixed(1);
    if (pinDialog.showModal) pinDialog.showModal(); else pinDialog.setAttribute('open', '');
    var filter = form.querySelector('[data-pin-filter]');
    if (filter) filter.focus();
  }
  if (pinDialog) {
    var filter = pinDialog.querySelector('[data-pin-filter]');
    if (filter) {
      filter.addEventListener('input', function () {
        var q = filter.value.trim().toLowerCase();
        pinDialog.querySelectorAll('[data-pin-text]').forEach(function (row) {
          row.hidden = !!q && row.dataset.pinText.indexOf(q) < 0;
        });
      });
    }
    // Typing a new place's name chooses "new place".
    var newName = pinDialog.querySelector('input[name="place_name"]');
    if (newName) {
      newName.addEventListener('input', function () {
        var radio = pinDialog.querySelector('input[name="choice"][value="new"]');
        if (radio && newName.value.trim()) radio.checked = true;
      });
    }
  }

  // The frame tool: choose the map, then its rectangle with two taps.
  var frameButton = document.querySelector('[data-map-tool="frame"]');
  var frameDialog = document.getElementById('tool-frame');
  if (frameButton && frameDialog) {
    frameButton.addEventListener('click', function () {
      if (frameDialog.showModal) frameDialog.showModal(); else frameDialog.setAttribute('open', '');
    });
    frameDialog.addEventListener('click', function (e) {
      var draw = e.target.closest ? e.target.closest('[data-map-frame-draw]') : null;
      if (!draw) return;
      e.preventDefault();
      frameDialog.close();
      clearDrawing();
      setMode('frame');
      say(words.frame_start || '');
    });
  }
  function framed(a, b) {
    var form = frameDialog && frameDialog.querySelector('.map-frame-form');
    if (!form) return;
    var pa = toPixels(a), pb = toPixels(b);
    L.rectangle(L.latLngBounds(a, b), { className: 'map-frame map-frame-new', interactive: false }).addTo(layer);
    form.elements.x1.value = pa[0].toFixed(1);
    form.elements.y1.value = pa[1].toFixed(1);
    form.elements.x2.value = pb[0].toFixed(1);
    form.elements.y2.value = pb[1].toFixed(1);
    var state = form.querySelector('[data-frame-state]');
    if (state) state.textContent = words.frame_drawn || '';
    if (frameDialog.showModal) frameDialog.showModal(); else frameDialog.setAttribute('open', '');
  }

  function tap(latlng) {
    if (mode === 'ruler') {
      addPoint(latlng);
      showRuler();
    } else if (mode === 'line' || mode === 'frame') {
      addPoint(latlng);
      if (points.length === 1) {
        say(mode === 'line' ? words.line_end || '' : words.frame_end || '');
        return;
      }
      var a = points[0], b = points[1], was = mode;
      setMode('');
      say('');
      if (was === 'line') drawn(a, b); else { clearDrawing(); framed(a, b); }
    } else if (mode === 'pin') {
      setMode('');
      say('');
      placePin(latlng);
    }
  }
  map.on('click', function (e) { tap(e.latlng); });

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
