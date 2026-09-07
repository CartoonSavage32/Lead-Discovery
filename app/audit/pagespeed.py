from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.audit.beavercheck import missing_audit
from app.config import AuditConfig
from app.models import AuditResult, Finding
from app.urls import domain_from_url, normalize_website

logger = logging.getLogger(__name__)

PAGESPEED_REPORT_URL = "https://pagespeed.web.dev/report?url="
POOR_PERFORMANCE = 50.0
MAX_FINDINGS = 3

_SKIP_AUDIT_IDS = {
    "metrics",
    "diagnostics",
    "performance-budget",
    "timing-budget",
    "screenshot-thumbnails",
    "final-screenshot",
    "full-page-screenshot",
    "network-requests",
    "network-rtt",
    "network-server-latency",
    "main-thread-tasks",
    "script-treemap-data",
    "user-timings",
    "resource-summary",
}

_SKIP_DISPLAY_MODES = {"informative", "manual", "notApplicable", "error"}

_ACTIONABLE_GROUPS = {"diagnostics", "load-opportunities"}

_ISSUE_TITLES = {
    "uses-optimized-images": "Images are slowing page load",
    "modern-image-formats": "Images are not using next-gen formats",
    "uses-responsive-images": "Images are larger than needed",
    "offscreen-images": "Offscreen images are loading too early",
    "unsized-images": "Images have no width or height",
    "efficient-animated-content": "Animated images are slowing page load",
    "unused-javascript": "Unused JavaScript is slowing page load",
    "unused-css-rules": "Unused CSS is slowing page load",
    "render-blocking-resources": "Render-blocking resources delay first paint",
    "unminified-javascript": "JavaScript is not minified",
    "unminified-css": "CSS is not minified",
    "uses-text-compression": "Text compression is missing",
    "uses-long-cache-ttl": "Static assets are not cached long enough",
    "server-response-time": "Server response is too slow",
    "redirects": "Redirects are delaying page load",
    "font-display": "Fonts are delaying text rendering",
    "legacy-javascript": "Legacy JavaScript is slowing page load",
    "bootup-time": "JavaScript execution is too heavy",
    "mainthread-work-breakdown": "Main-thread work is too heavy",
    "third-party-summary": "Third-party scripts are slowing page load",
    "dom-size": "The page DOM is too large",
    "largest-contentful-paint": "Largest Contentful Paint is too slow",
    "cumulative-layout-shift": "Layout shifts are disrupting the page",
    "total-blocking-time": "The main thread is blocked too long",
}

_IMAGE_AUDIT_IDS = {
    "uses-optimized-images",
    "modern-image-formats",
    "uses-responsive-images",
    "offscreen-images",
    "unsized-images",
    "efficient-animated-content",
    "lcp-lazy-loaded",
}


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _score_100(value: Any) -> float | None:
    parsed = _as_float(value)
    if parsed is None:
        return None
    return parsed * 100 if parsed <= 1.0 else parsed


def _category_score(categories: dict[str, Any], name: str) -> float | None:
    block = categories.get(name)
    if not isinstance(block, dict):
        return None
    return _score_100(block.get("score"))


def _audit_numeric(audits: dict[str, Any], name: str) -> float | None:
    block = audits.get(name)
    if not isinstance(block, dict):
        return None
    return _as_float(block.get("numericValue"))


def _report_url(url: str) -> str:
    return f"{PAGESPEED_REPORT_URL}{quote(url, safe='')}"


def _failed(audit: dict[str, Any]) -> bool:
    mode = str(audit.get("scoreDisplayMode") or "")
    if mode in _SKIP_DISPLAY_MODES:
        return False
    score = _as_float(audit.get("score"))
    if score is None:
        return mode in {"metricSavings", "binary"} and _savings_ms(audit) > 0
    return score < 1.0


def _savings_ms(audit: dict[str, Any]) -> float:
    details = audit.get("details")
    if isinstance(details, dict):
        savings = _as_float(details.get("overallSavingsMs"))
        if savings:
            return savings
    metric_savings = audit.get("metricSavings")
    if isinstance(metric_savings, dict):
        values = [_as_float(item) or 0.0 for item in metric_savings.values()]
        return max(values) if values else 0.0
    return 0.0


def _issue_title(audit_id: str, audit: dict[str, Any]) -> str:
    mapped = _ISSUE_TITLES.get(audit_id)
    if mapped:
        return mapped
    title = str(audit.get("title") or "").strip()
    return title or audit_id.replace("-", " ")


def _format_seconds(ms: float) -> str:
    seconds = ms / 1000.0
    if seconds >= 10:
        return f"{seconds:.0f}s"
    return f"{seconds:.1f}s"


def _related_metric(audit_id: str, audit: dict[str, Any], audits: dict[str, Any]) -> str | None:
    lcp_ms = _audit_numeric(audits, "largest-contentful-paint")
    cls = _audit_numeric(audits, "cumulative-layout-shift")
    tbt_ms = _audit_numeric(audits, "total-blocking-time")
    savings = audit.get("metricSavings") if isinstance(audit.get("metricSavings"), dict) else {}
    if audit_id in _IMAGE_AUDIT_IDS or "LCP" in savings:
        if lcp_ms is not None:
            return f"Largest Contentful Paint = {_format_seconds(lcp_ms)}"
    if audit_id == "largest-contentful-paint" and lcp_ms is not None:
        return f"Largest Contentful Paint = {_format_seconds(lcp_ms)}"
    if audit_id == "cumulative-layout-shift" and cls is not None:
        return f"Cumulative Layout Shift = {cls:.2f}"
    if audit_id in {"total-blocking-time", "bootup-time", "mainthread-work-breakdown"} and tbt_ms is not None:
        return f"Total Blocking Time = {tbt_ms:.0f}ms"
    display = str(audit.get("displayValue") or "").strip()
    if display:
        return display
    numeric = _as_float(audit.get("numericValue"))
    unit = str(audit.get("numericUnit") or "")
    if numeric is None:
        return None
    if unit == "millisecond":
        return f"{_issue_title(audit_id, audit)} = {_format_seconds(numeric)}"
    if unit == "byte":
        return f"{numeric / 1024:.0f} KiB"
    return None


def _audit_refs(categories: dict[str, Any]) -> list[dict[str, Any]]:
    performance = categories.get("performance")
    if not isinstance(performance, dict):
        return []
    refs = performance.get("auditRefs")
    return [item for item in refs if isinstance(item, dict)] if isinstance(refs, list) else []


def select_actionable_audits(
    categories: dict[str, Any],
    audits: dict[str, Any],
    limit: int = MAX_FINDINGS,
) -> list[tuple[str, dict[str, Any]]]:
    ranked: list[tuple[float, int, str, dict[str, Any]]] = []
    for index, ref in enumerate(_audit_refs(categories)):
        audit_id = str(ref.get("id") or "")
        if not audit_id or audit_id in _SKIP_AUDIT_IDS:
            continue
        group = str(ref.get("group") or "")
        if group not in _ACTIONABLE_GROUPS:
            continue
        audit = audits.get(audit_id)
        if not isinstance(audit, dict) or not _failed(audit):
            continue
        weight = _as_float(ref.get("weight")) or 0.0
        ranked.append((-_savings_ms(audit), -weight, audit_id, audit))
    if not ranked:
        for index, ref in enumerate(_audit_refs(categories)):
            audit_id = str(ref.get("id") or "")
            if audit_id not in {
                "largest-contentful-paint",
                "cumulative-layout-shift",
                "total-blocking-time",
            }:
                continue
            audit = audits.get(audit_id)
            if not isinstance(audit, dict) or not _failed(audit):
                continue
            ranked.append((-_savings_ms(audit), index, audit_id, audit))
    ranked.sort()
    if not ranked:
        for audit_id, audit in audits.items():
            if not isinstance(audit, dict) or audit_id in _SKIP_AUDIT_IDS:
                continue
            if not _failed(audit):
                continue
            if audit_id not in _ISSUE_TITLES and _savings_ms(audit) <= 0:
                continue
            ranked.append((-_savings_ms(audit), 0, audit_id, audit))
        ranked.sort()
    selected: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for _savings, _weight, audit_id, audit in ranked:
        if audit_id in seen:
            continue
        seen.add(audit_id)
        selected.append((audit_id, audit))
        if len(selected) >= limit:
            break
    return selected


def parse_pagespeed_payload(url: str, payload: dict[str, Any]) -> AuditResult:
    lighthouse = payload.get("lighthouseResult")
    if not isinstance(lighthouse, dict):
        return missing_audit(url)
    categories_raw = lighthouse.get("categories")
    audits_raw = lighthouse.get("audits")
    categories: dict[str, Any] = categories_raw if isinstance(categories_raw, dict) else {}
    audits: dict[str, Any] = audits_raw if isinstance(audits_raw, dict) else {}
    performance = _category_score(categories, "performance")
    if performance is None:
        return missing_audit(url)

    lcp_ms = _audit_numeric(audits, "largest-contentful-paint")
    cls = _audit_numeric(audits, "cumulative-layout-shift")
    tbt_ms = _audit_numeric(audits, "total-blocking-time")
    seo = _category_score(categories, "seo")
    accessibility = _category_score(categories, "accessibility")

    if performance >= POOR_PERFORMANCE:
        findings: list[Finding] = []
    else:
        selected = select_actionable_audits(categories, audits)
        if not selected:
            return missing_audit(url)
        findings = []
        for audit_id, audit in selected:
            savings = _savings_ms(audit)
            findings.append(
                Finding(
                    title=_issue_title(audit_id, audit),
                    message=_issue_title(audit_id, audit),
                    severity="critical" if savings >= 1000 else "warning",
                    category="performance",
                    metric=_related_metric(audit_id, audit, audits),
                )
            )

    normalized = normalize_website(url) or url
    report = _report_url(normalized)
    return AuditResult(
        url=normalized,
        domain=domain_from_url(normalized) or urlparse(normalized).netloc,
        found=True,
        score=performance,
        performance=performance,
        mobile_performance=performance,
        lcp_ms=lcp_ms,
        cls=cls,
        tbt_ms=tbt_ms,
        seo=seo,
        accessibility=accessibility,
        findings=findings,
        findings_count={
            "warning": sum(1 for item in findings if item.severity == "warning"),
            "critical": sum(1 for item in findings if item.severity == "critical"),
        },
        report_url=report,
        source="pagespeed",
        fetched_at=datetime.now(UTC),
        raw=payload,
    )


class PageSpeedClient:
    def __init__(self, client: httpx.AsyncClient, config: AuditConfig) -> None:
        self.client = client
        self.config = config

    async def lookup(self, url: str) -> AuditResult:
        key = (self.config.pagespeed.api_key or "").strip()
        if not key:
            return missing_audit(url)
        try:
            response = await self.client.get(
                self.config.pagespeed.base_url,
                params={
                    "url": url,
                    "key": key,
                    "strategy": self.config.pagespeed.strategy,
                },
                timeout=self.config.pagespeed.timeout_seconds,
            )
        except httpx.RequestError:
            logger.warning("PageSpeed request failed for %s", url, exc_info=True)
            return missing_audit(url)
        if response.status_code >= 400:
            logger.warning(
                "PageSpeed HTTP %s for %s",
                response.status_code,
                url,
            )
            return missing_audit(url)
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("error"):
            return missing_audit(url)
        return parse_pagespeed_payload(url, payload)

    async def batch_lookup(self, urls: list[str]) -> dict[str, AuditResult]:
        results: dict[str, AuditResult] = {}
        for item in urls:
            results[item] = await self.lookup(item)
        return results
