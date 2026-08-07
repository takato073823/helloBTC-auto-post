#!/usr/bin/env python3
"""INU 毎時投稿の GitHub Actions 用軽量ガード。"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo


JST = ZoneInfo("Asia/Tokyo")
DEFAULT_STATE_PATH = Path(__file__).with_name("inu_hourly_state.json")


def current_slot(now: dt.datetime | None = None) -> str:
    """定期投稿と同じ JST の1時間枠キーを返す。"""
    moment = now or dt.datetime.now(dt.timezone.utc)
    return f"{moment.astimezone(JST).strftime('%Y-%m-%d-%H')}-a"


def load_state(path: Path = DEFAULT_STATE_PATH) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def has_hourly_activity(state: dict, slot: str) -> bool:
    """公開済みまたは予約済みなら、この時間に復旧実行を重ねない。"""
    for key in ("history", "posted_slots", "reservations"):
        for row in state.get(key, []):
            if isinstance(row, dict) and row.get("slot") == slot:
                return True
    return False


def emit_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")
    print(f"{name}={value}")


def main() -> int:
    slot = current_slot()
    active = has_hourly_activity(load_state(), slot)
    emit_output("slot", slot)
    emit_output("needs_recovery", "false" if active else "true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
