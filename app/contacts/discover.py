from __future__ import annotations

import httpx

from app.config import ContactConfig
from app.contacts.extract import candidate_links, extract_contacts
from app.models import Contact


async def _fetch_httpx(client: httpx.AsyncClient, url: str, timeout: float) -> str:
    response = await client.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.text


async def _fetch_playwright(url: str, timeout: float) -> str:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
        content = await page.content()
        await browser.close()
        return content


async def discover_contacts(
    client: httpx.AsyncClient,
    website: str,
    config: ContactConfig,
) -> list[Contact]:
    pages = [website]
    try:
        home = await _fetch_httpx(client, website, config.timeout_seconds)
    except httpx.HTTPError:
        if not config.use_playwright_fallback:
            return []
        try:
            home = await _fetch_playwright(website, config.timeout_seconds)
        except Exception:
            return []

    pages.extend(candidate_links(home, website, config.path_keywords)[: config.max_pages - 1])
    collected: list[Contact] = extract_contacts(home, website, config)

    for page_url in pages[1:]:
        html = ""
        try:
            html = await _fetch_httpx(client, page_url, config.timeout_seconds)
        except httpx.HTTPError:
            if config.use_playwright_fallback:
                try:
                    html = await _fetch_playwright(page_url, config.timeout_seconds)
                except Exception:
                    continue
            else:
                continue
        collected.extend(extract_contacts(html, page_url, config))

    unique: list[Contact] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    for contact in collected:
        key = (contact.name, contact.email, contact.phone)
        if key in seen:
            continue
        seen.add(key)
        unique.append(contact)
    return unique
