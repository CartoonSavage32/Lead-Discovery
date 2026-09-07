from __future__ import annotations

import re

EMAIL_RE = re.compile(r"^[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}$", re.I)

_JUNK_LOCAL_PARTS = {
    "noreply",
    "no-reply",
    "no_reply",
    "donotreply",
    "do-not-reply",
    "mailer-daemon",
    "postmaster",
    "abuse",
    "privacy",
}

_JUNK_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "test.com",
    "localhost",
    "sentry.io",
    "wixpress.com",
}


def normalize_email(value: str | None) -> str | None:
    if not value:
        return None
    email = unescape_email(str(value)).strip().strip(".,;<>()[]").lower()
    if not email or email.startswith("mailto:"):
        email = email.removeprefix("mailto:").split("?", 1)[0].strip()
    return email or None


def unescape_email(value: str) -> str:
    return value.replace("%40", "@")


def is_usable_email(value: str | None) -> bool:
    return usable_email_rejection_reason(value) is None


def usable_email_rejection_reason(value: str | None) -> str | None:
    email = normalize_email(value)
    if not email:
        return "empty"
    if not EMAIL_RE.match(email):
        return "invalid_format"
    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain:
        return "invalid_format"
    if local in _JUNK_LOCAL_PARTS:
        return f"junk_local:{local}"
    if domain in _JUNK_DOMAINS:
        return f"junk_domain:{domain}"
    return None


def first_usable_email(*values: str | None) -> str | None:
    for value in values:
        if is_usable_email(value):
            return normalize_email(value)
    return None
