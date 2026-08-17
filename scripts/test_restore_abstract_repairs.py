"""抽象化された修復画像の復元データを検証する。"""

import unittest
from pathlib import Path

from restore_abstract_repairs import load_restore_map, media_slug_from_url


class RestoreAbstractRepairsTests(unittest.TestCase):
    def test_restore_map_has_unique_article_entries(self):
        mapping = load_restore_map(Path(__file__).with_name("featured_image_pre_repair_map.tsv"))
        self.assertEqual(52, len(mapping))
        self.assertEqual(
            "https://hellobtc.jp/wp-content/uploads/2026/08/featured-1786938232.jpg",
            mapping["crypto-fundamentals-over-market-cap"],
        )

    def test_extracts_wordpress_media_slug(self):
        self.assertEqual(
            "featured-1786938232",
            media_slug_from_url(
                "https://hellobtc.jp/wp-content/uploads/2026/08/featured-1786938232.jpg"
            ),
        )


if __name__ == "__main__":
    unittest.main()
