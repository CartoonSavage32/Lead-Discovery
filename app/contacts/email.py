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
    email = normalize_email(value)
    if not email or not EMAIL_RE.match(email):
        return False
    local, _, domain = email.partition("@")
    if not local or not domain or "." not in domain:
        return False
    if local in _JUNK_LOCAL_PARTS:
        return False
    if domain in _JUNK_DOMAINS:
        return False
    return True


def first_usable_email(*values: str | None) -> str | None:
    for value in values:
        if is_usable_email(value):
            return normalize_email(value)
    return None
