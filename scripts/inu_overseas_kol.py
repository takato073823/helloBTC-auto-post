#!/usr/bin/env python3
"""海外KOLリストの選定・入替と、ネイティブ引用用の素材取得。

このモジュールは、海外の金融・暗号資産アカウントを ``海外KOL`` リストへ
同期する。選定の根拠は必ず X API で取得したプロフィールと直近10投稿の実測値
であり、Grok の探索結果だけで会員化しない。

投稿そのものはここでは行わない。毎時投稿側がこのリストの新着から、動画は
``/video/1``、画像はネイティブ引用として、内容を吟味したものだけを利用する。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import re
from pathlib import Path
from typing import Any

from grok_client import generate_x_json
from x_list_client import XListClient


logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "inu_overseas_kol_state.json"
LIST_NAME = "海外KOL"
TARGET_SIZE = 100
CHURN_SIZE = 10
MIN_FOLLOWERS = 10_000
MIN_LAST10_MAX_IMPRESSIONS = 10_000
ACTIVE_WINDOW = dt.timedelta(days=3)
QUOTE_WINDOW = dt.timedelta(hours=3)
MIN_QUOTE_IMPRESSIONS = 1_000
MAX_DISCOVERY_CALLS = 6
MAX_CANDIDATE_EVALUATIONS = 120
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")

TOPIC_TERMS = (
    "bitcoin", "btc", "ethereum", "eth", "crypto", "cryptocurrency", "stablecoin", "etf",
    "onchain", "blockchain", "defi", "token", "wallet", "exchange", "web3", "mining",
    "macro", "fed", "fomc", "cpi", "pce", "treasury", "yield", "liquidity", "inflation",
    "stock", "equity", "earnings", "guidance", "semiconductor", "nvidia", "ai", "nasdaq",
    "比特币", "加密", "稳定币", "链上", "交易所", "美股", "宏观", "利率", "财报", "人工智能",
    "criptomonedas", "cripto", "acciones", "inflación", "mercado", "finanzas",
)
BLOCKED_TERMS = (
    "airdrop", "giveaway", "referral", "invite", "promotion", "promo code", "copy trade",
    "copytrade", "signals", "pump", "100x", "dm me", "casino", "betting", "lottery",
)
FOREIGN_LANGUAGES = {"en", "zh", "es", "ko", "fr", "de", "pt", "it", "tr", "ar", "ru", "hi", "id"}

DISCOVERY_TRACKS = (
    "English-speaking crypto market, ETF-flow, and on-chain analysts who routinely attach original charts, videos, or data visualizations",
    "English-speaking macro, US equities, rates, and liquidity accounts whose original visual posts reach active market audiences",
    "Chinese-language crypto, on-chain, derivatives, and ETF researchers who publish original data visualizations",
    "International crypto journalists, market-data accounts, and researchers who add evidence and visual context rather than reposting headlines",
    "International AI, semiconductor, and technology-market analysts whose earnings or market-structure posts include charts or source visuals",
    "Global exchange-risk, stablecoin, custody, and regulation researchers with original visual explainers",
)

DISCOVERY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "accounts": {
            "type": "array",
            "minItems": 10,
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "handle": {"type": "string"},
                    "language": {"type": "string"},
                    "focus": {"type": "string"},
                    "recent_post_url": {"type": "string"},
                    "why_relevant": {"type": "string"},
                },
                "required": ["handle", "language", "focus", "recent_post_url", "why_relevant"],
            },
        },
    },
    "required": ["accounts"],
}


def _value(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _items(response: Any) -> list[Any]:
    data = _value(response, "data", response)
    return data if isinstance(data, list) else []


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(value: Any) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(dt.timezone.utc)
    except (TypeError, ValueError):
        return None


def _metrics(item: Any) -> dict[str, int]:
    raw = _value(item, "public_metrics", {}) or {}
    if not isinstance(raw, dict):
        raw = vars(raw) if hasattr(raw, "__dict__") else {}
    result: dict[str, int] = {}
    for key in ("followers_count", "impression_count", "like_count", "reply_count", "retweet_count", "quote_count"):
        try:
            result[key] = max(0, int(raw.get(key, 0) or 0))
        except (TypeError, ValueError):
            result[key] = 0
    return result


def _contains(value: str, terms: tuple[str, ...]) -> bool:
    lower = value.lower()
    for term in terms:
        if term in {"ai", "eth"}:
            if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", lower):
                return True
        elif term in lower:
            return True
    return False


def _media_map(response: Any) -> dict[str, str]:
    includes = _value(response, "includes", {}) or {}
    media = _value(includes, "media", []) or []
    return {
        str(_value(item, "media_key")): str(_value(item, "type", ""))
        for item in media
        if _value(item, "media_key") and _value(item, "type")
    }


def _tweet_rows(response: Any, handle: str) -> list[dict[str, Any]]:
    media = _media_map(response)
    rows: list[dict[str, Any]] = []
    for tweet in _items(response)[:10]:
        tweet_id = str(_value(tweet, "id", ""))
        posted_at = _parse_iso(_value(tweet, "created_at"))
        if not tweet_id or not posted_at:
            continue
        attachments = _value(tweet, "attachments", {}) or {}
        keys = _value(attachments, "media_keys", []) or []
        media_types = sorted({media.get(str(key), "") for key in keys if media.get(str(key), "")})
        metrics = _metrics(tweet)
        rows.append(
            {
                "post_id": tweet_id,
                "post_url": f"https://x.com/{handle}/status/{tweet_id}",
                "posted_at": _iso(posted_at),
                "text": _text(_value(tweet, "text"))[:800],
                "lang": _text(_value(tweet, "lang"))[:12],
                "impression_count": metrics["impression_count"],
                "like_count": metrics["like_count"],
                "reply_count": metrics["reply_count"],
                "repost_count": metrics["retweet_count"],
                "quote_count": metrics["quote_count"],
                "media_types": media_types,
                "has_video": any(kind in {"video", "animated_gif"} for kind in media_types),
                "has_image": any(kind == "photo" for kind in media_types),
            }
        )
    return rows


def _recent_tweets(client: Any, user_id: str, handle: str) -> list[dict[str, Any]]:
    response = client.get_users_tweets(
        user_id,
        max_results=10,
        exclude=["retweets", "replies"],
        tweet_fields=["attachments", "created_at", "entities", "lang", "public_metrics"],
        expansions=["attachments.media_keys"],
        media_fields=["duration_ms", "media_key", "preview_image_url", "type", "url"],
        user_auth=True,
    )
    return _tweet_rows(response, handle)


def _profile_batches(client: Any, handles: list[str] | None = None, user_ids: list[str] | None = None) -> dict[str, Any]:
    profiles: dict[str, Any] = {}
    values = handles if handles is not None else user_ids or []
    key = "usernames" if handles is not None else "ids"
    for start in range(0, len(values), 100):
        response = client.get_users(
            **{key: values[start:start + 100]},
            user_fields=["created_at", "description", "protected", "public_metrics"],
            user_auth=True,
        )
        for profile in _items(response):
            index = _text(_value(profile, "username")).lower() if handles is not None else str(_value(profile, "id") or "")
            if index:
                profiles[index] = profile
    return profiles


def default_state() -> dict[str, Any]:
    return {
        "version": 1,
        "list_name": LIST_NAME,
        "list_id": "",
        "members": {},
        "unresolved_member_ids": [],
        "last_refreshed_at": "",
        "last_skip_reason": "",
    }


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return default_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_state()
    state = default_state()
    if isinstance(payload, dict):
        state.update(payload)
    if not isinstance(state.get("members"), dict):
        state["members"] = {}
    return state


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    stored = dict(state)
    stored["members"] = {key: state["members"][key] for key in sorted(state["members"])}
    path.write_text(json.dumps(stored, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_discovery_prompt(track: str, known: set[str], count: int) -> str:
    known_block = ", ".join(f"@{handle}" for handle in sorted(known)[:TARGET_SIZE]) or "none"
    return f"""
Find real public X accounts for INU's \"海外KOL\" research List using X Search.
Focus: {track}

Hard requirements for every returned account:
- An overseas financial or cryptocurrency account, not a Japanese-language account.
- More than 10,000 followers.
- At least one of its latest ten original posts has more than 10,000 impressions.
- Posted in the last three days, and its recent original posts include a useful image, chart, or video.
- Publishes finance, macro, equities, AI-market, crypto, ETF, exchange-risk, or on-chain analysis.
- Exclude referral marketing, token promotion, price calls, airdrops, gambling, copied headlines, and inactive accounts.
- This is a discovery pass, not the final eligibility decision: return 20 distinct, real handles when
  possible. Use X Search to find them, but do not omit otherwise strong candidates merely because you
  cannot calculate every threshold yourself; the X API will verify every threshold after this response.
- Return only handles and a real recent x.com status URL found by X Search. Do not invent accounts.
- Already known: {known_block}
- Return at most {count} candidates. It is acceptable to return fewer.
""".strip()


def discover_accounts(existing: set[str], needed: int, now: dt.datetime) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    calls = min(MAX_DISCOVERY_CALLS, max(1, math.ceil(max(needed, 20) / 15)))
    for track in DISCOVERY_TRACKS[:calls]:
        try:
            payload, _ = generate_x_json(
                build_discovery_prompt(track, existing | set(found), count=20),
                schema_name="inu_overseas_kol_accounts",
                schema=DISCOVERY_SCHEMA,
                from_date=now.date() - dt.timedelta(days=3),
                to_date=now.date(),
                max_output_tokens=3600,
            )
        except Exception as exc:
            logger.warning("海外KOL探索を保留: %s", exc)
            continue
        for row in payload.get("accounts", []):
            if not isinstance(row, dict):
                continue
            handle = _text(row.get("handle")).lstrip("@").lower()
            if HANDLE_RE.fullmatch(handle) and handle not in existing:
                found.setdefault(handle, {**row, "handle": handle})
    return list(found.values())


def score_account(profile: Any, posts: list[dict[str, Any]], candidate: dict[str, Any], now: dt.datetime, own_user_id: str) -> tuple[dict[str, Any] | None, str]:
    handle = _text(_value(profile, "username")).lower()
    user_id = str(_value(profile, "id") or "")
    if not HANDLE_RE.fullmatch(handle) or not user_id or user_id == own_user_id:
        return None, "invalid_or_self"
    if bool(_value(profile, "protected", False)):
        return None, "protected"
    bio = _text(_value(profile, "description"))
    if _contains(bio, BLOCKED_TERMS):
        return None, "blocked_bio"
    followers = _metrics(profile)["followers_count"]
    if followers <= MIN_FOLLOWERS:
        return None, "followers_below_10000"
    if not posts:
        return None, "no_recent_posts"
    newest = max((_parse_iso(row.get("posted_at")) for row in posts), default=None)
    if not newest or now.astimezone(dt.timezone.utc) - newest > ACTIVE_WINDOW:
        return None, "inactive_over_3_days"
    language_counts: dict[str, int] = {}
    topical = 0
    blocked = 0
    media_posts = 0
    for row in posts:
        language = str(row.get("lang", "")).lower()
        if language:
            language_counts[language] = language_counts.get(language, 0) + 1
        body = str(row.get("text", ""))
        topical += int(_contains(body, TOPIC_TERMS))
        blocked += int(_contains(body, BLOCKED_TERMS))
        media_posts += int(bool(row.get("has_video") or row.get("has_image")))
    dominant_language = max(language_counts, key=language_counts.get) if language_counts else str(candidate.get("language", "")).lower()
    if dominant_language == "ja" or (dominant_language and dominant_language not in FOREIGN_LANGUAGES):
        return None, "not_overseas_language"
    if topical < 2 and not _contains(bio + " " + _text(candidate.get("focus")), TOPIC_TERMS):
        return None, "not_finance_or_crypto"
    if blocked >= max(2, math.ceil(len(posts) / 2)):
        return None, "promotion_heavy"
    impressions = [int(row.get("impression_count", 0) or 0) for row in posts]
    if max(impressions, default=0) <= MIN_LAST10_MAX_IMPRESSIONS:
        return None, "latest10_has_no_10000_impression_post"
    if media_posts == 0:
        return None, "no_visual_post_in_latest10"
    average = round(sum(impressions) / len(impressions), 2)
    score = round(
        min(40.0, math.log10(max(followers, 1)) * 8)
        + min(35.0, math.log10(max(average, 1)) * 8)
        + min(15.0, media_posts * 3)
        + min(10.0, topical * 2),
        2,
    )
    best = max(posts, key=lambda row: int(row.get("impression_count", 0) or 0))
    return {
        "handle": handle,
        "user_id": user_id,
        "followers": followers,
        "language": dominant_language,
        "focus": _text(candidate.get("focus"))[:180],
        "why_relevant": _text(candidate.get("why_relevant"))[:240],
        "last_seen_at": _iso(newest),
        "recent_post_url": str(best["post_url"]),
        "last10_posts": posts[:10],
        "last10_average_impressions": average,
        "last10_max_impressions": max(impressions),
        "last10_media_posts": media_posts,
        "score": score,
        "tier": "member",
    }, ""


def _evaluate_candidates(client: Any, candidates: list[dict[str, Any]], now: dt.datetime, own_user_id: str) -> tuple[list[dict[str, Any]], set[str]]:
    profiles = _profile_batches(client, handles=[str(row["handle"]) for row in candidates]) if candidates else {}
    records: list[dict[str, Any]] = []
    unresolved: set[str] = set()
    for candidate in candidates:
        handle = str(candidate["handle"]).lower()
        profile = profiles.get(handle)
        if not profile:
            continue
        try:
            posts = _recent_tweets(client, str(_value(profile, "id")), handle)
        except Exception as exc:
            logger.info("海外KOLの直近10投稿を取得できません: @%s / %s", handle, exc)
            unresolved.add(str(_value(profile, "id") or ""))
            continue
        record, reason = score_account(profile, posts, candidate, now, own_user_id)
        if record:
            records.append(record)
        else:
            logger.info("海外KOL候補を除外: @%s / %s", handle, reason)
    return records, {value for value in unresolved if value}


def _existing_candidates(client: Any, member_ids: set[str]) -> list[dict[str, Any]]:
    profiles = _profile_batches(client, user_ids=sorted(member_ids)) if member_ids else {}
    result = []
    for user_id, profile in profiles.items():
        handle = _text(_value(profile, "username")).lower()
        if HANDLE_RE.fullmatch(handle):
            result.append({"handle": handle, "language": "", "focus": _text(_value(profile, "description")), "recent_post_url": "", "why_relevant": "既存の海外KOLリストを再評価"})
    return result


def refresh_overseas_kol(state: dict[str, Any], client: Any, now: dt.datetime, *, allow_create: bool, dry_run: bool) -> dict[str, Any]:
    list_client = XListClient(client)
    own_user_id = list_client.owner_id()
    if dry_run:
        return {"list_id": state.get("list_id", ""), "added": 0, "removed": 0, "members": 0, "churned": 0}
    list_id = list_client.resolve_list(state.get("list_id") or None, LIST_NAME, allow_create)
    if not list_id:
        state["last_skip_reason"] = "list_not_found_or_create_not_allowed"
        return {"list_id": "", "added": 0, "removed": 0, "members": 0, "churned": 0}
    state["list_id"] = list_id
    try:
        actual_ids = list_client.member_ids(list_id)
    except Exception as exc:
        state["last_skip_reason"] = f"list_members_unavailable: {exc}"
        return {"list_id": list_id, "added": 0, "removed": 0, "members": 0, "churned": 0}

    # 現会員は毎回、直近10投稿をAPIで取り直す。読取失敗は削除の根拠にせず保留する。
    existing = _existing_candidates(client, actual_ids)
    existing_records, unresolved = _evaluate_candidates(client, existing, now, own_user_id)
    valid_by_id = {str(row["user_id"]): row for row in existing_records}
    valid_handles = {row["handle"] for row in existing_records}
    unresolved_ids = set(unresolved)
    invalid_ids = actual_ids - set(valid_by_id) - unresolved_ids

    known = {
        str(row.get("handle", "")).lower()
        for row in state.get("members", {}).values()
        if row.get("handle")
    } | valid_handles
    # 満員時でも10件を比較できるよう先に候補を発見・実測確認し、補充不能なら
    # 既存会員を減らさない。
    churn_slots = CHURN_SIZE if len(valid_by_id) >= TARGET_SIZE else 0
    needed = max(0, TARGET_SIZE - len(valid_by_id)) + churn_slots
    discovered = discover_accounts(known, max(needed * 3, 30), now) if needed else []
    discovered_records, discovered_unresolved = _evaluate_candidates(
        client, discovered[:MAX_CANDIDATE_EVALUATIONS], now, own_user_id
    )
    unresolved_ids |= discovered_unresolved
    candidates = [row for row in discovered_records if str(row["user_id"]) not in actual_ids]
    candidates.sort(key=lambda row: (float(row["last10_average_impressions"]), float(row["score"])), reverse=True)

    churn_ids: set[str] = set()
    if len(valid_by_id) >= TARGET_SIZE and len(candidates) >= CHURN_SIZE:
        weakest = sorted(
            valid_by_id.values(),
            key=lambda row: (float(row["last10_average_impressions"]), float(row["score"])),
        )[:CHURN_SIZE]
        churn_ids = {str(row["user_id"]) for row in weakest}

    available = TARGET_SIZE - (len(valid_by_id) - len(churn_ids))
    additions = candidates[:max(0, available)]
    to_remove = sorted(invalid_ids | churn_ids)
    removed, pending_remove = list_client.remove_members(list_id, to_remove)
    added, pending_add = list_client.add_members(list_id, [str(row["user_id"]) for row in additions])

    for row in existing_records + discovered_records:
        saved = dict(row)
        saved["tier"] = "member" if str(row["user_id"]) in (set(valid_by_id) - churn_ids | set(added)) else "excluded"
        if str(row["user_id"]) in churn_ids:
            saved["exclusion_reason"] = "bottom_10_last10_average_impressions"
        state["members"][saved["handle"]] = saved
    for member in state["members"].values():
        if str(member.get("user_id")) in set(removed) and member.get("tier") == "member":
            member.update({"tier": "excluded", "exclusion_reason": "removed_from_x_list"})

    state["unresolved_member_ids"] = sorted(unresolved_ids)
    state["last_refreshed_at"] = _iso(now)
    state["last_skip_reason"] = "" if not (pending_add or pending_remove) else "list_sync_pending"
    return {
        "list_id": list_id,
        "added": len(added),
        "removed": len(removed),
        "members": len(valid_by_id) - len(churn_ids) + len(added),
        "churned": len(churn_ids & set(removed)),
    }


def live_visual_posts(now: dt.datetime, client: Any | None = None, state_path: Path = STATE_PATH, limit: int = 20) -> list[dict[str, Any]]:
    """海外KOLリストの統合タイムラインから、引用候補の動画・画像投稿だけを返す。"""
    state = load_state(state_path)
    list_id = str(state.get("list_id", ""))
    members = {
        str(row.get("user_id")): row
        for row in state.get("members", {}).values()
        if row.get("tier") == "member" and row.get("user_id")
    }
    if not list_id or not members:
        return []
    rows: list[dict[str, Any]] = []
    for row in XListClient(client).recent_tweet_rows(list_id, max_pages=3):
        author_id = str(row.get("author_id", ""))
        member = members.get(author_id)
        posted_at = _parse_iso(row.get("posted_at"))
        if not member or not posted_at or now.astimezone(dt.timezone.utc) - posted_at > QUOTE_WINDOW:
            continue
        text = _text(row.get("text"))
        if not _contains(text, TOPIC_TERMS) or _contains(text, BLOCKED_TERMS):
            continue
        if not (row.get("has_video") or row.get("has_image")):
            continue
        if int(row.get("impression_count", 0) or 0) < MIN_QUOTE_IMPRESSIONS:
            continue
        rows.append({
            **row,
            "handle": str(member["handle"]),
            "followers": int(member.get("followers", 0) or 0),
            "last10_average_impressions": float(member.get("last10_average_impressions", 0) or 0),
        })
    rows.sort(
        key=lambda row: (
            int(row.get("impression_count", 0) or 0),
            int(row.get("like_count", 0) or 0) * 80,
            bool(row.get("has_video")),
        ),
        reverse=True,
    )
    return rows[:limit]


def run(args: argparse.Namespace) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    now = dt.datetime.now(dt.timezone.utc)
    state_path = Path(args.state)
    state = load_state(state_path)
    result = refresh_overseas_kol(state, XListClient().client, now, allow_create=bool(args.allow_create), dry_run=bool(args.dry_run))
    save_state(state, state_path)
    logger.info("海外KOL同期: 会員=%d / 追加=%d / 削除=%d / 入替=%d", result["members"], result["added"], result["removed"], result["churned"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="INU海外KOLリストを更新")
    parser.add_argument("--allow-create", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--state", default=str(STATE_PATH))
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
