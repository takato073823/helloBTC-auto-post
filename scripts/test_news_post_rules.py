"""ニュース投稿の恒常ルールに対する軽量テスト。"""
import unittest

from generator import (
    _build_imagen_prompt, is_duplicate_seo_topic, normalize_swell_html,
    prepend_lead_heading, resolve_logo_brand,
)


class NewsPostRuleTests(unittest.TestCase):
    def test_prepends_a_reworded_h2(self):
        title = "BitMart、取引所事業を段階的に終了へ　8月26日に全取引停止"
        content = "<p>リード文</p>"
        actual = prepend_lead_heading(
            content, title, "BitMartが取引所事業を段階的に終了、8月26日に全取引を停止"
        )
        self.assertTrue(actual.startswith("<h2>BitMartが取引所事業を段階的に終了、8月26日に全取引を停止</h2>"))
        self.assertIn(content, actual)

    def test_never_reuses_the_post_title_verbatim(self):
        title = "BitMart、取引所事業を段階的に終了へ　8月26日に全取引停止"
        actual = prepend_lead_heading("<p>リード文</p>", title, title)
        self.assertTrue(actual.startswith("<h2>BitMart、取引所事業を段階的に終了へ 8月26日に全取引停止を解説</h2>"))

    def test_known_brand_uses_its_fixed_official_domain(self):
        self.assertEqual(
            resolve_logo_brand("BitMart、取引所事業を段階的に終了へ", ["暗号資産"]),
            ("BitMart", "bitmart.com"),
        )

    def test_rejects_source_media_logo(self):
        self.assertEqual(
            resolve_logo_brand(
                "米国の暗号資産法案Clarity Actが正念場",
                ["米国議会", "暗号資産規制"],
                "CoinDesk",
                "coindesk.com",
            ),
            (None, None),
        )

    def test_project_logo_is_integrated_not_overlaid(self):
        prompt = _build_imagen_prompt("dark exchange server room", "Bitget", "bitget.com")
        self.assertIn("official Bitget brand mark", prompt)
        self.assertIn("part of the environment", prompt)
        self.assertIn("never on a standalone card", prompt)
        self.assertIn("between 8 and 15 percent of the frame", prompt)
        self.assertIn("not omitted", prompt)
        self.assertIn("never stretch, compress, skew, warp", prompt)

    def test_unapproved_logo_is_excluded_from_prompt(self):
        prompt = _build_imagen_prompt("United States Capitol dome", "CoinDesk", "coindesk.com")
        self.assertNotIn("CoinDesk", prompt)
        self.assertIn("No logos, media branding", prompt)
        self.assertIn("coin must be completely unbranded", prompt)

    def test_primary_subject_has_safe_composition_margin(self):
        prompt = _build_imagen_prompt("United States Capitol dome", None, None)
        self.assertIn("8 percent safe margin from all four edges", prompt)
        self.assertIn("Never crop a building dome", prompt)
        self.assertIn("35 to 60 percent of the frame", prompt)
        self.assertIn("final 1.91:1 crop", prompt)

    def test_closes_an_incomplete_swell_box(self):
        broken = '<div class="swell-block-capbox"><div class="cap_box_content"><p>要点'
        self.assertEqual(
            normalize_swell_html(broken),
            broken + "</p></div></div>",
        )

    def test_blocks_a_near_duplicate_seo_topic(self):
        self.assertTrue(is_duplicate_seo_topic(
            "Solana",
            "Solana（SOL）とは？2026年最新版｜高速・低コストの次世代ブロックチェーン完全ガイド",
            ["Solana（SOL）とは？2024年最新版・ブロックチェーン技術から購入方法まで完全ガイド"],
        ))


if __name__ == "__main__":
    unittest.main()
