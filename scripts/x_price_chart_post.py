#!/usr/bin/env python3
"""Coinbaseで価格を検証し、TradingView画面を添えてINUのXへ投稿する。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import os
import re
import tempfile
from pathlib import Path

import requests

from inu_tradingview_capture import capture_tradingview_screenshot
from x_info_poster import BLOCKING_PATTERNS, MAX_WEIGHTED_LENGTH, weighted_length
from x_poster import _neutralize_service_domains, post_info_tweet


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "x_price_chart_state.json"
COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/{product}/candles"
SUPPORTED_PRODUCTS = {
    "BTC-USD": {"symbol": "BTC", "name": "Bitcoin", "decimals": 0, "tv": "COINBASE:BTCUSD", "tv_label": "ビットコイン／米ドル"},
    "ETH-USD": {"symbol": "ETH", "name": "Ethereum", "decimals": 0, "tv": "COINBASE:ETHUSD", "tv_label": "イーサリアム／米ドル"},
    "SOL-USD": {"symbol": "SOL", "name": "Solana", "decimals": 2, "tv": "COINBASE:SOLUSD", "tv_label": "ソラナ／米ドル"},
    "XRP-USD": {"symbol": "XRP", "name": "XRP", "decimals": 4, "tv": "COINBASE:XRPUSD", "tv_label": "XRP／米ドル"},
    "DOGE-USD": {"symbol": "DOGE", "name": "Dogecoin", "decimals": 5, "tv": "COINBASE:DOGEUSD", "tv_label": "ドージコイン／米ドル"},
    "ADA-USD": {"symbol": "ADA", "name": "Cardano", "decimals": 4, "tv": "COINBASE:ADAUSD", "tv_label": "カルダノ／米ドル"},
}
GRANULARITY_SECONDS = 3600
DISPLAY_CANDLES = 72

def parse_closed_candles(rows: list, now: dt.datetime | None = None) -> list[dict]:
    """Coinbaseの配列を検証し、確定済みの時間足だけ昇順で返す。"""
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    current_ts = current.timestamp()

    parsed: dict[int, dict] = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            raise ValueError("Coinbaseのローソク足データ形式が不正です")
        timestamp = int(row[0])
        low, high, open_price, close, volume = map(float, row[1:6])
        values = (low, high, open_price, close, volume)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Coinbaseのローソク足に不正な数値があります")
        if low <= 0 or high < low or not low <= open_price <= high or not low <= close <= high:
            raise ValueError("CoinbaseのOHLC整合性が取れません")
        if timestamp + GRANULARITY_SECONDS > current_ts:
            continue
        parsed[timestamp] = {
            "time": dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc),
            "low": low,
            "high": high,
            "open": open_price,
            "close": close,
            "volume": volume,
        }

    candles = [parsed[key] for key in sorted(parsed)]
    if len(candles) < 48:
        raise ValueError(f"確定済みローソク足が不足しています: {len(candles)}本")
    return candles[-DISPLAY_CANDLES:]


def fetch_closed_candles(
    now: dt.datetime | None = None,
    *,
    product: str = "BTC-USD",
) -> list[dict]:
    if product not in SUPPORTED_PRODUCTS:
        raise ValueError(f"未対応のCoinbase商品です: {product}")
    current = now or dt.datetime.now(dt.timezone.utc)
    start = current - dt.timedelta(hours=DISPLAY_CANDLES + 8)
    response = requests.get(
        COINBASE_CANDLES_URL.format(product=product),
        params={
            "granularity": GRANULARITY_SECONDS,
            "start": start.isoformat(),
            "end": current.isoformat(),
        },
        headers={"User-Agent": "helloBTC-INU-price-chart/1.0"},
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Coinbase APIの応答が配列ではありません")
    return parse_closed_candles(payload, now=current)


def calculate_metrics(candles: list[dict]) -> dict:
    if len(candles) < 25:
        raise ValueError("24時間比較に必要なローソク足が不足しています")
    last_close = candles[-1]["close"]
    close_24h = candles[-25]["close"]
    first_close = candles[0]["close"]
    period_high = max(candle["high"] for candle in candles)
    period_low = min(candle["low"] for candle in candles)
    span = period_high - period_low
    position = 0.5 if span == 0 else (last_close - period_low) / span
    return {
        "last_close": last_close,
        "change_24h": (last_close / close_24h - 1) * 100,
        "change_period": (last_close / first_close - 1) * 100,
        "period_high": period_high,
        "period_low": period_low,
        "position": max(0.0, min(1.0, position)),
        "closed_at": candles[-1]["time"] + dt.timedelta(seconds=GRANULARITY_SECONDS),
    }


def _signed_percent(value: float) -> str:
    return f"{value:+.2f}%"


def _price(value: float, decimals: int) -> str:
    return f"${value:,.{decimals}f}"


def build_tweet(metrics: dict, *, product: str = "BTC-USD") -> str:
    asset = SUPPORTED_PRODUCTS[product]
    symbol = asset["symbol"]
    decimals = asset["decimals"]
    change = metrics["change_24h"]
    position = metrics["position"]
    if abs(change) >= 2:
        headline = f"【{symbol}、24時間で{_signed_percent(change)}】"
    elif position >= 0.75:
        headline = f"【{symbol}、3日レンジ上限圏】"
    elif position <= 0.25:
        headline = f"【{symbol}、3日レンジ下限圏】"
    else:
        headline = f"【{symbol}、3日レンジの中間圏】"

    if position >= 0.75:
        focus = "高値圏を維持できるか"
    elif position <= 0.25:
        focus = "安値圏から戻せるか"
    else:
        focus = "どちら側へ値幅が広がるか"

    text = (
        f"{headline}\n\n"
        f"直近確定値は{_price(metrics['last_close'], decimals)}。24時間で{_signed_percent(change)}。\n"
        f"過去3日の高値{_price(metrics['period_high'], decimals)}、安値{_price(metrics['period_low'], decimals)}。\n\n"
        f"僕は、{focus}に注目しています。\n\n"
        f"※価格データ: Coinbase {product}／画像: TradingView"
    )
    return _neutralize_service_domains(text)


def validate_tweet(text: str) -> None:
    if re.search(r"https?://|www\.", text, flags=re.IGNORECASE):
        raise ValueError("価格投稿の本文にはURLを含めません")
    blocked = [pattern for pattern in BLOCKING_PATTERNS if pattern in text]
    if blocked:
        raise ValueError(f"禁止表現があります: {blocked}")
    length = weighted_length(text)
    if length > MAX_WEIGHTED_LENGTH:
        raise ValueError(f"Xの文字数上限を超えます: {length}/{MAX_WEIGHTED_LENGTH}")


def render_chart(
    candles: list[dict],
    output_path: Path,
    *,
    product: str = "BTC-USD",
) -> Path:
    """自作描画は行わず、公式TradingViewウィジェットの実画面を撮影する。"""
    asset = SUPPORTED_PRODUCTS[product]
    metrics = calculate_metrics(candles)
    capture_tradingview_screenshot(
        tradingview_symbol=asset["tv"],
        label=asset["tv_label"],
        date_range="1m|30",
        expected_price=metrics["last_close"],
        tolerance=0.02,
        output_path=output_path,
    )
    return output_path


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "posts": []}
    with path.open(encoding="utf-8") as handle:
        state = json.load(handle)
    state.setdefault("version", 1)
    state.setdefault("posts", [])
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


def run(args: argparse.Namespace) -> int:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", args.key):
        raise ValueError("二重投稿防止キーの形式が不正です")
    state_path = Path(args.state)
    state = load_state(state_path)
    if any(row.get("key") == args.key for row in state["posts"]):
        logger.info("この価格チャート投稿は実行済みです: %s", args.key)
        return 0

    product = getattr(args, "product", "BTC-USD")
    candles = fetch_closed_candles(product=product)
    metrics = calculate_metrics(candles)
    tweet = build_tweet(metrics, product=product)
    validate_tweet(tweet)

    if args.live and os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        raise ValueError("GitHub Actionsの再実行は重複投稿防止のため禁止しています")

    if args.output:
        output_path = Path(args.output)
        render_chart(candles, output_path, product=product)
        logger.info("投稿プレビュー\n%s", tweet)
        logger.info("チャートを保存: %s", output_path)
        if not args.live:
            return 0
        tweet_id = post_info_tweet(tweet, output_path)
    else:
        with tempfile.TemporaryDirectory(prefix="inu-price-chart-") as temp_dir:
            output_path = render_chart(
                candles,
                Path(temp_dir) / f"{product.lower()}.png",
                product=product,
            )
            logger.info("投稿プレビュー\n%s", tweet)
            if not args.live:
                return 0
            tweet_id = post_info_tweet(tweet, output_path)

    if not tweet_id:
        logger.error("価格チャートを投稿できませんでした。履歴は更新しません")
        return 1

    state["posts"].append(
        {
            "key": args.key,
            "tweet_id": str(tweet_id),
            "posted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "closed_candle_at": metrics["closed_at"].isoformat(),
            "product": product,
        }
    )
    save_state(state_path, state)
    logger.info("投稿完了: https://x.com/i/web/status/%s", tweet_id)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="INUのBTC価格チャート投稿")
    parser.add_argument("--live", action="store_true", help="Xへ実際に投稿する")
    parser.add_argument("--key", default="btc_price_chart_test_v1", help="二重投稿防止キー")
    parser.add_argument("--state", default=str(STATE_PATH))
    parser.add_argument("--output", help="生成するPNGの保存先")
    parser.add_argument("--product", choices=sorted(SUPPORTED_PRODUCTS), default="BTC-USD")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return run(build_parser().parse_args(argv))
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        logger.error("価格チャート投稿を中止: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
