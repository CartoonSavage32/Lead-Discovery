from __future__ import annotations

from random import Random

from app.models import City, Combination, Country, Industry


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
