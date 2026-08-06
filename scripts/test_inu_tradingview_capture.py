"""TradingView画面撮影の検証を、ネット接続なしで行う。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from inu_tradingview_capture import (
    build_widget_html,
    select_chart_window,
    validate_tradingview_screenshot,
    visible_price_matches,
)


class INUTradingViewCaptureTests(unittest.TestCase):
    def test_widget_is_white_candlestick_and_keeps_attribution(self):
        html = build_widget_html(
            tradingview_symbol="COINBASE:BTCUSD",
            label="Bitcoin / U.S. Dollar",
            date_range="12m|1W",
        )
        self.assertIn('"colorTheme":"light"', html)
        self.assertIn('"chartType":"candlesticks"', html)
        self.assertIn('"backgroundColor":"#ffffff"', html)
        self.assertIn('"locale":"en"', html)
        self.assertIn('"fontSize":"20"', html)
        self.assertIn('"headerFontSize":"large"', html)
        self.assertIn("1W candles / Past year", html)
        self.assertIn("by TradingView", html)
        self.assertIn("1080px", html)
        self.assertIn("1350px", html)

    def test_non_ascii_label_is_rejected_to_prevent_missing_glyphs(self):
        with self.assertRaises(ValueError):
            build_widget_html(
                tradingview_symbol="COINBASE:BTCUSD",
                label="ビットコイン／米ドル",
                date_range="12m|1W",
            )

    def test_price_must_match_detection_source(self):
        text = "Bitcoin / U.S. Dollar 64,031.25 USD +1.2%"
        self.assertTrue(visible_price_matches(text, 64_000, tolerance=0.01))
        self.assertFalse(visible_price_matches(text, 70_000, tolerance=0.01))

    def test_chart_window_keeps_candle_count_readable(self):
        self.assertEqual("5d|120", select_chart_window(24).date_range)
        self.assertEqual("5d|120", select_chart_window(72).date_range)
        self.assertEqual("1m|1D", select_chart_window(24 * 30).date_range)
        self.assertEqual("12m|1W", select_chart_window(24 * 365).date_range)

    def test_white_vertical_png_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "screen.png"
            image = Image.new("RGB", (1080, 1350), "white")
            draw = ImageDraw.Draw(image)
            draw.line((40, 1100, 1040, 200), fill="#2f8f7b", width=12)
            draw.text((40, 40), "TradingView", fill="black")
            image.save(path)
            self.assertEqual(path, validate_tradingview_screenshot(path))

    def test_dark_or_blank_image_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            blank = Path(directory) / "blank.png"
            dark = Path(directory) / "dark.png"
            Image.new("RGB", (1080, 1350), "white").save(blank)
            Image.new("RGB", (1080, 1350), "#111111").save(dark)
            with self.assertRaises(ValueError):
                validate_tradingview_screenshot(blank)
            with self.assertRaises(ValueError):
                validate_tradingview_screenshot(dark)

    def test_chart_with_candles_only_on_one_side_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "partial.png"
            image = Image.new("RGB", (1080, 1350), "white")
            draw = ImageDraw.Draw(image)
            # 左端だけにローソク足がある、実投稿で問題になった表示を再現する。
            for x in range(30, 220, 24):
                draw.line((x, 800, x, 430), fill="#dc4c55", width=6)
                draw.rectangle((x - 8, 540, x + 8, 700), fill="#dc4c55")
            draw.text((40, 40), "TradingView", fill="black")
            image.save(path)
            with self.assertRaisesRegex(ValueError, "片側"):
                validate_tradingview_screenshot(path)


if __name__ == "__main__":
    unittest.main()
