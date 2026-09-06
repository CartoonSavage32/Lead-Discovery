from __future__ import annotations

import httpx
import pytest

from app.config import TelegramConfig
from app.reports.telegram import TelegramClient


@pytest.mark.asyncio
async def test_telegram_send_message():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "sendMessage" in str(request.url)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient(
            client,
            TelegramConfig(bot_token="token", chat_id="123", api_base="https://api.telegram.org"),
        )
        await telegram.send_message("hello")


def test_telegram_disabled_without_secrets():
    telegram = TelegramClient(None, TelegramConfig())  # type: ignore[arg-type]
    assert telegram.enabled() is False


@pytest.mark.asyncio
async def test_telegram_document_stops_after_five_failures(tmp_path, monkeypatch: pytest.MonkeyPatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("app.reports.telegram.asyncio.sleep", fake_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500, json={"ok": False})

    path = tmp_path / "leads-2026-09-06-22-00.csv"
    path.write_text("Rank,Score\n", encoding="utf-8")
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        telegram = TelegramClient(
            client,
            TelegramConfig(bot_token="token", chat_id="123", api_base="https://api.telegram.org"),
        )
        with pytest.raises(httpx.HTTPStatusError):
            await telegram.send_document(path, caption="Website leads: 0")
    assert calls["n"] == 5
    assert len(sleeps) == 4
    assert all(item <= 60 for item in sleeps)
