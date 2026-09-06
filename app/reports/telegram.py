from __future__ import annotations

from pathlib import Path

import httpx

from app.config import TelegramConfig


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
        response = await self.client.post(
            self._url("sendMessage"),
            json={"chat_id": self.config.chat_id, "text": text},
            timeout=30,
        )
        response.raise_for_status()

    async def send_document(self, path: Path, caption: str | None = None) -> None:
        if not self.enabled():
            msg = "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required"
            raise RuntimeError(msg)
        with path.open("rb") as handle:
            files = {"document": (path.name, handle, "text/csv")}
            data = {"chat_id": self.config.chat_id}
            if caption:
                data["caption"] = caption
            response = await self.client.post(
                self._url("sendDocument"),
                data=data,
                files=files,
                timeout=60,
            )
        response.raise_for_status()
