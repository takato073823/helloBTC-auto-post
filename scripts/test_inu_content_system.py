"""INUの口調・画像・予算ルールをオフラインで検証する。"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from inu_budget import (
    estimate_grok_editorial_monthly_cost_yen,
    estimate_monthly_cost_yen,
    estimate_total_automation_cost_yen,
)
from inu_content_types import CONTENT_TYPES, get_content_policy
from inu_persona import lint_voice
from inu_post import compose_post, validate_post
from inu_risk_visual import apply_risk_alert_overlay, build_risk_alert_prompt
from inu_source_capture import SourceCaptureSpec, crop_source_image, validate_capture_spec
from inu_visual import build_gpt_image_prompt, select_visual_route


class INUContentSystemTests(unittest.TestCase):
    def test_voice_is_consistent(self):
        good = "この数字は資金の偏りを示しています。流入先の内訳は、公式集計で確認できます。"
        self.assertEqual([], lint_voice(good))
        self.assertIn("個人の見解", " ".join(lint_voice("僕の見方では、次を確認します。")))
        self.assertTrue(lint_voice("俺は爆上げ確定だと思うワン"))

    def test_no_basic_knowledge_route(self):
        self.assertEqual("reject", select_visual_route("basic_knowledge").route)

    def test_evidence_routes_are_not_synthetic(self):
        self.assertEqual(
            "market_service_screenshot",
            select_visual_route("crypto_market").route,
        )
        self.assertEqual("official_data_crop", select_visual_route("onchain").route)
        self.assertEqual("official_text_crop", select_visual_route("breaking_news").route)
        self.assertEqual("official_text_crop", select_visual_route("security_incident").route)
        self.assertEqual(
            "manual_quote_with_source_media",
            select_visual_route("x_reaction").route,
        )
        self.assertFalse(select_visual_route("etf_flow").gpt_image_allowed)

    def test_all_requested_timely_types_have_an_image_route(self):
        required = {
            "market_microstructure",
            "etf_flow",
            "institutional_flow",
            "whale_treasury",
            "earnings",
            "supply_event",
            "adoption_kpi",
            "policy_household",
            "macro_event",
            "developing_story",
            "historical_milestone",
            "cross_asset",
            "timeline_explainer",
            "security_incident",
            "risk_alert",
            "market_meme",
            "campaign",
        }
        self.assertTrue(required.issubset(CONTENT_TYPES))
        for key in required:
            self.assertNotIn(get_content_policy(key).visual_route, {"", "none", "text_only"})

    def test_risky_types_are_never_fully_automatic(self):
        for key in {
            "translation_quote",
            "x_reaction",
            "public_figure_statement",
            "security_incident",
            "risk_alert",
            "market_meme",
            "campaign",
            "ai_comparison",
        }:
            self.assertEqual("manual", get_content_policy(key).review_mode)

    def test_translation_requires_source_media(self):
        policy = get_content_policy("translation_quote")
        self.assertTrue(policy.requires_source_media)
        self.assertEqual("manual_quote_with_source_media", policy.visual_route)

    def test_inu_publish_modules_do_not_create_text_only_posts(self):
        scripts_dir = Path(__file__).resolve().parent
        publish_modules = [
            "inu_live_post.py",
            "inu_hourly_dispatcher.py",
            "x_price_chart_post.py",
        ]
        for name in publish_modules:
            source = (scripts_dir / name).read_text(encoding="utf-8")
            self.assertNotIn(".create_tweet(", source, name)
            self.assertIn("post_info_tweet", source if name != "inu_hourly_dispatcher.py" else (scripts_dir / "inu_live_post.py").read_text(encoding="utf-8"))

    def test_market_posters_use_service_screenshots_not_custom_charts(self):
        scripts_dir = Path(__file__).resolve().parent
        for name in ("x_price_chart_post.py", "inu_breaking_market.py"):
            source = (scripts_dir / name).read_text(encoding="utf-8")
            self.assertIn("capture_tradingview_screenshot", source)
            self.assertNotIn("matplotlib", source)
            self.assertNotIn("fig.savefig", source)

    def test_timeline_is_only_used_when_needed(self):
        decision = select_visual_route("security_incident", needs_timeline=True)
        self.assertEqual("gpt_timeline", decision.route)
        self.assertTrue(decision.gpt_image_allowed)

    def test_source_capture_rejects_non_primary_or_full_page(self):
        with self.assertRaises(ValueError):
            validate_capture_spec(
                SourceCaptureSpec(
                    source_url="https://example.com/news",
                    source_name="転載メディア",
                    published_at="2026-08-04",
                    evidence_type="official_text_crop",
                    selector="body",
                    is_primary_source=False,
                )
            )

    def test_source_crop_keeps_manifest(self):
        spec = SourceCaptureSpec(
            source_url="https://example.com/official-release",
            source_name="Example公式",
            published_at="2026-08-04",
            evidence_type="official_text_crop",
            selector="section.release-body",
        )
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            output = Path(directory) / "evidence.png"
            Image.new("RGB", (1000, 800), "white").save(source)
            crop_source_image(spec, source, output, (100, 100, 900, 600))
            self.assertTrue(output.exists())
            self.assertTrue(output.with_suffix(".source.json").exists())

    def test_gpt_prompt_requires_original_visual(self):
        prompt = build_gpt_image_prompt(
            visual_type="gpt_timeline",
            headline="取引所障害の時系列",
            key_points=["公式が障害を発表", "出金を一時停止", "復旧を確認"],
        )
        self.assertIn("original composition", prompt)
        self.assertIn("Do not copy", prompt)
        self.assertIn("no invented prices", prompt)
        self.assertNotIn("Create it in SOU style", prompt)

    def test_risk_alert_is_manual_primary_source_only(self):
        policy = get_content_policy("risk_alert")
        self.assertEqual("gpt_risk_alert", policy.visual_route)
        self.assertEqual("manual", policy.review_mode)
        self.assertTrue(policy.requires_primary_source)
        with self.assertRaises(ValueError):
            build_gpt_image_prompt(
                visual_type="gpt_risk_alert",
                headline="専用経路のみ",
                key_points=["一次情報"],
            )

    def test_risk_alert_prompt_reserves_headline_and_forbids_brands(self):
        prompt = build_risk_alert_prompt("hack")
        self.assertIn("leave a dark, uncluttered area", prompt)
        self.assertIn("no text", prompt)
        self.assertIn("no text, letters, numbers", prompt)
        self.assertIn("logos", prompt)
        self.assertIn("Do not copy", prompt)

    def test_risk_alert_rejects_unknown_theme(self):
        with self.assertRaises(ValueError):
            build_risk_alert_prompt("unknown")

    def test_risk_alert_overlay_is_landscape_png(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            output = Path(directory) / "output.png"
            Image.new("RGB", (900, 600), "#A0006A").save(source)
            apply_risk_alert_overlay(
                source,
                output,
                headline="全資産を失う前に\n見直すこと。",
            )
            with Image.open(output) as image:
                self.assertEqual("PNG", image.format)
                self.assertEqual((1536, 864), image.size)

    def test_risk_alert_rejects_invalid_headline_length(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.png"
            output = Path(directory) / "output.png"
            Image.new("RGB", (900, 600), "#A0006A").save(source)
            with self.assertRaises(ValueError):
                apply_risk_alert_overlay(source, output, headline="")
            with self.assertRaises(ValueError):
                apply_risk_alert_overlay(source, output, headline="長" * 43)

    def test_post_uses_natural_paragraphs_without_personal_opinion(self):
        text = compose_post(
            hook="⚡️ 米国市場で半導体株に資金が集中",
            facts=["複数のETFで過去最大級の流入が確認されました。"],
            opinion="僕の見方では、次は資金流入が他の分野へ広がるかを確認したいです。",
            tags=["米国株"],
        )
        validate_post(text)
        self.assertNotIn("・", text)
        self.assertIn("\n\n複数のETF", text)
        self.assertNotIn("僕の見方では", text)

    def test_hourly_medium_images_stay_under_budget_estimate(self):
        self.assertLessEqual(estimate_monthly_cost_yen(24), 10000)

    def test_hourly_grok_and_mixed_images_stay_under_budget_estimate(self):
        self.assertGreater(estimate_grok_editorial_monthly_cost_yen(24), 0)
        self.assertLessEqual(estimate_total_automation_cost_yen(), 10000)


if __name__ == "__main__":
    unittest.main()
