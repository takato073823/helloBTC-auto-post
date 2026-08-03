"""ニュース投稿の恒常ルールに対する軽量テスト。"""
import unittest

from generator import (
    is_duplicate_seo_topic, normalize_swell_html, prepend_lead_heading, resolve_logo_brand,
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
