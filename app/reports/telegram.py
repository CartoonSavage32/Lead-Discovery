from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from app.config import TelegramConfig

_MAX_ATTEMPTS = 5


class TelegramClient:
    def __init__(self, client: httpx.AsyncClient, config: TelegramConfig) -> None:
        self.client = client
        self.config = config

    def enabled(self) -> bool:
        return bool(self.config.bot_token and self.config.chat_id)

    def _url(self, method: str) -> str:
        return f"{self.config.api_base.rstrip('/')}/bot{self.config.bot_token}/{method}"

    async def send_message(self, text: str) -> None:
        if not self.enabled():
            msg = "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required"
            raise RuntimeError(msg)
        last_error: Exception | None = None
        delay = 1.0
        for attempt in range(_MAX_ATTEMPTS):
            try:
                response = await self.client.post(
                    self._url("sendMessage"),
                    json={"chat_id": self.config.chat_id, "text": text},
                    timeout=30,
                )
            except httpx.RequestError as exc:
                last_error = exc
                if attempt >= _MAX_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(min(delay, 60.0))
                delay *= 2
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= _MAX_ATTEMPTS - 1:
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else delay
                await asyncio.sleep(min(max(wait, 1.0), 60.0))
                delay *= 2
                continue
            response.raise_for_status()
            return
        if last_error is not None:
            raise last_error

    async def send_document(self, path: Path, caption: str | None = None) -> None:
        if not self.enabled():
            msg = "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required"
            raise RuntimeError(msg)
        last_error: Exception | None = None
        delay = 1.0
        for attempt in range(_MAX_ATTEMPTS):
            try:
                with path.open("rb") as handle:
                    files = {"document": (path.name, handle, "text/csv")}
                    data: dict[str, str] = {"chat_id": self.config.chat_id}
                    if caption:
                        data["caption"] = caption
                    response = await self.client.post(
                        self._url("sendDocument"),
                        data=data,
                        files=files,
                        timeout=60,
                    )
            except httpx.RequestError as exc:
                last_error = exc
                if attempt >= _MAX_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(min(delay, 60.0))
                delay *= 2
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= _MAX_ATTEMPTS - 1:
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else delay
                await asyncio.sleep(min(max(wait, 1.0), 60.0))
                delay *= 2
                continue
            response.raise_for_status()
            return
        if last_error is not None:
            raise last_error
