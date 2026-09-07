from __future__ import annotations

import re

import dns.resolver

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

SUSPICIOUS_EMAIL_PATTERNS = [
    "kammer",
    "chamber",
    "verband",
    "association",
    "ordre",
    "ordem",
    "guild",
    "federation",
    "directory",
    "listing",
    "yellowpages",
    "chambre",
]

_MX_TIMEOUT_SECONDS = 3.0
_mx_resolver: dns.resolver.Resolver | None = None


def _resolver() -> dns.resolver.Resolver:
    global _mx_resolver
    if _mx_resolver is None:
        resolver = dns.resolver.Resolver()
        resolver.timeout = _MX_TIMEOUT_SECONDS
        resolver.lifetime = _MX_TIMEOUT_SECONDS
        _mx_resolver = resolver
    return _mx_resolver


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


def _looks_organizational(email: str) -> bool:
    local, _, domain = email.partition("@")
    if any(pattern in domain for pattern in SUSPICIOUS_EMAIL_PATTERNS):
        return True
    tokens = [token for token in re.split(r"[._+\-]", local) if token]
    return any(token in SUSPICIOUS_EMAIL_PATTERNS for token in tokens)


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
    if _looks_organizational(email):
        return "organizational"
    return None


_mx_cache: dict[str, bool] = {}


def domain_can_receive_mail(email: str) -> bool:
    """Cheap, free, no-quota check: does this domain have valid MX records?"""
    normalized = normalize_email(email)
    if not normalized or "@" not in normalized:
        return False
    domain = normalized.split("@", 1)[1]
    cached = _mx_cache.get(domain)
    if cached is not None:
        return cached
    try:
        answers = _resolver().resolve(domain, "MX")
        ok = len(answers) > 0
        _mx_cache[domain] = ok
        return ok
    except (
        dns.resolver.NXDOMAIN,
        dns.resolver.NoAnswer,
        dns.resolver.NoNameservers,
    ):
        _mx_cache[domain] = False
        return False
    except Exception:
        # Timeouts and SERVFAIL are transient — do not cache a hard miss.
        return False


def is_deliverable_email(value: str | None) -> bool:
    if not is_usable_email(value):
        return False
    normalized = normalize_email(value)
    if not normalized:
        return False
    return domain_can_receive_mail(normalized)


def first_usable_email(*values: str | None) -> str | None:
    for value in values:
        if is_deliverable_email(value):
            return normalize_email(value)
    return None
