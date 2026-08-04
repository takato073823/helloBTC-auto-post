#!/usr/bin/env python3
"""確認済みの画像付き投稿から、アカウント全体で毎時1件だけ公開する。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

from inu_content_types import get_content_policy
from inu_live_post import publish_test_item, validate_test_item


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
CATALOG_PATH = SCRIPT_DIR / "inu_approved_posts.json"
STATE_PATH = SCRIPT_DIR / "inu_hourly_state.json"
JST = ZoneInfo("Asia/Tokyo")
MAX_HISTORY = 1000


def _parse_time(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("日時にはタイムゾーンが必要です")
    return parsed.astimezone(dt.timezone.utc)


def load_catalog(path: Path) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    posts = payload.get("posts", [])
    if not isinstance(posts, list):
        raise ValueError("承認済み投稿カタログの形式が不正です")
    ids = [str(item.get("id", "")) for item in posts]
    if len(ids) != len(set(ids)):
        raise ValueError("承認済み投稿IDが重複しています")
    return posts


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "posted_slots": [], "posted_ids": []}
    state = json.loads(path.read_text(encoding="utf-8"))
    state.setdefault("version", 1)
    state.setdefault("posted_slots", [])
    state.setdefault("posted_ids", [])
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def slot_key(now: dt.datetime | None = None) -> str:
    current = now or dt.datetime.now(dt.timezone.utc)
    return current.astimezone(JST).strftime("%Y-%m-%d-%H")


def validate_approved_item(item: dict, now: dt.datetime) -> None:
    required = {"approval_status", "approved_at", "expires_at"}
    missing = sorted(required - set(item))
    if missing:
        raise ValueError(f"承認情報が不足しています: {missing}")
    if item["approval_status"] != "approved":
        raise ValueError("承認済みではありません")
    approved_at = _parse_time(item["approved_at"])
    expires_at = _parse_time(item["expires_at"])
    current = now.astimezone(dt.timezone.utc)
    if approved_at > current:
        raise ValueError("承認日時が未来です")
    if expires_at <= current:
        raise ValueError("投稿候補の有効期限が切れています")
    policy = get_content_policy(item["topic_type"])
    if policy.review_mode == "manual":
        raise ValueError("手動投稿専用の系統です")
    validate_test_item(item)


def select_item(posts: list[dict], state: dict, now: dt.datetime) -> dict | None:
    used = set(state.get("posted_ids", []))
    candidates = []
    for item in posts:
        if item.get("id") in used:
            continue
        try:
            validate_approved_item(item, now)
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
            logger.warning("投稿候補を除外: %s / %s", item.get("id", "?"), exc)
            continue
        policy = get_content_policy(item["topic_type"])
        candidates.append((int(item.get("priority", policy.priority)), item["approved_at"], item))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (-row[0], row[1], row[2]["id"]))
    return candidates[0][2]


def mark_posted(state: dict, item: dict, slot: str, tweet_id: str) -> dict:
    updated = {
        "version": 1,
        "posted_slots": list(state.get("posted_slots", [])),
        "posted_ids": list(state.get("posted_ids", [])),
    }
    updated["posted_slots"].append(
        {"slot": slot, "post_id": item["id"], "tweet_id": str(tweet_id)}
    )
    updated["posted_ids"].append(item["id"])
    updated["posted_slots"] = updated["posted_slots"][-MAX_HISTORY:]
    updated["posted_ids"] = updated["posted_ids"][-MAX_HISTORY:]
    return updated


def run(args: argparse.Namespace) -> int:
    now = dt.datetime.now(dt.timezone.utc)
    current_slot = args.slot or slot_key(now)
    state_path = Path(args.state)
    state = load_state(state_path)
    if any(row.get("slot") == current_slot for row in state["posted_slots"]):
        logger.info("この時間は投稿済みです: %s", current_slot)
        return 0

    item = select_item(load_catalog(Path(args.catalog)), state, now)
    if not item:
        logger.info("現在公開できる承認済み投稿はありません")
        return 0
    logger.info("毎時投稿候補: %s / %s", item["id"], item["topic_type"])
    if not args.live:
        validate_approved_item(item, now)
        logger.info("ドライランのためXには投稿していません")
        return 0

    if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        raise RuntimeError("GitHub Actionsの再実行は重複投稿防止のため禁止しています")

    tweet_id = publish_test_item(item)
    save_state(state_path, mark_posted(state, item, current_slot, tweet_id))
    logger.info("毎時投稿完了: https://x.com/hellobtc_jp/status/%s", tweet_id)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="INU画像付き毎時投稿ディスパッチャー")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--slot")
    parser.add_argument("--catalog", default=str(CATALOG_PATH))
    parser.add_argument("--state", default=str(STATE_PATH))
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
