"""重要ニュース軽量監視の鮮度・重要度・重複防止を検証する。"""

from __future__ import annotations

import datetime as dt
import unittest

import inu_breaking_news_probe


NOW = dt.datetime(2026, 8, 4, 14, 30, tzinfo=dt.timezone.utc)


def signal(title: str, *, minutes_old: int = 5, url: str = "https://coindesk.com/latest") -> dict:
    published = NOW - dt.timedelta(minutes=minutes_old)
    return {
        "title": title,
        "url": url,
        "source": "CoinDesk",
        "published": published.strftime("%a, %d %b %Y %H:%M:%S +0000"),
        "summary": "A sufficiently detailed summary for downstream copy generation and verification.",
    }


class INUBreakingNewsProbeTests(unittest.TestCase):
    def test_high_impact_security_headline_is_selected(self):
        item = signal("Major crypto exchange halts withdrawals after $100 million hack")
        self.assertEqual(item, inu_breaking_news_probe.select_signal([item], {"history": []}, NOW))

    def test_prediction_and_generic_wrapup_are_blocked(self):
        self.assertFalse(inu_breaking_news_probe.is_high_impact_title("Bitcoin price prediction: BTC could reach a new high"))
        self.assertFalse(inu_breaking_news_probe.is_high_impact_title("What happened in crypto today"))
        self.assertFalse(inu_breaking_news_probe.is_high_impact_title("Daily crypto market roundup: ETF, BTC and XRP"))

    def test_old_headline_is_rejected(self):
        item = signal("SEC approves spot crypto ETF", minutes_old=50)
        self.assertIsNone(inu_breaking_news_probe.select_signal([item], {"history": []}, NOW))

    def test_used_url_is_rejected(self):
        item = signal("SEC approves spot crypto ETF")
        state = {"history": [{"source_url": item["url"]}]}
        self.assertIsNone(inu_breaking_news_probe.select_signal([item], state, NOW))

    def test_recent_breaking_post_applies_cooldown(self):
        item = signal("SEC approves spot crypto ETF")
        state = {
            "history": [
                {"priority": "breaking", "posted_at": (NOW - dt.timedelta(minutes=10)).isoformat()}
            ]
        }
        self.assertIsNone(inu_breaking_news_probe.select_signal([item], state, NOW))


if __name__ == "__main__":
    unittest.main()
