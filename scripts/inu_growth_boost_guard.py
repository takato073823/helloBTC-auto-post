#!/usr/bin/env python3
"""INU 成長ブーストの GitHub Actions 用軽量復旧ガード。"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path


DEFAULT_STATE_PATH = Path(__file__).with_name("inu_growth_boost_state.json")
FOLLOWER_TARGET = 1_000
MAX_AGE_MINUTES = 20


def load_state(path: Path = DEFAULT_STATE_PATH) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _parse_timestamp(value: object) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def needs_recovery(
    state: dict,
    now: dt.datetime | None = None,
    max_age_minutes: int = MAX_AGE_MINUTES,
) -> bool:
    """停止済み・目標達成済み以外で20分以上の欠落だけを補う。"""
    if state.get("stopped"):
        return False
    try:
        if int(state.get("last_follower_count", 0) or 0) >= FOLLOWER_TARGET:
            return False
    except (TypeError, ValueError):
        pass
    checked_at = _parse_timestamp(state.get("checked_at"))
    if not checked_at:
        return True
    moment = now or dt.datetime.now(dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc) - checked_at.astimezone(dt.timezone.utc) >= dt.timedelta(minutes=max_age_minutes)


def emit_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")
    print(f"{name}={value}")


def main() -> int:
    active = needs_recovery(load_state())
    emit_output("needs_recovery", "true" if active else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
