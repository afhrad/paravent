"""A world's days dated by a setting's calendar (app/services/calendar.py)."""

from __future__ import annotations

from types import SimpleNamespace

from httpx import AsyncClient

from app.models.gamestate import SourceSet, World
from app.services.calendar import Calendar, months_of
from app.services.clock import DAY
from app.services.i18n import UiCatalogue
from app.services.units import UnitFormat
from app.store.registry import StoreRegistry
from app.ui.clock_pages import by_season, weather_words
from app.ui.context import Page
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table

__all__ = ["table"]

# An invented calendar laid out as the books lay theirs out: a month's number, its name and
# a common name, holidays between the months.
REALMS = [
    ["1", "Frostmoot", "Deepwinter"],
    ["", "Annual holiday: Candlenight", ""],
    ["2", "Thawmark", "The Grey Thaw"],
    *[[str(n), f"M{n}", "Something"] for n in range(3, 13)],
]


def test_reading_calendars() -> None:
    realms = months_of(REALMS, 30)
    assert [m.name for m in realms[:3]] == ["Frostmoot", "Candlenight", "Thawmark"]
    assert realms[1].holiday and realms[1].days == 1 and realms[0].days == 30
    assert sum(m.days for m in realms) == 361
    assert realms[0].season == "winter" and realms[1].season == "winter"
    seasons = months_of([["1", "Ashmonth (mid-winter)"], ["2", "Rimemonth (late winter)"]], 28)
    assert seasons[0].name == "Ashmonth" and seasons[0].season == "winter"
    assert seasons[1].days == 28
    lengths = months_of([["1", "Brightwane", "29 days", "Lantern"]], 30)
    assert lengths[0].days == 29
    named = months_of([["1", "Greenwake (March)"]], 30)
    assert named[0].season == "spring"


def test_dates() -> None:
    calendar = Calendar("Realms", "DMG", months_of(REALMS, 30))
    today = calendar.index(1492, 2, 14)  # 14 Thawmark (Candlenight between)
    date = calendar.date(today)
    assert (date.year, date.month.name, date.day) == (1492, "Thawmark", 14)
    assert calendar.date(calendar.index(1492, 0, 30) + 1).month.name == "Candlenight"
    assert calendar.date(calendar.index(1492, 13, 30) + 1).year == 1493


def test_weather_by_season() -> None:
    assert by_season("light rain or light snowfall", "winter") == "light snowfall"
    assert by_season("Heavy rain or heavy snowfall", "summer") == "Heavy rain"
    assert by_season("no wind", "summer") == "no wind"
    assert by_season("light rain or light snowfall", "") == "light rain or light snowfall"


def test_weather_in_the_readers_words() -> None:
    german = Page(UiCatalogue().translator("de"))
    assert weather_words(german, "Normal for the season") == "jahreszeitlich normal"
    assert weather_words(german, "no wind") == "keiner"
    assert weather_words(german, "light rain or light snowfall") == (
        "leichter Regen oder leichter Schneefall"
    )
    assert weather_words(german, "30 degrees Fahrenheit colder") == "30 °F kälter als normal"
    # A reader in metres reads the difference in degrees Celsius.
    metric = SimpleNamespace(t=german.t, u=UnitFormat("metres", "de"))
    assert weather_words(metric, "20 degrees Fahrenheit hotter than normal") == (  # type: ignore[arg-type]
        "11 °C wärmer als normal"
    )
    # Words the vocabulary lacks stay as printed.
    assert weather_words(german, "Fog banks") == "Fog banks"


@requires_corpus
async def test_the_clock_reads_a_date(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, _lyra = table
    world = registry.gamestate.worlds.create(
        World(name="Realms", source_set=SourceSet(sources=["PHB", "DMG", "ERLW"]))
    )
    keys = [c.key for c in registry.calendars(world.id)]
    assert keys == ["The Calendar of Harptos", "Eberron Months"]
    harptos = registry.calendars(world.id)[0]
    mirtul = next(n for n, m in enumerate(harptos.months) if m.name == "Mirtul")
    await ac.post(
        f"/gm/clock/{world.id}/calendar",
        data={
            "calendar": "The Calendar of Harptos",
            "cday": "14",
            "cmonth": str(mirtul),
            "cyear": "1492",
            "era": "DR",
        },
    )
    page = (await ac.get(f"/gm?world={world.id}")).text
    assert "14 Mirtul 1492 DR · 08:00" in page and "spring" in page
    registry.clock.advance(world.id, 17 * DAY, "dm", "DM")  # past the month's end
    page = (await ac.get(f"/gm?world={world.id}")).text
    assert "1 Kythorn 1492 DR" in page
    bad = await ac.post(
        f"/gm/clock/{world.id}/calendar",
        data={"calendar": "The Calendar of Harptos", "cday": "31", "cmonth": "0", "cyear": "1"},
        headers={"HX-Request": "true"},
    )
    assert "not a date of this calendar" in bad.text


def test_a_leap_day_every_fourth_year() -> None:
    rows = [
        *[[str(n), f"M{n}", ""] for n in range(1, 8)],
        ["", "Annual holiday: Highsun", ""],
        ["", "Quadrennial holiday (leap day): Leapfeast", ""],
        *[[str(n), f"M{n}", ""] for n in range(8, 13)],
    ]
    calendar = Calendar("Reedmark", "DMG", months_of(rows, 30))
    shieldmeet = next(n for n, m in enumerate(calendar.months) if m.name == "Leapfeast")
    assert calendar.months[shieldmeet].every == 4
    assert calendar.length_of(1492) == 362 and calendar.length_of(1493) == 361
    assert calendar.has(1492, shieldmeet, 1) and not calendar.has(1493, shieldmeet, 1)
    for year in (1491, 1492, 1493, 1496):
        for month in (0, shieldmeet - 1, shieldmeet + 1):
            date = calendar.date(calendar.index(year, month, 1))
            assert (date.year, date.month) == (year, calendar.months[month])
    after = calendar.date(calendar.index(1493, shieldmeet - 1, 1) + 1)
    assert after.month.name == "M8"  # 1493 skips the leap day
    # An index counted with every leap day in every year names the same date.
    flat = 1493 * 362 + 30 * 3 + 4
    assert calendar.date(calendar.from_flat(flat)).month.name == "M4"
    assert calendar.date(calendar.from_flat(flat)).day == 5


@requires_corpus
async def test_an_old_epoch_keeps_its_date(table: tuple[AsyncClient, StoreRegistry, str]) -> None:
    ac, registry, _lyra = table
    world = registry.gamestate.worlds.create(
        World(name="Realms", source_set=SourceSet(sources=["PHB", "DMG"]))
    )
    harptos = registry.calendars(world.id)[0]
    mirtul = next(n for n, m in enumerate(harptos.months) if m.name == "Mirtul")
    flat = (
        1492 * sum(m.days for m in harptos.months)
        + sum(m.days for m in harptos.months[:mirtul])
        + 13
    )
    clock = registry.clock.of(world.id)
    registry.gamestate.clocks.upsert(
        clock.model_copy(
            update={
                "calendar": harptos.key,
                "calendar_epoch": flat,
                "calendar_era": "DR",
                "calendar_leaps": False,
            }
        )
    )
    shown = (await ac.get(f"/gm?world={world.id}")).text
    assert "14 Mirtul 1492 DR" in shown
    assert registry.gamestate.clocks.get(world.id).calendar_leaps
