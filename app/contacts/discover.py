from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from app.config import ContactConfig
from app.contacts.email import usable_email_rejection_reason
from app.contacts.extract import EMAIL_RE, candidate_links, extract_contacts
from app.models import Contact, WebsiteSignals
from app.contacts.signals import SLOW_LOAD_SECONDS, check_basic_website_signals

logger = logging.getLogger(__name__)

_CONTACT_DEBUG_REMAINING = 20


@dataclass
class FetchResult:
    html: str
    status_code: int | None
    elapsed: float
    final_url: str
    ok: bool
    source: str


async def _fetch_httpx(client: httpx.AsyncClient, url: str, timeout: float) -> FetchResult:
    started = time.monotonic()
    response = await client.get(url, timeout=timeout, follow_redirects=True)
    elapsed = time.monotonic() - started
    response.raise_for_status()
    return FetchResult(
        html=response.text,
        status_code=response.status_code,
        elapsed=elapsed,
        final_url=str(response.url),
        ok=True,
        source="httpx",
    )


async def _fetch_playwright(url: str, timeout: float) -> FetchResult:
    from playwright.async_api import async_playwright

    started = time.monotonic()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        response = await page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
        content = await page.content()
        final_url = page.url
        status = response.status if response is not None else None
        await browser.close()
    return FetchResult(
        html=content,
        status_code=status,
        elapsed=time.monotonic() - started,
        final_url=final_url,
        ok=True,
        source="playwright",
    )


def _log_email_candidates(website: str, html: str) -> None:
    raw = [match.group(0) for match in EMAIL_RE.finditer(html)]
    rejected: list[str] = []
    accepted: list[str] = []
    for item in raw:
        reason = usable_email_rejection_reason(item)
        if reason:
            rejected.append(f"{item}->{reason}")
        else:
            accepted.append(item)
    logger.info(
        "contact debug %s raw_emails=%s accepted=%s rejected=%s",
        website,
        len(raw),
        accepted[:8],
        rejected[:8],
    )


async def discover_contacts(
    client: httpx.AsyncClient,
    website: str,
    config: ContactConfig,
) -> tuple[list[Contact], WebsiteSignals | None]:
    try:
        return await _discover_contacts(client, website, config)
    except Exception:
        logger.exception("Contact discovery failed for %s", website)
        return [], None


async def _discover_contacts(
    client: httpx.AsyncClient,
    website: str,
    config: ContactConfig,
) -> tuple[list[Contact], WebsiteSignals | None]:
    global _CONTACT_DEBUG_REMAINING
    debug = _CONTACT_DEBUG_REMAINING > 0
    if debug:
        _CONTACT_DEBUG_REMAINING -= 1

    home_fetch: FetchResult | None = None
    try:
        home_fetch = await _fetch_httpx(client, website, config.timeout_seconds)
        if debug:
            logger.info(
                "contact debug %s homepage httpx status=%s elapsed=%.2fs",
                website,
                home_fetch.status_code,
                home_fetch.elapsed,
            )
    except httpx.HTTPError as exc:
        response = getattr(exc, "response", None)
        status = response.status_code if isinstance(response, httpx.Response) else None
        if debug:
            logger.info(
                "contact debug %s homepage httpx failed type=%s status=%s",
                website,
                type(exc).__name__,
                status,
            )
        if not config.use_playwright_fallback:
            return [], None
        try:
            home_fetch = await _fetch_playwright(website, config.timeout_seconds)
            if debug:
                logger.info(
                    "contact debug %s homepage playwright status=%s elapsed=%.2fs",
                    website,
                    home_fetch.status_code,
                    home_fetch.elapsed,
                )
        except Exception as play_exc:
            if debug:
                logger.info(
                    "contact debug %s homepage playwright failed type=%s",
                    website,
                    type(play_exc).__name__,
                )
            return [], None

    home = home_fetch.html
    extra_links = candidate_links(home, website, config.path_keywords)[: config.max_pages - 1]
    if debug:
        logger.info("contact debug %s follow_links=%s", website, extra_links)
        _log_email_candidates(website, home)

    parsed = urlparse(home_fetch.final_url or website)
    signals = check_basic_website_signals(
        home,
        home_fetch.elapsed,
        parsed.scheme == "https",
    )
    signals.status_code = home_fetch.status_code
    signals.final_url = home_fetch.final_url
    if home_fetch.status_code is not None and home_fetch.status_code >= 400:
        signals.fetch_ok = False

    collected: list[Contact] = extract_contacts(home, website, config)

    for page_url in extra_links:
        html = ""
        try:
            page_fetch = await _fetch_httpx(client, page_url, config.timeout_seconds)
            html = page_fetch.html
            if debug:
                logger.info(
                    "contact debug %s followed %s status=%s",
                    website,
                    page_url,
                    page_fetch.status_code,
                )
        except httpx.HTTPError as exc:
            if debug:
                logger.info(
                    "contact debug %s follow %s httpx failed type=%s",
                    website,
                    page_url,
                    type(exc).__name__,
                )
            if config.use_playwright_fallback:
                try:
                    page_fetch = await _fetch_playwright(page_url, config.timeout_seconds)
                    html = page_fetch.html
                except Exception:
                    continue
            else:
                continue
        if debug:
            _log_email_candidates(page_url, html)
        collected.extend(extract_contacts(html, page_url, config))

    unique: list[Contact] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    for contact in collected:
        key = (contact.name, contact.email, contact.phone)
        if key in seen:
            continue
        seen.add(key)
        unique.append(contact)
    if debug:
        emails = [item.email for item in unique if item.email]
        logger.info("contact debug %s extracted_emails=%s", website, emails)
    return unique, signals
