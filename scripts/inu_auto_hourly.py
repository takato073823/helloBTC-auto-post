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
from inu_hourly_dispatcher import JST, load_state, save_state, slot_key
from inu_live_post import publish_test_item, validate_test_item
from inu_news_visual import capture_source_hero_image, generate_editorial_news_visual
from inu_persona import VOICE_PROMPT
from inu_post import MAX_WEIGHTED_LENGTH, compose_post, validate_post, weighted_length
from inu_source_capture import SourceCaptureSpec, capture_official_evidence
from grok_client import generate_x_json
from llm_client import generate_json, generate_web_json
from scraper import fetch_from_rss


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

AUTO_TOPIC_TYPES = (
    "breaking_news",
    "reported_breaking_news",
    "developing_story",
    "market_microstructure",
    "etf_flow",
    "institutional_flow",
    "onchain",
    "whale_treasury",
    "earnings",
    "supply_event",
    "adoption_kpi",
    "policy_household",
    "macro_event",
)
MAX_AGE_HOURS = {
    "breaking_news": 2,
    "reported_breaking_news": 4,
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
    "macro_event": 12,
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
    "cointelegraph.com",
    "decrypt.co",
    "nikkei.com",
    "reuters.com",
    "theblock.co",
}
TRACKING_KEYS = {"gclid", "fbclid", "ref", "source"}
USER_AGENT = "Mozilla/5.0 (compatible; INUPrimarySourceVerifier/1.0)"
LOW_VALUE_ROUNDUP_PATTERNS = (
    r"\bwhat happened\b.*\b(today|this week)\b",
    r"\b(daily|weekly|market|crypto)\s+(roundup|wrap(?:-up)?|recap|briefing)\b",
    r"\b(top|biggest)\s+\d+\s+(crypto\s+)?(news|stories|events)\b",
    r"(?:本日|今日|今週).*(?:まとめ|総括)",
    r"(?:ニュース|市場).*(?:まとめ|総括)",
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
REPORT_COPY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "hook": {"type": "string"},
        "facts": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 2,
        },
        "opinion": {"type": "string"},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 2,
        },
    },
    "required": ["hook", "facts", "opinion", "tags"],
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
    """再確認枠ではGrokを再課金せず、毎時の主実行と手動実行だけで使う。"""
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
    return event.get("schedule") != "47 * * * *"


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
- 一次資料へ到達できない速報だけは、Reuters、Nikkei、Bloomberg、CoinDesk、Cointelegraph、Decrypt、The Blockの元記事をsource_urlにしてよい。その場合topic_typeはreported_breaking_news、visual_routeはreported_text_crop、is_primary_source=falseにする。
- source_urlは今回のWeb検索結果に実際に含まれるURLを使う。ただしreported_breaking_newsだけは、下記「大手メディアの最新見出し」に含まれる元記事URLも使える。
- 公開日時が確認でき、原則12時間以内。速報は2時間以内、続報は6時間以内。
- evidence_anchorは、一次資料なら一次資料ページ、reported_breaking_newsなら元記事ページにそのまま表示される4文字以上の原文を抜き出す。日本語訳しない。主要メディアでは記事タイトルを優先する。
- evidence_as_primaryは、根拠スクリーンショット自体が一目で意味の分かる公式資料・表・チャート・図版の場合だけtrueにする。単なる記事見出しや本文、余白の多いページならfalseにする。trueなら画像はその根拠スクリーンショット1枚だけで投稿する。
- 噂、匿名情報、価格予想、売買推奨、広告、キャンペーン、基礎知識、数日前の話題の言い換えは除外。
- 「What happened today」「今日のまとめ」「市場総括」「daily roundup」など、複数ニュースを束ねただけの単一記事は除外。総括投稿には独立した3件以上の出典と専用図解が必要なため、この自動経路では選ばない。
- まず一次資料を優先する。公式発表、ETF・オンチェーン、企業IR、規制・金融政策、AI、価格・市場構造の順に横断し、同じ分野だけで候補を埋めない。
- 候補配列にはhas_candidate=trueの項目だけを入れる。適切な候補がない場合だけ空配列にしてskip_reasonを書く。古い話題で穴埋めしない。
- 投稿文は日本語。hookは短く具体的な1行。factsは重要な数字・変更点を1〜2文に絞る。
- 事実の要約を繰り返さず、opinionでは「何が変わるか」または「次に何を見るか」を具体的に一つ書く。
- opinionは「僕の見方では」「僕としては」「個人的には」を自然に使い分ける。「僕は、〜と見ています」「〜がポイントです」「節目だと見ています」の定型的な結びは禁止。
- 絵文字は、客観的に大きな速報・相場急変だけhookの先頭に1個まで。本文中では使わない。
- 投稿全体は日本語の全角換算を考慮して180〜220以内を目標にし、非常に簡潔にする。
- 出典名とハッシュタグを含めても、本文にURLは書かない。

口調の基準:
{VOICE_PROMPT}

直近の投稿系統: {json.dumps(recent_topics, ensure_ascii=False)}
再利用禁止の出典URL: {json.dumps(recent_urls, ensure_ascii=False)}
重複・近似テーマ禁止の直近見出し: {json.dumps(recent_headlines, ensure_ascii=False)}
大手メディアの最新見出し（一次資料探索に使い、一次資料がない場合はreported_breaking_newsの最終出典として使用可）:
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
        if normalized["topic_type"] == "reported_breaking_news":
            normalized["is_primary_source"] = False
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
    """Xの最新シグナルを追加し、失敗時は従来のWeb調査だけで継続する。"""
    x_signals: list[dict[str, str]] = []
    try:
        x_signals = collect_grok_discovery_signals(now, state)
    except Exception as exc:
        logger.warning("GrokのX検索に失敗したため、従来のWeb調査で継続: %s", exc)
    return research_candidates(now, state, extra_signals=x_signals)


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
    """軽量監視で検知済みの主要メディア記事だけを速報候補にする。"""
    signals = collect_discovery_signals()
    selected = normalize_url(priority_url)
    matches = [row for row in signals if normalize_url(row.get("url", "")) == selected]
    if not matches:
        raise LookupError("検知した速報URLが最新RSSに存在しません")
    candidate = build_trusted_media_candidate(now, state, matches)
    return candidate, matches


def _is_near_recent_topic(title: str, state: dict) -> bool:
    recent = " ".join(str(row.get("hook", "")) for row in _recent_history(state)).lower()
    normalized = re.sub(r"[^a-z0-9一-鿿ぁ-んァ-ン]+", " ", title.lower())
    common = {"bitcoin", "crypto", "market", "latest", "today", "reports", "says"}
    tokens = {token for token in normalized.split() if len(token) >= 5 and token not in common}
    return any(token in recent for token in tokens)


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


def build_trusted_media_candidate(
    now: dt.datetime,
    state: dict,
    signals: list[dict[str, str]],
) -> dict:
    eligible = trusted_media_signals(now, state, signals)
    if not eligible:
        raise LookupError("4時間以内で重複しない主要メディア速報がありません")
    signal = eligible[0]
    published = parsedate_to_datetime(signal.get("published", "")).astimezone(
        dt.timezone.utc
    )
    title = signal["title"].strip()
    summary = " ".join(signal.get("summary", "").split()).strip()

    copy = generate_json(
        f"""
次の信頼できるニュースメディアの見出しとRSS要約だけを根拠に、INUのX投稿文を作成してください。
外部知識や数字を追加しないでください。日本語で簡潔にし、全体は全角100〜150文字程度を目標にします。
hookは具体的な速報見出しを1行。factsは重要な事実を1〜2文。opinionでは「何が変わるか」または「次に何を見るか」を一つだけ具体的に書きます。
opinionは「僕の見方では」「僕としては」「個人的には」を自然に使い、「僕は、〜と見ています」「〜がポイントです」で終えません。売買推奨や価格予想をしません。
絵文字は原則使わず、客観的に大きな速報ならhookの先頭に1個だけ使えます。
元記事: {title}
RSS要約: {summary}
媒体: {signal['source']}

口調の基準:
{VOICE_PROMPT}
""".strip(),
        schema_name="inu_reported_breaking_copy",
        schema=REPORT_COPY_SCHEMA,
        max_output_tokens=1200,
        model=os.environ.get("INU_COPY_MODEL", "gpt-5.6-luna"),
    )
    return {
        "has_candidate": True,
        "skip_reason": "",
        "topic_type": "reported_breaking_news",
        "hook": copy["hook"],
        "facts": copy["facts"],
        "opinion": copy["opinion"],
        "source_name": signal["source"],
        "source_url": signal["url"],
        "published_at": published.isoformat(),
        "evidence_anchor": title,
        "visual_route": "reported_text_crop",
        "tags": copy["tags"],
        "why_now": "4時間以内に配信された主要メディアの速報",
        "is_primary_source": False,
    }


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def fetch_and_verify_source(candidate: dict) -> str:
    url = normalize_url(candidate["source_url"])
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError("一次資料URLがHTTPSではありません")
    host = (parts.hostname or "").lower().removeprefix("www.")
    if candidate["topic_type"] == "reported_breaking_news":
        if not any(host == allowed or host.endswith(f".{allowed}") for allowed in TRUSTED_MEDIA_HOSTS):
            raise ValueError("主要メディア速報の許可ドメインではありません")
    elif _host_is_secondary(host):
        raise ValueError("報道・まとめサイトは最終一次資料にできません")
    response = requests.get(url, timeout=25, headers={"User-Agent": USER_AGENT})
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
    if topic_type not in AUTO_TOPIC_TYPES:
        raise ValueError("自動投稿の対象外系統です")
    policy = get_content_policy(topic_type)
    if policy.review_mode == "manual":
        raise ValueError("手動確認専用の系統です")
    if candidate.get("visual_route") != policy.visual_route:
        raise ValueError("投稿系統と画像形式が一致しません")
    if policy.requires_primary_source and not candidate.get("is_primary_source"):
        raise ValueError("一次資料として選定されていません")
    if topic_type == "reported_breaking_news" and candidate.get("is_primary_source"):
        raise ValueError("主要メディア速報の出典区分が不正です")

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
    # reported_breaking_newsは出典区分であり、暗号資産・AI・株式など内容は別物。
    # URL・見出しの重複検査を通過していれば、区分だけを理由に停止しない。
    if (
        topic_type != "reported_breaking_news"
        and len(recent_topics) == 2
        and all(value == topic_type for value in recent_topics)
    ):
        raise ValueError("同じ投稿系統が3件連続します")

    text = compose_candidate_text(candidate)
    validate_post(text)


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


def _build_item_from_candidate(
    candidate: dict,
    sources: list[dict[str, str]],
    state: dict,
    now: dt.datetime,
    slot: str,
) -> tuple[dict, dict]:
    """候補を検証し、根拠画像まで取得できた場合だけ投稿データを返す。"""
    selected = dict(candidate)
    validate_candidate(selected, sources, state, now)
    verified_url = fetch_and_verify_source(selected)
    selected["source_url"] = verified_url

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
        "additional_media": [
            {
                "media_path": _repo_relative(evidence_path),
                "source_manifest": _repo_relative(evidence_path.with_suffix(".source.json")),
            }
        ],
    }
    selected["generated_editorial_visual"] = generated_primary
    validate_test_item(item)
    return item, selected


def prepare(args: argparse.Namespace) -> int:
    now = dt.datetime.now(dt.timezone.utc)
    slot = args.slot or slot_key(now)
    state_path = Path(args.state)
    state = load_state(state_path)
    occupied = list(state.get("posted_slots", [])) + list(state.get("reservations", []))
    if any(row.get("slot") == slot for row in occupied):
        logger.info("この時間は投稿済みまたは予約済みです: %s", slot)
        _emit_output("ready", "false")
        return 0

    priority_url = str(getattr(args, "priority_url", "") or "").strip()
    priority = "breaking" if priority_url else "scheduled"
    candidates: list[dict] = []
    sources: list[dict[str, str]] = []
    signals: list[dict[str, str]] = []
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

    item: dict | None = None
    candidate: dict | None = None
    attempted_urls: set[str] = set()
    for position, option in enumerate(candidates, start=1):
        attempted_urls.add(normalize_url(str(option.get("source_url", ""))))
        try:
            item, candidate = _build_item_from_candidate(option, sources, state, now, slot)
            logger.info("複数候補の%d件目を採用", position)
            break
        except Exception as exc:
            logger.warning("投稿候補%dを除外: %s", position, exc)

    if item is None and not priority_url:
        for signal in trusted_media_signals(now, state, signals):
            signal_url = normalize_url(signal.get("url", ""))
            if signal_url in attempted_urls:
                continue
            attempted_urls.add(signal_url)
            try:
                option = build_trusted_media_candidate(now, state, [signal])
                item, candidate = _build_item_from_candidate(option, sources, state, now, slot)
                logger.info("確認済み主要メディア候補を採用: %s", signal.get("source", ""))
                break
            except Exception as exc:
                logger.warning("主要メディア候補を除外: %s / %s", signal.get("title", ""), exc)

    if item is None or candidate is None:
        logger.info("今時間の投稿を見送り: 検証と画像取得を通過する最新候補がありません")
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

    tweet_id = publish_test_item(item)
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
    }
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
