from __future__ import annotations

import json
import os
import time
from pathlib import Path

from app.models import AppState

_REPLACE_ATTEMPTS = 6
_REPLACE_BACKOFF_SECONDS = 0.05


def _is_transient_replace_error(exc: OSError) -> bool:
    if isinstance(exc, PermissionError):
        return True
    return getattr(exc, "winerror", None) == 5


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                return
            except OSError as exc:
                if not _is_transient_replace_error(exc) or attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_BACKOFF_SECONDS * (2**attempt))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def load_state(path: Path) -> AppState:
    if not path.exists():
        return AppState()
    raw = json.loads(path.read_text(encoding="utf-8"))
    return AppState.model_validate(raw)


def save_state(path: Path, state: AppState) -> None:
    payload = json.dumps(state.model_dump(mode="json"), indent=2, ensure_ascii=False)
    atomic_write_text(path, payload)


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = load_state(path)

    def persist(self) -> None:
        save_state(self.path, self.state)
