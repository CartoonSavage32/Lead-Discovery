from __future__ import annotations

from datetime import UTC, datetime

from app.config import ScoringConfig
from app.models import AuditResult, BusinessRecord, Finding, Lead, ScoreReason


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def _opportunity_from_score(category_score: float | None, poor_below: float) -> float:
    if category_score is None:
        return 0.0
    if category_score >= 90:
        return 0.0
    span = max(poor_below, 1.0)
    return _clamp((poor_below - min(category_score, poor_below)) / span * 100)


def score_website(
    _business: BusinessRecord,
    audit: AuditResult,
    config: ScoringConfig,
) -> tuple[float, list[ScoreReason], Finding | None, str | None]:
    weights = config.website
    reasons: list[ScoreReason] = []

    if audit.performance is not None:
        perf_opp = _opportunity_from_score(audit.performance, weights.severe_performance_below)
        reasons.append(
            ScoreReason(
                code="performance",
                message=(
                    "Severe performance problems" if perf_opp >= 50 else "Performance opportunity"
                ),
                points=perf_opp * (weights.performance_weight / 100),
                metric=f"performance={audit.performance}",
                severity="critical" if perf_opp >= 70 else "warning" if perf_opp >= 40 else "info",
            )
        )

    if audit.mobile_performance is not None:
        mobile_opp = _opportunity_from_score(audit.mobile_performance, weights.poor_mobile_below)
        reasons.append(
            ScoreReason(
                code="mobile",
                message="Poor mobile performance" if mobile_opp >= 50 else "Mobile performance",
                points=mobile_opp * (weights.mobile_weight / 100),
                metric=f"mobile={audit.mobile_performance}",
                severity=(
                    "critical" if mobile_opp >= 70 else "warning" if mobile_opp >= 40 else "info"
                ),
            )
        )

    cwv_parts: list[float] = []
    metrics: list[str] = []
    if audit.lcp_ms is not None:
        lcp_opp = _clamp((audit.lcp_ms / weights.lcp_poor_ms) * 100) if weights.lcp_poor_ms else 0
        if audit.lcp_ms >= weights.lcp_poor_ms:
            cwv_parts.append(min(100.0, lcp_opp))
        else:
            cwv_parts.append(min(100.0, lcp_opp * 0.4))
        metrics.append(f"LCP={audit.lcp_ms}ms")
    if audit.cls is not None:
        cls_opp = _clamp((audit.cls / weights.cls_poor) * 100) if weights.cls_poor else 0
        cwv_parts.append(min(100.0, cls_opp))
        metrics.append(f"CLS={audit.cls}")
    if audit.tbt_ms is not None:
        tbt_opp = _clamp((audit.tbt_ms / weights.tbt_poor_ms) * 100) if weights.tbt_poor_ms else 0
        cwv_parts.append(min(100.0, tbt_opp))
        metrics.append(f"TBT={audit.tbt_ms}ms")
    if cwv_parts:
        cwv_opp = sum(cwv_parts) / len(cwv_parts)
        reasons.append(
            ScoreReason(
                code="cwv",
                message="Poor Core Web Vitals" if cwv_opp >= 50 else "Core Web Vitals",
                points=cwv_opp * (weights.cwv_weight / 100),
                metric=", ".join(metrics) or None,
                severity="critical" if cwv_opp >= 70 else "warning" if cwv_opp >= 40 else "info",
            )
        )

    critical_count = audit.findings_count.get("critical", 0)
    critical_findings = [
        finding
        for finding in audit.findings
        if (finding.severity or "").lower() in {"critical", "high", "error"}
    ]
    if not critical_count:
        critical_count = len(critical_findings)
    broken_signals = [
        finding
        for finding in audit.findings
        if finding.category
        and finding.category.lower() in {"availability", "broken", "functionality", "runtime"}
    ]
    if broken_signals or critical_count:
        broken_opp = 100.0 if broken_signals else 40.0
        reasons.append(
            ScoreReason(
                code="broken",
                message="Broken functionality or critical failures"
                if broken_opp >= 80
                else "Functionality findings",
                points=broken_opp * (weights.broken_weight / 100),
                metric=f"critical_findings={critical_count}",
                severity="critical" if broken_opp >= 80 else "info",
            )
        )

    if audit.security_score is not None or critical_count:
        security_opp = _opportunity_from_score(audit.security_score, weights.security_poor_below)
        if critical_count:
            security_opp = max(security_opp, 80.0)
        reasons.append(
            ScoreReason(
                code="security",
                message="Critical security findings"
                if security_opp >= 70
                else "Security opportunity",
                points=security_opp * (weights.security_weight / 100),
                metric=f"security={audit.security_score}",
                severity=(
                    "critical"
                    if security_opp >= 70
                    else "warning"
                    if security_opp >= 40
                    else "info"
                ),
            )
        )

    if audit.seo is not None:
        seo_opp = _opportunity_from_score(audit.seo, weights.seo_poor_below)
        reasons.append(
            ScoreReason(
                code="seo",
                message="Significant SEO problems" if seo_opp >= 50 else "SEO opportunity",
                points=seo_opp * (weights.seo_weight / 100),
                metric=f"seo={audit.seo}",
                severity="warning" if seo_opp >= 50 else "info",
            )
        )

    if audit.accessibility is not None:
        a11y_opp = _opportunity_from_score(audit.accessibility, weights.accessibility_poor_below)
        reasons.append(
            ScoreReason(
                code="accessibility",
                message="Significant accessibility problems"
                if a11y_opp >= 50
                else "Accessibility opportunity",
                points=a11y_opp * (weights.accessibility_weight / 100),
                metric=f"accessibility={audit.accessibility}",
                severity="warning" if a11y_opp >= 50 else "info",
            )
        )

    total = _clamp(sum(reason.points for reason in reasons))
    top_finding = None
    if audit.findings:
        ranked = sorted(
            audit.findings,
            key=lambda item: {"critical": 3, "high": 3, "warning": 2, "info": 1}.get(
                (item.severity or "").lower(), 0
            ),
            reverse=True,
        )
        top_finding = ranked[0]
    top_metric = next((reason.metric for reason in reasons if reason.metric), None)
    return total, reasons, top_finding, top_metric


def score_no_website(
    business: BusinessRecord,
    config: ScoringConfig,
) -> tuple[float, list[ScoreReason], str | None]:
    weights = config.no_website
    reasons: list[ScoreReason] = []

    rating_opp = 0.0
    if business.rating is not None:
        rating_opp = _clamp((business.rating / weights.rating_full_at) * 100)
    reasons.append(
        ScoreReason(
            code="rating",
            message="High rating without a website"
            if rating_opp >= 80
            else "Rating unavailable or moderate",
            points=rating_opp * (weights.rating_weight / 100),
            metric=f"rating={business.rating}",
            severity="info",
        )
    )

    review_opp = 0.0
    if business.review_count is not None and weights.reviews_full_at:
        review_opp = _clamp((business.review_count / weights.reviews_full_at) * 100)
    reasons.append(
        ScoreReason(
            code="reviews",
            message="High review count"
            if review_opp >= 60
            else "Review count unavailable or low",
            points=review_opp * (weights.review_weight / 100),
            metric=f"reviews={business.review_count}",
            severity="info",
        )
    )

    category_opp = 100.0 if business.commercial else 20.0
    reasons.append(
        ScoreReason(
            code="category",
            message="Commercial category" if business.commercial else "Non-commercial category",
            points=category_opp * (weights.category_weight / 100),
            metric=business.category or business.industry,
            severity="info",
        )
    )

    location_opp = _clamp(business.location_weight * 100)
    reasons.append(
        ScoreReason(
            code="location",
            message="Location demand",
            points=location_opp * (weights.location_weight / 100),
            metric=f"{business.city}, {business.country}",
            severity="info",
        )
    )

    total = _clamp(sum(reason.points for reason in reasons))
    top_metric = next((reason.metric for reason in reasons if reason.metric), None)
    return total, reasons, top_metric


def build_lead(
    business: BusinessRecord,
    audit: AuditResult | None,
    config: ScoringConfig,
    minimum_score: float,
) -> Lead:
    if not business.website:
        score, reasons, metric = score_no_website(business, config)
        return Lead(
            business=business,
            website_status="no_website",
            score=round(score, 2),
            reasons=reasons,
            audit=None,
            qualified=score >= minimum_score,
            scored_at=datetime.now(UTC),
            top_finding=None,
            top_metric=metric,
        )
    if audit is None or not audit.found:
        reasons = [
            ScoreReason(
                code="no_public_audit",
                message="No public BeaverCheck audit",
                points=0,
                metric=None,
                severity="info",
            )
        ]
        return Lead(
            business=business,
            website_status="no_public_audit",
            score=0,
            reasons=reasons,
            audit=audit,
            qualified=False,
            scored_at=datetime.now(UTC),
            top_finding=None,
            top_metric=None,
        )
    score, reasons, finding, metric = score_website(business, audit, config)
    return Lead(
        business=business,
        website_status="has_website",
        score=round(score, 2),
        reasons=reasons,
        audit=audit,
        qualified=score >= minimum_score,
        scored_at=datetime.now(UTC),
        top_finding=finding,
        top_metric=metric,
    )
