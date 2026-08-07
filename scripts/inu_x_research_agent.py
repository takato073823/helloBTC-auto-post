#!/usr/bin/env python3
"""INU向けの公式X APIリサーチエージェント。

Grokの検索結果だけに依存せず、公式X APIから取得した実際の投稿・表示数・
反応・動画有無・話題量を候補化する。ここで得たX投稿はあくまで発見専用であり、
投稿公開前には既存の編集経路が必ず一次資料を確認する。

このモジュールは投稿、いいね、返信、フォローを一切実行しない。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os
import re
import statistics
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from inu_source_registry import discovery_x_handles
from x_poster import _get_client


logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "inu_x_research_state.json"
# 成長ブーストは独立した並行キューで動くため、同じJSONを丸ごと更新すると公式X探索と
# Git競合して候補が失われる。ウォッチリスト由来の発見は専用シャードに保持し、読込時に
# 統合する。これにより探索経路ごとの書込みが互いを上書きしない。
WATCHLIST_SIGNAL_STATE_PATH = SCRIPT_DIR / "inu_x_watchlist_signals_state.json"
# 昇格結果は投稿キューだけが更新する。探索シャードへ処理済みフラグを書き戻さないため、
# 公式探索・成長ブースト・投稿処理の3者が同じJSONを競合更新しない。
PROMOTION_STATE_PATH = SCRIPT_DIR / "inu_x_signal_promotion_state.json"
JST = dt.timezone(dt.timedelta(hours=9))

# 公式X APIのコストを一定に保つ。Countsは軽量な先行判定、本文取得は急増時または
# 定期ディープスキャン時だけ行う。上限は状態ファイル側で日付ごとにリセットされる。
MAX_COUNT_REQUESTS_PER_DAY = 72
MAX_DEEP_SEARCHES_PER_DAY = 18
MAX_WATCHER_GURU_SEARCHES_PER_DAY = 72
MAX_TREND_REQUESTS_PER_DAY = 6
DEEP_SCAN_INTERVAL = dt.timedelta(minutes=80)
WATCHER_GURU_SCAN_INTERVAL = dt.timedelta(minutes=20)
SIGNAL_MAX_AGE = dt.timedelta(hours=6)
# 高反応の発見を「記録だけ」で終わらせないための、投稿候補へ昇格させる上限。
# 速報経路はこの中から一次資料を確認し、確認できないものは投稿しない。
PROMOTION_SIGNAL_MAX_AGE = dt.timedelta(hours=2)
PROMOTION_MIN_IMPRESSIONS = 10_000
PROMOTION_MIN_SCORE = 36.0
MAX_STORED_SIGNALS = 240
MAX_STORED_SCANS = 360
JAPAN_WOEID = 23424856

EXTERNAL_MEDIA_HOSTS = {
    "bloomberg.com", "coindesk.com", "coinpost.jp", "cointelegraph.com", "decrypt.co",
    "dlnews.com", "forbes.com", "jp.reuters.com", "nikkei.com", "reuters.com",
    "theblock.co", "blockworks.co", "yahoo.co.jp",
}
BLOCKED_TERMS = (
    "airdrop", "giveaway", "referral", "invite", "招待", "キャンペーン", "プレゼント",
    "価格予想", "signals", "pump", "copytrade", "copy trade", "dm me", "100x", "稳赚",
    "casino", "gambling", "betting", "poker", "lottery",
)
TOPIC_TERMS = (
    "bitcoin", "btc", "ethereum", "eth", "crypto", "cryptocurrency", "stablecoin", "etf",
    "onchain", "blockchain", "defi", "token", "wallet", "exchange", "web3", "mining",
    "ビットコイン", "仮想通貨", "暗号資産", "ステーブルコイン", "オンチェーン", "取引所",
    "米国株", "日本株", "株式", "決算", "金利", "債券", "インフレ", "金融", "マクロ",
    "半導体", "人工知能", "比特币", "加密", "稳定币", "链上", "交易所", "美股", "宏观",
)
MATERIAL_SIGNAL_TERMS = (
    "etf", "inflow", "outflow", "liquidation", "funding", "open interest", "whale", "onchain",
    "exploit", "breach", "hack", "reserve", "approval", "approved", "regulation", "sec", "cftc",
    "fomc", "cpi", "pce", "earnings", "guidance", "revenue", "利回り", "決算", "規制", "流入",
    "流出", "清算", "ハッキング", "オンチェーン", "資金流", "监管", "财报", "资金流",
)
MEDIA_HANDLES = {"coindesk", "cointelegraph", "decryptmedia", "theblock__", "blockworks_"}
FIXED_DISCOVERY_HANDLES = discovery_x_handles()
FIXED_DISCOVERY_QUERY = " OR ".join(f"from:{handle}" for handle in FIXED_DISCOVERY_HANDLES)
WATCHER_GURU_HANDLE = "watcherguru"
WATCHER_GURU_QUERY = "from:WatcherGuru -is:reply -is:retweet"
WATCHER_GURU_URGENT_TERMS = (
    "breaking", "just in", "bitcoin", "btc", "ethereum", "eth", "crypto", "etf",
    "sec", "cftc", "fed", "fomc", "treasury", "tariff", "rate", "inflation",
    "approval", "approved", "hack", "exploit", "liquidation", "stablecoin",
)

# 1回のRecent Countsは1テーマだけを測る。20分間隔で循環するため、全テーマを
# 一度に大量取得せず、突発的な話題量の増加を捕捉できる。
TOPIC_QUERIES = (
    (
        "fixed_primary_x",
        f"({FIXED_DISCOVERY_QUERY}) -is:reply -is:retweet",
    ),
    (
        "crypto_market",
        "(Bitcoin OR BTC OR Ethereum OR ETH) (ETF OR inflow OR outflow OR liquidation OR funding OR \"open interest\") lang:en -is:reply -is:retweet",
    ),
    (
        "onchain_exchange",
        "(Bitcoin OR Ethereum OR stablecoin OR exchange OR wallet) (whale OR onchain OR exploit OR breach OR reserve OR outflow) lang:en -is:reply -is:retweet",
    ),
    (
        "macro_rates",
        "(Fed OR FOMC OR CPI OR PCE OR NFP OR Treasury OR DXY) (rate OR inflation OR yield OR liquidity OR tariff) lang:en -is:reply -is:retweet",
    ),
    (
        "regulation",
        "(SEC OR CFTC OR Congress OR \"stablecoin bill\" OR \"crypto regulation\") (Bitcoin OR crypto OR ETF OR stablecoin) lang:en -is:reply -is:retweet",
    ),
    (
        "equities_ai",
        "(S&P 500 OR Nasdaq OR NVDA OR NVIDIA OR Tesla OR AI OR semiconductor) (earnings OR guidance OR rally OR selloff OR capex) lang:en -is:reply -is:retweet",
    ),
    (
        "japanese_market",
        "(ビットコイン OR BTC OR 仮想通貨 OR 暗号資産 OR 米国株 OR 日本株 OR AI) (ETF OR 急騰 OR 急落 OR 流入 OR 規制 OR 決算 OR 金利) lang:ja -is:reply -is:retweet",
    ),
    (
        "chinese_market",
        "(比特币 OR 加密货币 OR 稳定币 OR 链上 OR 美股 OR 宏观 OR 人工智能) (ETF OR 资金流 OR 监管 OR 交易所 OR 利率 OR 财报) lang:zh -is:reply -is:retweet",
    ),
)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat()


def _parse_time(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            return None
        return value.astimezone(dt.timezone.utc)
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def _value(item: Any, key: str, default: Any = None) -> Any:
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _items(response: Any) -> list[Any]:
    data = _value(response, "data", response)
    return data if isinstance(data, list) else []


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _normal_url(value: str) -> str:
    parts = urlsplit((value or "").strip())
    query = urlencode([
        (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"ref", "source", "s"}
    ])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


def _host(value: str) -> str:
    return (urlsplit(value).hostname or "").lower().removeprefix("www.")


def _is_external_media_url(value: str) -> bool:
    host = _host(value)
    return any(host == blocked or host.endswith(f".{blocked}") for blocked in EXTERNAL_MEDIA_HOSTS)


def _contains_term(value: str, terms: tuple[str, ...]) -> bool:
    lower = value.lower()
    for term in terms:
        token = term.lower()
        if token in {"ai", "eth"}:
            if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", lower):
                return True
        elif token in lower:
            return True
    return False


def _metrics(item: Any) -> dict[str, int]:
    raw = _value(item, "public_metrics", {}) or {}
    if not isinstance(raw, dict):
        raw = vars(raw) if hasattr(raw, "__dict__") else {}
    result: dict[str, int] = {}
    for key in ("impression_count", "like_count", "reply_count", "retweet_count", "quote_count", "followers_count"):
        try:
            result[key] = max(0, int(raw.get(key, 0) or 0))
        except (TypeError, ValueError):
            result[key] = 0
    return result


def default_state() -> dict[str, Any]:
    return {"version": 1, "scans": [], "signals": [], "trends": [], "last_deep_scan_at": ""}


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
    for key in ("scans", "signals", "trends"):
        if not isinstance(state.get(key), list):
            state[key] = []
    return state


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    stable = dict(state)
    stable["scans"] = list(state.get("scans", []))[-MAX_STORED_SCANS:]
    stable["signals"] = list(state.get("signals", []))[-MAX_STORED_SIGNALS:]
    stable["trends"] = list(state.get("trends", []))[-120:]
    path.write_text(json.dumps(stable, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _signal_state_paths(path: Path) -> tuple[Path, ...]:
    """通常読込では公式探索とウォッチリスト探索を統合する。"""
    return (STATE_PATH, WATCHLIST_SIGNAL_STATE_PATH) if path == STATE_PATH else (path,)


def _signal_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for state_path in _signal_state_paths(path):
        rows.extend(row for row in load_state(state_path).get("signals", []) if isinstance(row, dict))
    return rows


def ensure_watchlist_signal_state(path: Path = WATCHLIST_SIGNAL_STATE_PATH) -> None:
    """成長処理が候補ゼロでも、専用シャードをGitへ安全に保存できるよう初期化する。"""
    if not path.exists():
        save_state(default_state(), path)


def _load_promotion_state(path: Path | None = None) -> dict[str, Any]:
    path = path or PROMOTION_STATE_PATH
    if not path.exists():
        return {"version": 1, "results": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "results": {}}
    results = payload.get("results", {}) if isinstance(payload, dict) else {}
    return {"version": 1, "results": results if isinstance(results, dict) else {}}


def _save_promotion_state(state: dict[str, Any], path: Path | None = None) -> None:
    path = path or PROMOTION_STATE_PATH
    rows = list((state.get("results") or {}).items())[-MAX_STORED_SIGNALS * 2 :]
    payload = {"version": 1, "results": dict(rows)}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _today_rows(rows: list[dict[str, Any]], now: dt.datetime, key: str = "checked_at") -> list[dict[str, Any]]:
    today = now.astimezone(JST).date()
    result = []
    for row in rows:
        stamp = _parse_time(row.get(key))
        if stamp and stamp.astimezone(JST).date() == today:
            result.append(row)
    return result


def _daily_count(state: dict[str, Any], now: dt.datetime, action: str) -> int:
    return sum(1 for row in _today_rows(list(state.get("scans", [])), now) if row.get("action") == action)


def _next_topic(state: dict[str, Any]) -> tuple[str, str]:
    # Bearer未設定時でも、OAuth 1.0aのディープスキャンだけで全テーマを循環する。
    # Bearerありなら軽量なCountsの回数で、より細かく循環する。
    progress_rows = [
        row for row in state.get("scans", [])
        if row.get("action") in {"count", "search"}
    ]
    return TOPIC_QUERIES[len(progress_rows) % len(TOPIC_QUERIES)]


def _recent_count_baseline(state: dict[str, Any], topic: str) -> float:
    values = [
        int(row.get("count", 0) or 0)
        for row in state.get("scans", [])
        if row.get("action") == "count" and row.get("topic") == topic and int(row.get("count", 0) or 0) >= 0
    ][-12:]
    return float(statistics.median(values)) if len(values) >= 3 else 0.0


def _bearer_token() -> str:
    return os.environ.get("X_RESEARCH_BEARER_TOKEN", "").strip()


def _bearer_get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    token = _bearer_token()
    if not token:
        raise RuntimeError("X_RESEARCH_BEARER_TOKEN が未設定です")
    response = requests.get(
        f"https://api.x.com{path}",
        params=params,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("X APIのJSON形式が不正です")
    return payload


def _recent_count(query: str, now: dt.datetime) -> int:
    # Recent Search/Counts は「リクエスト時刻の10秒前」までしか受け付けない。
    # GitHub Actionsの開始直後でも400にならないよう余裕を持たせる。
    end_time = now - dt.timedelta(seconds=15)
    payload = _bearer_get(
        "/2/tweets/counts/recent",
        {
            "query": query,
            "start_time": (end_time - dt.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_time": end_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "granularity": "minute",
        },
    )
    meta = payload.get("meta", {}) or {}
    try:
        return max(0, int(meta.get("total_tweet_count", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _fetch_japan_trends() -> list[dict[str, Any]]:
    payload = _bearer_get(
        f"/2/trends/by/woeid/{JAPAN_WOEID}",
        {"max_trends": 20, "trend.fields": "trend_name,tweet_count"},
    )
    rows = payload.get("data", []) or []
    return [row for row in rows if isinstance(row, dict)]


def _includes(response: Any, kind: str) -> list[Any]:
    values = _value(response, "includes", {}) or {}
    if isinstance(values, dict):
        result = values.get(kind, []) or []
    else:
        result = _value(values, kind, []) or []
    return result if isinstance(result, list) else []


def _tweet_urls(tweet: Any) -> list[str]:
    entities = _value(tweet, "entities", {}) or {}
    if not isinstance(entities, dict):
        entities = vars(entities) if hasattr(entities, "__dict__") else {}
    urls = entities.get("urls", []) or []
    result: list[str] = []
    for item in urls:
        expanded = _value(item, "expanded_url", "") or _value(item, "url", "")
        url = _normal_url(str(expanded))
        if url.startswith("https://") and url not in result:
            result.append(url)
    return result


def _media_types(tweet: Any, media_by_key: dict[str, Any]) -> list[str]:
    attachments = _value(tweet, "attachments", {}) or {}
    if not isinstance(attachments, dict):
        attachments = vars(attachments) if hasattr(attachments, "__dict__") else {}
    result = []
    for key in attachments.get("media_keys", []) or []:
        value = str(_value(media_by_key.get(str(key)), "type", "")).lower()
        if value and value not in result:
            result.append(value)
    return result


def _score(
    *, text: str, posted_at: dt.datetime, metrics: dict[str, int], followers: int,
    urls: list[str], media_types: list[str], verified: bool, trend_names: set[str], now: dt.datetime,
) -> tuple[float, str]:
    age_minutes = max(0.0, (now - posted_at).total_seconds() / 60)
    freshness = 24.0 if age_minutes <= 20 else 20.0 if age_minutes <= 60 else 14.0 if age_minutes <= 180 else 6.0
    impressions = metrics["impression_count"]
    interactions = metrics["like_count"] + metrics["reply_count"] * 1.6 + metrics["retweet_count"] * 2 + metrics["quote_count"] * 2
    # 大手アカウントの絶対数だけが勝たないよう、投稿後の時間とフォロワー規模で補正する。
    reach_per_hour = impressions * 60 / max(age_minutes, 8)
    velocity = min(26.0, math.log10(reach_per_hour + 1) * 5.0)
    engagement_rate = interactions / max(impressions, 1)
    engagement = min(16.0, engagement_rate * 550)
    follower_normalized = min(8.0, impressions / max(followers, 1) * 35)
    primary_hint = any(not _is_external_media_url(url) and "x.com/" not in url for url in urls)
    source_score = (12.0 if primary_hint else 0.0) + (4.0 if verified else 0.0)
    media_score = 5.0 if any(kind in {"video", "animated_gif"} for kind in media_types) else 2.0 if media_types else 0.0
    lower = text.lower()
    trend_score = 0.0
    for name in trend_names:
        if len(name) >= 2 and name.lower() in lower:
            trend_score = 7.0
            break
    score = round(freshness + velocity + engagement + follower_normalized + source_score + media_score + trend_score, 1)
    reason = f"表示{impressions:,}・投稿後{int(age_minutes)}分・反応率{engagement_rate:.1%}"
    if primary_hint:
        reason += "・公式候補リンクあり"
    if media_score >= 5:
        reason += "・動画あり"
    return score, reason


def _is_watcher_guru_priority(handle: str, text: str, posted_at: dt.datetime, now: dt.datetime) -> bool:
    """Watcher.Guruの金融・暗号資産速報だけを、一次資料確認へ優先送客する。"""
    return (
        handle.lower() == WATCHER_GURU_HANDLE
        and now - posted_at <= dt.timedelta(minutes=90)
        and _contains_term(text, WATCHER_GURU_URGENT_TERMS)
    )


def _is_actionable_signal(item: dict[str, Any]) -> bool:
    """古い状態にも適用する、発見候補として最低限必要なノイズ除外。"""
    text = " ".join([str(item.get("headline", "")), str(item.get("summary", ""))])
    if _contains_term(text, BLOCKED_TERMS):
        return False
    if str(item.get("discovery_type", "")) == "watchlist_timeline":
        try:
            impressions = int(item.get("impression_count", 0) or 0)
        except (TypeError, ValueError):
            impressions = 0
        if str(item.get("handle", "")).lower() in MEDIA_HANDLES:
            return False
        if impressions < 1_000 or not _contains_term(text, MATERIAL_SIGNAL_TERMS):
            return False
    return True


def _normalize_search_response(response: Any, topic: str, now: dt.datetime, trend_names: set[str]) -> list[dict[str, Any]]:
    users = {str(_value(user, "id", "")): user for user in _includes(response, "users")}
    media = {str(_value(item, "media_key", "")): item for item in _includes(response, "media")}
    rows: list[dict[str, Any]] = []
    for tweet in _items(response):
        text = _compact(str(_value(tweet, "text", "")))
        posted_at = _parse_time(_value(tweet, "created_at"))
        author_id = str(_value(tweet, "author_id", ""))
        author = users.get(author_id)
        handle = str(_value(author, "username", "")).lstrip("@")
        tweet_id = str(_value(tweet, "id", ""))
        if not tweet_id or not posted_at or not handle or not _contains_term(text, TOPIC_TERMS):
            continue
        if _contains_term(text, BLOCKED_TERMS):
            continue
        metrics = _metrics(tweet)
        user_metrics = _metrics(author)
        urls = _tweet_urls(tweet)
        media_types = _media_types(tweet, media)
        score, reason = _score(
            text=text,
            posted_at=posted_at,
            metrics=metrics,
            followers=user_metrics["followers_count"],
            urls=urls,
            media_types=media_types,
            verified=bool(_value(author, "verified", False)),
            trend_names=trend_names,
            now=now,
        )
        watcher_priority = _is_watcher_guru_priority(handle, text, posted_at, now)
        if watcher_priority:
            # 速報直後は表示数が育っていない。Watcher.Guruは発見専用にとどめ、
            # 後段で必ず一次資料へ戻す前提で、実測反応を待たず優先確認する。
            score += 18.0
            reason += "・Watcher.Guru速報を一次資料確認へ優先"
        if score < 34:
            continue
        rows.append(
            {
                "post_id": tweet_id,
                "post_url": f"https://x.com/{handle}/status/{tweet_id}",
                "handle": handle,
                "posted_at": _iso(posted_at),
                "headline": text[:180],
                "summary": text[:700],
                "why_trending": reason,
                "topic": topic,
                "discovery_type": "official_x_api",
                "score": score,
                "impression_count": metrics["impression_count"],
                "like_count": metrics["like_count"],
                "reply_count": metrics["reply_count"],
                "repost_count": metrics["retweet_count"],
                "quote_count": metrics["quote_count"],
                "author_followers": user_metrics["followers_count"],
                "linked_urls": urls[:5],
                "media_types": media_types,
                "has_video": any(kind in {"video", "animated_gif"} for kind in media_types),
                "source_priority": "watcherguru" if watcher_priority else "",
                "discovered_at": _iso(now),
            }
        )
    return rows


def _search_recent(
    client: Any,
    topic: str,
    query: str,
    now: dt.datetime,
    trends: set[str],
    *,
    sort_order: str = "relevancy",
) -> list[dict[str, Any]]:
    end_time = now - dt.timedelta(seconds=15)
    response = client.search_recent_tweets(
        query=query,
        start_time=end_time - dt.timedelta(hours=2),
        end_time=end_time,
        max_results=10,
        sort_order=sort_order,
        tweet_fields=["author_id", "attachments", "created_at", "entities", "lang", "public_metrics"],
        expansions=["author_id", "attachments.media_keys"],
        user_fields=["public_metrics", "username", "verified"],
        media_fields=["duration_ms", "media_key", "preview_image_url", "type"],
        user_auth=True,
    )
    return _normalize_search_response(response, topic, now, trends)


def _merge_signals(state: dict[str, Any], signals: list[dict[str, Any]], now: dt.datetime) -> None:
    cutoff = now - SIGNAL_MAX_AGE
    kept: dict[str, dict[str, Any]] = {}
    for row in state.get("signals", []):
        posted_at = _parse_time(row.get("posted_at"))
        key = str(row.get("post_id") or row.get("post_url") or "")
        if key and posted_at and posted_at >= cutoff:
            kept[key] = row
    for row in signals:
        key = str(row.get("post_id") or row.get("post_url") or "")
        if not key:
            continue
        existing = kept.get(key)
        if existing:
            # 同一投稿は、実測値が大きい新しい観測だけで更新する。
            if float(row.get("score", 0)) >= float(existing.get("score", 0)):
                row["seen_count"] = int(existing.get("seen_count", 1) or 1) + 1
                kept[key] = row
        else:
            row["seen_count"] = 1
            kept[key] = row
    state["signals"] = sorted(
        kept.values(),
        key=lambda item: (float(item.get("score", 0)), str(item.get("posted_at", ""))),
        reverse=True,
    )[:MAX_STORED_SIGNALS]


def ingest_watchlist_posts(
    posts: list[dict[str, Any]],
    now: dt.datetime | None = None,
    path: Path = WATCHLIST_SIGNAL_STATE_PATH,
) -> int:
    """成長施策が既に読んだリスト新着を再利用し、追加読取なしで候補化する。"""
    checked_at = now or _utcnow()
    state = load_state(path)
    rows: list[dict[str, Any]] = []
    for item in posts:
        text = _compact(str(item.get("text", "")))
        posted_at = _parse_time(item.get("posted_at"))
        handle = str(item.get("handle", "")).lstrip("@")
        post_url = _normal_url(str(item.get("post_url", "")))
        matched = re.search(r"/(?:status|statuses)/(\d+)(?:[/?#]|$)", post_url)
        if not text or not posted_at or not handle or not matched or not _contains_term(text, TOPIC_TERMS):
            continue
        if _contains_term(text, BLOCKED_TERMS):
            continue
        if handle.lower() in MEDIA_HANDLES:
            continue
        metrics = {
            "impression_count": max(0, int(item.get("impression_count", 0) or 0)),
            "like_count": max(0, int(item.get("like_count", 0) or 0)),
            "reply_count": 0,
            "retweet_count": 0,
            "quote_count": 0,
        }
        score, reason = _score(
            text=text, posted_at=posted_at, metrics=metrics, followers=0, urls=[], media_types=[],
            verified=False, trend_names=set(), now=checked_at,
        )
        if score < 30 or metrics["impression_count"] < 1_000 or not _contains_term(text, MATERIAL_SIGNAL_TERMS):
            continue
        rows.append(
            {
                "post_id": matched.group(1), "post_url": post_url, "handle": handle,
                "posted_at": _iso(posted_at), "headline": text[:180], "summary": text[:700],
                "why_trending": reason + "・厳選リストで観測", "topic": "watchlist",
                "discovery_type": "watchlist_timeline", "score": score,
                "impression_count": metrics["impression_count"], "like_count": metrics["like_count"],
                "reply_count": 0, "repost_count": 0, "quote_count": 0, "author_followers": 0,
                "linked_urls": [], "media_types": [], "has_video": False, "discovered_at": _iso(checked_at),
            }
        )
    _merge_signals(state, rows, checked_at)
    save_state(state, path)
    return len(rows)


def discovery_signals(now: dt.datetime | None = None, path: Path = STATE_PATH, limit: int = 16) -> list[dict[str, str]]:
    """自動投稿の一次資料リサーチに渡す、鮮度・実測値付きの発見シグナルを返す。"""
    checked_at = now or _utcnow()
    cutoff = checked_at - SIGNAL_MAX_AGE
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in _signal_rows(path):
        posted_at = _parse_time(item.get("posted_at"))
        url = _normal_url(str(item.get("post_url", "")))
        if not posted_at or posted_at < cutoff or not url or url in seen or not _is_actionable_signal(item):
            continue
        seen.add(url)
        media_hint = "動画あり" if item.get("has_video") else ""
        rows.append(
            {
                "title": str(item.get("headline", ""))[:180],
                "source": f"X @{str(item.get('handle', '')).lstrip('@')}（公式API実測）"[:80],
                "published": _iso(posted_at),
                "url": url,
                "summary": (
                    f"{str(item.get('summary', ''))[:460]} / 注目理由: {item.get('why_trending', '')} {media_hint}"
                )[:700],
                "discovery_type": str(item.get("discovery_type", "official_x_api")),
                # 後段がどの発見を処理しているかを明確にし、一般探索へ埋もれさせない。
                "signal_id": str(item.get("post_id", "")),
                "signal_score": str(item.get("score", "")),
                "impression_count": str(item.get("impression_count", "0")),
                "has_video": bool(item.get("has_video", False)),
                "source_handle": str(item.get("handle", "")).lstrip("@"),
                "source_priority": str(item.get("source_priority", "")),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def promotion_signals(
    now: dt.datetime | None = None,
    path: Path = STATE_PATH,
    limit: int = 3,
    promotion_path: Path | None = None,
) -> list[dict[str, str]]:
    """即時確認に回す高反応シグナルだけを、未処理のものから返す。

    X投稿は発見専用で、ここで返したこと自体は公開を意味しない。後段は同じ出来事の
    一次資料が確認できた場合だけ予約・公開し、確認不能なら処理済みとして記録する。
    """
    checked_at = now or _utcnow()
    cutoff = checked_at - PROMOTION_SIGNAL_MAX_AGE
    promotion_results = _load_promotion_state(promotion_path).get("results", {})
    eligible: list[dict[str, Any]] = []
    for item in _signal_rows(path):
        posted_at = _parse_time(item.get("posted_at"))
        if not posted_at or posted_at < cutoff or not _is_actionable_signal(item):
            continue
        promotion = promotion_results.get(_normal_url(str(item.get("post_url", ""))), {})
        if isinstance(promotion, dict) and promotion.get("status") in {"reserved", "published", "rejected"}:
            continue
        try:
            impressions = int(item.get("impression_count", 0) or 0)
            score = float(item.get("score", 0) or 0)
        except (TypeError, ValueError):
            continue
        watcher_priority = str(item.get("source_priority", "")) == "watcherguru"
        if not watcher_priority and (impressions < PROMOTION_MIN_IMPRESSIONS or score < PROMOTION_MIN_SCORE):
            continue
        if watcher_priority and score < PROMOTION_MIN_SCORE:
            continue
        eligible.append(item)

    eligible.sort(
        key=lambda row: (
            float(row.get("score", 0) or 0),
            int(row.get("impression_count", 0) or 0),
            str(row.get("posted_at", "")),
        ),
        reverse=True,
    )
    result: list[dict[str, str]] = []
    for item in eligible[:limit]:
        signal = next(
            (
                row
                for row in discovery_signals(checked_at, path, limit=MAX_STORED_SIGNALS)
                if row.get("signal_id") == str(item.get("post_id", ""))
            ),
            None,
        )
        if signal:
            result.append(signal)
    return result


def mark_promotion_result(
    signal_url: str,
    status: str,
    *,
    now: dt.datetime | None = None,
    reason: str = "",
    post_id: str = "",
    path: Path = STATE_PATH,
    promotion_path: Path | None = None,
) -> bool:
    """発見候補の検証結果を永続化し、同じシグナルを繰り返し処理しない。"""
    if status not in {"reserved", "published", "rejected"}:
        raise ValueError("昇格処理の状態が不正です")
    normalized = _normal_url(signal_url)
    if not any(
        _normal_url(str(item.get("post_url", ""))) == normalized
        for item in _signal_rows(path)
    ):
        return False
    state = _load_promotion_state(promotion_path)
    state["results"][normalized] = {
        "status": status,
        "processed_at": _iso(now or _utcnow()),
        "reason": str(reason)[:240],
        "post_id": str(post_id),
    }
    _save_promotion_state(state, promotion_path)
    return True


def _should_deep_scan(state: dict[str, Any], now: dt.datetime, spike: bool) -> bool:
    if _daily_count(state, now, "search") >= MAX_DEEP_SEARCHES_PER_DAY:
        return False
    last = _parse_time(state.get("last_deep_scan_at"))
    return spike or not last or now - last >= DEEP_SCAN_INTERVAL


def _should_scan_watcher_guru(state: dict[str, Any], now: dt.datetime) -> bool:
    if _daily_count(state, now, "watcher_guru_search") >= MAX_WATCHER_GURU_SEARCHES_PER_DAY:
        return False
    last = _parse_time(state.get("last_watcher_guru_scan_at"))
    return not last or now - last >= WATCHER_GURU_SCAN_INTERVAL


def scan(now: dt.datetime | None = None, path: Path = STATE_PATH, client: Any | None = None) -> dict[str, Any]:
    """20分間隔の探索を1回実行し、必要な時だけ実投稿を取得する。"""
    checked_at = now or _utcnow()
    state = load_state(path)
    trend_names: set[str] = {
        str(row.get("trend_name", ""))
        for row in state.get("trends", [])
        if str(row.get("trend_name", ""))
    }
    spike = False
    topic, query = _next_topic(state)

    if _bearer_token() and _daily_count(state, checked_at, "count") < MAX_COUNT_REQUESTS_PER_DAY:
        try:
            count = _recent_count(query, checked_at)
            baseline = _recent_count_baseline(state, topic)
            spike = (baseline >= 3 and count >= max(8, baseline * 2.2)) or count >= 150
            state["scans"].append({
                "checked_at": _iso(checked_at), "action": "count", "topic": topic,
                "count": count, "baseline": baseline, "spike": spike,
            })
        except Exception as exc:
            logger.warning("X Post Countsを見送り: %s", exc)

    if _bearer_token() and _daily_count(state, checked_at, "trend") < MAX_TREND_REQUESTS_PER_DAY:
        old_trends = _today_rows(list(state.get("trends", [])), checked_at, key="checked_at")
        newest = max((_parse_time(row.get("checked_at")) for row in old_trends), default=None)
        if not newest or checked_at - newest >= dt.timedelta(hours=4):
            try:
                trends = _fetch_japan_trends()
                state["trends"].extend({"checked_at": _iso(checked_at), **row} for row in trends)
                state["scans"].append({"checked_at": _iso(checked_at), "action": "trend", "topic": "japan"})
                trend_names.update(str(row.get("trend_name", "")) for row in trends if str(row.get("trend_name", "")))
            except Exception as exc:
                logger.warning("X地域トレンドを見送り: %s", exc)

    found: list[dict[str, Any]] = []
    searched = False
    # Watcher.Guruは一般トピックのローテーションに埋めず、20分ごとに新着だけを
    # 実測で読む。翻訳転載ではなく、同じ出来事の一次資料の検証へ渡すための入口にする。
    if _should_scan_watcher_guru(state, checked_at):
        try:
            watcher_rows = _search_recent(
                client or _get_client(), "watcher_guru", WATCHER_GURU_QUERY, checked_at,
                trend_names, sort_order="recency",
            )
            found.extend(watcher_rows)
            state["last_watcher_guru_scan_at"] = _iso(checked_at)
            state["scans"].append({
                "checked_at": _iso(checked_at), "action": "watcher_guru_search",
                "topic": "watcher_guru", "result_count": len(watcher_rows),
            })
            searched = True
        except Exception as exc:
            state["scans"].append({
                "checked_at": _iso(checked_at), "action": "watcher_guru_search_error",
                "topic": "watcher_guru", "error": str(exc)[:240],
            })
            logger.warning("Watcher.Guru速報探索を見送り: %s", exc)

    if _should_deep_scan(state, checked_at, spike):
        try:
            found = _search_recent(client or _get_client(), topic, query, checked_at, trend_names)
            state["last_deep_scan_at"] = _iso(checked_at)
            state["scans"].append({
                "checked_at": _iso(checked_at), "action": "search", "topic": topic,
                "result_count": len(found), "spike": spike,
            })
            searched = True
        except Exception as exc:
            state["scans"].append({
                "checked_at": _iso(checked_at), "action": "search_error", "topic": topic,
                "error": str(exc)[:240],
            })
            logger.warning("公式X API Recent Searchを見送り: %s", exc)

    _merge_signals(state, found, checked_at)
    save_state(state, path)
    return {"topic": topic, "spike": spike, "searched": searched, "signals": len(found)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="INU公式X APIリサーチエージェント")
    parser.add_argument("--state", default=str(STATE_PATH))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = scan(path=Path(args.state))
    logger.info("X探索結果: topic=%s spike=%s searched=%s signals=%s", result["topic"], result["spike"], result["searched"], result["signals"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
