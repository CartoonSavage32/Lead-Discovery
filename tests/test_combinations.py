from __future__ import annotations

from random import Random

from app.catalog import load_cities, load_countries, load_industries
from app.combinations import (
    load_geo,
    next_combination,
    shuffle_combinations,
    unprocessed_combinations,
)


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

