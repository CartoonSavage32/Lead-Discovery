from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class OsmTag(BaseModel):
    key: str
    value: str


class Industry(BaseModel):
    name: str
    commercial: bool = True
    osm_tags: list[OsmTag] = Field(default_factory=list)


class Country(BaseModel):
    name: str
    code: str | None = None


class City(BaseModel):
    name: str
    country: str
    location_weight: float = 0.5


class Combination(BaseModel):
    country: str
    city: str
    industry: str

    @property
    def key(self) -> str:
        return f"{self.country}|{self.city}|{self.industry}"


class Contact(BaseModel):
    name: str | None = None
    role: str | None = None
    email: str | None = None
    phone: str | None = None
    source_url: str | None = None
    evidence: str | None = None


class ScoreReason(BaseModel):
    code: str
    message: str
    points: float
    metric: str | None = None
    severity: str | None = None


class Finding(BaseModel):
    title: str | None = None
    message: str | None = None
    severity: str | None = None
    category: str | None = None


class AuditResult(BaseModel):
    url: str
    domain: str
    found: bool = False
    grade: str | None = None
    score: float | None = None
    performance: float | None = None
    mobile_performance: float | None = None
    desktop_performance: float | None = None
    lcp_ms: float | None = None
    cls: float | None = None
    tbt_ms: float | None = None
    seo: float | None = None
    accessibility: float | None = None
    security_score: float | None = None
    security_headers: dict[str, Any] = Field(default_factory=dict)
    technologies: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    findings_count: dict[str, int] = Field(default_factory=dict)
    beavercheck_url: str | None = None
    job_id: str | None = None
    fetched_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class BusinessRecord(BaseModel):
    name: str
    industry: str
    country: str
    city: str
    website: str | None = None
    domain: str | None = None
    rating: float | None = None
    review_count: int | None = None
    category: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    google_maps_url: str
    source: str
    source_id: str | None = None
    commercial: bool = True
    location_weight: float = 0.5


class Lead(BaseModel):
    business: BusinessRecord
    website_status: str
    score: float
    reasons: list[ScoreReason] = Field(default_factory=list)
    audit: AuditResult | None = None
    contacts: list[Contact] = Field(default_factory=list)
    qualified: bool = False
    scored_at: datetime
    top_finding: Finding | None = None
    top_metric: str | None = None


class DomainState(BaseModel):
    domain: str
    first_seen: datetime
    last_audit_at: datetime | None = None
    last_score: float | None = None
    website: str | None = None


class CombinationState(BaseModel):
    key: str
    country: str
    city: str
    industry: str
    completed_at: datetime
    business_count: int = 0


class ReportHistoryEntry(BaseModel):
    generated_at: datetime
    path: str
    qualified_count: int
    website_leads: int
    no_website_leads: int
    sent: bool = False


class AppState(BaseModel):
    processed_combinations: dict[str, CombinationState] = Field(default_factory=dict)
    in_progress_combination: str | None = None
    domains: dict[str, DomainState] = Field(default_factory=dict)
    businesses: dict[str, BusinessRecord] = Field(default_factory=dict)
    leads: dict[str, Lead] = Field(default_factory=dict)
    report_history: list[ReportHistoryEntry] = Field(default_factory=list)
    last_daily_report_date: str | None = None
