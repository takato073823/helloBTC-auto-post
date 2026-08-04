#!/usr/bin/env python3
"""主要市場の歴史的な値動きを検知し、実サービス画面付きで速報する。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

from inu_hourly_dispatcher import load_state, save_state
from inu_live_post import publish_test_item, validate_test_item
from inu_tradingview_capture import capture_tradingview_screenshot


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
STATE_PATH = SCRIPT_DIR / "inu_hourly_state.json"
ARTIFACT_DIR = SCRIPT_DIR / "artifacts" / "inu-breaking"
PREPARED_PATH = ARTIFACT_DIR / "prepared.json"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = "Mozilla/5.0 (compatible; helloBTC-INU-breaking-monitor/1.0)"
JST = ZoneInfo("Asia/Tokyo")
MAX_QUOTE_AGE = dt.timedelta(minutes=20)
MIN_RECORD_BREAK = 0.00005
MAX_HISTORY = 1000


@dataclass(frozen=True)
class MarketSpec:
    symbol: str
    label: str
    family: str
    timezone: str
    priority: int
    tradingview_symbol: str
    tradingview_label: str


MARKETS = (
    MarketSpec(
        "^GSPC", "S&P 500", "us_indices", "America/New_York", 100,
        "FOREXCOM:SPXUSD", "S&P 500",
    ),
    MarketSpec(
        "^NDX", "NASDAQ 100", "us_indices", "America/New_York", 90,
        "FOREXCOM:NSXUSD", "NASDAQ 100",
    ),
    MarketSpec(
        "^DJI", "NYダウ", "us_indices", "America/New_York", 80,
        "FOREXCOM:DJI", "Dow Jones",
    ),
    MarketSpec(
        "^N225", "日経平均", "jp_indices", "Asia/Tokyo", 100,
        "FOREXCOM:JPXJPY", "Nikkei 225",
    ),
)


def _get_json(symbol: str, *, interval: str, range_value: str) -> dict:
    response = requests.get(
        YAHOO_CHART_URL.format(symbol=quote(symbol, safe="")),
        params={"interval": interval, "range": range_value},
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    result = payload.get("chart", {}).get("result")
    if not isinstance(result, list) or not result:
        raise ValueError(f"{symbol}の市場データを取得できません")
    return result[0]


def _finite(value: object, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label}が不正です")
    return number


def detect_record(spec: MarketSpec, now: dt.datetime | None = None) -> dict | None:
    """当日の日中高値が、それ以前の1年高値を明確に上回った場合だけ返す。"""
    current = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    daily = _get_json(spec.symbol, interval="1d", range_value="1y")
    meta = daily.get("meta", {})
    quote_rows = daily.get("indicators", {}).get("quote", [])
    timestamps = daily.get("timestamp", [])
    if not quote_rows or len(timestamps) < 150:
        raise ValueError(f"{spec.label}の過去データが不足しています")
    highs = quote_rows[0].get("high", [])
    closes = quote_rows[0].get("close", [])
    if not (len(timestamps) == len(highs) == len(closes)):
        raise ValueError(f"{spec.label}の日足配列が不正です")

    market_tz = ZoneInfo(spec.timezone)
    quote_time = dt.datetime.fromtimestamp(int(meta["regularMarketTime"]), dt.timezone.utc)
    if current - quote_time > MAX_QUOTE_AGE or quote_time - current > dt.timedelta(minutes=2):
        logger.info("%sの現在値が古いため判定しません: %s", spec.label, quote_time.isoformat())
        return None
    market_date = quote_time.astimezone(market_tz).date()

    rows: list[tuple[dt.date, float, float]] = []
    for timestamp, high, close in zip(timestamps, highs, closes):
        if high is None or close is None:
            continue
        row_date = dt.datetime.fromtimestamp(int(timestamp), dt.timezone.utc).astimezone(market_tz).date()
        rows.append((row_date, _finite(high, "高値"), _finite(close, "終値")))
    today_rows = [row for row in rows if row[0] == market_date]
    prior_rows = [row for row in rows if row[0] < market_date]
    if not today_rows or len(prior_rows) < 140:
        return None

    current_high = max(row[1] for row in today_rows)
    prior_date, prior_high, _ = max(prior_rows, key=lambda row: row[1])
    if current_high <= prior_high * (1 + MIN_RECORD_BREAK):
        return None

    # 1年より前の歴史的高値も月足で確認し、「史上最高値」を52週高値と混同しない。
    lifetime = _get_json(spec.symbol, interval="1mo", range_value="max")
    lifetime_quotes = lifetime.get("indicators", {}).get("quote", [])
    lifetime_timestamps = lifetime.get("timestamp", [])
    if not lifetime_quotes:
        raise ValueError(f"{spec.label}の長期データがありません")
    lifetime_highs = lifetime_quotes[0].get("high", [])
    if len(lifetime_timestamps) != len(lifetime_highs):
        raise ValueError(f"{spec.label}の長期データ配列が不正です")
    current_month = (market_date.year, market_date.month)
    lifetime_rows: list[tuple[dt.date, float]] = []
    for timestamp, high in zip(lifetime_timestamps, lifetime_highs):
        if high is None:
            continue
        row_date = dt.datetime.fromtimestamp(int(timestamp), dt.timezone.utc).astimezone(market_tz).date()
        if (row_date.year, row_date.month) == current_month:
            continue
        lifetime_rows.append((row_date, _finite(high, "長期高値")))
    if not lifetime_rows:
        raise ValueError(f"{spec.label}の比較可能な長期データがありません")
    lifetime_date, lifetime_high = max(lifetime_rows, key=lambda row: row[1])
    if lifetime_high > prior_high:
        prior_date, prior_high = lifetime_date, lifetime_high
    if current_high <= prior_high * (1 + MIN_RECORD_BREAK):
        return None

    previous_close = prior_rows[-1][2]
    current_price = _finite(meta.get("regularMarketPrice"), "現在値")
    if current_high / current_price > 1.05:
        raise ValueError(f"{spec.label}の日中高値が現在値から乖離しすぎています")
    return {
        "kind": "all_time_high",
        "event_key": f"{spec.family}_record_{market_date.isoformat()}",
        "symbol": spec.symbol,
        "label": spec.label,
        "family": spec.family,
        "priority": spec.priority,
        "market_date": market_date.isoformat(),
        "quote_time": quote_time.isoformat(),
        "current_price": current_price,
        "current_high": current_high,
        "previous_close": previous_close,
        "previous_record": prior_high,
        "previous_record_date": prior_date.isoformat(),
        "change_percent": (current_price / previous_close - 1) * 100,
        "tradingview_symbol": spec.tradingview_symbol,
        "tradingview_label": spec.tradingview_label,
    }


def detect_best_event(now: dt.datetime | None = None) -> dict | None:
    events: list[dict] = []
    for spec in MARKETS:
        try:
            event = detect_record(spec, now=now)
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            logger.warning("%sの速報判定を見送り: %s", spec.label, exc)
            continue
        if event:
            events.append(event)
    if not events:
        return None
    return max(events, key=lambda row: row["priority"])


def fetch_intraday_points(event: dict) -> list[tuple[dt.datetime, float]]:
    payload = _get_json(event["symbol"], interval="1m", range_value="1d")
    timestamps = payload.get("timestamp", [])
    quote_rows = payload.get("indicators", {}).get("quote", [])
    if not quote_rows:
        raise ValueError("チャート用データがありません")
    closes = quote_rows[0].get("close", [])
    points = [
        (dt.datetime.fromtimestamp(int(timestamp), dt.timezone.utc), _finite(close, "5分足"))
        for timestamp, close in zip(timestamps, closes)
        if close is not None
    ]
    if len(points) < 3:
        raise ValueError("チャート用1分足が不足しています")
    latest = points[-1][0]
    if dt.datetime.now(dt.timezone.utc) - latest > MAX_QUOTE_AGE:
        raise ValueError("チャート用5分足が古いため投稿しません")
    return points


def _number(value: float) -> str:
    return f"{value:,.2f}"


def build_text(event: dict) -> str:
    direction = "+" if event["change_percent"] >= 0 else ""
    market_label = "日本市場" if event["family"] == "jp_indices" else "米国市場"
    text = (
        f"【速報】{event['label']}が史上最高値を更新\n\n"
        f"{market_label}で一時{_number(event['current_high'])}まで上昇。"
        f"{event['previous_record_date'][5:].replace('-', '/')}の過去最高値"
        f"{_number(event['previous_record'])}を突破。"
        f"前日終値比は{direction}{event['change_percent']:.2f}%。\n\n"
        "市場が未知の価格帯へ入った重要な節目です。\n\n"
        "僕は、上昇が大型株以外にも広がるかを見ています。\n\n"
        "※価格確認: Yahoo Finance／画像: TradingView"
    )
    return text


def render_chart(event: dict, points: list[tuple[dt.datetime, float]], output: Path) -> Path:
    """検知データと照合後、TradingViewの白背景画面を4:5で撮影する。"""
    if not points:
        raise ValueError("市場速報の照合データがありません")
    capture_tradingview_screenshot(
        tradingview_symbol=event["tradingview_symbol"],
        label=event["tradingview_label"],
        date_range="12m|1W",
        expected_price=event["current_price"],
        tolerance=0.02,
        output_path=output,
    )
    return output


def _repo_relative(path: Path) -> str:
    return str(path.resolve().relative_to(REPO_ROOT.resolve()))


def _emit_output(name: str, value: str) -> None:
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with Path(output_file).open("a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def _known_event_keys(state: dict) -> set[str]:
    rows = list(state.get("history", [])) + list(state.get("reservations", []))
    return {str(row.get("event_key")) for row in rows if row.get("event_key")}


def prepare(args: argparse.Namespace) -> int:
    state_path = Path(args.state)
    state = load_state(state_path)
    event = detect_best_event()
    if not event:
        logger.info("速報条件に該当する市場イベントはありません")
        _emit_output("ready", "false")
        return 0
    if event["event_key"] in _known_event_keys(state):
        logger.info("この市場速報は投稿済みまたは予約済みです: %s", event["event_key"])
        _emit_output("ready", "false")
        return 0

    points = fetch_intraday_points(event)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    image_path = ARTIFACT_DIR / f"{event['event_key']}.png"
    render_chart(event, points, image_path)
    manifest_path = image_path.with_suffix(".source.json")
    manifest = {
        "evidence_type": "market_service_screenshot",
        "data_verified": True,
        "capture_type": "service_screenshot",
        "screenshot_provider": "TradingView",
        "attribution_visible": True,
        "white_background": True,
        "source_url": f"https://www.tradingview.com/symbols/{event['tradingview_symbol'].replace(':', '-')}/",
        "detection_source_url": f"https://finance.yahoo.com/quote/{quote(event['symbol'], safe='')}/",
        "data_endpoint": YAHOO_CHART_URL.format(symbol=quote(event["symbol"], safe="")),
        "retrieved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "symbol": event["symbol"],
        "previous_record": event["previous_record"],
        "current_high": event["current_high"],
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    item = {
        "id": f"inu_breaking_{event['event_key']}",
        "topic_type": "historical_milestone",
        "visual_route": "market_service_screenshot",
        "text": build_text(event),
        "media_path": _repo_relative(image_path),
        "source_manifest": _repo_relative(manifest_path),
    }
    validate_test_item(item)
    prepared = {
        "prepared_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "event": event,
        "item": item,
    }
    PREPARED_PATH.write_text(json.dumps(prepared, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not args.dry_run:
        state.setdefault("reservations", []).append(
            {
                "event_key": event["event_key"],
                "post_id": item["id"],
                "topic_type": item["topic_type"],
                "priority": "breaking",
                "reserved_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            }
        )
        state["reservations"] = state["reservations"][-72:]
        save_state(state_path, state)
    logger.info("市場速報を準備: %s\n%s", event["event_key"], item["text"])
    _emit_output("ready", "true")
    _emit_output("event_key", event["event_key"])
    return 0


def publish(args: argparse.Namespace) -> int:
    if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        raise RuntimeError("GitHub Actionsの再実行は重複投稿防止のため禁止しています")
    prepared = json.loads(Path(args.prepared).read_text(encoding="utf-8"))
    event = prepared["event"]
    item = prepared["item"]
    state_path = Path(args.state)
    state = load_state(state_path)
    reservation = next(
        (
            row
            for row in state.get("reservations", [])
            if row.get("event_key") == event["event_key"] and row.get("post_id") == item["id"]
        ),
        None,
    )
    if reservation is None:
        raise RuntimeError("永続化済みの速報予約がないため公開しません")
    if any(row.get("event_key") == event["event_key"] for row in state.get("history", [])):
        logger.info("この市場速報はすでに公開済みです: %s", event["event_key"])
        return 0

    tweet_id = publish_test_item(item)
    posted_at = dt.datetime.now(dt.timezone.utc).isoformat()
    posted_row = {
        "event_key": event["event_key"],
        "post_id": item["id"],
        "tweet_id": str(tweet_id),
        "topic_type": item["topic_type"],
        "priority": "breaking",
        "source_url": f"https://finance.yahoo.com/quote/{quote(event['symbol'], safe='')}/",
        "published_at": event["quote_time"],
        "posted_at": posted_at,
        "hook": f"{event['label']}が史上最高値を更新",
    }
    state["reservations"] = [
        row for row in state.get("reservations", []) if row.get("event_key") != event["event_key"]
    ]
    state.setdefault("posted_ids", []).append(item["id"])
    state.setdefault("history", []).append(posted_row)
    state["posted_ids"] = state["posted_ids"][-MAX_HISTORY:]
    state["history"] = state["history"][-MAX_HISTORY:]
    save_state(state_path, state)
    tweet_url = f"https://x.com/hellobtc_jp/status/{tweet_id}"
    logger.info("INU市場速報投稿完了: %s", tweet_url)
    _emit_output("tweet_url", tweet_url)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="INU市場速報の自動投稿")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--publish", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--state", default=str(STATE_PATH))
    parser.add_argument("--prepared", default=str(PREPARED_PATH))
    return parser


def run(args: argparse.Namespace) -> int:
    return prepare(args) if args.prepare else publish(args)


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
