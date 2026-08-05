"""最新一次情報の選定・鮮度・重複防止をオフラインで検証する。"""

from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
        "opinion": "僕の見方では、次は流入先が広がるかを確認したいです。",
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

    def test_reported_news_is_not_blocked_only_by_the_source_category(self):
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
        inu_auto_hourly.validate_candidate(
            item,
            [{"url": item["source_url"], "title": "New market structure story"}],
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

    def test_reported_news_builds_a_native_link_card_without_image_capture(self):
        item = candidate(
            topic_type="reported_breaking_news",
            source_url="https://www.coindesk.com/policy/new-rule",
            visual_route="reported_text_crop",
            is_primary_source=False,
        )
        with tempfile.TemporaryDirectory() as directory:
            artifact_dir = Path(directory) / "inu-auto"
            with patch.object(
                inu_auto_hourly, "fetch_and_verify_source", return_value=item["source_url"]
            ), patch.object(inu_auto_hourly, "ARTIFACT_DIR", artifact_dir), patch.object(
                inu_auto_hourly, "capture_official_evidence"
            ) as evidence, patch.object(
                inu_auto_hourly, "capture_source_hero_image"
            ) as hero:
                built, _ = inu_auto_hourly._build_item_from_candidate(
                    item,
                    [{"url": item["source_url"], "title": "new rule"}],
                    {"posted_slots": [], "posted_ids": [], "history": []},
                    NOW,
                    "2026-08-04-21",
                )
            self.assertTrue(artifact_dir.is_dir())
        self.assertEqual(item["source_url"], built["link_card_url"])
        self.assertNotIn("media_path", built)
        evidence.assert_not_called()
        hero.assert_not_called()

    def test_media_and_text_are_always_required_by_prepared_item(self):
        text = inu_auto_hourly.compose_candidate_text(candidate())
        self.assertIn("僕の見方では", text)
        self.assertNotIn("https://", text)
        self.assertNotIn(candidate()["reader_interest"], text)
        self.assertNotIn(candidate()["follow_value"], text)
        inu_auto_hourly.validate_post(text)

    def test_long_generated_copy_is_compacted_without_another_api_call(self):
        item = candidate(
            hook="重要な市場ニュースです" * 12,
            facts=["公式発表で重要な数値が更新されました。" * 12],
            opinion="僕としては、次に資金の広がりを確認したいです。" * 10,
            source_name="Example Official Investor Relations Department" * 4,
        )
        text = inu_auto_hourly.compose_candidate_text(item)
        inu_auto_hourly.validate_post(text)
        self.assertIn("僕としては", text)
        self.assertNotIn("出典", text)

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
            "opinion": "僕としては、次は実際の利用先が増えるかを確認したいです。",
            "reader_interest": "トークン化されたMMFの利用先が増えるかで、RWAの実需を判断できるため",
            "follow_value": "大手機関によるRWAの実装範囲を継続して追えるため",
            "tags": ["RWA"],
        }
        with patch.object(inu_auto_hourly, "generate_json", return_value=copy):
            item = inu_auto_hourly.build_trusted_media_candidate(
                NOW, {"history": []}, signals
            )
        self.assertEqual(signals[0]["url"], item["source_url"])
        self.assertEqual(signals[0]["title"], item["evidence_anchor"])

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

    def test_single_source_daily_roundup_is_rejected_by_validation(self):
        item = candidate(
            topic_type="reported_breaking_news",
            hook="暗号資産市場で本日起きた主要ニュースを総括",
            source_url="https://cointelegraph.com/news/what-happened-in-crypto-today",
            published_at="2026-08-04T11:30:00Z",
            evidence_anchor="What happened in crypto today",
            visual_route="reported_text_crop",
            is_primary_source=False,
        )
        with self.assertRaisesRegex(ValueError, "単一ソースの総括"):
            inu_auto_hourly.validate_candidate(
                item,
                [{"url": item["source_url"], "title": "What happened in crypto today"}],
                {"posted_slots": [], "posted_ids": [], "history": []},
                NOW,
            )

    def test_trusted_media_fallback_skips_daily_roundup(self):
        signals = [
            {
                "title": "What happened in crypto today",
                "source": "CoinTelegraph",
                "published": "Tue, 04 Aug 2026 11:40:44 +0000",
                "url": "https://cointelegraph.com/news/what-happened-in-crypto-today",
                "summary": "A daily roundup of several unrelated cryptocurrency stories and market events.",
            }
        ]
        with self.assertRaisesRegex(LookupError, "主要メディア速報"):
            inu_auto_hourly.build_trusted_media_candidate(NOW, {"history": []}, signals)

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
            topic_type="reported_breaking_news",
            source_url="https://www.coindesk.com/policy/new-rule",
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
        self.assertEqual("reported_text_crop", candidates[1]["visual_route"])
        self.assertFalse(candidates[1]["is_primary_source"])

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
            "research_candidates",
            return_value=expected,
        ) as fallback:
            actual = inu_auto_hourly.research_candidates_with_grok(NOW, {"history": []})
        self.assertEqual(expected, actual)
        fallback.assert_called_once_with(NOW, {"history": []}, extra_signals=[])

    def test_retry_schedule_skips_second_grok_charge(self):
        event = json.dumps({"schedule": "47 * * * *"})
        with patch.dict(
            "os.environ",
            {"XAI_API_KEY": "configured", "GITHUB_EVENT_PATH": "/tmp/event.json"},
            clear=False,
        ), patch.object(Path, "read_text", return_value=event):
            self.assertFalse(inu_auto_hourly._is_primary_grok_run())

    def test_prepare_tries_the_next_candidate_instead_of_failing(self):
        options = [candidate(), candidate(source_url="https://example.com/official/second")]
        item = {
            "id": "inu_auto_second",
            "topic_type": "etf_flow",
            "visual_route": "official_data_crop",
            "text": "候補2を採用\n\n僕は、変化を確認します。\n\n#仮想通貨",
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

    def test_fallback_skips_only_after_primary_checked_same_slot(self):
        args = SimpleNamespace(
            state="/tmp/unused-state.json",
            slot="2026-08-04-21",
            priority_url="",
            dry_run=False,
        )
        state = {"scheduled_checks": [{"slot": args.slot, "result": "no_verified_candidate"}]}
        with patch.dict("os.environ", {"INU_SCHEDULE_RUN_KIND": "fallback"}, clear=False), patch.object(
            inu_auto_hourly, "load_state", return_value=state
        ), patch.object(inu_auto_hourly, "research_candidates_with_grok") as research:
            self.assertEqual(0, inu_auto_hourly.prepare(args))
        research.assert_not_called()


if __name__ == "__main__":
    unittest.main()
