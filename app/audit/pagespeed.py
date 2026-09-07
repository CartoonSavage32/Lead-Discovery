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
CRITICAL_PERFORMANCE = 30.0
POOR_LCP_MS = 4000.0
POOR_CLS = 0.25
POOR_TBT_MS = 600.0


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

    findings: list[Finding] = []
    if performance < POOR_PERFORMANCE:
        findings.append(
            Finding(
                title="Poor PageSpeed performance",
                message=f"Mobile performance score {performance:.0f}",
                severity="critical" if performance < CRITICAL_PERFORMANCE else "warning",
                category="performance",
            )
        )
    if lcp_ms is not None and lcp_ms >= POOR_LCP_MS:
        findings.append(
            Finding(
                title="Slow Largest Contentful Paint",
                message=f"LCP {lcp_ms:.0f}ms",
                severity="warning",
                category="performance",
            )
        )
    if cls is not None and cls >= POOR_CLS:
        findings.append(
            Finding(
                title="High Cumulative Layout Shift",
                message=f"CLS {cls:.2f}",
                severity="warning",
                category="performance",
            )
        )
    if tbt_ms is not None and tbt_ms >= POOR_TBT_MS:
        findings.append(
            Finding(
                title="High Total Blocking Time",
                message=f"TBT {tbt_ms:.0f}ms",
                severity="warning",
                category="performance",
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
        for url in urls:
            results[url] = await self.lookup(url)
        return results
