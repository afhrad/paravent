/* paravent.org — the few things the page does on its own:
   theme switch, reading progress, the folding screen opening with the scroll,
   reveals, the active section in the bar, the menu on a phone, the spotlight on
   feature cards, and screenshots not yet taken. No libraries. */
(function () {
  "use strict";
  var root = document.documentElement;
  root.classList.add("js");
  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function store(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* private mode: not kept */ }
  }

  /* The section the reader is in (the one crossing a line a third down the
     window) and the share of it above that line. */
  function here() {
    var line = window.innerHeight / 3;
    var sections = document.querySelectorAll("main [id], main > section");
    var found = null;
    sections.forEach(function (el) {
      var box = el.getBoundingClientRect();
      if (box.top <= line && box.bottom > line && el.id) found = { el: el, box: box };
    });
    if (!found) return { id: "", y: window.scrollY };
    return { id: found.el.id, frac: (line - found.box.top) / Math.max(1, found.box.height) };
  }

  function restore(place) {
    var target = place.id && document.getElementById(place.id);
    var top = target
      ? target.getBoundingClientRect().top + window.scrollY
        + place.frac * target.offsetHeight - window.innerHeight / 3
      : place.y || 0;
    window.scrollTo({ top: Math.max(0, top), behavior: "instant" });
  }

  var arriving = null;
  try {
    arriving = JSON.parse(sessionStorage.getItem("paravent-site-place") || "null");
    sessionStorage.removeItem("paravent-site-place");
  } catch (e) { arriving = null; }
  if (arriving && "scrollRestoration" in history) history.scrollRestoration = "manual";

  document.addEventListener("DOMContentLoaded", function () {
    if (arriving) {
      /* Shown straight in place, without the reveal's fade for what is already in view. */
      root.classList.add("arriving");
      restore(arriving);
      /* Again once fonts and pictures have settled the lengths. */
      window.addEventListener("load", function () {
        restore(arriving);
        setTimeout(function () { root.classList.remove("arriving"); }, 50);
      });
    }

    /* ---- theme: light ↔ dark, starting from what the device prefers */
    var toggle = document.querySelector("[data-theme-toggle]");
    var prefersDark = window.matchMedia("(prefers-color-scheme: dark)");
    function isDark() {
      return root.dataset.theme ? root.dataset.theme === "dark" : prefersDark.matches;
    }
    if (toggle) {
      toggle.addEventListener("click", function () {
        root.dataset.theme = isDark() ? "light" : "dark";
        store("paravent-site-theme", root.dataset.theme);
        shotsFollowTheme();
      });
    }
    /* The screenshots were taken in both modes: each shows the one the page is in. A
       dark picture that is missing falls back to the light one. */
    function shotsFollowTheme() {
      var dark = isDark();
      document.querySelectorAll("img[data-dark]").forEach(function (img) {
        if (!img.dataset.light) img.dataset.light = img.getAttribute("src");
        var wanted = dark && img.dataset.dark !== "missing" ? img.dataset.dark : img.dataset.light;
        if (img.getAttribute("src") !== wanted) img.setAttribute("src", wanted);
      });
    }
    document.querySelectorAll("img[data-dark]").forEach(function (img) {
      img.addEventListener("error", function () {
        if (img.getAttribute("src") === img.dataset.dark) {
          img.dataset.dark = "missing";
          img.setAttribute("src", img.dataset.light);
        }
      });
    });
    shotsFollowTheme();
    if (prefersDark.addEventListener) prefersDark.addEventListener("change", shotsFollowTheme);

    /* ---- switching the language keeps the reader where they were: the section in
       view and how far into it, which the other language's page restores (its
       sections share the ids but not the lengths). The choice itself is kept, so
       the start page no longer follows the browser's language. */
    document.querySelectorAll("[data-lang]").forEach(function (link) {
      link.addEventListener("click", function () {
        store("paravent-site-lang", link.dataset.lang);
        var place = here();
        if (place) {
          try { sessionStorage.setItem("paravent-site-place", JSON.stringify(place)); } catch (e) {}
        }
      });
    });

    /* ---- the menu on a phone */
    var menu = document.querySelector("[data-menu]");
    var nav = document.querySelector(".site-nav");
    if (menu && nav) {
      menu.addEventListener("click", function () {
        var open = nav.classList.toggle("open");
        menu.setAttribute("aria-expanded", String(open));
      });
      nav.addEventListener("click", function (event) {
        if (event.target.closest("a")) {
          nav.classList.remove("open");
          menu.setAttribute("aria-expanded", "false");
        }
      });
    }

    /* ---- scroll: progress bar, the bar's background, the folding screen.
       The screen arrives closed and springs two thirds open (the intro, with a
       little overshoot); the scroll through the hero opens the rest. */
    var top = document.querySelector(".site-top");
    var hero = document.querySelector(".hero");
    var intro = reduced ? 1 : 0;
    var scrolled = 0;
    function setOpen() {
      var open = reduced ? 1 : intro * 0.65 + scrolled * 0.35;
      root.style.setProperty("--open", open.toFixed(3));
      var settled = Math.min(1, Math.max(0, open));
      root.style.setProperty("--gleam", (Math.sin(Math.PI * settled) * 0.85).toFixed(3));
      /* The hinge swings early and settles late: 176° closed, 28° open. */
      var eased = 1 - Math.pow(1 - Math.min(1.15, Math.max(0, open)), 2);
      root.style.setProperty("--hinge", (176 - 148 * eased).toFixed(1) + "deg");
    }
    var ticking = false;
    function onScroll() {
      ticking = false;
      var max = document.documentElement.scrollHeight - window.innerHeight;
      var y = window.scrollY;
      root.style.setProperty("--read", max > 0 ? (y / max).toFixed(4) : "0");
      if (top) top.classList.toggle("scrolled", y > 12);
      if (hero) scrolled = Math.min(1, y / Math.max(1, hero.offsetHeight * 0.55));
      setOpen();
    }
    window.addEventListener("scroll", function () {
      if (!ticking) { ticking = true; requestAnimationFrame(onScroll); }
    }, { passive: true });
    onScroll();
    if (!reduced && document.querySelector(".screen3d")) {
      var start = null;
      var DELAY = 250, LENGTH = 1700;
      /* easeOutBack: past the mark, then back — the spring of a hinge. */
      var spring = function (t) { var c = 1.9; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); };
      var step = function (now) {
        if (start === null) start = now;
        var t = Math.min(1, Math.max(0, (now - start - DELAY) / LENGTH));
        intro = spring(t);
        setOpen();
        if (t < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    }

    /* ---- the headline's last words, one after the other; the last one stays */
    var rot = document.querySelector(".rot[data-words]");
    var words = [];
    try { words = JSON.parse(rot ? rot.getAttribute("data-words") : "[]"); } catch (e) { words = []; }
    if (rot && words.length > 1 && !reduced) {
      /* The headline keeps the height of its tallest word, so nothing below it jumps. */
      var heading = rot.closest("h1");
      var reserve = function () {
        var shown = rot.textContent, tallest = 0;
        heading.style.minHeight = "";
        words.forEach(function (word) {
          rot.textContent = word;
          tallest = Math.max(tallest, heading.offsetHeight);
        });
        rot.textContent = shown;
        heading.style.minHeight = tallest + "px";
      };
      var index = 0;
      rot.textContent = words[0];
      reserve();
      window.addEventListener("resize", reserve);
      /* Again once the titling font has arrived: its words are wider. */
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(reserve);
      var swap = function () {
        index += 1;
        rot.classList.add("out");
        setTimeout(function () {
          rot.textContent = words[index];
          rot.classList.remove("out");
          rot.classList.add("in");
          void rot.offsetWidth; // let the start position apply before moving in
          rot.classList.remove("in");
          if (index < words.length - 1) {
            setTimeout(swap, index === words.length - 2 ? 1500 : 1150);
          } else {
            rot.classList.add("done");
          }
        }, 320);
      };
      setTimeout(swap, 1900);
    }

    /* ---- reveals: once in view, with a small stagger within a group */
    var revealed = document.querySelectorAll(".reveal");
    if ("IntersectionObserver" in window && !reduced) {
      var seen = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          var el = entry.target;
          var siblings = Array.prototype.filter.call(el.parentNode.children, function (c) {
            return c.classList.contains("reveal");
          });
          el.style.setProperty("--delay", Math.min(siblings.indexOf(el), 6) * 0.06 + "s");
          el.classList.add("in");
          seen.unobserve(el);
        });
      }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
      revealed.forEach(function (el) { seen.observe(el); });
    } else {
      revealed.forEach(function (el) { el.classList.add("in"); });
    }

    /* ---- the section in view, marked in the bar */
    var links = {};
    document.querySelectorAll(".site-nav a[href^='#']").forEach(function (a) {
      links[a.getAttribute("href").slice(1)] = a;
    });
    if ("IntersectionObserver" in window) {
      var spy = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          var link = links[entry.target.id];
          if (!link || !entry.isIntersecting) return;
          Object.keys(links).forEach(function (id) { links[id].classList.remove("active"); });
          link.classList.add("active");
        });
      }, { rootMargin: "-45% 0px -50% 0px" });
      Object.keys(links).forEach(function (id) {
        var section = document.getElementById(id);
        if (section) spy.observe(section);
      });
    }

    /* ---- a spotlight on the feature card under the pointer */
    document.querySelectorAll(".feature").forEach(function (card) {
      card.addEventListener("pointermove", function (event) {
        var box = card.getBoundingClientRect();
        card.style.setProperty("--mx", event.clientX - box.left + "px");
        card.style.setProperty("--my", event.clientY - box.top + "px");
      });
    });

    /* ---- screenshots: the placeholder stays until the picture has loaded, so a
       screen not yet in assets/img/screens simply keeps it; a loaded one opens large
       on a click (or Enter), and a click outside it, the × or Esc closes it again */
    var box = document.querySelector("dialog.lightbox");
    var boxImg = box && box.querySelector("img");
    var boxCaption = box && box.querySelector(".lightbox-caption");
    function enlarge(img) {
      if (!box || !box.showModal) return;
      var figure = img.closest("figure");
      var caption = figure && figure.querySelector("figcaption");
      boxImg.src = img.currentSrc || img.src;
      boxImg.alt = img.alt;
      boxCaption.textContent = caption ? caption.textContent : "";
      box.showModal();
    }
    if (box) {
      box.querySelector(".lightbox-close").addEventListener("click", function () { box.close(); });
      box.addEventListener("click", function (event) {
        // the dialog itself is the space around the picture: a click there closes
        if (event.target === box || event.target === box.querySelector(".lightbox-figure")) box.close();
      });
    }
    document.querySelectorAll(".shot-body img").forEach(function (img) {
      var body = img.closest(".shot-body");
      function loaded() {
        img.classList.add("loaded");
        if (!box || body.classList.contains("zoomable")) return;
        body.classList.add("zoomable");
        body.setAttribute("role", "button");
        body.setAttribute("tabindex", "0");
        body.setAttribute("aria-label", (box.getAttribute("data-enlarge") || "") + ": " + img.alt);
        body.addEventListener("click", function () { enlarge(img); });
        body.addEventListener("keydown", function (event) {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); enlarge(img); }
        });
      }
      if (img.complete && img.naturalWidth > 0) loaded();
      img.addEventListener("load", loaded);
    });
  });
})();
