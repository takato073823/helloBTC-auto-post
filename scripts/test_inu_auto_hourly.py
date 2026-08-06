"""最新一次情報の選定・鮮度・重複防止をオフラインで検証する。"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests

import inu_auto_hourly
import inu_manual_news
import inu_quote_post


NOW = dt.datetime(2026, 8, 4, 12, 0, tzinfo=dt.timezone.utc)


def candidate(**overrides) -> dict:
    value = {
        "has_candidate": True,
        "skip_reason": "",
        "topic_type": "etf_flow",
        "hook": "米国のビットコインETF資金が反転",
        "facts": ["公式集計で1億ドルの純流入を確認しました。"],
        "opinion": "",
        "source_name": "Example ETF公式",
        "source_url": "https://example.com/official/flow?utm_source=test",
        "published_at": "2026-08-04T08:00:00Z",
        "evidence_anchor": "Net inflow 100 million",
        "evidence_as_primary": False,
        "visual_route": "official_data_crop",
        "tags": ["ビットコイン", "ETF"],
        "why_now": "4時間前に公式データが更新されたため",
        "reader_interest": "資金流入が継続するかで、ビットコインETFへの需要の強さを確認できるため",
        "follow_value": "ETFフローと機関投資家の資金移動を継続して追えるため",
        "is_primary_source": True,
    }
    value.update(overrides)
    return value


class INUAutoHourlyTests(unittest.TestCase):
    def test_individual_posts_use_state_files_separate_from_scheduled_posts(self):
        self.assertNotEqual(inu_auto_hourly.STATE_PATH, inu_manual_news.STATE_PATH)
        self.assertNotEqual(inu_auto_hourly.STATE_PATH, inu_quote_post.STATE_PATH)

    def test_market_chart_fallback_never_contains_personal_opinion(self):
        text = inu_auto_hourly._market_fallback_text(
            {
                "change_24h": -2.4,
                "period_high": 1.11,
                "period_low": 0.98,
                "last_close": 1.0524,
                "position": 0.56,
            },
            "XRP-USD",
            6,
        )
        self.assertNotIn("僕", text)
        self.assertIn("同レンジの56％地点", text)

    def test_grok_editorial_options_cannot_change_verified_facts_or_source(self):
        item = candidate()
        grok_copy = {
            "candidates": [{
                "hook": "⚡️ 米国ビットコインETFの資金が反転",
                "opinion": "",
                "why_now": "公式集計で当日の純流入額が更新されたためです。",
                "reader_interest": "資金流入の継続性を、次の需給判断へつなげられるためです。",
                "follow_value": "ETFフローと機関資金の変化を継続して確認できるためです。",
                "tags": ["ビットコイン"],
            }]
        }
        with patch.object(inu_auto_hourly, "generate_editorial_json", return_value=grok_copy):
            selected = inu_auto_hourly._select_grok_editorial_copy(
                item,
                [{"url": item["source_url"], "title": "official"}],
                {"posted_slots": [], "posted_ids": [], "history": [], "reservations": []},
                NOW,
            )
        self.assertEqual(item["facts"], selected["facts"])
        self.assertEqual(item["source_url"], selected["source_url"])
        self.assertEqual(item["evidence_anchor"], selected["evidence_anchor"])
        self.assertEqual("⚡️ 米国ビットコインETFの資金が反転", selected["hook"])
        self.assertEqual("", selected["opinion"])

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

    def test_category_review_rejects_a_different_topic(self):
        item = candidate(topic_type="onchain")
        with self.assertRaisesRegex(ValueError, "確認対象と異なる投稿系統"):
            inu_auto_hourly.validate_candidate(
                item,
                [{"url": item["source_url"], "title": "official"}],
                {"posted_slots": [], "posted_ids": [], "history": []},
                NOW,
                required_topic="etf_flow",
            )

    def test_category_research_prompt_limits_the_requested_topic(self):
        prompt = inu_auto_hourly.build_research_prompt(
            NOW,
            {"history": []},
            target_topic="macro_event",
        )
        self.assertIn("BLS Release Calendar", prompt)
        self.assertIn("topic_type=macro_event", prompt)
        self.assertNotIn("[etf_flow]", prompt)

    def test_uncited_primary_source_is_added_only_after_live_verification(self):
        item = candidate()
        sources = [{"url": "https://other.example/official", "title": "other"}]
        with patch.object(
            inu_auto_hourly,
            "fetch_and_verify_source",
            return_value=inu_auto_hourly.normalize_url(item["source_url"]),
        ) as verify, patch.object(
            inu_auto_hourly,
            "capture_official_evidence",
        ), patch.object(
            inu_auto_hourly,
            "capture_source_hero_image",
        ), patch.object(
            inu_auto_hourly,
            "validate_test_item",
        ), tempfile.TemporaryDirectory(dir=inu_auto_hourly.REPO_ROOT / "scripts") as directory:
            artifact_dir = Path(directory)
            with patch.object(inu_auto_hourly, "ARTIFACT_DIR", artifact_dir):
                built, selected = inu_auto_hourly._build_item_from_candidate(
                    item,
                    sources,
                    {"posted_slots": [], "posted_ids": [], "history": []},
                    NOW,
                    "2026-08-04-21",
                )
        self.assertEqual(
            inu_auto_hourly.normalize_url(item["source_url"]), selected["source_url"]
        )
        self.assertEqual("etf_flow", built["topic_type"])
        self.assertEqual(1, verify.call_count)
        self.assertIn(
            inu_auto_hourly.normalize_url(item["source_url"]),
            {inu_auto_hourly.normalize_url(row["url"]) for row in sources},
        )

    def test_uncited_source_is_not_added_when_live_verification_fails(self):
        item = candidate()
        sources = [{"url": "https://other.example/official", "title": "other"}]
        with patch.object(
            inu_auto_hourly,
            "fetch_and_verify_source",
            side_effect=ValueError("根拠原文が一次資料ページ内に確認できません"),
        ):
            with self.assertRaisesRegex(ValueError, "根拠原文"):
                inu_auto_hourly._build_item_from_candidate(
                    item,
                    sources,
                    {"posted_slots": [], "posted_ids": [], "history": []},
                    NOW,
                    "2026-08-04-21",
                )
        self.assertEqual(1, len(sources))

    def test_stale_candidate_is_rejected(self):
        item = candidate(published_at="2026-08-03T12:00:00Z")
        with self.assertRaisesRegex(ValueError, "鮮度上限"):
            inu_auto_hourly.validate_candidate(
                item,
                [{"url": item["source_url"], "title": "official"}],
                {"posted_slots": [], "posted_ids": [], "history": []},
                NOW,
            )

    def test_scheduled_macro_candidate_older_than_four_hours_is_rejected(self):
        item = candidate(
            topic_type="macro_event",
            published_at="2026-08-04T07:30:00Z",
        )
        with self.assertRaisesRegex(ValueError, "鮮度上限"):
            inu_auto_hourly.validate_candidate(
                item,
                [{"url": item["source_url"], "title": "official"}],
                {"posted_slots": [], "posted_ids": [], "history": []},
                NOW,
            )

    def test_candidate_without_material_change_is_rejected(self):
        item = candidate(
            hook="公式資料が更新されました",
            facts=["今回の資料には新しい情報が掲載されています。"],
            evidence_anchor="Official updated information",
        )
        with self.assertRaisesRegex(ValueError, "具体的な変化"):
            inu_auto_hourly.validate_candidate(
                item,
                [{"url": item["source_url"], "title": "official"}],
                {"posted_slots": [], "posted_ids": [], "history": []},
                NOW,
            )

    def test_candidate_needs_distinct_why_now(self):
        item = candidate(
            why_now="資金流入が継続するかで、ビットコインETFへの需要の強さを確認できるため",
        )
        with self.assertRaisesRegex(ValueError, "今投稿する理由と読者価値"):
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

    def test_third_party_media_cards_are_rejected_before_posting(self):
        item = candidate(
            topic_type="reported_breaking_news",
            source_url="https://www.coindesk.com/markets/new-topic",
            published_at="2026-08-04T11:30:00Z",
            visual_route="reported_text_crop",
            is_primary_source=False,
        )
        state = {
            "posted_slots": [],
            "posted_ids": [],
            "history": [
                {"topic_type": "reported_breaking_news", "hook": "AI企業の決算"},
                {"topic_type": "reported_breaking_news", "hook": "ビットコインETF"},
            ],
        }
        with self.assertRaisesRegex(ValueError, "第三者メディアURL"):
            inu_auto_hourly.validate_candidate(
                item,
                [{"url": item["source_url"], "title": "New market structure story"}],
                state,
                NOW,
            )

    def test_third_party_media_cannot_be_the_final_source(self):
        item = candidate(
            topic_type="reported_breaking_news",
            source_url="https://www.coindesk.com/tech/2026/08/04/latest",
            published_at="2026-08-04T11:30:00Z",
            visual_route="reported_text_crop",
            is_primary_source=False,
        )
        with self.assertRaisesRegex(ValueError, "第三者メディアURL"):
            inu_auto_hourly.validate_candidate(
                item,
                [{"url": item["source_url"], "title": "latest"}],
                {"posted_slots": [], "posted_ids": [], "history": []},
                NOW,
            )

    def test_cointelegraph_is_not_an_automatic_native_card_source(self):
        signals = [
            {
                "title": "A material crypto market change",
                "source": "Cointelegraph",
                "published": "Tue, 04 Aug 2026 11:40:44 +0000",
                "url": "https://cointelegraph.com/news/material-change",
                "summary": "A verified, material market development with concrete details.",
            }
        ]
        self.assertEqual([], inu_auto_hourly.trusted_media_signals(NOW, {"history": []}, signals))

    def test_local_crime_without_structural_crypto_impact_is_not_scheduled(self):
        signals = [
            {
                "title": "Former officer charged after $350,000 Bitcoin robbery",
                "source": "Decrypt",
                "published": "Tue, 04 Aug 2026 11:40:44 +0000",
                "url": "https://decrypt.co/374999/bitcoin-robbery",
                "summary": "Police say the suspects entered a private residence and stole Bitcoin worth $350,000.",
            }
        ]
        self.assertEqual([], inu_auto_hourly.trusted_media_signals(NOW, {"history": []}, signals))

    def test_exchange_breach_remains_eligible_despite_crime_language(self):
        signals = [
            {
                "title": "Exchange charged after wallet security breach exposes customer funds",
                "source": "Decrypt",
                "published": "Tue, 04 Aug 2026 11:40:44 +0000",
                "url": "https://decrypt.co/375000/exchange-security-breach",
                "summary": "Regulators charged the exchange after a security breach exposed customer wallet balances.",
            }
        ]
        self.assertEqual(signals, inu_auto_hourly.trusted_media_signals(NOW, {"history": []}, signals))

    def test_reported_news_never_builds_a_native_link_card(self):
        item = candidate(
            topic_type="reported_breaking_news",
            source_url="https://www.coindesk.com/policy/new-rule",
            visual_route="reported_text_crop",
            is_primary_source=False,
        )
        with self.assertRaisesRegex(ValueError, "第三者メディアURL"):
            inu_auto_hourly._build_item_from_candidate(
                item,
                [{"url": item["source_url"], "title": "new rule"}],
                {"posted_slots": [], "posted_ids": [], "history": []},
                NOW,
                "2026-08-04-21",
            )

    def test_official_data_post_uses_one_meaningful_evidence_image(self):
        item = candidate(
            topic_type="earnings",
            hook="企業の営業利益が前年同期比18％増",
            facts=["売上高の増加を受け、通期見通しを据え置きました。"],
            evidence_anchor="営業利益は前年同期比18％増",
            reader_interest="利益率と通期見通しから、事業の需要を確認できるためです。",
            follow_value="利益率と通期見通しの変化を継続して追えるためです。",
        )
        with tempfile.TemporaryDirectory(dir=inu_auto_hourly.SCRIPT_DIR) as directory:
            artifact_dir = Path(directory) / "inu-auto"
            with patch.object(
                inu_auto_hourly, "fetch_and_verify_source", return_value=item["source_url"]
            ), patch.object(inu_auto_hourly, "ARTIFACT_DIR", artifact_dir), patch.object(
                inu_auto_hourly, "capture_official_evidence"
            ) as evidence, patch.object(
                inu_auto_hourly, "capture_source_hero_image"
            ) as hero, patch.object(
                inu_auto_hourly, "generate_editorial_news_visual"
            ) as generated, patch.object(inu_auto_hourly, "validate_test_item"):
                built, _ = inu_auto_hourly._build_item_from_candidate(
                    item,
                    [{"url": item["source_url"], "title": "決算資料"}],
                    {"posted_slots": [], "posted_ids": [], "history": []},
                    NOW,
                    "2026-08-04-21",
                )
        self.assertTrue(built["media_path"].endswith("-evidence.png"))
        self.assertNotIn("additional_media", built)
        evidence.assert_called_once()
        hero.assert_not_called()
        generated.assert_not_called()

    def test_media_and_text_are_always_required_by_prepared_item(self):
        text = inu_auto_hourly.compose_candidate_text(candidate())
        self.assertNotIn("僕の見方では", text)
        self.assertNotIn("僕", text)
        self.assertNotIn("https://", text)
        self.assertNotIn(candidate()["reader_interest"], text)
        self.assertNotIn(candidate()["follow_value"], text)
        inu_auto_hourly.validate_post(text)

    def test_overseas_kol_video_is_prepared_as_native_video_reference(self):
        source = {
            "post_id": "2086000000000000001",
            "post_url": "https://x.com/globalmacro/status/2086000000000000001",
            "posted_at": "2026-08-04T11:10:00Z",
            "text": "Bitcoin ETF flow chart shows a material intraday change.",
            "handle": "globalmacro",
            "impression_count": 80_000,
            "like_count": 500,
            "has_video": True,
            "has_image": False,
        }
        payload = {
            "candidates": [{
                "source_tweet_id": source["post_id"],
                "delivery_mode": "x_native_video_reference",
                "hook": "📊 ETFフローの変化を確認",
                "facts": ["動画では短時間の資金フロー変化を示しています。"],
                "opinion": "",
                "tags": ["ビットコイン"],
                "why_now": "直近3時間の高表示動画で、資金フローの変化を視覚的に確認できるためです。",
                "reader_interest": "短期の資金フロー変化が価格と出来高に波及するかを判断する材料になるためです。",
                "follow_value": "ETFフローと価格反応の継続性を、次の更新でも追えるためです。",
            }],
            "skip_reason": "",
        }
        with patch.object(inu_auto_hourly, "collect_overseas_kol_visual_posts", return_value=[source]), patch.object(
            inu_auto_hourly, "generate_x_json", return_value=(payload, [])
        ):
            result = inu_auto_hourly._build_overseas_kol_quote_item(NOW, {"history": [], "reservations": []})
        self.assertIsNotNone(result)
        item, selected = result
        self.assertEqual("x_native_video_reference", item["delivery_mode"])
        self.assertEqual(source["post_id"], item["source_tweet_id"])
        self.assertNotIn("https://", item["text"])
        self.assertEqual(source["post_url"], selected["source_url"])

    def test_long_generated_copy_is_compacted_without_another_api_call(self):
        item = candidate(
            hook="重要な市場ニュースです" * 12,
            facts=["公式発表で重要な数値が更新されました。" * 12],
            opinion="",
            source_name="Example Official Investor Relations Department" * 4,
        )
        text = inu_auto_hourly.compose_candidate_text(item)
        inu_auto_hourly.validate_post(text)
        self.assertNotIn("僕としては", text)
        self.assertNotIn("僕", text)
        self.assertNotIn("出典", text)

    def test_trusted_media_is_discovery_only(self):
        signals = [
            {
                "title": "BlackRock tokenizes European money market funds with Kinexys",
                "source": "Decrypt",
                "published": "Tue, 04 Aug 2026 11:40:44 +0000",
                "url": "https://decrypt.co/374894/blackrock-tokenizes-funds",
                "summary": "BlackRock expanded tokenized access to European money market funds using JPMorgan's Kinexys network.",
            }
        ]
        self.assertEqual(signals, inu_auto_hourly.trusted_media_signals(NOW, {"history": []}, signals))

    def test_generic_official_announcement_is_rejected_before_posting(self):
        item = candidate(
            topic_type="macro_event",
            hook="米財務省、四半期定例入札を公表へ",
            facts=["米財務省の公式ページでは、2026年8月5日に四半期リファンディングを公表予定です。"],
            reader_interest="米国債市場の今後を確認する材料になるためです。",
        )
        with self.assertRaisesRegex(ValueError, "公式ページの説明だけ"):
            inu_auto_hourly.validate_candidate(
                item,
                [{"url": item["source_url"], "title": "Treasury announcement"}],
                {"posted_slots": [], "posted_ids": [], "history": []},
                NOW,
            )

    def test_earnings_schedule_or_ir_calendar_is_rejected_before_posting(self):
        item = candidate(
            topic_type="earnings",
            hook="GSユアサ、15時に1Q決算を発表予定",
            facts=["IRカレンダーに第1四半期決算発表を掲載しています。"],
            evidence_anchor="2027年3月期 第1四半期決算発表",
            reader_interest="発表予定を確認し、決算結果を待つ材料になるためです。",
            follow_value="GSユアサの車載・産業用電池の利益率と通期見通しを継続して追えるため",
        )
        with self.assertRaisesRegex(ValueError, "予定・IRカレンダー"):
            inu_auto_hourly.validate_candidate(
                item,
                [{"url": item["source_url"], "title": "IRカレンダー"}],
                {"posted_slots": [], "posted_ids": [], "history": []},
                NOW,
            )

    def test_earnings_result_with_specific_metrics_can_be_considered(self):
        item = candidate(
            topic_type="earnings",
            hook="GSユアサ、1Q営業利益が前年同期比18％増",
            facts=["車載電池の増収で通期見通しを据え置きました。"],
            evidence_anchor="営業利益は前年同期比18％増",
            reader_interest="利益率と通期見通しから、車載電池の需要を確認できるためです。",
            follow_value="車載・産業用電池の利益率と通期見通しを継続して追えるためです。",
        )
        inu_auto_hourly.validate_candidate(
            item,
            [{"url": item["source_url"], "title": "決算短信"}],
            {"posted_slots": [], "posted_ids": [], "history": []},
            NOW,
        )

    def test_follow_value_cannot_repeat_reader_interest(self):
        item = candidate(follow_value=candidate()["reader_interest"])
        with self.assertRaisesRegex(ValueError, "閲覧理由の言い換え"):
            inu_auto_hourly.validate_candidate(
                item,
                [{"url": item["source_url"], "title": "official"}],
                {"posted_slots": [], "posted_ids": [], "history": []},
                NOW,
            )

    def test_follow_value_can_reference_a_material_policy_announcement(self):
        item = candidate(
            follow_value="FOMCの金利発表と利下げ判断がリスク資産へ与える影響を継続して追えるため"
        )
        inu_auto_hourly.validate_candidate(
            item,
            [{"url": item["source_url"], "title": "official"}],
            {"posted_slots": [], "posted_ids": [], "history": []},
            NOW,
        )

    def test_research_prompt_softly_nudges_underrepresented_growth_topics(self):
        state = {
            "history": [
                {
                    "topic_type": "etf_flow",
                    "posted_at": "2026-08-04T11:00:00Z",
                }
                for _ in range(5)
            ]
        }
        topics = inu_auto_hourly._underrepresented_growth_topics(state, NOW)
        self.assertIn("onchain", topics)
        self.assertNotIn("etf_flow", topics)
        prompt = inu_auto_hourly.build_research_prompt(NOW, state)
        self.assertIn("直近7日間で手薄な投稿系統", prompt)
        self.assertIn("onchain", prompt)
        self.assertNotIn("必ずこの系統", prompt)
        self.assertIn("決算発表予定、IRカレンダー、説明会予定", prompt)
        self.assertIn("毎時の定期枠は必ず投稿まで到達させる", prompt)
        self.assertIn("その事実を一目で確認できる画像または動画", prompt)
        self.assertIn("内容を示す絵文字をhookの先頭に1個", prompt)

    def test_priority_signal_from_third_party_media_is_not_posted(self):
        selected = {
            "title": "SEC approves a new spot crypto ETF",
            "source": "CoinDesk",
            "published": "Tue, 04 Aug 2026 11:55:00 +0000",
            "url": "https://www.coindesk.com/policy/selected?utm_source=rss",
            "summary": "The regulator approved a new spot crypto exchange traded fund after completing its review.",
        }
        with self.assertRaisesRegex(LookupError, "最終出典"):
            inu_auto_hourly.research_priority_signal(
                NOW,
                {"history": []},
                "https://www.coindesk.com/policy/selected",
            )

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

    def test_single_source_daily_roundup_is_rejected_by_validation(self):
        item = candidate(
            topic_type="etf_flow",
            hook="暗号資産市場で本日起きた主要ニュースを総括",
            source_url="https://example.com/official/what-happened-today",
            evidence_anchor="What happened in crypto today",
            visual_route="official_data_crop",
            is_primary_source=True,
        )
        with self.assertRaisesRegex(ValueError, "単一ソースの総括"):
            inu_auto_hourly.validate_candidate(
                item,
                [{"url": item["source_url"], "title": "What happened in crypto today"}],
                {"posted_slots": [], "posted_ids": [], "history": []},
                NOW,
            )

    def test_trusted_media_discovery_skips_daily_roundup(self):
        signals = [
            {
                "title": "What happened in crypto today",
                "source": "CoinTelegraph",
                "published": "Tue, 04 Aug 2026 11:40:44 +0000",
                "url": "https://cointelegraph.com/news/what-happened-in-crypto-today",
                "summary": "A daily roundup of several unrelated cryptocurrency stories and market events.",
            }
        ]
        self.assertEqual([], inu_auto_hourly.trusted_media_signals(NOW, {"history": []}, signals))

    def test_discovery_drops_roundup_before_llm_research(self):
        articles = [
            {
                "title": "What happened in crypto today",
                "source": "CoinTelegraph",
                "published": "Tue, 04 Aug 2026 11:40:44 +0000",
                "url": "https://cointelegraph.com/news/what-happened-in-crypto-today",
                "description": "Daily roundup",
            },
            {
                "title": "BNY adds staking to digital asset custody",
                "source": "CoinDesk",
                "published": "Tue, 04 Aug 2026 11:45:00 +0000",
                "url": "https://www.coindesk.com/business/bny-staking",
                "description": "BNY plans to add staking to its digital asset custody platform.",
            },
        ]
        with patch.object(inu_auto_hourly, "fetch_from_rss", return_value=articles):
            signals = inu_auto_hourly.collect_discovery_signals()
        self.assertEqual([articles[1]["url"]], [row["url"] for row in signals])

    def test_research_returns_ranked_multiple_candidates(self):
        first = candidate()
        second = candidate(
            topic_type="onchain",
            source_url="https://example.com/official/onchain",
            visual_route="official_text_crop",
            is_primary_source=True,
        )
        payload = {"candidates": [first, second], "skip_reason": ""}
        with patch.object(inu_auto_hourly, "collect_discovery_signals", return_value=[]), patch.object(
            inu_auto_hourly,
            "generate_web_json",
            return_value=(payload, [{"url": first["source_url"], "title": "flow"}]),
        ):
            candidates, _, _ = inu_auto_hourly.research_candidates(NOW, {"history": []})
        self.assertEqual(2, len(candidates))
        self.assertEqual("official_data_crop", candidates[1]["visual_route"])
        self.assertTrue(candidates[1]["is_primary_source"])

    def test_grok_x_signal_is_discovery_only_and_not_a_final_source(self):
        x_signal = {
            "title": "公式アカウントがETFフローを速報",
            "source": "X @official",
            "published": NOW.isoformat(),
            "url": "https://x.com/official/status/123",
            "summary": "純流入が急増したため確認する",
            "discovery_type": "grok_x_search",
        }
        official = candidate()
        captured = {}

        def fake_web(prompt, **kwargs):
            captured["prompt"] = prompt
            return {"candidates": [official], "skip_reason": ""}, [
                {"url": official["source_url"], "title": "Official"}
            ]

        with patch.object(inu_auto_hourly, "collect_discovery_signals", return_value=[]), patch.object(
            inu_auto_hourly, "generate_web_json", side_effect=fake_web
        ):
            _, sources, signals = inu_auto_hourly.research_candidates(
                NOW, {"history": []}, extra_signals=[x_signal]
            )
        self.assertIn("Grok X Search", captured["prompt"])
        self.assertEqual([x_signal], signals)
        self.assertNotIn(x_signal["url"], [row["url"] for row in sources])

    def test_curated_chinese_x_sources_are_prioritized_only_for_discovery(self):
        sources = inu_auto_hourly.load_curated_x_sources()
        self.assertEqual(
            [
                "WatcherGuru",
                "RelaxView",
                "falali2015",
                "tun2049",
                "damobianyuan",
                "huoshan007",
                "jiao_newlife",
                "gengdaJ",
                "lxfater",
                "AI_jacksaku",
                "star_okx",
                "CoinankCN",
                "CryptoSpill",
            ],
            [row["handle"] for row in sources],
        )
        prompt = inu_auto_hourly.build_grok_prompt(NOW, {"history": []})
        self.assertIn("@RelaxView", prompt)
        self.assertIn("発見専用", prompt)
        self.assertIn("最終根拠・転載元・投稿文の出典には絶対に使わない", prompt)

    def test_grok_failure_falls_back_to_existing_web_research(self):
        expected = ([candidate()], [{"url": "https://example.com"}], [])
        with patch.object(
            inu_auto_hourly,
            "collect_grok_discovery_signals",
            side_effect=RuntimeError("temporary failure"),
        ), patch.object(
            inu_auto_hourly,
            "collect_official_x_api_signals",
            return_value=[],
        ), patch.object(
            inu_auto_hourly,
            "research_candidates",
            return_value=expected,
        ) as fallback:
            actual = inu_auto_hourly.research_candidates_with_grok(NOW, {"history": []})
        self.assertEqual(expected, actual)
        fallback.assert_called_once_with(NOW, {"history": []}, extra_signals=[])

    def test_fallback_schedule_skips_duplicate_grok_charge(self):
        event = json.dumps({"schedule": "37 * * * *"})
        with patch.dict(
            "os.environ",
            {"XAI_API_KEY": "configured", "GITHUB_EVENT_PATH": "/tmp/event.json"},
            clear=False,
        ), patch.object(Path, "read_text", return_value=event):
            self.assertFalse(inu_auto_hourly._is_primary_grok_run())

    def test_primary_and_fallback_share_one_hourly_slot(self):
        now = dt.datetime(2026, 8, 4, 12, 47, tzinfo=dt.timezone.utc)
        self.assertEqual("2026-08-04-21-a", inu_auto_hourly._scheduled_slot_key(now, "primary"))
        self.assertEqual("2026-08-04-21-a", inu_auto_hourly._scheduled_slot_key(now, "fallback"))
        self.assertEqual("2026-08-04-21", inu_auto_hourly._scheduled_slot_key(now, "retry"))

    def test_market_fallback_excludes_a_recently_used_product(self):
        now = dt.datetime(2026, 8, 4, 12, 47, tzinfo=dt.timezone.utc)
        state = {
            "posted_slots": [
                {
                    "post_id": "inu_market_2026_08_04_11_a_xrp-usd",
                    "source_url": "https://www.tradingview.com/symbols/COINBASE-XRPUSD/",
                    "posted_at": (now - dt.timedelta(hours=2)).isoformat(),
                }
            ],
            "reservations": [],
        }
        metrics = {
            "BTC-USD": {"change_24h": 1.0, "position": 0.5},
            "XRP-USD": {"change_24h": -4.0, "position": 0.2},
        }
        artifacts = inu_auto_hourly.REPO_ROOT / "scripts" / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=artifacts) as directory, patch.object(
            inu_auto_hourly, "MARKET_FALLBACK_PRODUCTS", ("BTC-USD", "XRP-USD")
        ), patch.object(
            inu_auto_hourly, "ARTIFACT_DIR", Path(directory)
        ), patch(
            "x_price_chart_post.fetch_closed_candles",
            side_effect=lambda now, product: [{"product": product}],
        ), patch(
            "x_price_chart_post.calculate_metrics",
            side_effect=lambda candles: {
                **metrics[candles[0]["product"]],
                "last_close": 100.0,
                "period_high": 120.0,
                "period_low": 80.0,
                "closed_at": now,
            },
        ), patch(
            "x_price_chart_post.render_chart",
            return_value=Path(directory) / "chart.png",
        ), patch.object(inu_auto_hourly, "validate_test_item"):
            _, candidate = inu_auto_hourly.build_market_data_fallback(
                now, state, "2026-08-04-21-a"
            )
        self.assertIn("COINBASE-BTCUSD", candidate["source_url"])

    def test_prepare_tries_the_next_candidate_instead_of_failing(self):
        options = [candidate(), candidate(source_url="https://example.com/official/second")]
        item = {
            "id": "inu_auto_second",
            "topic_type": "etf_flow",
            "visual_route": "official_data_crop",
            "text": "候補2を採用\n\n公式資料で条件変更を確認しました。\n\n#仮想通貨",
            "media_path": "scripts/artifacts/inu-auto/test.png",
            "source_manifest": "scripts/artifacts/inu-auto/test.source.json",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prepared_path = root / "prepared.json"
            args = SimpleNamespace(
                state=str(root / "state.json"),
                slot="2026-08-04-21",
                priority_url="",
                dry_run=True,
            )
            with patch.object(
                inu_auto_hourly,
                "research_candidates",
                return_value=(options, [{"url": row["source_url"], "title": "source"} for row in options], []),
            ), patch.object(
                inu_auto_hourly,
                "_build_item_from_candidate",
                side_effect=[ValueError("first rejected"), (item, options[1])],
            ) as build, patch.object(inu_auto_hourly, "PREPARED_PATH", prepared_path):
                result = inu_auto_hourly.prepare(args)
            self.assertEqual(0, result)
            self.assertEqual(2, build.call_count)
            prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
            self.assertEqual(options[1]["source_url"], prepared["candidate"]["source_url"])

    def test_fallback_researches_again_after_primary_had_no_candidate(self):
        args = SimpleNamespace(state="/tmp/unused-state.json", slot="2026-08-04-21", priority_url="", dry_run=True)
        state = {"scheduled_checks": [{"slot": args.slot, "result": "no_verified_candidate"}]}
        market_item = {
            "id": "inu_market_test",
            "topic_type": "crypto_market",
            "visual_route": "market_service_screenshot",
            "text": "📈 BTC、主要6銘柄で直近24時間の値動き最大\n\n確定済みの1時間足で、直近24時間の値動きが最大でした。\n\n#仮想通貨",
            "media_path": "scripts/artifacts/inu-auto/test.png",
            "source_manifest": "scripts/artifacts/inu-auto/test.source.json",
        }
        market_candidate = {
            "topic_type": "crypto_market",
            "hook": "📈 BTC、主要6銘柄で直近24時間の値動き最大",
            "why_now": "主要銘柄を比較した確定値で最大の値動きが出たためです。",
            "follow_value": "BTCの出来高と3日レンジの更新を継続して追います。",
            "source_url": "https://www.tradingview.com/symbols/COINBASE-BTCUSD/",
            "published_at": NOW.isoformat(),
        }
        with patch.dict("os.environ", {"INU_SCHEDULE_RUN_KIND": "fallback"}, clear=False), patch.object(
            inu_auto_hourly, "load_state", return_value=state
        ), patch.object(inu_auto_hourly, "research_candidates_with_grok") as research:
            with tempfile.TemporaryDirectory() as directory, patch.object(
                inu_auto_hourly, "PREPARED_PATH", Path(directory) / "prepared.json"
            ), patch.object(
                inu_auto_hourly, "build_market_data_fallback", return_value=(market_item, market_candidate)
            ) as fallback:
                self.assertEqual(0, inu_auto_hourly.prepare(args))
        research.assert_called_once()
        fallback.assert_called_once()

    def test_prepare_repairs_copy_before_discarding_a_verified_candidate(self):
        option = candidate()
        repaired = candidate(opinion="")
        item = {
            "id": "inu_auto_repaired",
            "topic_type": "etf_flow",
            "visual_route": "official_data_crop",
            "text": "📈 ETF資金が反転\n\n公式集計で当日の純流入額が更新されました。\n\n#仮想通貨",
            "media_path": "scripts/artifacts/inu-auto/test.png",
            "source_manifest": "scripts/artifacts/inu-auto/test.source.json",
        }
        args = SimpleNamespace(state="/tmp/unused-state.json", slot="2026-08-04-21", priority_url="", dry_run=True)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            inu_auto_hourly, "load_state", return_value={"history": []}
        ), patch.object(
            inu_auto_hourly, "research_candidates_with_grok", return_value=([option], [{"url": option["source_url"], "title": "official"}], [])
        ), patch.object(
            inu_auto_hourly,
            "_build_item_from_candidate",
            side_effect=[ValueError("読者が今見る具体的な理由が不足しています"), (item, repaired)],
        ) as build, patch.object(
            inu_auto_hourly, "repair_candidate_editorial_copy", return_value=repaired
        ) as repair, patch.object(inu_auto_hourly, "PREPARED_PATH", Path(directory) / "prepared.json"):
            self.assertEqual(0, inu_auto_hourly.prepare(args))
        repair.assert_called_once()
        self.assertEqual(2, build.call_count)

    def test_prepare_does_not_repeat_an_unreachable_source_host(self):
        first = candidate(source_url="https://slow.example.com/first")
        second = candidate(source_url="https://slow.example.com/second")
        market_item = {
            "id": "inu_market_test",
            "topic_type": "crypto_market",
            "visual_route": "market_service_screenshot",
            "text": "📉 XRP、主要6銘柄で直近24時間の値動き最大\n\n確定済みの1時間足で、直近24時間の値動きが最大でした。\n\n#仮想通貨",
            "media_path": "scripts/artifacts/inu-auto/test.png",
            "source_manifest": "scripts/artifacts/inu-auto/test.source.json",
        }
        market_candidate = {
            "topic_type": "crypto_market",
            "hook": "📉 XRP、主要6銘柄で直近24時間の値動き最大",
            "why_now": "主要銘柄を比較した確定値で最大の値動きが出たためです。",
            "follow_value": "XRPの出来高と3日レンジの更新を継続して追います。",
            "source_url": "https://www.tradingview.com/symbols/COINBASE-XRPUSD/",
            "published_at": NOW.isoformat(),
        }
        args = SimpleNamespace(state="/tmp/unused-state.json", slot="2026-08-04-21", priority_url="", dry_run=True)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            inu_auto_hourly, "load_state", return_value={"history": []}
        ), patch.object(
            inu_auto_hourly,
            "research_candidates_with_grok",
            return_value=([first, second], [{"url": first["source_url"], "title": "official"}], []),
        ), patch.object(
            inu_auto_hourly,
            "_build_item_from_candidate",
            side_effect=requests.exceptions.ReadTimeout("source timed out"),
        ) as build, patch.object(
            inu_auto_hourly, "research_rescue_candidates", return_value=([], [])
        ), patch.object(
            inu_auto_hourly, "build_market_data_fallback", return_value=(market_item, market_candidate)
        ), patch.object(inu_auto_hourly, "PREPARED_PATH", Path(directory) / "prepared.json"):
            self.assertEqual(0, inu_auto_hourly.prepare(args))
        self.assertEqual(1, build.call_count)


if __name__ == "__main__":
    unittest.main()
