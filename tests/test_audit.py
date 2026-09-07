from __future__ import annotations

import json

from datetime import UTC, datetime

import httpx
import pytest

from app.audit.beavercheck import BeaverCheckClient, parse_audit_payload
from app.audit.service import AuditService, unique_website_urls
from app.config import AuditConfig
from app.models import AuditResult


class ImmediateLimiter:
    async def acquire(self) -> None:
        return None


class DummyClient:
    def __init__(self) -> None:
        self.batch_calls = 0
        self.requested: list[list[str]] = []

    async def batch_lookup(self, urls: list[str]) -> dict[str, AuditResult]:
        self.batch_calls += 1
        self.requested.append(list(urls))
        results: dict[str, AuditResult] = {}
        for url in urls:
            found = "found" in url
            results[url] = AuditResult(
                url=url,
                domain=url.split("://", 1)[-1],
                found=found,
                score=40 if found else None,
                fetched_at=datetime.now(UTC),
            )
        return results


def test_parse_beavercheck_payload_uses_documented_fields():
    payload = {
        "url": "https://example.com",
        "job_id": "abc",
        "share_url": "https://beavercheck.com/s/x",
        "composite": {"score": 71, "grade": "C"},
        "categories": {
            "performance": {"score": 40},
            "security": {"score": 80},
            "seo": {"score": 60},
            "accessibility": {"score": 46},
        },
        "lighthouse": {
            "mobile": {"performance": 31},
            "desktop": {"performance": 55},
        },
        "cwv": {"lcp_ms": 4200, "cls": 0.12, "tbt_ms": 500},
        "technologies": ["Cloudflare"],
        "findings": [{"title": "CSP missing", "severity": "warning", "category": "security"}],
        "findings_count": {"critical": 4, "warning": 1},
        "links": {"full_report": "https://beavercheck.com/results/abc"},
        "headers": {"content-security-policy": "missing"},
    }
    result = parse_audit_payload("https://example.com", payload)
    assert result.grade == "C"
    assert result.score == 71
    assert result.performance == 40
    assert result.mobile_performance == 31
    assert result.desktop_performance == 55
    assert result.lcp_ms == 4200
    assert result.technologies == ["Cloudflare"]
    assert result.findings[0].title == "CSP missing"
    assert result.findings_count["critical"] == 4
    assert result.security_headers["content-security-policy"] == "missing"
    assert result.beavercheck_url == "https://beavercheck.com/s/x"
    assert result.source == "beavercheck"
    assert result.report_url == "https://beavercheck.com/s/x"


def test_parse_does_not_invent_lighthouse_from_nulls():
    payload = {
        "composite": {"score": 71, "grade": "C"},
        "categories": {"performance": {"score": 0, "grade": ""}},
        "lighthouse": {"mobile": {"performance": None}, "desktop": {"performance": None}},
        "cwv": {"lcp_ms": None, "cls": None, "tbt_ms": None},
        "technologies": ["Cloudflare"],
        "findings_count": {"critical": 4},
    }
    result = parse_audit_payload("https://example.com", payload)
    assert result.performance == 0
    assert result.mobile_performance is None
    assert result.lcp_ms is None
    assert result.beavercheck_url is None


@pytest.mark.asyncio
async def test_thirteen_urls_send_three_batches():
    client = DummyClient()
    service = AuditService(client, AuditConfig(batch_size=5))
    urls = unique_website_urls([f"https://site-{index}.test" for index in range(13)])
    assert len(urls) == 13
    sizes: list[int] = []
    for index in range(0, len(urls), 5):
        chunk = urls[index : index + 5]
        sizes.append(len(chunk))
        await service.audit_batch(chunk)
    assert sizes == [5, 5, 3]
    assert client.batch_calls == 3
    assert [len(chunk) for chunk in client.requested] == [5, 5, 3]


@pytest.mark.asyncio
async def test_batch_client_found_false_is_not_retried():
    calls = {"batch": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/batch"):
            calls["batch"] += 1
            body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"url": url, "found": False, "error": "no public result for url"}
                        for url in body["urls"]
                    ]
                },
            )
        raise AssertionError(f"unexpected request {request.url}")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = BeaverCheckClient(
            http,
            AuditConfig(rate_limit_per_minute=120, max_retries=0),
            ImmediateLimiter(),
        )
        results = await client.batch_lookup(["https://none.test"])
        again = await client.batch_lookup(["https://none.test"])
    assert calls["batch"] == 2
    assert results["https://none.test"].found is False
    assert again["https://none.test"].found is False


@pytest.mark.asyncio
async def test_retries_429_using_retry_after(monkeypatch: pytest.MonkeyPatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.audit.beavercheck.asyncio.sleep", fake_sleep)
    states = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        states["n"] += 1
        if states["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rate"})
        return httpx.Response(
            200,
            json={"results": [{"url": "https://ok.test", "found": False}]},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = BeaverCheckClient(
            http,
            AuditConfig(rate_limit_per_minute=10_000, max_retries=2),
            ImmediateLimiter(),
        )
        results = await client.batch_lookup(["https://ok.test"])
    assert 2.0 in sleeps
    assert results["https://ok.test"].found is False


@pytest.mark.asyncio
async def test_retries_5xx_with_backoff(monkeypatch: pytest.MonkeyPatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.audit.beavercheck.asyncio.sleep", fake_sleep)
    states = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        states["n"] += 1
        if states["n"] < 3:
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://ok.test",
                        "found": True,
                        "data": {
                            "composite": {"score": 10, "grade": "F"},
                            "categories": {},
                            "lighthouse": {},
                            "cwv": {},
                            "technologies": [],
                        },
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = BeaverCheckClient(
            http,
            AuditConfig(rate_limit_per_minute=10_000, max_retries=4, backoff_seconds=1),
            ImmediateLimiter(),
        )
        results = await client.batch_lookup(["https://ok.test"])
    assert sleeps == [1.0, 2.0]
    assert results["https://ok.test"].found is True
    assert results["https://ok.test"].score == 10
    assert "/submit" not in str(states)


class RecordingClient:
    def __init__(self, results: dict[str, AuditResult]) -> None:
        self.results = results
        self.calls: list[list[str]] = []

    async def batch_lookup(self, urls: list[str]) -> dict[str, AuditResult]:
        self.calls.append(list(urls))
        return {url: self.results[url] for url in urls}


def _audit(url: str, *, found: bool, **kwargs: object) -> AuditResult:
    payload: dict[str, object] = {
        "url": url,
        "domain": url.split("://", 1)[-1],
        "found": found,
        "fetched_at": datetime.now(UTC),
    }
    payload.update(kwargs)
    return AuditResult.model_validate(payload)


def _psi_payload(*, performance: float, lcp_ms: float = 1200) -> dict:
    return {
        "lighthouseResult": {
            "categories": {
                "performance": {"score": performance / 100},
                "seo": {"score": 0.8},
                "accessibility": {"score": 0.7},
            },
            "audits": {
                "largest-contentful-paint": {"numericValue": lcp_ms},
                "cumulative-layout-shift": {"numericValue": 0.05},
                "total-blocking-time": {"numericValue": 120},
            },
        }
    }


@pytest.mark.asyncio
async def test_beavercheck_success_skips_pagespeed():
    url = "https://found.test"
    primary = RecordingClient({url: _audit(url, found=True, source="beavercheck", score=71)})
    fallback = RecordingClient({url: _audit(url, found=True, source="pagespeed", performance=10)})
    service = AuditService(primary, AuditConfig(), fallback=fallback)
    results = await service.audit_batch([url])
    assert results[url].source == "beavercheck"
    assert results[url].score == 71
    assert fallback.calls == []


@pytest.mark.asyncio
async def test_beavercheck_miss_uses_pagespeed_fallback():
    url = "https://none.test"
    primary = RecordingClient({url: _audit(url, found=False)})
    fallback = RecordingClient(
        {
            url: _audit(
                url,
                found=True,
                source="pagespeed",
                performance=22,
                report_url="https://pagespeed.web.dev/report?url=https%3A%2F%2Fnone.test",
                findings=[{"title": "Poor PageSpeed performance", "severity": "critical", "category": "performance"}],
            )
        }
    )
    service = AuditService(primary, AuditConfig(), fallback=fallback)
    results = await service.audit_batch([url])
    assert results[url].found is True
    assert results[url].source == "pagespeed"
    assert results[url].performance == 22
    assert results[url].report_url
    assert fallback.calls == [[url]]


@pytest.mark.asyncio
async def test_beavercheck_error_uses_pagespeed_fallback():
    url = "https://down.test"

    class FailingPrimary:
        async def batch_lookup(self, urls: list[str]) -> dict[str, AuditResult]:
            raise httpx.ReadTimeout("beavercheck timeout")

    fallback = RecordingClient(
        {url: _audit(url, found=True, source="pagespeed", performance=40)}
    )
    service = AuditService(FailingPrimary(), AuditConfig(), fallback=fallback)
    results = await service.audit_batch([url])
    assert results[url].source == "pagespeed"
    assert results[url].found is True


@pytest.mark.asyncio
async def test_both_providers_fail_returns_unaudited():
    url = "https://gone.test"
    primary = RecordingClient({url: _audit(url, found=False)})
    fallback = RecordingClient({url: _audit(url, found=False)})
    service = AuditService(primary, AuditConfig(), fallback=fallback)
    results = await service.audit_batch([url])
    assert results[url].found is False
    assert results[url].findings == []
    assert results[url].performance is None


def test_pagespeed_finding_from_poor_performance():
    from app.audit.pagespeed import parse_pagespeed_payload

    result = parse_pagespeed_payload("https://slow.test", _psi_payload(performance=18, lcp_ms=6500))
    assert result.found is True
    assert result.source == "pagespeed"
    assert result.performance == 18
    assert result.mobile_performance == 18
    assert result.lcp_ms == 6500
    assert result.report_url
    titles = {item.title for item in result.findings}
    assert "Poor PageSpeed performance" in titles
    assert "Slow Largest Contentful Paint" in titles


def test_pagespeed_healthy_result_does_not_invent_findings():
    from app.audit.pagespeed import parse_pagespeed_payload

    result = parse_pagespeed_payload("https://fast.test", _psi_payload(performance=92, lcp_ms=1100))
    assert result.found is True
    assert result.findings == []


@pytest.mark.asyncio
async def test_pagespeed_http_error_is_unaudited():
    from app.audit.pagespeed import PageSpeedClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "unavailable"}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = PageSpeedClient(
            http,
            AuditConfig(pagespeed={"api_key": "test-key", "timeout_seconds": 5}),
        )
        result = await client.lookup("https://bad.test")
    assert result.found is False
    assert result.findings == []

