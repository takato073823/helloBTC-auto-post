#!/usr/bin/env python3
"""INUのフォロワー1,000人到達まで使う、限定的なX成長施策。

動画で示された4施策をそのまま大量実行するのではなく、一次資料と読者価値を
確認できる場合だけ自動実行する。記事投稿・毎時投稿の状態とは完全に分離する。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import re
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

from grok_client import generate_x_json
from inu_auto_hourly import (
    SECONDARY_HOSTS,
    USER_AGENT,
    _compact_text,
    _parse_timestamp,
    normalize_url,
)
from inu_hourly_dispatcher import JST
from inu_persona import VOICE_PROMPT
from inu_post import compose_post, validate_post
from inu_source_capture import SourceCaptureSpec, capture_official_evidence
from inu_growth_watchlist import (
    JAPANESE_LANGUAGE,
    STATE_PATH as WATCHLIST_STATE_PATH,
    TARGET_SIZE,
    is_japanese_timeline,
    load_denylist,
    load_state as load_watchlist_state,
    save_state as save_watchlist_state,
)
from inu_x_research_agent import ingest_watchlist_posts
from x_list_client import XListClient
from x_poster import _get_client, like_tweet, post_info_reply_tweet, post_info_tweet


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "inu_growth_boost_state.json"
ARTIFACT_DIR = SCRIPT_DIR / "artifacts" / "inu-growth-boost"
FOLLOWER_TARGET = 1_000
MAX_AGE_MINUTES = {"A": 25, "B": 90, "C": 90, "D": 120}
DAILY_LIMITS = {"A": 1, "B": 6, "C": 3, "D": 1}
PUBLISHING_TACTICS = {"A", "C", "D"}
VALID_TACTICS = set(MAX_AGE_MINUTES)
MIN_UNDERLIKED_IMPRESSIONS = 500
MAX_UNDERLIKED_LIKE_RATE = 0.03
MIN_REPLY_LIKES = 100
MIN_FOLLOWER_ADMISSION_FOLLOWERS = 1_000
MIN_FOLLOWER_ADMISSION_IMPRESSIONS = 500
MAX_FOLLOWER_ADMISSIONS_PER_RUN = 5
MAX_FOLLOWER_EVALUATIONS_PER_RUN = 5
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
EXTERNAL_MEDIA_HOSTS = SECONDARY_HOSTS | {
    "decrypt.co",
    "theblock.co",
    "blockworks.co",
    "dlnews.com",
}
BOOST_B_KEYWORDS = (
    "bitcoin", "btc", "ethereum", "eth", "crypto", "cryptocurrency", "stablecoin", "etf",
    "onchain", "blockchain", "defi", "token", "wallet", "exchange", "web3", "bitcoin",
    "ビットコイン", "仮想通貨", "暗号資産", "オンチェーン", "ステーブルコイン", "取引所",
    "米国株", "日本株", "株式", "金利", "インフレ", "ai", "半導体", "比特币", "加密",
    "链上", "稳定币", "交易所", "美股", "宏观", "人工智能",
)
BOOST_B_BLOCKED = ("airdrop", "giveaway", "referral", "招待", "キャンペーン", "プレゼント", "価格予想")


BOOST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "tactic": {"type": "string", "enum": ["A", "B", "C", "D"]},
                    "post_url": {"type": "string"},
                    "target_handle": {"type": "string"},
                    "posted_at": {"type": "string"},
                    "source_name": {"type": "string"},
                    "source_url": {"type": "string"},
                    "source_published_at": {"type": "string"},
                    "evidence_anchor": {"type": "string"},
                    "hook": {"type": "string"},
                    "facts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 2,
                    },
                    "opinion": {"type": "string"},
                    "reply_text": {"type": "string"},
                    "reply_options": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 0,
                        "maxItems": 3,
                    },
                    "mention_context": {"type": "string"},
                    "trend_keyword": {"type": "string"},
                    "why_this_matters": {"type": "string"},
                    "why_target": {"type": "string"},
                    "estimated_recent_impressions": {"type": "integer", "minimum": 0},
                },
                "required": [
                    "tactic", "post_url", "target_handle", "posted_at", "source_name",
                    "source_url", "source_published_at", "evidence_anchor", "hook", "facts",
                    "opinion", "reply_text", "reply_options", "mention_context", "trend_keyword", "why_this_matters", "why_target",
                    "estimated_recent_impressions",
                ],
            },
            "maxItems": 12,
        },
        "skip_reason": {"type": "string"},
    },
    "required": ["signals", "skip_reason"],
}


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {"version": 1, "stopped": False, "actions": []}
    state = json.loads(path.read_text(encoding="utf-8"))
    state.setdefault("version", 1)
    state.setdefault("stopped", False)
    state.setdefault("actions", [])
    state.setdefault("seen_follower_ids", {})
    return state


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _status_id(url: str) -> str:
    matched = re.search(r"/(?:status|statuses)/(\d{15,22})(?:[/?#]|$)", url)
    return matched.group(1) if matched else ""


def _is_x_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return host in {"x.com", "twitter.com", "mobile.twitter.com"}


def _value(item, key: str, default=None):
    return item.get(key, default) if isinstance(item, dict) else getattr(item, key, default)


def _metrics(item) -> dict[str, int]:
    raw = _value(item, "public_metrics", {}) or {}
    if not isinstance(raw, dict):
        raw = vars(raw) if hasattr(raw, "__dict__") else {}
    result: dict[str, int] = {}
    for key in ("impression_count", "like_count", "reply_count", "retweet_count", "quote_count", "followers_count"):
        try:
            result[key] = int(raw.get(key, 0) or 0)
        except (TypeError, ValueError):
            result[key] = 0
    return result


def _tweet_row(tweet, handle: str) -> dict | None:
    tweet_id = str(_value(tweet, "id", ""))
    posted_at = _value(tweet, "created_at")
    if not tweet_id or not posted_at:
        return None
    metrics = _metrics(tweet)
    return {
        "post_url": f"https://x.com/{handle}/status/{tweet_id}",
        "target_handle": handle,
        "handle": handle,
        "posted_at": str(posted_at),
        "text": str(_value(tweet, "text", "")),
        "impression_count": metrics["impression_count"],
        "like_count": metrics["like_count"],
    }


def _is_relevant_investment_post(text: str) -> bool:
    lower = text.lower()
    def includes(keyword: str) -> bool:
        token = keyword.lower()
        if token in {"ai", "eth"}:
            return bool(re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", lower))
        return token in lower
    return (
        any(includes(keyword) for keyword in BOOST_B_KEYWORDS)
        and not any(blocked in lower for blocked in BOOST_B_BLOCKED)
    )


def underliked_reach_score(row: dict) -> float:
    """実際の表示数に対して反応が薄い投稿を優先する。"""
    impressions = max(0, int(row.get("impression_count", 0) or 0))
    likes = max(0, int(row.get("like_count", 0) or 0))
    if impressions < MIN_UNDERLIKED_IMPRESSIONS:
        return -1.0
    like_rate = likes / impressions
    if like_rate > MAX_UNDERLIKED_LIKE_RATE:
        return -1.0
    # 大きなリーチをまず優先し、同程度ならいいね率が低い方を選ぶ。
    return round(impressions * (1.0 - like_rate), 4)


def follower_count(client=None) -> int:
    response = (client or _get_client()).get_me(user_fields=["public_metrics"], user_auth=True)
    metrics = getattr(response.data, "public_metrics", None)
    if metrics is None and isinstance(response.data, dict):
        metrics = response.data.get("public_metrics")
    if not isinstance(metrics, dict) or not isinstance(metrics.get("followers_count"), int):
        raise RuntimeError("Xのフォロワー数を取得できません")
    return metrics["followers_count"]


def _recent_actions(state: dict, now: dt.datetime) -> list[dict]:
    cutoff = now.astimezone(dt.timezone.utc) - dt.timedelta(days=7)
    rows = []
    for row in state.get("actions", []):
        try:
            if _parse_timestamp(str(row.get("acted_at", ""))) >= cutoff:
                rows.append(row)
        except (TypeError, ValueError):
            continue
    return rows


def _daily_count(state: dict, tactic: str, now: dt.datetime) -> int:
    local_day = now.astimezone(JST).date()
    count = 0
    for row in _recent_actions(state, now):
        try:
            if row.get("tactic") == tactic and _parse_timestamp(row["acted_at"]).astimezone(JST).date() == local_day:
                count += 1
        except (KeyError, TypeError, ValueError):
            continue
    return count


def build_prompt(now: dt.datetime, state: dict, target_posts: list[dict] | None = None) -> str:
    used = [row.get("post_url", "") for row in _recent_actions(state, now)]
    watchlist_context = ""
    if target_posts:
        links = "\n".join(f"- @{row['handle']}: {row['post_url']}" for row in target_posts[:24])
        watchlist_context = f"""
今回の対象は、A〜D用に適格性を確認済みのXリストの新着投稿です。
A/Bは必ず次の投稿またはその投稿者だけを対象にしてください。Cは投稿内容・反応数・一次資料の
条件を満たす場合に限り、リスト外のアカウントも対象にできます。Dはトレンド検証後の独立投稿です。
{links}
"""
    return f"""
あなたは、投資情報アカウントINUの成長施策を厳選する編集者です。現在は
{now.astimezone(JST).isoformat()}（日本時間）。X Searchで直近2時間の投稿を確認し、
次の4施策のうち、実行に値する候補だけをsignalsに入れてください。候補なしは正常です。

A（メンション／専門家への文脈ある接点）: 暗号資産・投資の関連専門家または公式アカウントの
新しい投稿に対し、一次資料で補足できる場合だけ。無関係なタグ付け、拡散依頼、定型あいさつは禁止。
B（高リーチ・低反応投稿へのいいね）: INUと対象読者が重なる監視対象の新規投稿。表示数が高い一方で
いいね率が低い投稿を最優先にする。広告、煽り、価格予想、一般論、転載には使わない。
C（話題投稿への高付加価値返信）: 注目を集めている投稿に、元投稿より具体的な数字・条件・一次資料を
足せる場合だけ。reply_textは元投稿の言い換えでなく、読者が保存したくなる具体的補足にする。
D（トレンドワード接続）: Xでいま上昇している話題を、暗号資産・米国株・日本株・AI・マクロの
事実へ自然に接続できる場合だけ。関係のないトレンド便乗は禁止。

共通条件:
- post_urlは、X Searchで実際に確認したstatus URLだけ。
- source_urlは、政府・規制当局・企業・取引所・ETF発行体・公式データ・公式GitHub等の一次資料。
  報道記事、Cointelegraph、Decrypt、CoinDesk、Reuters、Nikkei、まとめ、ブログを絶対に使わない。
- source_urlの根拠文言をevidence_anchorに原文のまま8文字以上で書く。見つからない場合は候補にしない。
- 事実は確認済みのものだけ。売買推奨、価格予想、煽り、案件、プレゼント、無関係な人物の話題は禁止。
- A/C/Dの文章は、1行の具体見出し、事実、僕の見方の順。自然な日本語で、本文URLは書かない。
- Aは、専門領域と今この人を見る理由を具体的に書いたmention_contextを返す。mention_contextには
  @target_handle を1回だけ含める。Aは独立した紹介投稿として送るため、拡散依頼・定型あいさつ・無関係なタグ付けは禁止。
- Cは実際に100いいね以上ある投資・暗号資産・金融・AI関連の投稿だけ。reply_optionsに、80〜210字の
  返信案を異なる切り口で3案入れる。reply_textにはそのうち最も事実関係が明確な1案を同じ内容で入れる。
  元投稿の具体的な論点を一次資料の数字・条件で補強する。「この論点は公式資料の〜とも整合します」のように
  投稿主の分析が検証で裏づけられる場合だけ補足し、過剰な持ち上げ・依頼・定型文を使わない。
- Bのreply_text等は空文字でよい。Bはpost_urlと対象の妥当性だけを返す。
- Aはestimated_recent_impressionsが1,000以上。Cのいいね数は後段のX APIで実測確認するため、
  表示数の推測だけで候補を水増ししない。
- DはX Searchで現在上昇しているトレンドワードを調べたうえでtrend_keywordを必ず書く。トレンド語が
  投稿本文の中に自然に入り、暗号資産・株式・AI・マクロの一次情報と一文で接続できないなら候補にしない。
- すべて投稿後2時間以内、同じ出来事は1件だけ。

{watchlist_context}

{VOICE_PROMPT}

すでに実行済み・除外対象の投稿: {json.dumps(used, ensure_ascii=False)}
""".strip()


def collect_candidates(now: dt.datetime, state: dict, target_posts: list[dict] | None = None) -> list[dict]:
    payload, _ = generate_x_json(
        build_prompt(now, state, target_posts),
        schema_name="inu_growth_boost_candidates",
        schema=BOOST_SCHEMA,
        from_date=now.astimezone(JST).date() - dt.timedelta(days=1),
        to_date=now.astimezone(JST).date(),
        max_output_tokens=3200,
    )
    return [row for row in payload.get("signals", []) if isinstance(row, dict)]


def active_watchlist_handles() -> set[str]:
    """200件の適格リストに実際に同期済みのアカウントだけを返す。"""
    try:
        watchlist = load_watchlist_state(WATCHLIST_STATE_PATH)
    except Exception:
        return set()
    return {
        str(row.get("handle", "")).lower()
        for row in watchlist.get("members", {}).values()
        if (
            row.get("tier") == "member"
            and row.get("language") == JAPANESE_LANGUAGE
            and row.get("handle")
        )
    }


def recent_watchlist_posts(client=None) -> list[dict]:
    """200件を個別巡回せず、Xリストの統合タイムラインから新着を取得する。"""
    watchlist = load_watchlist_state(WATCHLIST_STATE_PATH)
    list_id = str(watchlist.get("list_id", "")).strip()
    members = {
        str(row.get("user_id")): str(row.get("handle"))
        for row in watchlist.get("members", {}).values()
        if (
            row.get("tier") == "member"
            and row.get("language") == JAPANESE_LANGUAGE
            and row.get("user_id")
            and row.get("handle")
        )
    }
    if not list_id or not members:
        return []
    rows: list[dict] = []
    for tweet in XListClient(client or _get_client()).recent_tweets(list_id, max_pages=3):
        author_id = str(_value(tweet, "author_id", ""))
        handle = members.get(author_id)
        if handle:
            row = _tweet_row(tweet, handle)
            if row:
                rows.append(row)
    return rows


def _boost_b_from_watchlist_posts(now: dt.datetime, state: dict, posts: list[dict]) -> dict | None:
    if _daily_count(state, "B", now) >= DAILY_LIMITS["B"]:
        return None
    ranked: list[tuple[float, dt.datetime, dict]] = []
    for row in posts:
        try:
            posted_at = _parse_timestamp(str(row["posted_at"]))
        except (TypeError, ValueError):
            continue
        age = now.astimezone(dt.timezone.utc) - posted_at
        if age < dt.timedelta(minutes=-5) or age > dt.timedelta(minutes=MAX_AGE_MINUTES["B"]):
            continue
        if not _is_relevant_investment_post(str(row.get("text", ""))):
            continue
        score = underliked_reach_score(row)
        if score < 0:
            continue
        candidate = {
            "tactic": "B", "post_url": row["post_url"], "target_handle": row["handle"],
            "posted_at": posted_at.isoformat(), "source_name": "", "source_url": "",
            "source_published_at": "", "evidence_anchor": "", "hook": "", "facts": [],
            "opinion": "", "reply_text": "", "reply_options": [], "mention_context": "", "trend_keyword": "",
            "why_this_matters": "", "estimated_recent_impressions": int(row.get("impression_count", 0) or 0),
            "why_target": "高い表示数に対していいね率が低く、INU読者に関連する新規投稿のため",
            "actual_impression_count": int(row.get("impression_count", 0) or 0),
            "actual_like_count": int(row.get("like_count", 0) or 0),
        }
        ranked.append((score, posted_at, candidate))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return ranked[0][2] if ranked else None


def discover_boost_b(now: dt.datetime, state: dict, client=None, target_posts: list[dict] | None = None) -> dict | None:
    """通知ONの代わりに、適格200件の統合タイムラインを優先して初動を拾う。"""
    watched = _boost_b_from_watchlist_posts(now, state, target_posts or [])
    if watched:
        return watched
    # Bは原則として「いいね」リスト内だけで実行する。リストが既に存在するのに
    # 一時的に読めない時は、リスト外の12件へ広げず安全に見送る。
    if active_watchlist_handles():
        return None
    # 初回構築中でも、リスト外を代替対象にしない。Bは日本語の「いいね」
    # リストだけを対象にし、対象がいなければ安全に見送る。
    return None


def _response_data(response) -> list:
    data = _value(response, "data", response)
    return data if isinstance(data, list) else []


def _follower_admission_record(profile, tweets: list, own_user_id: str, denylist: set[str], now: dt.datetime) -> dict | None:
    """新規フォロワーを、日本語・投稿反応の条件を満たす場合だけ入れる。"""
    user_id = str(_value(profile, "id", ""))
    handle = str(_value(profile, "username", "")).lower().lstrip("@")
    if not user_id or user_id == own_user_id or not HANDLE_RE.fullmatch(handle):
        return None
    if handle in denylist or bool(_value(profile, "protected", False)):
        return None
    followers = _metrics(profile)["followers_count"]
    if followers < MIN_FOLLOWER_ADMISSION_FOLLOWERS:
        return None
    if not is_japanese_timeline(tweets):
        return None
    rows = [_tweet_row(tweet, handle) for tweet in tweets]
    rows = [row for row in rows if row]
    if not rows or not any(_is_relevant_investment_post(str(row["text"])) for row in rows):
        return None
    max_impressions = max((int(row["impression_count"]) for row in rows), default=0)
    # 「500を超える」ため、500ちょうどは対象にしない。
    if max_impressions <= MIN_FOLLOWER_ADMISSION_IMPRESSIONS:
        return None
    newest = max((_parse_timestamp(str(row["posted_at"])) for row in rows), default=now)
    if now.astimezone(dt.timezone.utc) - newest > dt.timedelta(days=14):
        return None
    best = max(rows, key=lambda row: int(row["impression_count"]))
    # 標準のウォッチリスト評価と比較できる70〜80点台に留め、低品質会員を
    # 不当に押し出さない。満員時は後段の差分条件も通過した場合だけ入替える。
    score = round(70.0 + min(10.0, max_impressions / 10_000), 1)
    return {
        "handle": handle,
        "user_id": user_id,
        "focus": "INUをフォロー後に基準を満たしたアカウント",
        "role": "qualified_new_follower",
        "language": JAPANESE_LANGUAGE,
        "followers": followers,
        "score": score,
        "last_seen_at": newest.astimezone(dt.timezone.utc).isoformat(),
        "recent_post_url": best["post_url"],
        "why_relevant": "日本語中心の投稿で、フォロワー1,000人以上かつ直近10投稿内に表示500超を確認",
        "observed_interactions": int(best["like_count"]),
        "observed_impressions": max_impressions,
        "engagement_component": 15.0,
        "hard_gate": True,
        "tier": "member",
        "added_at": now.astimezone(dt.timezone.utc).isoformat(),
        "low_score_cycles": 0,
        "score_history": [score],
        "admission_source": "qualified_new_follower",
    }


def admit_qualified_new_followers(state: dict, client, now: dt.datetime) -> list[dict]:
    """新規フォロワーを『いいね』リストへ反映し、直後のB候補として返す。"""
    watchlist = load_watchlist_state(WATCHLIST_STATE_PATH)
    list_id = str(watchlist.get("list_id", "")).strip()
    if not list_id:
        return []
    list_client = XListClient(client)
    own_user_id = list_client.owner_id()
    actual_ids = list_client.member_ids(list_id)
    denylist = load_denylist()
    seen = state.setdefault("seen_follower_ids", {})
    follower_kwargs = {
        "max_results": 100,
        "user_fields": ["created_at", "description", "protected", "public_metrics"],
        "user_auth": True,
    }
    page_token = str(state.get("follower_scan_pagination_token", "")).strip()
    if page_token:
        follower_kwargs["pagination_token"] = page_token
    try:
        response = client.get_users_followers(
            own_user_id,
            **follower_kwargs,
        )
    except Exception as exc:
        logger.info("新規フォロワーの確認を見送り: %s", exc)
        return []

    meta = _value(response, "meta", {}) or {}
    next_token = _value(meta, "next_token", "")
    state["follower_scan_pagination_token"] = str(next_token or "")
    admitted_posts: list[dict] = []
    admitted_count = 0
    evaluated_count = 0
    changed = False
    for profile in _response_data(response):
        if admitted_count >= MAX_FOLLOWER_ADMISSIONS_PER_RUN or evaluated_count >= MAX_FOLLOWER_EVALUATIONS_PER_RUN:
            break
        user_id = str(_value(profile, "id", ""))
        if not user_id or user_id in seen or user_id in actual_ids:
            continue
        handle = str(_value(profile, "username", "")).lower().lstrip("@")
        # 直近投稿の読取枠を使う前に、プロフィールだけで分かる条件を弾く。
        if (
            not HANDLE_RE.fullmatch(handle)
            or handle in denylist
            or bool(_value(profile, "protected", False))
            or _metrics(profile)["followers_count"] < MIN_FOLLOWER_ADMISSION_FOLLOWERS
        ):
            continue
        existing = watchlist.get("members", {}).get(handle, {})
        cooldown = str(existing.get("cooldown_until", ""))
        if existing.get("tier") == "excluded" and cooldown:
            try:
                if _parse_timestamp(cooldown) > now.astimezone(dt.timezone.utc):
                    continue
            except (TypeError, ValueError):
                continue
        try:
            evaluated_count += 1
            tweets = _response_data(client.get_users_tweets(
                user_id, max_results=10, exclude=["retweets", "replies"],
                tweet_fields=["created_at", "lang", "public_metrics"], user_auth=True,
            ))
        except Exception as exc:
            logger.info("新規フォロワーの直近投稿を取得できません: %s / %s", user_id, exc)
            continue
        record = _follower_admission_record(profile, tweets, own_user_id, denylist, now)
        if not record:
            seen[user_id] = now.astimezone(dt.timezone.utc).isoformat()
            continue

        members = watchlist.setdefault("members", {})
        active = [item for item in members.values() if item.get("tier") == "member"]
        if len(actual_ids) >= TARGET_SIZE:
            weakest = min(
                (item for item in active if str(item.get("user_id")) in actual_ids),
                key=lambda item: float(item.get("score", 0) or 0), default=None,
            )
            added_at = _parse_timestamp(str(weakest.get("added_at", ""))) if weakest and weakest.get("added_at") else None
            if (
                not weakest
                or (added_at and now.astimezone(dt.timezone.utc) - added_at < dt.timedelta(days=7))
                or float(record["score"]) < float(weakest.get("score", 0) or 0) + 5.0
            ):
                seen[user_id] = now.astimezone(dt.timezone.utc).isoformat()
                continue
            removed, _ = list_client.remove_members(list_id, [str(weakest["user_id"])])
            if not removed:
                continue
            weakest.update({
                "tier": "excluded", "exclusion_reason": "replaced_by_qualified_new_follower",
                "cooldown_until": (now.astimezone(dt.timezone.utc) + dt.timedelta(days=30)).isoformat(),
            })
            actual_ids.discard(str(weakest["user_id"]))
        added, _ = list_client.add_members(list_id, [user_id])
        if user_id not in added:
            continue
        members[record["handle"]] = record
        actual_ids.add(user_id)
        changed = True
        admitted_count += 1
        seen[user_id] = now.astimezone(dt.timezone.utc).isoformat()
        admitted_posts.extend(row for row in (_tweet_row(tweet, record["handle"]) for tweet in tweets) if row)
    if len(seen) > 2_000:
        state["seen_follower_ids"] = dict(list(seen.items())[-2_000:])
    if changed:
        watchlist["last_follower_admission_at"] = now.astimezone(dt.timezone.utc).isoformat()
        save_watchlist_state(watchlist, WATCHLIST_STATE_PATH)
    return admitted_posts


def _refresh_live_metrics(candidate: dict, client) -> None:
    """X APIの実測値でB/Cの閾値を確認する。推定値だけで反応しない。"""
    post_id = _status_id(normalize_url(str(candidate.get("post_url", ""))))
    if not post_id:
        raise ValueError("X投稿IDが取得できません")
    response = client.get_tweet(post_id, tweet_fields=["public_metrics"], user_auth=True)
    metrics = _metrics(_value(response, "data", {}))
    candidate["actual_impression_count"] = metrics["impression_count"]
    candidate["actual_like_count"] = metrics["like_count"]


def _verify_primary_source(candidate: dict) -> str:
    source_url = normalize_url(str(candidate.get("source_url", "")))
    parts = urlsplit(source_url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    if parts.scheme != "https" or not host or any(host == blocked or host.endswith(f".{blocked}") for blocked in EXTERNAL_MEDIA_HOSTS):
        raise ValueError("一次資料URLが不正です")
    response = requests.get(source_url, timeout=25, headers={"User-Agent": USER_AGENT})
    response.raise_for_status()
    if "html" not in response.headers.get("content-type", "").lower():
        raise ValueError("自動切り抜き可能な公式HTMLではありません")
    soup = BeautifulSoup(response.text, "lxml")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    visible = _compact_text(soup.get_text(" ", strip=True))
    anchor = _compact_text(str(candidate.get("evidence_anchor", "")))
    if len(anchor) < 8 or anchor not in visible:
        raise ValueError("一次資料に根拠文言を確認できません")
    return source_url


def validate_candidate(candidate: dict, state: dict, now: dt.datetime) -> str:
    tactic = str(candidate.get("tactic", ""))
    if tactic not in VALID_TACTICS:
        raise ValueError("施策種別が不正です")
    post_url = normalize_url(str(candidate.get("post_url", "")))
    post_id = _status_id(post_url)
    if not _is_x_url(post_url) or not post_id:
        raise ValueError("X投稿URLが不正です")
    if _daily_count(state, tactic, now) >= DAILY_LIMITS[tactic]:
        raise ValueError("この施策の日次上限に達しています")
    if any(_status_id(str(row.get("post_url", ""))) == post_id for row in _recent_actions(state, now)):
        raise ValueError("このX投稿にはすでに反応済みです")
    posted_at = _parse_timestamp(str(candidate.get("posted_at", "")))
    age = now.astimezone(dt.timezone.utc) - posted_at
    if age < dt.timedelta(minutes=-5) or age > dt.timedelta(minutes=MAX_AGE_MINUTES[tactic]):
        raise ValueError("施策に必要な鮮度を満たしません")
    handle = str(candidate.get("target_handle", "")).strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle):
        raise ValueError("対象アカウントが不正です")
    known_targets = active_watchlist_handles()
    # いいねは監視リストだけに限定する。Cは、100いいね以上かつ一次資料で補強できる
    # 場合に限りリスト外も許可し、Dはトレンド自体を対象にした独立投稿である。
    if tactic in {"A", "B"} and known_targets and handle.lower() not in known_targets:
        raise ValueError("A/B対象リスト外のアカウントです")
    if tactic == "A" and int(candidate.get("estimated_recent_impressions", 0)) < 1_000:
        raise ValueError("Aの対象に必要な反応水準を満たしません")
    if tactic == "C" and int(candidate.get("actual_like_count", 0) or 0) < MIN_REPLY_LIKES:
        raise ValueError("Cの対象に必要ないいね数を満たしません")
    if tactic == "D":
        keyword = str(candidate.get("trend_keyword", "")).strip()
        text = " ".join([str(candidate.get("hook", "")), *map(str, candidate.get("facts", [])), str(candidate.get("opinion", ""))])
        if len(keyword) < 2 or keyword.lower() not in text.lower():
            raise ValueError("Dのトレンド接続が不明です")
    if tactic == "B":
        impressions = int(candidate.get("actual_impression_count", 0) or 0)
        likes = int(candidate.get("actual_like_count", 0) or 0)
        if impressions < MIN_UNDERLIKED_IMPRESSIONS or likes / max(impressions, 1) > MAX_UNDERLIKED_LIKE_RATE:
            raise ValueError("Bの高表示・低いいね条件を満たしません")
        if len(str(candidate.get("why_target", "")).strip()) < 18:
            raise ValueError("Bの対象理由が不足しています")
        return post_id
    source_published = _parse_timestamp(str(candidate.get("source_published_at", "")))
    source_age = now.astimezone(dt.timezone.utc) - source_published
    if source_age < dt.timedelta(minutes=-15) or source_age > dt.timedelta(hours=6):
        raise ValueError("一次資料の鮮度を満たしません")
    if len(str(candidate.get("why_this_matters", "")).strip()) < 18:
        raise ValueError("読者価値が不足しています")
    reply_text = str(candidate.get("reply_text", "")).strip()
    if tactic == "A":
        mention_context = str(candidate.get("mention_context", "")).strip()
        if not 35 <= len(mention_context) <= 140 or f"@{handle}" not in mention_context:
            raise ValueError("Aの専門家紹介文が不十分です")
        validate_post(compose_post(
            hook=str(candidate["hook"]),
            facts=[*list(candidate["facts"]), mention_context],
            opinion=str(candidate["opinion"]), tags=["仮想通貨"],
        ))
    elif tactic == "C":
        if not 80 <= len(reply_text) <= 210:
            raise ValueError("返信の情報量が不足または過多です")
        if re.search(r"https?://|www\.", reply_text, flags=re.IGNORECASE):
            raise ValueError("返信本文に外部URLを直書きできません")
        validate_post(reply_text)
    else:
        validate_post(compose_post(
            hook=str(candidate["hook"]), facts=list(candidate["facts"]),
            opinion=str(candidate["opinion"]), tags=["仮想通貨"],
        ))
    _verify_primary_source(candidate)
    return post_id


def _capture_evidence(candidate: dict, tactic: str, now: dt.datetime) -> Path:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    output = ARTIFACT_DIR / f"{now.strftime('%Y%m%dT%H%M%S')}-{tactic}.png"
    source_url = _verify_primary_source(candidate)
    spec = SourceCaptureSpec(
        source_url=source_url,
        source_name=str(candidate["source_name"]),
        published_at=_parse_timestamp(str(candidate["source_published_at"])).date().isoformat(),
        evidence_type="official_text_crop",
        selector="[data-inu-growth-evidence]",
        is_primary_source=True,
    )
    return asyncio.run(capture_official_evidence(spec, output, evidence_anchor=str(candidate["evidence_anchor"])))


def _record(state: dict, candidate: dict, tactic: str, now: dt.datetime, *, action: str, tweet_id: str = "") -> None:
    state["actions"].append({
        "tactic": tactic,
        "action": action,
        "post_url": normalize_url(str(candidate["post_url"])),
        "target_handle": str(candidate["target_handle"]).lstrip("@"),
        "source_url": normalize_url(str(candidate.get("source_url", ""))),
        "tweet_id": str(tweet_id),
        "acted_at": now.astimezone(dt.timezone.utc).isoformat(),
    })
    state["actions"] = state["actions"][-500:]


def _reply_variants(candidate: dict) -> list[dict]:
    """Grokの返信案を重複なく並べ、公開前検証で一案ずつ選べるようにする。"""
    values = [*list(candidate.get("reply_options", []) or []), candidate.get("reply_text", "")]
    variants: list[dict] = []
    seen: set[str] = set()
    for value in values:
        reply = " ".join(str(value or "").split()).strip()
        if not reply or reply in seen:
            continue
        seen.add(reply)
        variant = dict(candidate)
        variant["reply_text"] = reply
        variants.append(variant)
    return variants or [dict(candidate)]


def execute_one(state: dict, candidates: list[dict], now: dt.datetime, client=None) -> str | None:
    # 返信・独立投稿を優先する。初動いいねは、十分な投稿候補がない時だけ行う。
    order = {"C": 0, "A": 1, "D": 2, "B": 3}
    for candidate in sorted(candidates, key=lambda row: order.get(str(row.get("tactic")), 99)):
        tactic = str(candidate.get("tactic", ""))
        variants = _reply_variants(candidate) if tactic == "C" else [candidate]
        for variant in variants:
            try:
                if tactic in {"B", "C"}:
                    _refresh_live_metrics(variant, client or _get_client())
                post_id = validate_candidate(variant, state, now)
                if tactic == "B":
                    if not like_tweet(post_id):
                        raise RuntimeError("いいねに失敗しました")
                    _record(state, variant, tactic, now, action="like")
                    return "B"
                image = _capture_evidence(variant, tactic, now)
                if tactic == "C":
                    tweet_id = post_info_reply_tweet(str(variant["reply_text"]).strip(), image, post_id)
                else:
                    facts = list(variant["facts"])
                    if tactic == "A":
                        facts.append(str(variant["mention_context"]).strip())
                    text = compose_post(
                        hook=str(variant["hook"]), facts=facts,
                        opinion=str(variant["opinion"]), tags=["仮想通貨"],
                    )
                    tweet_id = post_info_tweet(text, image)
                if not tweet_id:
                    raise RuntimeError("画像付きX投稿に失敗しました")
                _record(state, variant, tactic, now, action="publish", tweet_id=str(tweet_id))
                return tactic
            except Exception as exc:
                logger.info("ブースト%sを見送り: %s", tactic or "?", exc)
    return None


def run(args: argparse.Namespace) -> int:
    now = dt.datetime.now(dt.timezone.utc)
    state = load_state(Path(args.state))
    followers = follower_count()
    state["last_follower_count"] = followers
    state["checked_at"] = now.isoformat()
    if followers >= FOLLOWER_TARGET:
        state["stopped"] = True
        state["stop_reason"] = f"followers_reached_{FOLLOWER_TARGET}"
        save_state(state, Path(args.state))
        logger.info("フォロワー%d人に到達したためブースト施策を停止しました", followers)
        return 0
    if state.get("stopped"):
        logger.info("ブースト施策は停止中です: %s", state.get("stop_reason", ""))
        save_state(state, Path(args.state))
        return 0
    api = _get_client()
    try:
        admitted_posts = admit_qualified_new_followers(state, api, now)
    except Exception as exc:
        # フォロワー判定に失敗しても、既存の成長施策は止めない。
        logger.info("新規フォロワーのリスト審査を見送り: %s", exc)
        admitted_posts = []
    try:
        # 200件の対象リストは統合タイムラインを1回読むだけで確認する。
        # 個別アカウントを大量巡回しないため、X APIの読取枠を圧迫しない。
        target_posts = admitted_posts + recent_watchlist_posts(api)
    except Exception as exc:
        logger.info("ブースト対象リストを取得できません: %s", exc)
        target_posts = []
    try:
        # 成長施策で既に取得した統合タイムラインを、通常投稿の発見候補にも再利用する。
        # ここでは状態保存だけで、投稿・いいね・返信を追加で実行しない。
        if target_posts:
            ingest_watchlist_posts(target_posts, now)
    except Exception as exc:
        logger.info("リスト新着の探索エージェント連携を見送り: %s", exc)
    try:
        # BはGrokの候補抽出を待たず、厳選リストの新規投稿だけを初動で確認する。
        # これにより「通知ON＋最初のいいね」を10分間隔で自動化する。
        boost_b = discover_boost_b(now, state, api, target_posts=target_posts)
    except Exception as exc:
        logger.warning("Bの初動監視に失敗したため、候補探索へ進みます: %s", exc)
        boost_b = None
    if boost_b:
        tactic = execute_one(state, [boost_b], now, api)
        if tactic:
            save_state(state, Path(args.state))
            logger.info("ブースト実行結果: %s", tactic)
            return 0
        # リスト取得時と直前の実測で値が変わることがある。Bが見送りになった場合は、
        # 同じ回でA/C/Dの候補探索を続ける。
    try:
        candidates = collect_candidates(now, state, target_posts)
    except Exception as exc:
        # X Searchの引用が返らない・一時的に検索できない場合は、古い候補や
        # 推測で穴埋めせず、この回を候補なしとして終了する。
        state["last_skip_reason"] = f"x_search_unavailable: {exc}"
        save_state(state, Path(args.state))
        logger.warning("ブースト候補を取得できないため今回は見送ります: %s", exc)
        return 0
    # X Searchの取得自体は成功した。候補なしでも過去の一時エラーを残さず、
    # 次の自動実行の状態判定を正確にする。
    state["last_skip_reason"] = ""
    tactic = execute_one(state, candidates, now, api)
    save_state(state, Path(args.state))
    logger.info("ブースト実行結果: %s", tactic or "候補なし")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="INUの限定X成長施策")
    parser.add_argument("--state", default=str(STATE_PATH))
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
