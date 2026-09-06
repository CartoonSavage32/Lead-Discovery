from __future__ import annotations

import json
from pathlib import Path

from app.models import BusinessRecord, Combination
from app.urls import google_maps_search_url


class FileDiscovery:
    """Fixture/replacement provider. Load permitted business records from JSON."""

    def __init__(self, path: Path) -> None:
        self.path = path

    async def discover(self, combination: Combination) -> list[BusinessRecord]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        records = [BusinessRecord.model_validate(item) for item in payload]
        maps_url = google_maps_search_url(
            combination.industry,
            combination.city,
            combination.country,
        )
        matched: list[BusinessRecord] = []
        for record in records:
            if (
                record.country == combination.country
                and record.city == combination.city
                and record.industry == combination.industry
            ):
                matched.append(record.model_copy(update={"google_maps_url": maps_url}))
        return matched
