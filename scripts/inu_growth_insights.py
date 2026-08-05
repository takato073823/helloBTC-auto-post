#!/usr/bin/env python3
"""INUの公開済み投稿を日次で振り返り、次の候補選定へ返す。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from inu_editorial_policy import AUTO_SELECTABLE_TOPIC_TYPES
from inu_hourly_dispatcher import load_state
from x_poster import _get_client


SCRIPT_DIR = Path(__file__).resolve().parent
HOURLY_STATE_PATH = SCRIPT_DIR / "inu_hourly_state.json"
OUTPUT_PATH = SCRIPT_DIR / "inu_growth_insights.json"
LOOKBACK_HOURS = 72


def _parse_timestamp(value: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def _metrics(value: Any) -> dict[str, int]:
    raw = getattr(value, "public_metrics", None)
    if raw is None and isinstance(value, dict):
        raw = value.get("public_metrics", {})
    if not isinstance(raw, dict):
        raw = vars(raw) if hasattr(raw, "__dict__") else {}
    return {
        key: int(raw.get(key, 0) or 0)
        for key in ("impression_count", "like_count", "reply_count", "retweet_count", "quote_count")
    }


def _value(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def fetch_own_post_metrics(client: Any, tweet_ids: set[str]) -> dict[str, dict[str, int]]:
    """自アカウントの公開済み投稿だけを読み、外部アカウントを監視しない。"""
    if not tweet_ids:
        return {}
    me = _value(client.get_me(user_auth=True), "data")
    user_id = str(_value(me, "id", ""))
    if not user_id:
        raise RuntimeError("自アカウントを取得できません")
    response = client.get_users_tweets(
        user_id,
        max_results=100,
        tweet_fields=["created_at", "public_metrics"],
        user_auth=True,
    )
    values = _value(response, "data", []) or []
    result: dict[str, dict[str, int]] = {}
    for tweet in values:
        tweet_id = str(_value(tweet, "id", ""))
        if tweet_id in tweet_ids:
            result[tweet_id] = _metrics(tweet)
    return result


def _engagement_per_mille(metrics: dict[str, int]) -> float:
    impressions = max(int(metrics.get("impression_count", 0)), 1)
    actions = (
        int(metrics.get("like_count", 0))
        + 2 * int(metrics.get("reply_count", 0))
        + 2 * int(metrics.get("retweet_count", 0))
        + 2 * int(metrics.get("quote_count", 0))
    )
    return round(actions * 1000 / impressions, 2)


def build_insights(state: dict, metrics_by_id: dict[str, dict[str, int]], now: dt.datetime) -> dict:
    cutoff = now - dt.timedelta(hours=LOOKBACK_HOURS)
    rows: list[dict] = []
    for item in state.get("history", []):
        posted_at = _parse_timestamp(str(item.get("posted_at", "")))
        tweet_id = str(item.get("tweet_id", ""))
        topic_type = str(item.get("topic_type", ""))
        if (
            not posted_at
            or posted_at < cutoff
            or not tweet_id
            or topic_type not in AUTO_SELECTABLE_TOPIC_TYPES
        ):
            continue
        metrics = metrics_by_id.get(tweet_id)
        if not metrics:
            continue
        rows.append(
            {
                "tweet_id": tweet_id,
                "topic_type": topic_type,
                "posted_at": posted_at.isoformat(),
                "impressions": metrics["impression_count"],
                "engagement_per_mille": _engagement_per_mille(metrics),
            }
        )

    by_topic: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["topic_type"]:
            by_topic[row["topic_type"]].append(row)
    topic_summary = [
        {
            "topic_type": topic,
            "posts": len(values),
            "mean_impressions": round(sum(row["impressions"] for row in values) / len(values)),
            "mean_engagement_per_mille": round(
                sum(row["engagement_per_mille"] for row in values) / len(values), 2
            ),
        }
        for topic, values in by_topic.items()
    ]
    topic_summary.sort(
        key=lambda row: (row["mean_engagement_per_mille"], row["mean_impressions"]),
        reverse=True,
    )

    # サンプル1件では偶然の可能性が高い。少なくとも2件の実績がある場合だけ
    # 候補の同率時のタイブレークに使う。
    reliable = [row for row in topic_summary if row["posts"] >= 2]
    preferred = [row["topic_type"] for row in reliable[:2]]
    return {
        "version": 1,
        "generated_at": now.isoformat(),
        "lookback_hours": LOOKBACK_HOURS,
        "measured_posts": len(rows),
        "topic_summary": topic_summary,
        "selection_guidance": {
            "preferred_topics_when_quality_is_equal": preferred,
            "rule": "鮮度・一次情報・読者価値が同等の候補だけで使う。投稿本数を埋める目的では使わない。",
        },
    }


def load_insight_guidance(path: Path = OUTPUT_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    guidance = payload.get("selection_guidance", {})
    return guidance if isinstance(guidance, dict) else {}


def run(args: argparse.Namespace) -> int:
    now = dt.datetime.now(dt.timezone.utc)
    state = load_state(Path(args.state))
    tweet_ids = {
        str(row.get("tweet_id", ""))
        for row in state.get("history", [])
        if str(row.get("tweet_id", ""))
    }
    metrics = fetch_own_post_metrics(_get_client(), tweet_ids) if not args.dry_run else {}
    report = build_insights(state, metrics, now)
    output = Path(args.output)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"measured_posts={report['measured_posts']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="INU投稿の成長インサイトを日次作成")
    parser.add_argument("--state", default=str(HOURLY_STATE_PATH))
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    parser.add_argument("--dry-run", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
