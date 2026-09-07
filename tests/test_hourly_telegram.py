from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.models import BusinessRecord, Lead, ScoreReason
from app.pipeline import LeadApp
from app.reports.csv_report import CSV_COLUMNS, hourly_summary, render_csv
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
        self.calls: list[tuple[str, str, str | None, str]] = []

    def enabled(self) -> bool:
        return True

    async def send_message(self, text: str) -> None:
        self.calls.append(("message", text, None, ""))

    async def send_document(self, path: Path, caption: str | None = None) -> None:
        body = path.read_text(encoding="utf-8")
        self.calls.append(("document", body, caption, path.name))
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
    kind, body, caption, filename = telegram.calls[0]
    assert kind == "document"
    assert caption == hourly_summary(leads)
    assert "Website leads: 1" in caption
    assert "No-website leads: 1" in caption
    assert "Top opportunity: Alpha" in caption
    assert " - 80" not in caption
    assert filename.startswith("leads-")
    assert filename.endswith(".csv")
    assert filename != "hourly-leads.csv"
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
    assert succeeding.calls[0][2] == hourly_summary(leads)


@pytest.mark.asyncio
async def test_empty_hourly_report_sends_headers_and_zero_caption(app_config):
    app = LeadApp(app_config)
    telegram = RecordingTelegram()
    await app.send_hourly_telegram_report(telegram)
    assert len(telegram.calls) == 1
    kind, body, caption, filename = telegram.calls[0]
    assert kind == "document"
    header = body.strip().splitlines()[0]
    for column in CSV_COLUMNS:
        assert column in header
    assert body.replace("\r\n", "\n") == render_csv([]).replace("\r\n", "\n")
    assert caption == (
        "Website leads: 0\nNo-website leads: 0\nTop opportunity: n/a"
    )
    assert filename != "hourly-leads.csv"
    assert app._hourly_leads == []


@pytest.mark.asyncio
async def test_hourly_filenames_are_unique(app_config, monkeypatch: pytest.MonkeyPatch):
    app = LeadApp(app_config)
    stamps = iter(
        [
            datetime(2026, 9, 6, 16, 30, tzinfo=UTC),
            datetime(2026, 9, 6, 17, 30, tzinfo=UTC),
        ]
    )

    def fake_now() -> datetime:
        return next(stamps)

    monkeypatch.setattr("app.pipeline.utcnow", fake_now)
    first = RecordingTelegram()
    second = RecordingTelegram()
    await app.send_hourly_telegram_report(first)
    await app.send_hourly_telegram_report(second)
    name_one = first.calls[0][3]
    name_two = second.calls[0][3]
    assert name_one == "leads-2026-09-06-22-00.csv"
    assert name_two == "leads-2026-09-06-23-00.csv"
    assert name_one != name_two


@pytest.mark.asyncio
async def test_hourly_report_contains_only_new_leads(app_config):
    app = LeadApp(app_config)
    old = _lead("Old Daily", 99, "has_website")
    app.store.state.leads["old"] = old
    fresh = _lead("New Hour", 80, "has_website")
    app._hourly_leads = [fresh]
    telegram = RecordingTelegram()
    await app.send_hourly_telegram_report(telegram)
    body = telegram.calls[0][1]
    caption = telegram.calls[0][2] or ""
    assert "New Hour" in body
    assert "Old Daily" not in body
    assert "Top opportunity: New Hour" in caption
    assert "Old Daily" not in caption
    assert len(telegram.calls) == 1
    assert telegram.calls[0][0] == "document"


@pytest.mark.asyncio
async def test_hourly_queue_only_qualified_and_dedupes(app_config):
    from app.models import BusinessRecord
    from app.urls import google_maps_search_url

    app = LeadApp(app_config)

    def biz(name: str, **kwargs: object) -> BusinessRecord:
        payload: dict[str, object] = {
            "name": name,
            "industry": "bakery",
            "country": "India",
            "city": "Mumbai",
            "google_maps_url": google_maps_search_url("bakery", "Mumbai", "India"),
            "source": "test",
            "commercial": True,
            "location_weight": 0.9,
        }
        payload.update(kwargs)
        return BusinessRecord.model_validate(payload)

    weak = biz("Weak Shop", website=None, rating=None, review_count=1)
    strong = biz(
        "Strong Shop",
        website=None,
        rating=4.8,
        review_count=120,
        email="hello@gmail.com",
    )
    async with app._client() as client:
        await app._record_lead(weak, None, client)
        await app._record_lead(strong, None, client)
        await app._record_lead(strong, None, client)
    assert [item.business.name for item in app._hourly_leads] == ["Strong Shop"]
    assert app.store.state.leads
