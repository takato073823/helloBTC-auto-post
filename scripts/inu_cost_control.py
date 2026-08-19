"""INU自動投稿の従量課金APIを、JST日次回数で強制制限する。"""

from __future__ import annotations

import datetime as dt
import os


JST = dt.timezone(dt.timedelta(hours=9))
DEFAULT_LIMITS = {
    # xAI X Searchは4時間ごとの深い探索だけ。公式X API/RSSはこの制限外。
    "grok_x_search": 6,
    # 検証済み事実の文章化だけに使うため、2時間枠の最大12回。
    "grok_editorial": 12,
    # OpenAI Web SearchはxAIで発見した有力候補の一次資料確認に限定する。
    "openai_web_search": 8,
}
ENV_KEYS = {
    "grok_x_search": "INU_MAX_GROK_X_SEARCHES_PER_DAY",
    "grok_editorial": "INU_MAX_GROK_EDITORIALS_PER_DAY",
    "openai_web_search": "INU_MAX_OPENAI_WEB_SEARCHES_PER_DAY",
}


def daily_limit(action: str) -> int:
    if action not in DEFAULT_LIMITS:
        raise ValueError(f"未知のAPI費用区分です: {action}")
    raw = os.environ.get(ENV_KEYS[action], str(DEFAULT_LIMITS[action]))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{ENV_KEYS[action]} は整数で指定してください") from exc
    return max(0, min(value, 96))


def _usage_bucket(state: dict, now: dt.datetime) -> dict:
    day = now.astimezone(JST).date().isoformat()
    current = state.get("api_usage")
    if not isinstance(current, dict) or current.get("jst_date") != day:
        current = {"jst_date": day, "counts": {}, "claims": []}
        state["api_usage"] = current
    current.setdefault("counts", {})
    current.setdefault("claims", [])
    return current


def claim_api_call(state: dict, action: str, now: dt.datetime) -> bool:
    """APIを呼ぶ直前に枠を確保する。失敗時も課金され得るため返却しない。"""
    bucket = _usage_bucket(state, now)
    counts = bucket["counts"]
    used = max(0, int(counts.get(action, 0) or 0))
    limit = daily_limit(action)
    if used >= limit:
        return False
    counts[action] = used + 1
    bucket["claims"].append({"action": action, "claimed_at": now.astimezone(dt.timezone.utc).isoformat()})
    bucket["claims"] = bucket["claims"][-96:]
    return True


def usage_snapshot(state: dict, now: dt.datetime) -> dict:
    bucket = _usage_bucket(state, now)
    return {
        action: {
            "used": int(bucket["counts"].get(action, 0) or 0),
            "limit": daily_limit(action),
        }
        for action in DEFAULT_LIMITS
    }
