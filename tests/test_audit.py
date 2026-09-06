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
