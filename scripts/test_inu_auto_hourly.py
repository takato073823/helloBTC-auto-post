"""最新一次情報の選定・鮮度・重複防止をオフラインで検証する。"""

from __future__ import annotations

import datetime as dt
import unittest
from unittest.mock import patch

import inu_auto_hourly


NOW = dt.datetime(2026, 8, 4, 12, 0, tzinfo=dt.timezone.utc)


def candidate(**overrides) -> dict:
    value = {
        "has_candidate": True,
        "skip_reason": "",
        "topic_type": "etf_flow",
        "hook": "米国のビットコインETF資金が反転",
        "facts": ["公式集計で1億ドルの純流入を確認しました。"],
        "opinion": "僕は、流入先の偏りが次の注目点だと見ています。",
        "source_name": "Example ETF公式",
        "source_url": "https://example.com/official/flow?utm_source=test",
        "published_at": "2026-08-04T08:00:00Z",
        "evidence_anchor": "Net inflow 100 million",
        "visual_route": "official_data_crop",
        "tags": ["ビットコイン", "ETF"],
        "why_now": "4時間前に公式データが更新されたため",
        "is_primary_source": True,
    }
    value.update(overrides)
    return value


class INUAutoHourlyTests(unittest.TestCase):
    def test_tracking_parameters_are_removed(self):
        actual = inu_auto_hourly.normalize_url(
            "HTTPS://Example.COM/release/?utm_source=x&id=2#top"
        )
        self.assertEqual("https://example.com/release?id=2", actual)

    def test_candidate_url_must_be_in_web_search_sources(self):
        with self.assertRaisesRegex(ValueError, "参照元一覧"):
            inu_auto_hourly.validate_candidate(
                candidate(),
                [{"url": "https://other.example/official", "title": "other"}],
                {"posted_slots": [], "posted_ids": [], "history": []},
                NOW,
            )

    def test_stale_candidate_is_rejected(self):
        item = candidate(published_at="2026-08-03T12:00:00Z")
        with self.assertRaisesRegex(ValueError, "鮮度上限"):
            inu_auto_hourly.validate_candidate(
                item,
                [{"url": item["source_url"], "title": "official"}],
                {"posted_slots": [], "posted_ids": [], "history": []},
                NOW,
            )

    def test_reserved_source_is_not_selected_again(self):
        item = candidate()
        state = {
            "posted_slots": [],
            "posted_ids": [],
            "history": [],
            "reservations": [{"source_url": inu_auto_hourly.normalize_url(item["source_url"])}],
        }
        with self.assertRaisesRegex(ValueError, "予約済み"):
            inu_auto_hourly.validate_candidate(
                item,
                [{"url": item["source_url"], "title": "official"}],
                state,
                NOW,
            )

    def test_three_consecutive_same_topics_are_rejected(self):
        item = candidate()
        state = {
            "posted_slots": [],
            "posted_ids": [],
            "history": [{"topic_type": "etf_flow"}, {"topic_type": "etf_flow"}],
        }
        with self.assertRaisesRegex(ValueError, "3件連続"):
            inu_auto_hourly.validate_candidate(
                item,
                [{"url": item["source_url"], "title": "official"}],
                state,
                NOW,
            )

    def test_trusted_media_breaking_news_can_be_non_primary(self):
        item = candidate(
            topic_type="reported_breaking_news",
            source_url="https://www.coindesk.com/tech/2026/08/04/latest",
            published_at="2026-08-04T11:30:00Z",
            visual_route="reported_text_crop",
            is_primary_source=False,
        )
        inu_auto_hourly.validate_candidate(
            item,
            [{"url": item["source_url"], "title": "latest"}],
            {"posted_slots": [], "posted_ids": [], "history": []},
            NOW,
        )

    def test_media_and_text_are_always_required_by_prepared_item(self):
        text = inu_auto_hourly.compose_candidate_text(candidate())
        self.assertIn("僕は", text)
        self.assertNotIn("https://", text)
        inu_auto_hourly.validate_post(text)

    def test_long_generated_copy_is_compacted_without_another_api_call(self):
        item = candidate(
            hook="重要な市場ニュースです" * 12,
            facts=["公式発表で重要な数値が更新されました。" * 12],
            opinion="僕は、この変化が次の市場の焦点になると見ています。" * 10,
            source_name="Example Official Investor Relations Department" * 4,
        )
        text = inu_auto_hourly.compose_candidate_text(item)
        inu_auto_hourly.validate_post(text)
        self.assertIn("僕は", text)
        self.assertIn("出典:", text)

    def test_trusted_media_fallback_keeps_the_rss_url(self):
        signals = [
            {
                "title": "BlackRock tokenizes European money market funds with Kinexys",
                "source": "Decrypt",
                "published": "Tue, 04 Aug 2026 11:40:44 +0000",
                "url": "https://decrypt.co/374894/blackrock-tokenizes-funds",
                "summary": "BlackRock expanded tokenized access to European money market funds using JPMorgan's Kinexys network.",
            }
        ]
        copy = {
            "hook": "ブラックロックが欧州MMFのトークン化を拡大",
            "facts": ["対象は欧州のマネー・マーケット・ファンドです。"],
            "opinion": "僕は、RWAの実利用が広がる動きとして注目しています。",
            "tags": ["RWA"],
        }
        with patch.object(inu_auto_hourly, "generate_json", return_value=copy):
            item = inu_auto_hourly.build_trusted_media_candidate(
                NOW, {"history": []}, signals
            )
        self.assertEqual(signals[0]["url"], item["source_url"])
        self.assertEqual(signals[0]["title"], item["evidence_anchor"])

    def test_priority_signal_cannot_switch_to_another_rss_story(self):
        selected = {
            "title": "SEC approves a new spot crypto ETF",
            "source": "CoinDesk",
            "published": "Tue, 04 Aug 2026 11:55:00 +0000",
            "url": "https://www.coindesk.com/policy/selected?utm_source=rss",
            "summary": "The regulator approved a new spot crypto exchange traded fund after completing its review.",
        }
        other = dict(selected, title="Other story", url="https://www.coindesk.com/other")
        expected = candidate(
            topic_type="reported_breaking_news",
            source_url=selected["url"],
            visual_route="reported_text_crop",
            is_primary_source=False,
        )
        with patch.object(
            inu_auto_hourly,
            "collect_discovery_signals",
            return_value=[other, selected],
        ), patch.object(
            inu_auto_hourly,
            "build_trusted_media_candidate",
            return_value=expected,
        ) as build:
            actual, sources = inu_auto_hourly.research_priority_signal(
                NOW,
                {"history": []},
                "https://www.coindesk.com/policy/selected",
            )
        self.assertEqual(expected, actual)
        self.assertEqual([selected], sources)
        build.assert_called_once_with(NOW, {"history": []}, [selected])

    def test_breaking_reservation_keeps_priority(self):
        state = {"reservations": [], "posted_slots": [], "posted_ids": [], "history": []}
        updated = inu_auto_hourly._reserve(
            state,
            {"id": "breaking_1"},
            candidate(),
            "breaking_news_123",
            NOW,
            priority="breaking",
        )
        self.assertEqual("breaking", updated["reservations"][0]["priority"])


if __name__ == "__main__":
    unittest.main()
