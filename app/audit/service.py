from __future__ import annotations

import logging
from typing import Protocol

from app.audit.beavercheck import missing_audit
from app.config import AuditConfig
from app.models import AuditResult
from app.urls import domain_from_url, normalize_website

logger = logging.getLogger(__name__)


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


def _usable(audit: AuditResult | None) -> bool:
    return audit is not None and audit.found


class AuditService:
    def __init__(
        self,
        client: AuditClient,
        config: AuditConfig,
        fallback: AuditClient | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.fallback = fallback

    async def audit_batch(self, urls: list[str]) -> dict[str, AuditResult]:
        if not urls:
            return {}
        batch_size = max(1, min(5, self.config.batch_size))
        if len(urls) > batch_size:
            msg = f"BeaverCheck batch accepts at most {batch_size} URLs"
            raise ValueError(msg)
        try:
            batch = await self.client.batch_lookup(urls)
        except Exception:
            logger.warning("BeaverCheck batch failed; trying PageSpeed fallback", exc_info=True)
            batch = {}
        results: dict[str, AuditResult] = {}
        for url in urls:
            matched = batch.get(url)
            if matched is None:
                for key, value in batch.items():
                    if domain_from_url(key) == domain_from_url(url):
                        matched = value
                        break
            if _usable(matched):
                results[url] = matched
                continue
            results[url] = await self._fallback_or_missing(url, matched)
        return results

    async def _fallback_or_missing(
        self,
        url: str,
        current: AuditResult | None,
    ) -> AuditResult:
        if self.fallback is None:
            return current if current is not None else missing_audit(url)
        try:
            alt_batch = await self.fallback.batch_lookup([url])
            alt = alt_batch.get(url)
            if _usable(alt):
                return alt
        except Exception:
            logger.warning("PageSpeed fallback failed for %s", url, exc_info=True)
        return current if current is not None else missing_audit(url)
