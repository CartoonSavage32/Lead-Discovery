from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from app.config import AuditConfig
from app.models import AuditResult, Finding
from app.urls import domain_from_url, normalize_website


class SupportsAcquire(Protocol):
    async def acquire(self) -> None: ...


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tech_names(raw: Any) -> list[str]:
    if not raw:
        return []
    names: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]))
    return names


def _parse_findings(raw: Any) -> list[Finding]:
    findings: list[Finding] = []
    if not isinstance(raw, list):
        return findings
    for item in raw:
        if not isinstance(item, dict):
            continue
        findings.append(
            Finding(
                title=item.get("title") or item.get("name"),
                message=item.get("message") or item.get("description"),
                severity=item.get("severity"),
                category=item.get("category"),
            )
        )
    return findings


def _findings_count(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    counts: dict[str, int] = {}
    for key, value in raw.items():
        parsed = _as_int(value)
        if parsed is not None:
            counts[str(key)] = parsed
    return counts


def _category_score(categories: dict[str, Any], name: str) -> float | None:
    block = categories.get(name) or {}
    if not isinstance(block, dict):
        return None
    return _as_float(block.get("score"))


def parse_audit_payload(url: str, payload: dict[str, Any]) -> AuditResult:
    composite_raw = payload.get("composite")
    categories_raw = payload.get("categories")
    lighthouse_raw = payload.get("lighthouse")
    cwv_raw = payload.get("cwv")
    links_raw = payload.get("links")
    composite: dict[str, Any] = composite_raw if isinstance(composite_raw, dict) else {}
    categories: dict[str, Any] = categories_raw if isinstance(categories_raw, dict) else {}
    lighthouse: dict[str, Any] = lighthouse_raw if isinstance(lighthouse_raw, dict) else {}
    mobile_raw = lighthouse.get("mobile")
    desktop_raw = lighthouse.get("desktop")
    mobile: dict[str, Any] = mobile_raw if isinstance(mobile_raw, dict) else {}
    desktop: dict[str, Any] = desktop_raw if isinstance(desktop_raw, dict) else {}
    cwv: dict[str, Any] = cwv_raw if isinstance(cwv_raw, dict) else {}
    links: dict[str, Any] = links_raw if isinstance(links_raw, dict) else {}
    headers = payload.get("headers")
    if not isinstance(headers, dict):
        headers = payload.get("security_headers")
    if not isinstance(headers, dict):
        headers = {}
    share_url = payload.get("share_url")
    report_url = links.get("full_report")
    beavercheck_url = str(share_url) if share_url else str(report_url) if report_url else None
    normalized = normalize_website(url) or url
    grade_raw = composite.get("grade")
    grade = str(grade_raw) if grade_raw else None
    return AuditResult(
        url=normalized,
        domain=domain_from_url(normalized) or urlparse(normalized).netloc,
        found=True,
        grade=grade,
        score=_as_float(composite.get("score")),
        performance=_category_score(categories, "performance"),
        mobile_performance=_as_float(mobile.get("performance")),
        desktop_performance=_as_float(desktop.get("performance")),
        lcp_ms=_as_float(cwv.get("lcp_ms")),
        cls=_as_float(cwv.get("cls")),
        tbt_ms=_as_float(cwv.get("tbt_ms")),
        seo=_category_score(categories, "seo"),
        accessibility=_category_score(categories, "accessibility"),
        security_score=_category_score(categories, "security"),
        security_headers=headers,
        technologies=_tech_names(payload.get("technologies")),
        findings=_parse_findings(payload.get("findings")),
        findings_count=_findings_count(payload.get("findings_count")),
        beavercheck_url=beavercheck_url,
        report_url=beavercheck_url,
        source="beavercheck",
        job_id=payload.get("job_id"),
        fetched_at=datetime.now(UTC),
        raw=payload,
    )


def missing_audit(url: str) -> AuditResult:
    normalized = normalize_website(url) or url
    return AuditResult(
        url=normalized,
        domain=domain_from_url(normalized) or urlparse(normalized).netloc,
        found=False,
        fetched_at=datetime.now(UTC),
    )


class BeaverCheckClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        config: AuditConfig,
        limiter: SupportsAcquire,
    ) -> None:
        self.client = client
        self.config = config
        self.limiter = limiter

    def _url(self, path: str) -> str:
        return f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"

    async def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        delay = self.config.backoff_seconds
        last_error: Exception | None = None
        attempts = min(5, max(1, self.config.max_retries + 1))
        for attempt in range(attempts):
            await self.limiter.acquire()
            try:
                response = await self.client.request(
                    method,
                    self._url(path),
                    timeout=self.config.timeout_seconds,
                    **kwargs,
                )
            except httpx.RequestError as exc:
                last_error = exc
                if attempt >= attempts - 1:
                    raise
                await asyncio.sleep(min(delay, 60.0, self.config.backoff_max_seconds))
                delay *= 2
                continue
            if response.status_code == 429:
                if attempt >= attempts - 1:
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After")
                wait = (
                    float(retry_after)
                    if retry_after
                    else min(delay, self.config.backoff_max_seconds)
                )
                await asyncio.sleep(min(max(wait, 1.0), 60.0))
                delay *= 2
                continue
            if response.status_code >= 500:
                if attempt >= attempts - 1:
                    response.raise_for_status()
                await asyncio.sleep(min(delay, 60.0, self.config.backoff_max_seconds))
                delay *= 2
                continue
            return response
        if last_error is not None:
            raise last_error
        msg = "BeaverCheck request failed after retries"
        raise RuntimeError(msg)

    async def findings(self, job_id: str) -> list[Finding]:
        response = await self._send("GET", f"results/{job_id}/findings")
        if response.status_code == 404:
            return []
        if response.status_code == 400:
            return []
        response.raise_for_status()
        payload = response.json()
        items = payload.get("findings") if isinstance(payload, dict) else None
        if items is None and isinstance(payload, dict):
            items = payload.get("results")
        return _parse_findings(items)

    async def batch_lookup(self, urls: list[str]) -> dict[str, AuditResult]:
        results: dict[str, AuditResult] = {}
        if not urls:
            return results
        if len(urls) > 5:
            msg = "BeaverCheck batch accepts at most 5 URLs"
            raise ValueError(msg)
        response = await self._send("POST", "batch", json={"urls": urls})
        response.raise_for_status()
        payload = response.json()
        items = payload.get("results") if isinstance(payload, dict) else []
        by_url: dict[str, dict[str, Any]] = {}
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("url"):
                    by_url[str(item["url"])] = item
        for url in urls:
            item = by_url.get(url)
            if item is None:
                results[url] = missing_audit(url)
                continue
            if item.get("found") is True:
                body = item.get("data")
                if not isinstance(body, dict):
                    body = item.get("result")
                if isinstance(body, dict):
                    results[url] = parse_audit_payload(url, body)
                elif item.get("composite"):
                    results[url] = parse_audit_payload(url, item)
                else:
                    results[url] = missing_audit(url)
            else:
                results[url] = missing_audit(url)
        return results
