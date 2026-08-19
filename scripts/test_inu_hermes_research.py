from __future__ import annotations

import datetime as dt
import unittest

from inu_hermes_research import validate_packet


class INUHermesResearchTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 8, 19, 4, 0, tzinfo=dt.timezone.utc)

    def test_cited_recent_x_signal_is_accepted(self):
        packet = validate_packet(
            {
                "degraded": False,
                "credential_source": "xai-oauth",
                "signals": [
                    {
                        "post_url": "https://x.com/example/status/123456",
                        "handle": "@example",
                        "posted_at": "2026-08-19T03:30:00+00:00",
                        "headline": "ETFフロー更新",
                        "summary": "新しい数値",
                        "why_trending": "反応増加",
                        "topic": "etf",
                        "citations": ["https://x.com/example/status/123456"],
                    }
                ],
            },
            now=self.now,
        )
        self.assertEqual("ready", packet["status"])
        self.assertEqual("example", packet["signals"][0]["handle"])

    def test_uncited_or_stale_signal_is_rejected(self):
        packet = validate_packet(
            {
                "degraded": False,
                "signals": [
                    {
                        "post_url": "https://x.com/example/status/123456",
                        "posted_at": "2026-08-17T03:30:00+00:00",
                        "citations": [],
                    }
                ],
            },
            now=self.now,
        )
        self.assertEqual("no_cited_signal", packet["status"])
        self.assertEqual([], packet["signals"])

    def test_degraded_search_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "degraded"):
            validate_packet({"degraded": True, "signals": []}, now=self.now)


if __name__ == "__main__":
    unittest.main()
