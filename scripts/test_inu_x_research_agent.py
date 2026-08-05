"""公式X API探索エージェントのコスト・鮮度・発見専用ルールを検証する。"""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import inu_x_research_agent as research


NOW = dt.datetime(2026, 8, 6, 3, 0, tzinfo=dt.timezone.utc)


def tweet(**overrides):
    value = {
        "id": "2086000000000000001",
        "author_id": "author-1",
        "text": "Bitcoin ETF inflows reach a new high https://issuer.example/flow",
        "created_at": "2026-08-06T02:45:00Z",
        "public_metrics": {
            "impression_count": 18_000,
            "like_count": 210,
            "reply_count": 20,
            "retweet_count": 40,
            "quote_count": 15,
        },
        "entities": {"urls": [{"expanded_url": "https://issuer.example/flow?utm_source=x"}]},
        "attachments": {"media_keys": ["media-1"]},
    }
    value.update(overrides)
    return value


class ResearchAgentTests(unittest.TestCase):
    def test_direct_x_post_is_ranked_using_actual_metrics_and_video_presence(self):
        response = SimpleNamespace(
            data=[tweet()],
            includes={
                "users": [{"id": "author-1", "username": "issuer", "verified": True, "public_metrics": {"followers_count": 100_000}}],
                "media": [{"media_key": "media-1", "type": "video"}],
            },
        )
        rows = research._normalize_search_response(response, "crypto_market", NOW, set())
        self.assertEqual(1, len(rows))
        self.assertEqual("official_x_api", rows[0]["discovery_type"])
        self.assertTrue(rows[0]["has_video"])
        self.assertIn("表示18,000", rows[0]["why_trending"])
        self.assertEqual(["https://issuer.example/flow"], rows[0]["linked_urls"])

    def test_promotional_and_unrelated_posts_are_not_signals(self):
        response = SimpleNamespace(
            data=[
                tweet(text="Bitcoin ETF giveaway now!"),
                tweet(id="2086000000000000002", text="A nice morning for everyone"),
            ],
            includes={"users": [{"id": "author-1", "username": "issuer", "public_metrics": {}}], "media": []},
        )
        self.assertEqual([], research._normalize_search_response(response, "crypto_market", NOW, set()))

    def test_watchlist_ingest_reuses_existing_read_without_publishing(self):
        post = {
            "post_url": "https://x.com/analyst/status/2086000000000000003",
            "handle": "analyst",
            "posted_at": "2026-08-06T02:50:00Z",
            "text": "Bitcoin ETF flows are accelerating today.",
            "impression_count": 6_000,
            "like_count": 30,
        }
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            self.assertEqual(1, research.ingest_watchlist_posts([post], NOW, state_path))
            signals = research.discovery_signals(NOW, state_path)
        self.assertEqual(1, len(signals))
        self.assertEqual("watchlist_timeline", signals[0]["discovery_type"])
        self.assertIn("厳選リストで観測", signals[0]["summary"])

    def test_watchlist_rejects_media_and_promotional_noise_before_editorial_research(self):
        post = {
            "post_url": "https://x.com/coindesk/status/2086000000000000008",
            "handle": "coindesk",
            "posted_at": "2026-08-06T02:50:00Z",
            "text": "Bitcoin ETF inflows are accelerating today.",
            "impression_count": 60_000,
            "like_count": 300,
        }
        casino = post | {
            "post_url": "https://x.com/analyst/status/2086000000000000009",
            "handle": "analyst",
            "text": "Best crypto casino giveaway today",
        }
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            self.assertEqual(0, research.ingest_watchlist_posts([post, casino], NOW, state_path))
            self.assertEqual([], research.discovery_signals(NOW, state_path))

    def test_deep_search_is_limited_but_spike_can_advance_it(self):
        state = research.default_state()
        state["last_deep_scan_at"] = "2026-08-06T02:30:00+00:00"
        self.assertFalse(research._should_deep_scan(state, NOW, False))
        self.assertTrue(research._should_deep_scan(state, NOW, True))
        state["scans"] = [
            {"checked_at": "2026-08-06T00:00:00+00:00", "action": "search"}
            for _ in range(research.MAX_DEEP_SEARCHES_PER_DAY)
        ]
        self.assertFalse(research._should_deep_scan(state, NOW, True))

    def test_topic_rotation_also_works_without_bearer_counts(self):
        state = research.default_state()
        first = research._next_topic(state)[0]
        state["scans"] = [{"checked_at": "2026-08-06T02:00:00+00:00", "action": "search"}]
        second = research._next_topic(state)[0]
        self.assertNotEqual(first, second)

    def test_recent_search_ends_before_request_time(self):
        class Client:
            def __init__(self):
                self.kwargs = None

            def search_recent_tweets(self, **kwargs):
                self.kwargs = kwargs
                return SimpleNamespace(data=[], includes={})

        client = Client()
        research._search_recent(client, "crypto_market", "Bitcoin", NOW, set())
        self.assertLess(client.kwargs["end_time"], NOW)
        self.assertEqual(dt.timedelta(seconds=15), NOW - client.kwargs["end_time"])

    @patch("inu_x_research_agent._get_client")
    def test_without_bearer_token_agent_still_runs_direct_search_on_schedule(self, get_client):
        get_client.return_value = SimpleNamespace()
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"X_RESEARCH_BEARER_TOKEN": ""}, clear=False), patch.object(
            research, "_search_recent", return_value=[]
        ) as search:
            result = research.scan(NOW, Path(directory) / "state.json")
        self.assertTrue(result["searched"])
        search.assert_called_once()


if __name__ == "__main__":
    unittest.main()
