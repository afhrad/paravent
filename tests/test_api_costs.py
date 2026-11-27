"""What the Claude API costs (app/services/api_costs.py): each answer priced by its
model, fallbacks by their own; months kept beside the translations; a run's own share;
and where the pages say it."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient

from app.services.api_costs import ApiCosts, Spend, price_of, spend_of, tally
from app.store.registry import StoreRegistry
from tests import test_translations as tt
from tests.test_translations import client  # noqa: F401 - the fixture


def answer(model: str, **usage: Any) -> SimpleNamespace:
    fields = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0,
              "cache_creation_input_tokens": 0, "iterations": None, **usage}  # fmt: skip
    return SimpleNamespace(model=model, usage=SimpleNamespace(**fields))


class TestPrices:
    def test_an_answer_by_its_model(self) -> None:
        spend = spend_of(
            answer(
                "claude-opus-5-5",
                input_tokens=1_000_000,
                output_tokens=100_000,
                cache_read_input_tokens=2_000_000,
                cache_creation_input_tokens=10_000,
            )
        )
        # 4 + 2 × 0.20 + 0.01 × 4 × 1.25 + 0.1 × 20
        assert spend.usd == pytest.approx(4 + 0.4 + 0.05 + 2)
        assert (spend.tokens_in, spend.output, spend.requests) == (3_010_000, 100_000, 1)
        assert price_of("claude-opus-5-5-20260401") == price_of("claude-opus-5-5")

    def test_a_fallback_at_its_own_price(self) -> None:
        parts = [
            SimpleNamespace(type="message", model="claude-opus-5-5", input_tokens=1_000_000,
                            output_tokens=0, cache_read_input_tokens=0,
                            cache_creation_input_tokens=0),
            SimpleNamespace(type="fallback_message", model="claude-sonnet-5-5",
                            input_tokens=1_000_000, output_tokens=1_000_000,
                            cache_read_input_tokens=0, cache_creation_input_tokens=0),
        ]  # fmt: skip
        spend = spend_of(answer("claude-sonnet-5-5", iterations=parts))
        assert spend.usd == pytest.approx(4 + 2 + 10) and spend.requests == 1

    def test_an_unknown_model_is_counted_not_priced(self) -> None:
        spend = spend_of(answer("claude-future-9", input_tokens=500, output_tokens=20))
        assert spend.usd == 0 and spend.unpriced == 520
        assert spend_of(SimpleNamespace()).requests == 0  # no usage at all


class TestLedger:
    async def test_months_and_a_runs_own_share(self, tmp_path: Path) -> None:
        costs = ApiCosts(tmp_path / "i18n" / "_api_costs.json")
        one = spend_of(answer("claude-opus-5-5", input_tokens=1000, output_tokens=1000))
        costs.record(one)  # outside any run: the month only

        async def ask() -> None:
            await asyncio.sleep(0)
            costs.record(one)

        with tally() as run:
            await asyncio.gather(ask(), ask(), ask())  # tasks started inside count too
        assert run.requests == 3 and run.usd == pytest.approx(3 * one.usd)
        month = costs.month()
        assert month.requests == 4 and month.output == 4000
        assert list(json.loads(costs.path.read_text())["months"]) == list(costs.months())
        # A file that does not read counts from nothing, and is written anew.
        costs.path.write_text("{not json")
        assert costs.month().requests == 0
        costs.record(one)
        assert costs.month().requests == 1
        assert Spend.of({"usd": "x", "input": True}).usd == 0

    async def test_drafts_are_counted(self, tmp_path: Path, client: Any) -> None:  # noqa: F811
        _ac, registry = client

        class Priced(tt._Reply):
            model = "claude-opus-5-5"
            usage = SimpleNamespace(
                input_tokens=2000, output_tokens=3000, cache_read_input_tokens=0,
                cache_creation_input_tokens=0, iterations=None,
            )  # fmt: skip

        machine, _fake = tt.TestMachineDrafts()._machine(
            registry.translations, lambda request: Priced(tt._german(request).content[0].text)
        )
        machine._costs = registry.api_costs
        from app.services.translation_targets import Target
        from app.services.translations import ENTRY_FIELDS

        data = tt._fireball(registry.compendium)
        target = Target("ref", "spell", "Fireball|PHB", data, ENTRY_FIELDS, "Fireball", ())
        with tally() as run:
            assert await machine.draft("de", target, by="dm")
        assert run.requests >= 1 and run.output == 3000 * run.requests
        assert registry.api_costs.month().requests == run.requests
        assert registry.api_costs.path.parent == registry.settings.i18n_dir


class TestPages:
    async def test_a_run_says_what_it_cost(
        self,
        client: tuple[AsyncClient, StoreRegistry],  # noqa: F811
    ) -> None:
        ac, registry = client

        class Priced(tt._Reply):
            model = "claude-opus-5-5"
            usage = SimpleNamespace(
                input_tokens=1_200_000, output_tokens=200_000, cache_read_input_tokens=0,
                cache_creation_input_tokens=0, iterations=None,
            )  # fmt: skip

        machine, _fake = tt.TestMachineDrafts()._machine(
            registry.translations, lambda request: Priced(tt._german(request).content[0].text)
        )
        machine._costs = registry.api_costs
        registry.__dict__["machine_translator"] = machine
        registry.__dict__.pop("translation_jobs", None)
        # The editor's own run says it in its notice.
        done = await ac.post(
            "/gm/translate/ref/spell/PHB/Fireball?lang=de", data={"action": "machine"}
        )
        shown = (await ac.get(done.headers["location"])).text
        assert "cost=" in done.headers["location"]
        assert "etwa" in shown
        await ac.post("/gm/translations/jobs", data={"lang": "de", "scope": "top:25"})
        job = registry.translation_jobs.jobs("de")[0]
        for _ in range(200):
            if job.finished:
                break
            await asyncio.sleep(0.05)
        assert job.finished and job.spend.requests
        fragment = (await ac.get("/gm/translations/jobs?lang=de")).text
        assert "data-paravent-toast=" in fragment and "Tokens hinein" in fragment
        page = (await ac.get("/gm/translations?lang=de")).text
        assert "Was die Claude-API gekostet hat" in page and "etwa" in page
