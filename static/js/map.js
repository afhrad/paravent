// A map in its viewer (documentation/MAPS.md): Leaflet in its flat-picture mode, a large
// map from its tiles, a small one as one picture; where it was left kept per user; the
// ruler; and the line drawn for the scale. Everything is measured in the original's
// pixels, by the scale's feet per pixel. Pins point at the world's places, people and
// stories, and other maps lie on this one as frames; each sits on a layer, shown or hidden
// for this user, and names are written on the map at the picture's own scale.
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
  // The gamemaster's map, or the same map as the players see it: nothing to change.
  var base = el.dataset.base || '/gm/maps/' + mapId;
  var player = !!el.dataset.player;
  var readout = document.querySelector('[data-map-readout]');
  var grid = el.dataset.grid ? JSON.parse(el.dataset.grid) : null;

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
    var url = base + '/view';
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

  // The tools that draw: while one is in hand, a drag draws instead of moving the map,
  // and a tap still sets a point. The arrow keys and the zoom still move the map.
  var DRAWING = { ruler: true, line: true, frame: true, route: true };

  function setMode(next) {
    mode = next;
    el.classList.toggle('map-drawing', !!mode);
    if (mode) map.doubleClickZoom.disable(); else map.doubleClickZoom.enable();
    if (DRAWING[mode]) map.dragging.disable(); else map.dragging.enable();
    if (rulerButton) rulerButton.setAttribute('aria-pressed', mode === 'ruler' ? 'true' : 'false');
    ['pin', 'label', 'route'].forEach(function (tool) {
      var b = document.querySelector('[data-map-tool="' + tool + '"]');
      if (b) b.setAttribute('aria-pressed', mode === tool ? 'true' : 'false');
    });
  }

  function showRuler(extra) {
    var all = extra ? points.concat([extra]) : points;
    if (all.length < 2) { say(words.start || ''); return; }
    var feet = pathFeet(all);
    var text = fill(words.total, { distance: distance(feet) });
    if (grid) {
      var hexes = Math.round(feet / feetPerPx / grid.across);
      text += ' · ' + fill(hexes === 1 ? words.hexes.one : words.hexes.other, { n: hexes });
    }
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
  var layers = JSON.parse(el.dataset.layers || '[]');
  var labels = JSON.parse(el.dataset.labels || '[]');
  var markers = {};

  // -- layers (documentation/MAPS.md §7): a group and a pane each, the last on top ----

  var groups = {}, byId = {};
  layers.forEach(function (layer, index) {
    var pane = map.createPane('layer-' + layer.id);
    pane.style.zIndex = String(601 + index);  // above the tiles, below tooltips and cards
    groups[layer.id] = L.layerGroup();
    byId[layer.id] = layer;
  });
  function groupOf(id) {
    return groups[id] ? id : (layers[0] ? layers[0].id : '');
  }
  function paneOf(id) { return groups[groupOf(id)] ? 'layer-' + groupOf(id) : 'markerPane'; }
  function isLocked(id) { var l = byId[groupOf(id)]; return !!(l && l.locked); }
  var loose = L.layerGroup().addTo(map);  // a map without layers yet
  function holder(id) { return groups[groupOf(id)] || loose; }

  // A layer shows when the user shows it and the map is zoomed in far enough.
  function showLayers() {
    var zoom = map.getZoom();
    layers.forEach(function (layer) {
      var on = layer.shown && (layer.from_zoom === null || zoom >= layer.from_zoom - 1e-6);
      var group = groups[layer.id];
      if (on && !map.hasLayer(group)) group.addTo(map);
      else if (!on && map.hasLayer(group)) map.removeLayer(group);
    });
  }

  function layerSelect(current) {
    var select = node('select', { name: 'layer', 'aria-label': words.layer });
    layers.forEach(function (layer) {
      var opt = node('option', { value: layer.id, text: layer.name });
      if (layer.id === groupOf(current)) opt.selected = true;
      select.appendChild(opt);
    });
    return select;
  }

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
    var url = base + '/pins/' + pin.id;
    var kind = (words.kinds || {})[pin.kind] || pin.kind;
    var links = node('p', { 'class': 'map-card-links' }, [
      pin.url ? node('a', { href: pin.url, 'class': 'btn btn-sm', text: words.open }) : null,
      pin.map_url ? node('a', { href: pin.map_url, 'class': 'btn btn-sm', text: words.go_to_map }) : null
    ]);
    if (player) {
      return node('div', { 'class': 'map-card' }, [
        node('span', { 'class': 'eyebrow', text: kind }),
        node('b', { 'class': 'map-card-title', text: shownName(pin) }),
        pin.note ? node('p', { 'class': 'map-card-text', text: pin.note }) : null,
        pin.map_url ? links : null
      ]);
    }
    var labelInput = node('input', { type: 'text', name: 'label', value: pin.label || '',
      maxlength: '120', 'aria-label': words.label, placeholder: pin.name || words.label });
    var noteInput = pin.kind === 'note'
      ? node('textarea', { name: 'note', rows: '3', 'aria-label': words.note_text }) : null;
    if (noteInput) noteInput.value = pin.note || '';
    var save = node('button', { type: 'submit', 'class': 'btn btn-sm btn-primary', text: words.save });
    var remove = node('button', { type: 'button', 'class': 'btn btn-sm btn-ghost btn-danger', text: words.remove });
    var layerInput = layers.length > 1 ? layerSelect(pin.layer) : null;
    var form = node('form', { 'class': 'map-card-form' }, [
      labelInput, noteInput, layerInput,
      node('span', { 'class': 'map-card-actions' }, [save, remove])
    ]);
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var fields = { label: labelInput.value, note: noteInput ? noteInput.value : '' };
      if (layerInput) fields.layer = layerInput.value;
      post(url, fields).then(function (r) {
        if (!r.ok) return;
        pin.label = labelInput.value.trim();
        if (noteInput) { pin.note = noteInput.value; pin.excerpt = noteInput.value.split('\n')[0]; }
        marker.closePopup();
        holder(pin.layer).removeLayer(marker);
        if (layerInput) pin.layer = layerInput.value;
        addPin(pin);  // again, on its layer
      });
    });
    remove.addEventListener('click', function () {
      post(url + '/delete').then(function (r) {
        if (r.ok) { holder(pin.layer).removeLayer(marker); delete markers[pin.id]; }
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

  // While a tool is in hand, a tap on a pin or a name is a tap on the map.
  function passTaps(marker) {
    marker.on('click', function (e) {
      if (!mode) return;
      marker.closePopup();
      if (!DRAWING[mode]) tap(e.latlng);  // a drawing tool reads the pointer itself
    });
  }

  function addPin(pin) {
    var marker = L.marker(toLatLng(pin.x, pin.y), {
      icon: pinIcon(pin), draggable: !player && !isLocked(pin.layer), keyboard: true,
      title: shownName(pin),
      riseOnHover: true, pane: paneOf(pin.layer)
    });
    marker.options.layerId = groupOf(pin.layer);
    marker.bindPopup(function () { return pinCard(pin, marker); }, { minWidth: 220, maxWidth: 300 });
    passTaps(marker);
    marker.on('dragend', function () {
      var at = toPixels(marker.getLatLng());
      pin.x = at[0]; pin.y = at[1];
      post(base + '/pins/' + pin.id + '/move', { x: at[0].toFixed(1), y: at[1].toFixed(1) });
    });
    marker.addTo(holder(pin.layer));
    markers[pin.id] = marker;
  }
  pins.forEach(addPin);

  // -- names written on the map: sized in the picture's pixels, turned, styled ----------

  var STYLE_SCALE = { settlement: 1, region: 1.5, water: 1.1, feature: 0.8 };

  function labelIcon(label) {
    var text = node('span', { 'class': 'map-label map-label-' + label.style, text: label.text });
    text.style.setProperty('--s', String(label.size));
    text.style.setProperty('--a', label.angle + 'deg');
    return L.divIcon({ className: 'map-label-anchor', html: text.outerHTML, iconSize: [0, 0] });
  }

  function labelCard(label, marker) {
    var url = base + '/labels/' + label.id;
    var text = node('input', { type: 'text', name: 'text', value: label.text, maxlength: '80',
      'aria-label': words.text, required: '' });
    var style = node('select', { name: 'style', 'aria-label': words.style });
    Object.keys(words.styles || {}).forEach(function (k) {
      var opt = node('option', { value: k, text: words.styles[k] });
      if (k === label.style) opt.selected = true;
      style.appendChild(opt);
    });
    var size = node('input', { type: 'number', name: 'size', min: '1', step: 'any',
      value: String(label.size), 'aria-label': words.size, title: words.size });
    var angle = node('input', { type: 'range', name: 'angle', min: '-180', max: '180', step: '1',
      value: String(label.angle), 'aria-label': words.angle, title: words.angle });
    angle.addEventListener('input', function () {
      label.angle = +angle.value;
      marker.setIcon(labelIcon(label));
    });
    var layerInput = layers.length > 1 ? layerSelect(label.layer) : null;
    var save = node('button', { type: 'submit', 'class': 'btn btn-sm btn-primary', text: words.save });
    var remove = node('button', { type: 'button', 'class': 'btn btn-sm btn-ghost btn-danger',
      text: words.remove_label });
    var form = node('form', { 'class': 'map-card-form' }, [
      text, style, node('span', { 'class': 'map-card-row' }, [size, angle]), layerInput,
      node('span', { 'class': 'map-card-actions' }, [save, remove])
    ]);
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var fields = { text: text.value, style: style.value, size: size.value, angle: angle.value };
      if (layerInput) fields.layer = layerInput.value;
      post(url, fields).then(function (r) {
        if (!r.ok) return;
        marker.closePopup();
        holder(label.layer).removeLayer(marker);
        label.text = text.value.trim(); label.style = style.value;
        label.size = +size.value; label.angle = +angle.value;
        if (layerInput) label.layer = layerInput.value;
        addLabel(label);
        scaleLabels();
      });
    });
    remove.addEventListener('click', function () {
      post(url + '/delete').then(function (r) { if (r.ok) holder(label.layer).removeLayer(marker); });
    });
    return node('div', { 'class': 'map-card' }, [
      node('span', { 'class': 'eyebrow', text: (words.styles || {})[label.style] || '' }), form
    ]);
  }

  function addLabel(label) {
    var marker = L.marker(toLatLng(label.x, label.y), {
      icon: labelIcon(label), draggable: !player && !isLocked(label.layer), keyboard: !player,
      title: label.text, pane: paneOf(label.layer), interactive: !player
    });
    marker.options.layerId = groupOf(label.layer);
    if (!player) marker.bindPopup(function () { return labelCard(label, marker); }, { minWidth: 240 });
    passTaps(marker);
    marker.on('dragend', function () {
      var at = toPixels(marker.getLatLng());
      label.x = at[0]; label.y = at[1];
      post(base + '/labels/' + label.id + '/move',
        { x: at[0].toFixed(1), y: at[1].toFixed(1) });
    });
    marker.addTo(holder(label.layer));
  }
  labels.forEach(addLabel);

  // How large a picture pixel is on screen now; a name smaller than a few pixels hides.
  function scaleLabels() {
    var k = Math.pow(2, map.getZoom() - Z);
    el.style.setProperty('--k', String(k));
    el.querySelectorAll('.map-label').forEach(function (n) {
      var px = parseFloat(n.style.getPropertyValue('--s')) * k;
      n.classList.toggle('is-tiny', px < 5);
    });
  }

  frames.forEach(function (f) {
    var rect = L.rectangle(
      L.latLngBounds(toLatLng(f.x, f.y + f.height), toLatLng(f.x + f.width, f.y)),
      { className: 'map-frame', weight: 2 }
    ).addTo(map);
    rect.bindTooltip(f.name, { permanent: true, direction: 'center', className: 'map-frame-name' });
    var open = node('a', { href: f.url, 'class': 'btn btn-sm btn-primary', text: words.go_to_map });
    var unframe = player ? null : node('form', { method: 'post', action: '/gm/maps/' + f.id + '/frame/clear' }, [
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
      if (mode && !DRAWING[mode]) tap(e.latlng);
      else if (!mode) card.setLatLng(e.latlng).openOn(map);
    });
  });

  // The names beside the pins show once the map is zoomed in.
  function pinNames() { el.classList.toggle('map-show-names', map.getZoom() >= Z - 3); }
  map.on('zoomend', function () { pinNames(); showLayers(); scaleLabels(); });
  pinNames();
  showLayers();
  scaleLabels();

  // -- the hex grid (documentation/MAPS.md §8b): drawn on a canvas over the picture ----

  var gridPane = map.createPane('grid');
  gridPane.style.zIndex = '450';  // above the picture, beneath every layer
  gridPane.style.pointerEvents = 'none';
  var gridCanvas = null;

  // The hexes in view, as one path: each hex's centre from the grid's own, its columns
  // (flat side up) or rows (corner up) shifted by half a hex every other one.
  function drawGrid() {
    if (!gridCanvas) return;
    var size = map.getSize();
    var ratio = window.devicePixelRatio || 1;
    var pad = { x: size.x / 2, y: size.y / 2 };
    var w = size.x + 2 * pad.x, h = size.y + 2 * pad.y;
    gridCanvas.width = Math.round(w * ratio);
    gridCanvas.height = Math.round(h * ratio);
    gridCanvas.style.width = w + 'px';
    gridCanvas.style.height = h + 'px';
    L.DomUtil.setPosition(gridCanvas, map.containerPointToLayerPoint([-pad.x, -pad.y]));
    var ctx = gridCanvas.getContext('2d');
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, w, h);
    var k = Math.pow(2, map.getZoom() - Z);
    var d = grid.across, r = d / Math.sqrt(3);
    if (d * k < 10) return;  // too small to read
    var o = map.latLngToContainerPoint(toLatLng(0, 0));
    function sx(x) { return o.x + x * k + pad.x; }
    function sy(y) { return o.y + y * k + pad.y; }
    // The view in the original's pixels.
    var x0 = (-pad.x - o.x) / k, x1 = (size.x + pad.x - o.x) / k;
    var y0 = (-pad.y - o.y) / k, y1 = (size.y + pad.y - o.y) / k;
    // Only over the picture itself, not the margin around it.
    ctx.save();
    ctx.beginPath();
    ctx.rect(sx(0), sy(0), W * k, H * k);
    ctx.clip();
    var flat = grid.orientation !== 'pointy';
    var stepA = flat ? 1.5 * r : d, stepB = flat ? d : 1.5 * r;
    var a0 = Math.floor(((flat ? x0 : y0) - (flat ? grid.x : grid.y)) / stepA) - 1;
    var a1 = Math.ceil(((flat ? x1 : y1) - (flat ? grid.x : grid.y)) / stepA) + 1;
    ctx.beginPath();
    for (var a = a0; a <= a1; a++) {
      var shift = ((a % 2) + 2) % 2 ? stepB / 2 : 0;
      var lo = flat ? y0 - grid.y : x0 - grid.x, hi = flat ? y1 - grid.y : x1 - grid.x;
      var b0 = Math.floor((lo - shift) / stepB) - 1, b1 = Math.ceil((hi - shift) / stepB) + 1;
      for (var b = b0; b <= b1; b++) {
        var cx = flat ? grid.x + a * stepA : grid.x + b * stepB + shift;
        var cy = flat ? grid.y + b * stepB + shift : grid.y + a * stepA;
        for (var i = 0; i <= 6; i++) {
          var angle = Math.PI / 3 * i + (flat ? 0 : Math.PI / 6);
          var px = sx(cx + r * Math.cos(angle)), py = sy(cy + r * Math.sin(angle));
          if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
        }
      }
    }
    var style = getComputedStyle(el);
    ctx.strokeStyle = style.getPropertyValue('--map-grid').trim() || 'rgba(20, 20, 20, 0.45)';
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.restore();
  }

  function showGrid() {
    var on = grid && grid.shown;
    if (on && !gridCanvas) {
      gridCanvas = L.DomUtil.create('canvas', 'map-grid', gridPane);
      drawGrid();
    } else if (!on && gridCanvas) {
      gridCanvas.remove();
      gridCanvas = null;
    }
  }
  map.on('moveend resize', drawGrid);
  map.on('zoomstart', function () { if (gridCanvas) gridCanvas.style.visibility = 'hidden'; });
  map.on('zoomend', function () {
    if (gridCanvas) { gridCanvas.style.visibility = ''; drawGrid(); }
  });
  showGrid();

  // Lining the grid up: a tap on a hex's centre, then back to the dialog with it.
  var gridDialog = document.getElementById('tool-grid');
  if (gridDialog) {
    gridDialog.addEventListener('click', function (e) {
      var place = e.target.closest ? e.target.closest('[data-map-grid-place]') : null;
      if (!place) return;
      e.preventDefault();
      gridDialog.close();
      clearDrawing();
      setMode('grid');
      say(words.grid_place || '');
    });
  }
  function gridPlaced(latlng) {
    var form = gridDialog && gridDialog.querySelector('.map-grid-form');
    if (!form) return;
    var at = toPixels(latlng);
    form.elements.x.value = at[0].toFixed(1);
    form.elements.y.value = at[1].toFixed(1);
    var state = form.querySelector('[data-grid-state]');
    if (state) state.textContent = words.grid_placed || '';
    if (gridDialog.showModal) gridDialog.showModal(); else gridDialog.setAttribute('open', '');
  }

  // -- the map as a picture (documentation/MAPS.md §9b): the part in view, sent along ---

  var pictureOpen = document.querySelector('[data-picture-open]');
  var pictureDialog = document.getElementById('tool-picture');
  if (pictureOpen && pictureDialog) {
    pictureOpen.addEventListener('click', function () {
      var view = map.getBounds();
      var a = toPixels(view.getNorthWest()), b = toPixels(view.getSouthEast());
      var fields = pictureDialog.querySelectorAll('input[name="area"]');
      [a[0], a[1], b[0] - a[0], b[1] - a[1]].forEach(function (v, i) {
        if (fields[i]) fields[i].value = v.toFixed(1);
      });
    }, true);
  }

  // -- roads (documentation/MAPS.md §8) ------------------------------------------------

  var routes = JSON.parse(el.dataset.routes || '[]');
  var party = el.dataset.party ? JSON.parse(el.dataset.party) : null;
  var canTravel = !!el.dataset.travel;
  var editing = null;  // the road whose points are shown to drag

  function stopName(route, stop) {
    if (stop.name) return stop.name;
    if (stop.index === 0) return words.route_start;
    if (stop.index === route.points.length - 1) return words.route_end;
    return fill(words.point, { n: stop.index });
  }

  function miles(m) { return distance(m * 5280); }

  function routeCard(route) {
    var url = base + '/routes/' + route.id;
    var name = node('input', { type: 'text', name: 'name', value: route.name, maxlength: '80',
      'aria-label': words.route_name, placeholder: words.route_name });
    var kind = node('select', { name: 'kind', 'aria-label': words.route_name });
    Object.keys(words.route_kinds || {}).forEach(function (k) {
      var opt = node('option', { value: k, text: words.route_kinds[k] });
      if (k === route.kind) opt.selected = true;
      kind.appendChild(opt);
    });
    var layerInput = layers.length > 1 ? layerSelect(route.layer) : null;
    var rows = route.stretches.map(function (st, i) {
      var terrain = node('select', { name: 'terrain_' + i, 'aria-label': words.no_terrain });
      terrain.appendChild(node('option', { value: '', text: words.no_terrain }));
      Object.keys(words.terrains || {}).forEach(function (k) {
        var opt = node('option', { value: k, text: words.terrains[k] });
        if (k === st.terrain) opt.selected = true;
        terrain.appendChild(opt);
      });
      var hard = node('input', { type: 'checkbox', name: 'difficult_' + i, value: '1',
        'aria-label': words.difficult, title: words.difficult });
      hard.checked = !!st.difficult;
      return node('li', { 'class': 'map-route-stretch' }, [
        terrain, hard,
        node('span', { 'class': 'muted', text: st.miles === null ? '' : miles(st.miles) })
      ]);
    });
    var save = node('button', { type: 'submit', 'class': 'btn btn-sm btn-primary', text: words.save });
    var form = node('form', { 'class': 'map-card-form' }, [
      name, kind, layerInput, node('ul', { 'class': 'map-route-stretches' }, rows),
      node('span', { 'class': 'map-card-actions' }, [save])
    ]);
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      post(url, Object.fromEntries(new FormData(form).entries())).then(function (r) {
        if (r.ok) location.reload();
      });
    });
    var travel = canTravel && route.miles
      ? node('button', { type: 'button', 'class': 'btn btn-sm btn-primary', text: words.travel }) : null;
    if (travel) travel.addEventListener('click', function () { map.closePopup(); openTravel(route); });
    var points = player || isLocked(route.layer) ? null
      : node('button', { type: 'button', 'class': 'btn btn-sm', text: words.edit_points });
    if (points) points.addEventListener('click', function () { map.closePopup(); editPoints(route); });
    var remove = node('button', { type: 'button', 'class': 'btn btn-sm btn-ghost btn-danger',
      text: words.remove_route });
    remove.addEventListener('click', function () {
      post(url + '/delete').then(function (r) { if (r.ok) location.reload(); });
    });
    var head = (words.route_kinds || {})[route.kind] || '';
    if (route.miles !== null) head += ' · ' + miles(route.miles);
    var terrains = route.stretches.map(function (st) {
      return ((words.terrains || {})[st.terrain] || words.no_terrain) +
        (st.difficult ? ' (' + words.difficult + ')' : '');
    }).filter(function (v, i, all) { return all.indexOf(v) === i; }).join(', ');
    if (player) {
      return node('div', { 'class': 'map-card map-route-card' }, [
        node('span', { 'class': 'eyebrow', text: head }),
        route.name ? node('b', { 'class': 'map-card-title', text: route.name }) : null,
        node('span', { 'class': 'map-card-text', text: terrains })
      ]);
    }
    var more = node('details', { 'class': 'map-route-more' }, [
      node('summary', { text: words.edit }), form, node('p', { 'class': 'map-card-links' }, [remove])
    ]);
    return node('div', { 'class': 'map-card map-route-card' }, [
      node('span', { 'class': 'eyebrow', text: head }),
      route.name ? node('b', { 'class': 'map-card-title', text: route.name }) : null,
      node('span', { 'class': 'map-card-text', text: terrains }),
      node('p', { 'class': 'map-card-links' }, [travel, points]),
      more
    ]);
  }

  function addRoute(route) {
    var group = holder(route.layer);
    var latlngs = route.points.map(function (p) { return toLatLng(p[0], p[1]); });
    var card = L.popup({ minWidth: 260, maxWidth: 320 });
    route.stretches.forEach(function (st, i) {
      var end = i + 1 < route.stretches.length ? route.stretches[i + 1].start : latlngs.length - 1;
      var line = L.polyline(latlngs.slice(st.start, end + 1), {
        pane: paneOf(route.layer),
        className: 'map-route map-route-' + (route.kind || 'country') +
          (st.difficult ? ' map-route-difficult' : ''),
        weight: 5
      }).addTo(group);
      line.on('click', function (e) {
        L.DomEvent.stop(e);
        if (mode) { if (!DRAWING[mode]) tap(e.latlng); return; }
        card.setLatLng(e.latlng).setContent(routeCard(route)).openOn(map);
      });
      if (route.name && i === Math.floor(route.stretches.length / 2)) {
        line.bindTooltip(route.name, { permanent: true, direction: 'center',
          className: 'map-route-name' });
      }
    });
  }
  routes.forEach(addRoute);

  // A road's points to drag, each with a card to split the road there or take it out.
  var pointLayer = L.layerGroup().addTo(map);
  function editPoints(route) {
    pointLayer.clearLayers();
    editing = route;
    var pointsUrl = base + '/routes/' + route.id + '/points/';
    var starts = route.stretches.map(function (st) { return st.start; });
    route.points.forEach(function (p, i) {
      var handle = L.marker(toLatLng(p[0], p[1]), {
        draggable: true,
        icon: L.divIcon({ className: 'map-route-point' + (starts.indexOf(i) >= 0 ? ' is-start' : ''),
          iconSize: [14, 14], iconAnchor: [7, 7] }),
        zIndexOffset: 500
      }).addTo(pointLayer);
      handle.on('dragend', function () {
        var at = toPixels(handle.getLatLng());
        post(pointsUrl + i + '/move', { x: at[0].toFixed(1), y: at[1].toFixed(1) }).then(function (r) {
          if (r.ok) location.reload();
        });
      });
      var actions = [];
      if (i > 0 && i < route.points.length - 1 && starts.indexOf(i) < 0) {
        var split = node('button', { type: 'button', 'class': 'btn btn-sm', text: words.split });
        split.addEventListener('click', function () {
          post(pointsUrl + i + '/split').then(function (r) { if (r.ok) location.reload(); });
        });
        actions.push(split);
      }
      if (route.points.length > 2) {
        var drop = node('button', { type: 'button', 'class': 'btn btn-sm btn-ghost btn-danger',
          text: words.remove_point });
        drop.addEventListener('click', function () {
          post(pointsUrl + i + '/delete').then(function (r) { if (r.ok) location.reload(); });
        });
        actions.push(drop);
      }
      if (actions.length) handle.bindPopup(node('div', { 'class': 'map-card' }, actions));
    });
  }

  // Travel along a road: from where to where, then the road's choices.
  var travelDialog = document.getElementById('tool-travel');
  function openTravel(route) {
    if (!travelDialog) return;
    var form = travelDialog.querySelector('form');
    var from = form.querySelector('[data-travel-from]');
    var to = form.querySelector('[data-travel-to]');
    var way = form.querySelector('[data-travel-way]');
    form.elements.route.value = route.id;
    [from, to].forEach(function (select) { select.innerHTML = ''; });
    var here = party && party.route === route.id ? party : null;
    if (here) from.appendChild(node('option', { value: String(here.at), text: words.party }));
    route.stops.forEach(function (stop) {
      var text = stopName(route, stop) + ' · ' + miles(stop.at);
      from.appendChild(node('option', { value: String(stop.at), text: text }));
      to.appendChild(node('option', { value: String(stop.at), text: text }));
    });
    var last = route.stops[route.stops.length - 1].at;
    var goal = here && here.to !== null && Math.abs(here.to - here.at) > 1e-3 ? here.to
      : (here && Math.abs(here.at - last) < 1e-3 ? 0 : last);
    to.value = String(goal);
    if (!to.value && to.options.length) to.selectedIndex = to.options.length - 1;
    function show() {
      way.textContent = miles(Math.abs(parseFloat(to.value) - parseFloat(from.value)));
    }
    from.onchange = to.onchange = show;
    show();
    if (travelDialog.showModal) travelDialog.showModal(); else travelDialog.setAttribute('open', '');
  }

  if (party) {
    L.marker(toLatLng(party.x, party.y), {
      icon: L.divIcon({ className: 'map-party', html: '<span></span>', iconSize: [22, 22],
        iconAnchor: [11, 11] }),
      title: words.party, keyboard: false, zIndexOffset: 1000, interactive: false
    }).addTo(map);
  }

  // The road tool: tap or drag its points, the terrain chosen as it goes, then done.
  var routeButton = document.querySelector('[data-map-tool="route"]');
  var routeBar = document.querySelector('[data-route-bar]');
  var routeDialog = document.getElementById('tool-route');
  var segTerrains = [], segHard = [];
  function routeEnd(keep) {
    if (routeBar) routeBar.hidden = true;
    var drawnPoints = points.slice();
    setMode('');
    say('');
    if (!keep || drawnPoints.length < 2) { clearDrawing(); return; }
    var form = routeDialog.querySelector('.map-route-form');
    form.elements.points.value = JSON.stringify(drawnPoints.map(function (ll) {
      var at = toPixels(ll);
      return [Math.round(at[0] * 10) / 10, Math.round(at[1] * 10) / 10];
    }));
    form.elements.terrains.value = JSON.stringify(segTerrains);
    form.elements.difficult.value = JSON.stringify(segHard);
    form.elements.layer.value = currentLayer();
    if (routeDialog.showModal) routeDialog.showModal(); else routeDialog.setAttribute('open', '');
  }
  if (routeButton && routeBar) {
    routeButton.addEventListener('click', function () {
      if (mode === 'route') { routeEnd(true); return; }
      clearDrawing();
      segTerrains = []; segHard = [];
      setMode('route');
      routeBar.hidden = false;
      say(words.route_place || '');
    });
    routeBar.querySelector('[data-route-done]').addEventListener('click', function () { routeEnd(true); });
    routeBar.querySelector('[data-route-cancel]').addEventListener('click', function () { routeEnd(false); });
  }
  function routePoint(latlng) {
    addPoint(latlng);
    if (points.length > 1) {
      segTerrains.push(routeBar.querySelector('[data-route-terrain]').value);
      segHard.push(routeBar.querySelector('[data-route-difficult]').checked);
    }
    if (feetPerPx && points.length > 1) say(fill(words.total, { distance: distance(pathFeet(points)) }));
  }

  // -- the layer panel ----------------------------------------------------------------

  var currentKey = 'paravent-map-layer-' + mapId;
  function currentLayer() {
    var id = '';
    try { id = localStorage.getItem(currentKey) || ''; } catch (err) { id = ''; }
    return byId[id] && !byId[id].locked ? id : '';
  }
  var panel = document.getElementById('layers');
  if (panel) {
    var chosen = currentLayer();
    if (chosen) {
      var radio = panel.querySelector('input[name="current-layer"][value="' + chosen + '"]');
      if (radio) radio.checked = true;
    }
    panel.addEventListener('change', function (e) {
      if (e.target.name !== 'current-layer') return;
      try { localStorage.setItem(currentKey, e.target.value); } catch (err) { /* private */ }
    });
    panel.addEventListener('click', function (e) {
      var eye = e.target.closest ? e.target.closest('[data-layer-eye]') : null;
      var lock = e.target.closest ? e.target.closest('[data-layer-lock]') : null;
      var here = e.target.closest ? e.target.closest('[data-layer-zoom-here]') : null;
      if (eye) {
        var layer = eye.dataset.layerEye === 'grid' ? grid : byId[eye.dataset.layerEye];
        layer.shown = !layer.shown;
        eye.setAttribute('aria-pressed', layer.shown ? 'true' : 'false');
        eye.closest('.map-layer').classList.toggle('is-hidden', !layer.shown);
        post(base + '/layers/' + eye.dataset.layerEye + '/shown', { shown: layer.shown ? '1' : '0' });
        if (layer === grid) showGrid();
        showLayers();
        scaleLabels();
      } else if (lock) {
        var locked = byId[lock.dataset.layerLock];
        locked.locked = !locked.locked;
        lock.setAttribute('aria-pressed', locked.locked ? 'true' : 'false');
        var row = lock.closest('.map-layer');
        var current = row.querySelector('input[name="current-layer"]');
        if (current) { current.disabled = locked.locked; if (locked.locked) current.checked = false; }
        post(base + '/layers/' + locked.id + '/lock', { locked: locked.locked ? '1' : '' });
        groups[locked.id].eachLayer(function (m) {
          if (!m.dragging) return;
          if (locked.locked) m.dragging.disable(); else m.dragging.enable();
        });
      } else if (here) {
        here.form.elements.zoom.value = map.getZoom().toFixed(2);
      }
    });
  }

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
    form.elements.layer.value = currentLayer();
    if (pinDialog.showModal) pinDialog.showModal(); else pinDialog.setAttribute('open', '');
    var filter = form.querySelector('[data-pin-filter]');
    var chosen = pinFor && form.querySelector('input[name="choice"][value="' + CSS.escape(pinFor) + '"]');
    if (chosen) {
      // Opened from its page: it is chosen, the list shows it alone, one press pins it.
      chosen.checked = true;
      if (filter) {
        filter.value = el.dataset.pinForName || '';
        filter.dispatchEvent(new Event('input'));
      }
      var submit = form.querySelector('button[type="submit"]');
      if (submit) submit.focus();
      pinFor = '';
    } else if (filter) filter.focus();
  }
  // Opened to pin something (?pin_for=): the pin tool is on at once.
  var pinFor = el.dataset.pinFor || '';
  if (pinFor && pinButton) {
    clearDrawing();
    setMode('pin');
    say(fill(words.pin_for, { name: el.dataset.pinForName || '' }));
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

  // The name tool: a tap, then the dialog, a size filled in that reads well now.
  var labelButton = document.querySelector('[data-map-tool="label"]');
  var labelDialog = document.getElementById('tool-label');
  if (labelButton) {
    labelButton.addEventListener('click', function () {
      if (mode === 'label') { setMode(''); say(''); return; }
      clearDrawing();
      setMode('label');
      say(words.label_place || '');
    });
  }
  function placeLabel(latlng) {
    var form = labelDialog && labelDialog.querySelector('.map-label-form');
    if (!form) return;
    var at = toPixels(latlng);
    form.elements.x.value = at[0].toFixed(1);
    form.elements.y.value = at[1].toFixed(1);
    form.elements.layer.value = currentLayer();
    var k = Math.pow(2, map.getZoom() - Z);
    var scale = STYLE_SCALE[form.elements.style.value] || 1;
    form.elements.size.value = String(Math.max(1, Math.round(16 * scale / k)));
    if (labelDialog.showModal) labelDialog.showModal(); else labelDialog.setAttribute('open', '');
    form.elements.text.focus();
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
    } else if (mode === 'label') {
      setMode('');
      say('');
      placeLabel(latlng);
    } else if (mode === 'route') {
      routePoint(latlng);
    } else if (mode === 'grid') {
      setMode('');
      say('');
      gridPlaced(latlng);
    }
  }
  map.on('click', function (e) { if (!DRAWING[mode]) tap(e.latlng); });

  function follow(latlng) {
    if (!mode || !points.length) return;
    var last = points[points.length - 1];
    if (!trail) {
      trail = L.polyline([last, latlng], { className: 'map-ruler-trail', interactive: false })
        .addTo(layer);
    } else {
      trail.setLatLngs([last, latlng]);
    }
    if (mode === 'ruler') showRuler(latlng);
  }
  map.on('mousemove', function (e) { if (!pressed) follow(e.latlng); });

  // Drawing with the pointer: a press and a release where it was is a tap; a drag
  // draws from where it began (or from the last point of the ruler) to where it ends.
  var pressed = null;
  function at(e) { return map.mouseEventToLatLng(e); }
  el.addEventListener('pointerdown', function (e) {
    if (!DRAWING[mode] || e.button > 0 || !e.isPrimary) return;
    if (e.target.closest && e.target.closest('.leaflet-control')) return;
    pressed = { x: e.clientX, y: e.clientY, latlng: at(e), moved: false };
    if (el.setPointerCapture) el.setPointerCapture(e.pointerId);
    e.stopPropagation();  // a road, pin or name beneath is drawn over, not opened
  }, true);
  el.addEventListener('pointermove', function (e) {
    if (!pressed) return;
    if (Math.hypot(e.clientX - pressed.x, e.clientY - pressed.y) > 6 && !pressed.moved) {
      pressed.moved = true;
      // The drag's start is the first point, unless the ruler already has one.
      var chained = mode === 'ruler' || mode === 'route';
      if (!points.length || !chained) { if (!chained) clearDrawing(); tap(pressed.latlng); }
    }
    if (pressed.moved) follow(at(e));
  }, true);
  function release(e) {
    if (!pressed) return;
    var was = pressed;
    pressed = null;
    if (e.type === 'pointercancel') return;
    if (was.moved && points.length) tap(at(e));
    else if (!was.moved) tap(was.latlng);
  }
  // Listened for before anything on the map can stop them: drawing works over a road,
  // a pin or a name as well as over bare map.
  el.addEventListener('pointerup', release, true);
  el.addEventListener('pointercancel', release, true);

  map.on('dblclick', function () {
    if (mode === 'ruler') endRuler();
    else if (mode === 'route') routeEnd(true);
  });

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || !mode) return;
    if (mode === 'ruler') endRuler();
    else if (mode === 'route') routeEnd(false);
    else { setMode(''); clearDrawing(); say(''); }
  });
})();
