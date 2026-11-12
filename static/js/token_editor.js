// The token upload dialog (documentation/TOKENS.md §3): drop or choose a picture, drag it
// to centre it in the frame, zoom it, save. The form sends the picture with where it sits
// (x, y: the point of the picture at the frame's centre, 0–1; zoom: 1 fills the opening);
// the server places it the same way. Without this script the form still uploads,
// centred.
(() => {
  "use strict";

  function init(root) {
    const form = root.querySelector("form.tok-upload");
    const input = form.querySelector('input[type="file"]');
    const drop = root.querySelector(".tok-drop");
    const dialog = root.querySelector("dialog.tok-dialog");
    if (!form || !input || !dialog) return;
    const stage = dialog.querySelector(".tok-stage");
    const pic = stage.querySelector(".tok-src");
    const range = dialog.querySelector(".tok-zoom");
    const inner = parseFloat(root.dataset.inner || "0.82");
    const field = (name) => form.querySelector(`input[name="${name}"]`);
    const state = { x: 0.5, y: 0.5, zoom: 1, w: 1, h: 1 };
    let url = null;

    function size() {
      const s = stage.clientWidth;
      const opening = s * inner;
      const scale = (opening / Math.min(state.w, state.h)) * state.zoom;
      return { s, opening, width: state.w * scale, height: state.h * scale };
    }

    // The picture always covers the opening: its centre point stays where it can.
    function clamp() {
      const { opening, width, height } = size();
      const halfX = opening / 2 / width;
      const halfY = opening / 2 / height;
      state.x = Math.min(1 - halfX, Math.max(halfX, state.x));
      state.y = Math.min(1 - halfY, Math.max(halfY, state.y));
    }

    function layout() {
      clamp();
      const { s, width, height } = size();
      pic.style.width = `${width}px`;
      pic.style.height = `${height}px`;
      pic.style.left = `${s / 2 - state.x * width}px`;
      pic.style.top = `${s / 2 - state.y * height}px`;
    }

    // Show a picture under the frame: a chosen file (centred), or the element's own
    // picture where it last sat (Adjust).
    function show(src, start) {
      pic.onload = () => {
        state.w = pic.naturalWidth || 1;
        state.h = pic.naturalHeight || 1;
        Object.assign(state, start);
        range.value = String(state.zoom);
        if (!dialog.open) dialog.showModal();
        layout();
      };
      pic.src = src;
    }

    function open(file) {
      if (!file || !file.type.startsWith("image/")) return;
      if (url) URL.revokeObjectURL(url);
      url = URL.createObjectURL(file);
      show(url, { x: 0.5, y: 0.5, zoom: 1 });
    }

    const adjust = root.querySelector(".tok-adjust");
    if (adjust && root.dataset.picture) {
      adjust.addEventListener("click", () => {
        input.value = "";
        show(root.dataset.picture, {
          x: parseFloat(root.dataset.x || "0.5"),
          y: parseFloat(root.dataset.y || "0.5"),
          zoom: parseFloat(root.dataset.zoom || "1"),
        });
      });
    }

    input.addEventListener("change", () => open(input.files && input.files[0]));
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
        input.files = files;
        open(files[0]);
      });
    }

    // Dragging moves the picture under the frame.
    let last = null;
    stage.addEventListener("pointerdown", (event) => {
      last = { x: event.clientX, y: event.clientY };
      stage.setPointerCapture(event.pointerId);
    });
    stage.addEventListener("pointermove", (event) => {
      if (!last) return;
      const { width, height } = size();
      state.x -= (event.clientX - last.x) / width;
      state.y -= (event.clientY - last.y) / height;
      last = { x: event.clientX, y: event.clientY };
      layout();
    });
    ["pointerup", "pointercancel"].forEach((type) =>
      stage.addEventListener(type, () => {
        last = null;
      }),
    );
    function zoomTo(value) {
      state.zoom = Math.min(8, Math.max(1, value));
      range.value = String(state.zoom);
      layout();
    }
    stage.addEventListener(
      "wheel",
      (event) => {
        event.preventDefault();
        zoomTo(state.zoom * Math.exp(-event.deltaY * 0.0015));
      },
      { passive: false },
    );
    range.addEventListener("input", () => zoomTo(parseFloat(range.value)));

    dialog.querySelector(".tok-save").addEventListener("click", () => {
      field("x").value = state.x.toFixed(4);
      field("y").value = state.y.toFixed(4);
      field("zoom").value = state.zoom.toFixed(3);
      dialog.close();
      form.requestSubmit();
    });
    dialog.querySelector(".tok-cancel").addEventListener("click", () => {
      dialog.close();
      input.value = "";
    });
    window.addEventListener("resize", () => dialog.open && layout());
  }

  function start() {
    document.querySelectorAll("[data-token-editor]").forEach((root) => {
      if (root.dataset.ready) return;
      root.dataset.ready = "1";
      init(root);
    });
  }
  document.addEventListener("DOMContentLoaded", start);
  document.addEventListener("htmx:load", start);
  start();
})();
