"""Xのステーブルコイン報酬検討記事の公開前検証。"""

import unittest
from unittest.mock import patch

from PIL import Image

from x_stablecoin_news import (
    CIRCLE_USDC_OFFICIAL,
    COINDESK_REPORT,
    FEATURED_IMAGE,
    X_CREATORS_POST,
    X_MONEY_OFFICIAL,
    X_REVENUE_HELP,
    find_existing_article,
    post_article_to_x,
    validate_article,
    x_stablecoin_article,
)


class XStablecoinNewsTests(unittest.TestCase):
    def setUp(self):
        self.article = x_stablecoin_article()

    def test_article_passes_prepublication_validation(self):
        validate_article(self.article)

    def test_report_and_primary_sources_are_linked(self):
        content = self.article["content"]
        for source in [
            COINDESK_REPORT,
            X_CREATORS_POST,
            X_REVENUE_HELP,
            X_MONEY_OFFICIAL,
            CIRCLE_USDC_OFFICIAL,
        ]:
            self.assertIn(f'href="{source}"', content)

    def test_unconfirmed_status_is_clear_in_title_and_body(self):
        self.assertIn("検討か", self.article["title"])
        self.assertIn("と報道", self.article["title"])
        self.assertIn("Xはステーブルコイン導入を公式には発表していない", self.article["content"])
        self.assertIn("現時点で未確定", self.article["content"])
        self.assertNotIn("XはUSDCを採用した", self.article["content"])

    def test_featured_image_is_valid_wordpress_size(self):
        with Image.open(FEATURED_IMAGE) as image:
            self.assertEqual("JPEG", image.format)
            self.assertEqual((1200, 630), image.size)

    def test_existing_slug_lookup_fails_closed_through_request(self):
        class FakeWordPress:
            def _request(self, method, endpoint, **kwargs):
                self.call = (method, endpoint, kwargs)
                return [{"id": 2534, "link": "https://hellobtc.jp/article/"}]

        wp = FakeWordPress()
        existing = find_existing_article(wp)
        self.assertEqual(2534, existing["id"])
        self.assertEqual("GET", wp.call[0])
        self.assertEqual("any", wp.call[2]["params"]["status"])

    @patch("x_stablecoin_news.post_tweet", return_value=None)
    def test_x_post_failure_is_not_silently_accepted(self, _post_tweet):
        with self.assertRaisesRegex(RuntimeError, "X投稿を完了できません"):
            post_article_to_x(self.article, "https://hellobtc.jp/article/")


if __name__ == "__main__":
    unittest.main()
