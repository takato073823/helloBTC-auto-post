"""ニュース投稿の恒常ルールに対する軽量テスト。"""
import unittest
from pathlib import Path
from unittest.mock import patch

from generator import (
    AI_SEARCH_CONTENT_RULES, FEATURED_PHOTO_QUALITY_PROFILE, NEWS_ARTICLE_SCHEMA,
    _build_imagen_prompt, _generate_kiso_article, _generate_rich_article,
    _image_article_context, _image_review_passed,
    _image_text_review_prompt, append_source_attribution, is_duplicate_news_topic,
    is_duplicate_seo_topic, normalize_swell_html, prepend_direct_answer,
    prepend_lead_heading, resolve_logo_brand,
)
from scraper import get_latest_articles, source_name_from_url


class NewsPostRuleTests(unittest.TestCase):
    def test_news_schema_requires_a_direct_answer(self):
        self.assertIn("direct_answer", NEWS_ARTICLE_SCHEMA["required"])

    def test_ai_search_rules_require_answer_first_and_source_separation(self):
        self.assertIn("冒頭200文字以内", AI_SEARCH_CONTENT_RULES)
        self.assertIn("hellobtc-direct-answer", AI_SEARCH_CONTENT_RULES)
        self.assertIn("各H2・H3見出しの直後", AI_SEARCH_CONTENT_RULES)
        self.assertIn("一次情報", AI_SEARCH_CONTENT_RULES)
        self.assertIn("helloBTC編集部の整理", AI_SEARCH_CONTENT_RULES)

    def test_direct_answer_is_inserted_after_the_news_lead_heading(self):
        content = "<h2>ニュースの要点</h2><h3>確認できた事実</h3><p>本文</p>"
        actual = prepend_direct_answer(content, "日本の読者に重要なニュースの結論です。")
        self.assertLess(actual.index("<h2>"), actual.index("hellobtc-direct-answer"))
        self.assertLess(actual.index("hellobtc-direct-answer"), actual.index("<h3>"))
        self.assertIn("<strong>結論：</strong>日本の読者に重要なニュースの結論です。", actual)

    def test_direct_answer_is_safe_and_never_duplicated(self):
        actual = prepend_direct_answer("<p>本文</p>", "<b>結論</b> & 影響")
        self.assertIn("結論 &amp; 影響", actual)
        self.assertNotIn("<b>", actual)
        self.assertEqual(actual, prepend_direct_answer(actual, "別の回答"))
        self.assertEqual(1, actual.count('class="hellobtc-direct-answer"'))

    @patch("generator._generate_meta_json")
    @patch("generator._call_llm")
    def test_rich_seo_article_falls_back_to_answer_first(self, call_llm, generate_meta):
        call_llm.return_value = "<!-- wp:paragraph --><p>従来の導入文</p><!-- /wp:paragraph -->"
        generate_meta.return_value = {"excerpt": "記事の中心的な疑問に答える要約です。"}
        actual = _generate_rich_article("コラム")["content"]
        self.assertTrue(actual.startswith('<!-- wp:paragraph {"className":"hellobtc-direct-answer"} -->'))
        self.assertEqual(1, actual.count('class="hellobtc-direct-answer"'))

    @patch("generator._generate_meta_json")
    @patch("generator._call_llm")
    def test_basic_seo_article_falls_back_to_answer_first(self, call_llm, generate_meta):
        call_llm.return_value = "<!-- wp:paragraph --><p>従来の導入文</p><!-- /wp:paragraph -->"
        generate_meta.return_value = {"excerpt": "コインの特徴と注意点を先に答える要約です。"}
        actual = _generate_kiso_article()["content"]
        self.assertTrue(actual.startswith('<!-- wp:paragraph {"className":"hellobtc-direct-answer"} -->'))
        self.assertEqual(1, actual.count('class="hellobtc-direct-answer"'))

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
        self.assertIn(FEATURED_PHOTO_QUALITY_PROFILE, prompt)
        self.assertEqual(1, prompt.count(FEATURED_PHOTO_QUALITY_PROFILE))
        self.assertIn("photorealistic Reuters-style editorial news photography", prompt)
        self.assertIn("article-specific real-world scene", prompt)
        self.assertNotIn("Minimalist studio product photography", prompt)
        self.assertIn("Editorial relevance is mandatory", prompt)
        self.assertIn("Do not add generic crypto-news decoration", prompt)
        self.assertIn("government building, capitol, parliament, White House", prompt)
        self.assertIn("unless it is explicitly named in the opening brief", prompt)
        self.assertIn("not a reusable generic news scene", prompt)

    def test_repair_quality_profile_is_not_duplicated(self):
        base_prompt = f"hardware wallet on a desk, {FEATURED_PHOTO_QUALITY_PROFILE}"
        prompt = _build_imagen_prompt(base_prompt, None, None)
        self.assertEqual(1, prompt.count(FEATURED_PHOTO_QUALITY_PROFILE))

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
        self.assertIn("Every negative constraint in the visual brief is mandatory", prompt)
        self.assertIn("Reject any unrequested currency sign, crypto-token mark", prompt)
        self.assertIn("Bitcoin or Ethereum symbol is still a rejection", prompt)
        self.assertIn("pseudo-text", prompt)
        self.assertIn("garbled text", prompt)
        self.assertIn("unexpected Chinese", prompt)
        self.assertTrue(_image_review_passed("PASS"))
        self.assertFalse(_image_review_passed("REJECT: fake Chinese text"))

    def test_current_image_model_is_used_before_legacy_fallback(self):
        source = Path(__file__).with_name("generator.py").read_text(encoding="utf-8")
        self.assertLess(
            source.index('("gemini-3.1-flash-image", "gemini")'),
            source.index('("gemini-2.5-flash-image", "gemini")'),
        )
        self.assertNotIn('("imagen-4.0-fast-generate-001", "imagen")', source)
        self.assertIn("文字なしの代替アイキャッチを使用します", source)

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

    def test_blocks_a_near_duplicate_news_topic(self):
        existing = ["ビットコイン6万4,000ドル付近で推移、中東情勢が市場に影響"]
        self.assertTrue(is_duplicate_news_topic(
            "ビットコイン6万4,000ドルを維持、中東情勢で不安定な値動き",
            existing,
        ))
        self.assertFalse(is_duplicate_news_topic(
            "韓国がPolymarketへのアクセスを制限、規制対象を拡大",
            existing,
        ))

    def test_newsnow_records_the_real_publisher_name(self):
        self.assertEqual("CoinDesk", source_name_from_url("https://www.coindesk.com/markets/story"))
        self.assertEqual("Example News", source_name_from_url("https://example-news.com/story"))

    @patch("scraper.scrape_newsnow")
    @patch("scraper.fetch_from_rss")
    def test_latest_articles_prioritize_fresh_rss_sources(self, fetch_rss, scrape_newsnow):
        fetch_rss.return_value = [
            {"url": "https://source.example/older", "published_timestamp": 100},
            {"url": "https://source.example/newer", "published_timestamp": 200},
        ]
        actual = get_latest_articles(count=2)
        self.assertEqual(
            ["https://source.example/newer", "https://source.example/older"],
            [article["url"] for article in actual],
        )
        scrape_newsnow.assert_not_called()

    def test_auto_post_runs_three_quality_focused_slots_per_day(self):
        workflow = (
            Path(__file__).parents[1] / ".github" / "workflows" / "auto_post.yml"
        ).read_text(encoding="utf-8")
        self.assertEqual(3, workflow.count('- cron:'))

    def test_appends_a_safe_visible_source_link(self):
        actual = append_source_attribution(
            "<p>本文</p>", "CoinDesk & News", "https://example.com/news?id=1&lang=en"
        )
        self.assertIn('class="hellobtc-source"', actual)
        self.assertIn('href="https://example.com/news?id=1&amp;lang=en"', actual)
        self.assertIn("CoinDesk &amp; News", actual)
        self.assertIn('rel="noopener noreferrer"', actual)
        self.assertIn("helloBTCの編集方針", actual)
        self.assertIn("about-hellobtc-editorial-policy", actual)

    def test_does_not_append_an_invalid_source_url(self):
        self.assertEqual(
            append_source_attribution("<p>本文</p>", "source", "javascript:alert(1)"),
            "<p>本文</p>",
        )


if __name__ == "__main__":
    unittest.main()
