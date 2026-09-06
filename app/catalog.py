from __future__ import annotations

from pathlib import Path

import yaml

from app.models import City, Country, Industry


def _read_yaml_list(path: Path) -> list[object]:
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or []
    if not isinstance(payload, list):
        msg = f"Expected a list in {path}"
        raise ValueError(msg)
    return payload


def load_countries(path: Path) -> list[Country]:
    return [Country.model_validate(item) for item in _read_yaml_list(path)]


def load_cities(path: Path) -> list[City]:
    return [City.model_validate(item) for item in _read_yaml_list(path)]


def load_industries(path: Path) -> list[Industry]:
    return [Industry.model_validate(item) for item in _read_yaml_list(path)]
