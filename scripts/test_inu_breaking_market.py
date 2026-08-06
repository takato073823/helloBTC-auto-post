"""市場速報の検知・重複防止・画像要件を検証する。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import inu_breaking_market


NOW = dt.datetime(2026, 8, 4, 14, 20, tzinfo=dt.timezone.utc)
SPEC = inu_breaking_market.MARKETS[0]


def daily_payload(*, current_high: float = 7678.27) -> dict:
    start = dt.date(2025, 12, 1)
    dates = [start + dt.timedelta(days=index) for index in range(247)]
    dates[-2] = dt.date(2026, 8, 3)
    dates[-1] = dt.date(2026, 8, 4)
    timestamps = [
        int(dt.datetime.combine(day, dt.time(14, 30), tzinfo=dt.timezone.utc).timestamp())
        for day in dates
    ]
    highs = [7000.0 + index for index in range(len(dates))]
    closes = [value - 10 for value in highs]
    highs[-80] = 7620.90
    closes[-80] = 7609.78
    highs[-2], closes[-2] = 7610.04, 7600.50
    highs[-1], closes[-1] = current_high, current_high - 9
    return {
        "meta": {
            "regularMarketTime": int(NOW.timestamp()),
            "regularMarketPrice": current_high - 9,
        },
        "timestamp": timestamps,
        "indicators": {"quote": [{"high": highs, "close": closes}]},
    }


class INUBreakingMarketTests(unittest.TestCase):
    def test_new_record_is_detected_against_prior_trading_days(self):
        with patch.object(inu_breaking_market, "_get_json", return_value=daily_payload()):
            event = inu_breaking_market.detect_record(SPEC, now=NOW)
        self.assertIsNotNone(event)
        self.assertEqual("us_indices_record_2026-08-04", event["event_key"])
        self.assertAlmostEqual(7620.90, event["previous_record"], places=2)

    def test_no_record_returns_none(self):
        with patch.object(
            inu_breaking_market,
            "_get_json",
            return_value=daily_payload(current_high=7615.0),
        ):
            self.assertIsNone(inu_breaking_market.detect_record(SPEC, now=NOW))

    def test_stale_quote_is_rejected(self):
        payload = daily_payload()
        payload["meta"]["regularMarketTime"] = int((NOW - dt.timedelta(hours=1)).timestamp())
        with patch.object(inu_breaking_market, "_get_json", return_value=payload):
            self.assertIsNone(inu_breaking_market.detect_record(SPEC, now=NOW))

    def test_text_is_factual_and_has_no_url(self):
        with patch.object(inu_breaking_market, "_get_json", return_value=daily_payload()):
            event = inu_breaking_market.detect_record(SPEC, now=NOW)
        text = inu_breaking_market.build_text(event)
        self.assertNotIn("僕", text)
        self.assertIn("重要な節目", text)
        self.assertNotRegex(text, r"https?://|www\.")

    def test_prepare_skips_known_event(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            state = Path(temp_dir) / "state.json"
            state.write_text(
                json.dumps({"history": [{"event_key": "us_indices_record_2026-08-04"}]}),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                prepare=True,
                publish=False,
                dry_run=True,
                state=str(state),
                prepared=str(Path(temp_dir) / "prepared.json"),
            )
            with patch.object(
                inu_breaking_market,
                "detect_best_event",
                return_value={"event_key": "us_indices_record_2026-08-04"},
            ), patch.object(inu_breaking_market, "fetch_intraday_points") as fetch:
                self.assertEqual(0, inu_breaking_market.run(args))
                fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
