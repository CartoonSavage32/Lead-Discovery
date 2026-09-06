from __future__ import annotations

from datetime import UTC, datetime

from app.config import ScoringConfig
from app.contacts.email import first_usable_email
from app.models import AuditResult, BusinessRecord, Contact, Finding, Lead, ScoreReason

QUALIFIED = "qualified"
REVIEW = "review"
REJECT = "reject"


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _severity_rank(severity: str | None) -> int:
    return {"critical": 3, "high": 3, "error": 3, "warning": 2, "info": 1}.get(
        (severity or "").lower(),
        0,
    )


def is_strong_business(business: BusinessRecord) -> bool:
    if not business.commercial:
        return False
    if business.rating is None or business.rating < 4.0:
        return False
    if business.review_count is None or business.review_count < 25:
        return False
    return business.location_weight >= 0.35


def collect_outreach_email(business: BusinessRecord, contacts: list[Contact]) -> str | None:
    contact_emails = [item.email for item in contacts]
    return first_usable_email(business.email, *contact_emails)


def score_business_quality(
    business: BusinessRecord,
    config: ScoringConfig,
) -> tuple[float, list[ScoreReason]]:
    weights = config.no_website
    reasons: list[ScoreReason] = []

    rating_points = 0.0
    if business.rating is not None:
        rating_points = _clamp((business.rating / max(weights.rating_full_at, 0.1)) * 15)
    reasons.append(
        ScoreReason(
            code="rating",
            message=(
                f"Rated {business.rating:.1f}"
                if business.rating is not None
                else "No public rating"
            ),
            points=rating_points,
            metric=f"rating={business.rating}",
            severity="info",
        )
    )

    review_points = 0.0
    if business.review_count is not None and weights.reviews_full_at:
        review_points = _clamp((business.review_count / weights.reviews_full_at) * 10)
    reasons.append(
        ScoreReason(
            code="reviews",
            message=(
                f"{business.review_count} reviews"
                if business.review_count is not None
                else "No public review count"
            ),
            points=review_points,
            metric=f"reviews={business.review_count}",
            severity="info",
        )
    )

    category_points = 10.0 if business.commercial else 0.0
    reasons.append(
        ScoreReason(
            code="category",
            message="Commercial category" if business.commercial else "Non-commercial category",
            points=category_points,
            metric=business.category or business.industry,
            severity="info",
        )
    )

    location_points = _clamp(business.location_weight * 5)
    reasons.append(
        ScoreReason(
            code="location",
            message=f"{business.city}, {business.country}",
            points=location_points,
            metric=f"{business.city}, {business.country}",
            severity="info",
        )
    )
    return _clamp(sum(item.points for item in reasons)), reasons


def score_contactability(email: str | None) -> tuple[float, list[ScoreReason]]:
    if email:
        return 20.0, [
            ScoreReason(
                code="email",
                message=f"Public business email {email}",
                points=20.0,
                metric=email,
                severity="info",
            )
        ]
    return 0.0, [
        ScoreReason(
            code="email",
            message="No usable public business email",
            points=0.0,
            metric=None,
            severity="warning",
        )
    ]


def _opportunity_from_score(category_score: float | None, poor_below: float) -> float:
    if category_score is None:
        return 0.0
    if category_score >= 90:
        return 0.0
    span = max(poor_below, 1.0)
    return _clamp((poor_below - min(category_score, poor_below)) / span * 100)


def score_website_opportunity(
    audit: AuditResult,
    config: ScoringConfig,
) -> tuple[float, list[ScoreReason], Finding | None, bool]:
    if not audit.found:
        reason = ScoreReason(
            code="no_public_audit",
            message="No public BeaverCheck audit",
            points=0.0,
            metric=None,
            severity="info",
        )
        return 0.0, [reason], None, False

    weights = config.website
    reasons: list[ScoreReason] = []

    if audit.performance is not None:
        perf_opp = _opportunity_from_score(audit.performance, weights.severe_performance_below)
        if perf_opp:
            reasons.append(
                ScoreReason(
                    code="performance",
                    message=f"Performance score {audit.performance:.0f}",
                    points=perf_opp * (weights.performance_weight / 100) * 0.4,
                    metric=f"performance={audit.performance}",
                    severity="critical" if perf_opp >= 70 else "warning" if perf_opp >= 40 else "info",
                )
            )

    if audit.mobile_performance is not None:
        mobile_opp = _opportunity_from_score(audit.mobile_performance, weights.poor_mobile_below)
        if mobile_opp:
            reasons.append(
                ScoreReason(
                    code="mobile",
                    message=f"Mobile performance {audit.mobile_performance:.0f}",
                    points=mobile_opp * (weights.mobile_weight / 100) * 0.4,
                    metric=f"mobile={audit.mobile_performance}",
                    severity="critical"
                    if mobile_opp >= 70
                    else "warning"
                    if mobile_opp >= 40
                    else "info",
                )
            )

    cwv_parts: list[float] = []
    metrics: list[str] = []
    if audit.lcp_ms is not None:
        lcp_opp = _clamp((audit.lcp_ms / weights.lcp_poor_ms) * 100) if weights.lcp_poor_ms else 0
        if audit.lcp_ms >= weights.lcp_poor_ms:
            cwv_parts.append(min(100.0, lcp_opp))
        metrics.append(f"LCP={audit.lcp_ms}ms")
    if audit.cls is not None and audit.cls >= weights.cls_poor:
        cwv_parts.append(_clamp((audit.cls / weights.cls_poor) * 100))
        metrics.append(f"CLS={audit.cls}")
    if audit.tbt_ms is not None and audit.tbt_ms >= weights.tbt_poor_ms:
        cwv_parts.append(_clamp((audit.tbt_ms / weights.tbt_poor_ms) * 100))
        metrics.append(f"TBT={audit.tbt_ms}ms")
    if cwv_parts:
        cwv_opp = sum(cwv_parts) / len(cwv_parts)
        reasons.append(
            ScoreReason(
                code="cwv",
                message="Poor Core Web Vitals",
                points=cwv_opp * (weights.cwv_weight / 100) * 0.4,
                metric=", ".join(metrics) or None,
                severity="critical" if cwv_opp >= 70 else "warning",
            )
        )

    critical_findings = [
        finding
        for finding in audit.findings
        if _severity_rank(finding.severity) >= 3
    ]
    critical_count = audit.findings_count.get("critical", 0) or audit.findings_count.get("high", 0)
    if not critical_count:
        critical_count = len(critical_findings)
    broken_signals = [
        finding
        for finding in audit.findings
        if finding.category
        and finding.category.lower() in {"availability", "broken", "functionality", "runtime"}
    ]
    if broken_signals or critical_findings:
        broken_opp = 100.0 if broken_signals or critical_count else 40.0
        reasons.append(
            ScoreReason(
                code="broken",
                message="Broken functionality or critical findings",
                points=broken_opp * (weights.broken_weight / 100) * 0.4,
                metric=f"critical_findings={critical_count}",
                severity="critical",
            )
        )

    if audit.security_score is not None:
        security_opp = _opportunity_from_score(audit.security_score, weights.security_poor_below)
        if security_opp:
            reasons.append(
                ScoreReason(
                    code="security",
                    message=f"Security score {audit.security_score:.0f}",
                    points=security_opp * (weights.security_weight / 100) * 0.4,
                    metric=f"security={audit.security_score}",
                    severity="critical" if security_opp >= 70 else "warning",
                )
            )
    elif critical_findings and any(
        (item.category or "").lower() == "security" for item in critical_findings
    ):
        reasons.append(
            ScoreReason(
                code="security",
                message="Critical security findings",
                points=80.0 * (weights.security_weight / 100) * 0.4,
                metric="security findings",
                severity="critical",
            )
        )

    if audit.seo is not None:
        seo_opp = _opportunity_from_score(audit.seo, weights.seo_poor_below)
        if seo_opp:
            reasons.append(
                ScoreReason(
                    code="seo",
                    message=f"SEO score {audit.seo:.0f}",
                    points=seo_opp * (weights.seo_weight / 100) * 0.4,
                    metric=f"seo={audit.seo}",
                    severity="warning" if seo_opp >= 50 else "info",
                )
            )

    if audit.accessibility is not None:
        a11y_opp = _opportunity_from_score(audit.accessibility, weights.accessibility_poor_below)
        if a11y_opp:
            reasons.append(
                ScoreReason(
                    code="accessibility",
                    message=f"Accessibility score {audit.accessibility:.0f}",
                    points=a11y_opp * (weights.accessibility_weight / 100) * 0.4,
                    metric=f"accessibility={audit.accessibility}",
                    severity="warning" if a11y_opp >= 50 else "info",
                )
            )

    total = _clamp(sum(reason.points for reason in reasons))
    poor_signals = sum(1 for item in reasons if _severity_rank(item.severity) >= 2)
    clear = bool(critical_findings or broken_signals or poor_signals >= 2 or total >= 18)
    if not reasons:
        reasons.append(
            ScoreReason(
                code="healthy_site",
                message="Website audit shows no clear fixable problems",
                points=0.0,
                metric=f"composite={audit.score}",
                severity="info",
            )
        )
        clear = False
        total = 0.0

    top_finding = None
    if audit.findings:
        ranked = sorted(audit.findings, key=lambda item: _severity_rank(item.severity), reverse=True)
        top_finding = ranked[0]
    return total, reasons, top_finding, clear


def score_no_website_opportunity(
    business: BusinessRecord,
    strong: bool,
) -> tuple[float, list[ScoreReason], bool]:
    if not strong:
        return 0.0, [
            ScoreReason(
                code="no_website",
                message="No website, but the business is not strong enough to pitch",
                points=0.0,
                metric=None,
                severity="info",
            )
        ], False
    points = 32.0
    if business.rating is not None and business.rating >= 4.5:
        points += 4.0
    if business.review_count is not None and business.review_count >= 80:
        points += 4.0
    points = _clamp(points)
    return points, [
        ScoreReason(
            code="no_website",
            message="Established business with no website",
            points=points,
            metric=f"rating={business.rating};reviews={business.review_count}",
            severity="info",
        )
    ], True


def concise_reason(reasons: list[ScoreReason], qualification: str, email: str | None) -> str:
    useful = [item.message for item in reasons if item.points > 0 or item.code in {"email", "no_public_audit", "no_website", "healthy_site"}]
    if qualification == QUALIFIED:
        prefix = "Qualified: "
    elif qualification == REVIEW:
        prefix = "Review: "
    else:
        prefix = "Reject: "
    detail = "; ".join(useful[:3]) if useful else "Insufficient evidence"
    if qualification != QUALIFIED and not email:
        if "email" not in detail.lower():
            detail = f"No usable public business email; {detail}"
    return prefix + detail


def classify_lead(
    *,
    score: float,
    minimum_score: float,
    email: str | None,
    strong: bool,
    website_status: str,
    clear_opportunity: bool,
    commercial: bool,
) -> str:
    if (
        email
        and commercial
        and strong
        and clear_opportunity
        and score >= minimum_score
        and website_status in {"has_website", "no_website"}
    ):
        return QUALIFIED
    if score >= 40 and (email or strong):
        return REVIEW
    return REJECT


def build_lead(
    business: BusinessRecord,
    audit: AuditResult | None,
    config: ScoringConfig,
    minimum_score: float,
    contacts: list[Contact] | None = None,
) -> Lead:
    contacts = contacts or []
    email = collect_outreach_email(business, contacts)
    strong = is_strong_business(business)
    quality, quality_reasons = score_business_quality(business, config)
    contact_points, contact_reasons = score_contactability(email)

    if not business.website:
        website_status = "no_website"
        opportunity, opp_reasons, clear = score_no_website_opportunity(business, strong)
        finding = None
        metric = next((item.metric for item in opp_reasons if item.metric), None)
        used_audit = None
    elif audit is None or not audit.found:
        website_status = "no_public_audit"
        opportunity, opp_reasons, finding, clear = 0.0, [], None, False
        if audit is not None:
            opportunity, opp_reasons, finding, clear = score_website_opportunity(audit, config)
        else:
            opp_reasons = [
                ScoreReason(
                    code="no_public_audit",
                    message="No public BeaverCheck audit",
                    points=0.0,
                    metric=None,
                    severity="info",
                )
            ]
        metric = None
        used_audit = audit
        clear = False
    else:
        website_status = "has_website"
        opportunity, opp_reasons, finding, clear = score_website_opportunity(audit, config)
        metric = next((item.metric for item in opp_reasons if item.metric), None)
        used_audit = audit

    reasons = quality_reasons + contact_reasons + opp_reasons
    score = round(_clamp(quality + contact_points + opportunity), 2)
    qualification = classify_lead(
        score=score,
        minimum_score=minimum_score,
        email=email,
        strong=strong,
        website_status=website_status,
        clear_opportunity=clear,
        commercial=business.commercial,
    )
    return Lead(
        business=business,
        website_status=website_status,
        score=score,
        reasons=reasons,
        audit=used_audit,
        contacts=contacts,
        qualified=qualification == QUALIFIED,
        qualification=qualification,
        qualification_reason=concise_reason(reasons, qualification, email),
        outreach_email=email,
        scored_at=datetime.now(UTC),
        top_finding=finding,
        top_metric=metric,
    )


def score_website(
    business: BusinessRecord,
    audit: AuditResult,
    config: ScoringConfig,
) -> tuple[float, list[ScoreReason], Finding | None, str | None]:
    lead = build_lead(business, audit, config, minimum_score=0)
    return lead.score, lead.reasons, lead.top_finding, lead.top_metric


def score_no_website(
    business: BusinessRecord,
    config: ScoringConfig,
) -> tuple[float, list[ScoreReason], str | None]:
    lead = build_lead(business, None, config, minimum_score=0)
    return lead.score, lead.reasons, lead.top_metric
