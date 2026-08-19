#!/usr/bin/env python3
"""INU 毎時投稿の GitHub Actions 用軽量ガード。"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from inu_pipeline_contracts import is_active_reservation


JST = ZoneInfo("Asia/Tokyo")
DEFAULT_STATE_PATH = Path(__file__).with_name("inu_hourly_state.json")


def _positive_int_from_env(name: str, default: int, *, maximum: int) -> int:
    """壊れた環境変数で、復旧ガードが全時間帯に誤作動しないようにする。"""
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if 1 <= value <= maximum else default


def post_interval_hours() -> int:
    """定期投稿の時間間隔。現在は2時間ごと。"""
    interval = _positive_int_from_env("INU_POST_INTERVAL_HOURS", 1, maximum=24)
    return interval if 24 % interval == 0 else 1


def post_start_hour_jst() -> int:
    """周期の起点となるJST時刻。現在は奇数時に投稿する。"""
    return _positive_int_from_env("INU_POST_START_HOUR_JST", 0, maximum=23)


def current_slot(now: dt.datetime | None = None) -> str:
    """定期投稿と同じ JST の1時間枠キーを返す。"""
    moment = now or dt.datetime.now(dt.timezone.utc)
    return f"{moment.astimezone(JST).strftime('%Y-%m-%d-%H')}-a"


def is_scheduled_post_hour(now: dt.datetime | None = None) -> bool:
    """現在のJST時間が、定期投稿と復旧の対象時間かを返す。"""
    moment = now or dt.datetime.now(dt.timezone.utc)
    hour = moment.astimezone(JST).hour
    return (hour - post_start_hour_jst()) % post_interval_hours() == 0


def load_state(path: Path = DEFAULT_STATE_PATH) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def has_hourly_activity(
    state: dict,
    slot: str,
    now: dt.datetime | None = None,
) -> bool:
    """公開済みまたは有効な予約leaseだけを、この時間の活動として扱う。"""
    moment = now or dt.datetime.now(dt.timezone.utc)
    for key in ("history", "posted_slots"):
        for row in state.get(key, []):
            if isinstance(row, dict) and row.get("slot") == slot:
                return True
    for row in state.get("reservations", []):
        if (
            isinstance(row, dict)
            and row.get("slot") == slot
            and is_active_reservation(row, moment)
        ):
            return True

    # 重要ニュース・相場速報が同じJST時間に公開済みなら、その投稿を
    # 定時枠の代わりとして扱う。重要情報の直後に低優先度の定時投稿を
    # 重ねないためであり、個別URL投稿はこの状態ファイルに書き込まれない。
    hour_prefix = slot.removesuffix("-a")
    for row in state.get("history", []):
        if not isinstance(row, dict):
            continue
        posted_at = str(row.get("posted_at", ""))
        try:
            timestamp = dt.datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if timestamp.astimezone(JST).strftime("%Y-%m-%d-%H") == hour_prefix:
            return True
    return False


def emit_output(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")
    print(f"{name}={value}")


def main() -> int:
    now = dt.datetime.now(dt.timezone.utc)
    slot = current_slot(now)
    scheduled = is_scheduled_post_hour(now)
    active = has_hourly_activity(load_state(), slot, now)
    emit_output("slot", slot)
    emit_output("scheduled_slot", "true" if scheduled else "false")
    emit_output("needs_recovery", "true" if scheduled and not active else "false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
