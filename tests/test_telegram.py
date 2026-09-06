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
