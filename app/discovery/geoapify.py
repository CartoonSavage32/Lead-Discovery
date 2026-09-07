from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from app.config import AppConfig
from app.discovery.geoapify_categories import categories_for_industry
from app.models import BusinessRecord, City, Combination, Industry
from app.state import atomic_write_text
from app.urls import domain_from_url, google_maps_search_url, normalize_website

logger = logging.getLogger(__name__)

GEOCODING_URL = "https://api.geoapify.com/v1/geocode/search"
PLACES_URL = "https://api.geoapify.com/v2/places"


class GeoapifyQuotaExceeded(Exception):
    def __init__(self, combination: Combination) -> None:
        self.combination = combination
        super().__init__(f"Geoapify daily quota exhausted during {combination.key}")


def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    kept = [
        part
        for part in parsed.query.split("&")
        if not part.lower().startswith("apikey=")
    ]
    return urlunparse(parsed._replace(query="&".join(kept)))


def _body_snippet(response: httpx.Response | None, limit: int = 200) -> str:
    if response is None:
        return ""
    try:
        text = response.text
    except Exception:
        return ""
    return " ".join(text.split())[:limit]


def _log_request_failure(
    resource: str,
    combination: Combination,
    *,
    url: str,
    exc: Exception | None = None,
    response: httpx.Response | None = None,
) -> None:
    status = response.status_code if response is not None else None
    snippet = _body_snippet(response)
    exc_type = type(exc).__name__ if exc is not None else None
    logger.warning(
        "%s request failed for %s url=%s type=%s status=%s body=%r",
        resource,
        combination.key,
        _redact_url(url),
        exc_type,
        status,
        snippet,
    )


def _is_quota_response(response: httpx.Response) -> bool:
    if response.status_code == 429:
        return True
    text = (response.text or "").lower()
    markers = ("quota", "credit", "rate limit", "daily limit", "limit exceeded")
    return response.status_code in {402, 403} and any(marker in text for marker in markers)


def _contact_value(properties: dict[str, Any], *keys: str) -> str | None:
    contact = properties.get("contact")
    if isinstance(contact, dict):
        for key in keys:
            value = contact.get(key)
            if value:
                return str(value)
    for key in keys:
        value = properties.get(key)
        if value:
            return str(value)
    datasource = properties.get("datasource")
    if isinstance(datasource, dict):
        raw = datasource.get("raw")
        if isinstance(raw, dict):
            for key in keys:
                value = raw.get(key) or raw.get(f"contact:{key}")
                if value:
                    return str(value)
    return None


def _address(properties: dict[str, Any]) -> str | None:
    formatted = properties.get("formatted") or properties.get("address_line1")
    if formatted:
        return str(formatted)
    parts = [
        properties.get("housenumber"),
        properties.get("street"),
        properties.get("postcode"),
        properties.get("city"),
    ]
    joined = " ".join(str(part) for part in parts if part)
    return joined or None


def _category_label(properties: dict[str, Any], industry: Industry) -> str:
    categories = properties.get("categories")
    if isinstance(categories, list) and categories:
        return str(categories[0])
    if isinstance(categories, str) and categories:
        return categories
    return industry.name


def records_from_geoapify(
    features: list[dict[str, Any]],
    combination: Combination,
    industry: Industry,
    city: City,
    limit: int,
) -> list[BusinessRecord]:
    maps_url = google_maps_search_url(
        combination.industry,
        combination.city,
        combination.country,
    )
    records: list[BusinessRecord] = []
    seen: set[str] = set()
    for feature in features:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(properties, dict):
            continue
        name = properties.get("name") or properties.get("name:en")
        if not name:
            continue
        website = normalize_website(
            _contact_value(properties, "website", "url", "contact.website")
        )
        source_id = str(properties.get("place_id") or properties.get("osm_id") or name)
        key = website or source_id
        if key in seen:
            continue
        seen.add(key)
        records.append(
            BusinessRecord(
                name=str(name),
                industry=combination.industry,
                country=combination.country,
                city=combination.city,
                website=website,
                domain=domain_from_url(website),
                category=_category_label(properties, industry),
                phone=_contact_value(properties, "phone", "phone_international"),
                email=_contact_value(properties, "email"),
                address=_address(properties),
                google_maps_url=maps_url,
                source="geoapify",
                source_id=source_id,
                commercial=industry.commercial,
                location_weight=city.location_weight,
            )
        )
        if len(records) >= limit:
            break
    return records


def _geocode_payload_to_hit(payload: dict[str, Any]) -> dict[str, Any] | None:
    results = payload.get("results")
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, dict):
            return first
    features = payload.get("features")
    if isinstance(features, list) and features:
        first = features[0]
        if isinstance(first, dict):
            props = first.get("properties")
            if isinstance(props, dict):
                return props
    return None


class GeocodeCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._entries: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._entries = {}
            return
        try:
            import json

            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("Could not read geocode cache %s; starting empty", self.path)
            self._entries = {}
            return
        self._entries = raw if isinstance(raw, dict) else {}

    def _save(self) -> None:
        import json

        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, json.dumps(self._entries, indent=2, ensure_ascii=False))

    def get(self, city: str, country: str) -> dict[str, Any] | None:
        self._load()
        hit = self._entries.get(f"{city}|{country}")
        if not isinstance(hit, dict):
            return None
        if hit.get("lat") is None or hit.get("lon") is None:
            return None
        return hit

    def put(self, city: str, country: str, lat: float, lon: float, place_id: str | None) -> None:
        self._load()
        self._entries[f"{city}|{country}"] = {
            "lat": lat,
            "lon": lon,
            "place_id": place_id,
            "cached_at": datetime.now(UTC).isoformat(),
        }
        self._save()


class GeoapifyDiscovery:
    def __init__(
        self,
        client: httpx.AsyncClient,
        config: AppConfig,
        industries: list[Industry],
        cities: list[City],
        cache: GeocodeCache | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.industries = {item.name: item for item in industries}
        self.cities = {(item.country, item.name): item for item in cities}
        self.cache = cache or GeocodeCache(config.resolved_geocode_cache_path)
        self._last_request_at = 0.0
        self.geocode_calls = 0
        self.places_calls = 0

    async def _respect_rate_limit(self) -> None:
        per_second = max(0.1, self.config.discovery.geoapify.requests_per_second)
        interval = 1.0 / per_second
        elapsed = time.monotonic() - self._last_request_at
        wait = interval - elapsed
        if self._last_request_at > 0 and wait > 0:
            await asyncio.sleep(wait)
        self._last_request_at = time.monotonic()

    async def _get(
        self,
        url: str,
        combination: Combination,
        resource: str,
        params: dict[str, Any],
    ) -> httpx.Response:
        await self._respect_rate_limit()
        try:
            response = await self.client.get(
                url,
                params=params,
                headers={
                    "User-Agent": self.config.discovery.user_agent,
                    "Accept": "application/json",
                },
                timeout=self.config.discovery.timeout_seconds,
            )
        except httpx.RequestError as exc:
            _log_request_failure(resource, combination, url=url, exc=exc)
            raise
        if _is_quota_response(response):
            _log_request_failure(resource, combination, url=str(response.url), response=response)
            raise GeoapifyQuotaExceeded(combination)
        if response.status_code >= 400:
            _log_request_failure(resource, combination, url=str(response.url), response=response)
            response.raise_for_status()
        return response

    async def _geocode(self, combination: Combination) -> dict[str, Any] | None:
        cached = self.cache.get(combination.city, combination.country)
        if cached is not None:
            logger.info(
                "Geoapify geocode cache hit for %s|%s",
                combination.city,
                combination.country,
            )
            return cached
        logger.info(
            "Geoapify geocode cache miss for %s|%s; calling Geocoding API",
            combination.city,
            combination.country,
        )
        response = await self._get(
            GEOCODING_URL,
            combination,
            "Geoapify geocode",
            {
                "text": f"{combination.city}, {combination.country}",
                "type": "city",
                "limit": 1,
                "format": "json",
                "apiKey": self.config.discovery.geoapify.api_key,
            },
        )
        self.geocode_calls += 1
        payload = response.json()
        first = _geocode_payload_to_hit(payload if isinstance(payload, dict) else {})
        if first is None:
            logger.warning("Geoapify geocode returned no city for %s", combination.key)
            return None
        lat = first.get("lat")
        lon = first.get("lon")
        if lat is None or lon is None:
            logger.warning("Geoapify geocode missing coordinates for %s", combination.key)
            return None
        hit = {
            "lat": float(lat),
            "lon": float(lon),
            "place_id": first.get("place_id"),
            "cached_at": datetime.now(UTC).isoformat(),
        }
        self.cache.put(
            combination.city,
            combination.country,
            hit["lat"],
            hit["lon"],
            str(hit["place_id"]) if hit.get("place_id") else None,
        )
        return hit

    async def _places(
        self,
        combination: Combination,
        categories: list[str],
        location: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        place_id = location.get("place_id")
        if place_id:
            spatial = f"place:{place_id}"
        else:
            radius = self.config.discovery.geoapify.radius_meters
            spatial = f"circle:{location['lon']},{location['lat']},{radius}"
        response = await self._get(
            PLACES_URL,
            combination,
            "Geoapify places",
            {
                "categories": ",".join(categories),
                "filter": spatial,
                "limit": limit,
                "apiKey": self.config.discovery.geoapify.api_key,
            },
        )
        self.places_calls += 1
        payload = response.json()
        features = payload.get("features") if isinstance(payload, dict) else None
        return features if isinstance(features, list) else []

    async def discover(self, combination: Combination) -> list[BusinessRecord]:
        industry = self.industries.get(combination.industry)
        city = self.cities.get((combination.country, combination.city))
        if industry is None or city is None:
            return []
        categories = categories_for_industry(industry)
        if not categories:
            logger.warning("No Geoapify categories mapped for industry %s", industry.name)
            return []
        before_geocode = self.geocode_calls
        before_places = self.places_calls
        location = await self._geocode(combination)
        if location is None:
            return []
        limit = max(1, self.config.discovery.max_results_per_combination)
        features = await self._places(combination, categories, location, limit)
        used_geocode = self.geocode_calls - before_geocode
        used_places = self.places_calls - before_places
        logger.info(
            "Geoapify %s credits geocode=%s places=%s total=%s",
            combination.key,
            used_geocode,
            used_places,
            used_geocode + used_places,
        )
        return records_from_geoapify(features, combination, industry, city, limit)
