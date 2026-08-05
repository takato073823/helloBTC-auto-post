"""INUの日次振り返りが、少ない実績を過大評価しないことを検証する。"""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from inu_growth_insights import build_insights, load_insight_guidance


NOW = dt.datetime(2026, 8, 5, 12, tzinfo=dt.timezone.utc)


class INUGrowthInsightsTests(unittest.TestCase):
    def test_only_reliable_topics_are_used_as_tiebreaker(self):
        state = {
            "history": [
                {"tweet_id": "1", "topic_type": "etf_flow", "posted_at": "2026-08-05T10:00:00Z"},
                {"tweet_id": "2", "topic_type": "etf_flow", "posted_at": "2026-08-05T09:00:00Z"},
                {"tweet_id": "3", "topic_type": "onchain", "posted_at": "2026-08-05T08:00:00Z"},
            ]
        }
        metrics = {
            "1": {"impression_count": 1000, "like_count": 20, "reply_count": 2, "retweet_count": 1, "quote_count": 0},
            "2": {"impression_count": 800, "like_count": 16, "reply_count": 1, "retweet_count": 1, "quote_count": 0},
            "3": {"impression_count": 5000, "like_count": 100, "reply_count": 10, "retweet_count": 4, "quote_count": 1},
        }
        report = build_insights(state, metrics, NOW)
        self.assertEqual(3, report["measured_posts"])
        self.assertEqual(["etf_flow"], report["selection_guidance"]["preferred_topics_when_quality_is_equal"])

    def test_missing_or_old_metrics_are_not_used(self):
        state = {
            "history": [
                {"tweet_id": "old", "topic_type": "etf_flow", "posted_at": "2026-07-30T10:00:00Z"},
                {"tweet_id": "missing", "topic_type": "onchain", "posted_at": "2026-08-05T10:00:00Z"},
            ]
        }
        report = build_insights(state, {}, NOW)
        self.assertEqual(0, report["measured_posts"])
        self.assertEqual([], report["selection_guidance"]["preferred_topics_when_quality_is_equal"])

    def test_historical_or_manual_topic_is_not_used_for_auto_selection(self):
        state = {
            "history": [
                {"tweet_id": "legacy", "topic_type": "reported_breaking_news", "posted_at": "2026-08-05T10:00:00Z"},
                {"tweet_id": "1", "topic_type": "earnings", "posted_at": "2026-08-05T10:00:00Z"},
                {"tweet_id": "2", "topic_type": "earnings", "posted_at": "2026-08-05T09:00:00Z"},
            ]
        }
        metrics = {
            "legacy": {"impression_count": 9000, "like_count": 500, "reply_count": 40, "retweet_count": 20, "quote_count": 10},
            "1": {"impression_count": 100, "like_count": 4, "reply_count": 0, "retweet_count": 0, "quote_count": 0},
            "2": {"impression_count": 120, "like_count": 4, "reply_count": 0, "retweet_count": 0, "quote_count": 0},
        }
        report = build_insights(state, metrics, NOW)
        self.assertEqual(2, report["measured_posts"])
        self.assertEqual(["earnings"], report["selection_guidance"]["preferred_topics_when_quality_is_equal"])

    def test_guidance_loader_tolerates_missing_or_invalid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "insights.json"
            self.assertEqual({}, load_insight_guidance(path))
            path.write_text("not-json", encoding="utf-8")
            self.assertEqual({}, load_insight_guidance(path))


if __name__ == "__main__":
    unittest.main()
