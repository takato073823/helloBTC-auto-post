from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import inu_follow_growth as growth


NOW = dt.datetime(2026, 8, 7, 4, 0, tzinfo=dt.timezone.utc)


def profile(followers: int = 1_000):
    return SimpleNamespace(
        id="101", username="marketjp", protected=False,
        description="ビットコインと米国株のデータを確認",
        public_metrics={"followers_count": followers},
    )


def tweets():
    return [
        SimpleNamespace(id="1", text="ビットコインETFの資金フローを確認", lang="ja", created_at="2026-08-07T03:00:00Z"),
        SimpleNamespace(id="2", text="米国株と金利のデータを更新", lang="ja", created_at="2026-08-07T02:00:00Z"),
    ]


class FollowGrowthTests(unittest.TestCase):
    def test_candidate_requires_japanese_relevant_account_and_at_most_1000_followers(self):
        candidate = {"handle": "marketjp", "recent_post_url": "https://x.com/marketjp/status/1", "why_relevant": "金融データを継続発信"}
        record = growth._candidate_record(profile(), tweets(), candidate, "self", set(), NOW)
        self.assertEqual("marketjp", record["handle"])
        self.assertIsNone(growth._candidate_record(profile(1_001), tweets(), candidate, "self", set(), NOW))

    @patch("inu_follow_growth.unfollow_user", return_value=True)
    def test_reconcile_keeps_followback_and_unfollows_only_after_two_days(self, unfollow):
        state = {"targets": {
            "kept": {"status": "pending", "followed_at": "2026-08-05T03:59:00+00:00"},
            "expired": {"status": "pending", "followed_at": "2026-08-05T03:59:00+00:00"},
            "fresh": {"status": "pending", "followed_at": "2026-08-06T04:00:00+00:00"},
        }}
        kept, unfollowed = growth.reconcile_followbacks(state, {"kept"}, NOW)
        self.assertEqual((1, 1), (kept, unfollowed))
        self.assertEqual("followed_back", state["targets"]["kept"]["status"])
        self.assertEqual("unfollowed", state["targets"]["expired"]["status"])
        self.assertEqual("pending", state["targets"]["fresh"]["status"])
        unfollow.assert_called_once_with("expired")

    def test_discovery_is_throttled_to_six_per_day_and_four_hours(self):
        state = {"targets": {str(index): {"status": "pending", "followed_at": NOW.isoformat()} for index in range(6)}}
        self.assertFalse(growth.should_discover(state, NOW))
        self.assertFalse(growth.should_discover({"targets": {}, "last_discovery_at": "2026-08-07T01:00:00+00:00"}, NOW))
        self.assertTrue(growth.should_discover({"targets": {}, "last_discovery_at": "2026-08-07T00:00:00+00:00"}, NOW))

    @patch("inu_follow_growth.follow_user", return_value=True)
    @patch("inu_follow_growth.discover_candidates")
    def test_run_records_one_verified_follow(self, discover, follow):
        discover.return_value = [{"handle": "marketjp", "recent_post_url": "https://x.com/marketjp/status/1", "why_relevant": "金融データを継続発信"}]

        class Client:
            def get_users_followers(self, *_args, **_kwargs):
                return SimpleNamespace(data=[], meta={})

            def get_users(self, **_kwargs):
                return SimpleNamespace(data=[profile()])

            def get_users_tweets(self, *_args, **_kwargs):
                return SimpleNamespace(data=tweets())

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            result = growth.run(Client(), "self", path, NOW)
            self.assertTrue(result["followed"])
            self.assertEqual("pending", growth.load_state(path)["targets"]["101"]["status"])
            follow.assert_called_once_with("101")

    @patch("inu_follow_growth.follow_user", return_value=True)
    @patch("inu_follow_growth.discover_candidates")
    def test_run_does_not_follow_an_existing_follower(self, discover, follow):
        discover.return_value = [{"handle": "marketjp", "recent_post_url": "https://x.com/marketjp/status/1", "why_relevant": "金融データを継続発信"}]

        class Client:
            def get_users_followers(self, *_args, **_kwargs):
                return SimpleNamespace(data=[SimpleNamespace(id="101")], meta={})

            def get_users(self, **_kwargs):
                return SimpleNamespace(data=[profile()])

            def get_users_tweets(self, *_args, **_kwargs):
                return SimpleNamespace(data=tweets())

        with tempfile.TemporaryDirectory() as directory:
            result = growth.run(Client(), "self", Path(directory) / "state.json", NOW)
        self.assertFalse(result["followed"])
        follow.assert_not_called()
