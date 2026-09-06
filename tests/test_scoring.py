from __future__ import annotations

from datetime import UTC, datetime

from app.config import ScoringConfig
from app.models import AuditResult, BusinessRecord, Finding
from app.scoring import build_lead, score_no_website, score_website
from app.urls import google_maps_search_url


def _business(**kwargs: object) -> BusinessRecord:
    payload: dict[str, object] = {
        "name": "Sunrise Bakery",
        "industry": "bakery",
        "country": "India",
        "city": "Mumbai",
        "google_maps_url": google_maps_search_url("bakery", "Mumbai", "India"),
        "source": "test",
        "commercial": True,
        "location_weight": 0.9,
    }
    payload.update(kwargs)
    return BusinessRecord.model_validate(payload)


def test_website_score_high_for_severe_issues():
    audit = AuditResult(
        url="https://slow.example",
        domain="slow.example",
        found=True,
        grade="F",
        score=20,
        performance=10,
        mobile_performance=8,
        desktop_performance=12,
        lcp_ms=8000,
        cls=0.4,
        tbt_ms=900,
        seo=20,
        accessibility=25,
        security_score=20,
        findings=[Finding(title="Missing HTTPS", severity="critical", category="security")],
        fetched_at=datetime.now(UTC),
    )
    score, reasons, finding, _metric = score_website(
        _business(website="https://slow.example"),
        audit,
        ScoringConfig(),
    )
    assert score >= 55
    assert finding is not None
    assert any(item.code == "security" for item in reasons)


def test_no_website_high_rated_commercial():
    business = _business(rating=4.9, review_count=220, website=None)
    score, reasons, _metric = score_no_website(business, ScoringConfig())
    assert score >= 70
    assert any("rating" in item.code for item in reasons)


def test_build_lead_reasons_and_qualification():
    business = _business(rating=4.8, review_count=150, website=None)
    lead = build_lead(business, None, ScoringConfig(), minimum_score=50)
    assert lead.website_status == "no_website"
    assert lead.qualified
    assert lead.reasons
    assert lead.score == round(lead.score, 2)


def test_no_public_audit_is_not_a_bad_website():
    business = _business(website="https://unknown.test", domain="unknown.test")
    audit = AuditResult(
        url="https://unknown.test",
        domain="unknown.test",
        found=False,
        fetched_at=datetime.now(UTC),
    )
    lead = build_lead(business, audit, ScoringConfig(), minimum_score=50)
    assert lead.website_status == "no_public_audit"
    assert lead.qualified is False
    assert lead.score == 0
    assert all(item.code != "performance" for item in lead.reasons)
    assert lead.reasons[0].code == "no_public_audit"
