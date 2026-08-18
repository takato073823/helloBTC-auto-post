import unittest
from pathlib import Path

from generator import TOPIC_HUB_LINKS, append_topic_hub_links
from repair_frontier_seo import (
    CANONICAL_SOLANA_ID,
    CANONICAL_SOLANA_SLUG,
    CONTENT_BUILDERS,
    PILLAR_REWRITES,
    SOLANA_DUPLICATES,
    record_old_slug,
)


class FrontierSeoContentTests(unittest.TestCase):
    def test_all_pillars_have_direct_answer_sources_and_internal_links(self):
        for post_id, builder in CONTENT_BUILDERS.items():
            with self.subTest(post_id=post_id):
                content = builder()
                self.assertIn("hellobtc-direct-answer", content)
                self.assertIn("hellobtc-official-sources", content)
                self.assertIn("hellobtc-related-guides", content)
                self.assertIn('rel="noopener noreferrer"', content)

    def test_tax_article_does_not_repeat_dangerous_old_advice(self):
        content = CONTENT_BUILDERS[430]()
        self.assertNotIn("損失の繰越控除を活用", content)
        self.assertNotIn("20万円まで非課税", content)
        self.assertIn("20万円まで一律非課税という意味ではない", content)
        self.assertIn("nta.go.jp", content)

    def test_earn_article_has_no_income_or_safety_guarantee(self):
        content = CONTENT_BUILDERS[129]()
        self.assertNotIn("稼ぎやすい", content)
        self.assertNotIn("初心者でも稼ぐことは可能", content)
        self.assertNotIn("必ず稼げ", content)
        self.assertIn("利益を保証しない", content)

    def test_duplicate_inventory_is_exact_and_excludes_canonical(self):
        ids = [post_id for post_id, _ in SOLANA_DUPLICATES]
        slugs = [slug for _, slug in SOLANA_DUPLICATES]
        self.assertEqual(18, len(ids))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertNotIn(CANONICAL_SOLANA_ID, ids)
        self.assertNotIn(CANONICAL_SOLANA_SLUG, slugs)

    def test_every_rewrite_has_an_evergreen_title_and_description(self):
        for fields in PILLAR_REWRITES.values():
            self.assertNotIn("2026年版", fields["title"])
            self.assertGreaterEqual(len(fields["excerpt"]), 50)

    def test_news_topic_hubs_have_three_descriptive_links_and_are_idempotent(self):
        self.assertTrue(TOPIC_HUB_LINKS)
        for cluster, links in TOPIC_HUB_LINKS.items():
            with self.subTest(cluster=cluster):
                self.assertEqual(3, len(links))
                first = append_topic_hub_links("<p>本文</p>", cluster)
                self.assertIn("HELLOBTC_TOPIC_HUB_START", first)
                self.assertEqual(first, append_topic_hub_links(first, cluster))

    def test_scaled_generic_seo_schedule_is_disabled(self):
        repo = Path(__file__).resolve().parent.parent
        workflow = (repo / ".github/workflows/seo_post.yml").read_text(encoding="utf-8")
        self.assertNotIn("schedule:", workflow)

    def test_bingx_is_reduced_to_two_runs_per_week(self):
        repo = Path(__file__).resolve().parent.parent
        workflow = (repo / ".github/workflows/bingx_seo.yml").read_text(encoding="utf-8")
        self.assertEqual(2, workflow.count('- cron: "0 0 * *'))

    def test_future_bingx_articles_do_not_emit_faqpage_schema(self):
        source = Path(__file__).with_name("bingx_seo.py").read_text(encoding="utf-8")
        live_block = source[source.index("# FAQは読者向け"):source.index("# 出典リンクボックス")]
        self.assertNotIn("build_faq_schema_html(faq)", live_block)


class FakeWordPress:
    def __init__(self):
        self.calls = []

    def update_post(self, post_id, **fields):
        self.calls.append((post_id, fields))
        return {"slug": fields["slug"]}


class RedirectTests(unittest.TestCase):
    def test_old_slug_rotation_returns_to_canonical(self):
        wp = FakeWordPress()
        record_old_slug(wp, 7, "旧スラッグ", "canonical")
        self.assertEqual(
            [(7, {"slug": "旧スラッグ"}), (7, {"slug": "canonical"})],
            wp.calls,
        )


if __name__ == "__main__":
    unittest.main()
