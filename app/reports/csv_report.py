from __future__ import annotations

import csv
from datetime import datetime
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

from app.models import Lead
from app.state import atomic_write_text

CSV_COLUMNS = [
    "Rank",
    "Score",
    "Business",
    "Industry",
    "Country",
    "City",
    "Website",
    "Website Status",
    "Rating",
    "Review Count",
    "BeaverCheck URL",
    "Reason",
    "Finding",
    "Severity",
    "Metric",
    "Decision Maker",
    "Role",
    "Email",
    "Phone",
    "Google Maps URL",
]


def sort_leads(leads: list[Lead]) -> list[Lead]:
    return sorted(leads, key=lambda lead: lead.score, reverse=True)


def lead_row(rank: int, lead: Lead) -> dict[str, str]:
    contact = lead.contacts[0] if lead.contacts else None
    finding = lead.top_finding
    reason = lead.qualification_reason or "; ".join(
        f"{item.message} ({item.points:.1f})" for item in lead.reasons
    )
    return {
        "Rank": str(rank),
        "Score": f"{lead.score:.2f}",
        "Business": lead.business.name,
        "Industry": lead.business.industry,
        "Country": lead.business.country,
        "City": lead.business.city,
        "Website": lead.business.website or "",
        "Website Status": lead.website_status,
        "Rating": "" if lead.business.rating is None else str(lead.business.rating),
        "Review Count": ""
        if lead.business.review_count is None
        else str(lead.business.review_count),
        "BeaverCheck URL": (
            (lead.audit.report_url or lead.audit.beavercheck_url) if lead.audit else ""
        )
        or "",
        "Reason": reason,
        "Finding": (finding.title or finding.message or "") if finding else "",
        "Severity": (finding.severity or "") if finding else "",
        "Metric": lead.top_metric or ((finding.metric if finding else None) or ""),
        "Decision Maker": (contact.name if contact else "") or "",
        "Role": (contact.role if contact else "") or "",
        "Email": lead.outreach_email or "",
        "Phone": (contact.phone if contact else lead.business.phone) or "",
        "Google Maps URL": lead.business.google_maps_url,
    }


def render_csv(leads: list[Lead]) -> str:
    ranked = sort_leads(leads)
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for index, lead in enumerate(ranked, start=1):
        writer.writerow(lead_row(index, lead))
    return buffer.getvalue()


def write_csv(path: Path, leads: list[Lead]) -> Path:
    atomic_write_text(path, render_csv(leads))
    return path


def report_filename(now: datetime, timezone: str) -> str:
    local = now.astimezone(ZoneInfo(timezone))
    return f"leads-{local.strftime('%Y-%m-%d')}.csv"


def hourly_report_filename(now: datetime, timezone: str) -> str:
    local = now.astimezone(ZoneInfo(timezone))
    return f"leads-{local.strftime('%Y-%m-%d-%H-%M')}.csv"


def summarize(leads: list[Lead]) -> str:
    website = sum(1 for lead in leads if lead.website_status == "has_website")
    none = sum(1 for lead in leads if lead.website_status == "no_website")
    ranked = sort_leads(leads)
    top = ranked[0] if ranked else None
    top_line = f"{top.business.name} - {top.score:.0f}" if top else "n/a"
    return (
        f"Website leads: {website}\n"
        f"No-website leads: {none}\n"
        f"Top opportunity: {top_line}"
    )


def hourly_summary(leads: list[Lead]) -> str:
    website = sum(1 for lead in leads if lead.website_status == "has_website")
    none = sum(1 for lead in leads if lead.website_status == "no_website")
    ranked = sort_leads(leads)
    top = ranked[0] if ranked else None
    top_line = top.business.name if top else "n/a"
    return (
        f"Website leads: {website}\n"
        f"No-website leads: {none}\n"
        f"Top opportunity: {top_line}"
    )
