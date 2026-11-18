"""Record Paravent in real browsers and cut the recording into videos (website/README.md,
"Videos").

A take opens one browser window per actor — the game master at a desk, a player on a
phone — each logged in as its user, and plays a script: clicks, typing, scrolling,
captions, layout changes. Chrome streams each window's frames as they change (the
DevTools screencast, at CSS pixels); every frame is kept with its time. Nothing is
filmed from the screen, so a take runs headless and is repeatable: the server it plays
against rolls from a fixed seed (``PARAVENT_DICE_SEED``).

The cut composes the output frame by frame with Pillow: a background, each actor's
window at 1:1 (so text stays sharp) in a frame of its kind, a caption, title cards in
the Tome theme's fonts. The frames go to ffmpeg as raw video for H.264. From the same
cut come a WebVTT file of the captions, a poster and short GIF loops between marks.

ffmpeg here has no text filters, which is why text is drawn by Pillow, not ffmpeg.
"""

from __future__ import annotations

import base64
import io
import json
import math
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FILM = Path(__file__).resolve().parent
SITE = FILM.parent
ROOT = SITE.parent
FONTS = ROOT / "app/themes/tome/fonts"
LOGO = ROOT / "static/icons/png/paravent-icon-512.png"

WIDTH, HEIGHT, FPS = 1920, 1080, 30
DESK = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 900}
# Where the windows start when they stand side by side: room above for their labels.
TOP = 50
# The Tome theme's colours: leather behind, parchment for text, gilt for titles.
LEATHER = (38, 27, 20)
LEATHER_LIGHT = (66, 46, 32)
PARCHMENT = (243, 233, 210)
GILT = (214, 175, 96)
INK = (40, 28, 20)

# What no public video shows (as for the screenshots, shots.HIDDEN): the game master's
# translation prompts, the sound desk's speaker notice, and the books' own prose and
# pictures the character wizard quotes, which are not in the SRD.
HIDDEN = (
    ".cb-translate, .tr-note, .sd-status, .cg-card-about, .cg-about, .ref-fluff-body,"
    " .ref-fluff-image{ display:none !important; }"
)

# Drawn into every recorded page: a pointer that follows the mouse, a ring where it
# presses (a phone shows only the ring, as a finger leaves no pointer), and a gilt
# outline the script can put round what it talks about. A recording has no system
# pointer, so without these a viewer would not see where anything was clicked.
OVERLAY = """
(() => {
  if (window.__film) return; window.__film = true;
  const phone = %(phone)s;
  const css = `
    #film-cursor{position:fixed;left:0;top:0;width:26px;height:26px;z-index:2147483647;
      pointer-events:none;transform:translate(-100px,-100px);transition:opacity .2s}
    #film-cursor svg{filter:drop-shadow(0 2px 3px rgba(0,0,0,.45))}
    .film-ring{position:fixed;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%%;
      border:3px solid rgba(214,175,96,.95);background:rgba(214,175,96,.25);z-index:2147483646;
      pointer-events:none;animation:film-ring .55s ease-out forwards}
    @keyframes film-ring{from{transform:scale(.4);opacity:1}to{transform:scale(1.5);opacity:0}}
    .film-mark{outline:3px solid rgba(214,175,96,.95)!important;outline-offset:3px!important;
      border-radius:6px;transition:outline-color .3s}
    %(hidden)s`;
  const start = () => {
    const style = document.createElement('style'); style.textContent = css;
    document.head.appendChild(style);
    if (!phone) {
      const c = document.createElement('div'); c.id = 'film-cursor';
      c.innerHTML = '<svg width="26" height="26" viewBox="0 0 24 24">'
        + '<path d="M4 2l15 11-6.5 1 4 7.5-3 1.5-4-7.5L4 20z" fill="#fff" stroke="#222"'
        + ' stroke-width="1.4" stroke-linejoin="round"/></svg>';
      document.documentElement.appendChild(c);
      const at = JSON.parse(sessionStorage.getItem('film-at') || 'null');
      if (at) c.style.transform = `translate(${at[0]}px,${at[1]}px)`;
    }
  };
  if (document.head) start(); else addEventListener('DOMContentLoaded', start);
  addEventListener('mousemove', e => {
    const c = document.getElementById('film-cursor');
    if (c) c.style.transform = `translate(${e.clientX}px,${e.clientY}px)`;
    sessionStorage.setItem('film-at', JSON.stringify([e.clientX, e.clientY]));
  }, true);
  addEventListener('mousedown', e => {
    const r = document.createElement('div'); r.className = 'film-ring';
    r.style.left = e.clientX + 'px'; r.style.top = e.clientY + 'px';
    document.documentElement.appendChild(r); setTimeout(() => r.remove(), 700);
  }, true);
})();
"""


# -- the take: what happened, when -----------------------------------------------------


@dataclass
class Event:
    """Something on the timeline, at a wall-clock time: a caption, a layout, a card, a
    mark for a loop or the poster."""

    at: float
    kind: str  # caption | layout | card | mark
    value: Any


@dataclass
class Actor:
    """One recorded browser window and what it does, as a person would: the pointer
    travels to what it presses, words are typed a key at a time."""

    take: Take
    name: str
    phone: bool
    page: Any
    folder: Path
    label: str = ""
    size: tuple[int, int] = (0, 0)
    frames: list[tuple[float, Path]] = field(default_factory=list)
    _xy: tuple[float, float] = (640.0, 400.0)

    def go(self, path: str, settle: float = 1.0) -> None:
        """Open a page of the app."""
        self.page.goto(self.take.base + path)
        self.page.wait_for_load_state("load")
        self.take.wait(settle)

    def _find(self, selector: str, nth: int = 0) -> Any:
        found = self.page.locator(selector)
        if nth < 0:
            return found.last
        return found.nth(nth)

    def scroll(self, selector: str, nth: int = 0, block: str = "center") -> None:
        """Scroll smoothly until ``selector`` is in view."""
        target = self._find(selector, nth)
        target.wait_for(state="attached", timeout=10000)
        target.evaluate(f"e => e.scrollIntoView({{behavior: 'smooth', block: '{block}'}})")
        self.take.wait(0.7)

    def point(self, selector: str, nth: int = 0, *, scroll: bool = True) -> tuple[float, float]:
        """Move the pointer to the middle of ``selector`` along an eased path."""
        target = self._find(selector, nth)
        target.wait_for(state="visible", timeout=10000)
        if scroll:
            box = target.bounding_box()
            view = self.page.viewport_size
            if box is None or box["y"] < 60 or box["y"] + box["height"] > view["height"] - 40:
                self.scroll(selector, nth)
        box = target.bounding_box()
        assert box is not None, selector
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        if not self.phone:
            x0, y0 = self._xy
            steps = max(8, min(28, int(math.hypot(x - x0, y - y0) / 25)))
            for n in range(1, steps + 1):
                k = n / steps
                ease = k * k * (3 - 2 * k)
                self.page.mouse.move(x0 + (x - x0) * ease, y0 + (y - y0) * ease)
                self.page.wait_for_timeout(14)
        self._xy = (x, y)
        return x, y

    def click(self, selector: str, nth: int = 0, *, settle: float = 0.9) -> None:
        """Press what ``selector`` finds (the ``nth`` one; -1 the last)."""
        x, y = self.point(selector, nth)
        self.take.wait(0.25)
        self.page.mouse.click(x, y)
        self.take.wait(settle)

    def mark(self, selector: str, nth: int = 0, seconds: float = 1.6) -> None:
        """Outline ``selector`` in gilt for a moment: "this, here"."""
        target = self._find(selector, nth)
        target.evaluate("e => e.classList.add('film-mark')")
        self.take.wait(seconds)
        target.evaluate("e => e.classList.remove('film-mark')")

    def type(self, selector: str, text: str, *, clear: bool = True, delay: int = 45) -> None:
        """Type ``text`` into a field, a key at a time."""
        self.click(selector, settle=0.2)
        field = self._find(selector)
        if clear:
            field.fill("")
        field.press_sequentially(text, delay=delay)
        self.take.wait(0.4)

    def choose(self, selector: str, value: str) -> None:
        """Pick an option of a drop-down by its value."""
        self.point(selector)
        self._find(selector).select_option(value)
        self.take.wait(0.6)

    def key(self, key: str, settle: float = 0.6) -> None:
        self.page.keyboard.press(key)
        self.take.wait(settle)

    def run(self, script: str) -> Any:
        """Evaluate JavaScript in the page (for what a person does without a click,
        such as closing a toast)."""
        return self.page.evaluate(script)


class Take:
    """One recording: its actors, its timeline, and the frames on disk."""

    def __init__(self, browser: Any, base: str, folder: Path, password: str, lang: str) -> None:
        self.browser = browser
        self.base = base
        self.folder = folder
        self.password = password
        self.lang = lang
        self.actors: dict[str, Actor] = {}
        self.events: list[Event] = []
        self.start = time.time()
        folder.mkdir(parents=True, exist_ok=True)

    def actor(
        self,
        name: str,
        user: str,
        *,
        phone: bool = False,
        label: str = "",
        size: dict[str, int] | None = None,
    ) -> Actor:
        """A window logged in as ``user``, in the take's language, being recorded."""
        viewport = size or (PHONE if phone else DESK)
        context = self.browser.new_context(
            viewport=viewport,
            device_scale_factor=1,
            is_mobile=phone,
            has_touch=phone,
            color_scheme="light",
        )
        context.add_init_script(OVERLAY % {"phone": "true" if phone else "false", "hidden": HIDDEN})
        context.request.post(
            self.base + "/login", form={"username": user, "password": self.password}
        )
        context.request.post(self.base + "/language", form={"lang": self.lang})
        page = context.new_page()
        folder = self.folder / name
        folder.mkdir(parents=True, exist_ok=True)
        actor = Actor(
            self,
            name,
            phone,
            page,
            folder,
            label=label,
            size=(viewport["width"], viewport["height"]),
        )
        cdp = context.new_cdp_session(page)

        def frame(event: dict[str, Any]) -> None:
            path = folder / f"{len(actor.frames):06d}.jpg"
            path.write_bytes(base64.b64decode(event["data"]))
            actor.frames.append((float(event["metadata"]["timestamp"]), path))
            cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

        cdp.on("Page.screencastFrame", frame)
        cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 85,
                "maxWidth": viewport["width"],
                "maxHeight": viewport["height"],
            },
        )
        self.actors[name] = actor
        return actor

    def wait(self, seconds: float) -> None:
        """Let time pass, the windows streaming meanwhile."""
        pages = [a.page for a in self.actors.values()]
        if pages:
            pages[0].wait_for_timeout(int(seconds * 1000))
        else:
            time.sleep(seconds)

    def _event(self, kind: str, value: Any) -> None:
        self.events.append(Event(time.time(), kind, value))

    def layout(self, name: str, *who: str) -> None:
        """What the viewer sees from now: ``desk`` (one window), ``phone`` (a phone with
        the caption beside it), ``both`` (a desk and a phone side by side). ``who`` names
        the actors in it, in order."""
        self._event("layout", (name, who))

    def say(self, text: str, *, hold: float | None = None) -> None:
        """Show a caption from now until the next, and give the viewer time to read it."""
        self._event("caption", text)
        self.wait(hold if hold is not None else reading_time(text))

    def caption(self, text: str) -> None:
        """Show a caption from now, without waiting (it reads while something happens)."""
        self._event("caption", text)

    def quiet(self) -> None:
        """No caption from now."""
        self._event("caption", "")

    def card(self, title: str, subtitle: str = "", seconds: float = 3.2) -> None:
        """A title card over everything for ``seconds``."""
        self._event("card", (title, subtitle))
        self.wait(seconds)
        self._event("card", None)

    def mark(self, name: str) -> None:
        """A point in time the cut refers to: ``<loop>:start`` / ``<loop>:end`` for a GIF
        loop, ``poster`` for the poster frame."""
        self._event("mark", name)

    def save(self) -> Path:
        """Keep the timeline beside the frames, so a cut can be made again later."""
        data = {
            "start": self.start,
            "end": time.time(),
            "events": [[e.at, e.kind, e.value] for e in self.events],
            "actors": {
                name: {
                    "phone": a.phone,
                    "label": a.label,
                    "size": list(a.size),
                    "frames": [[t, str(p.relative_to(self.folder))] for t, p in a.frames],
                }
                for name, a in self.actors.items()
            },
        }
        path = self.folder / "take.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path


def reading_time(text: str) -> float:
    """How long a caption stays before the script goes on: about three words a second,
    never under two seconds."""
    return max(2.0, len(text.split()) / 2.8 + 0.8)


# -- the cut ------------------------------------------------------------------------------


@cache
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """A font of the Tome theme (shipped as WOFF2, read through fontTools)."""
    from fontTools.ttLib import TTFont

    data = TTFont(FONTS / f"{name}.woff2")
    data.flavor = None
    buffer = io.BytesIO()
    data.save(buffer)
    return ImageFont.truetype(io.BytesIO(buffer.getvalue()), size)


def fit(image: Image.Image, w: int, h: int) -> Image.Image:
    """A frame of another size in a window's box, scaled by its width and never
    stretched: the space it leaves stays the page's own white."""
    scale = w / image.width
    scaled = image.resize((w, max(1, round(image.height * scale))), Image.LANCZOS)
    out = Image.new("RGB", (w, h), (255, 255, 255))
    out.paste(scaled.crop((0, 0, w, min(h, scaled.height))), (0, 0))
    return out


def wrap(
    draw: ImageDraw.ImageDraw, text: str, face: ImageFont.FreeTypeFont, width: int
) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            trial = f"{line} {word}".strip()
            if draw.textlength(trial, font=face) <= width or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int


class Cut:
    """Composes a take into frames."""

    def __init__(self, take_file: Path, title: str) -> None:
        self.folder = take_file.parent
        data = json.loads(take_file.read_text(encoding="utf-8"))
        self.start = data["start"]
        self.end = data["end"]
        self.title = title
        self.events = [Event(at, kind, value) for at, kind, value in data["events"]]
        self.actors = data["actors"]
        self.frames = {
            name: [(t, self.folder / p) for t, p in a["frames"]] for name, a in self.actors.items()
        }
        # The timeline starts at the first caption, card or layout: the logins and the
        # first page loads before it are cut away.
        firsts = [e.at for e in self.events if e.kind in ("layout", "card", "caption")]
        self.begin = min(firsts) if firsts else self.start
        self.duration = self.end - self.begin
        self._backgrounds: dict[Any, Image.Image] = {}
        self._decoded: dict[Path, Image.Image] = {}

    # what holds at a moment

    def _state(self, kind: str, at: float, default: Any) -> Any:
        value = default
        for event in self.events:
            if event.at > at:
                break
            if event.kind == kind:
                value = event.value
        return value

    def _frame(self, name: str, at: float) -> Path | None:
        found = None
        for t, path in self.frames.get(name, []):
            if t > at:
                break
            found = path
        if found is None and self.frames.get(name):
            found = self.frames[name][0][1]
        return found

    def marks(self) -> dict[str, float]:
        """Every mark at its time in the video."""
        return {e.value: e.at - self.begin for e in self.events if e.kind == "mark"}

    def captions(self) -> list[tuple[float, float, str]]:
        """Each caption with its start and end in the video."""
        out: list[tuple[float, float, str]] = []
        shown = [e for e in self.events if e.kind == "caption"]
        for n, event in enumerate(shown):
            if not event.value:
                continue
            until = shown[n + 1].at if n + 1 < len(shown) else self.end
            out.append((event.at - self.begin, until - self.begin, event.value))
        return out

    # drawing

    def _image(self, path: Path) -> Image.Image:
        image = self._decoded.get(path)
        if image is None:
            image = Image.open(path).convert("RGB")
            if len(self._decoded) > 64:
                self._decoded.clear()
            self._decoded[path] = image
        return image

    def _background(
        self, layout: str, who: tuple[str, ...], caption: str
    ) -> tuple[Image.Image, list[tuple[str, Box]]]:
        key = (layout, who, caption)
        cached = self._backgrounds.get(key)
        boxes = self._boxes(layout, who)
        if cached is not None:
            return cached, boxes
        canvas = Image.new("RGB", (WIDTH, HEIGHT), LEATHER)
        glow = Image.new("RGB", (WIDTH, HEIGHT), LEATHER_LIGHT)
        mask = Image.new("L", (WIDTH, HEIGHT), 0)
        ImageDraw.Draw(mask).ellipse((-300, -500, WIDTH + 300, HEIGHT + 200), fill=150)
        canvas.paste(glow, (0, 0), mask.filter(ImageFilter.GaussianBlur(220)))
        draw = ImageDraw.Draw(canvas)
        for name, box in boxes:
            phone = self.actors[name]["phone"]
            self._frame_for(canvas, box, phone)
            label = self.actors[name].get("label") or ""
            if label and layout != "phone":
                face = font("cinzel-latin-700-normal", 22)
                draw.text((box.x, box.y - 34), label.upper(), font=face, fill=GILT)
        if caption:
            self._caption(canvas, draw, layout, boxes, caption)
        face = font("cinzel-latin-700-normal", 20)
        draw.text(
            (WIDTH - 40, HEIGHT - 30), "PARAVENT", font=face, fill=(150, 120, 80), anchor="rd"
        )
        if len(self._backgrounds) > 40:
            self._backgrounds.clear()
        self._backgrounds[key] = canvas
        return canvas, boxes

    def _frame_for(self, canvas: Image.Image, box: Box, phone: bool) -> None:
        """The shadow and bezel round a window."""
        pad = 16 if phone else 0
        radius = 46 if phone else 10
        shadow = Image.new("L", (WIDTH, HEIGHT), 0)
        ImageDraw.Draw(shadow).rounded_rectangle(
            (box.x - pad, box.y - pad + 12, box.x + box.w + pad, box.y + box.h + pad + 12),
            radius=radius,
            fill=170,
        )
        canvas.paste((10, 6, 4), (0, 0), shadow.filter(ImageFilter.GaussianBlur(18)))
        draw = ImageDraw.Draw(canvas)
        if phone:
            draw.rounded_rectangle(
                (box.x - pad, box.y - pad, box.x + box.w + pad, box.y + box.h + pad),
                radius=radius,
                fill=(18, 16, 15),
                outline=(70, 62, 55),
                width=2,
            )
        else:
            draw.rounded_rectangle(
                (box.x - 2, box.y - 2, box.x + box.w + 2, box.y + box.h + 2),
                radius=radius,
                fill=(90, 70, 50),
            )

    def _caption(
        self,
        canvas: Image.Image,
        draw: ImageDraw.ImageDraw,
        layout: str,
        boxes: list[tuple[str, Box]],
        text: str,
    ) -> None:
        if layout == "phone":
            # Beside the phone, large: the screen is narrow, the caption has room.
            face = font("alegreya-sans-latin-500-normal", 46)
            x = boxes[0][1].x + boxes[0][1].w + 110 if boxes else 200
            width = WIDTH - x - 120
            lines = wrap(draw, text, face, width)
            height = len(lines) * 62
            y = (HEIGHT - height) // 2
            draw.rectangle((x - 36, y - 10, x - 30, y + height), fill=GILT)
            for n, line in enumerate(lines):
                draw.text((x, y + n * 62), line, font=face, fill=PARCHMENT)
            return
        bottom = max((box.y + box.h for _, box in boxes), default=HEIGHT - 160) + 16
        room = HEIGHT - bottom - 24
        # As large as the room under the windows allows, never cut short.
        for points in (38, 34, 30, 27):
            face = font("alegreya-sans-latin-500-normal", points)
            lines = wrap(draw, text, face, WIDTH - 360)
            line_h = int(points * 1.3)
            if len(lines) * line_h <= room:
                break
        height = len(lines) * line_h
        y = bottom + (HEIGHT - bottom - height) // 2
        for n, line in enumerate(lines):
            draw.text((WIDTH // 2, y + n * line_h), line, font=face, fill=PARCHMENT, anchor="ma")

    def _boxes(self, layout: str, who: tuple[str, ...]) -> list[tuple[str, Box]]:
        def size(name: str) -> tuple[int, int]:
            # The window's own size: Chrome's first frame may come before the page has
            # its full height, and a box taken from it squeezes every frame after.
            if self.actors[name].get("size"):
                w, h = self.actors[name]["size"]
                return int(w), int(h)
            path = self.frames[name][-1][1] if self.frames.get(name) else None
            if path is None:
                viewport = PHONE if self.actors[name]["phone"] else DESK
                return viewport["width"], viewport["height"]
            with Image.open(path) as image:
                return image.size

        names = [n for n in who if n in self.actors]
        if layout == "both" and len(names) >= 2:
            (dw, dh), (pw, ph) = size(names[0]), size(names[1])
            gap = 90
            total = dw + gap + pw + 32
            x = (WIDTH - total) // 2
            return [
                (names[0], Box(x, TOP + (ph - dh) // 2 if ph > dh else TOP, dw, dh)),
                (names[1], Box(x + dw + gap + 16, TOP, pw, ph)),
            ]
        if layout == "table" and len(names) >= 2:
            # The game master's screen scaled down beside the players' phones at 1:1.
            phones = [size(n) for n in names[1:]]
            gap, margin = 50, 50
            room = WIDTH - 2 * margin - sum(w + 32 for w, _ in phones) - gap * len(phones)
            dw, dh = size(names[0])
            scale = min(1.0, room / dw)
            w, h = int(dw * scale), int(dh * scale)
            ph = max(h for _, h in phones)
            out = [(names[0], Box(margin, TOP + (ph - h) // 2, w, h))]
            x = margin + w + gap
            for name, (pw, phh) in zip(names[1:], phones, strict=True):
                out.append((name, Box(x + 16, TOP, pw, phh)))
                x += pw + 32 + gap
            return out
        if layout == "phones" and names:
            sizes = [size(n) for n in names]
            gap = 120
            total = sum(w + 32 for w, _ in sizes) + gap * (len(sizes) - 1)
            x = (WIDTH - total) // 2
            out = []
            for name, (w, h) in zip(names, sizes, strict=True):
                out.append((name, Box(x + 16, TOP, w, h)))
                x += w + 32 + gap
            return out
        if layout == "phone" and names:
            w, h = size(names[0])
            return [(names[0], Box(330, (HEIGHT - h) // 2, w, h))]
        if names:
            w, h = size(names[0])
            return [(names[0], Box((WIDTH - w) // 2, 62, w, h))]
        return []

    def _card(self, canvas: Image.Image, title: str, subtitle: str) -> Image.Image:
        card = Image.new("RGB", (WIDTH, HEIGHT), LEATHER)
        glow = Image.new("RGB", (WIDTH, HEIGHT), LEATHER_LIGHT)
        mask = Image.new("L", (WIDTH, HEIGHT), 0)
        ImageDraw.Draw(mask).ellipse((300, 150, WIDTH - 300, HEIGHT - 150), fill=200)
        card.paste(glow, (0, 0), mask.filter(ImageFilter.GaussianBlur(160)))
        draw = ImageDraw.Draw(card)
        if LOGO.exists():
            with Image.open(LOGO) as logo:
                mark = logo.convert("RGBA").resize((120, 120))
            card.paste(mark, ((WIDTH - 120) // 2, 250), mark)
        face = font("cinzel-latin-700-normal", 76)
        lines = wrap(draw, title, face, WIDTH - 400)
        y = 420
        for line in lines:
            draw.text((WIDTH // 2, y), line, font=face, fill=GILT, anchor="ma")
            y += 92
        draw.line((WIDTH // 2 - 220, y + 14, WIDTH // 2 + 220, y + 14), fill=GILT, width=2)
        if subtitle:
            face = font("libre-baskerville-latin-400-italic", 38)
            for n, line in enumerate(wrap(draw, subtitle, face, WIDTH - 500)):
                draw.text(
                    (WIDTH // 2, y + 50 + n * 54), line, font=face, fill=PARCHMENT, anchor="ma"
                )
        return card

    def frame_at(self, t: float) -> tuple[Any, Callable[[], Image.Image]]:
        """The key of the frame at ``t`` seconds into the video and how to draw it: the
        same key twice means the same picture."""
        at = self.begin + t
        card = self._state("card", at, None)
        if card:
            title, subtitle = card
            return ("card", title, subtitle), lambda: self._card(
                Image.new("RGB", (1, 1)), title, subtitle
            )
        layout, who = self._state("layout", at, ("desk", ()))
        who = tuple(who)
        caption = self._state("caption", at, "")
        shown = tuple((n, self._frame(n, at)) for n in who)

        def draw() -> Image.Image:
            background, boxes = self._background(layout, who, caption)
            canvas = background.copy()
            for name, box in boxes:
                path = dict(shown).get(name)
                if path is None:
                    continue
                image = self._image(path)
                if image.size != (box.w, box.h):
                    image = fit(image, box.w, box.h)
                mask = Image.new("L", image.size, 0)
                radius = 30 if self.actors[name]["phone"] else 8
                ImageDraw.Draw(mask).rounded_rectangle((0, 0, *image.size), radius=radius, fill=255)
                canvas.paste(image, (box.x, box.y), mask)
            return canvas

        return (layout, who, caption, shown), draw

    def frames_out(self) -> Iterator[bytes]:
        total = int(self.duration * FPS)
        last_key: Any = None
        last: bytes = b""
        for n in range(total):
            key, draw = self.frame_at(n / FPS)
            if key != last_key:
                last = draw().tobytes()
                last_key = key
            yield last


# -- export -------------------------------------------------------------------------------


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def write_mp4(cut: Cut, path: Path) -> None:
    """The video, H.264 at 1080p30, for any player and the web."""
    path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{WIDTH}x{HEIGHT}",
            "-r",
            str(FPS),
            "-i",
            "-",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "20",
            "-tune",
            "stillimage",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(path),
        ],
        stdin=subprocess.PIPE,
    )
    assert process.stdin is not None
    for frame in cut.frames_out():
        process.stdin.write(frame)
    process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError(f"ffmpeg failed on {path}")


def write_vtt(cut: Cut, path: Path) -> None:
    """The captions as WebVTT, for a page's <track> and for search."""

    def stamp(seconds: float) -> str:
        h, rest = divmod(max(0.0, seconds), 3600)
        m, s = divmod(rest, 60)
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"

    lines = ["WEBVTT", ""]
    for start, end, text in cut.captions():
        lines += [f"{stamp(start)} --> {stamp(end)}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_poster(video: Path, at: float, path: Path) -> None:
    ffmpeg("-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(path))


def write_gif(video: Path, start: float, end: float, path: Path, width: int = 800) -> None:
    """A short loop as GIF: twelve frames a second, its own palette."""
    graph = (
        f"fps=12,scale={width}:-1:flags=lanczos,split[a][b];"
        "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4"
    )
    ffmpeg(
        "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(video), "-vf", graph, str(path)
    )


def loops(marks: dict[str, float]) -> list[tuple[str, float, float]]:
    """The GIF loops the marks name: ``<name>:start`` … ``<name>:end``."""
    out = []
    for key, start in marks.items():
        if key.endswith(":start"):
            name = key.removesuffix(":start")
            end = marks.get(f"{name}:end")
            if end is not None and end > start:
                out.append((name, start, end))
    return out


def publish(take_file: Path, out: Path, slug: str, title: str) -> list[Path]:
    """Cut a take into ``<slug>.mp4``, ``.vtt``, ``.jpg`` and one GIF per loop."""
    cut = Cut(take_file, title)
    out.mkdir(parents=True, exist_ok=True)
    video = out / f"{slug}.mp4"
    write_mp4(cut, video)
    write_vtt(cut, out / f"{slug}.vtt")
    marks = cut.marks()
    made = [video, out / f"{slug}.vtt"]
    poster = out / f"{slug}.jpg"
    write_poster(video, marks.get("poster", min(6.0, cut.duration / 2)), poster)
    made.append(poster)
    for name, start, end in loops(marks):
        gif = out / f"{slug}-{name}.gif"
        write_gif(video, start, end, gif)
        made.append(gif)
    return made


def clean(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


__all__ = [
    "DESK",
    "PHONE",
    "Actor",
    "Cut",
    "Take",
    "clean",
    "loops",
    "publish",
    "reading_time",
]
