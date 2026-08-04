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
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from inu_content_types import get_content_policy
from inu_hourly_dispatcher import JST, load_state, save_state, slot_key
from inu_live_post import publish_test_item, validate_test_item
from inu_post import compose_post, validate_post
from inu_source_capture import SourceCaptureSpec, capture_official_evidence
from llm_client import generate_web_json
from scraper import fetch_from_rss


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
STATE_PATH = SCRIPT_DIR / "inu_hourly_state.json"
ARTIFACT_DIR = SCRIPT_DIR / "artifacts" / "inu-auto"
PREPARED_PATH = ARTIFACT_DIR / "prepared.json"
MAX_HISTORY = 1000

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
    "reported_breaking_news": 2,
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
        "visual_route",
        "tags",
        "why_now",
        "is_primary_source",
    ],
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


def collect_discovery_signals() -> list[dict[str, str]]:
    """大手暗号資産メディアの最新見出しを、一次資料探索の入口として取得する。"""
    signals: list[dict[str, str]] = []
    try:
        for article in fetch_from_rss(max_per_feed=3):
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
    return signals[:15]


def build_research_prompt(
    now: dt.datetime,
    state: dict,
    discovery_signals: list[dict[str, str]] | None = None,
) -> str:
    recent = _recent_history(state)
    recent_topics = [row.get("topic_type", "") for row in recent[-8:] if row.get("topic_type")]
    recent_urls = [row.get("source_url", "") for row in recent if row.get("source_url")]
    local = now.astimezone(JST)
    return f"""
あなたは投資情報アカウントINUの一次情報リサーチ担当です。現在時刻は
{local.isoformat()}（日本時間）です。必ずWeb検索を実行し、この時刻から見て新しい
暗号資産・ビットコイン・米国株・日本株・AI・金融政策・地政学の重要情報を1件だけ選んでください。

最重要条件:
- 少なくとも「暗号資産公式」「ETF・オンチェーン」「米国企業IR・AI」
  「日本企業IR」「中央銀行・規制当局」「Xで話題になった公式発表」の観点を分けて検索してから比較する。
- ニュースメディアやXの話題は発見に使ってよいが、最終source_urlは発表主体の公式サイト、規制当局、中央銀行、取引所、上場企業IR、ETF発行体、公式データ提供元などの一次資料にする。
- 一次資料へ到達できない速報だけは、Reuters、Nikkei、Bloomberg、CoinDesk、Cointelegraph、Decrypt、The Blockの元記事をsource_urlにしてよい。その場合topic_typeはreported_breaking_news、visual_routeはreported_text_crop、is_primary_source=falseにする。
- source_urlは今回のWeb検索結果に実際に含まれるURLだけを使う。
- 公開日時が確認でき、原則12時間以内。速報は2時間以内、続報は6時間以内。
- evidence_anchorは一次資料ページにそのまま表示される4文字以上の原文を抜き出す。日本語訳しない。
- 噂、匿名情報、価格予想、売買推奨、広告、キャンペーン、基礎知識、数日前の話題の言い換えは除外。
- 適切な候補がなければhas_candidate=falseにする。古い話題で穴埋めしない。
- 投稿文は日本語。hookは短く具体的にし、factsは重要な数字・変更点を1〜2文。
- opinionには必ず「僕は」または「個人的には」を使い、事実と見解を分ける。
- 投稿全体がXの280文字制限に収まるよう非常に簡潔にする。
- 出典名とハッシュタグを含めても、本文にURLは書かない。

直近の投稿系統: {json.dumps(recent_topics, ensure_ascii=False)}
再利用禁止の出典URL: {json.dumps(recent_urls, ensure_ascii=False)}
大手メディアの最新見出し（発見専用。最終出典には使わない）:
{json.dumps(discovery_signals or [], ensure_ascii=False)}
次は直近と異なる系統を優先する。選択可能なtopic_typeは:
{', '.join(AUTO_TOPIC_TYPES)}
visual_routeは数字・表・チャートが根拠ならofficial_data_crop、それ以外はofficial_text_crop。主要メディア速報だけreported_text_crop。
""".strip()


def research_candidate(now: dt.datetime, state: dict) -> tuple[dict, list[dict[str, str]]]:
    signals = collect_discovery_signals()
    candidate, sources = generate_web_json(
        build_research_prompt(now, state, signals),
        schema_name="inu_live_candidate",
        schema=CANDIDATE_SCHEMA,
        max_output_tokens=2200,
        # 複数市場から一次資料まで辿る必要があるため、検索選定はTerraを使う。
        model=os.environ.get("INU_RESEARCH_MODEL", "gpt-5.6-terra"),
    )
    sources.extend(
        {"url": row["url"], "title": row["title"]}
        for row in signals
        if row.get("url")
    )
    return candidate, sources


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
    if len(recent_topics) == 2 and all(value == topic_type for value in recent_topics):
        raise ValueError("同じ投稿系統が3件連続します")

    text = compose_candidate_text(candidate)
    validate_post(text)


def compose_candidate_text(candidate: dict) -> str:
    return compose_post(
        hook=candidate["hook"],
        facts=candidate["facts"],
        opinion=candidate["opinion"],
        source_label=candidate["source_name"],
        # 共通タグ処理が #仮想通貨 を補うため、固有タグは1件に限定する。
        tags=candidate["tags"][:1],
    )


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


def _reserve(state: dict, item: dict, candidate: dict, slot: str, now: dt.datetime) -> dict:
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
            "reserved_at": now.isoformat(),
        }
    )
    updated["reservations"] = reservations[-72:]
    updated.setdefault("posted_slots", list(state.get("posted_slots", [])))
    updated.setdefault("posted_ids", list(state.get("posted_ids", [])))
    updated.setdefault("history", list(state.get("history", [])))
    return updated


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

    candidate, sources = research_candidate(now, state)
    try:
        validate_candidate(candidate, sources, state, now)
        verified_url = fetch_and_verify_source(candidate)
    except LookupError as exc:
        logger.info("今時間の投稿を見送り: %s", exc)
        _emit_output("ready", "false")
        return 0
    candidate["source_url"] = verified_url

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    image_path = ARTIFACT_DIR / f"{slot}.png"
    spec = SourceCaptureSpec(
        source_url=verified_url,
        source_name=candidate["source_name"],
        published_at=_parse_timestamp(candidate["published_at"]).date().isoformat(),
        evidence_type=candidate["visual_route"],
        selector="[data-inu-auto-evidence]",
        is_primary_source=bool(candidate["is_primary_source"]),
    )
    asyncio.run(
        capture_official_evidence(
            spec,
            image_path,
            evidence_anchor=candidate["evidence_anchor"],
        )
    )
    item = {
        "id": _candidate_id(candidate),
        "topic_type": candidate["topic_type"],
        "visual_route": candidate["visual_route"],
        "text": compose_candidate_text(candidate),
        "media_path": _repo_relative(image_path),
        "source_manifest": _repo_relative(image_path.with_suffix(".source.json")),
    }
    validate_test_item(item)
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
        save_state(state_path, _reserve(state, item, candidate, slot, now))
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
        "source_url": normalize_url(candidate["source_url"]),
        "published_at": candidate["published_at"],
        "posted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
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
    parser.add_argument("--state", default=str(STATE_PATH))
    parser.add_argument("--prepared", default=str(PREPARED_PATH))
    return parser


def run(args: argparse.Namespace) -> int:
    return prepare(args) if args.prepare else publish(args)


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
