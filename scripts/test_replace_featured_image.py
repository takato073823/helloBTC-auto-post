"""既存記事のアイキャッチ差し替えで承認済み画像を優先することを確認する。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from replace_featured_image import load_replacement_image


class ReplaceFeaturedImageTests(unittest.TestCase):
    def test_uses_approved_image_file_without_ai_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            asset = Path(directory) / "approved.png"
            asset.write_bytes(b"approved-image")
            with patch("replace_featured_image.generate_featured_image") as generate:
                image = load_replacement_image(
                    str(asset), image_prompt="ignored", tags=[], logo_brand=None,
                    logo_domain=None, article_title="タイトル", article_content="本文",
                )
            self.assertEqual(b"approved-image", image)
            generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
