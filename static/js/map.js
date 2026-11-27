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

  // The tools that draw: while one is in hand, a drag draws instead of moving the map,
  // and a tap still sets a point. The arrow keys and the zoom still move the map.
  var DRAWING = { ruler: true, line: true, frame: true };

  function setMode(next) {
    mode = next;
    el.classList.toggle('map-drawing', !!mode);
    if (mode) map.doubleClickZoom.disable(); else map.doubleClickZoom.enable();
    if (DRAWING[mode]) map.dragging.disable(); else map.dragging.enable();
    if (rulerButton) rulerButton.setAttribute('aria-pressed', mode === 'ruler' ? 'true' : 'false');
    ['pin', 'label'].forEach(function (tool) {
      var b = document.querySelector('[data-map-tool="' + tool + '"]');
      if (b) b.setAttribute('aria-pressed', mode === tool ? 'true' : 'false');
    });
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
    var layerInput = layers.length > 1 ? layerSelect(pin.layer) : null;
    var form = node('form', { 'class': 'map-card-form' }, [
      labelInput, noteInput, layerInput,
      node('span', { 'class': 'map-card-actions' }, [save, remove])
    ]);
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var fields = { label: labelInput.value, note: noteInput ? noteInput.value : '' };
      if (layerInput) fields.layer = layerInput.value;
      post(base, fields).then(function (r) {
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
      post(base + '/delete').then(function (r) {
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
      icon: pinIcon(pin), draggable: !isLocked(pin.layer), keyboard: true, title: shownName(pin),
      riseOnHover: true, pane: paneOf(pin.layer)
    });
    marker.options.layerId = groupOf(pin.layer);
    marker.bindPopup(function () { return pinCard(pin, marker); }, { minWidth: 220, maxWidth: 300 });
    passTaps(marker);
    marker.on('dragend', function () {
      var at = toPixels(marker.getLatLng());
      pin.x = at[0]; pin.y = at[1];
      post('/gm/maps/' + mapId + '/pins/' + pin.id + '/move', { x: at[0].toFixed(1), y: at[1].toFixed(1) });
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
    var base = '/gm/maps/' + mapId + '/labels/' + label.id;
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
      post(base, fields).then(function (r) {
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
      post(base + '/delete').then(function (r) { if (r.ok) holder(label.layer).removeLayer(marker); });
    });
    return node('div', { 'class': 'map-card' }, [
      node('span', { 'class': 'eyebrow', text: (words.styles || {})[label.style] || '' }), form
    ]);
  }

  function addLabel(label) {
    var marker = L.marker(toLatLng(label.x, label.y), {
      icon: labelIcon(label), draggable: !isLocked(label.layer), keyboard: true,
      title: label.text, pane: paneOf(label.layer)
    });
    marker.options.layerId = groupOf(label.layer);
    marker.bindPopup(function () { return labelCard(label, marker); }, { minWidth: 240 });
    passTaps(marker);
    marker.on('dragend', function () {
      var at = toPixels(marker.getLatLng());
      label.x = at[0]; label.y = at[1];
      post('/gm/maps/' + mapId + '/labels/' + label.id + '/move',
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
        var layer = byId[eye.dataset.layerEye];
        layer.shown = !layer.shown;
        eye.setAttribute('aria-pressed', layer.shown ? 'true' : 'false');
        eye.closest('.map-layer').classList.toggle('is-hidden', !layer.shown);
        post('/gm/maps/' + mapId + '/layers/' + layer.id + '/shown', { shown: layer.shown ? '1' : '0' });
        showLayers();
        scaleLabels();
      } else if (lock) {
        var locked = byId[lock.dataset.layerLock];
        locked.locked = !locked.locked;
        lock.setAttribute('aria-pressed', locked.locked ? 'true' : 'false');
        var row = lock.closest('.map-layer');
        var current = row.querySelector('input[name="current-layer"]');
        if (current) { current.disabled = locked.locked; if (locked.locked) current.checked = false; }
        post('/gm/maps/' + mapId + '/layers/' + locked.id + '/lock', { locked: locked.locked ? '1' : '' });
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
  });
  el.addEventListener('pointermove', function (e) {
    if (!pressed) return;
    if (Math.hypot(e.clientX - pressed.x, e.clientY - pressed.y) > 6 && !pressed.moved) {
      pressed.moved = true;
      // The drag's start is the first point, unless the ruler already has one.
      if (!points.length || mode !== 'ruler') { if (mode !== 'ruler') clearDrawing(); tap(pressed.latlng); }
    }
    if (pressed.moved) follow(at(e));
  });
  function release(e) {
    if (!pressed) return;
    var was = pressed;
    pressed = null;
    if (e.type === 'pointercancel') return;
    if (was.moved && points.length) tap(at(e));
    else if (!was.moved) tap(was.latlng);
  }
  el.addEventListener('pointerup', release);
  el.addEventListener('pointercancel', release);

  map.on('dblclick', function () { if (mode === 'ruler') endRuler(); });

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape' || !mode) return;
    if (mode === 'ruler') endRuler();
    else { setMode(''); clearDrawing(); say(''); }
  });
})();
