"""INUのA〜D成長施策が無差別な反応にならないことを検証する。"""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
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
        "opinion": "僕の見方では、次は実際の資金流入を確認したいです。",
        "reply_text": "公式資料では、承認の対象と条件が明記されています。\n\n見出しだけで判断せず、実際の資金流入が始まるかを見る必要があります。\n\n僕の見方では、次に確認すべきなのは取引開始後のフローです。",
        "mention_context": "ETFと機関資金の動きを追うなら、@example の直近の検証も見る価値があります。",
        "trend_keyword": "ETF",
        "why_this_matters": "承認後の資金流入は暗号資産市場の需給を直接左右するため",
        "why_target": "ETFと機関資金を継続的に扱う投資情報アカウントの新規投稿のため",
        "estimated_recent_impressions": 25000,
    }
    value.update(overrides)
    return value


class GrowthBoostTests(unittest.TestCase):
    def state(self) -> dict:
        return {"version": 1, "stopped": False, "actions": []}

    @patch("inu_growth_boost._verify_primary_source", return_value="https://official.example/release")
    def test_viral_reply_needs_genuine_reach_and_primary_source(self, _source):
        self.assertEqual(
            "2085000000000000001",
            inu_growth_boost.validate_candidate(signal(), self.state(), NOW),
        )
        with self.assertRaisesRegex(ValueError, "話題性"):
            inu_growth_boost.validate_candidate(
                signal(estimated_recent_impressions=9999), self.state(), NOW
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
        )
        self.assertEqual(
            "2085000000000000001",
            inu_growth_boost.validate_candidate(item, self.state(), NOW),
        )

    @patch("inu_growth_boost._verify_primary_source", return_value="https://official.example/release")
    def test_daily_limit_and_duplicate_target_are_enforced(self, _source):
        state = self.state()
        state["actions"] = [
            {
                "tactic": "C",
                "post_url": "https://x.com/other/status/2085000000000000002",
                "acted_at": "2026-08-05T11:40:00+00:00",
            }
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

    @patch("inu_growth_boost.collect_candidates", return_value=[])
    @patch("inu_growth_boost.follower_count", return_value=1000)
    def test_target_reached_stops_without_research(self, _followers, _candidates):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            result = inu_growth_boost.run(type("Args", (), {"state": str(path)})())
            self.assertEqual(0, result)
            state = inu_growth_boost.load_state(path)
            self.assertTrue(state["stopped"])
            _candidates.assert_not_called()

    @patch("inu_growth_boost.collect_candidates", side_effect=RuntimeError("no X citations"))
    @patch("inu_growth_boost.follower_count", return_value=42)
    def test_missing_x_search_citation_is_a_safe_skip(self, _followers, _candidates):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            result = inu_growth_boost.run(type("Args", (), {"state": str(path)})())
            self.assertEqual(0, result)
            state = inu_growth_boost.load_state(path)
            self.assertIn("x_search_unavailable", state["last_skip_reason"])


if __name__ == "__main__":
    unittest.main()
