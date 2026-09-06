from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.config import DiscoveryConfig
from app.models import BusinessRecord, City, Combination, Industry
from app.urls import domain_from_url, google_maps_search_url, normalize_website

logger = logging.getLogger(__name__)

MAX_TRANSIENT_ATTEMPTS = 5


def _retry_after_seconds(response: httpx.Response, default: float = 30.0) -> float:
    raw = response.headers.get("Retry-After")
    if not raw:
        return default
    try:
        return max(float(raw), 1.0)
    except ValueError:
        return default


def _backoff_seconds(attempt: int, response: httpx.Response | None = None) -> float:
    exponential = float(2 ** (attempt + 1))
    if response is None:
        return min(exponential, 60.0)
    hinted = _retry_after_seconds(response, default=exponential)
    return min(max(hinted, exponential), 60.0)


def _area_id(osm_id: int, osm_type: str) -> int | None:
    if osm_type == "relation":
        return 3600000000 + osm_id
    if osm_type == "way":
        return 2400000000 + osm_id
    return None


def _overpass_query(area_id: int, industry: Industry, limit: int) -> str:
    clauses: list[str] = []
    for tag in industry.osm_tags:
        safe_key = tag.key.replace('"', "")
        safe_value = tag.value.replace('"', "")
        clauses.append(f'nwr["{safe_key}"="{safe_value}"](area.searchArea);')
    if not clauses:
        return ""
    inner = "\n  ".join(clauses)
    return (
        f"[out:json][timeout:60];\n"
        f"area({area_id})->.searchArea;\n"
        f"(\n  {inner}\n);\n"
        f"out center tags {limit};\n"
    )


def _float_tag(tags: dict[str, str], *keys: str) -> float | None:
    for key in keys:
        raw = tags.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except ValueError:
            continue
    return None


def _int_tag(tags: dict[str, str], *keys: str) -> int | None:
    for key in keys:
        raw = tags.get(key)
        if raw is None:
            continue
        try:
            return int(float(raw))
        except ValueError:
            continue
    return None


def records_from_overpass(
    elements: list[dict[str, Any]],
    combination: Combination,
    industry: Industry,
    city: City,
) -> list[BusinessRecord]:
    maps_url = google_maps_search_url(
        combination.industry,
        combination.city,
        combination.country,
    )
    records: list[BusinessRecord] = []
    seen: set[str] = set()
    for element in elements:
        tags = {str(k): str(v) for k, v in (element.get("tags") or {}).items()}
        name = tags.get("name") or tags.get("name:en")
        if not name:
            continue
        website = normalize_website(
            tags.get("website") or tags.get("contact:website") or tags.get("url")
        )
        source_id = str(element.get("id"))
        key = website or f"{element.get('type')}:{source_id}"
        if key in seen:
            continue
        seen.add(key)
        records.append(
            BusinessRecord(
                name=name,
                industry=combination.industry,
                country=combination.country,
                city=combination.city,
                website=website,
                domain=domain_from_url(website),
                rating=_float_tag(tags, "stars", "rating", "stars:tripadvisor"),
                review_count=_int_tag(
                    tags, "review_count", "reviews", "stars:tripadvisor:count"
                ),
                category=industry.name,
                phone=tags.get("phone") or tags.get("contact:phone"),
                email=tags.get("email") or tags.get("contact:email"),
                address=tags.get("addr:full")
                or " ".join(
                    part
                    for part in (
                        tags.get("addr:housenumber"),
                        tags.get("addr:street"),
                    )
                    if part
                )
                or None,
                google_maps_url=maps_url,
                source="openstreetmap",
                source_id=f"{element.get('type')}:{source_id}",
                commercial=industry.commercial,
                location_weight=city.location_weight,
            )
        )
    return records


class OsmDiscovery:
    """Permitted business-data source using Nominatim + Overpass (OpenStreetMap)."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        config: DiscoveryConfig,
        industries: list[Industry],
        cities: list[City],
    ) -> None:
        self.client = client
        self.config = config
        self.industries = {item.name: item for item in industries}
        self.cities = {(item.country, item.name): item for item in cities}

    async def _request(
        self,
        method: str,
        url: str,
        combination: Combination,
        resource: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(MAX_TRANSIENT_ATTEMPTS):
            try:
                response = await self.client.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers={"User-Agent": self.config.user_agent},
                    timeout=self.config.timeout_seconds,
                )
            except httpx.RequestError as exc:
                last_error = exc
                if attempt >= MAX_TRANSIENT_ATTEMPTS - 1:
                    logger.warning(
                        "%s network error for %s; giving up after %s attempts",
                        resource,
                        combination.key,
                        MAX_TRANSIENT_ATTEMPTS,
                    )
                    raise
                wait = _backoff_seconds(attempt)
                logger.info(
                    "%s network error for %s; waiting %ss before retry %s/%s",
                    resource,
                    combination.key,
                    int(wait),
                    attempt + 1,
                    MAX_TRANSIENT_ATTEMPTS,
                )
                await asyncio.sleep(wait)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= MAX_TRANSIENT_ATTEMPTS - 1:
                    logger.warning(
                        "%s %s for %s; giving up after %s attempts",
                        resource,
                        response.status_code,
                        combination.key,
                        MAX_TRANSIENT_ATTEMPTS,
                    )
                    response.raise_for_status()
                wait = _backoff_seconds(attempt, response)
                logger.info(
                    "%s HTTP %s for %s; waiting %ss before retry %s/%s",
                    resource,
                    response.status_code,
                    combination.key,
                    int(wait),
                    attempt + 1,
                    MAX_TRANSIENT_ATTEMPTS,
                )
                await asyncio.sleep(wait)
                continue
            response.raise_for_status()
            return response
        if last_error is not None:
            raise last_error
        msg = f"{resource} failed for {combination.key} after {MAX_TRANSIENT_ATTEMPTS} attempts"
        raise RuntimeError(msg)

    async def _nominatim_area(self, combination: Combination) -> int | None:
        response = await self._request(
            "GET",
            f"{self.config.nominatim_url.rstrip('/')}/search",
            combination,
            "Nominatim",
            params={
                "q": f"{combination.city}, {combination.country}",
                "format": "json",
                "limit": 1,
            },
        )
        results = response.json()
        if not results:
            return None
        first = results[0]
        osm_id = int(first["osm_id"])
        osm_type = str(first.get("osm_type", "relation"))
        return _area_id(osm_id, osm_type)

    async def discover(self, combination: Combination) -> list[BusinessRecord]:
        industry = self.industries.get(combination.industry)
        city = self.cities.get((combination.country, combination.city))
        if industry is None or city is None:
            return []
        area_id = await self._nominatim_area(combination)
        if area_id is None:
            return []
        query = _overpass_query(area_id, industry, self.config.max_results_per_combination)
        if not query:
            return []
        response = await self._request(
            "POST",
            self.config.overpass_url,
            combination,
            "Overpass",
            data={"data": query},
        )
        payload = response.json()
        elements = payload.get("elements") or []
        return records_from_overpass(elements, combination, industry, city)
