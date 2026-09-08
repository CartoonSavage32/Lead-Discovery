from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class WebsiteScoringConfig(BaseModel):
    performance_weight: float = 20
    mobile_weight: float = 15
    cwv_weight: float = 15
    broken_weight: float = 10
    security_weight: float = 20
    seo_weight: float = 10
    accessibility_weight: float = 10
    severe_performance_below: float = 50
    poor_mobile_below: float = 50
    lcp_poor_ms: float = 4000
    cls_poor: float = 0.25
    tbt_poor_ms: float = 600
    seo_poor_below: float = 55
    accessibility_poor_below: float = 55
    security_poor_below: float = 55


class NoWebsiteScoringConfig(BaseModel):
    rating_weight: float = 35
    review_weight: float = 25
    category_weight: float = 25
    location_weight: float = 15
    rating_full_at: float = 4.5
    reviews_full_at: int = 100


class ScoringConfig(BaseModel):
    website: WebsiteScoringConfig = Field(default_factory=WebsiteScoringConfig)
    no_website: NoWebsiteScoringConfig = Field(default_factory=NoWebsiteScoringConfig)


class PageSpeedConfig(BaseModel):
    api_key: str = ""
    base_url: str = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    strategy: str = "mobile"
    timeout_seconds: float = 60


class AuditConfig(BaseModel):
    refresh_interval_hours: int = 168
    rate_limit_per_minute: int = 25
    batch_size: int = 5
    base_url: str = "https://beavercheck.com/api/v2"
    timeout_seconds: float = 30
    max_retries: int = 4
    backoff_seconds: float = 1.0
    backoff_max_seconds: float = 32.0
    pagespeed: PageSpeedConfig = Field(default_factory=PageSpeedConfig)


class GeoapifyConfig(BaseModel):
    requests_per_second: float = 5
    radius_meters: int = 15000
    daily_credit_budget: int = 3000
    quota_cooldown_minutes: int = 60
    api_key: str = ""


class DiscoveryConfig(BaseModel):
    provider: str = "geoapify"
    max_results_per_combination: int = 50
    timeout_seconds: float = 90
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    overpass_urls: list[str] = Field(default_factory=list)
    user_agent: str = "WebsiteLeads/1.0 (lead research; contact@localhost)"
    file_path: str | None = None
    nominatim_delay_seconds: float = 1.1
    overpass_min_interval_seconds: float = 1.75
    recheck_after_days: float = 21
    max_combinations_per_day: int = 30
    geoapify: GeoapifyConfig = Field(default_factory=GeoapifyConfig)


class ContactConfig(BaseModel):
    path_keywords: list[str] = Field(
        default_factory=lambda: [
            "about",
            "contact",
            "team",
            "leadership",
            "kontakt",
            "equipe",
            "nosotros",
            "chi-siamo",
            "sobre",
        ]
    )
    role_keywords: list[str] = Field(
        default_factory=lambda: [
            "owner",
            "founder",
            "co-founder",
            "director",
            "partner",
            "principal",
            "manager",
            "proprietor",
            "ceo",
            "managing director",
        ]
    )
    max_pages: int = 6
    timeout_seconds: float = 20
    use_playwright_fallback: bool = True


class TelegramConfig(BaseModel):
    bot_token: str = ""
    chat_id: str = ""
    api_base: str = "https://api.telegram.org"


class AppConfig(BaseModel):
    timezone: str = "Asia/Kolkata"
    report_time: str = "20:30"
    minimum_score: float = 52
    data_dir: Path = Path("data")
    loop_delay_seconds: float = 5
    countries_file: str = "data/countries.yaml"
    cities_file: str = "data/cities.yaml"
    industries_file: str = "data/industries.yaml"
    state_file: str | None = None
    audit: AuditConfig = Field(default_factory=AuditConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    contacts: ContactConfig = Field(default_factory=ContactConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    geocode_cache_path: Path = Path("data/geocode_cache.json")

    @property
    def state_path(self) -> Path:
        if self.state_file:
            return Path(self.state_file)
        return self.data_dir / "state.json"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def resolved_geocode_cache_path(self) -> Path:
        path = Path(self.geocode_cache_path)
        if path.is_absolute():
            return path
        parts = path.parts
        if parts and parts[0] == "data":
            return self.data_dir.joinpath(*parts[1:])
        return self.data_dir / path


def _load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_config(config_path: Path, data_dir: Path | None = None) -> AppConfig:
    raw = _load_yaml(config_path) or {}
    if data_dir is not None:
        raw["data_dir"] = str(data_dir)
    return AppConfig.model_validate(raw)


def apply_runtime_env(config: AppConfig) -> AppConfig:
    import os

    config.telegram.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", config.telegram.bot_token)
    config.telegram.chat_id = os.environ.get("TELEGRAM_CHAT_ID", config.telegram.chat_id)
    geo_key = os.environ.get("GEOAPIFY_API_KEY") or os.environ.get("GEO_API_KEY")
    if geo_key:
        config.discovery.geoapify.api_key = geo_key
    psi_key = os.environ.get("PAGESPEED_API_KEY", "")
    if psi_key:
        config.audit.pagespeed.api_key = psi_key
    return config


def require_discovery_credentials(config: AppConfig) -> None:
    provider = config.discovery.provider.lower()
    if provider == "geoapify" and not str(config.discovery.geoapify.api_key or "").strip():
        raise ValueError(
            "GEOAPIFY_API_KEY is required when discovery.provider is geoapify. "
            "Add it to .env and restart."
        )
