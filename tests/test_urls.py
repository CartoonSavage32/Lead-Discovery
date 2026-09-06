from __future__ import annotations

from app.urls import domain_from_url, google_maps_search_url, normalize_website


def test_google_maps_url_uses_industry_city_country():
    url = google_maps_search_url("real estate", "São Paulo", "Brazil")
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "real" in url
    assert "Paulo" in url or "S" in url
    assert "Brazil" in url


def test_no_hardcoded_query_shape():
    bakery = google_maps_search_url("bakery", "Mumbai", "India")
    dentist = google_maps_search_url("dentist", "Tokyo", "Japan")
    assert bakery != dentist
    assert "bakery" in bakery
    assert "dentist" in dentist


def test_normalize_and_domain():
    assert domain_from_url("https://www.Example.com/path") == "example.com"
    assert normalize_website("example.com") == "https://example.com"
    assert normalize_website("") is None
