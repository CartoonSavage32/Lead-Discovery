from __future__ import annotations

from datetime import UTC, datetime

from app.config import ScoringConfig
from app.contacts.email import is_usable_email
from app.models import AuditResult, BusinessRecord, Contact, Finding, WebsiteSignals
from app.scoring import (
    QUALIFIED,
    REJECT,
    REVIEW,
    build_lead,
    check_basic_website_signals,
    is_strong_business,
)
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
    assert not is_usable_email("info@aerztekammer-berlin.de")


def test_phone_only_cannot_qualify():
    business = _business(website=None, phone="+91111", email=None)
    lead = build_lead(business, None, ScoringConfig(), minimum_score=52)
    assert lead.qualified is False
    assert lead.qualification == REVIEW
    assert lead.outreach_email is None
    assert is_strong_business(business) is True


def test_no_contact_signal_is_not_strong():
    business = _business(website=None, email=None, phone=None, rating=None, review_count=None)
    lead = build_lead(business, None, ScoringConfig(), minimum_score=52)
    assert is_strong_business(business) is False
    assert lead.qualification == REJECT
    assert lead.qualified is False


def test_strong_no_website_with_email_qualifies():
    business = _business(website=None, email="hello@gmail.com", phone=None, rating=None, review_count=None)
    lead = build_lead(business, None, ScoringConfig(), minimum_score=52)
    assert lead.website_status == "no_website"
    assert lead.qualified is True
    assert lead.qualification == QUALIFIED
    assert lead.outreach_email == "hello@gmail.com"
    assert lead.qualification_reason
    assert "Qualified" in lead.qualification_reason


def test_osm_email_is_kept_when_usable():
    business = _business(website=None, email="shop@bakery.test")
    lead = build_lead(business, None, ScoringConfig(), minimum_score=52)
    assert lead.outreach_email == "shop@bakery.test"


def test_website_contact_email_can_qualify_fix_opportunity():
    business = _business(
        website="https://slow.example",
        domain="slow.example",
        email=None,
        phone="+91111",
    )
    contacts = [Contact(email="owner@slow.example")]
    lead = build_lead(business, _severe_audit(), ScoringConfig(), 52, contacts=contacts)
    assert lead.website_status == "has_website"
    assert lead.qualified is True
    assert lead.outreach_email == "owner@slow.example"
    assert any(item.code == "performance" for item in lead.reasons)


def test_pagespeed_performance_finding_can_qualify():
    business = _business(
        website="https://slow.example",
        domain="slow.example",
        email="owner@slow.example",
        phone="+91111",
    )
    audit = AuditResult(
        url="https://slow.example",
        domain="slow.example",
        found=True,
        source="pagespeed",
        performance=18,
        mobile_performance=18,
        lcp_ms=6500,
        report_url="https://pagespeed.web.dev/report?url=https%3A%2F%2Fslow.example",
        findings=[
            Finding(
                title="Poor PageSpeed performance",
                severity="critical",
                category="performance",
            )
        ],
        findings_count={"critical": 1},
        fetched_at=datetime.now(UTC),
    )
    lead = build_lead(business, audit, ScoringConfig(), 52)
    assert lead.website_status == "has_website"
    assert lead.qualified is True
    assert lead.audit is not None
    assert lead.audit.source == "pagespeed"
    assert any(item.code == "performance" for item in lead.reasons)


def test_healthy_website_is_not_a_good_lead():
    business = _business(
        website="https://fast.example",
        domain="fast.example",
        email="ok@fast.example",
    )
    lead = build_lead(business, _healthy_audit(), ScoringConfig(), 52)
    assert lead.qualified is False
    assert lead.qualification in {REVIEW, REJECT}
    assert all(item.code != "performance" or item.points == 0 for item in lead.reasons)


def test_no_public_audit_does_not_invent_beavercheck_problems():
    business = _business(website="https://unknown.test", domain="unknown.test", email="a@unknown.test")
    audit = AuditResult(
        url="https://unknown.test",
        domain="unknown.test",
        found=False,
        fetched_at=datetime.now(UTC),
    )
    lead = build_lead(business, audit, ScoringConfig(), minimum_score=52)
    assert lead.website_status == "no_public_audit"
    assert lead.qualified is False
    assert all(item.code != "performance" for item in lead.reasons)
    assert any(item.code == "no_public_audit" for item in lead.reasons)


def test_no_public_audit_qualifies_with_homepage_signals():
    business = _business(website="https://unknown.test", domain="unknown.test", email="a@unknown.test")
    audit = AuditResult(
        url="https://unknown.test",
        domain="unknown.test",
        found=False,
        fetched_at=datetime.now(UTC),
    )
    signals = WebsiteSignals(
        no_https=True,
        no_viewport=True,
        slow_load=False,
        missing_title_or_description=False,
        fetch_ok=True,
    )
    lead = build_lead(
        business,
        audit,
        ScoringConfig(),
        minimum_score=52,
        website_signals=signals,
    )
    assert lead.website_status == "no_public_audit"
    assert lead.qualified is True
    assert lead.score >= 52
    assert any(item.code == "no_https" for item in lead.reasons)


def test_basic_website_signals_from_html():
    html = "<html><head></head><body>Hello</body></html>"
    signals = check_basic_website_signals(html, response_time=4.2, is_https=False)
    assert signals.no_https is True
    assert signals.no_viewport is True
    assert signals.slow_load is True
    assert signals.missing_title_or_description is True


def test_basic_website_signals_healthy_https_page():
    html = (
        '<html><head><title>Shop</title>'
        '<meta name="viewport" content="width=device-width">'
        '<meta name="description" content="A bakery">'
        "</head><body></body></html>"
    )
    signals = check_basic_website_signals(html, response_time=0.4, is_https=True)
    assert signals.no_https is False
    assert signals.no_viewport is False
    assert signals.slow_load is False
    assert signals.missing_title_or_description is False


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


def test_no_website_custom_domain_email_is_not_clear_opportunity():
    business = _business(website=None, email="info@gustav-hartung.de", phone="+49301")
    lead = build_lead(business, None, ScoringConfig(), minimum_score=52)
    assert lead.website_status == "no_website"
    assert lead.qualified is False
    assert lead.qualification != QUALIFIED
    assert any("email domain suggests" in item.message.lower() for item in lead.reasons)


def test_association_and_undeliverable_emails_never_become_outreach():
    association = _business(website=None, email="info@aerztekammer-berlin.de", phone="+49301")
    missing_mx = _business(
        website=None,
        email="owner@thisdomaindoesnotexist12345.com",
        phone="+49301",
    )
    assert build_lead(association, None, ScoringConfig(), 52).outreach_email is None
    assert build_lead(missing_mx, None, ScoringConfig(), 52).outreach_email is None
    contacts = [Contact(email="info@vet-association.org")]
    scraped = _business(website="https://vet.test", domain="vet.test", email=None, phone="+1")
    lead = build_lead(scraped, None, ScoringConfig(), 52, contacts=contacts)
    assert lead.outreach_email is None

