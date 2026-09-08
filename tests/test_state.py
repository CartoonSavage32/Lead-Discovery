from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import AppConfig
from app.models import AppState, CombinationState
from app.scheduler import report_due
from app.state import StateStore, load_state, save_state


def test_canonicalize_rekeys_processed_combinations(tmp_path: Path):
    path = tmp_path / "state.json"
    payload = {
        "processed_combinations": {
            "India|Mumbai": {
                "key": "India|Mumbai",
                "country": "India",
                "city": "Mumbai",
                "industry": "bakery",
                "completed_at": datetime.now(UTC).isoformat(),
                "business_count": 1,
            }
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_state(path)
    assert "India|Mumbai" not in loaded.processed_combinations
    assert "India|Mumbai|bakery" in loaded.processed_combinations
    assert loaded.processed_combinations["India|Mumbai|bakery"].key == "India|Mumbai|bakery"
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.state.processed_combinations["India|Mumbai|bakery"] = CombinationState(
        key="India|Mumbai|bakery",
        country="India",
        city="Mumbai",
        industry="bakery",
        completed_at=datetime.now(UTC),
        business_count=3,
    )
    store.persist()
    assert path.exists()
    assert list(tmp_path.glob("*.tmp")) == []
    loaded = load_state(path)
    assert "India|Mumbai|bakery" in loaded.processed_combinations


def test_atomic_write_leaves_no_tmp_file(tmp_path: Path):
    path = tmp_path / "state.json"
    save_state(path, AppState())
    save_state(path, AppState(last_daily_report_date="2026-09-06"))
    assert path.exists()
    assert list(tmp_path.glob("*.tmp")) == []
    loaded = load_state(path)
    assert loaded.last_daily_report_date == "2026-09-06"


def test_report_due_once_per_day():
    config = AppConfig(timezone="Asia/Kolkata", report_time="20:30")
    tz = ZoneInfo("Asia/Kolkata")
    before = datetime(2026, 9, 6, 20, 0, tzinfo=tz)
    after = datetime(2026, 9, 6, 20, 30, tzinfo=tz)
    later = datetime(2026, 9, 6, 21, 0, tzinfo=tz)
    assert report_due(before, config, None) is False
    assert report_due(after, config, None) is True
    assert report_due(later, config, "2026-09-06") is False
    assert report_due(later, config, "2026-09-05") is True
