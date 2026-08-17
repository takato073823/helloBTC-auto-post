"""既存アイキャッチ監査の対象判定テスト。"""

import unittest

from audit_featured_images import is_legacy_generated_image


class AuditFeaturedImagesTests(unittest.TestCase):
    def test_selects_only_legacy_generated_featured_images(self):
        self.assertTrue(is_legacy_generated_image("https://example.com/featured-12345.jpg"))
        self.assertTrue(is_legacy_generated_image("https://example.com/featured-12345.png?cache=1"))
        self.assertFalse(is_legacy_generated_image("https://example.com/featured-replaced-post-12345.jpg"))
        self.assertFalse(is_legacy_generated_image("https://example.com/bingx-guide-img1.jpg"))


if __name__ == "__main__":
    unittest.main()
