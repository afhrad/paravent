"""A world's days dated by a setting's calendar (app/services/calendar.py)."""

from __future__ import annotations

from httpx import AsyncClient

from app.models.gamestate import SourceSet, World
from app.services.calendar import Calendar, months_of
from app.services.clock import DAY
from app.store.registry import StoreRegistry
from app.ui.clock_pages import by_season
from tests.conftest import requires_corpus
from tests.test_travel_downtime_shop import table

__all__ = ["table"]

REALMS = [
    ["1", "Hammer", "Deepwinter"],
    ["", "Annual holiday: Midwinter", ""],
    ["2", "Alturiak", "The Claw of Winter"],
    *[[str(n), f"M{n}", "Something"] for n in range(3, 13)],
]


def test_reading_calendars() -> None:
    realms = months_of(REALMS, 30)
    assert [m.name for m in realms[:3]] == ["Hammer", "Midwinter", "Alturiak"]
    assert realms[1].holiday and realms[1].days == 1 and realms[0].days == 30
    assert sum(m.days for m in realms) == 361
    assert realms[0].season == "winter" and realms[1].season == "winter"
    eberron = months_of([["1", "Zarantyr (mid-winter)"], ["2", "Olarune (late winter)"]], 28)
    assert eberron[0].name == "Zarantyr" and eberron[0].season == "winter"
    assert eberron[1].days == 28
    theros = months_of([["1", "Lyokymion", "29 days", "Thassa"]], 30)
    assert theros[0].days == 29
    ravnica = months_of([["1", "Seleszeni (March)"]], 30)
    assert ravnica[0].season == "spring"


def test_dates() -> None:
    calendar = Calendar("Realms", "DMG", months_of(REALMS, 30))
    today = calendar.index(1492, 2, 14)  # 14 Alturiak (Midwinter between)
    date = calendar.date(today)
    assert (date.year, date.month.name, date.day) == (1492, "Alturiak", 14)
    assert calendar.date(calendar.index(1492, 0, 30) + 1).month.name == "Midwinter"
    assert calendar.date(calendar.index(1492, 13, 30) + 1).year == 1493


def test_weather_by_season() -> None:
    assert by_season("light rain or light snowfall", "winter") == "light snowfall"
    assert by_season("Heavy rain or heavy snowfall", "summer") == "Heavy rain"
    assert by_season("no wind", "summer") == "no wind"
    assert by_season("light rain or light snowfall", "") == "light rain or light snowfall"


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
