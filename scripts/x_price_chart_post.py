#!/usr/bin/env python3
"""実データからBTC価格チャートを生成し、INUのXへ画像付き投稿する。"""

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
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import requests
from matplotlib.patches import Rectangle

from x_info_poster import BLOCKING_PATTERNS, MAX_WEIGHTED_LENGTH, weighted_length
from x_poster import _neutralize_service_domains, post_info_tweet


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_PATH = SCRIPT_DIR / "x_price_chart_state.json"
COINBASE_CANDLES_URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles"
JST = ZoneInfo("Asia/Tokyo")
GRANULARITY_SECONDS = 3600
DISPLAY_CANDLES = 72

BLACK = "#111111"
MUTED = "#687078"
GRID = "#E7E9EC"
ORANGE = "#F7931A"
GREEN = "#0A9B72"
RED = "#E5484D"


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


def fetch_closed_candles(now: dt.datetime | None = None) -> list[dict]:
    current = now or dt.datetime.now(dt.timezone.utc)
    start = current - dt.timedelta(hours=DISPLAY_CANDLES + 8)
    response = requests.get(
        COINBASE_CANDLES_URL,
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


def build_tweet(metrics: dict) -> str:
    change = metrics["change_24h"]
    position = metrics["position"]
    if abs(change) >= 2:
        headline = f"【BTC、24時間で{_signed_percent(change)}】"
    elif position >= 0.75:
        headline = "【BTC、3日レンジ上限圏】"
    elif position <= 0.25:
        headline = "【BTC、3日レンジ下限圏】"
    else:
        headline = "【BTC、3日レンジの中間圏】"

    if position >= 0.75:
        focus = "高値圏を維持できるか"
    elif position <= 0.25:
        focus = "安値圏から戻せるか"
    else:
        focus = "どちら側へ値幅が広がるか"

    text = (
        f"{headline}\n\n"
        f"直近確定値は${metrics['last_close']:,.0f}。24時間で{_signed_percent(change)}。\n"
        f"過去3日の高値${metrics['period_high']:,.0f}、安値${metrics['period_low']:,.0f}。\n\n"
        f"僕は、{focus}に注目しています。\n\n"
        "※Coinbase BTC-USD／1時間足"
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


def render_chart(candles: list[dict], output_path: Path) -> Path:
    metrics = calculate_metrics(candles)
    dates = [mdates.date2num(candle["time"].astimezone(JST)) for candle in candles]
    candle_width = (1 / 24) * 0.64

    fig = plt.figure(figsize=(8, 10), dpi=135, facecolor="white")
    # 右側の価格目盛りと現在値ラベルが画像外へ切れない余白を確保する。
    ax = fig.add_axes([0.10, 0.16, 0.76, 0.55])
    ax.set_facecolor("white")

    for x, candle in zip(dates, candles):
        color = GREEN if candle["close"] >= candle["open"] else RED
        ax.vlines(x, candle["low"], candle["high"], color=color, linewidth=1.15, zorder=2)
        bottom = min(candle["open"], candle["close"])
        height = abs(candle["close"] - candle["open"])
        if height == 0:
            ax.hlines(candle["close"], x - candle_width / 2, x + candle_width / 2, color=color, linewidth=1.6)
        else:
            ax.add_patch(
                Rectangle(
                    (x - candle_width / 2, bottom),
                    candle_width,
                    height,
                    facecolor=color,
                    edgecolor=color,
                    linewidth=0.6,
                    zorder=3,
                )
            )

    ax.axhline(metrics["last_close"], color=ORANGE, linewidth=1.15, linestyle=(0, (4, 4)), alpha=0.9)
    ax.annotate(
        f" ${metrics['last_close']:,.0f} ",
        xy=(1.0, metrics["last_close"]),
        xycoords=("axes fraction", "data"),
        ha="left",
        va="center",
        fontsize=10,
        color="white",
        bbox={"boxstyle": "round,pad=0.25", "facecolor": ORANGE, "edgecolor": ORANGE},
        clip_on=False,
    )

    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.grid(axis="x", visible=False)
    ax.spines[["top", "left", "bottom"]].set_visible(False)
    ax.spines["right"].set_color(GRID)
    ax.yaxis.tick_right()
    ax.tick_params(axis="y", colors=MUTED, labelsize=9, length=0, pad=8)
    ax.tick_params(axis="x", colors=MUTED, labelsize=9, length=0, pad=10)
    ax.yaxis.set_major_formatter(lambda value, _position: f"${value:,.0f}")
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=12, tz=JST))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d\n%H:%M", tz=JST))
    ax.set_xlim(dates[0] - candle_width, dates[-1] + candle_width * 4)
    price_padding = (metrics["period_high"] - metrics["period_low"]) * 0.08 or 1
    ax.set_ylim(metrics["period_low"] - price_padding, metrics["period_high"] + price_padding)

    fig.text(0.10, 0.930, "INU MARKET", fontsize=12, fontweight="bold", color=ORANGE)
    fig.text(0.10, 0.875, "Bitcoin / US Dollar", fontsize=24, fontweight="bold", color=BLACK)
    fig.text(0.10, 0.842, "BTCUSD  ·  COINBASE  ·  1H", fontsize=10.5, fontweight="bold", color=MUTED)
    fig.text(0.10, 0.775, f"${metrics['last_close']:,.2f}", fontsize=30, fontweight="bold", color=BLACK)
    change_color = GREEN if metrics["change_24h"] >= 0 else RED
    fig.text(0.10, 0.738, f"24H  {_signed_percent(metrics['change_24h'])}", fontsize=12, fontweight="bold", color=change_color)
    fig.text(0.34, 0.738, f"3D  {_signed_percent(metrics['change_period'])}", fontsize=12, fontweight="bold", color=MUTED)
    fig.add_artist(plt.Line2D([0.10, 0.93], [0.725, 0.725], color=GRID, linewidth=1))

    closed_at = metrics["closed_at"].astimezone(JST)
    fig.text(0.10, 0.090, "72 CLOSED HOURLY CANDLES", fontsize=9, fontweight="bold", color=MUTED)
    fig.text(0.10, 0.060, f"Source: Coinbase Exchange  ·  Data through {closed_at:%Y-%m-%d %H:%M} JST", fontsize=8.5, color=MUTED)
    fig.text(0.90, 0.060, "INU", fontsize=9, fontweight="bold", ha="right", color=BLACK)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, facecolor="white", format="png", dpi=135)
    plt.close(fig)
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

    candles = fetch_closed_candles()
    metrics = calculate_metrics(candles)
    tweet = build_tweet(metrics)
    validate_tweet(tweet)

    if args.output:
        output_path = Path(args.output)
        render_chart(candles, output_path)
        logger.info("投稿プレビュー\n%s", tweet)
        logger.info("チャートを保存: %s", output_path)
        if not args.live:
            return 0
        tweet_id = post_info_tweet(tweet, output_path)
    else:
        with tempfile.TemporaryDirectory(prefix="inu-price-chart-") as temp_dir:
            output_path = render_chart(candles, Path(temp_dir) / "btc-usd.png")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return run(build_parser().parse_args(argv))
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        logger.error("価格チャート投稿を中止: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
