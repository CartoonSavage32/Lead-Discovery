from __future__ import annotations

from pathlib import Path

import httpx

from app.config import AppConfig
from app.discovery.file_source import FileDiscovery
from app.discovery.osm import OsmDiscovery
from app.models import City, Industry


def build_discovery(
    config: AppConfig,
    client: httpx.AsyncClient,
    industries: list[Industry],
    cities: list[City],
) -> OsmDiscovery | FileDiscovery:
    provider = config.discovery.provider.lower()
    if provider == "file":
        path = Path(config.discovery.file_path or "data/sample_businesses.json")
        return FileDiscovery(path)
    if provider == "osm":
        return OsmDiscovery(client, config.discovery, industries, cities)
    msg = f"Unknown discovery provider: {config.discovery.provider}"
    raise ValueError(msg)
