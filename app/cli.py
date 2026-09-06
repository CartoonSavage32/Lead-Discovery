from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from app.config import load_config
from app.pipeline import LeadApp
from app.reports.telegram import TelegramClient


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def build_app() -> LeadApp:
    load_dotenv()
    config_path = Path(os.environ.get("CONFIG_PATH", "config.yaml"))
    data_dir_env = os.environ.get("DATA_DIR")
    data_dir = Path(data_dir_env) if data_dir_env else None
    config = load_config(config_path, data_dir=data_dir)
    config.telegram.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", config.telegram.bot_token)
    config.telegram.chat_id = os.environ.get("TELEGRAM_CHAT_ID", config.telegram.chat_id)
    state_path = os.environ.get("STATE_PATH")
    if state_path:
        config.state_file = state_path
    return LeadApp(config)


async def cmd_run(app: LeadApp) -> int:
    await app.run_forever()
    return 0


async def cmd_discover(app: LeadApp) -> int:
    combination, records = await app.discover_next()
    if combination is None:
        print("No unprocessed combinations remain.")
        return 0
    print(f"Discovered {len(records)} businesses for {combination.key}")
    return 0


async def cmd_scan(app: LeadApp) -> int:
    leads = await app.scan_records()
    print(f"Scanned {len(leads)} businesses; qualified {sum(1 for item in leads if item.qualified)}")
    return 0


async def cmd_report(app: LeadApp) -> int:
    path = await app.generate_and_send_report()
    print(f"Wrote {path}")
    return 0


async def cmd_test_telegram(app: LeadApp) -> int:
    async with app._client() as client:
        telegram = TelegramClient(client, app.config.telegram)
        await telegram.send_message("Website leads: Telegram test message")
    print("Telegram test message sent")
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    parser = argparse.ArgumentParser(prog="app")
    parser.add_argument(
        "command",
        choices=["run", "discover", "scan", "report", "test-telegram"],
    )
    args = parser.parse_args(argv)
    app = build_app()
    mapping = {
        "run": cmd_run,
        "discover": cmd_discover,
        "scan": cmd_scan,
        "report": cmd_report,
        "test-telegram": cmd_test_telegram,
    }
    return asyncio.run(mapping[args.command](app))
