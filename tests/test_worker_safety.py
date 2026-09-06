from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from random import Random

import httpx
import pytest

from app.audit.beavercheck import missing_audit
from app.config import DiscoveryConfig
from app.discovery.osm import MAX_TRANSIENT_ATTEMPTS, OsmDiscovery
from app.models import City, Combination, CombinationState, Industry, OsmTag
from app.pipeline import CombinationDeferred, LeadApp
from app.state import StateStore
from app.urls import google_maps_search_url


def _mark_others(app: LeadApp, keep: set[str]) -> None:
    now = datetime.now(UTC)
    for combination in app.combinations:
        if combination.key not in keep:
            app.store.state.processed_combinations[combination.key] = CombinationState(
                key=combination.key,
                country=combination.country,
                city=combination.city,
                industry=combination.industry,
                completed_at=now,
            )


def _businesses(count: int, industry: str = "bakery") -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(count):
        rows.append(
            {
                "name": f"Shop {index}",
                "industry": industry,
                "country": "India",
                "city": "Mumbai",
                "website": f"https://shop-{index}.test",
                "google_maps_url": google_maps_search_url(industry, "Mumbai", "India"),
                "source": "file",
                "commercial": True,
                "location_weight": 0.9,
            }
        )
    return rows


@pytest.mark.asyncio
async def test_thirteen_urls_are_three_sequential_batches(app_config, tmp_data: Path):
    (tmp_data / "biz.json").write_text(json.dumps(_businesses(13)), encoding="utf-8")
    app = LeadApp(app_config, rng=Random(0))
    _mark_others(app, {"India|Mumbai|bakery"})
    batches: list[list[str]] = []

    async def fake_batch(self, urls: list[str]) -> dict:
        batches.append(list(urls))
        return {url: missing_audit(url) for url in urls}

    from app.audit import beavercheck

    monkey_target = beavercheck.BeaverCheckClient.batch_lookup
    beavercheck.BeaverCheckClient.batch_lookup = fake_batch  # type: ignore[method-assign]
    try:
        finished = await app.process_one_combination()
    finally:
        beavercheck.BeaverCheckClient.batch_lookup = monkey_target  # type: ignore[method-assign]
    assert finished is not None
    assert [len(item) for item in batches] == [5, 5, 3]
    assert finished.key in app.store.state.processed_combinations


@pytest.mark.asyncio
async def test_next_combination_waits_until_all_urls_finish(app_config, tmp_data: Path):
    payload = _businesses(13, "bakery") + _businesses(2, "dentist")
    (tmp_data / "biz.json").write_text(json.dumps(payload), encoding="utf-8")
    app = LeadApp(app_config, rng=Random(0))
    _mark_others(app, {"India|Mumbai|bakery", "India|Mumbai|dentist"})
    app.store.state.in_progress_combination = "India|Mumbai|bakery"
    events: list[str] = []

    async def fake_batch(self, urls: list[str]) -> dict:
        events.append(f"batch:{app.store.state.in_progress_combination}:{len(urls)}")
        return {url: missing_audit(url) for url in urls}

    orig_complete = app._mark_complete

    def tracking_complete(combination, business_count: int) -> None:
        events.append(f"complete:{combination.key}")
        orig_complete(combination, business_count)

    app._mark_complete = tracking_complete  # type: ignore[method-assign]
    from app.audit import beavercheck

    original = beavercheck.BeaverCheckClient.batch_lookup
    beavercheck.BeaverCheckClient.batch_lookup = fake_batch  # type: ignore[method-assign]
    try:
        first = await app.process_one_combination()
        second = await app.process_one_combination()
    finally:
        beavercheck.BeaverCheckClient.batch_lookup = original  # type: ignore[method-assign]
    assert first is not None and second is not None
    bakery_batches = [item for item in events if item.startswith("batch:India|Mumbai|bakery")]
    dentist_batches = [item for item in events if item.startswith("batch:India|Mumbai|dentist")]
    complete_at = events.index("complete:India|Mumbai|bakery")
    first_dentist = events.index(dentist_batches[0])
    assert len(bakery_batches) == 3
    assert first_dentist > complete_at


@pytest.mark.asyncio
async def test_overpass_429_retries_same_combination(monkeypatch: pytest.MonkeyPatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.discovery.osm.asyncio.sleep", fake_sleep)
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "nominatim" in str(request.url):
            return httpx.Response(
                200,
                json=[{"osm_id": 123, "osm_type": "relation"}],
            )
        posts["n"] += 1
        if posts["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "30"})
        return httpx.Response(
            200,
            json={
                "elements": [
                    {"id": 9, "type": "node", "tags": {"name": "Cafe Crust", "website": "crust.test"}}
                ]
            },
        )

    combination = Combination(country="India", city="Mumbai", industry="bakery")
    industry = Industry(name="bakery", osm_tags=[OsmTag(key="shop", value="bakery")])
    city = City(name="Mumbai", country="India", location_weight=0.9)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OsmDiscovery(
            client,
            DiscoveryConfig(overpass_url="https://overpass.example/api"),
            [industry],
            [city],
        )
        records = await provider.discover(combination)
    assert posts["n"] == 2
    assert 30.0 in sleeps
    assert len(records) == 1
    assert records[0].name == "Cafe Crust"


@pytest.mark.asyncio
async def test_overpass_504_retries_same_combination(monkeypatch: pytest.MonkeyPatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.discovery.osm.asyncio.sleep", fake_sleep)
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "nominatim" in str(request.url):
            return httpx.Response(
                200,
                json=[{"osm_id": 123, "osm_type": "relation"}],
            )
        posts["n"] += 1
        if posts["n"] == 1:
            return httpx.Response(504, headers={"Retry-After": "8"})
        return httpx.Response(
            200,
            json={
                "elements": [
                    {"id": 9, "type": "node", "tags": {"name": "Cafe Crust", "website": "crust.test"}}
                ]
            },
        )

    combination = Combination(country="India", city="Mumbai", industry="bakery")
    industry = Industry(name="bakery", osm_tags=[OsmTag(key="shop", value="bakery")])
    city = City(name="Mumbai", country="India", location_weight=0.9)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OsmDiscovery(
            client,
            DiscoveryConfig(overpass_url="https://overpass.example/api"),
            [industry],
            [city],
        )
        records = await provider.discover(combination)
    assert posts["n"] == 2
    assert sleeps
    assert len(records) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 504])
async def test_overpass_gives_up_after_five_transient_attempts(
    monkeypatch: pytest.MonkeyPatch, status: int
):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.discovery.osm.asyncio.sleep", fake_sleep)
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "nominatim" in str(request.url):
            return httpx.Response(
                200,
                json=[{"osm_id": 123, "osm_type": "relation"}],
            )
        posts["n"] += 1
        return httpx.Response(status, headers={"Retry-After": "4"})

    combination = Combination(country="India", city="Mumbai", industry="bakery")
    industry = Industry(name="bakery", osm_tags=[OsmTag(key="shop", value="bakery")])
    city = City(name="Mumbai", country="India", location_weight=0.9)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OsmDiscovery(
            client,
            DiscoveryConfig(overpass_url="https://overpass.example/api"),
            [industry],
            [city],
        )
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await provider.discover(combination)
    assert exc_info.value.response.status_code == status
    assert posts["n"] == MAX_TRANSIENT_ATTEMPTS
    assert len(sleeps) == MAX_TRANSIENT_ATTEMPTS - 1
    assert sleeps == sorted(sleeps)


@pytest.mark.asyncio
async def test_overpass_network_errors_cannot_retry_forever(monkeypatch: pytest.MonkeyPatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.discovery.osm.asyncio.sleep", fake_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "nominatim" in str(request.url):
            return httpx.Response(
                200,
                json=[{"osm_id": 123, "osm_type": "relation"}],
            )
        calls["n"] += 1
        raise httpx.ConnectError("overpass down", request=request)

    combination = Combination(country="India", city="Mumbai", industry="bakery")
    industry = Industry(name="bakery", osm_tags=[OsmTag(key="shop", value="bakery")])
    city = City(name="Mumbai", country="India", location_weight=0.9)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OsmDiscovery(
            client,
            DiscoveryConfig(overpass_url="https://overpass.example/api"),
            [industry],
            [city],
        )
        with pytest.raises(httpx.ConnectError):
            await provider.discover(combination)
    assert calls["n"] == MAX_TRANSIENT_ATTEMPTS
    assert len(sleeps) == MAX_TRANSIENT_ATTEMPTS - 1
    assert sleeps == [2.0, 4.0, 8.0, 16.0]


@pytest.mark.asyncio
async def test_overpass_502_cannot_retry_forever(monkeypatch: pytest.MonkeyPatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.discovery.osm.asyncio.sleep", fake_sleep)
    posts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "nominatim" in str(request.url):
            return httpx.Response(
                200,
                json=[{"osm_id": 123, "osm_type": "relation"}],
            )
        posts["n"] += 1
        return httpx.Response(502, headers={"Retry-After": "30"})

    combination = Combination(country="India", city="Mumbai", industry="bakery")
    industry = Industry(name="bakery", osm_tags=[OsmTag(key="shop", value="bakery")])
    city = City(name="Mumbai", country="India", location_weight=0.9)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        provider = OsmDiscovery(
            client,
            DiscoveryConfig(overpass_url="https://overpass.example/api"),
            [industry],
            [city],
        )
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await provider.discover(combination)
    assert exc_info.value.response.status_code == 502
    assert posts["n"] == MAX_TRANSIENT_ATTEMPTS
    assert len(sleeps) == MAX_TRANSIENT_ATTEMPTS - 1
    assert all(item <= 60.0 for item in sleeps)


@pytest.mark.asyncio
async def test_failed_overpass_defers_and_continues_to_next_combination(
    app_config, tmp_data: Path
):
    payload = _businesses(1, "bakery") + _businesses(1, "dentist")
    (tmp_data / "biz.json").write_text(json.dumps(payload), encoding="utf-8")
    app = LeadApp(app_config, rng=Random(0))
    _mark_others(app, {"India|Mumbai|bakery", "India|Mumbai|dentist"})
    app.store.state.in_progress_combination = "India|Mumbai|bakery"
    app._defer_cooldown_seconds = 10_000

    async def fake_discover(combination: Combination) -> list:
        if combination.key == "India|Mumbai|bakery":
            request = httpx.Request("POST", "https://overpass.example/api")
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError("429", request=request, response=response)
        return []

    app.discover_combination = fake_discover  # type: ignore[method-assign]
    with pytest.raises(CombinationDeferred) as deferred:
        await app.process_one_combination()
    assert deferred.value.combination.key == "India|Mumbai|bakery"
    assert "India|Mumbai|bakery" not in app.store.state.processed_combinations
    assert app.store.state.in_progress_combination is None
    nxt = app._next_combination()
    assert nxt is not None
    assert nxt.key == "India|Mumbai|dentist"
    finished = await app.process_one_combination()
    assert finished is not None
    assert finished.key == "India|Mumbai|dentist"
    assert "India|Mumbai|dentist" in app.store.state.processed_combinations
    assert "India|Mumbai|bakery" not in app.store.state.processed_combinations
    assert app._next_combination() is None
    app._deferred_until["India|Mumbai|bakery"] = 0
    later = app._next_combination()
    assert later is not None
    assert later.key == "India|Mumbai|bakery"


@pytest.mark.asyncio
async def test_overpass_timeout_defers_and_continues_to_next_combination(
    app_config, tmp_data: Path
):
    payload = _businesses(1, "bakery") + _businesses(1, "dentist")
    (tmp_data / "biz.json").write_text(json.dumps(payload), encoding="utf-8")
    app = LeadApp(app_config, rng=Random(0))
    _mark_others(app, {"India|Mumbai|bakery", "India|Mumbai|dentist"})
    app.store.state.in_progress_combination = "India|Mumbai|bakery"
    app._defer_cooldown_seconds = 10_000

    async def fake_discover(combination: Combination) -> list:
        if combination.key == "India|Mumbai|bakery":
            raise httpx.ConnectTimeout(
                "timed out",
                request=httpx.Request("POST", "https://overpass.example/api"),
            )
        return []

    app.discover_combination = fake_discover  # type: ignore[method-assign]
    with pytest.raises(CombinationDeferred):
        await app.process_one_combination()
    assert "India|Mumbai|bakery" not in app.store.state.processed_combinations
    assert app.store.state.in_progress_combination is None
    nxt = app._next_combination()
    assert nxt is not None
    assert nxt.key == "India|Mumbai|dentist"


@pytest.mark.asyncio
async def test_beavercheck_429_does_not_skip_remaining_urls(
    app_config, tmp_data: Path, monkeypatch: pytest.MonkeyPatch
):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.audit.beavercheck.asyncio.sleep", fake_sleep)
    (tmp_data / "biz.json").write_text(json.dumps(_businesses(13)), encoding="utf-8")
    app = LeadApp(app_config, rng=Random(0))
    app.config.audit.max_retries = 2
    _mark_others(app, {"India|Mumbai|bakery"})
    attempts = {"n": 0}
    batch_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        urls = body["urls"]
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"error": "rate"})
        batch_sizes.append(len(urls))
        return httpx.Response(
            200,
            json={"results": [{"url": url, "found": False} for url in urls]},
        )

    transport = httpx.MockTransport(handler)

    def fake_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    app._client = fake_client  # type: ignore[method-assign]
    from app.rate_limit import RateLimiter

    app.config.audit.rate_limit_per_minute = 10_000
    original_limiter = RateLimiter.acquire

    async def immediate(self) -> None:
        return None

    RateLimiter.acquire = immediate  # type: ignore[method-assign]
    try:
        finished = await app.process_one_combination()
    finally:
        RateLimiter.acquire = original_limiter  # type: ignore[method-assign]
    assert finished is not None
    assert batch_sizes == [5, 5, 3]
    assert 1.0 in sleeps
    assert finished.key in app.store.state.processed_combinations


@pytest.mark.asyncio
async def test_combination_completes_if_one_beavercheck_batch_fails(app_config, tmp_data: Path):
    (tmp_data / "biz.json").write_text(json.dumps(_businesses(13)), encoding="utf-8")
    app = LeadApp(app_config, rng=Random(0))
    _mark_others(app, {"India|Mumbai|bakery"})
    calls = {"n": 0}

    async def fake_batch(self, urls: list[str]) -> dict:
        calls["n"] += 1
        if calls["n"] == 2:
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("POST", "https://beavercheck.com/api/v2/batch"),
                response=httpx.Response(500),
            )
        return {url: missing_audit(url) for url in urls}

    from app.audit import beavercheck

    original = beavercheck.BeaverCheckClient.batch_lookup
    beavercheck.BeaverCheckClient.batch_lookup = fake_batch  # type: ignore[method-assign]
    try:
        finished = await app.process_one_combination()
    finally:
        beavercheck.BeaverCheckClient.batch_lookup = original  # type: ignore[method-assign]
    assert finished is not None
    assert finished.key in app.store.state.processed_combinations
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_state_persisted_once_after_successful_combination(app_config, tmp_data: Path):
    (tmp_data / "biz.json").write_text(json.dumps(_businesses(4)), encoding="utf-8")
    app = LeadApp(app_config, rng=Random(0))
    _mark_others(app, {"India|Mumbai|bakery"})
    persist_calls = {"n": 0}
    original = app.store.persist

    def counting_persist() -> None:
        persist_calls["n"] += 1
        original()

    app.store.persist = counting_persist  # type: ignore[method-assign]

    async def fake_batch(self, urls: list[str]) -> dict:
        return {url: missing_audit(url) for url in urls}

    from app.audit import beavercheck

    previous = beavercheck.BeaverCheckClient.batch_lookup
    beavercheck.BeaverCheckClient.batch_lookup = fake_batch  # type: ignore[method-assign]
    try:
        finished = await app.process_one_combination()
    finally:
        beavercheck.BeaverCheckClient.batch_lookup = previous  # type: ignore[method-assign]
    assert finished is not None
    assert persist_calls["n"] == 1
    assert finished.key in app.store.state.processed_combinations
    on_disk = StateStore(app_config.state_path)
    assert finished.key in on_disk.state.processed_combinations


@pytest.mark.asyncio
async def test_persist_failure_does_not_advance_combination(app_config, tmp_data: Path):
    (tmp_data / "biz.json").write_text(json.dumps(_businesses(4)), encoding="utf-8")
    app = LeadApp(app_config, rng=Random(0))
    _mark_others(app, {"India|Mumbai|bakery"})

    async def fake_batch(self, urls: list[str]) -> dict:
        return {url: missing_audit(url) for url in urls}

    def failing_persist() -> None:
        raise PermissionError(5, "Access is denied")

    from app.audit import beavercheck

    previous = beavercheck.BeaverCheckClient.batch_lookup
    beavercheck.BeaverCheckClient.batch_lookup = fake_batch  # type: ignore[method-assign]
    app.store.persist = failing_persist  # type: ignore[method-assign]
    try:
        with pytest.raises(PermissionError):
            await app.process_one_combination()
        assert "India|Mumbai|bakery" not in app.store.state.processed_combinations
        nxt = app._next_combination()
        assert nxt is not None
        assert nxt.key == "India|Mumbai|bakery"
    finally:
        beavercheck.BeaverCheckClient.batch_lookup = previous  # type: ignore[method-assign]


def test_restart_resumes_unfinished_combination(app_config, tmp_data: Path):
    app = LeadApp(app_config, rng=Random(0))
    _mark_others(app, {"India|Mumbai|bakery", "India|Mumbai|dentist"})
    app.store.state.in_progress_combination = "India|Mumbai|bakery"
    app.store.persist()
    restarted = LeadApp(app_config, rng=Random(1))
    nxt = restarted._next_combination()
    assert nxt is not None
    assert nxt.key == "India|Mumbai|bakery"
    loaded = StateStore(app_config.state_path)
    assert loaded.state.in_progress_combination == "India|Mumbai|bakery"
    assert "India|Mumbai|bakery" not in loaded.state.processed_combinations
