from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.config import ContactConfig
from app.contacts.email import is_usable_email
from app.models import Contact

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
JSON_LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)


def _clean_phone(value: str) -> str | None:
    digits = re.sub(r"[^\d+]", "", value)
    if len(re.sub(r"\D", "", digits)) < 8:
        return None
    return value.strip()


def _looks_like_person_name(value: str) -> bool:
    parts = [part for part in re.split(r"\s+", value.strip()) if part]
    if len(parts) < 2 or len(parts) > 5:
        return False
    if any(len(part) < 2 for part in parts):
        return False
    if EMAIL_RE.search(value) or PHONE_RE.search(value):
        return False
    return all(part[0].isalpha() for part in parts)


def _extract_json_ld_contacts(html: str, page_url: str, role_keywords: list[str]) -> list[Contact]:
    contacts: list[Contact] = []
    for match in JSON_LD_RE.finditer(html):
        raw = unescape(match.group(1).strip())
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        blocks = payload if isinstance(payload, list) else [payload]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            graph = block.get("@graph")
            nodes = graph if isinstance(graph, list) else [block]
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_type = str(node.get("@type") or "")
                if "Person" not in node_type:
                    continue
                name = node.get("name")
                job = str(node.get("jobTitle") or node.get("role") or "")
                if not name or not job:
                    continue
                if not any(keyword.lower() in job.lower() for keyword in role_keywords):
                    continue
                email = node.get("email")
                telephone = node.get("telephone")
                contacts.append(
                    Contact(
                        name=str(name),
                        role=job,
                        email=str(email) if is_usable_email(str(email) if email else None) else None,
                        phone=str(telephone) if telephone else None,
                        source_url=page_url,
                        evidence=f"json-ld:{job}",
                    )
                )
    return contacts


def extract_contacts(html: str, page_url: str, config: ContactConfig) -> list[Contact]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    contacts: list[Contact] = _extract_json_ld_contacts(html, page_url, config.role_keywords)

    emails = {
        match.group(0).lower()
        for match in EMAIL_RE.finditer(text)
        if is_usable_email(match.group(0))
    }
    phones: set[str] = set()
    for match in PHONE_RE.finditer(text):
        cleaned = _clean_phone(match.group(0))
        if cleaned:
            phones.add(cleaned)

    mailto: list[str] = []
    tel: list[str] = []
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "")
        if href.lower().startswith("mailto:"):
            mailto.append(href.replace("mailto:", "").split("?")[0])
        elif href.lower().startswith("tel:"):
            tel.append(href.replace("tel:", ""))
    emails.update(item.lower() for item in mailto if is_usable_email(item))
    for item in tel:
        cleaned = _clean_phone(item)
        if cleaned:
            phones.add(cleaned)

    labeled_pattern = re.compile(
        rf"(?i:({'|'.join(re.escape(k) for k in config.role_keywords)}))"
        r"\s*[:\-–]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})"
    )
    for match in labeled_pattern.finditer(text):
        role = match.group(1)
        name = match.group(2).strip()
        if not _looks_like_person_name(name):
            continue
        contacts.append(
            Contact(
                name=name,
                role=role.title(),
                source_url=page_url,
                evidence=match.group(0)[:160],
            )
        )

    if not contacts and (emails or phones):
        contacts.append(
            Contact(
                email=next(iter(sorted(emails)), None),
                phone=next(iter(sorted(phones)), None),
                source_url=page_url,
                evidence="page contact details without a labeled person",
            )
        )
    elif contacts:
        first_email = next(iter(sorted(emails)), None)
        first_phone = next(iter(sorted(phones)), None)
        for contact in contacts:
            if not contact.email:
                contact.email = first_email
            if not contact.phone:
                contact.phone = first_phone

    unique: list[Contact] = []
    seen: set[tuple[str | None, str | None, str | None, str | None]] = set()
    for contact in contacts:
        key = (contact.name, contact.role, contact.email, contact.phone)
        if key in seen:
            continue
        seen.add(key)
        unique.append(contact)
    return unique


def candidate_links(html: str, base_url: str, keywords: list[str]) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []
    seen: set[str] = set()
    base_host = urlparse(base_url).hostname
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "")
        label = anchor.get_text(" ", strip=True).lower()
        absolute = urljoin(base_url, href)
        host = urlparse(absolute).hostname
        if not host or not base_host or host.removeprefix("www.") != base_host.removeprefix("www."):
            continue
        haystack = f"{href.lower()} {label}"
        if not any(keyword.lower() in haystack for keyword in keywords):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        found.append(absolute)
    return found
