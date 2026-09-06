from __future__ import annotations

from typing import Protocol

from app.audit.beavercheck import missing_audit
from app.config import AuditConfig
from app.models import AuditResult
from app.urls import domain_from_url, normalize_website


class AuditClient(Protocol):
    async def batch_lookup(self, urls: list[str]) -> dict[str, AuditResult]: ...


def unique_website_urls(urls: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for url in urls:
        normalized = normalize_website(url)
        if not normalized:
            continue
        domain = domain_from_url(normalized)
        if not domain or domain in seen:
            continue
        seen.add(domain)
        unique.append(normalized)
    return unique


class AuditService:
    def __init__(self, client: AuditClient, config: AuditConfig) -> None:
        self.client = client
        self.config = config

    async def audit_batch(self, urls: list[str]) -> dict[str, AuditResult]:
        if not urls:
            return {}
        batch_size = max(1, min(5, self.config.batch_size))
        if len(urls) > batch_size:
            msg = f"BeaverCheck batch accepts at most {batch_size} URLs"
            raise ValueError(msg)
        batch = await self.client.batch_lookup(urls)
        results: dict[str, AuditResult] = {}
        for url in urls:
            matched = batch.get(url)
            if matched is None:
                for key, value in batch.items():
                    if domain_from_url(key) == domain_from_url(url):
                        matched = value
                        break
            results[url] = matched if matched is not None else missing_audit(url)
        return results
