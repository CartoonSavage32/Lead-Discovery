from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.models import BusinessRecord, Lead, ScoreReason
from app.pipeline import LeadApp
from app.reports.csv_report import CSV_COLUMNS, render_csv, summarize
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


class RecordingTelegram:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, str | None]] = []

    def enabled(self) -> bool:
        return True

    async def send_message(self, text: str) -> None:
        self.calls.append(("message", text, None))

    async def send_document(self, path: Path, caption: str | None = None) -> None:
        body = path.read_text(encoding="utf-8")
        self.calls.append(("document", body, caption))
        if self.fail:
            request = httpx.Request("POST", "https://api.telegram.org/botx/sendDocument")
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError("Telegram failed", request=request, response=response)


@pytest.mark.asyncio
async def test_hourly_report_sends_one_document_message(app_config):
    app = LeadApp(app_config)
    leads = [_lead("Alpha", 80, "has_website"), _lead("Beta", 40, "no_website")]
    app._hourly_leads = list(leads)
    telegram = RecordingTelegram()
    await app.send_hourly_telegram_report(telegram)
    assert len(telegram.calls) == 1
    kind, body, caption = telegram.calls[0]
    assert kind == "document"
    assert caption == summarize(leads)
    assert "Website leads: 1" in caption
    assert "No-website leads: 1" in caption
    assert "Top opportunity: Alpha" in caption
    assert body.replace("\r\n", "\n") == render_csv(leads).replace("\r\n", "\n")



@pytest.mark.asyncio
async def test_hourly_results_cleared_only_after_successful_send(app_config):
    app = LeadApp(app_config)
    leads = [_lead("Keep", 70, "has_website")]
    app._hourly_leads = list(leads)
    failing = RecordingTelegram(fail=True)
    with pytest.raises(httpx.HTTPStatusError):
        await app.send_hourly_telegram_report(failing)
    assert len(app._hourly_leads) == 1
    assert app._hourly_leads[0].business.name == "Keep"
    assert failing.calls[0][0] == "document"

    succeeding = RecordingTelegram()
    await app.send_hourly_telegram_report(succeeding)
    assert app._hourly_leads == []
    assert len(succeeding.calls) == 1
    assert succeeding.calls[0][0] == "document"
    assert succeeding.calls[0][2] == summarize(leads)


@pytest.mark.asyncio
async def test_empty_hourly_report_sends_headers_and_zero_caption(app_config):
    app = LeadApp(app_config)
    telegram = RecordingTelegram()
    await app.send_hourly_telegram_report(telegram)
    assert len(telegram.calls) == 1
    kind, body, caption = telegram.calls[0]
    assert kind == "document"
    header = body.strip().splitlines()[0]
    for column in CSV_COLUMNS:
        assert column in header
    assert body.replace("\r\n", "\n") == render_csv([]).replace("\r\n", "\n")
    assert caption == (
        "Website leads: 0\nNo-website leads: 0\nTop opportunity: n/a"
    )
    assert app._hourly_leads == []
