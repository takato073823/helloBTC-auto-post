"""BTC価格チャート投稿を、秘密情報なしで検証する。"""

from __future__ import annotations

import argparse
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import x_price_chart_post


def sample_rows(count: int = 74, now: dt.datetime | None = None) -> list[list[float]]:
    current = now or dt.datetime(2026, 8, 4, 6, 30, tzinfo=dt.timezone.utc)
    base = int(current.replace(minute=0, second=0, microsecond=0).timestamp())
    rows = []
    for index in range(count):
        timestamp = base - index * 3600
        open_price = 100_000 + (count - index) * 30
        close = open_price + (120 if index % 2 == 0 else -80)
        rows.append([timestamp, min(open_price, close) - 50, max(open_price, close) + 50, open_price, close, 10])
    return rows


class XPriceChartPostTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 8, 4, 6, 30, tzinfo=dt.timezone.utc)

    def test_in_progress_candle_is_dropped_and_rows_are_sorted(self):
        candles = x_price_chart_post.parse_closed_candles(sample_rows(now=self.now), now=self.now)
        self.assertEqual(len(candles), 72)
        self.assertLess(candles[-1]["time"].timestamp() + 3600, self.now.timestamp() + 1)
        self.assertEqual(candles, sorted(candles, key=lambda candle: candle["time"]))

    def test_malformed_ohlc_is_rejected(self):
        rows = sample_rows(now=self.now)
        rows[1][1] = rows[1][2] + 1
        with self.assertRaises(ValueError):
            x_price_chart_post.parse_closed_candles(rows, now=self.now)

    def test_tweet_is_factual_url_free_and_within_limit(self):
        candles = x_price_chart_post.parse_closed_candles(sample_rows(now=self.now), now=self.now)
        text = x_price_chart_post.build_tweet(x_price_chart_post.calculate_metrics(candles))
        x_price_chart_post.validate_tweet(text)
        self.assertIn("Coinbase BTC-USD", text)
        self.assertNotIn("http", text)
        self.assertNotIn("買うべき", text)

    def test_chart_is_1080_by_1350(self):
        candles = x_price_chart_post.parse_closed_candles(sample_rows(now=self.now), now=self.now)
        with tempfile.TemporaryDirectory() as tmp:
            path = x_price_chart_post.render_chart(candles, Path(tmp) / "chart.png")
            with Image.open(path) as image:
                self.assertEqual(image.size, (1080, 1350))

    def test_dry_run_never_posts_or_writes_state(self):
        candles = x_price_chart_post.parse_closed_candles(sample_rows(now=self.now), now=self.now)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            output = Path(tmp) / "chart.png"
            args = argparse.Namespace(live=False, key="test", state=str(state), output=str(output))
            with patch.object(x_price_chart_post, "fetch_closed_candles", return_value=candles), patch.object(
                x_price_chart_post, "post_info_tweet"
            ) as post:
                self.assertEqual(x_price_chart_post.run(args), 0)
            post.assert_not_called()
            self.assertFalse(state.exists())
            self.assertTrue(output.exists())

    def test_successful_live_post_records_key_and_prevents_duplicate(self):
        candles = x_price_chart_post.parse_closed_candles(sample_rows(now=self.now), now=self.now)
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            args = argparse.Namespace(live=True, key="once", state=str(state), output=str(Path(tmp) / "chart.png"))
            with patch.object(x_price_chart_post, "fetch_closed_candles", return_value=candles), patch.object(
                x_price_chart_post, "post_info_tweet", return_value="123"
            ) as post:
                self.assertEqual(x_price_chart_post.run(args), 0)
                self.assertEqual(x_price_chart_post.run(args), 0)
            self.assertEqual(post.call_count, 1)
            self.assertIn('"tweet_id": "123"', state.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
