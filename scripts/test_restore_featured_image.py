"""既存アイキャッチ復元の入力検証テスト。"""

import unittest

from restore_featured_image import validate_restore_target


class RestoreFeaturedImageTests(unittest.TestCase):
    def test_accepts_media_from_same_wordpress_site(self):
        validate_restore_target(
            "https://hellobtc.jp",
            123,
            "https://hellobtc.jp/wp-content/uploads/2026/08/featured-123.jpg",
        )

    def test_rejects_media_from_another_site(self):
        with self.assertRaises(ValueError):
            validate_restore_target(
                "https://hellobtc.jp",
                123,
                "https://example.com/wp-content/uploads/featured-123.jpg",
            )

    def test_rejects_non_media_url(self):
        with self.assertRaises(ValueError):
            validate_restore_target("https://hellobtc.jp", 123, "https://hellobtc.jp/post/")


if __name__ == "__main__":
    unittest.main()
