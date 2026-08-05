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
    load_curated_x_sources,
    normalize_url,
)
from inu_hourly_dispatcher import JST
from inu_persona import VOICE_PROMPT
from inu_post import compose_post, validate_post
from inu_source_capture import SourceCaptureSpec, capture_official_evidence
from x_poster import _get_client, like_tweet, post_info_reply_tweet, post_info_tweet


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "inu_growth_boost_state.json"
ARTIFACT_DIR = SCRIPT_DIR / "artifacts" / "inu-growth-boost"
FOLLOWER_TARGET = 1_000
MAX_AGE_MINUTES = {"A": 25, "B": 20, "C": 90, "D": 120}
DAILY_LIMITS = {"A": 1, "B": 3, "C": 1, "D": 1}
PUBLISHING_TACTICS = {"A", "C", "D"}
VALID_TACTICS = set(MAX_AGE_MINUTES)
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
                    "mention_context": {"type": "string"},
                    "trend_keyword": {"type": "string"},
                    "why_this_matters": {"type": "string"},
                    "why_target": {"type": "string"},
                    "estimated_recent_impressions": {"type": "integer", "minimum": 0},
                },
                "required": [
                    "tactic", "post_url", "target_handle", "posted_at", "source_name",
                    "source_url", "source_published_at", "evidence_anchor", "hook", "facts",
                    "opinion", "reply_text", "mention_context", "trend_keyword", "why_this_matters", "why_target",
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
    return state


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _status_id(url: str) -> str:
    matched = re.search(r"/(?:status|statuses)/(\d{15,22})(?:[/?#]|$)", url)
    return matched.group(1) if matched else ""


def _is_x_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return host in {"x.com", "twitter.com", "mobile.twitter.com"}


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


def build_prompt(now: dt.datetime, state: dict) -> str:
    used = [row.get("post_url", "") for row in _recent_actions(state, now)]
    return f"""
あなたは、投資情報アカウントINUの成長施策を厳選する編集者です。現在は
{now.astimezone(JST).isoformat()}（日本時間）。X Searchで直近2時間の投稿を確認し、
次の4施策のうち、実行に値する候補だけをsignalsに入れてください。候補なしは正常です。

A（メンション／専門家への文脈ある接点）: 暗号資産・投資の関連専門家または公式アカウントの
新しい投稿に対し、一次資料で補足できる場合だけ。無関係なタグ付け、拡散依頼、定型あいさつは禁止。
B（通知＋初動）: INUと対象読者が重なる監視対象の新規投稿。いいねだけを送るため、広告、煽り、
価格予想、一般論、転載には使わない。
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
- Cのreply_textは80〜210字で、相手への過剰な持ち上げ・依頼・定型文を使わない。
- Bのreply_text等は空文字でよい。Bはpost_urlと対象の妥当性だけを返す。
- Aはestimated_recent_impressionsが1,000以上、Cは10,000以上でなければ候補にしない。
- Dはtrend_keywordを必ず書く。自然な接続を一文で説明できないなら候補にしない。
- すべて投稿後2時間以内、同じ出来事は1件だけ。

{VOICE_PROMPT}

すでに実行済み・除外対象の投稿: {json.dumps(used, ensure_ascii=False)}
""".strip()


def collect_candidates(now: dt.datetime, state: dict) -> list[dict]:
    payload, _ = generate_x_json(
        build_prompt(now, state),
        schema_name="inu_growth_boost_candidates",
        schema=BOOST_SCHEMA,
        from_date=now.astimezone(JST).date() - dt.timedelta(days=1),
        to_date=now.astimezone(JST).date(),
        max_output_tokens=3200,
    )
    return [row for row in payload.get("signals", []) if isinstance(row, dict)]


def discover_boost_b(now: dt.datetime, state: dict, client=None) -> dict | None:
    """通知ONの代わりに、厳選リストを定期ポーリングして初動だけを拾う。"""
    if _daily_count(state, "B", now) >= DAILY_LIMITS["B"]:
        return None
    api = client or _get_client()
    newest: tuple[dt.datetime, dict] | None = None
    for source in load_curated_x_sources():
        handle = str(source["handle"])
        try:
            user = api.get_user(username=handle, user_auth=True).data
            user_id = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
            if not user_id:
                continue
            response = api.get_users_tweets(
                user_id,
                max_results=5,
                exclude=["retweets", "replies"],
                tweet_fields=["created_at", "lang"],
                user_auth=True,
            )
        except Exception as exc:
            logger.info("Bの監視対象を取得できません: @%s / %s", handle, exc)
            continue
        for tweet in getattr(response, "data", None) or []:
            tweet_id = str(getattr(tweet, "id", None) or (tweet.get("id") if isinstance(tweet, dict) else ""))
            text = str(getattr(tweet, "text", None) or (tweet.get("text", "") if isinstance(tweet, dict) else ""))
            created = getattr(tweet, "created_at", None) or (tweet.get("created_at") if isinstance(tweet, dict) else None)
            if not tweet_id or not created:
                continue
            try:
                posted_at = _parse_timestamp(str(created))
            except (TypeError, ValueError):
                continue
            age = now.astimezone(dt.timezone.utc) - posted_at
            lower = text.lower()
            if age < dt.timedelta(minutes=-5) or age > dt.timedelta(minutes=MAX_AGE_MINUTES["B"]):
                continue
            if not any(keyword.lower() in lower for keyword in BOOST_B_KEYWORDS):
                continue
            if any(blocked in lower for blocked in BOOST_B_BLOCKED):
                continue
            candidate = {
                "tactic": "B",
                "post_url": f"https://x.com/{handle}/status/{tweet_id}",
                "target_handle": handle,
                "posted_at": posted_at.isoformat(),
                "source_name": "",
                "source_url": "",
                "source_published_at": "",
                "evidence_anchor": "",
                "hook": "",
                "facts": [],
                "opinion": "",
                "reply_text": "",
                "mention_context": "",
                "trend_keyword": "",
                "why_this_matters": "",
                "why_target": f"{source['focus']}を継続して扱う厳選監視対象の新規投稿のため",
                "estimated_recent_impressions": 0,
            }
            if newest is None or posted_at > newest[0]:
                newest = (posted_at, candidate)
    return newest[1] if newest else None


def _verify_primary_source(candidate: dict) -> str:
    source_url = normalize_url(str(candidate.get("source_url", "")))
    parts = urlsplit(source_url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    if parts.scheme != "https" or not host or host in EXTERNAL_MEDIA_HOSTS:
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
    if any(row.get("post_url") == post_url for row in _recent_actions(state, now)):
        raise ValueError("このX投稿にはすでに反応済みです")
    posted_at = _parse_timestamp(str(candidate.get("posted_at", "")))
    age = now.astimezone(dt.timezone.utc) - posted_at
    if age < dt.timedelta(minutes=-5) or age > dt.timedelta(minutes=MAX_AGE_MINUTES[tactic]):
        raise ValueError("施策に必要な鮮度を満たしません")
    handle = str(candidate.get("target_handle", "")).strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle):
        raise ValueError("対象アカウントが不正です")
    if tactic == "A" and int(candidate.get("estimated_recent_impressions", 0)) < 1_000:
        raise ValueError("Aの対象に必要な反応水準を満たしません")
    if tactic == "C" and int(candidate.get("estimated_recent_impressions", 0)) < 10_000:
        raise ValueError("Cの対象に必要な話題性を満たしません")
    if tactic == "D" and len(str(candidate.get("trend_keyword", "")).strip()) < 2:
        raise ValueError("Dのトレンド接続が不明です")
    if tactic == "B":
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


def execute_one(state: dict, candidates: list[dict], now: dt.datetime) -> str | None:
    # 返信・独立投稿を優先する。初動いいねは、十分な投稿候補がない時だけ行う。
    order = {"C": 0, "A": 1, "D": 2, "B": 3}
    for candidate in sorted(candidates, key=lambda row: order.get(str(row.get("tactic")), 99)):
        tactic = str(candidate.get("tactic", ""))
        try:
            post_id = validate_candidate(candidate, state, now)
            if tactic == "B":
                if not like_tweet(post_id):
                    raise RuntimeError("いいねに失敗しました")
                _record(state, candidate, tactic, now, action="like")
                return "B"
            image = _capture_evidence(candidate, tactic, now)
            if tactic == "C":
                tweet_id = post_info_reply_tweet(str(candidate["reply_text"]).strip(), image, post_id)
            else:
                facts = list(candidate["facts"])
                if tactic == "A":
                    facts.append(str(candidate["mention_context"]).strip())
                text = compose_post(
                    hook=str(candidate["hook"]), facts=facts,
                    opinion=str(candidate["opinion"]), tags=["仮想通貨"],
                )
                tweet_id = post_info_tweet(text, image)
            if not tweet_id:
                raise RuntimeError("画像付きX投稿に失敗しました")
            _record(state, candidate, tactic, now, action="publish", tweet_id=str(tweet_id))
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
    try:
        # BはGrokの候補抽出を待たず、厳選リストの新規投稿だけを初動で確認する。
        # これにより「通知ON＋最初のいいね」を10分間隔で自動化する。
        boost_b = discover_boost_b(now, state)
    except Exception as exc:
        logger.warning("Bの初動監視に失敗したため、候補探索へ進みます: %s", exc)
        boost_b = None
    if boost_b:
        tactic = execute_one(state, [boost_b], now)
        save_state(state, Path(args.state))
        logger.info("ブースト実行結果: %s", tactic or "候補なし")
        return 0
    try:
        candidates = collect_candidates(now, state)
    except Exception as exc:
        # X Searchの引用が返らない・一時的に検索できない場合は、古い候補や
        # 推測で穴埋めせず、この回を候補なしとして終了する。
        state["last_skip_reason"] = f"x_search_unavailable: {exc}"
        save_state(state, Path(args.state))
        logger.warning("ブースト候補を取得できないため今回は見送ります: %s", exc)
        return 0
    tactic = execute_one(state, candidates, now)
    save_state(state, Path(args.state))
    logger.info("ブースト実行結果: %s", tactic or "候補なし")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="INUの限定X成長施策")
    parser.add_argument("--state", default=str(STATE_PATH))
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
