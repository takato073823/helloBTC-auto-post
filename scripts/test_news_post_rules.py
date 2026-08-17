"""ニュース投稿の恒常ルールに対する軽量テスト。"""
import unittest

from generator import (
    _build_imagen_prompt, _image_article_context, _image_review_passed,
    _image_text_review_prompt, append_source_attribution, is_duplicate_seo_topic,
    normalize_swell_html, prepend_lead_heading, resolve_logo_brand,
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

    def test_robinhood_and_hyperliquid_are_approved_logo_brands(self):
        self.assertEqual(
            resolve_logo_brand("Robinhoodが英国で仮想通貨取引開始", ["Robinhood"]),
            ("Robinhood", "robinhood.com"),
        )
        self.assertEqual(
            resolve_logo_brand("HyperliquidのRWA無期限先物が急拡大", ["HYPE"]),
            ("Hyperliquid", "hyperliquid.xyz"),
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
        prompt = _build_imagen_prompt("hardware wallet on a desk", None, None)
        self.assertIn("8 percent safe margin from all four edges", prompt)
        self.assertIn("Never crop, obscure, or cut off the primary subject", prompt)
        self.assertIn("35 to 60 percent of the frame", prompt)
        self.assertIn("final 1.91:1 crop", prompt)

    def test_featured_image_requires_article_specific_subject(self):
        prompt = _build_imagen_prompt("hardware wallet on a desk", None, None)
        self.assertIn("Editorial relevance is mandatory", prompt)
        self.assertIn("Do not add generic crypto-news decoration", prompt)
        self.assertIn("government building, capitol, parliament, White House", prompt)
        self.assertIn("unless it is explicitly named in the opening brief", prompt)
        self.assertIn("not a reusable generic news scene", prompt)

    def test_featured_image_does_not_send_japanese_article_copy_to_imagen(self):
        context = _image_article_context(
            "Robinhoodが英国で仮想通貨取引開始",
            "<p>英国で50銘柄超の取引を手数料無料で提供し、AI分析にも対応する。</p>",
        )
        self.assertNotIn("Robinhoodが英国で仮想通貨取引開始", context)
        self.assertNotIn("50銘柄超の取引を手数料無料", context)
        self.assertIn("Never recreate, quote, typeset, translate, or imitate", context)
        prompt = _build_imagen_prompt(
            "mobile crypto trading app", "Robinhood", "robinhood.com",
            "Robinhoodが英国で仮想通貨取引開始", "<p>英国向けの手数料無料取引。</p>",
        )
        self.assertNotIn("Robinhoodが英国で仮想通貨取引開始", prompt)
        self.assertNotIn("英国向けの手数料無料取引", prompt)

    def test_featured_image_allows_accurate_object_text_but_forbids_corruption(self):
        prompt = _build_imagen_prompt('SEC document headed "SEC"', None, None)
        self.assertIn("Never create a webpage, news article screenshot", prompt)
        self.assertIn("Physical documents, screens, and signs may appear", prompt)
        self.assertIn("one to three short, fully legible Japanese or English terms", prompt)
        self.assertIn("Spell every word correctly", prompt)
        self.assertIn("pseudo-text", prompt)
        self.assertIn("garbled text", prompt)
        self.assertIn("unreadable character clusters", prompt)
        self.assertNotIn("Absolutely no writing", prompt)

    def test_explicit_text_free_brief_keeps_every_surface_blank(self):
        prompt = _build_imagen_prompt("regulatory desk, no writing or symbols", None, None)
        self.assertIn("explicitly requires a text-free image", prompt)
        self.assertIn("Absolutely no writing or writing-like marks", prompt)
        self.assertIn("Keep every visible surface fully blank", prompt)
        self.assertIn("Do not add documents, labels, screens", prompt)
        self.assertIn("Minimalist studio product photography", prompt)
        self.assertIn("Do not infer or add contextual props", prompt)
        self.assertNotIn("Photojournalism, Reuters news photography style", prompt)
        self.assertNotIn("Natural writing on an article-specific physical item is allowed", prompt)

    def test_generated_image_review_accepts_readable_text_and_rejects_garbling(self):
        prompt = _image_text_review_prompt(None, 'SEC document headed "SEC"')
        self.assertIn('SEC document headed "SEC"', prompt)
        self.assertIn("correctly spelled", prompt)
        self.assertIn("Do not reject an image merely because it contains accurate", prompt)
        self.assertIn("pseudo-text", prompt)
        self.assertIn("garbled text", prompt)
        self.assertIn("unexpected Chinese", prompt)
        self.assertTrue(_image_review_passed("PASS"))
        self.assertFalse(_image_review_passed("REJECT: fake Chinese text"))

    def test_approved_logo_does_not_allow_other_media_branding(self):
        prompt = _image_text_review_prompt("Bitget")
        self.assertIn("single authentic Bitget brand mark", prompt)
        self.assertIn("Reject every other logo, publisher mark, or media brand", prompt)

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

    def test_appends_a_safe_visible_source_link(self):
        actual = append_source_attribution(
            "<p>本文</p>", "CoinDesk & News", "https://example.com/news?id=1&lang=en"
        )
        self.assertIn('class="hellobtc-source"', actual)
        self.assertIn('href="https://example.com/news?id=1&amp;lang=en"', actual)
        self.assertIn("CoinDesk &amp; News", actual)
        self.assertIn('rel="noopener noreferrer"', actual)

    def test_does_not_append_an_invalid_source_url(self):
        self.assertEqual(
            append_source_attribution("<p>本文</p>", "source", "javascript:alert(1)"),
            "<p>本文</p>",
        )


if __name__ == "__main__":
    unittest.main()
