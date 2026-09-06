from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.discovery.file_source import FileDiscovery
from app.discovery.osm import records_from_overpass
from app.models import City, Combination, Industry, OsmTag
from app.pipeline import LeadApp
from app.urls import google_maps_search_url


@pytest.mark.asyncio
async def test_file_discovery_filters_combination(tmp_path: Path):
    payload = [
        {
            "name": "Good Bread",
            "industry": "bakery",
            "country": "India",
            "city": "Mumbai",
            "website": "https://bread.test",
            "google_maps_url": "https://example.invalid",
            "source": "file",
        },
        {
            "name": "Other",
            "industry": "dentist",
            "country": "Japan",
            "city": "Tokyo",
            "google_maps_url": "https://example.invalid",
            "source": "file",
        },
    ]
    path = tmp_path / "biz.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    provider = FileDiscovery(path)
    combo = Combination(country="India", city="Mumbai", industry="bakery")
    records = await provider.discover(combo)
    assert len(records) == 1
    assert records[0].name == "Good Bread"
    assert records[0].google_maps_url == google_maps_search_url("bakery", "Mumbai", "India")


def test_overpass_records_skip_unnamed():
    industry = Industry(name="bakery", osm_tags=[OsmTag(key="shop", value="bakery")])
    city = City(name="Mumbai", country="India", location_weight=0.9)
    combination = Combination(country="India", city="Mumbai", industry="bakery")
    elements = [
        {"id": 1, "type": "node", "tags": {"shop": "bakery"}},
        {
            "id": 2,
            "type": "node",
            "tags": {
                "name": "Cafe Crust",
                "website": "crust.test",
                "stars": "4.6",
                "reviews": "12",
            },
        },
    ]
    records = records_from_overpass(elements, combination, industry, city)
    assert len(records) == 1
    assert records[0].domain == "crust.test"
    assert records[0].rating == 4.6
    assert records[0].source == "openstreetmap"


@pytest.mark.asyncio
async def test_pipeline_does_not_repeat_combination(app_config, tmp_data: Path):
    businesses = [
        {
            "name": "Good Bread",
            "industry": "bakery",
            "country": "India",
            "city": "Mumbai",
            "website": None,
            "rating": 4.8,
            "review_count": 90,
            "google_maps_url": google_maps_search_url("bakery", "Mumbai", "India"),
            "source": "file",
            "commercial": True,
            "location_weight": 0.9,
        }
    ]
    (tmp_data / "biz.json").write_text(json.dumps(businesses), encoding="utf-8")
    from datetime import UTC, datetime

    from app.models import CombinationState

    app = LeadApp(app_config, rng=__import__("random").Random(0))
    now = datetime.now(UTC)
    for combination in app.combinations:
        if combination.key != "India|Mumbai|bakery":
            app.store.state.processed_combinations[combination.key] = CombinationState(
                key=combination.key,
                country=combination.country,
                city=combination.city,
                industry=combination.industry,
                completed_at=now,
            )
    first = await app.process_one_combination()
    assert first is not None
    assert first.key == "India|Mumbai|bakery"
    assert first.key in app.store.state.processed_combinations
    assert app.store.state.in_progress_combination is None
    second = await app.process_one_combination()
    assert second is None
