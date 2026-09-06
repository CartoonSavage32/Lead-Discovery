from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.config import AppConfig, load_config


@pytest.fixture
def tmp_data(tmp_path: Path) -> Path:
    (tmp_path / "reports").mkdir()
    return tmp_path


@pytest.fixture
def sample_files(tmp_path: Path) -> dict[str, Path]:
    countries = tmp_path / "countries.yaml"
    cities = tmp_path / "cities.yaml"
    industries = tmp_path / "industries.yaml"
    countries.write_text("- {name: India, code: IN}\n- {name: Japan, code: JP}\n", encoding="utf-8")
    cities.write_text(
        "- {name: Mumbai, country: India, location_weight: 0.9}\n"
        "- {name: Tokyo, country: Japan, location_weight: 0.95}\n",
        encoding="utf-8",
    )
    industries.write_text(
        yaml.safe_dump(
            [
                {
                    "name": "bakery",
                    "commercial": True,
                    "osm_tags": [{"key": "shop", "value": "bakery"}],
                },
                {
                    "name": "dentist",
                    "commercial": True,
                    "osm_tags": [{"key": "amenity", "value": "dentist"}],
                },
            ]
        ),
        encoding="utf-8",
    )
    return {"countries": countries, "cities": cities, "industries": industries}


@pytest.fixture
def app_config(tmp_data: Path, sample_files: dict[str, Path]) -> AppConfig:
    config_path = tmp_data / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "timezone": "Asia/Kolkata",
                "report_time": "20:30",
                "minimum_score": 50,
                "data_dir": str(tmp_data),
                "countries_file": str(sample_files["countries"]),
                "cities_file": str(sample_files["cities"]),
                "industries_file": str(sample_files["industries"]),
                "discovery": {"provider": "file", "file_path": str(tmp_data / "biz.json")},
                "audit": {
                    "rate_limit_per_minute": 120,
                    "max_retries": 0,
                },
                "contacts": {"use_playwright_fallback": False},
            }
        ),
        encoding="utf-8",
    )
    return load_config(config_path, data_dir=tmp_data)
