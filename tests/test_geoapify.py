from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.config import AppConfig, apply_runtime_env, require_discovery_credentials
from app.discovery.factory import build_discovery
from app.discovery.geoapify import (
    GeoapifyDiscovery,
    GeoapifyQuotaExceeded,
    records_from_geoapify,
)
from app.models import City, Combination, Industry
from app.pipeline import CombinationDeferred, LeadApp


def _config(tmp_path: Path, **kwargs: object) -> AppConfig:
    payload: dict[str, object] = {
        "data_dir": str(tmp_path),
        "geocode_cache_path": str(tmp_path / "geocode_cache.json"),
        "discovery": {
            "provider": "geoapify",
            "max_results_per_combination": 10,
            "timeout_seconds": 5,
            "geoapify": {
                "api_key": "test-key",
                "requests_per_second": 1000,
                "quota_cooldown_minutes": 60,
            },
        },
    }
    payload.update(kwargs)
    return AppConfig.model_validate(payload)


def _industry() -> Industry:
    return Industry(name="bakery", geoapify_categories=["commercial.food_and_drink.bakery"])


def _city() -> City:
    return City(name="Paris", country="France", location_weight=0.94)


def test_records_from_geoapify_map_contact_fields():
    combination = Combination(country="France", city="Paris", industry="bakery")
    features = [
        {"properties": {}},
        {
            "properties": {
                "name": "Pain Chaud",
                "place_id": "abc",
                "website": "painchaud.fr",
                "contact": {"phone": "+331234", "email": "hello@painchaud.fr"},
                "formatted": "1 Rue du Pain, Paris",
                "categories": ["commercial.food_and_drink.bakery"],
            }
        },
    ]
    records = records_from_geoapify(features, combination, _industry(), _city(), limit=10)
    assert len(records) == 1
    assert records[0].website == "https://painchaud.fr"
    assert records[0].phone == "+331234"
    assert records[0].email == "hello@painchaud.fr"
    assert records[0].source == "geoapify"
    assert records[0].address == "1 Rue du Pain, Paris"


@pytest.mark.asyncio
async def test_geocode_is_cached_across_industries_and_processes(tmp_path: Path):
    geocode_hits = {"n": 0}
    places_hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/geocode/search" in url:
            geocode_hits["n"] += 1
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "lat": 48.8566,
                            "lon": 2.3522,
                            "place_id": "paris-id",
                        }
                    ]
                },
            )
        places_hits["n"] += 1
        return httpx.Response(
            200,
            json={
                "features": [
                    {"properties": {"name": "Shop", "place_id": f"p{places_hits['n']}"}}
                ]
            },
        )

    config = _config(tmp_path)
    industry = _industry()
    florist = Industry(name="florist", geoapify_categories=["commercial.florist"])
    city = _city()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        first = GeoapifyDiscovery(client, config, [industry, florist], [city])
        bakeries = await first.discover(Combination(country="France", city="Paris", industry="bakery"))
        flowers = await first.discover(Combination(country="France", city="Paris", industry="florist"))
    assert geocode_hits["n"] == 1
    assert places_hits["n"] == 2
    assert bakeries and flowers
    cache_path = tmp_path / "geocode_cache.json"
    assert cache_path.exists()
    raw = cache_path.read_text(encoding="utf-8")
    assert "Paris|France" in raw

    async with httpx.AsyncClient(transport=transport) as client:
        second = GeoapifyDiscovery(client, config, [industry], [city])
        again = await second.discover(Combination(country="France", city="Paris", industry="bakery"))
    assert geocode_hits["n"] == 1
    assert places_hits["n"] == 3
    assert again


@pytest.mark.asyncio
async def test_quota_429_does_not_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.discovery.geoapify.asyncio.sleep", fake_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, json={"message": "Daily quota exceeded"})

    config = _config(tmp_path)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = GeoapifyDiscovery(client, config, [_industry()], [_city()])
        with pytest.raises(GeoapifyQuotaExceeded):
            await provider.discover(Combination(country="France", city="Paris", industry="bakery"))
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_pipeline_quota_pauses_discovery(app_config, caplog: pytest.LogCaptureFixture):
    app_config.discovery.provider = "geoapify"
    app_config.discovery.geoapify.api_key = "test-key"
    app_config.discovery.geoapify.quota_cooldown_minutes = 60
    app = LeadApp(app_config)
    calls = {"n": 0}

    async def fake_discover(combination):
        calls["n"] += 1
        raise GeoapifyQuotaExceeded(combination)

    app.discover_combination = fake_discover  # type: ignore[method-assign]
    with pytest.raises(CombinationDeferred):
        await app.discover_next()
    assert calls["n"] == 1
    assert app._discovery_paused()
    assert "Geoapify daily quota exhausted, pausing discovery until reset" in caplog.text
    combo, records = await app.discover_next()
    assert combo is None
    assert records == []
    assert calls["n"] == 1


def test_missing_geoapify_key_fails_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GEOAPIFY_API_KEY", raising=False)
    monkeypatch.delenv("GEO_API_KEY", raising=False)
    config = _config(tmp_path)
    config.discovery.geoapify.api_key = ""
    apply_runtime_env(config)
    with pytest.raises(ValueError, match="GEOAPIFY_API_KEY"):
        require_discovery_credentials(config)


@pytest.mark.asyncio
async def test_factory_registers_geoapify(tmp_path: Path):
    config = _config(tmp_path)
    async with httpx.AsyncClient() as client:
        provider = build_discovery(config, client, [_industry()], [_city()])
    assert isinstance(provider, GeoapifyDiscovery)
