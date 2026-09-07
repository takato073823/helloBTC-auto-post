"""記事固有アイキャッチを公開条件にするための回帰テスト。"""

import json
import unittest
from pathlib import Path

from PIL import Image

from generator import (
    _image_text_review_prompt,
    _select_editorial_color_direction,
    _select_editorial_lighting_direction,
    resolve_logo_brand,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


class FeaturedImagePolicyTests(unittest.TestCase):
    def test_review_checks_the_verified_article_title(self):
        prompt = _image_text_review_prompt(
            "Hyperliquid", "a custody console", "Hyperliquid ETFの機関保有"
        )
        self.assertIn("Hyperliquid ETFの機関保有", prompt)
        self.assertIn("primary visual subject must directly depict", prompt)

    def test_title_is_used_for_review_but_not_sent_to_image_generator(self):
        source = (REPO_ROOT / "scripts" / "generator.py").read_text(encoding="utf-8")
        self.assertIn("article_title", source)
        self.assertIn("Never recreate, quote, typeset, translate", source)

    def test_known_brand_can_be_resolved_from_verified_generator_metadata(self):
        self.assertEqual(
            ("Fidelity", "fidelity.com"),
            resolve_logo_brand("フィデリティの分析", [], "Fidelity", "fidelity.com"),
        )

    def test_color_direction_varies_by_article_subject(self):
        security = _select_editorial_color_direction("bridge hack drains tokens")
        regulation = _select_editorial_color_direction("SEC filing amendment")
        ai = _select_editorial_color_direction("AI training data model")
        self.assertIn("warning-red", security)
        self.assertIn("forest green", regulation)
        self.assertIn("violet", ai)
        self.assertNotEqual(security, regulation)
        self.assertNotEqual(regulation, ai)

    def test_featured_images_no_longer_force_cool_blue_tones(self):
        source = (REPO_ROOT / "scripts" / "generator.py").read_text(encoding="utf-8")
        self.assertNotIn('"Muted color grading, slightly desaturated, cool tones. "', source)
        self.assertIn("Do not default to a blue/cyan crypto aesthetic", source)

    def test_lighting_direction_uses_light_scenes_when_the_topic_allows_it(self):
        etf = _select_editorial_lighting_direction("Bitcoin ETF institutional inflow")
        ai = _select_editorial_lighting_direction("AI training data model")
        breach = _select_editorial_lighting_direction("bridge hack drains tokens")
        self.assertIn("bright natural window light", etf)
        self.assertIn("bright natural window light", ai)
        self.assertIn("primary subject remains clearly lit", breach)

    def test_featured_images_no_longer_default_to_dark_backgrounds(self):
        source = (REPO_ROOT / "scripts" / "generator.py").read_text(encoding="utf-8")
        self.assertNotIn('"Photorealistic scene, dramatic lighting, dark background.', source)
        self.assertIn("never default to a dark crypto aesthetic", source)

    def test_no_template_fallback_is_used_for_failed_featured_images(self):
        source = (REPO_ROOT / "scripts" / "generator.py").read_text(encoding="utf-8")
        self.assertIn("FeaturedImageGenerationError", source)
        self.assertNotIn("return create_editorial_image(fallback_seed)", source)

    def test_failed_featured_image_stops_news_publication(self):
        source = (REPO_ROOT / "scripts" / "main.py").read_text(encoding="utf-8")
        self.assertIn("アイキャッチ条件を満たせないため公開を保留", source)
        self.assertNotIn("画像生成/アップロード失敗（記事投稿は続行）", source)

    def test_replacement_manifest_contains_every_affected_article(self):
        manifest_path = REPO_ROOT / "assets" / "featured" / "2026-08-25-onward" / "manifest.json"
        rows = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(14, len(rows))
        for row in rows:
            image_path = REPO_ROOT / row["image_file"]
            self.assertTrue(image_path.is_file())
            with Image.open(image_path) as image:
                self.assertGreaterEqual(image.width / image.height, 1.7)


if __name__ == "__main__":
    unittest.main()
