from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from random import Random

import pytest

from app.models import CombinationState
from app.pipeline import LeadApp
from app.urls import business_key, google_maps_search_url


def _biz(name: str, industry: str, email: str | None = None) -> dict[str, object]:
    return {
        "name": name,
        "industry": industry,
        "country": "India",
        "city": "Mumbai",
        "website": None,
        "email": email,
        "phone": "+91111",
        "google_maps_url": google_maps_search_url(industry, "Mumbai", "India"),
        "source": "file",
        "commercial": True,
        "location_weight": 0.9,
    }


@pytest.mark.asyncio
async def test_daily_pacing_cap_stops_after_limit(app_config, tmp_data: Path):
    app_config.discovery.max_combinations_per_day = 2
    app_config.discovery.recheck_after_days = 0
    payload = [_biz("Bread", "bakery", "hello@gmail.com"), _biz("Smile", "dentist", "hello@gmail.com")]
    (tmp_data / "biz.json").write_text(json.dumps(payload), encoding="utf-8")
    app = LeadApp(app_config, rng=Random(0))
    processed: list[str] = []
    for _ in range(6):
        combo = await app.process_one_combination()
        if combo is None:
            break
        processed.append(combo.key)
    assert len(processed) == 2
    assert app.store.state.daily_combinations_processed == 2
    assert app._daily_cap_reached()
    assert app._next_combination() is None
    assert app._earliest_recheck_time() is not None


@pytest.mark.asyncio
async def test_daily_counter_resets_on_new_utc_day(
    app_config, tmp_data: Path, monkeypatch: pytest.MonkeyPatch
):
    app_config.discovery.max_combinations_per_day = 1
    app_config.discovery.recheck_after_days = 21
    payload = [_biz("Bread", "bakery"), _biz("Smile", "dentist")]
    (tmp_data / "biz.json").write_text(json.dumps(payload), encoding="utf-8")
    current = {"now": datetime(2026, 9, 8, 12, 0, tzinfo=UTC)}

    monkeypatch.setattr("app.pipeline.utcnow", lambda: current["now"])
    app = LeadApp(app_config, rng=Random(0))
    first = await app.process_one_combination()
    assert first is not None
    assert app.store.state.daily_combinations_processed == 1
    assert app._next_combination() is None

    current["now"] = datetime(2026, 9, 9, 0, 1, tzinfo=UTC)
    assert app._daily_cap_reached() is False
    second = await app.process_one_combination()
    assert second is not None
    assert second.key != first.key
    assert app.store.state.daily_counter_date == "2026-09-09"
    assert app.store.state.daily_combinations_processed == 1


@pytest.mark.asyncio
async def test_combinations_spread_across_simulated_days(
    app_config, tmp_data: Path, monkeypatch: pytest.MonkeyPatch
):
    app_config.discovery.max_combinations_per_day = 2
    app_config.discovery.recheck_after_days = 21
    payload = [_biz("Bread", "bakery"), _biz("Smile", "dentist")]
    (tmp_data / "biz.json").write_text(json.dumps(payload), encoding="utf-8")
    current = {"now": datetime(2026, 9, 8, 12, 0, tzinfo=UTC)}
    monkeypatch.setattr("app.pipeline.utcnow", lambda: current["now"])
    app = LeadApp(app_config, rng=Random(0))
    day_one: list[str] = []
    while True:
        combo = await app.process_one_combination()
        if combo is None:
            break
        day_one.append(combo.key)
    assert len(day_one) == 2
    assert len(app.store.state.processed_combinations) == 2

    current["now"] = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    day_two: list[str] = []
    while True:
        combo = await app.process_one_combination()
        if combo is None:
            break
        day_two.append(combo.key)
    assert len(day_two) == 2
    assert not set(day_one) & set(day_two)
    assert len(app.store.state.processed_combinations) == 4


@pytest.mark.asyncio
async def test_recheck_overwrites_lead_in_place(app_config, tmp_data: Path):
    app_config.discovery.max_combinations_per_day = 10
    app_config.discovery.recheck_after_days = 0
    path = tmp_data / "biz.json"
    path.write_text(json.dumps([_biz("Bread", "bakery")]), encoding="utf-8")
    app = LeadApp(app_config, rng=Random(0))
    for combination in app.combinations:
        if combination.key != "India|Mumbai|bakery":
            app.store.state.processed_combinations[combination.key] = CombinationState(
                key=combination.key,
                country=combination.country,
                city=combination.city,
                industry=combination.industry,
                completed_at=datetime.now(UTC) - timedelta(days=1),
            )
    first = await app.process_one_combination()
    assert first is not None
    lead_key = business_key(None, "Bread", "Mumbai", "India")
    assert app.store.state.leads[lead_key].outreach_email is None
    later = datetime.now(UTC) + timedelta(days=30)
    for combination in app.combinations:
        if combination.key != "India|Mumbai|bakery":
            app.store.state.processed_combinations[combination.key] = CombinationState(
                key=combination.key,
                country=combination.country,
                city=combination.city,
                industry=combination.industry,
                completed_at=later,
            )

    path.write_text(
        json.dumps([_biz("Bread", "bakery", "hello@gmail.com")]),
        encoding="utf-8",
    )
    app.store.state.daily_combinations_processed = 0
    second = await app.process_one_combination()
    assert second is not None
    assert second.key == "India|Mumbai|bakery"
    assert len([key for key in app.store.state.leads if key == lead_key]) == 1
    assert app.store.state.leads[lead_key].outreach_email == "hello@gmail.com"
    assert app.store.state.processed_combinations["India|Mumbai|bakery"].completed_at
