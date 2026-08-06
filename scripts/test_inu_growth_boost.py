"""INUのA〜D成長施策が無差別な反応にならないことを検証する。"""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

import inu_growth_boost


NOW = dt.datetime(2026, 8, 5, 12, 0, tzinfo=dt.timezone.utc)


def signal(**overrides) -> dict:
    value = {
        "tactic": "C",
        "post_url": "https://x.com/example/status/2085000000000000001",
        "target_handle": "example",
        "posted_at": "2026-08-05T11:30:00Z",
        "source_name": "Example regulator",
        "source_url": "https://official.example/release",
        "source_published_at": "2026-08-05T11:15:00Z",
        "evidence_anchor": "Verified official evidence anchor",
        "hook": "📌 規制当局が新しいETF判断を公表",
        "facts": ["公式資料で条件変更を確認しました。"],
        "opinion": "",
        "reply_text": "公式資料では、承認の対象と条件が明記されています。\n\n市場への影響は、取引開始後の実際の資金流入で具体化します。\n\n対象商品の開始時点と日次フローを確認できる状態です。",
        "reply_options": [
            "公式資料では、承認の対象と条件が明記されています。\n\n市場への影響は、取引開始後の実際の資金流入で具体化します。\n\n対象商品の開始時点と日次フローを確認できる状態です。",
            "承認の事実に加え、公式資料には開始条件も記載されています。\n\n市場への影響は実際の資金流入で表れます。\n\n取引開始後は日次フローで需給の変化を確認できます。",
        ],
        "mention_context": "ETFと機関資金の動きを追うなら、@example の直近の検証も見る価値があります。",
        "trend_keyword": "ETF",
        "why_this_matters": "承認後の資金流入は暗号資産市場の需給を直接左右するため",
        "why_target": "ETFと機関資金を継続的に扱う投資情報アカウントの新規投稿のため",
        "estimated_recent_impressions": 25000,
        "actual_impression_count": 25000,
        "actual_like_count": 150,
    }
    value.update(overrides)
    return value


class GrowthBoostTests(unittest.TestCase):
    def setUp(self):
        # ローカルの実運用ウォッチリストの件数に依存せず、各候補単体の判定を検証する。
        patcher = patch("inu_growth_boost.active_watchlist_handles", return_value=set())
        patcher.start()
        self.addCleanup(patcher.stop)

    def state(self) -> dict:
        return {"version": 1, "stopped": False, "actions": []}

    @patch("inu_growth_boost._verify_primary_source", return_value="https://official.example/release")
    def test_viral_reply_needs_actual_likes_and_primary_source(self, _source):
        self.assertEqual(
            "2085000000000000001",
            inu_growth_boost.validate_candidate(signal(), self.state(), NOW),
        )
        with self.assertRaisesRegex(ValueError, "いいね数"):
            inu_growth_boost.validate_candidate(
                signal(actual_like_count=99), self.state(), NOW
            )

    @patch("inu_growth_boost._verify_primary_source", return_value="https://official.example/release")
    def test_expert_mention_requires_a_real_context(self, _source):
        item = signal(tactic="A", estimated_recent_impressions=1500, posted_at="2026-08-05T11:50:00Z")
        self.assertEqual(
            "2085000000000000001",
            inu_growth_boost.validate_candidate(item, self.state(), NOW),
        )
        with self.assertRaisesRegex(ValueError, "専門家紹介文"):
            inu_growth_boost.validate_candidate(
                signal(tactic="A", estimated_recent_impressions=1500, posted_at="2026-08-05T11:50:00Z", mention_context="@example"),
                self.state(),
                NOW,
            )

    @patch("inu_growth_boost._verify_primary_source", return_value="https://official.example/release")
    def test_reply_cannot_be_short_or_stale(self, _source):
        with self.assertRaisesRegex(ValueError, "情報量"):
            inu_growth_boost.validate_candidate(signal(reply_text="短い返信"), self.state(), NOW)
        with self.assertRaisesRegex(ValueError, "鮮度"):
            inu_growth_boost.validate_candidate(
                signal(posted_at="2026-08-05T10:00:00Z"), self.state(), NOW
            )

    def test_reply_variants_are_unique_and_keep_multiple_grok_options(self):
        options = inu_growth_boost._reply_variants(signal())
        self.assertEqual(2, len(options))
        self.assertNotEqual(options[0]["reply_text"], options[1]["reply_text"])

    def test_external_media_can_never_be_primary_evidence(self):
        with self.assertRaisesRegex(ValueError, "一次資料URL"):
            inu_growth_boost._verify_primary_source(
                signal(source_url="https://decrypt.co/news/important")
            )

    def test_early_like_does_not_need_to_create_text_or_media(self):
        item = signal(
            tactic="B",
            posted_at="2026-08-05T11:50:00Z",
            estimated_recent_impressions=0,
            source_url="",
            source_name="",
            source_published_at="",
            evidence_anchor="",
            reply_text="",
            why_this_matters="",
            actual_impression_count=1_000,
            actual_like_count=10,
        )
        self.assertEqual(
            "2085000000000000001",
            inu_growth_boost.validate_candidate(item, self.state(), NOW),
        )

    def test_boost_b_uses_fresh_relevant_post_from_japanese_watchlist(self):
        posts = [{
            "post_url": "https://x.com/targetone/status/2085000000000000004",
            "handle": "targetone", "posted_at": "2026-08-05T11:50:00Z",
            "text": "ビットコインETFの資金フローを確認。",
            "impression_count": 5_000, "like_count": 20,
        }]
        found = inu_growth_boost.discover_boost_b(NOW, self.state(), target_posts=posts)
        self.assertIsNotNone(found)
        self.assertEqual("B", found["tactic"])
        self.assertEqual("targetone", found["target_handle"])

    def test_boost_b_rejects_stale_and_campaign_posts(self):
        old = [{
            "post_url": "https://x.com/targetone/status/2085000000000000005",
            "handle": "targetone", "posted_at": "2026-08-05T10:00:00Z",
            "text": "Bitcoin ETF giveaway", "impression_count": 5_000, "like_count": 20,
        }]
        self.assertIsNone(inu_growth_boost.discover_boost_b(NOW, self.state(), target_posts=old))
    @patch("inu_growth_boost._verify_primary_source", return_value="https://official.example/release")
    def test_daily_limit_and_duplicate_target_are_enforced(self, _source):
        state = self.state()
        state["actions"] = [
            {
                "tactic": "C",
                "post_url": "https://x.com/other/status/2085000000000000002",
                "acted_at": "2026-08-05T11:40:00+00:00",
            },
            {
                "tactic": "C",
                "post_url": "https://x.com/other/status/2085000000000000003",
                "acted_at": "2026-08-05T11:45:00+00:00",
            },
            {
                "tactic": "C",
                "post_url": "https://x.com/other/status/2085000000000000004",
                "acted_at": "2026-08-05T11:50:00+00:00",
            },
        ]
        with self.assertRaisesRegex(ValueError, "日次上限"):
            inu_growth_boost.validate_candidate(signal(), state, NOW)
        state["actions"] = [
            {
                "tactic": "A",
                "post_url": signal()["post_url"],
                "acted_at": "2026-08-05T11:40:00+00:00",
            }
        ]
        with self.assertRaisesRegex(ValueError, "すでに反応済み"):
            inu_growth_boost.validate_candidate(signal(), state, NOW)

    @patch("inu_growth_boost.admit_qualified_new_followers", return_value=[])
    @patch("inu_growth_boost.recent_watchlist_posts", return_value=[])
    @patch("inu_growth_boost.collect_candidates", return_value=[])
    @patch("inu_growth_boost.follower_count", return_value=1000)
    def test_target_reached_stops_without_research(self, _followers, _candidates, _posts, _admit):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            result = inu_growth_boost.run(type("Args", (), {"state": str(path)})())
            self.assertEqual(0, result)
            state = inu_growth_boost.load_state(path)
            self.assertTrue(state["stopped"])
            _candidates.assert_not_called()

    @patch("inu_growth_boost.admit_qualified_new_followers", return_value=[])
    @patch("inu_growth_boost.recent_watchlist_posts", return_value=[])
    @patch("inu_growth_boost._get_client", return_value=SimpleNamespace())
    @patch("inu_growth_boost.collect_candidates", side_effect=RuntimeError("no X citations"))
    @patch("inu_growth_boost.follower_count", return_value=42)
    def test_missing_x_search_citation_is_a_safe_skip(self, _followers, _candidates, _client, _posts, _admit):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            result = inu_growth_boost.run(type("Args", (), {"state": str(path)})())
            self.assertEqual(0, result)
            state = inu_growth_boost.load_state(path)
            self.assertIn("x_search_unavailable", state["last_skip_reason"])

    @patch("inu_growth_boost.admit_qualified_new_followers", return_value=[])
    @patch("inu_growth_boost.recent_watchlist_posts", return_value=[])
    @patch("inu_growth_boost._get_client", return_value=SimpleNamespace())
    @patch("inu_growth_boost.collect_candidates", return_value=[])
    @patch("inu_growth_boost.follower_count", return_value=42)
    def test_successful_search_clears_previous_skip_reason(self, _followers, _candidates, _client, _posts, _admit):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            inu_growth_boost.save_state({"version": 1, "stopped": False, "actions": [], "last_skip_reason": "x_search_unavailable: old"}, path)
            result = inu_growth_boost.run(type("Args", (), {"state": str(path)})())
            self.assertEqual(0, result)
            self.assertEqual("", inu_growth_boost.load_state(path)["last_skip_reason"])

    def test_boost_b_prioritizes_reach_with_low_like_rate(self):
        posts = [
            {"post_url": "https://x.com/a/status/2085000000000000011", "handle": "a", "posted_at": "2026-08-05T11:50:00Z", "text": "Bitcoin ETF update", "impression_count": 8_000, "like_count": 50},
            {"post_url": "https://x.com/b/status/2085000000000000012", "handle": "b", "posted_at": "2026-08-05T11:51:00Z", "text": "Bitcoin ETF update", "impression_count": 2_000, "like_count": 100},
            {"post_url": "https://x.com/c/status/2085000000000000013", "handle": "c", "posted_at": "2026-08-05T11:52:00Z", "text": "Bitcoin ETF update", "impression_count": 450, "like_count": 0},
        ]
        found = inu_growth_boost._boost_b_from_watchlist_posts(NOW, self.state(), posts)
        self.assertEqual("a", found["target_handle"])

    def test_new_follower_requires_both_explicit_thresholds(self):
        profile = SimpleNamespace(id="follower-1", username="newfollower", protected=False, public_metrics={"followers_count": 1_000})
        tweet = SimpleNamespace(id="2085000000000000014", text="ビットコインETFを更新", lang="ja", created_at="2026-08-05T11:50:00Z", public_metrics={"impression_count": 501, "like_count": 5})
        japanese_second = SimpleNamespace(id="2085000000000000016", text="ETFの資金流入を確認", lang="ja", created_at="2026-08-05T11:45:00Z", public_metrics={"impression_count": 300, "like_count": 4})
        record = inu_growth_boost._follower_admission_record(profile, [tweet, japanese_second], "self", set(), NOW)
        self.assertEqual("newfollower", record["handle"])
        self.assertIsNone(inu_growth_boost._follower_admission_record(profile, [SimpleNamespace(id="2085000000000000015", text="Bitcoin", lang="en", created_at="2026-08-05T11:50:00Z", public_metrics={"impression_count": 500, "like_count": 5})], "self", set(), NOW))

    @patch("inu_growth_boost._verify_primary_source", return_value="https://official.example/release")
    def test_trend_keyword_must_be_in_the_published_text(self, _source):
        item = signal(tactic="D", posted_at="2026-08-05T11:50:00Z", hook="📌 市場の流れを確認", facts=["公式資料で条件変更を確認しました。"], opinion="", trend_keyword="ETF")
        with self.assertRaisesRegex(ValueError, "トレンド接続"):
            inu_growth_boost.validate_candidate(item, self.state(), NOW)

    def test_same_status_id_is_never_reacted_to_twice(self):
        state = self.state()
        state["actions"] = [{"tactic": "B", "post_url": "https://twitter.com/Example/status/2085000000000000001", "acted_at": "2026-08-05T11:40:00+00:00"}]
        item = signal(tactic="B", source_url="", source_name="", source_published_at="", evidence_anchor="", reply_text="", why_this_matters="", actual_impression_count=1000, actual_like_count=10)
        with self.assertRaisesRegex(ValueError, "すでに反応済み"):
            inu_growth_boost.validate_candidate(item, state, NOW)

    def test_like_rate_requires_high_reach_and_low_reaction(self):
        base = signal(tactic="B", source_url="", source_name="", source_published_at="", evidence_anchor="", reply_text="", why_this_matters="")
        self.assertEqual("2085000000000000001", inu_growth_boost.validate_candidate(base | {"actual_impression_count": 500, "actual_like_count": 15}, self.state(), NOW))
        with self.assertRaisesRegex(ValueError, "高表示・低いいね"):
            inu_growth_boost.validate_candidate(base | {"actual_impression_count": 500, "actual_like_count": 16}, self.state(), NOW)

    @patch("inu_growth_boost.save_watchlist_state")
    @patch("inu_growth_boost.load_watchlist_state")
    def test_new_follower_admission_prefilters_before_timeline_reads(self, load_watchlist, _save):
        load_watchlist.return_value = {"list_id": "list-1", "members": {}}
        low = SimpleNamespace(id="low", username="lowaccount", protected=False, public_metrics={"followers_count": 999})
        qualified = SimpleNamespace(id="good", username="goodaccount", protected=False, public_metrics={"followers_count": 1_000})
        qualifying_tweet = SimpleNamespace(id="2085000000000000099", text="ビットコインETFを更新", lang="ja", created_at="2026-08-05T11:50:00Z", public_metrics={"impression_count": 501, "like_count": 2})
        qualifying_second = SimpleNamespace(id="2085000000000000100", text="資金フローを確認", lang="ja", created_at="2026-08-05T11:45:00Z", public_metrics={"impression_count": 200, "like_count": 1})

        class Api:
            def __init__(self):
                self.timeline_reads = []
                self.added = []
            def get_me(self, **_kwargs): return SimpleNamespace(data=SimpleNamespace(id="self"))
            def get_list_members(self, *_args, **_kwargs): return SimpleNamespace(data=[], meta={})
            def get_users_followers(self, *_args, **_kwargs): return SimpleNamespace(data=[low, qualified], meta={})
            def get_users_tweets(self, user_id, **_kwargs):
                self.timeline_reads.append(user_id)
                return SimpleNamespace(data=[qualifying_tweet, qualifying_second])
            def add_list_member(self, _list_id, user_id, **_kwargs): self.added.append(user_id)

        api = Api()
        posts = inu_growth_boost.admit_qualified_new_followers(self.state(), api, NOW)
        self.assertEqual(["good"], api.timeline_reads)
        self.assertEqual(["good"], api.added)
        self.assertEqual("goodaccount", posts[0]["handle"])


if __name__ == "__main__":
    unittest.main()
