from __future__ import annotations

from datetime import UTC, datetime

from app.config import ScoringConfig
from app.contacts.email import is_usable_email
from app.models import AuditResult, BusinessRecord, Contact, Finding
from app.scoring import QUALIFIED, REJECT, REVIEW, build_lead, is_strong_business
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


def _severe_audit() -> AuditResult:
    return AuditResult(
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
        findings_count={"critical": 1},
        fetched_at=datetime.now(UTC),
    )


def _healthy_audit() -> AuditResult:
    return AuditResult(
        url="https://fast.example",
        domain="fast.example",
        found=True,
        grade="A",
        score=92,
        performance=95,
        mobile_performance=94,
        lcp_ms=1200,
        cls=0.05,
        tbt_ms=80,
        seo=90,
        accessibility=88,
        security_score=91,
        findings=[],
        fetched_at=datetime.now(UTC),
    )


def test_usable_email_rejects_noreply_and_examples():
    assert is_usable_email("info@bakery.test")
    assert is_usable_email("  Hello@Bakery.Test  ")
    assert not is_usable_email("noreply@bakery.test")
    assert not is_usable_email("privacy@example.com")
    assert not is_usable_email("not-an-email")
    assert not is_usable_email(None)


def test_phone_only_cannot_qualify():
    business = _business(rating=4.8, review_count=150, website=None, phone="+91111", email=None)
    lead = build_lead(business, None, ScoringConfig(), minimum_score=50)
    assert lead.qualified is False
    assert lead.qualification == REVIEW
    assert lead.outreach_email is None


def test_weak_no_website_is_rejected():
    business = _business(rating=None, review_count=2, website=None, email="info@weak.test")
    lead = build_lead(business, None, ScoringConfig(), minimum_score=50)
    assert is_strong_business(business) is False
    assert lead.qualification == REJECT
    assert lead.qualified is False


def test_strong_no_website_with_email_qualifies():
    business = _business(rating=4.8, review_count=150, website=None, email="hello@bakery.test")
    lead = build_lead(business, None, ScoringConfig(), minimum_score=50)
    assert lead.website_status == "no_website"
    assert lead.qualified is True
    assert lead.qualification == QUALIFIED
    assert lead.outreach_email == "hello@bakery.test"
    assert lead.qualification_reason
    assert "Qualified" in lead.qualification_reason


def test_osm_email_is_kept_when_usable():
    business = _business(rating=4.6, review_count=80, website=None, email="shop@bakery.test")
    lead = build_lead(business, None, ScoringConfig(), minimum_score=50)
    assert lead.outreach_email == "shop@bakery.test"


def test_website_contact_email_can_qualify_fix_opportunity():
    business = _business(
        website="https://slow.example",
        domain="slow.example",
        rating=4.7,
        review_count=90,
        email=None,
    )
    contacts = [Contact(email="owner@slow.example")]
    lead = build_lead(business, _severe_audit(), ScoringConfig(), 50, contacts=contacts)
    assert lead.website_status == "has_website"
    assert lead.qualified is True
    assert lead.outreach_email == "owner@slow.example"
    assert any(item.code == "performance" for item in lead.reasons)


def test_healthy_website_is_not_a_good_lead():
    business = _business(
        website="https://fast.example",
        domain="fast.example",
        rating=4.9,
        review_count=200,
        email="ok@fast.example",
    )
    lead = build_lead(business, _healthy_audit(), ScoringConfig(), 50)
    assert lead.qualified is False
    assert lead.qualification in {REVIEW, REJECT}
    assert all(item.code != "performance" or item.points == 0 for item in lead.reasons)


def test_no_public_audit_does_not_invent_problems():
    business = _business(website="https://unknown.test", domain="unknown.test", email="a@unknown.test")
    audit = AuditResult(
        url="https://unknown.test",
        domain="unknown.test",
        found=False,
        fetched_at=datetime.now(UTC),
    )
    lead = build_lead(business, audit, ScoringConfig(), minimum_score=50)
    assert lead.website_status == "no_public_audit"
    assert lead.qualified is False
    assert all(item.code != "performance" for item in lead.reasons)
    assert any(item.code == "no_public_audit" for item in lead.reasons)


def test_fabricated_or_junk_email_is_ignored():
    business = _business(
        rating=4.8,
        review_count=120,
        website=None,
        email="noreply@bakery.test",
    )
    lead = build_lead(
        business,
        None,
        ScoringConfig(),
        50,
        contacts=[Contact(email="privacy@example.com")],
    )
    assert lead.outreach_email is None
    assert lead.qualified is False
