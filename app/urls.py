from __future__ import annotations

from urllib.parse import quote_plus, urlparse


def google_maps_search_url(industry: str, city: str, country: str) -> str:
    query = quote_plus(f"{industry} {city} {country}")
    return f"https://www.google.com/maps/search/?api=1&query={query}"


def normalize_website(url: str | None) -> str | None:
    if not url:
        return None
    value = url.strip()
    if not value:
        return None
    if value.startswith("//"):
        value = f"https:{value}"
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    parsed = urlparse(value)
    if not parsed.netloc:
        return None
    return value


def domain_from_url(url: str | None) -> str | None:
    normalized = normalize_website(url)
    if not normalized:
        return None
    host = urlparse(normalized).hostname
    if not host:
        return None
    return host.lower().removeprefix("www.")


def business_key(domain: str | None, name: str, city: str, country: str) -> str:
    if domain:
        return f"domain:{domain}"
    slug = "|".join(part.strip().lower() for part in (name, city, country))
    return f"place:{slug}"
