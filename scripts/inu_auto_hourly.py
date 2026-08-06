#!/usr/bin/env python3
"""最新の一次情報を調査し、INUの画像付き投稿を毎時1件だけ準備・公開する。"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import re
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from inu_content_types import get_content_policy
from inu_editorial_policy import (
    AUTO_SELECTABLE_TOPIC_TYPES,
    AUTO_POST_PLAYBOOK,
    EDITORIAL_CONSTITUTION,
    validate_auto_post_quality,
)
from inu_growth_insights import load_insight_guidance
from inu_hourly_dispatcher import JST, load_state, save_state, slot_key
from inu_live_post import publish_test_item, validate_test_item
from inu_news_visual import capture_source_hero_image, generate_editorial_news_visual
from inu_overseas_kol import live_visual_posts as collect_overseas_kol_visual_posts
from inu_persona import VOICE_PROMPT
from inu_post import MAX_WEIGHTED_LENGTH, compose_post, validate_post, weighted_length
from inu_source_capture import SourceCaptureSpec, capture_official_evidence
from inu_x_research_agent import discovery_signals as collect_official_x_api_signals
from grok_client import generate_editorial_json, generate_x_json
from llm_client import generate_json, generate_web_json
from scraper import fetch_from_rss
from x_poster import post_quote_tweet, post_video_reference_tweet


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
STATE_PATH = SCRIPT_DIR / "inu_hourly_state.json"
ARTIFACT_DIR = SCRIPT_DIR / "artifacts" / "inu-auto"
PREPARED_PATH = ARTIFACT_DIR / "prepared.json"
CURATED_X_SOURCES_PATH = SCRIPT_DIR / "inu_curated_x_sources.json"
MAX_HISTORY = 1000
MAX_GENERATED_EDITORIAL_VISUALS_PER_DAY = 18
MAX_SCHEDULED_CHECKS = 168
GROWTH_TOPIC_ROTATION = (
    "etf_flow",
    "onchain",
    "market_microstructure",
    "institutional_flow",
    "policy_household",
    "earnings",
    "adoption_kpi",
)
AUTO_TOPIC_TYPES = AUTO_SELECTABLE_TOPIC_TYPES
MARKET_FALLBACK_PRODUCTS = (
    "BTC-USD",
    "ETH-USD",
    "SOL-USD",
    "XRP-USD",
    "DOGE-USD",
    "ADA-USD",
)
# 同じ銘柄・同じ値動きを短時間に繰り返さない。定期枠は毎時1本なので、
# この時間内は6銘柄を一巡させてから同じ銘柄を再検討する。
MARKET_FALLBACK_PRODUCT_COOLDOWN = dt.timedelta(hours=6)
MAX_AGE_HOURS = {
    "breaking_news": 2,
    "developing_story": 6,
    "market_microstructure": 8,
    "etf_flow": 12,
    "institutional_flow": 12,
    "onchain": 8,
    "whale_treasury": 8,
    "earnings": 12,
    "supply_event": 12,
    "adoption_kpi": 12,
    "policy_household": 12,
    # 毎時投稿では、発表から時間が経ったマクロ情報を「速報」として出さない。
    # 後追いの解説は個別の編集投稿で扱い、定期枠では4時間以内に限定する。
    "macro_event": 4,
}
SECONDARY_HOSTS = {
    "bloomberg.com",
    "coindesk.com",
    "coinpost.jp",
    "cointelegraph.com",
    "forbes.com",
    "jp.reuters.com",
    "nikkei.com",
    "reuters.com",
    "theblock.co",
    "yahoo.co.jp",
}
TRUSTED_MEDIA_HOSTS = {
    "bloomberg.com",
    "coindesk.com",
    "decrypt.co",
    "nikkei.com",
    "reuters.com",
    "theblock.co",
}
TRACKING_KEYS = {"gclid", "fbclid", "ref", "source"}
USER_AGENT = "Mozilla/5.0 (compatible; INUPrimarySourceVerifier/1.0)"
SOURCE_VERIFY_TIMEOUT_SECONDS = 12
LOW_VALUE_ROUNDUP_PATTERNS = (
    r"\bwhat happened\b.*\b(today|this week)\b",
    r"\b(daily|weekly|market|crypto)\s+(roundup|wrap(?:-up)?|recap|briefing)\b",
    r"\b(top|biggest)\s+\d+\s+(crypto\s+)?(news|stories|events)\b",
    r"(?:本日|今日|今週).*(?:まとめ|総括)",
    r"(?:ニュース|市場).*(?:まとめ|総括)",
)
# 単に「資料が出た」だけの投稿を防ぐ。数字だけでなく、投資家が見るべき
# 変更・決定・需給・規制上の変化が本文に必要になる。
GENERIC_SOURCE_FILLER_PATTERN = re.compile(
    r"(?:公式(?:ページ|サイト)|資料(?:では|によると)|ページ(?:では|によると))"
)
GENERIC_PUBLICATION_PATTERN = re.compile(r"(?:公表|発表|公開|掲載)(?:へ|予定|しました)?")
MATERIAL_CHANGE_PATTERN = re.compile(
    r"(?:承認|却下|可決|否決|開始|終了|停止|禁止|解禁|導入|撤回|引き上げ|引き下げ|増額|減額|"
    r"上方修正|下方修正|流入|流出|買い戻し|売却|購入|発行|償還|最高値|最安値|急騰|急落|"
    r"金利|利回り|入札|ETF|決算|売上|利益|供給|需要|ハッキング|流出|清算|提携)"
)
MATERIAL_NUMBER_PATTERN = re.compile(
    r"(?:[$¥€£]\s?\d|\d[\d,.]*\s?(?:%|％|ドル|円|億|万|兆|BTC|ETH|株|bp|ベーシス))",
    re.IGNORECASE,
)
# 事前に決まっているIR日程やカレンダーは、結果・変更・市場反応ではない。
# 「今日○時に決算予定」のような候補を、鮮度だけでニュース扱いしない。
ROUTINE_SCHEDULE_PATTERN = re.compile(
    r"(?:IRカレンダー|今後の予定|決算(?:発表)?(?:予定|日)|業績(?:発表)?(?:予定|日)|"
    r"決算説明会|決算(?:を)?発表予定|業績(?:を)?発表予定|定例(?:入札|会合).{0,12}予定)"
)
EARNINGS_RESULT_PATTERN = re.compile(
    r"(?:売上(?:高)?|営業利益|経常利益|純利益|EBITDA|EPS|通期(?:予想|見通し)?|"
    r"業績(?:予想|見通し)|上方修正|下方修正|増収|減収|増益|減益|黒字|赤字|前年比)"
)
# 一件の事件・逮捕だけでは、投資家の資産・市場判断に直結しない。取引所侵害や
# カストディ上の欠陥など、構造的な暗号資産リスクへ発展している場合だけを残す。
LOCAL_CRIME_PATTERN = re.compile(
    r"\b(?:robbery|robber|home invasion|kidnap(?:ping)?|arrested|indicted|charged)\b",
    re.IGNORECASE,
)
STRUCTURAL_CRYPTO_RISK_PATTERN = re.compile(
    r"\b(?:exchange|hack(?:ed|ing)?|breach|exploit|custody|wallet|security|fraud|scam|"
    r"ransomware|regulat(?:or|ion|ory))\b",
    re.IGNORECASE,
)

CANDIDATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "has_candidate": {"type": "boolean"},
        "skip_reason": {"type": "string"},
        "topic_type": {"type": "string", "enum": list(AUTO_TOPIC_TYPES)},
        "hook": {"type": "string"},
        "facts": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 2,
        },
        "opinion": {"type": "string"},
        "source_name": {"type": "string"},
        "source_url": {"type": "string"},
        "published_at": {"type": "string"},
        "evidence_anchor": {"type": "string"},
        "evidence_as_primary": {"type": "boolean"},
        "visual_route": {
            "type": "string",
            "enum": ["official_text_crop", "official_data_crop", "reported_text_crop"],
        },
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 2,
        },
        "why_now": {"type": "string"},
        "reader_interest": {"type": "string"},
        "follow_value": {"type": "string"},
        "is_primary_source": {"type": "boolean"},
    },
    "required": [
        "has_candidate",
        "skip_reason",
        "topic_type",
        "hook",
        "facts",
        "opinion",
        "source_name",
        "source_url",
        "published_at",
        "evidence_anchor",
        "evidence_as_primary",
        "visual_route",
        "tags",
        "why_now",
        "reader_interest",
        "follow_value",
        "is_primary_source",
    ],
}
CANDIDATE_SET_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidates": {
            "type": "array",
            "items": CANDIDATE_SCHEMA,
            "minItems": 0,
            "maxItems": 6,
        },
        "skip_reason": {"type": "string"},
    },
    "required": ["candidates", "skip_reason"],
}
EDITORIAL_REPAIR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hook": {"type": "string"},
        "opinion": {"type": "string"},
        "why_now": {"type": "string"},
        "reader_interest": {"type": "string"},
        "follow_value": {"type": "string"},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 2,
        },
    },
    "required": [
        "hook",
        "opinion",
        "why_now",
        "reader_interest",
        "follow_value",
        "tags",
    ],
}
EDITORIAL_ALTERNATIVES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidates": {
            "type": "array",
            "items": EDITORIAL_REPAIR_SCHEMA,
            "minItems": 1,
            "maxItems": 3,
        },
    },
    "required": ["candidates"],
}
X_SIGNAL_SET_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "post_url": {"type": "string"},
                    "handle": {"type": "string"},
                    "posted_at": {"type": "string"},
                    "headline": {"type": "string"},
                    "summary": {"type": "string"},
                    "why_trending": {"type": "string"},
                    "topic": {"type": "string"},
                },
                "required": [
                    "post_url",
                    "handle",
                    "posted_at",
                    "headline",
                    "summary",
                    "why_trending",
                    "topic",
                ],
            },
            "minItems": 1,
            "maxItems": 12,
        },
        "skip_reason": {"type": "string"},
    },
    "required": ["signals", "skip_reason"],
}
KOL_QUOTE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source_tweet_id": {"type": "string"},
                    "delivery_mode": {"type": "string", "enum": ["x_native_video_reference", "x_native_quote"]},
                    "hook": {"type": "string"},
                    "facts": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2},
                    "opinion": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 2},
                    "why_now": {"type": "string"},
                    "reader_interest": {"type": "string"},
                    "follow_value": {"type": "string"},
                },
                "required": [
                    "source_tweet_id", "delivery_mode", "hook", "facts", "opinion", "tags",
                    "why_now", "reader_interest", "follow_value",
                ],
            },
            "maxItems": 4,
        },
        "skip_reason": {"type": "string"},
    },
    "required": ["candidates", "skip_reason"],
}
def normalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    query = urlencode(
        [
            (key, val)
        for key, val in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
        ]
    )
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, ""))


def is_x_url(value: str) -> bool:
    host = (urlsplit(value).hostname or "").lower().removeprefix("www.")
    return host in {"x.com", "twitter.com", "mobile.twitter.com"}


def _parse_timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("公開日時にはタイムゾーンが必要です")
    return parsed.astimezone(dt.timezone.utc)


def _host_is_secondary(host: str) -> bool:
    host = host.lower().removeprefix("www.")
    return any(host == blocked or host.endswith(f".{blocked}") for blocked in SECONDARY_HOSTS)


def _recent_history(state: dict) -> list[dict]:
    history = list(state.get("history", []))
    if history:
        return history[-24:]
    return [dict(row) for row in state.get("posted_slots", [])][-24:]


def _underrepresented_growth_topics(state: dict, now: dt.datetime, days: int = 7) -> list[str]:
    """直近の偏りを緩和するため、成長に寄与する投稿系統を軽く誘導する。"""
    cutoff = now.astimezone(dt.timezone.utc) - dt.timedelta(days=days)
    counts = {topic: 0 for topic in GROWTH_TOPIC_ROTATION}
    for row in state.get("history", []):
        topic = str(row.get("topic_type", ""))
        if topic not in counts:
            continue
        try:
            posted_at = _parse_timestamp(str(row.get("posted_at", "")))
        except (TypeError, ValueError):
            continue
        if posted_at >= cutoff:
            counts[topic] += 1
    return sorted(GROWTH_TOPIC_ROTATION, key=lambda topic: (counts[topic], GROWTH_TOPIC_ROTATION.index(topic)))[:3]


def is_low_value_single_source_roundup(value: str) -> bool:
    """単一記事を引用しただけでは情報価値が出ない総括見出しを検出する。"""
    normalized = " ".join(str(value).lower().split())
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in LOW_VALUE_ROUNDUP_PATTERNS)


def collect_discovery_signals() -> list[dict[str, str]]:
    """大手暗号資産メディアの最新見出しを、一次資料探索の入口として取得する。"""
    signals: list[dict[str, str]] = []
    try:
        for article in fetch_from_rss(max_per_feed=5):
            if is_low_value_single_source_roundup(str(article.get("title", ""))):
                logger.info("単一ソースの総括記事を発見候補から除外: %s", article.get("title", ""))
                continue
            signals.append(
                {
                    "title": str(article.get("title", ""))[:180],
                    "source": str(article.get("source", ""))[:60],
                    "published": str(article.get("published", ""))[:80],
                    "url": str(article.get("url", ""))[:500],
                    "summary": re.sub(
                        r"<[^>]+>", " ", str(article.get("description", ""))
                    )[:700],
                }
            )
    except Exception as exc:
        logger.warning("ニュース発見フィードを取得できません: %s", exc)
    return signals[:30]


def _is_primary_grok_run() -> bool:
    """復旧枠を除き、毎時2本の独立した候補探索ではGrokを使う。"""
    if not os.environ.get("XAI_API_KEY", "").strip():
        return False
    event_path = os.environ.get("GITHUB_EVENT_PATH", "").strip()
    if not event_path:
        return True
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("GitHubイベントを判定できないため、Grok検索を通常実行します")
        return True
    return event.get("schedule") != "37 * * * *"


def load_curated_x_sources(path: Path = CURATED_X_SOURCES_PATH) -> list[dict[str, str]]:
    """人手で選定したX探索アカウントだけをGrokの優先探索対象にする。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("厳選X情報源を読み込めません: %s", exc)
        return []
    sources: list[dict[str, str]] = []
    for row in payload.get("sources", []):
        if not isinstance(row, dict):
            continue
        handle = str(row.get("handle", "")).strip().lstrip("@")
        focus = str(row.get("focus", "")).strip()
        use_when = str(row.get("use_when", "")).strip()
        if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", handle) or not focus or not use_when:
            logger.warning("形式が不正な厳選X情報源を除外: %s", handle or "(handleなし)")
            continue
        sources.append(
            {
                "handle": handle,
                "language": str(row.get("language", "")).strip()[:20],
                "focus": focus[:120],
                "use_when": use_when[:180],
            }
        )
    # 設定ファイルは人手レビュー済みの少数リスト。探索範囲を十分に確保しつつ、
    # Grokの指示が冗長にならないよう上限を20件にする。
    return sources[:20]


def build_grok_prompt(now: dt.datetime, state: dict) -> str:
    recent_urls = [row.get("discovery_url", "") for row in _recent_history(state)]
    recent_hooks = [row.get("hook", "") for row in _recent_history(state)]
    curated_sources = load_curated_x_sources()
    curated_block = "\n".join(
        f"- @{row['handle']}（{row['focus']}）：{row['use_when']}"
        for row in curated_sources
    ) or "- 現在、厳選情報源の登録はありません。"
    return f"""
あなたは投資情報アカウントINUのX速報リサーチ担当です。現在時刻は
{now.astimezone(JST).isoformat()}（日本時間）です。X Searchを実行し、原則6時間以内に
投稿された重要シグナルを最大12件、重要度順に返してください。6時間以内に不足する場合だけ
12時間以内まで広げてください。

検索対象:
- ビットコイン、イーサリアム、急騰・急落した主要暗号資産、ETFフロー、クジラ・オンチェーン
- 暗号資産取引所、プロジェクト、規制当局、中央銀行、ETF発行体の公式アカウント
- 米国株、日本株、AI企業、金利・雇用・物価、地政学で市場が反応する発表
- 著名人の市場に関する新しい発言、短時間で反応が急増している投稿

優先して確認する厳選X情報源（中国語圏の先行シグナルを拾うための発見専用）:
{curated_block}

条件:
- 公式発表、具体的な数値、価格変動の節目、制度変更を優先する。
- 上記の厳選情報源は優先的にX Searchで確認する。ただし、投稿自体を最終根拠・転載元・投稿文の出典には絶対に使わない。
- インフルエンサー投稿は発見の手掛かりとして採用できるが、噂・煽り・価格予想・広告・キャンペーンは除外する。厳選情報源であっても、公式リンク、トランザクション、企業・取引所・政府の発表、実測データのいずれにも到達できない内容はsignalsに入れない。
- headlineは何が起きたか、why_trendingはなぜ今すぐ調べる価値があるかを具体的に書く。
- post_urlはX Searchで実際に確認したstatus URLだけを使う。
- posted_atは必ずタイムゾーンを含むISO 8601形式（例: 2026-08-05T03:15:00Z）で返す。
- 同じ出来事の転載は1件にまとめ、古い話題で件数を埋めない。
- 出力は日本語。ただしアカウント名、固有名詞、数値は原文を維持する。
- signalsは「投稿する情報」ではなく、一次情報を探すための発見候補である。引用可能なXのstatus投稿が1件でも見つかった場合は、signalsを空にしない。公式・非公式を問わず、広告・煽り・価格予想を除いた上位の出来事を返す。最終的な真偽・一次情報の有無は後段で検証するため、ここで過度に候補を絞り込まない。

再利用禁止のX投稿: {json.dumps(recent_urls, ensure_ascii=False)}
近似テーマ禁止の直近見出し: {json.dumps(recent_hooks, ensure_ascii=False)}
""".strip()


def collect_grok_discovery_signals(now: dt.datetime, state: dict) -> list[dict[str, str]]:
    if not _is_primary_grok_run():
        if os.environ.get("XAI_API_KEY", "").strip():
            logger.info("47分の再確認枠ではGrok検索を省略し、月間予算を守ります")
        return []
    local_date = now.astimezone(JST).date()
    payload, _ = generate_x_json(
        build_grok_prompt(now, state),
        schema_name="inu_x_discovery_signals",
        schema=X_SIGNAL_SET_SCHEMA,
        from_date=local_date - dt.timedelta(days=1),
        to_date=local_date,
        max_output_tokens=3000,
        model=os.environ.get("XAI_RESEARCH_MODEL", "grok-4.3"),
    )
    if not payload.get("signals"):
        logger.info("GrokのX検索は候補なし: %s", payload.get("skip_reason", ""))
    signals: list[dict[str, str]] = []
    seen: set[str] = set()
    invalid_dates = 0
    stale = 0
    invalid_urls = 0
    for row in payload.get("signals", []):
        try:
            posted_at = _parse_timestamp(str(row.get("posted_at", "")))
        except (TypeError, ValueError):
            invalid_dates += 1
            continue
        age = now.astimezone(dt.timezone.utc) - posted_at
        url = normalize_url(str(row.get("post_url", "")))
        if age < dt.timedelta(minutes=-15) or age > dt.timedelta(hours=12):
            stale += 1
            continue
        if not is_x_url(url) or url in seen:
            invalid_urls += 1
            continue
        seen.add(url)
        handle = str(row.get("handle", "")).strip().lstrip("@")
        signals.append(
            {
                "title": str(row.get("headline", ""))[:180],
                "source": f"X @{handle}"[:60],
                "published": posted_at.isoformat(),
                "url": url[:500],
                "summary": (
                    f"{row.get('summary', '')} / 注目理由: {row.get('why_trending', '')}"
                )[:700],
                "discovery_type": "grok_x_search",
            }
        )
    logger.info(
        "GrokのX検索: 取得%d件 / 採用%d件 / 日時不正%d件 / 鮮度外%d件 / URL重複%d件",
        len(payload.get("signals", [])),
        len(signals),
        invalid_dates,
        stale,
        invalid_urls,
    )
    return signals


def build_research_prompt(
    now: dt.datetime,
    state: dict,
    discovery_signals: list[dict[str, str]] | None = None,
) -> str:
    recent = _recent_history(state)
    recent_topics = [row.get("topic_type", "") for row in recent[-8:] if row.get("topic_type")]
    recent_urls = [row.get("source_url", "") for row in recent if row.get("source_url")]
    recent_headlines = [row.get("hook", "") for row in recent if row.get("hook")]
    underrepresented_topics = _underrepresented_growth_topics(state, now)
    insight_guidance = load_insight_guidance()
    local = now.astimezone(JST)
    return f"""
あなたは投資情報アカウントINUの一次情報リサーチ担当です。現在時刻は
{local.isoformat()}（日本時間）です。必ずWeb検索を実行し、この時刻から見て新しい
暗号資産・ビットコイン・米国株・日本株・AI・金融政策・地政学の重要情報を
重要度順に最大6件選んでください。1件目が検証で落ちても次を使えるよう、発信元と
topic_typeが異なる候補を優先してください。

最重要条件:
- 少なくとも「暗号資産公式」「ETF・オンチェーン」「米国企業IR・AI」
  「日本企業IR」「中央銀行・規制当局」「Xで話題になった公式発表」の観点を分けて検索してから比較する。
- ニュースメディアやXの話題は発見に使ってよいが、最終source_urlは発表主体の公式サイト、規制当局、中央銀行、取引所、上場企業IR、ETF発行体、公式データ提供元などの一次資料にする。
- 「Grok X Search」と記載されたシグナルのX URLは発見専用であり、最終source_urlには絶対に使わない。投稿内容を公式発表・一次データで独立に確認できない場合は候補から除外する。
- ニュースメディアやXの投稿は発見専用。Reuters、Nikkei、Bloomberg、CoinDesk、Decrypt、The Block、Cointelegraphなど第三者メディアのURLを最終source_urlにしてはいけない。一次資料へ到達できない場合は候補から除外する。外部記事カードはhelloBTCの価値を薄めるため、自動投稿では絶対に使わない。
- source_urlは今回のWeb検索結果に実際に含まれる、発表主体の一次資料URLだけを使う。
- 公開日時が確認でき、原則12時間以内。速報は2時間以内、続報は6時間以内。
- evidence_anchorは、一次資料ページにそのまま表示される4文字以上の原文を抜き出す。日本語訳しない。
- evidence_as_primaryは、根拠スクリーンショット自体が一目で意味の分かる公式資料・表・チャート・図版の場合だけtrueにする。単なる記事見出しや本文、余白の多いページならfalseにする。trueなら画像はその根拠スクリーンショット1枚だけで投稿する。
- 決算・業績は、売上・利益・通期見通し・修正などの実績または具体的な変更が公開済みの場合だけ選ぶ。決算発表予定、IRカレンダー、説明会予定、発表時刻だけのページは候補から除外する。
- 噂、匿名情報、価格予想、売買推奨、広告、キャンペーン、基礎知識、数日前の話題の言い換えは除外。
- 「What happened today」「今日のまとめ」「市場総括」「daily roundup」など、複数ニュースを束ねただけの単一記事は除外。総括投稿には独立した3件以上の出典と専用図解が必要なため、この自動経路では選ばない。
- まず一次資料を優先する。公式発表、ETF・オンチェーン、企業IR、規制・金融政策、AI、価格・市場構造の順に横断し、同じ分野だけで候補を埋めない。
- 毎時の定期枠は必ず投稿まで到達させる。候補配列にはhas_candidate=trueの項目を最低3件返す。大きな速報がない時間は、直近12時間以内に更新された一次資料から、ETF・オンチェーン・取引所の安全性・金融政策・企業IR・AI・市場構造のいずれかで「今の市場で何が変わっているか」を具体的な数値や決定で示す候補を選ぶ。予定、基礎知識、古いニュース、同じ話題の言い換えは禁止。
- 候補なしを通常の結論にしない。速報性が低い題材で穴埋めするのではなく、Web検索を追加して、最新のX話題を起点に公式発表・実測データ・企業IRへ遡り、画像で意味が伝わる一次資料を伴う候補を作る。
- 単なる企業IRの更新、発表予定、一般的な事業紹介、公開資料の存在だけでは選ばない。候補を比較したうえで、今この時刻に読む必然性が最も強いものだけを上位に置く。X上の話題性は必須ではないが、話題性がない場合でも、数値・制度・需給・安全性・価格に実際の変化があることを示せない候補は除外する。
- 投稿文は日本語。hookは短く具体的な1行。factsは重要な数字・変更点を1〜2文に絞る。
- 候補ごとにreader_interestへ「読者が今これを見る具体的な理由」を一文で書く。単に公式ページ・資料・発表を紹介する文は不可。投資家が見るべき金額、増減、決定、規制変更、需給、価格反応、または次に確認すべき具体的な事項を示せない候補は選ばない。
- follow_valueへ「この出来事を起点に、INUを継続してフォローすると追える投資テーマ・続報」を一文で書く。reader_interestの言い換え、フォロー要求、公式発表の紹介だけは禁止。この値は内部の編集判定・振り返り用で、投稿本文には書かない。
- hook・factsにも、reader_interestの根拠となる具体的な変更点を必ず入れる。「〜を公表へ」「公式ページでは〜」だけの投稿は禁止。
- 事実の要約を繰り返さず、opinionでは「何が変わるか」または「次に何を見るか」を具体的に一つ書く。
- opinionは「僕の見方では」「僕としては」「個人的には」を自然に使い分ける。「僕は、〜と見ています」「〜がポイントです」「節目だと見ています」の定型的な結びは禁止。
- 採用する投稿では、内容を示す絵文字をhookの先頭に1個使う（例：🚨重要速報、📈最高値・上昇、📉急落、⚠️安全性・制度リスク、🏦金融機関・政策）。装飾ではなく、読者がスクロール中に出来事の性質を瞬時に把握するために使う。本文中には使わず、事実と合わない絵文字は使わない。
- 投稿全体は日本語の全角換算を考慮して180〜220以内を目標にし、非常に簡潔にする。
- 出典名とハッシュタグを含めても、本文にURLは書かない。

口調の基準:
{VOICE_PROMPT}

INUの編集憲法:
{EDITORIAL_CONSTITUTION}

自動投稿の品質ゲート:
{AUTO_POST_PLAYBOOK}

直近の自アカウント実績からの選定補助（実績不足なら空。鮮度・一次情報・読者価値が同等の候補だけで使い、
投稿本数を埋める理由にしてはいけない）:
{json.dumps(insight_guidance, ensure_ascii=False)}

直近の投稿系統: {json.dumps(recent_topics, ensure_ascii=False)}
直近7日間で手薄な投稿系統（速報性を損なわない範囲で優先的に検討）: {json.dumps(underrepresented_topics, ensure_ascii=False)}
再利用禁止の出典URL: {json.dumps(recent_urls, ensure_ascii=False)}
重複・近似テーマ禁止の直近見出し: {json.dumps(recent_headlines, ensure_ascii=False)}
大手メディアの最新見出し（一次資料探索だけに使用。最終出典には使用禁止）:
{json.dumps(discovery_signals or [], ensure_ascii=False)}
次は直近と異なる系統を優先する。選択可能なtopic_typeは:
{', '.join(AUTO_TOPIC_TYPES)}
visual_routeは数字・表・チャートが根拠ならofficial_data_crop、それ以外はofficial_text_crop。主要メディア速報だけreported_text_crop。
""".strip()


def _normalize_researched_candidate(candidate: dict) -> dict:
    normalized = dict(candidate)
    normalized.setdefault("evidence_as_primary", False)
    if normalized.get("has_candidate") and normalized.get("topic_type") in AUTO_TOPIC_TYPES:
        policy = get_content_policy(normalized["topic_type"])
        normalized["visual_route"] = policy.visual_route
    return normalized


def research_candidates(
    now: dt.datetime,
    state: dict,
    extra_signals: list[dict[str, str]] | None = None,
) -> tuple[list[dict], list[dict[str, str]], list[dict[str, str]]]:
    signals = list(extra_signals or []) + collect_discovery_signals()
    signals = signals[:42]
    payload, sources = generate_web_json(
        build_research_prompt(now, state, signals),
        schema_name="inu_live_candidate_set",
        schema=CANDIDATE_SET_SCHEMA,
        max_output_tokens=5200,
        # 複数市場から一次資料まで辿る必要があるため、検索選定はTerraを使う。
        model=os.environ.get("INU_RESEARCH_MODEL", "gpt-5.6-terra"),
        request_timeout_seconds=70.0,
    )
    sources.extend(
        {"url": row["url"], "title": row["title"]}
        for row in signals
        if row.get("url") and not is_x_url(row["url"])
    )
    candidates = [
        _normalize_researched_candidate(candidate)
        for candidate in payload.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    return candidates, sources, signals


def research_candidates_with_grok(
    now: dt.datetime, state: dict
) -> tuple[list[dict], list[dict[str, str]], list[dict[str, str]]]:
    """実測X APIとGrokの発見シグナルを併用し、失敗時もWeb調査で継続する。"""
    x_signals: list[dict[str, str]] = []
    try:
        # 公式X APIの探索エージェントが既に収集・採点した実測シグナルを優先する。
        # X投稿は最終出典にせず、後段のWeb調査で必ず一次資料へ戻る。
        x_signals.extend(collect_official_x_api_signals(now))
    except Exception as exc:
        logger.warning("公式X API探索シグナルを読み込めないためGrok探索を継続: %s", exc)
    try:
        x_signals.extend(collect_grok_discovery_signals(now, state))
    except Exception as exc:
        logger.warning("GrokのX検索に失敗したため、公式X API・Web調査で継続: %s", exc)
    unique: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for signal in x_signals:
        url = normalize_url(str(signal.get("url", "")))
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        unique.append(signal)
    return research_candidates(now, state, extra_signals=unique)


def _kol_quote_prompt(now: dt.datetime, state: dict, posts: list[dict]) -> str:
    used = {
        str(row.get("source_tweet_id", ""))
        for row in list(state.get("history", [])) + list(state.get("reservations", []))
        if row.get("source_tweet_id")
    }
    return f"""
あなたは投資情報アカウントINUの海外KOL引用投稿を選定する編集者です。
現在時刻は{now.astimezone(JST).isoformat()}です。以下は、直近10投稿の実測値で
厳選済みの海外金融・暗号資産アカウントが、直近3時間に公開した動画・画像付き投稿です。

採用できるのは、いま見ても投資家の判断材料になる具体的なデータ・市場構造・金融・
暗号資産の論点を、元投稿の動画または画像から一目で把握できるものだけです。
元投稿の翻訳、感想、価格予想、煽り、広告、一般論、人物への言及だけの投稿は採用しない。
本文に書く事実は元投稿で明示された数値・条件・出来事だけに限定し、確認できない断定はしない。

動画がある投稿は delivery_mode を x_native_video_reference にする。画像のみは
x_native_quote にする。どちらも元投稿のネイティブメディアと投稿者表示を保つので、
画像・動画ファイルの再アップロードや本文へのURL直書きは禁止する。

投稿文はINUの口調で、絵文字1つで始める短い見出し、1〜2個の具体的な事実、僕の見方の順。
「出典：」「この投稿によると」「海外で話題」などの説明は書かない。僕の見方は、元投稿を
持ち上げるのではなく、次に確認すべき価格・資金・条件・反応を一つに絞る。
why_now、reader_interest、follow_value は内部判定用で、抽象語だけにしない。

すでに引用済みまたは予約済みの元投稿ID: {json.dumps(sorted(used), ensure_ascii=False)}
候補:
{json.dumps(posts, ensure_ascii=False)}
""".strip()


def _build_overseas_kol_quote_item(now: dt.datetime, state: dict) -> tuple[dict, dict] | None:
    """海外KOLのネイティブ動画・画像を、通常の一次資料候補の次に検討する。"""
    posts = collect_overseas_kol_visual_posts(now, limit=16)
    if not posts:
        return None
    payload, _ = generate_x_json(
        _kol_quote_prompt(now, state, posts),
        schema_name="inu_overseas_kol_native_quote",
        schema=KOL_QUOTE_SCHEMA,
        from_date=now.astimezone(JST).date() - dt.timedelta(days=1),
        to_date=now.astimezone(JST).date(),
        max_output_tokens=2600,
        model=os.environ.get("XAI_RESEARCH_MODEL", "grok-4.3"),
        request_timeout_seconds=55.0,
    )
    by_id = {str(row.get("post_id", "")): row for row in posts}
    used_ids = {
        str(row.get("source_tweet_id", ""))
        for row in list(state.get("history", [])) + list(state.get("reservations", []))
    }
    for raw in payload.get("candidates", []):
        if not isinstance(raw, dict):
            continue
        source_id = str(raw.get("source_tweet_id", ""))
        source = by_id.get(source_id)
        if not source or source_id in used_ids:
            continue
        posted_at = _parse_timestamp(str(source["posted_at"]))
        age = now.astimezone(dt.timezone.utc) - posted_at
        if age < dt.timedelta(minutes=-10) or age > dt.timedelta(hours=3):
            continue
        expected_mode = "x_native_video_reference" if source.get("has_video") else "x_native_quote"
        if str(raw.get("delivery_mode")) != expected_mode:
            continue
        text = compose_post(
            hook=str(raw.get("hook", "")),
            facts=[str(value) for value in raw.get("facts", [])],
            opinion=str(raw.get("opinion", "")),
            tags=[str(value).lstrip("#＃") for value in raw.get("tags", [])][:1],
        )
        if re.search(r"https?://|www\.", text, flags=re.IGNORECASE):
            continue
        try:
            validate_post(text)
        except ValueError:
            continue
        if expected_mode == "x_native_video_reference" and weighted_length(text) > MAX_WEIGHTED_LENGTH - 24:
            continue
        candidate = {
            "topic_type": "x_reaction",
            "source_url": str(source["post_url"]),
            "source_tweet_id": source_id,
            "source_handle": str(source["handle"]),
            "published_at": posted_at.isoformat(),
            "why_now": str(raw.get("why_now", "")),
            "reader_interest": str(raw.get("reader_interest", "")),
            "follow_value": str(raw.get("follow_value", "")),
            "hook": str(raw.get("hook", "")),
            "delivery_mode": expected_mode,
        }
        if min(len(candidate["why_now"].strip()), len(candidate["reader_interest"].strip()), len(candidate["follow_value"].strip())) < 18:
            continue
        item = {
            "id": f"inu_overseas_kol_{source_id}",
            "topic_type": "x_reaction",
            "visual_route": "x_native_video_reference" if expected_mode == "x_native_video_reference" else "x_native_quote",
            "delivery_mode": expected_mode,
            "source_tweet_id": source_id,
            "text": text,
        }
        return item, candidate
    return None


def _prefer_overseas_kol_turn(state: dict) -> bool:
    """新着の海外KOL引用を、一次資料と並ぶ定期的な投稿柱として扱う。

    海外KOLを「他の候補がなかったときだけ」の穴埋めにすると、Xで話題の動画・画像を
    取りこぼす。直近3件にネイティブ引用がなければ優先して検討するが、速報URLが
    明示されたときは呼び出し元が常にそちらを優先する。
    """
    recent = [
        str(row.get("topic_type", ""))
        for row in list(state.get("history", []))[-3:]
        if isinstance(row, dict)
    ]
    return "x_reaction" not in recent


def build_rescue_research_prompt(
    now: dt.datetime,
    state: dict,
    failure_reasons: list[str],
    discovery_signals: list[dict[str, str]],
) -> str:
    """一次探索が不発だった時間枠だけ、別の入口から再探索する指示。"""
    local = now.astimezone(JST)
    recent_urls = [row.get("source_url", "") for row in _recent_history(state)]
    recent_hooks = [row.get("hook", "") for row in _recent_history(state)]
    return f"""
あなたはINUの毎時投稿を必ず成立させる、二回目の一次情報リサーチ担当です。
現在時刻は{local.isoformat()}（日本時間）。最初の探索で候補は見つかったものの、
下記の理由で公開できませんでした。以下とは異なる発表主体・URLをWeb検索し、
新しい一次資料だけから投稿候補を3〜6件返してください。

最初の探索の失敗理由: {json.dumps(failure_reasons[-12:], ensure_ascii=False)}

必須条件:
- source_urlは、政府・規制当局・中央銀行・上場企業IR・取引所・ETF発行体・
  プロジェクト公式・公式データ提供元の、今回のWeb検索結果に現れたHTTPSのHTMLページだけにする。
- Reuters、Nikkei、Bloomberg、CoinDesk、Decrypt、The Block、Cointelegraph等の
  第三者メディア、X投稿、カレンダー、予定だけの発表、広告、まとめ記事は禁止。
- 公開済みか更新済みの数値、決定、制度変更、需給、価格節目、決算実績のどれかを、
  evidence_anchorの原文とfactsで明示する。根拠ページを切り抜いて意味が分かること。
- まず、Xで話題のシグナルから公式資料へ戻る。そこに使えるものがなければ、
  ETF日次データ、オンチェーン・取引所の公式データ、規制当局、企業IR、金融政策、
  AI企業の更新を横断して追加検索する。
- 発表から原則12時間以内。速報は2時間以内、マクロは4時間以内に限る。
- has_candidate=trueを最低3件返す。候補なしで終えず、同じ話題の言い換えではなく
  発表主体とtopic_typeを分散させる。
- hookは事実を短く示す1行で、性質に合う絵文字を先頭に一つ使う。
- opinionは僕の一人称で、売買推奨をせずに「次に確認する具体的な対象」を一つ述べる。
- reader_interestは今見る理由、follow_valueは今後追う別の続報対象にして、互いの言い換えにしない。

口調の基準:
{VOICE_PROMPT}

自動投稿の品質ゲート:
{AUTO_POST_PLAYBOOK}

再利用禁止の出典URL: {json.dumps(recent_urls, ensure_ascii=False)}
近似テーマ禁止の直近見出し: {json.dumps(recent_hooks, ensure_ascii=False)}
X・RSSからの発見シグナル（発見専用。最終出典にはしない）:
{json.dumps(discovery_signals[:30], ensure_ascii=False)}
選択可能なtopic_type: {', '.join(AUTO_TOPIC_TYPES)}
""".strip()


def research_rescue_candidates(
    now: dt.datetime,
    state: dict,
    failure_reasons: list[str],
    discovery_signals: list[dict[str, str]],
) -> tuple[list[dict], list[dict[str, str]]]:
    """候補を捨てずに、別の一次情報の組み合わせで一度だけ再探索する。"""
    payload, sources = generate_web_json(
        build_rescue_research_prompt(now, state, failure_reasons, discovery_signals),
        schema_name="inu_live_candidate_rescue_set",
        schema=CANDIDATE_SET_SCHEMA,
        max_output_tokens=5200,
        model=os.environ.get("INU_RESEARCH_MODEL", "gpt-5.6-terra"),
        request_timeout_seconds=60.0,
    )
    candidates = [
        _normalize_researched_candidate(candidate)
        for candidate in payload.get("candidates", [])
        if isinstance(candidate, dict)
    ]
    return candidates, sources


EDITORIAL_REPAIR_ERROR_MARKERS = (
    "見出しが短すぎて",
    "今投稿する必然性",
    "読者が今見る",
    "僕の見方として",
    "今投稿する理由と読者価値",
    "継続フォロー価値",
    "投稿文を安全に",
)


def _is_editorial_repairable_error(error: Exception) -> bool:
    message = str(error)
    return any(marker in message for marker in EDITORIAL_REPAIR_ERROR_MARKERS)


def repair_candidate_editorial_copy(candidate: dict, failure_reason: str) -> dict:
    """検証済みの事実を変えず、投稿として成立する表現だけを一度修復する。"""
    facts = [str(value).strip() for value in candidate.get("facts", []) if str(value).strip()]
    prompt = f"""
INUの毎時投稿候補の編集欄だけを修復してください。根拠・数値・固有名詞を新たに作ったり、
以下の検証済みfactsやevidence_anchorにない事実を加えたりしてはいけません。
URL・出典・媒体名・ハッシュタグの説明を本文へ入れないでください。

検証済みのfacts: {json.dumps(facts, ensure_ascii=False)}
根拠原文: {candidate.get('evidence_anchor', '')}
現在の見出し: {candidate.get('hook', '')}
現在の不備: {failure_reason}

書き直すのはhook、opinion、why_now、reader_interest、follow_value、tagsだけです。
- hookは先頭に出来事に合う絵文字を一つ、続けて何が変わったかを短く書く。
- opinionは必ず「僕」を使い、売買推奨をせず、次に追う数値・条件・反応を一つだけ示す。
- why_nowは更新時点または新しい数値、reader_interestは今の判断に関わる理由、
  follow_valueは別の続報テーマにする。三つを言い換えにしない。
- INUの自然な日本語。定型の「節目だと見ています」「ポイントです」は使わない。

口調: {VOICE_PROMPT}
""".strip()
    repaired = generate_json(
        prompt,
        schema_name="inu_editorial_copy_repair",
        schema=EDITORIAL_REPAIR_SCHEMA,
        max_output_tokens=1100,
        model=os.environ.get("INU_EDITORIAL_MODEL", "gpt-5.6-luna"),
    )
    updated = dict(candidate)
    updated.update(repaired)
    return updated


def _grok_editorial_copy_prompt(candidate: dict) -> str:
    """一次資料で固定した事実を、Grokの編集対象として明示する。"""
    facts = [str(value).strip() for value in candidate.get("facts", []) if str(value).strip()]
    return f"""
あなたは投資情報アカウントINUの編集者です。以下は一次資料で検証済みの投稿候補です。
この事実を増減・言い換えによる意味変更をせず、Xでスクロールを止め、続けてフォローする
理由が伝わる自然な日本語の投稿文を3案作成してください。

絶対条件:
- URL、出典名、媒体名、未確認の数値・固有名詞・推測は一切追加しない。
- factsと根拠原文以外の事実は書かない。売買推奨、価格予想、煽り、定型句は禁止。
- hookは出来事に合う絵文字1つで始め、短く「何が変わったか」を示す。
- opinionは必ず「僕」を使い、次に確認すべき具体的な条件・数字・反応を一つだけ示す。
- why_now、reader_interest、follow_valueは内部判定用。抽象語・同じ内容の言い換えにしない。
- tagsは1〜2個。本文に「出典：」「速報」「海外で話題」は入れない。

topic_type: {candidate.get('topic_type', '')}
現在の見出し: {candidate.get('hook', '')}
検証済みfacts: {json.dumps(facts, ensure_ascii=False)}
根拠原文: {candidate.get('evidence_anchor', '')}
口調: {VOICE_PROMPT}
""".strip()


def _grok_editorial_copy_options(candidate: dict) -> list[dict]:
    """Grokの複数案から、事実以外を変更できない編集欄だけを取り出す。"""
    if os.environ.get("INU_GROK_EDITORIAL_ENABLED", "true").strip().lower() not in {"1", "true", "yes"}:
        return []
    payload = generate_editorial_json(
        _grok_editorial_copy_prompt(candidate),
        schema_name="inu_verified_editorial_alternatives",
        schema=EDITORIAL_ALTERNATIVES_SCHEMA,
        max_output_tokens=2200,
        model=os.environ.get("XAI_EDITORIAL_MODEL", os.environ.get("XAI_RESEARCH_MODEL", "grok-4.3")),
        request_timeout_seconds=55.0,
    )
    options: list[dict] = []
    for raw in payload.get("candidates", []):
        if not isinstance(raw, dict):
            continue
        option = {
            key: raw.get(key)
            for key in ("hook", "opinion", "why_now", "reader_interest", "follow_value", "tags")
        }
        if all(option.get(key) not in {None, ""} for key in option if key != "tags") and option.get("tags"):
            options.append(option)
    return options


def _select_grok_editorial_copy(
    candidate: dict,
    sources: list[dict[str, str]],
    state: dict,
    now: dt.datetime,
) -> dict:
    """複数案を既存品質ゲートで選別し、最初に通ったものだけを採用する。"""
    try:
        options = _grok_editorial_copy_options(candidate)
    except Exception as exc:
        logger.info("Grok編集案を使わず既存候補で継続: %s", exc)
        return candidate
    for index, copy in enumerate(options, start=1):
        option = dict(candidate)
        option.update(copy)
        try:
            validate_candidate(option, sources, state, now)
            logger.info("Grok編集案%dを品質ゲート通過として採用", index)
            return option
        except Exception as exc:
            logger.info("Grok編集案%dを除外: %s", index, exc)
    return candidate


def research_candidate(
    now: dt.datetime, state: dict
) -> tuple[dict, list[dict[str, str]], list[dict[str, str]]]:
    """旧呼び出しとの互換用。新しい自動経路はresearch_candidatesを使う。"""
    candidates, sources, signals = research_candidates(now, state)
    if candidates:
        return candidates[0], sources, signals
    return {
        "has_candidate": False,
        "skip_reason": "適切な一次情報がありません",
    }, sources, signals


def research_priority_signal(now: dt.datetime, state: dict, priority_url: str) -> tuple[dict, list[dict[str, str]]]:
    """外部記事は発見専用。一次資料がない速報の自動投稿は行わない。"""
    del now, state, priority_url
    raise LookupError("外部メディア記事はhelloBTCの自動投稿の最終出典に使いません")


def _is_near_recent_topic(title: str, state: dict) -> bool:
    recent = " ".join(str(row.get("hook", "")) for row in _recent_history(state)).lower()
    normalized = re.sub(r"[^a-z0-9一-鿿ぁ-んァ-ン]+", " ", title.lower())
    common = {"bitcoin", "crypto", "market", "latest", "today", "reports", "says"}
    tokens = {token for token in normalized.split() if len(token) >= 5 and token not in common}
    return any(token in recent for token in tokens)


def _is_local_crime_without_structural_crypto_impact(signal: dict[str, str]) -> bool:
    """局地的な事件を、暗号資産ニュースの穴埋めとして選ばない。"""
    text = " ".join(
        [str(signal.get("title", "")), str(signal.get("summary", ""))]
    )
    return bool(
        LOCAL_CRIME_PATTERN.search(text)
        and not STRUCTURAL_CRYPTO_RISK_PATTERN.search(text)
    )


def trusted_media_signals(
    now: dt.datetime,
    state: dict,
    signals: list[dict[str, str]],
) -> list[dict[str, str]]:
    eligible: list[tuple[dt.datetime, dict[str, str]]] = []
    for signal in signals:
        title = signal.get("title", "").strip()
        summary = " ".join(signal.get("summary", "").split()).strip()
        if is_low_value_single_source_roundup(title):
            continue
        if _is_local_crime_without_structural_crypto_impact(signal):
            continue
        if len(title) < 12 or len(summary) < 40:
            continue
        host = (urlsplit(signal.get("url", "")).hostname or "").lower().removeprefix("www.")
        if not any(host == allowed or host.endswith(f".{allowed}") for allowed in TRUSTED_MEDIA_HOSTS):
            continue
        try:
            published = parsedate_to_datetime(signal.get("published", "")).astimezone(
                dt.timezone.utc
            )
        except (TypeError, ValueError, OverflowError):
            continue
        age = now.astimezone(dt.timezone.utc) - published
        if age < dt.timedelta(minutes=-15) or age > dt.timedelta(hours=4):
            continue
        if _is_near_recent_topic(signal.get("title", ""), state):
            continue
        eligible.append((published, signal))
    eligible.sort(key=lambda row: row[0], reverse=True)
    return [signal for _, signal in eligible]


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def fetch_and_verify_source(candidate: dict) -> str:
    url = normalize_url(candidate["source_url"])
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError("一次資料URLがHTTPSではありません")
    host = (parts.hostname or "").lower().removeprefix("www.")
    if _host_is_secondary(host):
        raise ValueError("報道・まとめサイトは最終一次資料にできません")
    response = requests.get(
        url,
        timeout=SOURCE_VERIFY_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").lower()
    if "html" not in content_type:
        raise ValueError("自動切り抜き可能な公式HTMLではありません")
    soup = BeautifulSoup(response.text, "lxml")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    visible_text = " ".join(soup.get_text(" ", strip=True).split())
    anchor = " ".join(candidate["evidence_anchor"].split()).strip()
    compact_page = _compact_text(visible_text)
    compact_anchor = _compact_text(anchor)
    if len(compact_anchor) < 4 or compact_anchor not in compact_page:
        prefix = compact_anchor[:24]
        if len(prefix) < 8 or prefix not in compact_page:
            raise ValueError("根拠原文が一次資料ページ内に確認できません")
    return url


def validate_candidate(
    candidate: dict,
    sources: list[dict[str, str]],
    state: dict,
    now: dt.datetime,
) -> None:
    if not candidate.get("has_candidate"):
        raise LookupError(candidate.get("skip_reason") or "適切な一次情報がありません")
    topic_type = candidate.get("topic_type")
    if topic_type == "reported_breaking_news":
        raise ValueError("第三者メディアURLの記事カードは自動投稿しません")
    if topic_type not in AUTO_TOPIC_TYPES:
        raise ValueError("自動投稿の対象外系統です")
    policy = get_content_policy(topic_type)
    if policy.review_mode == "manual":
        raise ValueError("手動確認専用の系統です")
    if candidate.get("visual_route") != policy.visual_route:
        raise ValueError("投稿系統と画像形式が一致しません")
    if policy.requires_primary_source and not candidate.get("is_primary_source"):
        raise ValueError("一次資料として選定されていません")
    selected = normalize_url(candidate.get("source_url", ""))
    cited = {normalize_url(row.get("url", "")) for row in sources if row.get("url")}
    if selected not in cited:
        raise ValueError("選定URLがWeb検索の参照元一覧にありません")
    selected_titles = [
        str(row.get("title", ""))
        for row in sources
        if normalize_url(row.get("url", "")) == selected
    ]
    roundup_evidence = [
        str(candidate.get("hook", "")),
        str(candidate.get("evidence_anchor", "")),
        *selected_titles,
    ]
    if any(is_low_value_single_source_roundup(value) for value in roundup_evidence):
        raise ValueError("単一ソースの総括記事は自動投稿できません")

    _validate_reader_interest(candidate)
    _validate_follow_value(candidate)
    validate_auto_post_quality(candidate)

    used_urls = {
        normalize_url(row.get("source_url", ""))
        for row in list(state.get("history", [])) + list(state.get("reservations", []))
        if row.get("source_url")
    }
    if selected in used_urls:
        raise ValueError("同じ一次資料は投稿済みまたは予約済みです")

    published = _parse_timestamp(candidate.get("published_at", ""))
    age = now.astimezone(dt.timezone.utc) - published
    if age < dt.timedelta(minutes=-15):
        raise ValueError("公開日時が未来です")
    if age > dt.timedelta(hours=MAX_AGE_HOURS[topic_type]):
        raise ValueError("この系統の鮮度上限を超えています")

    recent_topics = [row.get("topic_type") for row in _recent_history(state)[-2:]]
    if (
        len(recent_topics) == 2
        and all(value == topic_type for value in recent_topics)
    ):
        raise ValueError("同じ投稿系統が3件連続します")

    text = compose_candidate_text(candidate)
    validate_post(text)


def _validate_reader_interest(candidate: dict) -> None:
    """公開前に、読者へ渡す具体的な価値が本文へ落ちているか確認する。"""
    reader_interest = " ".join(str(candidate.get("reader_interest", "")).split())
    if len(reader_interest) < 18:
        raise ValueError("読者が今見る具体的な理由が不足しています")

    hook_and_facts = " ".join(
        [
            str(candidate.get("hook", "")),
            *[str(fact) for fact in candidate.get("facts", [])],
            str(candidate.get("evidence_anchor", "")),
        ]
    )
    if ROUTINE_SCHEDULE_PATTERN.search(hook_and_facts):
        raise ValueError("予定・IRカレンダーだけでは自動投稿できません")
    if candidate.get("topic_type") == "earnings" and not EARNINGS_RESULT_PATTERN.search(
        hook_and_facts
    ):
        raise ValueError("決算実績・業績見通しの具体的根拠がないため自動投稿できません")
    if GENERIC_SOURCE_FILLER_PATTERN.search(hook_and_facts):
        raise ValueError("公式ページの説明だけでは自動投稿できません")

    # 「公表」をニュースにするなら、何が変わるかを金額・条件・決定のどれかで
    # 本文に明示する。予定の紹介だけでは読者に渡す情報量が足りない。
    if GENERIC_PUBLICATION_PATTERN.search(hook_and_facts) and not (
        MATERIAL_CHANGE_PATTERN.search(hook_and_facts)
        or MATERIAL_NUMBER_PATTERN.search(hook_and_facts)
    ):
        raise ValueError("公表予定だけで具体的な変化がないため自動投稿できません")


def _validate_follow_value(candidate: dict) -> None:
    """単発の閲覧価値と、継続フォローする価値を混同しない。"""
    follow_value = " ".join(str(candidate.get("follow_value", "")).split())
    reader_interest = " ".join(str(candidate.get("reader_interest", "")).split())
    if len(follow_value) < 18:
        raise ValueError("継続フォローする具体的な価値が不足しています")
    compact_follow = _compact_text(follow_value)
    compact_interest = _compact_text(reader_interest)
    if GENERIC_SOURCE_FILLER_PATTERN.search(follow_value):
        raise ValueError("継続フォロー価値が公式発表の紹介だけになっています")
    if GENERIC_PUBLICATION_PATTERN.search(follow_value) and not (
        MATERIAL_CHANGE_PATTERN.search(follow_value)
        or MATERIAL_NUMBER_PATTERN.search(follow_value)
    ):
        raise ValueError("継続フォロー価値が公式発表の紹介だけになっています")
    if compact_follow == compact_interest or compact_follow in compact_interest or compact_interest in compact_follow:
        raise ValueError("継続フォロー価値が閲覧理由の言い換えになっています")


def compose_candidate_text(candidate: dict) -> str:
    tags = [str(tag).lstrip("#＃") for tag in candidate["tags"] if str(tag).strip()]
    text = compose_post(
        hook=candidate["hook"],
        facts=candidate["facts"],
        opinion=candidate["opinion"],
        # 共通タグ処理が #仮想通貨 を補うため、固有タグは1件に限定する。
        tags=tags[:1],
    )
    if weighted_length(text) <= MAX_WEIGHTED_LENGTH:
        return text

    # まず補足事実と固有タグだけを外し、文章自体は自然な一文のまま残す。
    compact = compose_post(
        hook=candidate["hook"],
        facts=[candidate["facts"][0]],
        opinion=candidate["opinion"],
        tags=[],
    )
    if weighted_length(compact) <= MAX_WEIGHTED_LENGTH:
        return compact

    def clip(value: str, limit: int) -> str:
        clean = " ".join(value.split()).strip()
        return clean if len(clean) <= limit else clean[: max(1, limit - 1)].rstrip("、。 ") + "…"

    # APIを再呼び出しせず、事実と僕の見解を残して確実に収める。
    compact = compose_post(
        hook=clip(candidate["hook"], 26),
        facts=[clip(candidate["facts"][0], 34)],
        opinion=clip(candidate["opinion"], 32),
        tags=[],
    )
    if weighted_length(compact) > MAX_WEIGHTED_LENGTH:
        raise ValueError("投稿文を安全に280文字以内へ短縮できません")
    return compact


def _candidate_id(candidate: dict) -> str:
    digest = hashlib.sha256(
        f"{normalize_url(candidate['source_url'])}|{candidate['published_at']}".encode()
    ).hexdigest()[:14]
    return f"inu_auto_{digest}"


def _repo_relative(path: Path) -> str:
    return str(path.resolve().relative_to(REPO_ROOT.resolve()))


def _emit_output(name: str, value: str) -> None:
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with Path(output_file).open("a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def _reserve(
    state: dict,
    item: dict,
    candidate: dict,
    slot: str,
    now: dt.datetime,
    *,
    priority: str = "scheduled",
) -> dict:
    updated = dict(state)
    reservations = [
        row for row in state.get("reservations", []) if row.get("slot") != slot
    ]
    reservations.append(
        {
            "slot": slot,
            "post_id": item["id"],
            "source_url": normalize_url(candidate["source_url"]),
            "topic_type": candidate["topic_type"],
            "priority": priority,
            "generated_editorial_visual": bool(candidate.get("generated_editorial_visual")),
            "reserved_at": now.isoformat(),
        }
    )
    updated["reservations"] = reservations[-72:]
    updated.setdefault("posted_slots", list(state.get("posted_slots", [])))
    updated.setdefault("posted_ids", list(state.get("posted_ids", [])))
    updated.setdefault("history", list(state.get("history", [])))
    return updated


def _generated_editorial_visual_count(state: dict, now: dt.datetime) -> int:
    """JST当日に予約・投稿済みとなった生成主画像だけを数える。"""
    today = now.astimezone(JST).date().isoformat()
    rows = list(state.get("reservations", [])) + list(state.get("posted_slots", []))
    return sum(
        1
        for row in rows
        if row.get("generated_editorial_visual")
        and str(row.get("slot", "")).startswith(f"{today}-")
    )


def _scheduled_run_kind() -> str:
    """GitHub の定刻実行だけに、主実行／予備実行の識別子を与える。"""
    return os.environ.get("INU_SCHEDULE_RUN_KIND", "").strip().lower()


def _scheduled_slot_key(now: dt.datetime, kind: str) -> str:
    """毎時1枠を主実行と予備実行で共有する。"""
    hour = now.astimezone(JST).strftime("%Y-%m-%d-%H")
    if kind in {"primary", "fallback"}:
        return f"{hour}-a"
    return slot_key(now)


def _has_completed_scheduled_check(state: dict, slot: str) -> bool:
    return any(row.get("slot") == slot for row in state.get("scheduled_checks", []))


def _record_scheduled_check(state: dict, slot: str, now: dt.datetime, kind: str) -> dict:
    """候補なしでも、主実行済みであることを予備実行へ引き継ぐ。"""
    updated = dict(state)
    checks = list(state.get("scheduled_checks", []))
    checks.append(
        {
            "slot": slot,
            "checked_at": now.isoformat(),
            "kind": kind,
            "result": "no_verified_candidate",
        }
    )
    updated["scheduled_checks"] = checks[-MAX_SCHEDULED_CHECKS:]
    return updated


def _build_item_from_candidate(
    candidate: dict,
    sources: list[dict[str, str]],
    state: dict,
    now: dt.datetime,
    slot: str,
) -> tuple[dict, dict]:
    """候補を検証し、根拠画像まで取得できた場合だけ投稿データを返す。"""
    selected = dict(candidate)
    # Web検索の引用一覧は発見経路を固定するために使う。ただし、検索モデルが
    # 一次資料の正確なURLを返しても、同じレスポンスの引用一覧に載らないことが
    # ある。この場合だけ先に実ページを取得し、HTTPS・非メディア・根拠原文まで
    # 確認できたものを「直接検証済み」の根拠として追加する。URL文字列だけを
    # 信じて通すのではなく、従来の一次資料検証を先行させる。
    selected_url = normalize_url(selected.get("source_url", ""))
    cited_urls = {normalize_url(row.get("url", "")) for row in sources if row.get("url")}
    verified_url: str | None = None
    if selected_url and selected_url not in cited_urls:
        verified_url = fetch_and_verify_source(selected)
        sources.append(
            {
                "url": verified_url,
                "title": f"{selected.get('source_name', '一次資料')}（直接検証済み）",
            }
        )
        logger.info("引用一覧外の一次資料を実ページ検証で確認: %s", verified_url)
    validate_candidate(selected, sources, state, now)
    # 事実・出典・鮮度を確定してからGrokに編集だけを依頼する。Grok案も同じ
    # 品質ゲートへ戻すため、もっともらしい創作やURL差し替えは公開へ進まない。
    selected = _select_grok_editorial_copy(selected, sources, state, now)
    if verified_url is None:
        verified_url = fetch_and_verify_source(selected)
    selected["source_url"] = verified_url
    # リンクカード経路でも、次段のprepared.jsonを必ず保存できるようにする。
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

    evidence_path = ARTIFACT_DIR / f"{slot}-evidence.png"
    spec = SourceCaptureSpec(
        source_url=verified_url,
        source_name=selected["source_name"],
        published_at=_parse_timestamp(selected["published_at"]).date().isoformat(),
        evidence_type=selected["visual_route"],
        selector="[data-inu-auto-evidence]",
        is_primary_source=bool(selected["is_primary_source"]),
    )
    asyncio.run(
        capture_official_evidence(
            spec,
            evidence_path,
            evidence_anchor=selected["evidence_anchor"],
        )
    )
    if selected.get("evidence_as_primary"):
        item = {
            "id": _candidate_id(selected),
            "topic_type": selected["topic_type"],
            "visual_route": selected["visual_route"],
            "text": compose_candidate_text(selected),
            "media_path": _repo_relative(evidence_path),
            "source_manifest": _repo_relative(evidence_path.with_suffix(".source.json")),
        }
        validate_test_item(item)
        return item, selected
    # 表・数値・チャートが一次根拠となる投稿は、その根拠だけを1枚添付する。
    # 補助的な生成画像を足すと、何を見ればよいかがぼやけ、根拠の信用性も落ちる。
    if selected["visual_route"] == "official_data_crop":
        item = {
            "id": _candidate_id(selected),
            "topic_type": selected["topic_type"],
            "visual_route": selected["visual_route"],
            "text": compose_candidate_text(selected),
            "media_path": _repo_relative(evidence_path),
            "source_manifest": _repo_relative(evidence_path.with_suffix(".source.json")),
        }
        validate_test_item(item)
        return item, selected

    primary_path = ARTIFACT_DIR / f"{slot}-main.png"
    generated_primary = False
    try:
        capture_source_hero_image(
            source_url=verified_url,
            source_name=selected["source_name"],
            published_at=_parse_timestamp(selected["published_at"]).date().isoformat(),
            output_path=primary_path,
            is_primary_source=bool(selected["is_primary_source"]),
        )
    except Exception as source_image_error:
        if _generated_editorial_visual_count(state, now) >= MAX_GENERATED_EDITORIAL_VISUALS_PER_DAY:
            raise ValueError("主画像がなく、生成画像の日次上限に達しています") from source_image_error
        logger.info("出典の主画像を取得できないため生成ビジュアルへ切替: %s", source_image_error)
        generate_editorial_news_visual(
            hook=selected["hook"],
            facts=selected["facts"],
            topic_type=selected["topic_type"],
            source_url=verified_url,
            source_name=selected["source_name"],
            published_at=_parse_timestamp(selected["published_at"]).date().isoformat(),
            output_path=primary_path,
            is_primary_source=bool(selected["is_primary_source"]),
        )
        generated_primary = True
    item = {
        "id": _candidate_id(selected),
        "topic_type": selected["topic_type"],
        "visual_route": selected["visual_route"],
        "text": compose_candidate_text(selected),
        "media_path": _repo_relative(primary_path),
        "source_manifest": _repo_relative(primary_path.with_suffix(".source.json")),
    }
    selected["generated_editorial_visual"] = generated_primary
    validate_test_item(item)
    return item, selected


def _market_fallback_text(metrics: dict, product: str, compared_count: int) -> str:
    """候補再探索まで不発だった時間だけ使う、実測値だけの市場投稿。"""
    from x_price_chart_post import SUPPORTED_PRODUCTS

    asset = SUPPORTED_PRODUCTS[product]
    symbol = asset["symbol"]
    decimals = asset["decimals"]
    change = float(metrics["change_24h"])
    high = float(metrics["period_high"])
    low = float(metrics["period_low"])
    price = float(metrics["last_close"])
    direction = "上昇" if change >= 0 else "下落"
    emoji = "📈" if change >= 0 else "📉"
    range_position = float(metrics["position"]) * 100
    text = (
        f"{emoji} {symbol}、主要{compared_count}銘柄で直近24時間の値動き最大\n\n"
        f"Coinbaseの確定済み1時間足で、{symbol}は{price:,.{decimals}f}ドル。24時間では{change:+.2f}％の{direction}です。\n"
        f"過去3日の高値は{high:,.{decimals}f}ドル、安値は{low:,.{decimals}f}ドル。現在値は同レンジの{range_position:.0f}％地点です。\n\n"
        "#仮想通貨"
    )
    if "僕" in text or "私" in text:
        raise ValueError("価格チャート投稿に個人の意見は含めません")
    return text


def _hour_has_post_or_reservation(state: dict, now: dt.datetime) -> bool:
    """同じJST時間に既に一本成立していれば、非常用チャートを重ねない。"""
    hour = now.astimezone(JST).strftime("%Y-%m-%d-%H")
    rows = list(state.get("posted_slots", [])) + list(state.get("reservations", []))
    return any(str(row.get("slot", "")).startswith(hour) for row in rows)


def _market_product_from_row(row: dict) -> str | None:
    """保存済みの市場投稿からCoinbase銘柄を復元する。"""
    post_id = str(row.get("post_id", "")).lower()
    source_url = str(row.get("source_url", "")).upper()
    for product in MARKET_FALLBACK_PRODUCTS:
        if product.lower() in post_id:
            return product
        tradingview_product = product.replace("-", "")
        if f"COINBASE-{tradingview_product}" in source_url:
            return product
    return None


def _recent_market_fallback_products(state: dict, now: dt.datetime) -> set[str]:
    """クールダウン中の市場投稿銘柄だけを返す。"""
    cutoff = now - MARKET_FALLBACK_PRODUCT_COOLDOWN
    products: set[str] = set()
    rows = list(state.get("posted_slots", [])) + list(state.get("reservations", []))
    for row in rows:
        if not isinstance(row, dict):
            continue
        product = _market_product_from_row(row)
        if not product:
            continue
        timestamp = next(
            (
                row.get(key)
                for key in ("posted_at", "reserved_at", "published_at")
                if row.get(key)
            ),
            None,
        )
        if not timestamp:
            continue
        try:
            if _parse_timestamp(str(timestamp)) >= cutoff:
                products.add(product)
        except (TypeError, ValueError):
            continue
    return products


def build_market_data_fallback(
    now: dt.datetime,
    state: dict,
    slot: str,
) -> tuple[dict, dict]:
    """二度の一次情報探索が不発時だけ、実サービス画面と確定足から毎時枠を守る。"""
    from x_price_chart_post import (
        SUPPORTED_PRODUCTS,
        calculate_metrics,
        fetch_closed_candles,
        render_chart,
    )

    snapshots: list[tuple[str, list[dict], dict]] = []
    failures: list[str] = []
    for product in MARKET_FALLBACK_PRODUCTS:
        try:
            candles = fetch_closed_candles(now=now, product=product)
            snapshots.append((product, candles, calculate_metrics(candles)))
        except Exception as exc:
            failures.append(f"{product}: {exc}")
    if not snapshots:
        raise RuntimeError(
            "一次情報の再探索と市場データの取得がすべて失敗しました: "
            + " / ".join(failures[:3])
        )

    recent_products = _recent_market_fallback_products(state, now)
    eligible_snapshots = [row for row in snapshots if row[0] not in recent_products]
    if not eligible_snapshots:
        raise RuntimeError("同一銘柄の市場投稿クールダウン中のため、価格チャートを重ねません")

    product, candles, metrics = max(
        eligible_snapshots,
        key=lambda row: (abs(float(row[2]["change_24h"])), abs(float(row[2]["position"]) - 0.5)),
    )
    asset = SUPPORTED_PRODUCTS[product]
    chart_path = ARTIFACT_DIR / f"{slot}-market.png"
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    render_chart(candles, chart_path, product=product)

    tradingview_symbol = str(asset["tv"]).replace(":", "-")
    source_url = f"https://www.tradingview.com/symbols/{tradingview_symbol}/"
    manifest_path = chart_path.with_suffix(".source.json")
    manifest_path.write_text(
        json.dumps(
            {
                "evidence_type": "market_service_screenshot",
                "source_url": source_url,
                "data_source": f"https://api.exchange.coinbase.com/products/{product}/candles",
                "data_verified": True,
                "capture_type": "service_screenshot",
                "screenshot_provider": "TradingView",
                "attribution_visible": True,
                "white_background": True,
                "is_primary_source": False,
                "captured_at": now.isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    text = _market_fallback_text(metrics, product, len(snapshots))
    candidate = {
        "topic_type": "crypto_market",
        "hook": text.splitlines()[0],
        "why_now": "主要銘柄を同時比較した直近24時間の確定値で、最も大きい値動きが出ているためです。",
        "reader_interest": f"{asset['symbol']}の値動きが主要銘柄の中で最大となり、短期の資金の偏りを確認できるためです。",
        "follow_value": f"{asset['symbol']}の出来高と3日レンジの更新を継続して追います。",
        "source_url": source_url,
        "published_at": metrics["closed_at"].isoformat(),
        "generated_editorial_visual": False,
    }
    item = {
        "id": f"inu_market_{slot.replace('-', '_')}_{product.lower()}",
        "topic_type": "crypto_market",
        "visual_route": "market_service_screenshot",
        "text": text,
        "media_path": _repo_relative(chart_path),
        "source_manifest": _repo_relative(manifest_path),
    }
    validate_test_item(item)
    logger.info("一次情報の候補が成立しないため、実測市場投稿を準備: %s", product)
    return item, candidate


def prepare(args: argparse.Namespace) -> int:
    now = dt.datetime.now(dt.timezone.utc)
    scheduled_kind = _scheduled_run_kind()
    slot = args.slot or _scheduled_slot_key(now, scheduled_kind)
    state_path = Path(args.state)
    state = load_state(state_path)
    occupied = list(state.get("posted_slots", [])) + list(state.get("reservations", []))
    if any(row.get("slot") == slot for row in occupied):
        logger.info("この時間は投稿済みまたは予約済みです: %s", slot)
        _emit_output("ready", "false")
        return 0

    # 37分の予備実行は「主実行が候補なしだった時間」の再探索枠でもある。
    # 投稿済み・予約済みは上の occupied 判定で止まるため、候補なしという
    # 実行記録だけを理由に投稿機会を捨てない。
    if scheduled_kind == "fallback" and _has_completed_scheduled_check(state, slot):
        logger.info("主実行は候補未確定のため、予備枠で一次資料を再探索: %s", slot)

    priority_url = str(getattr(args, "priority_url", "") or "").strip()
    priority = "breaking" if priority_url else "scheduled"
    candidates: list[dict] = []
    sources: list[dict[str, str]] = []
    signals: list[dict[str, str]] = []
    item: dict | None = None
    candidate: dict | None = None
    failure_reasons: list[str] = []
    unreachable_hosts: set[str] = set()
    if priority_url:
        try:
            candidate, sources = research_priority_signal(now, state, priority_url)
            signals = sources
            candidates = [candidate]
        except (LookupError, ValueError, requests.RequestException) as exc:
            logger.info("検知済み速報を採用できないため見送り: %s", exc)
            _emit_output("ready", "false")
            return 0
    else:
        # Xで伸びている新着の動画・画像は、ニュース探索の失敗時だけではなく
        # 定期的に優先する。速報URLがある場合は上の分岐で一次資料を最優先する。
        if _prefer_overseas_kol_turn(state):
            try:
                overseas_quote = _build_overseas_kol_quote_item(now, state)
                if overseas_quote:
                    item, candidate = overseas_quote
                    logger.info("海外KOLのネイティブ引用候補を優先採用: %s", candidate["source_tweet_id"])
            except Exception as exc:
                failure_reasons.append(f"海外KOL引用探索失敗: {exc}"[:260])
                logger.info("海外KOLのネイティブ引用候補を見送り: %s", exc)
        if item is None:
            try:
                candidates, sources, signals = research_candidates_with_grok(now, state)
            except Exception as exc:
                logger.warning("一次資料の複数候補リサーチに失敗: %s", exc)
                signals = collect_discovery_signals()
                sources = [
                    {"url": row["url"], "title": row["title"]}
                    for row in signals
                    if row.get("url")
                ]

    def try_candidates(
        options: list[dict],
        option_sources: list[dict[str, str]],
        *,
        phase: str,
    ) -> None:
        nonlocal item, candidate
        for position, option in enumerate(options, start=1):
            host = (urlsplit(str(option.get("source_url", ""))).hostname or "").lower()
            if host and host in unreachable_hosts:
                reason = "同一時間枠でこの一次サイトへの接続失敗を確認済みです"
                failure_reasons.append(f"{phase}{position}: {reason}"[:260])
                logger.warning("%sの投稿候補%dを除外: %s (%s)", phase, position, reason, host)
                continue
            try:
                item, candidate = _build_item_from_candidate(
                    option, option_sources, state, now, slot
                )
                logger.info("%sの%d件目を採用", phase, position)
                return
            except Exception as exc:
                reason = str(exc)
                failure_reasons.append(f"{phase}{position}: {reason}"[:260])
                logger.warning("%sの投稿候補%dを除外: %s", phase, position, reason)
                if host and isinstance(exc, requests.RequestException):
                    unreachable_hosts.add(host)
                # 事実・URL・鮮度は維持したまま、文章の判定だけで落ちた候補を
                # そのまま捨てない。一度だけ文章欄を修復して同じ検証を通す。
                if not _is_editorial_repairable_error(exc):
                    continue
                try:
                    repaired = repair_candidate_editorial_copy(option, reason)
                    item, candidate = _build_item_from_candidate(
                        repaired, option_sources, state, now, slot
                    )
                    logger.info("%sの%d件目を編集修復して採用", phase, position)
                    return
                except Exception as repair_exc:
                    repair_reason = str(repair_exc)
                    failure_reasons.append(f"{phase}{position}編集修復: {repair_reason}"[:260])
                    logger.warning(
                        "%sの投稿候補%dは編集修復後も除外: %s",
                        phase,
                        position,
                        repair_reason,
                    )

    if item is None:
        try_candidates(candidates, sources, phase="一次探索")

    # 最初の候補群で止まらず、失敗理由を渡して探索入口を変える。特に、Xで見つけた
    # 話題から一次URLに辿れない、または公式ページの表現が弱い時間をここで救う。
    if item is None and not priority_url:
        try:
            rescue_candidates, rescue_sources = research_rescue_candidates(
                now, state, failure_reasons, signals
            )
            try_candidates(rescue_candidates, rescue_sources, phase="再探索")
        except Exception as exc:
            failure_reasons.append(f"再探索失敗: {exc}"[:260])
            logger.warning("一次情報の再探索に失敗: %s", exc)

    # 一次資料の候補がこの時点で成立しない場合、海外KOLリストで実測済みの
    # 新着動画・画像をネイティブ引用として検討する。元投稿のメディアと投稿者を
    # そのまま表示し、データのない生成画像や転載画像には置き換えない。
    if item is None and not priority_url:
        try:
            overseas_quote = _build_overseas_kol_quote_item(now, state)
            if overseas_quote:
                item, candidate = overseas_quote
                logger.info("海外KOLのネイティブ引用候補を採用: %s", candidate["source_tweet_id"])
        except Exception as exc:
            failure_reasons.append(f"海外KOL引用探索失敗: {exc}"[:260])
            logger.info("海外KOLのネイティブ引用候補を見送り: %s", exc)

    if item is None or candidate is None:
        # 二段階の一次情報探索まで不発でも、毎時枠を空けない。Coinbaseの確定済み
        # データとTradingViewの実画面で、値動き最大の銘柄を一件だけ伝える。
        # ただし同じJST時間に既に一本成立している場合にはチャートを重ねない。
        if not _hour_has_post_or_reservation(state, now):
            try:
                item, candidate = build_market_data_fallback(now, state, slot)
            except Exception as exc:
                # 出典の捏造や文字だけの穴埋めはせず、workflowを失敗させて次の
                # 予備実行で再試行できるようにする。
                logger.error(
                    "毎時投稿の全リカバリー経路が失敗: %s / 理由: %s",
                    exc,
                    " | ".join(failure_reasons[-6:]),
                )
                raise RuntimeError("毎時投稿のリカバリーに失敗しました") from exc
        else:
            logger.info("このJST時間はすでに投稿済みのため、追加の低品質候補は公開しません")
            if scheduled_kind in {"primary", "fallback"} and not args.dry_run:
                save_state(state_path, _record_scheduled_check(state, slot, now, scheduled_kind))
            _emit_output("ready", "false")
            return 0

    prepared = {
        "slot": slot,
        "prepared_at": now.isoformat(),
        "item": item,
        "candidate": candidate,
        "why_now": candidate["why_now"],
    }
    PREPARED_PATH.write_text(
        json.dumps(prepared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not args.dry_run:
        save_state(
            state_path,
            _reserve(state, item, candidate, slot, now, priority=priority),
        )
    logger.info("毎時投稿を準備: %s / %s", item["id"], candidate["topic_type"])
    logger.info("投稿本文:\n%s", item["text"])
    _emit_output("ready", "true")
    _emit_output("post_id", item["id"])
    _emit_output("topic_type", candidate["topic_type"])
    return 0


def publish(args: argparse.Namespace) -> int:
    if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        raise RuntimeError("GitHub Actionsの再実行は重複投稿防止のため禁止しています")
    prepared = json.loads(Path(args.prepared).read_text(encoding="utf-8"))
    item = prepared["item"]
    candidate = prepared["candidate"]
    slot = prepared["slot"]
    state_path = Path(args.state)
    state = load_state(state_path)
    reservation = next(
        (
            row
            for row in state.get("reservations", [])
            if row.get("slot") == slot and row.get("post_id") == item["id"]
        ),
        None,
    )
    if reservation is None:
        raise RuntimeError("永続化済みの投稿予約がないため公開しません")
    if any(row.get("slot") == slot for row in state.get("posted_slots", [])):
        logger.info("この時間はすでに公開済みです: %s", slot)
        return 0

    delivery_mode = str(item.get("delivery_mode", ""))
    if delivery_mode == "x_native_video_reference":
        tweet_id = post_video_reference_tweet(item["text"], item["source_tweet_id"])
    elif delivery_mode == "x_native_quote":
        tweet_id = post_quote_tweet(item["text"], item["source_tweet_id"])
    else:
        tweet_id = publish_test_item(item)
    if not tweet_id:
        raise RuntimeError("INU投稿を公開できませんでした。文字だけの代替投稿は行いません")
    posted_row = {
        "slot": slot,
        "post_id": item["id"],
        "tweet_id": str(tweet_id),
        "topic_type": candidate["topic_type"],
        "priority": str(reservation.get("priority", "scheduled")),
        "generated_editorial_visual": bool(reservation.get("generated_editorial_visual")),
        "source_url": normalize_url(candidate["source_url"]),
        "published_at": candidate["published_at"],
        "posted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "hook": candidate["hook"],
        "follow_value": candidate["follow_value"],
    }
    if delivery_mode:
        posted_row.update(
            {
                "delivery_mode": delivery_mode,
                "source_tweet_id": str(item["source_tweet_id"]),
                "source_handle": str(candidate.get("source_handle", "")),
            }
        )
    state["reservations"] = [
        row for row in state.get("reservations", []) if row.get("post_id") != item["id"]
    ]
    state.setdefault("posted_slots", []).append(posted_row)
    state.setdefault("posted_ids", []).append(item["id"])
    state.setdefault("history", []).append(posted_row)
    state["posted_slots"] = state["posted_slots"][-MAX_HISTORY:]
    state["posted_ids"] = state["posted_ids"][-MAX_HISTORY:]
    state["history"] = state["history"][-MAX_HISTORY:]
    save_state(state_path, state)
    tweet_url = f"https://x.com/hellobtc_jp/status/{tweet_id}"
    logger.info("INU毎時投稿完了: %s", tweet_url)
    _emit_output("tweet_url", tweet_url)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="INU一次情報の毎時自動投稿")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--publish", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--slot")
    parser.add_argument("--priority-url", default="")
    parser.add_argument("--state", default=str(STATE_PATH))
    parser.add_argument("--prepared", default=str(PREPARED_PATH))
    return parser


def run(args: argparse.Namespace) -> int:
    return prepare(args) if args.prepare else publish(args)


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
