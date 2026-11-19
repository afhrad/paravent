// The image dialog (app/ui/image_pages.py): pick a picture the world already has, or drop
// or choose one, then frame it in a chosen shape and give it effects. The frame is sent
// as a crop word — the part of the original shown, as fractions of its width and height
// — so the picture itself is never cut and the frame can be moved again later.
//
// Two modes: a form (covers, title images) submits path, crop and effects; "insert" (a
// Markdown editor's Image button) writes ![[path|crop:… effects]] at the cursor, or
// replaces the picture the cursor stands on, uploading a new file first.
(() => {
  "use strict";

  const RATIOS = { "10:3": 10 / 3, "16:9": 16 / 9, "3:2": 3 / 2, "4:3": 4 / 3, "1:1": 1, "3:4": 3 / 4 };
  const MIN = 0.04; // the smallest frame, as a share of a side
  // A picture in Markdown: Obsidian's ![[path|…]] or ![alt|…](path).
  const PICTURE = /!\[\[([^\]|]+)((?:\|[^\]]*)?)\]\]|!\[([^\]]*)\]\(([^)\s]+)\)/g;

  function clamp(v, lo, hi) {
    return Math.min(hi, Math.max(lo, v));
  }

  function init(root) {
    const dlg = root.querySelector(".img-dialog");
    if (!dlg) return;
    const form = root.querySelector(".img-form");
    const file = root.querySelector('input[type="file"]');
    const pathField = root.querySelector(".img-path");
    const cropField = root.querySelector(".img-crop");
    const drop = root.querySelector(".img-drop");
    const slot = dlg.querySelector(".img-browser-slot");
    const step = dlg.querySelector(".img-frame-step");
    const stage = dlg.querySelector(".img-stage");
    const pic = dlg.querySelector(".img-src");
    const box = dlg.querySelector(".img-frame");
    const save = dlg.querySelector(".img-save");
    const back = dlg.querySelector(".img-back");
    const chips = Array.from(dlg.querySelectorAll(".img-aspects [data-aspect]"));
    const insert = root.dataset.mode === "insert";
    const f = { x: 0, y: 0, w: 1, h: 1 };
    let ratio = RATIOS[root.dataset.aspect] || null; // null: free
    let objectUrl = null;
    let chosen = { path: pathField ? pathField.value : "", file: null };
    let target = null; // insert mode: { area, from, to } — where the markup goes

    if (form) form.classList.add("is-live");

    function size() {
      return { W: pic.naturalWidth || 1, H: pic.naturalHeight || 1 };
    }

    function mark(name) {
      chips.forEach((c) => c.setAttribute("aria-pressed", c.dataset.aspect === name ? "true" : "false"));
    }

    // The largest frame of the shape, centred on where the frame was.
    function fit() {
      if (!ratio) return;
      const { W, H } = size();
      const cx = f.x + f.w / 2;
      const cy = f.y + f.h / 2;
      let w = 1;
      let h = W / ratio / H;
      if (h > 1) {
        h = 1;
        w = (H * ratio) / W;
      }
      f.w = w;
      f.h = h;
      f.x = clamp(cx - w / 2, 0, 1 - w);
      f.y = clamp(cy - h / 2, 0, 1 - h);
    }

    function draw() {
      box.style.left = `${f.x * 100}%`;
      box.style.top = `${f.y * 100}%`;
      box.style.width = `${f.w * 100}%`;
      box.style.height = `${f.h * 100}%`;
    }

    const uploadZone = dlg.querySelector(".img-upload");

    function show(which) {
      slot.hidden = which !== "browse";
      if (uploadZone) uploadZone.hidden = which !== "browse";
      step.hidden = which !== "frame";
      save.hidden = which !== "frame";
      back.hidden = which !== "frame";
      if (!dlg.open) dlg.showModal();
    }

    // The shape that matches a frame, else free.
    function shapeOf() {
      const { W, H } = size();
      const actual = (f.w * W) / (f.h * H);
      const found = Object.keys(RATIOS).find((k) => Math.abs(RATIOS[k] - actual) / actual < 0.02);
      return found || "free";
    }

    // The frame step with a picture: a new one framed in the default shape, or one
    // already set where its frame sits.
    function frame(src, start) {
      pic.onload = () => {
        if (start) {
          Object.assign(f, start);
          const name = shapeOf();
          ratio = RATIOS[name] || null;
          mark(name);
        } else {
          Object.assign(f, { x: 0, y: 0, w: 1, h: 1 });
          ratio = RATIOS[root.dataset.aspect] || null;
          mark(root.dataset.aspect || "free");
          fit();
        }
        draw();
      };
      pic.src = src;
      show("frame");
    }

    // The tags field. The world's tags are offered as chips beneath a tags field, those
    // matching the word being typed and not yet in it; a tap adds one, so "harbour" is not
    // written again as "harbor". The browser's own suggestions cannot help here: a field
    // of several tags, comma-separated, is never one of them.
    const tagsField = dlg.querySelector(".img-tags");
    let vocabulary = [];

    function fill(html) {
      slot.innerHTML = html;
      vocabulary = Array.from(slot.querySelectorAll(".img-tagchip")).map((chip) => chip.dataset.tag);
    }

    function suggestions(field) {
      let row = field.nextElementSibling;
      if (!row || !row.classList.contains("img-tag-suggest")) {
        row = document.createElement("div");
        row.className = "chips img-tag-suggest";
        field.insertAdjacentElement("afterend", row);
      }
      return row;
    }

    function suggest(field) {
      const row = suggestions(field);
      const parts = field.value.split(",");
      const typing = parts[parts.length - 1].trim().toLowerCase();
      const have = parts.slice(0, -1).map((p) => p.trim().toLowerCase());
      // Those starting with what is typed first, then those containing it.
      const offered = vocabulary
        .filter((tag) => !have.includes(tag) && tag !== typing && tag.includes(typing))
        .sort((a, b) => Number(!a.startsWith(typing)) - Number(!b.startsWith(typing)))
        .slice(0, 12);
      row.replaceChildren(
        ...offered.map((tag) => {
          const chip = document.createElement("button");
          chip.type = "button";
          chip.className = "chip img-tag-offer";
          chip.textContent = tag;
          chip.addEventListener("click", () => {
            const kept = field.value.split(",").slice(0, -1).map((p) => p.trim()).filter(Boolean);
            field.value = [...kept, tag].join(", ") + ", ";
            field.focus();
            suggest(field);
          });
          return chip;
        }),
      );
      row.hidden = !offered.length;
    }

    // A tags field: the world's tags loaded (once) as soon as it is used, then offered.
    function watchTags(field) {
      if (!field || field.dataset.suggesting) return;
      field.dataset.suggesting = "1";
      field.setAttribute("autocomplete", "off");
      const offer = () => load().then(() => suggest(field));
      field.addEventListener("focus", offer);
      field.addEventListener("input", () => suggest(field));
    }
    watchTags(tagsField);

    // The world's pictures, fetched once (again after tagging).
    let loading = null;
    function load() {
      if (!loading) {
        loading = fetch(root.dataset.browse, { credentials: "same-origin" })
          .then((r) => (r.ok ? r.text() : ""))
          .then(fill);
      }
      return loading;
    }

    function browse() {
      show("browse");
      load();
    }

    // The tags a picture of the world carries, to edit in the frame step.
    function tagsOf(path) {
      const pick = Array.from(slot.querySelectorAll(".img-pick")).find((p) => p.dataset.path === path);
      return pick ? pick.dataset.tags || "" : "";
    }

    function parseFrame(text) {
      const values = (text || "").split(/[,_]/).map(parseFloat);
      return values.length === 4 && values.every((v) => !Number.isNaN(v))
        ? { x: values[0], y: values[1], w: values[2], h: values[3] }
        : null;
    }

    function openFile(picked) {
      if (!picked || !picked.type.startsWith("image/")) return;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = URL.createObjectURL(picked);
      chosen = { path: "", file: picked };
      if (pathField) pathField.value = "";
      if (tagsField) tagsField.value = "";
      frame(objectUrl, null);
    }

    // Filtering: every word typed in the name, tags or shape, and every tag chip pressed.
    function filter() {
      const box_ = slot.querySelector(".img-filter");
      const typed = (box_ ? box_.value : "").trim().toLowerCase().split(/\s+/).filter(Boolean);
      const pressed = Array.from(slot.querySelectorAll('.img-tagchip[aria-pressed="true"]')).map(
        (c) => c.dataset.tag,
      );
      slot.querySelectorAll(".img-pick").forEach((p) => {
        const said = p.dataset.words || "";
        const own = (p.dataset.tags || "").split(", ");
        p.hidden = !typed.every((w) => said.includes(w)) || !pressed.every((t) => own.includes(t));
      });
      slot.querySelectorAll(".img-group").forEach((g) => {
        g.hidden = !g.querySelector(".img-pick:not([hidden])");
      });
    }

    // Select mode: picks are ticked to tag or untag together, not picked.
    function selecting() {
      const toggle = slot.querySelector(".img-select");
      return toggle && toggle.getAttribute("aria-pressed") === "true";
    }
    function counted() {
      const chosen_ = Array.from(slot.querySelectorAll(".img-pick.is-chosen"));
      const n = chosen_.length;
      const out = slot.querySelector(".img-bulk-n");
      if (out) out.textContent = n ? `${n} ✓` : "";
      one(n === 1 ? chosen_[0] : null);
    }

    // One picture ticked: where it is used, a new name for it, and delete while unused.
    function one(pick) {
      const panel = slot.querySelector(".img-one");
      if (!panel) return;
      panel.hidden = !pick;
      if (!pick) return;
      let uses = [];
      try {
        uses = JSON.parse(pick.dataset.uses || "[]");
      } catch (err) {
        uses = [];
      }
      panel.querySelector(".img-one-name").textContent = pick.title;
      const list = panel.querySelector(".img-uses");
      list.replaceChildren(
        ...uses.map((u) => {
          const li = document.createElement("li");
          const a = document.createElement("a");
          a.href = u.url;
          a.textContent = u.label;
          li.appendChild(a);
          return li;
        }),
      );
      panel.querySelector(".img-unused").hidden = uses.length > 0;
      panel.querySelector(".img-in-use").hidden = uses.length === 0;
      panel.querySelector(".img-delete").disabled = uses.length > 0;
      const field = panel.querySelector(".img-rename-name");
      field.value = pick.title.replace(/\.[^.]+$/, "");
    }

    function manage(op) {
      const browser = slot.querySelector(".img-browser");
      const pick = slot.querySelector(".img-pick.is-chosen");
      if (!browser || !pick) return;
      const url = op === "rename" ? browser.dataset.renameUrl : browser.dataset.deleteUrl;
      if (!url) return;
      const body = new FormData();
      body.append("path", pick.dataset.path);
      if (op === "rename") {
        const name = slot.querySelector(".img-rename-name").value.trim();
        if (!name) return;
        body.append("name", name);
      } else {
        const ask = slot.querySelector(".img-delete").dataset.confirm || "";
        if (ask && !window.confirm(ask.replace("{name}", pick.title))) return;
      }
      fetch(url, { method: "POST", body, credentials: "same-origin" })
        .then((r) => (r.ok ? r.text() : null))
        .then((html) => {
          if (html !== null) {
            fill(html);
            loading = Promise.resolve();
          }
        });
    }
    function bulk(op) {
      const browser = slot.querySelector(".img-browser");
      const field = slot.querySelector(".img-bulk-tags");
      const picked = Array.from(slot.querySelectorAll(".img-pick.is-chosen"));
      if (!browser || !field || !field.value.trim() || !picked.length) return;
      const body = new FormData();
      picked.forEach((p) => body.append("path", p.dataset.path));
      body.append(op, field.value);
      fetch(browser.dataset.tagsUrl, { method: "POST", body, credentials: "same-origin" })
        .then((r) => (r.ok ? r.text() : null))
        .then((html) => {
          if (html !== null) {
            fill(html);
            loading = Promise.resolve();
          }
        });
    }

    slot.addEventListener("click", (event) => {
      const chip = event.target.closest(".img-tagchip");
      if (chip) {
        chip.setAttribute("aria-pressed", chip.getAttribute("aria-pressed") === "true" ? "false" : "true");
        filter();
        return;
      }
      const toggle = event.target.closest(".img-select");
      if (toggle) {
        const on = toggle.getAttribute("aria-pressed") !== "true";
        toggle.setAttribute("aria-pressed", on ? "true" : "false");
        slot.querySelector(".img-bulk").hidden = !on;
        if (!on) slot.querySelectorAll(".img-pick.is-chosen").forEach((p) => p.classList.remove("is-chosen"));
        counted();
        return;
      }
      if (event.target.closest(".img-rename")) return manage("rename");
      if (event.target.closest(".img-delete")) return manage("delete");
      if (event.target.closest(".img-bulk-add")) return bulk("add");
      if (event.target.closest(".img-bulk-remove")) return bulk("remove");
      const pick = event.target.closest(".img-pick");
      if (!pick) return;
      if (selecting()) {
        pick.classList.toggle("is-chosen");
        counted();
        return;
      }
      // Picking a picture of the world.
      chosen = { path: pick.dataset.path, file: null };
      if (pathField) pathField.value = pick.dataset.path;
      if (file) file.value = "";
      if (tagsField) tagsField.value = pick.dataset.tags || "";
      frame(pick.dataset.src, null);
    });
    slot.addEventListener("input", (event) => {
      if (event.target.matches(".img-filter")) filter();
    });
    // The selection's tags field comes with the browser, drawn anew after each change.
    slot.addEventListener("focusin", (event) => {
      if (event.target.matches(".img-bulk-tags")) {
        watchTags(event.target);
        suggest(event.target);
      }
    });

    if (file) file.addEventListener("change", () => openFile(file.files && file.files[0]));
    if (drop) {
      ["dragenter", "dragover"].forEach((type) =>
        drop.addEventListener(type, (event) => {
          event.preventDefault();
          drop.classList.add("is-over");
        }),
      );
      ["dragleave", "drop"].forEach((type) =>
        drop.addEventListener(type, () => drop.classList.remove("is-over")),
      );
      drop.addEventListener("drop", (event) => {
        event.preventDefault();
        const files = event.dataTransfer && event.dataTransfer.files;
        if (!files || !files.length) return;
        if (file) file.files = files;
        openFile(files[0]);
      });
    }

    const browseButton = root.querySelector(".img-browse");
    if (browseButton) browseButton.addEventListener("click", browse);
    back.addEventListener("click", browse);
    const adjust = root.querySelector(".img-adjust");
    if (adjust && root.dataset.src) {
      adjust.addEventListener("click", () => {
        chosen = { path: pathField ? pathField.value : "", file: null };
        frame(root.dataset.src, parseFrame(root.dataset.frame) || { x: 0, y: 0, w: 1, h: 1 });
        load().then(() => {
          if (tagsField) tagsField.value = tagsOf(chosen.path);
        });
      });
    }

    chips.forEach((chip) =>
      chip.addEventListener("click", () => {
        ratio = RATIOS[chip.dataset.aspect] || null;
        mark(chip.dataset.aspect);
        fit();
        draw();
      }),
    );

    // Dragging inside the frame moves it; a corner resizes it from the opposite one,
    // keeping its shape unless it is free.
    let drag = null;
    stage.addEventListener("pointerdown", (event) => {
      const handle = event.target.closest(".img-handle");
      if (!handle && !event.target.closest(".img-frame")) return;
      event.preventDefault();
      const rect = stage.getBoundingClientRect();
      drag = { corner: handle ? handle.dataset.corner : "", rect, last: event, start: { ...f } };
      stage.setPointerCapture(event.pointerId);
    });
    stage.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const { rect } = drag;
      const px = clamp((event.clientX - rect.left) / rect.width, 0, 1);
      const py = clamp((event.clientY - rect.top) / rect.height, 0, 1);
      if (!drag.corner) {
        f.x = clamp(f.x + (event.clientX - drag.last.clientX) / rect.width, 0, 1 - f.w);
        f.y = clamp(f.y + (event.clientY - drag.last.clientY) / rect.height, 0, 1 - f.h);
        drag.last = event;
        draw();
        return;
      }
      const s = drag.start;
      const ax = drag.corner.includes("w") ? s.x + s.w : s.x;
      const ay = drag.corner.includes("n") ? s.y + s.h : s.y;
      let w = Math.max(MIN, Math.abs(px - ax));
      let h = Math.max(MIN, Math.abs(py - ay));
      if (ratio) {
        const { W, H } = size();
        h = (w * W) / ratio / H;
        const roomY = drag.corner.includes("n") ? ay : 1 - ay;
        const roomX = drag.corner.includes("w") ? ax : 1 - ax;
        if (h > roomY) {
          h = roomY;
          w = (h * H * ratio) / W;
        }
        w = Math.min(w, roomX);
        h = (w * W) / ratio / H;
      }
      f.w = w;
      f.h = h;
      f.x = clamp(drag.corner.includes("w") ? ax - w : ax, 0, 1 - w);
      f.y = clamp(drag.corner.includes("n") ? ay - h : ay, 0, 1 - h);
      draw();
    });
    ["pointerup", "pointercancel"].forEach((type) =>
      stage.addEventListener(type, () => {
        drag = null;
      }),
    );

    function cropWord() {
      const whole = f.x < 0.001 && f.y < 0.001 && f.w > 0.999 && f.h > 0.999;
      const v = (n) => n.toFixed(4);
      return whole ? "" : `crop:${v(f.x)}_${v(f.y)}_${v(f.w)}_${v(f.h)}`;
    }

    function effects() {
      return Array.from(dlg.querySelectorAll('input[name="effects"]:checked')).map((e) => e.value);
    }

    // Insert mode: the picture's Markdown, written where the cursor was.
    function write(path) {
      const words = [cropWord(), ...effects()].filter(Boolean).join(" ");
      const text = `![[${path}${words ? `|${words}` : ""}]]`;
      if (target && window.ParaventText) {
        window.ParaventText.replace(target.area, target.from, target.to, text);
        target.area.focus();
      }
      // The tags typed, kept for the picture (the browser shows them next time).
      if (tagsField) {
        const body = new FormData();
        body.append("path", path);
        body.append("tags", tagsField.value);
        fetch(`${root.dataset.browse}/tags`, { method: "POST", body, credentials: "same-origin" });
        loading = null;
      }
    }

    save.addEventListener("click", () => {
      const word = cropWord();
      dlg.close();
      if (!insert) {
        if (cropField) cropField.value = word;
        form.requestSubmit();
        return;
      }
      if (!chosen.file) {
        write(chosen.path);
        return;
      }
      const body = new FormData();
      body.append("file", chosen.file, chosen.file.name || "image.png");
      fetch(root.dataset.upload, { method: "POST", body, credentials: "same-origin" })
        .then((r) => {
          const stored = r.headers.get("X-Media-Path");
          return r.ok && stored ? stored : r.text().then((why) => Promise.reject(why));
        })
        .then(write)
        .catch((why) => window.alert(why || "offline"));
    });
    dlg.querySelector(".img-cancel").addEventListener("click", () => {
      dlg.close();
      if (file) file.value = "";
      if (pathField && root.dataset.src === undefined) pathField.value = "";
    });

    // Insert mode: opened by the editor's Image button, on the picture the cursor
    // stands on (to reframe it) or with nothing yet (to pick or upload one).
    root.addEventListener("paravent-image-open", (event) => {
      const area = event.detail.area;
      const at = area.selectionStart;
      target = { area, from: at, to: area.selectionEnd };
      PICTURE.lastIndex = 0;
      let found;
      while ((found = PICTURE.exec(area.value))) {
        if (found.index <= at && at <= found.index + found[0].length) break;
      }
      if (found && found.index <= at) {
        const path = found[1] || found[4];
        const words = (found[2] || "").replace(/^\|/, "") || (found[3] || "").split("|").pop();
        const crop = (words.match(/crop:([\d._]+)/) || [])[1];
        target = { area, from: found.index, to: found.index + found[0].length };
        chosen = { path, file: null };
        dlg.querySelectorAll('input[name="effects"]').forEach((box_) => {
          box_.checked = new RegExp(`(^|\\s)${box_.value}(\\s|$)`).test(words || "");
        });
        frame(root.dataset.media + path, parseFrame(crop) || { x: 0, y: 0, w: 1, h: 1 });
        if (tagsField) tagsField.value = "";
        load().then(() => {
          if (tagsField) tagsField.value = tagsOf(path);
        });
        return;
      }
      dlg.querySelectorAll('input[name="effects"]').forEach((box_) => {
        box_.checked = false;
      });
      if (tagsField) tagsField.value = "";
      browse();
    });
  }

  function start() {
    document.querySelectorAll("[data-image-editor]").forEach((root) => {
      if (root.dataset.ready) return;
      root.dataset.ready = "1";
      init(root);
    });
  }
  document.addEventListener("DOMContentLoaded", start);
  document.addEventListener("htmx:load", start);
  start();
})();
