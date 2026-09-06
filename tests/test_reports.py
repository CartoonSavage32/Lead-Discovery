from __future__ import annotations

from datetime import UTC, datetime

from app.models import BusinessRecord, Lead, ScoreReason
from app.reports.csv_report import CSV_COLUMNS, render_csv, sort_leads, summarize
from app.urls import google_maps_search_url


def _lead(name: str, score: float, status: str) -> Lead:
    return Lead(
        business=BusinessRecord(
            name=name,
            industry="bakery",
            country="India",
            city="Mumbai",
            website="https://a.test" if status == "has_website" else None,
            google_maps_url=google_maps_search_url("bakery", "Mumbai", "India"),
            source="test",
        ),
        website_status=status,
        score=score,
        reasons=[ScoreReason(code="x", message="reason", points=score, metric="m")],
        qualified=True,
        scored_at=datetime.now(UTC),
        top_metric="m",
    )


def test_csv_sorted_and_columns():
    csv_text = render_csv([_lead("Low", 10, "no_website"), _lead("High", 90, "has_website")])
    lines = csv_text.strip().splitlines()
    header = lines[0]
    for column in CSV_COLUMNS:
        assert column in header
    assert lines[1].startswith("1,90.00,High")
    assert "Low" in lines[2]


def test_summary_counts():
    leads = [
        _lead("A", 80, "has_website"),
        _lead("B", 70, "no_website"),
        _lead("C", 90, "has_website"),
    ]
    text = summarize(leads)
    assert "Website leads: 2" in text
    assert "No-website leads: 1" in text
    assert "Top opportunity: C - 90" in text
    assert sort_leads(leads)[0].business.name == "C"
