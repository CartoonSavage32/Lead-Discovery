from __future__ import annotations

import re

from bs4 import BeautifulSoup

from app.models import WebsiteSignals

SLOW_LOAD_SECONDS = 3.0


def check_basic_website_signals(
    html: str,
    response_time: float,
    is_https: bool,
) -> WebsiteSignals:
    soup = BeautifulSoup(html or "", "html.parser")
    viewport = soup.find("meta", attrs={"name": re.compile(r"viewport", re.I)})
    title = soup.find("title")
    description = soup.find("meta", attrs={"name": re.compile(r"description", re.I)})
    title_ok = bool(title and title.get_text(strip=True))
    desc_ok = bool(description and str(description.get("content") or "").strip())
    return WebsiteSignals(
        no_https=not is_https,
        no_viewport=viewport is None,
        slow_load=response_time > SLOW_LOAD_SECONDS,
        missing_title_or_description=not (title_ok and desc_ok),
        fetch_ok=True,
        fetch_seconds=response_time,
    )
