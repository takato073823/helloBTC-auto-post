"""TradingView公式ウィジェットを白背景の縦長PNGとして撮影する。"""

from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path

from PIL import Image, ImageStat


WIDTH = 1080
HEIGHT = 1350
WIDGET_HEIGHT = 1310
SCRIPT_URL = "https://s3.tradingview.com/external-embedding/embed-widget-symbol-overview.js"
ALLOWED_RANGES = {
    "1d|1",
    "1m|30",
    "3m|60",
    "12m|1D",
    "60m|1W",
    "all|1M",
}
SYMBOL_PATTERN = re.compile(r"^[A-Z0-9_]+:[A-Z0-9.!_-]+$")
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9 .&/()_-]+$")
ERROR_MARKERS = (
    "only available on tradingview",
    "このシンボルはtradingview上でのみ",
    "symbol is invalid",
    "no data",
    "データなし",
    "シンボルが無効",
)


def build_widget_html(
    *,
    tradingview_symbol: str,
    label: str,
    date_range: str,
) -> str:
    if not SYMBOL_PATTERN.fullmatch(tradingview_symbol):
        raise ValueError(f"TradingView銘柄コードが不正です: {tradingview_symbol}")
    if date_range not in ALLOWED_RANGES:
        raise ValueError(f"TradingView表示期間が不正です: {date_range}")
    if (
        not label.strip()
        or len(label) > 60
        or not LABEL_PATTERN.fullmatch(label.strip())
    ):
        raise ValueError("TradingView表示名が不正です")

    interval = date_range.split("|", 1)[1]
    config = {
        "symbols": [[label.strip(), f"{tradingview_symbol}|{interval}"]],
        "chartOnly": False,
        "width": WIDTH,
        "height": WIDGET_HEIGHT,
        "locale": "en",
        "colorTheme": "light",
        "autosize": False,
        "showVolume": False,
        "showMA": False,
        "hideDateRanges": False,
        "hideMarketStatus": False,
        "hideSymbolLogo": False,
        "scalePosition": "right",
        "scaleMode": "Normal",
        "fontFamily": "-apple-system, BlinkMacSystemFont, Arial, sans-serif",
        "fontSize": "14",
        "headerFontSize": "large",
        "noTimeScale": False,
        "valuesTracking": "1",
        "changeMode": "price-and-percent",
        "chartType": "candlesticks",
        "dateRanges": [date_range],
        "backgroundColor": "#ffffff",
        "widgetFontColor": "#111111",
        "gridLineColor": "rgba(15, 23, 42, 0.08)",
        "upColor": "#2f8f7b",
        "downColor": "#dc4c55",
        "borderUpColor": "#2f8f7b",
        "borderDownColor": "#dc4c55",
        "wickUpColor": "#2f8f7b",
        "wickDownColor": "#dc4c55",
    }
    config_json = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
    symbol_path = tradingview_symbol.replace(":", "-")
    return f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <style>
    * {{ box-sizing: border-box; }}
    html, body {{ width: {WIDTH}px; height: {HEIGHT}px; margin: 0; overflow: hidden; background: #fff; }}
    .tradingview-widget-container {{ width: {WIDTH}px; height: {HEIGHT}px; background: #fff; }}
    .tradingview-widget-container__widget {{ width: {WIDTH}px; height: {WIDGET_HEIGHT}px; }}
    .tradingview-widget-copyright {{ height: {HEIGHT - WIDGET_HEIGHT}px; padding: 8px 20px 0; text-align: right; color: #667085; font: 16px/24px -apple-system, BlinkMacSystemFont, Arial, sans-serif; background: #fff; }}
    .tradingview-widget-copyright a {{ color: #2962ff; text-decoration: none; }}
  </style>
</head>
<body>
  <div class="tradingview-widget-container">
    <div class="tradingview-widget-container__widget"></div>
    <div class="tradingview-widget-copyright">
      <a href="https://www.tradingview.com/symbols/{symbol_path}/" rel="noopener nofollow">{label.strip()}</a> by TradingView
    </div>
    <script type="text/javascript" src="{SCRIPT_URL}" async>{config_json}</script>
  </div>
</body>
</html>"""


def _visible_numbers(text: str) -> list[float]:
    normalized = text.replace("\u202f", "").replace("\u00a0", "").replace(",", "")
    numbers = []
    for raw in re.findall(r"(?<![A-Za-z])\d+(?:\.\d+)?", normalized):
        try:
            value = float(raw)
        except ValueError:
            continue
        if math.isfinite(value) and value > 0:
            numbers.append(value)
    return numbers


def visible_price_matches(text: str, expected_price: float, *, tolerance: float) -> bool:
    if not math.isfinite(expected_price) or expected_price <= 0:
        raise ValueError("照合価格が不正です")
    if not 0 < tolerance <= 0.05:
        raise ValueError("価格照合の許容差が不正です")
    return any(abs(value / expected_price - 1) <= tolerance for value in _visible_numbers(text))


def validate_tradingview_screenshot(path: str | Path) -> Path:
    image_path = Path(path)
    if not image_path.is_file():
        raise ValueError("TradingViewスクリーンショットがありません")
    with Image.open(image_path) as opened:
        opened.verify()
    with Image.open(image_path) as opened:
        if opened.format != "PNG" or opened.size != (WIDTH, HEIGHT):
            raise ValueError("TradingViewスクリーンショットの形式またはサイズが不正です")
        image = opened.convert("RGB")
        statistics = ImageStat.Stat(image.resize((90, 112)))
        if max(statistics.var) < 120:
            raise ValueError("TradingViewスクリーンショットが空白に近いため使用しません")
        # 白背景であることを、四隅のうち3点以上で確認する。
        corners = (
            image.getpixel((0, 0)),
            image.getpixel((WIDTH - 1, 0)),
            image.getpixel((0, HEIGHT - 1)),
            image.getpixel((WIDTH - 1, HEIGHT - 1)),
        )
        if sum(all(channel >= 230 for channel in pixel) for pixel in corners) < 3:
            raise ValueError("TradingViewスクリーンショットが白背景ではありません")
    return image_path


def capture_tradingview_screenshot(
    *,
    tradingview_symbol: str,
    label: str,
    date_range: str,
    expected_price: float,
    tolerance: float,
    output_path: str | Path,
    timeout_ms: int = 45_000,
) -> tuple[Path, str]:
    """公式ウィジェットの表示値を照合し、画面全体をPNG撮影する。"""
    from playwright.sync_api import sync_playwright

    html = build_widget_html(
        tradingview_symbol=tradingview_symbol,
        label=label,
        date_range=date_range,
    )
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    visible_text = ""

    with sync_playwright() as playwright:
        system_chrome = (
            shutil.which("google-chrome")
            or shutil.which("google-chrome-stable")
            or shutil.which("chromium")
        )
        launch_options = {"headless": True}
        if system_chrome:
            launch_options["executable_path"] = system_chrome
        browser = playwright.chromium.launch(**launch_options)
        page = browser.new_page(
            viewport={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=1,
            color_scheme="light",
        )
        page.route(
            "http://inu-chart.local/**",
            lambda route: route.fulfill(status=200, content_type="text/html", body=html),
        )
        try:
            page.goto("http://inu-chart.local/", wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_selector("iframe", state="attached", timeout=timeout_ms)
            handle = page.query_selector("iframe")
            frame = handle.content_frame() if handle else None
            if frame is None:
                raise ValueError("TradingViewウィジェットを読み込めません")
            frame.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            frame.locator("body").wait_for(state="visible", timeout=timeout_ms)
            page.wait_for_timeout(4_000)
            visible_text = frame.locator("body").inner_text(timeout=timeout_ms)
            lowered = visible_text.lower()
            if any(marker in lowered for marker in ERROR_MARKERS):
                raise ValueError("TradingViewで対象銘柄を表示できません")
            attribution = page.locator(".tradingview-widget-copyright")
            if not attribution.is_visible() or "TradingView" not in attribution.inner_text():
                raise ValueError("TradingViewの出典表記が見えません")
            page.screenshot(path=str(destination), type="png", full_page=False)
            if not visible_price_matches(visible_text, expected_price, tolerance=tolerance):
                candidates = _visible_numbers(visible_text)[:20]
                raise ValueError(
                    "検知データとTradingView表示価格が一致しません: "
                    f"expected={expected_price:g}, visible={candidates}"
                )
        finally:
            browser.close()

    validate_tradingview_screenshot(destination)
    return destination, visible_text
