#!/usr/bin/env python3
"""INUの限定的なフォロー・フォローバック施策。

対象は日本語で投資・金融・暗号資産を継続発信する小規模アカウントだけに絞る。
機械的な大量フォローを避けるため、日次上限・探索間隔・2日後の自動解除を状態として
保存する。投稿やリストへの追加はここでは行わない。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path
from typing import Any

from grok_client import generate_x_json
from inu_auto_hourly import _parse_timestamp
from inu_growth_watchlist import is_japanese_timeline, load_denylist
from x_poster import follow_user, unfollow_user


logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "inu_follow_growth_state.json"
MAX_TARGET_FOLLOWERS = 1_000
FOLLOWBACK_GRACE = dt.timedelta(days=2)
MAX_FOLLOWS_PER_DAY = 6
MAX_UNFOLLOWS_PER_RUN = 5
DISCOVERY_INTERVAL = dt.timedelta(hours=4)
ACTIVE_DAYS = 14
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
TOPIC_TERMS = (
    "bitcoin", "btc", "ethereum", "eth", "crypto", "cryptocurrency", "stablecoin", "etf",
    "onchain", "blockchain", "defi", "token", "wallet", "exchange", "web3",
    "ビットコイン", "仮想通貨", "暗号資産", "ステーブルコイン", "オンチェーン", "取引所",
    "米国株", "日本株", "株式", "決算", "金利", "債券", "インフレ", "金融", "マクロ",
    "半導体", "人工知能", "AI",
)
BLOCKED_TERMS = (
    "airdrop", "giveaway", "referral", "invite", "招待", "キャンペーン", "プレゼント",
    "価格予想", "signals", "pump", "copytrade", "copy trade", "dm me", "100x",
)

FOLLOW_DISCOVERY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "accounts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "handle": {"type": "string"},
                    "recent_post_url": {"type": "string"},
                    "why_relevant": {"type": "string"},
                },
                "required": ["handle", "recent_post_url", "why_relevant"],
            },
            "maxItems": 8,
        },
    },
    "required": ["accounts"],
}


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _value(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _data(response: Any) -> list[Any]:
    data = _value(response, "data", response)
    return data if isinstance(data, list) else []


def _metrics(item: Any) -> dict[str, int]:
    raw = _value(item, "public_metrics", {}) or {}
    if not isinstance(raw, dict):
        raw = vars(raw) if hasattr(raw, "__dict__") else {}
    try:
        return {"followers_count": int(raw.get("followers_count", 0) or 0)}
    except (TypeError, ValueError):
        return {"followers_count": 0}


def _text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _has_term(text: str, terms: tuple[str, ...]) -> bool:
    lower = text.lower()
    for term in terms:
        token = term.lower()
        if token in {"ai", "eth"}:
            if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", lower):
                return True
        elif token in lower:
            return True
    return False


def default_state() -> dict[str, Any]:
    return {"version": 1, "targets": {}, "last_discovery_at": "", "last_skip_reason": ""}


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return default_state()
    state = default_state()
    if isinstance(payload, dict):
        state.update(payload)
    if not isinstance(state.get("targets"), dict):
        state["targets"] = {}
    return state


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    stable = dict(state)
    stable["targets"] = {key: state["targets"][key] for key in sorted(state["targets"])}
    path.write_text(json.dumps(stable, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _daily_follow_count(state: dict[str, Any], now: dt.datetime) -> int:
    day = now.astimezone(dt.timezone.utc).date()
    count = 0
    for row in state.get("targets", {}).values():
        try:
            followed_at = _parse_timestamp(str(row.get("followed_at", "")))
        except (TypeError, ValueError):
            followed_at = None
        if row.get("status") in {"pending", "followed_back"} and followed_at and followed_at.date() == day:
            count += 1
    return count


def _discover_prompt(known_handles: set[str]) -> str:
    known = ", ".join(f"@{handle}" for handle in sorted(known_handles)[:200]) or "なし"
    return f"""
INUの投資情報アカウントが、同じ関心を持つ日本語ユーザーと交流するための候補をX Searchで探してください。

対象:
- 日本語で金融、米国株、日本株、暗号資産、ビットコイン、ETF、マクロ、AI・半導体のいずれかを継続的に投稿する個人または小規模分析アカウント。
- 直近14日以内に、根拠・データ・一次情報を含む関連投稿がある。
- フォロワー数が1,000人以下とX Searchで確認できる、または小規模アカウントと確認できるもの。

除外:
- 海外言語中心、企業の公式告知専用、エアドロップ、紹介、案件、プレゼント、価格予想だけ、相互フォロー募集。
- 確認できない推測のhandle。

handleは@なし、recent_post_urlは直近の実在するstatus URLにしてください。既知の対象 ({known}) は返さないでください。
候補なしは正常です。
""".strip()


def discover_candidates(known_handles: set[str], now: dt.datetime) -> list[dict[str, str]]:
    payload, _ = generate_x_json(
        _discover_prompt(known_handles),
        schema_name="inu_follow_growth_candidates",
        schema=FOLLOW_DISCOVERY_SCHEMA,
        from_date=now.date() - dt.timedelta(days=ACTIVE_DAYS),
        to_date=now.date(),
        max_output_tokens=1800,
    )
    candidates: dict[str, dict[str, str]] = {}
    for row in payload.get("accounts", []):
        if not isinstance(row, dict):
            continue
        handle = _text(row.get("handle")).lstrip("@").lower()
        if HANDLE_RE.fullmatch(handle) and handle not in known_handles:
            candidates.setdefault(handle, {"handle": handle, "recent_post_url": _text(row.get("recent_post_url")), "why_relevant": _text(row.get("why_relevant"))})
    return list(candidates.values())


def _candidate_record(profile: Any, tweets: list[Any], candidate: dict[str, str], own_user_id: str, denylist: set[str], now: dt.datetime) -> dict[str, Any] | None:
    user_id = str(_value(profile, "id", ""))
    handle = _text(_value(profile, "username")).lstrip("@").lower()
    if not re.fullmatch(r"\d{1,22}", user_id) or user_id == own_user_id or not HANDLE_RE.fullmatch(handle) or handle in denylist:
        return None
    if bool(_value(profile, "protected", False)) or _metrics(profile)["followers_count"] > MAX_TARGET_FOLLOWERS:
        return None
    profile_text = _text(_value(profile, "description"))
    if _has_term(profile_text, BLOCKED_TERMS) or not is_japanese_timeline(tweets):
        return None
    fresh = []
    for tweet in tweets:
        posted_at = _parse_timestamp(str(_value(tweet, "created_at", "")))
        if posted_at and now - posted_at <= dt.timedelta(days=ACTIVE_DAYS):
            fresh.append(tweet)
    text = " ".join(_text(_value(tweet, "text")) for tweet in fresh)
    if not fresh or not _has_term(profile_text + " " + text, TOPIC_TERMS) or _has_term(text, BLOCKED_TERMS):
        return None
    return {
        "user_id": user_id,
        "handle": handle,
        "followers": _metrics(profile)["followers_count"],
        "recent_post_url": candidate.get("recent_post_url", ""),
        "why_relevant": candidate.get("why_relevant", ""),
        "status": "pending",
        "followed_at": now.astimezone(dt.timezone.utc).isoformat(),
    }


def follower_ids(client: Any, own_user_id: str) -> set[str] | None:
    """取得失敗時はNoneを返し、誤解除を絶対にしない。"""
    ids: set[str] = set()
    token = ""
    try:
        for _ in range(10):
            kwargs: dict[str, Any] = {"max_results": 100, "user_auth": True}
            if token:
                kwargs["pagination_token"] = token
            response = client.get_users_followers(own_user_id, **kwargs)
            ids.update(str(_value(row, "id", "")) for row in _data(response) if _value(row, "id", ""))
            meta = _value(response, "meta", {}) or {}
            token = _text(_value(meta, "next_token", ""))
            if not token:
                break
    except Exception as exc:
        logger.info("フォローバック確認を見送りします: %s", exc)
        return None
    return ids


def reconcile_followbacks(state: dict[str, Any], inbound_ids: set[str] | None, now: dt.datetime) -> tuple[int, int]:
    """フォローバック済みを保持し、48時間経過後の未反応だけを解除する。"""
    if inbound_ids is None:
        return 0, 0
    kept = 0
    unfollowed = 0
    for user_id, row in state.get("targets", {}).items():
        if row.get("status") != "pending":
            continue
        if user_id in inbound_ids:
            row["status"] = "followed_back"
            row["followed_back_at"] = now.astimezone(dt.timezone.utc).isoformat()
            kept += 1
            continue
        followed_at = _parse_timestamp(str(row.get("followed_at", "")))
        if not followed_at or now - followed_at < FOLLOWBACK_GRACE or unfollowed >= MAX_UNFOLLOWS_PER_RUN:
            continue
        if unfollow_user(user_id):
            row["status"] = "unfollowed"
            row["unfollowed_at"] = now.astimezone(dt.timezone.utc).isoformat()
            unfollowed += 1
    return kept, unfollowed


def should_discover(state: dict[str, Any], now: dt.datetime) -> bool:
    if _daily_follow_count(state, now) >= MAX_FOLLOWS_PER_DAY:
        return False
    raw_previous = _text(state.get("last_discovery_at", ""))
    if not raw_previous:
        return True
    try:
        previous = _parse_timestamp(raw_previous)
    except (TypeError, ValueError):
        return True
    return not previous or now - previous >= DISCOVERY_INTERVAL


def follow_one_new_target(
    state: dict[str, Any],
    client: Any,
    own_user_id: str,
    now: dt.datetime,
    inbound_ids: set[str] | None = None,
) -> bool:
    if not should_discover(state, now):
        return False
    state["last_discovery_at"] = now.astimezone(dt.timezone.utc).isoformat()
    known_handles = {str(row.get("handle", "")).lower() for row in state.get("targets", {}).values() if row.get("handle")}
    denylist = load_denylist()
    try:
        candidates = discover_candidates(known_handles, now)
    except Exception as exc:
        state["last_skip_reason"] = f"follow_discovery_unavailable: {exc}"
        logger.info("フォロー候補の探索を見送りします: %s", exc)
        return False
    for candidate in candidates:
        try:
            response = client.get_users(
                usernames=[candidate["handle"]],
                user_fields=["created_at", "description", "protected", "public_metrics"],
                user_auth=True,
            )
            profiles = _data(response)
            if not profiles:
                continue
            profile = profiles[0]
            user_id = str(_value(profile, "id", ""))
            tweets = _data(client.get_users_tweets(
                user_id, max_results=10, exclude=["retweets", "replies"],
                tweet_fields=["created_at", "lang", "public_metrics"], user_auth=True,
            ))
            record = _candidate_record(profile, tweets, candidate, own_user_id, denylist, now)
        except Exception as exc:
            logger.info("フォロー候補の実在確認を見送りします: @%s / %s", candidate["handle"], exc)
            continue
        if not record:
            continue
        # 既にINUをフォローしている人は、成長施策として新規フォローしない。
        if inbound_ids is not None and record["user_id"] in inbound_ids:
            continue
        if follow_user(record["user_id"]):
            state.setdefault("targets", {})[record["user_id"]] = record
            state["last_skip_reason"] = ""
            logger.info("限定フォローを実行: @%s", record["handle"])
            return True
    state["last_skip_reason"] = "no_qualified_follow_target"
    return False


def run(client: Any, own_user_id: str, state_path: Path = STATE_PATH, now: dt.datetime | None = None) -> dict[str, int | bool]:
    moment = now or _utcnow()
    state = load_state(state_path)
    inbound_ids = follower_ids(client, own_user_id)
    kept, unfollowed = reconcile_followbacks(state, inbound_ids, moment)
    followed = follow_one_new_target(state, client, own_user_id, moment, inbound_ids)
    state["checked_at"] = moment.astimezone(dt.timezone.utc).isoformat()
    save_state(state, state_path)
    return {"followed": followed, "followed_back": kept, "unfollowed": unfollowed}
