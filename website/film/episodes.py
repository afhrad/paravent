"""The episodes: what happens in each, written once for every language.

A script finds what it presses by the app's forms and ids, never by the words on a
button, so the same script records the English and the German video. The words it shows
come from ``texts.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from engine import Actor, Take
from stage import GM, PLAYER, PLAYER2, Table, fight, session, table
from texts import EPISODES, LABELS


@dataclass(frozen=True)
class Episode:
    slug: str
    stage: Callable[..., Table]
    play: Callable[[Take, Table, dict[str, str], dict[str, str]], None]


# -- helpers on the tracker -------------------------------------------------------------


def _active(gm: Actor) -> str:
    """The text of the row whose turn it is."""
    row = gm.page.locator("li.combatant.is-active").first
    return row.inner_text() if row.count() else ""


def _next_until(gm: Actor, take: Take, wanted: Callable[[Actor], bool], most: int = 12) -> None:
    for _ in range(most):
        if wanted(gm):
            return
        gm.click('form[action$="/next"] button', settle=1.1)
    raise RuntimeError("the turn never came")


def _foe_active(gm: Actor) -> bool:
    return gm.page.locator("li.combatant.is-active.is-foe").count() > 0


def _named_active(name: str) -> Callable[[Actor], bool]:
    return lambda gm: name in _active(gm)


def _attack(gm: Actor, take: Take) -> None:
    """The selected creature attacks until an attack waits for the game master: a natural 1
    rolls no damage and leaves nothing to resolve."""
    for _ in range(4):
        gm.click('form[action$="/attack"]:not(.cb-pending-form) button', settle=1.4)
        if gm.page.locator(".cb-pending-form").count():
            return
    raise RuntimeError("no attack came to resolve")


def _player_attack(gm: Actor, player: Actor, take: Take, button: str) -> None:
    """A player attacks from the sheet until the roll waits in the tracker."""
    for _ in range(4):
        player.click(button, settle=1.6)
        if gm.page.locator(".cb-pending-form").count():
            return
    raise RuntimeError("no attack came to resolve")


def _resolve(gm: Actor, take: Take, target: str) -> None:
    """Pick the waiting attack's target by name and press Hit."""
    pending = gm.page.locator(".cb-pending-form").first
    pending.wait_for(state="visible", timeout=10000)
    select = pending.locator("select[name=target]")
    gm.point(".cb-pending-form select[name=target]")
    option = select.locator("option", has_text=target).first
    select.select_option(value=option.get_attribute("value"))
    take.wait(0.6)
    gm.click(".cb-pending-form button.btn-dmg", settle=1.4)


# -- episodes ---------------------------------------------------------------------------


def play_fight(take: Take, table: Table, t: dict[str, str], labels: dict[str, str]) -> None:
    gm = take.actor("gm", GM, label=labels["gm"])
    lyra = take.actor("lyra", PLAYER, phone=True, label=labels["lyra"])
    tracker = f"/gm/combat/{table.combat}"
    gm.go(tracker)
    lyra.go(f"/characters/{table.lyra}")

    take.card(t["title"], t["subtitle"])
    take.layout("both", "gm", "lyra")
    take.say(t["intro"])
    take.caption(t["rolled"])
    gm.mark('li.combatant:has-text("Varis")', seconds=1.4)
    gm.mark('li.combatant:has-text("Brunhild")', seconds=1.4)

    take.caption(t["lyra_rolls"])
    take.mark("initiative:start")
    lyra.click(
        'form[action$="/act/roll"]:has(input[name=kind][value=initiative]) button', settle=1.6
    )
    take.say(t["arrives"], hold=2.4)
    take.mark("initiative:end")

    take.caption(t["monsters"])
    # Each group's button once: it stays after rolling, for a reroll.
    for n in range(gm.page.locator('form[action$="/roll"] button').count()):
        gm.click('form[action$="/roll"] button', n, settle=0.9)
    take.caption(t["begin"])
    gm.click('form[action$="/begin"] button', settle=1.6)
    take.mark("poster")

    take.caption(t["next"])
    _next_until(gm, take, _foe_active)
    take.caption(t["foe"])
    gm.click("li.combatant.is-active a.cb-name", settle=1.4)
    take.say(t["attack"], hold=1.2)
    take.mark("hit:start")
    _attack(gm, take)
    take.caption(t["target"])
    _resolve(gm, take, "Lyra")
    take.say(t["landed"], hold=2.6)
    take.mark("hit:end")

    _next_until(gm, take, _named_active("Lyra"))
    take.say(t["your_turn"], hold=2.4)
    take.caption(t["fire_bolt"])
    _player_attack(
        gm,
        lyra,
        take,
        'form[action$="/act/roll"]:has(input[name=subject][value^="Fire Bolt"]) button',
    )
    take.caption(t["verdict"])
    _resolve(gm, take, "Snik")
    take.say(t["burned"], hold=3.0)
    take.say(t["outro"])
    take.card(t["end_title"], t["end_subtitle"], seconds=2.6)


def _roll(kind: str, subject: str) -> str:
    """A sheet's roll button for a check (``skill``, ``perception``)."""
    return (
        f'form[action$="/act/roll"]:has(input[name=kind][value="{kind}"])'
        f':has(input[name=subject][value="{subject}"]) button'
    )


def _view(actor: Actor, view: str) -> None:
    """A phone's sheet view: play, stats, track or gear."""
    actor.click(f'a[data-view-link="{view}"]', settle=1.0)


def _open(actor: Actor, form: str) -> None:
    """Open the folded section a form sits in, if it is folded."""
    if actor.page.locator(form).first.is_hidden():
        actor.click(f"details:has({form}) > summary", settle=0.8)


def _share(
    gm: Actor, take: Take, chapter: str, characters: list[str], title: str, *, shown: bool
) -> None:
    """Share the chapter's first read-aloud box with ``characters``: as a viewer sees it
    done (``shown``), or out of sight, the game master's window not in the picture."""
    if shown:
        gm.click("a.md-share", settle=1.2)
    else:
        gm.page.goto(take.base + f"/gm/chapters/{chapter}")
        gm.page.locator("a.md-share").first.click()
    gm.page.wait_for_selector("form.journal-share")
    for box in gm.page.locator("form.journal-share input[name=characters]").all():
        wanted = box.get_attribute("value") in characters
        if box.is_checked() == wanted:
            continue
        if shown:
            box.click()
            take.wait(0.3)
        else:
            box.set_checked(wanted)
    if shown:
        gm.type("form.journal-share input[name=title]", title)
        gm.click("form.journal-share button[type=submit]", settle=1.6)
    else:
        gm.page.locator("form.journal-share input[name=title]").fill(title)
        gm.page.locator("form.journal-share button[type=submit]").click()
        take.wait(1.6)


def play_phone(take: Take, table: Table, t: dict[str, str], labels: dict[str, str]) -> None:
    gm = take.actor("gm", GM, label=labels["gm"])
    lyra = take.actor("lyra", PLAYER, phone=True, label=labels["lyra"])
    lyra.go(f"/characters/{table.lyra}")
    take.card(t["title"], t["subtitle"])
    take.layout("phone", "lyra")
    take.say(t["intro"])
    _view(lyra, "stats")
    take.say(t["stats"], hold=2.0)
    take.mark("roll:start")
    lyra.click(_roll("skill", "perception"), settle=1.2)
    take.say(t["rolled"])
    take.mark("roll:end")
    _view(lyra, "play")
    take.caption(t["play"])
    lyra.scroll('form:has(input[name=spell][value^="Magic Missile"])')
    take.wait(2.0)
    take.caption(t["cast"])
    lyra.click(
        'form:has(input[name=spell][value^="Magic Missile"]) button[formaction$="/act/cast"]',
        settle=1.6,
    )
    take.wait(1.2)
    _view(lyra, "track")
    take.say(t["track"], hold=2.2)
    take.caption(t["rest"])
    lyra.click('form[action$="/act/rest"]:has(input[name=kind][value=short]) button', settle=1.6)
    take.wait(1.0)
    take.caption(t["log"])
    lyra.click(".shell-log", settle=1.4)
    take.wait(2.4)
    lyra.key("Escape", settle=0.8)
    take.caption(t["handout"])
    _share(gm, take, table.chapter, [table.lyra], "Grey Reed", shown=False)
    take.wait(2.6)
    take.caption(t["journal"])
    lyra.go("/journal", settle=1.2)
    lyra.click('a[href^="/journal/"]:not([href*="notes"])', settle=1.6)
    take.wait(1.6)
    take.say(t["outro"])
    take.card(t["end_title"], t["end_subtitle"], seconds=2.6)


def _type_lines(actor: Actor, take: Take, text: str) -> None:
    """Type Markdown line by line, closing any suggestion before each line break."""
    for n, line in enumerate(text.split("\n")):
        if n:
            actor.key("Escape", settle=0.05)
            actor.key("Enter", settle=0.1)
        if line:
            actor.page.keyboard.type(line, delay=38)
    take.wait(0.6)


def play_adventure(take: Take, table: Table, t: dict[str, str], labels: dict[str, str]) -> None:
    gm = take.actor("gm", GM, label=labels["gm"])
    lyra = take.actor("lyra", PLAYER, phone=True, label=labels["lyra"])
    gm.go(f"/gm/chapters/{table.chapter}/edit")
    lyra.go(f"/characters/{table.lyra}")
    take.card(t["title"], t["subtitle"])
    take.layout("desk", "gm")
    take.say(t["intro"])
    gm.click("textarea[name=body]", settle=0.3)
    gm.run(
        "(() => { const a = document.querySelector('textarea[name=body]');"
        " a.focus(); a.selectionStart = a.selectionEnd = a.value.length;"
        " a.scrollTop = a.scrollHeight; })()"
    )
    take.caption(t["typing"])
    _type_lines(gm, take, "\n\n" + t["new_text"])
    take.caption(t["preview"])
    reference = '#preview a.ref[href*="Goblin"]'
    gm.page.locator(reference).last.evaluate(
        "e => e.scrollIntoView({behavior: 'smooth', block: 'center'})"
    )
    take.wait(0.8)
    gm.point(reference, -1, scroll=False)
    take.wait(3.4)
    take.caption(t["save"])
    gm.page.mouse.move(400, 300)
    gm.click("form.editor-form button[type=submit]:not([name])", settle=1.6)
    gm.scroll(".md-callout", nth=-1)
    take.wait(2.4)
    take.layout("both", "gm", "lyra")
    take.caption(t["share"])
    gm.mark("a.md-share", nth=-1, seconds=1.4)
    gm.click("a.md-share", nth=-1, settle=1.2)
    take.caption(t["choose"])
    for box in gm.page.locator("form.journal-share input[name=characters]").all():
        if box.is_checked() != (box.get_attribute("value") == table.lyra):
            box.click()
            take.wait(0.3)
    gm.type("form.journal-share input[name=title]", t["handout_title"])
    take.mark("share:start")
    gm.click("form.journal-share button[type=submit]", settle=1.4)
    take.say(t["sent"], hold=3.0)
    take.mark("share:end")
    take.say(t["outro"])
    take.card(t["end_title"], t["end_subtitle"], seconds=2.6)


def play_between(take: Take, table: Table, t: dict[str, str], labels: dict[str, str]) -> None:
    gm = take.actor("gm", GM, label=labels["gm"])
    lyra = take.actor("lyra", PLAYER, phone=True, label=labels["lyra"])
    party = f"/gm?world={table.world}"
    gm.go(party)
    lyra.go(f"/characters/{table.lyra}")
    take.card(t["title"], t["subtitle"])
    take.layout("both", "gm", "lyra")
    take.say(t["intro"])
    take.caption(t["clock"])
    take.mark("clock:start")
    gm.click('form[action$="/advance"]:has(input[name=minutes][value="60"]) button', settle=1.8)
    take.mark("clock:end")
    take.caption(t["travel"])
    _open(gm, "form.party-travel-form")
    gm.choose("form.party-travel-form select[name=unit]", "day")
    gm.click("form.party-travel-form button[type=submit]", settle=2.0)
    take.say(t["travelled"], hold=2.6)
    take.caption(t["shop"])
    _open(gm, "form.shop-form")
    gm.type("form.shop-form input[name=name]", t["shop_name"])
    gm.click("form.shop-form button[type=submit]", settle=1.8)
    take.caption(t["browse"])
    lyra.go(f"/characters/{table.lyra}/shop", settle=1.4)
    lyra.scroll('form:has(input[name=item][value="Torch|PHB"])')
    take.wait(1.0)
    lyra.click('form:has(input[name=item][value="Torch|PHB"]) button', settle=1.6)
    take.say(t["bought"], hold=2.4)
    take.caption(t["downtime"])
    _open(gm, "form[action$='/downtime']")
    gm.type("form[action$='/downtime'] input[name=days]", "7")
    gm.click("form[action$='/downtime'] button[type=submit]", settle=1.6)
    take.caption(t["project"])
    lyra.go(f"/characters/{table.lyra}/downtime", settle=1.4)
    research = "form.downtime-form:has(input[name=kind][value=research])"
    _open(lyra, research)
    lyra.type(f"{research} input[name=label]", t["research_label"])
    lyra.type(f"{research} input[name=needed]", "5")
    lyra.click(f"{research} button[type=submit]", settle=1.6)
    lyra.click("form:has(input[name=op][value=spend]) button[type=submit]", settle=1.6)
    take.say(t["spent"], hold=2.6)
    take.say(t["outro"])
    take.card(t["end_title"], t["end_subtitle"], seconds=2.6)


def _answer_rest(actor: Actor, take: Take) -> None:
    """Answer what a wizard step still asks with its first offer (a subrace, a choice)."""
    for _ in range(4):
        if actor.page.locator('button[value="next"]:not([disabled])').count():
            return
        open_question = actor.page.locator(
            "fieldset.cg-question:not(:has(input:checked)) label:has(input[type=radio])"
        )
        if not open_question.count():
            return
        box = open_question.first
        box.scroll_into_view_if_needed()
        actor.click(
            "fieldset.cg-question:not(:has(input:checked)) label:has(input[type=radio])", settle=1.2
        )


def _wizard_next(actor: Actor, take: Take) -> None:
    """Press the wizard's next step once it may be pressed."""
    actor.page.wait_for_selector('button[value="next"]:not([disabled])', timeout=10000)
    actor.click('button[value="next"]', settle=1.6)


def play_start(take: Take, table: Table, t: dict[str, str], labels: dict[str, str]) -> None:
    gm = take.actor("gm", GM, label=labels["gm"])
    lyra = take.actor("lyra", PLAYER, phone=True, label=labels["lyra"])
    gm.go("/gm/worlds/new")
    lyra.go("/play")
    take.card(t["title"], t["subtitle"])
    take.layout("desk", "gm")
    take.say(t["intro"])
    gm.type("input[name=name]", t["world_name"])
    gm.type("textarea[name=description]", t["world_text"])
    take.caption(t["books"])
    for book in ("PHB", "MM", "DMG"):
        box = f'input[name=sources][value="{book}"]'
        if not gm.page.locator(box).first.is_checked():
            gm.click(box, settle=0.4)
    take.wait(1.2)
    gm.click("form.stack-form button[type=submit]", settle=1.8)
    take.say(t["created"])
    world = gm.page.url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    take.layout("both", "gm", "lyra")
    take.caption(t["wizard"])
    lyra.go("/chargen/new", settle=1.0)
    if lyra.page.locator("form.cg-new select[name=world]").count():
        lyra.choose("form.cg-new select[name=world]", world)
    lyra.click("form.cg-new button[type=submit]", settle=1.6)
    take.layout("phone", "lyra")
    take.caption(t["species"])
    lyra.click('label:has(input[name=species][value="Half-Orc|PHB"])', settle=1.2)
    _answer_rest(lyra, take)
    _wizard_next(lyra, take)
    take.caption(t["class"])
    lyra.click('label:has(input[name=class][value="Wizard|PHB"])', settle=1.2)
    _answer_rest(lyra, take)
    take.wait(1.6)
    _wizard_next(lyra, take)
    take.caption(t["abilities"])
    lyra.click('label:has(input[name="abilities.method"][value=standard])', settle=1.6)
    take.wait(1.6)
    take.say(t["more"])
    take.caption(t["sheet"])
    lyra.go(f"/characters/{table.lyra}", settle=1.4)
    take.wait(2.6)
    take.say(t["outro"])
    take.card(t["end_title"], t["end_subtitle"], seconds=2.6)


def _hit_all(gm: Actor, take: Take) -> None:
    """Every foe still standing to 0 hit points, a row at a time."""
    for _ in range(8):
        rows = gm.page.locator("li.combatant.is-foe:not(.is-dead) form[action$='/hp']")
        if not rows.count():
            return
        form = rows.first
        form.locator("input[name=amount]").fill("20")
        take.wait(0.3)
        box = form.locator("button").first.bounding_box()
        if box:
            gm.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        take.wait(0.9)


def play_session(take: Take, table: Table, t: dict[str, str], labels: dict[str, str]) -> None:
    gm = take.actor("gm", GM, label=labels["gm"])
    lyra = take.actor("lyra", PLAYER, phone=True, label=labels["lyra"])
    brunhild = take.actor("brunhild", PLAYER2, phone=True, label=labels["brunhild"])
    gm.go(f"/gm/session?world={table.world}")
    lyra.go(f"/characters/{table.lyra}")
    brunhild.go(f"/characters/{table.brunhild}")
    take.card(t["title"], t["subtitle"])
    take.layout("table", "gm", "lyra", "brunhild")
    take.say(t["intro"])
    take.caption(t["start"])
    gm.click(f'form[action$="/session/{table.world}/start"] button', settle=1.6)
    take.caption(t["read"])
    gm.go(f"/gm/chapters/{table.chapter}", settle=1.2)
    gm.mark(".md-callout", seconds=1.6)
    _share(gm, take, table.chapter, [table.lyra, table.brunhild], t["title"], shown=True)
    take.say(t["shared"], hold=2.6)
    take.caption(t["check"])
    gm.go(f"/gm?world={table.world}", settle=1.2)
    _open(gm, "form.party-checks-form")
    gm.scroll("form.party-checks-form")
    gm.choose("form.party-checks-form select[name=what]", "skill:perception")
    gm.type("form.party-checks-form input[name=dc]", "13")
    for box in gm.page.locator("form.party-checks-form input[name=character]").all():
        if box.is_checked() != (box.get_attribute("value") in (table.lyra, table.brunhild)):
            box.click()
    gm.click("form.party-checks-form button[type=submit]:not([name])", settle=1.6)
    take.caption(t["rolls"])
    for phone in (lyra, brunhild):
        _view(phone, "stats")
        phone.click(_roll("skill", "perception"), settle=1.0)
    take.say(t["results"], hold=3.0)
    take.caption(t["fight"])
    gm.go(f"/gm/encounters/{table.encounter}", settle=1.0)
    gm.click('form[action$="/run"] button', settle=1.8)
    take.caption(t["initiative"])
    for phone in (lyra, brunhild):
        _view(phone, "play")
        phone.click(
            'form[action$="/act/roll"]:has(input[name=kind][value=initiative]) button', settle=1.0
        )
    for n in range(gm.page.locator('form[action$="/roll"] button').count()):
        gm.click('form[action$="/roll"] button', n, settle=0.7)
    # Varis is played by the game master: the initiative typed in, as rolled at the table.
    varis = gm.page.locator("li.combatant:has-text('Varis')").first.get_attribute("id") or ""
    field = f'form[action$="/c/{varis.removeprefix("c-")}/initiative"] input[name=value]'
    gm.type(field, "12")
    gm.key("Enter", settle=1.0)
    gm.click('form[action$="/begin"] button', settle=1.6)
    _next_until(gm, take, _foe_active)
    take.caption(t["goblin"])
    gm.click("li.combatant.is-active a.cb-name", settle=1.2)
    _attack(gm, take)
    _resolve(gm, take, "Brunhild")
    take.wait(1.4)
    take.caption(t["brunhild"])
    _player_attack(
        gm, brunhild, take, 'form[action$="/act/roll"]:has(input[name=kind][value=attack]) button'
    )
    _resolve(gm, take, table.extra.get("first_goblin", "Snik"))
    take.wait(1.2)
    take.caption(t["quick"])
    _hit_all(gm, take)
    take.caption(t["end"])
    gm.click("a.cb-end", settle=1.4)
    gm.click("form[action$='/end'] button[type=submit]", settle=1.6)  # the confirmation
    take.caption(t["award"])
    gm.click("form.cb-award button[type=submit]", settle=1.8)
    take.caption(t["treasure"])
    gm.go(f"/gm/treasure/{table.extra['treasure']}", settle=1.2)
    share = "form[action$='/share']"
    gm.scroll(share)
    gm.choose(f"{share} select[name='item.0']", table.lyra)
    for box in gm.page.locator(f"{share} input[name=coins_to]").all():
        if box.is_checked() != (box.get_attribute("value") in (table.lyra, table.brunhild)):
            box.click()
    gm.click(f"{share} button[type=submit]", settle=1.8)
    take.say(t["shared_out"], hold=2.4)
    take.caption(t["close"])
    gm.go(f"/gm/session?world={table.world}", settle=1.0)
    gm.click(f'form[action$="/session/{table.world}/end"] button', settle=2.0)
    take.layout("desk", "gm")
    take.say(t["note"], hold=4.0)
    take.say(t["outro"])
    take.card(t["end_title"], t["end_subtitle"], seconds=2.6)


def _plain(library: Any, scratch: Any, lang: str, users: Any) -> Table:
    return table(library, scratch, lang, users)[0]


EPISODE_LIST = [
    Episode("fight", fight, play_fight),
    Episode("phone", _plain, play_phone),
    Episode("adventure", _plain, play_adventure),
    Episode("between", _plain, play_between),
    Episode("start", _plain, play_start),
    Episode("session", session, play_session),
]
BY_SLUG = {e.slug: e for e in EPISODE_LIST}


def texts(slug: str, lang: str) -> tuple[dict[str, str], dict[str, str]]:
    return EPISODES[slug][lang], LABELS[lang]


__all__ = ["BY_SLUG", "EPISODE_LIST", "Episode", "texts"]
