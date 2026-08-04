"""COLDCARDセキュリティ速報の公開前検証。"""

import unittest

from coldcard_security_news import (
    BLOCK_ANALYSIS,
    OFFICIAL_ADVISORY,
    OFFICIAL_DOWNLOADS,
    OFFICIAL_TECHNICAL_REPORT,
    coldcard_article,
    find_existing_article,
    validate_article,
)


class ColdcardSecurityNewsTests(unittest.TestCase):
    def setUp(self):
        self.article = coldcard_article()

    def test_article_passes_safety_validation(self):
        validate_article(self.article)

    def test_primary_and_independent_sources_are_linked(self):
        content = self.article["content"]
        for source in [
            OFFICIAL_ADVISORY,
            OFFICIAL_TECHNICAL_REPORT,
            OFFICIAL_DOWNLOADS,
            BLOCK_ANALYSIS,
        ]:
            self.assertIn(f'href="{source}"', content)

    def test_affected_versions_and_migration_warning_are_present(self):
        content = self.article["content"]
        for fact in ["4.0.1〜4.1.9", "4.2.0以降", "5.6.0以降", "1.5.0Q以降"]:
            self.assertIn(fact, content)
        self.assertIn("更新するだけでは", content)
        self.assertIn("新しいシード", content)

    def test_remote_compromise_is_distinguished_from_rng_issue(self):
        content = self.article["content"]
        self.assertIn("インターネット経由でCOLDCARD本体へ侵入", content)
        self.assertIn("タイプの攻撃ではない", content)

    def test_no_unverified_loss_amount_is_claimed(self):
        content = self.article["content"]
        self.assertNotRegex(content, r"\d+(?:\.\d+)?\s*BTC")
        self.assertIn("被害総額や被害ウォレット数を公式には確定していない", content)

    def test_featured_image_is_unbranded(self):
        self.assertEqual(self.article["logo_brand"], "")
        self.assertEqual(self.article["logo_domain"], "")
        self.assertIn("unbranded", self.article["image_prompt"])

    def test_existing_slug_blocks_duplicate_publication(self):
        class FakeWordPress:
            def get_posts_by_slugs(self, slugs, status):
                self.request = (slugs, status)
                return [{"id": 42, "slug": slugs[0]}]

        wp = FakeWordPress()
        existing = find_existing_article(wp, self.article["slug"])
        self.assertEqual(existing["id"], 42)
        self.assertEqual(wp.request, ([self.article["slug"]], "any"))


if __name__ == "__main__":
    unittest.main()
