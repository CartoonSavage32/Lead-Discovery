from __future__ import annotations

from pathlib import Path
from random import Random

from app.catalog import load_cities, load_countries, load_industries
from app.combinations import (
    load_geo,
    next_combination,
    shuffle_combinations,
    unprocessed_combinations,
)
from app.pipeline import LeadApp


def test_combination_generation(sample_files):
    countries = load_countries(sample_files["countries"])
    cities = load_cities(sample_files["cities"])
    industries = load_industries(sample_files["industries"])
    combinations = load_geo(countries, cities, industries)
    keys = {item.key for item in combinations}
    assert len(combinations) == 4
    assert "India|Mumbai|bakery" in keys
    assert "Japan|Tokyo|dentist" in keys
    assert "India|Tokyo|bakery" not in keys


def test_shuffle_is_deterministic_with_seed(sample_files):
    countries = load_countries(sample_files["countries"])
    cities = load_cities(sample_files["cities"])
    industries = load_industries(sample_files["industries"])
    combinations = load_geo(countries, cities, industries)
    a = [item.key for item in shuffle_combinations(combinations, Random(1))]
    b = [item.key for item in shuffle_combinations(combinations, Random(1))]
    c = [item.key for item in shuffle_combinations(combinations, Random(2))]
    assert a == b
    assert a != c


def test_duplicate_prevention(sample_files):
    countries = load_countries(sample_files["countries"])
    cities = load_cities(sample_files["cities"])
    industries = load_industries(sample_files["industries"])
    combinations = load_geo(countries, cities, industries)
    processed = {"India|Mumbai|bakery", "India|Mumbai|dentist"}
    remaining = unprocessed_combinations(combinations, processed)
    assert all(item.key not in processed for item in remaining)
    nxt = next_combination(combinations, processed, Random(0))
    assert nxt is not None
    assert nxt.key not in processed
    exhausted = next_combination(combinations, {item.key for item in combinations}, Random(0))
    assert exhausted is None


def test_recheck_picks_oldest_past_window(sample_files):
    from datetime import UTC, datetime, timedelta

    from app.combinations import next_recheck_combination
    from app.models import CombinationState

    countries = load_countries(sample_files["countries"])
    cities = load_cities(sample_files["cities"])
    industries = load_industries(sample_files["industries"])
    combinations = load_geo(countries, cities, industries)
    now = datetime(2026, 9, 8, tzinfo=UTC)
    processed = {
        "India|Mumbai|bakery": CombinationState(
            key="India|Mumbai|bakery",
            country="India",
            city="Mumbai",
            industry="bakery",
            completed_at=now - timedelta(days=30),
        ),
        "Japan|Tokyo|dentist": CombinationState(
            key="Japan|Tokyo|dentist",
            country="Japan",
            city="Tokyo",
            industry="dentist",
            completed_at=now - timedelta(days=5),
        ),
    }
    nxt = next_recheck_combination(
        combinations,
        processed,
        now=now,
        recheck_after_days=21,
        deferred_keys=set(),
    )
    assert nxt is not None
    assert nxt.key == "India|Mumbai|bakery"
    skipped = next_recheck_combination(
        combinations,
        processed,
        now=now,
        recheck_after_days=21,
        deferred_keys={"India|Mumbai|bakery"},
    )
    assert skipped is None


def test_new_industry_is_immediately_available_as_tier1(
    app_config, tmp_data: Path, sample_files: dict[str, Path]
):
    from datetime import UTC, datetime, timedelta

    import yaml

    from app.models import CombinationState

    app_config.discovery.max_combinations_per_day = 30
    app_config.discovery.recheck_after_days = 21
    completed = datetime.now(UTC) - timedelta(days=2)
    app = LeadApp(app_config, rng=Random(0))
    old_keys = {item.key for item in app.combinations}
    assert all(item.industry in {"bakery", "dentist"} for item in app.combinations)
    for combination in app.combinations:
        app.store.state.processed_combinations[combination.key] = CombinationState(
            key=combination.key,
            country=combination.country,
            city=combination.city,
            industry=combination.industry,
            completed_at=completed,
        )
    app.store.persist()
    assert app._next_combination() is None

    industries = yaml.safe_load(sample_files["industries"].read_text(encoding="utf-8"))
    industries.append(
        {
            "name": "photographer",
            "commercial": True,
            "osm_tags": [{"key": "craft", "value": "photographer"}],
        }
    )
    sample_files["industries"].write_text(yaml.safe_dump(industries), encoding="utf-8")

    restarted = LeadApp(app_config, rng=Random(0))
    nxt = restarted._next_combination()
    assert nxt is not None
    assert nxt.industry == "photographer"
    assert nxt.key not in old_keys
    assert restarted._earliest_recheck_time() is not None
    assert restarted._earliest_recheck_time() > datetime.now(UTC)

    restarted.store.state.daily_counter_date = datetime.now(UTC).date().isoformat()
    restarted.store.state.daily_combinations_processed = 30
    assert restarted._daily_cap_reached()
    assert restarted._next_combination() is None


def test_combination_key_includes_industry():
    from app.models import Combination

    key = Combination(country="France", city="Paris", industry="photographer").key
    assert key == "France|Paris|photographer"
    assert key.split("|") == ["France", "Paris", "photographer"]


def test_earliest_recheck_time_is_oldest_plus_window():
    from datetime import UTC, datetime, timedelta

    from app.combinations import earliest_recheck_at
    from app.models import CombinationState

    now = datetime(2026, 9, 8, tzinfo=UTC)
    processed = {
        "India|Mumbai|bakery": CombinationState(
            key="India|Mumbai|bakery",
            country="India",
            city="Mumbai",
            industry="bakery",
            completed_at=now - timedelta(days=10),
        ),
        "Japan|Tokyo|dentist": CombinationState(
            key="Japan|Tokyo|dentist",
            country="Japan",
            city="Tokyo",
            industry="dentist",
            completed_at=now - timedelta(days=2),
        ),
    }
    nxt = earliest_recheck_at(processed, recheck_after_days=21)
    assert nxt == (now - timedelta(days=10)) + timedelta(days=21)
