from __future__ import annotations

from datetime import UTC, datetime, timedelta
from random import Random

from app.models import City, Combination, CombinationState, Country, Industry


def load_geo(
    countries: list[Country],
    cities: list[City],
    industries: list[Industry],
) -> list[Combination]:
    country_names = {country.name for country in countries}
    city_by_country: dict[str, list[City]] = {}
    for city in cities:
        if city.country not in country_names:
            msg = f"City {city.name!r} references unknown country {city.country!r}"
            raise ValueError(msg)
        city_by_country.setdefault(city.country, []).append(city)

    combinations: list[Combination] = []
    for country in countries:
        for city in city_by_country.get(country.name, []):
            for industry in industries:
                combinations.append(
                    Combination(
                        country=country.name,
                        city=city.name,
                        industry=industry.name,
                    )
                )
    return combinations


def shuffle_combinations(
    combinations: list[Combination],
    rng: Random | None = None,
) -> list[Combination]:
    shuffled = list(combinations)
    (rng or Random()).shuffle(shuffled)
    return shuffled


def unprocessed_combinations(
    combinations: list[Combination],
    processed_keys: set[str],
) -> list[Combination]:
    return [item for item in combinations if item.key not in processed_keys]


def next_combination(
    combinations: list[Combination],
    processed_keys: set[str],
    rng: Random | None = None,
) -> Combination | None:
    remaining = shuffle_combinations(
        unprocessed_combinations(combinations, processed_keys),
        rng,
    )
    if not remaining:
        return None
    return remaining[0]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def next_recheck_combination(
    combinations: list[Combination],
    processed: dict[str, CombinationState],
    *,
    now: datetime,
    recheck_after_days: float,
    deferred_keys: set[str],
) -> Combination | None:
    by_key = {item.key: item for item in combinations}
    window = timedelta(days=max(0.0, recheck_after_days))
    now_utc = _aware(now)
    eligible: list[tuple[str, datetime]] = []
    for key, state in processed.items():
        if key in deferred_keys or key not in by_key:
            continue
        completed = _aware(state.completed_at)
        if now_utc - completed >= window:
            eligible.append((key, completed))
    if not eligible:
        return None
    eligible.sort(key=lambda item: item[1])
    return by_key[eligible[0][0]]


def earliest_recheck_at(
    processed: dict[str, CombinationState],
    *,
    recheck_after_days: float,
) -> datetime | None:
    if not processed:
        return None
    window = timedelta(days=max(0.0, recheck_after_days))
    times = [_aware(state.completed_at) + window for state in processed.values()]
    return min(times)
