"""INUの実投稿ゲートをオフラインで検証する。"""

from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path

from PIL import Image

from inu_live_post import REPO_ROOT, load_test_item, publish_test_item, validate_test_item, validated_media_paths


class INULivePostTests(unittest.TestCase):
    def test_committed_test_item_is_valid(self):
        text, media_path = validate_test_item(load_test_item("btc_etf_flow_2026_08_03"))
        self.assertIn("僕は", text)
        self.assertTrue(media_path.is_file())

    def test_missing_tweet_id_is_a_hard_failure(self):
        item = load_test_item("btc_etf_flow_2026_08_03")
        with self.assertRaises(RuntimeError):
            publish_test_item(item, poster=lambda _text, _path: None)

    def test_success_returns_tweet_id(self):
        item = load_test_item("btc_etf_flow_2026_08_03")
        tweet_id = publish_test_item(item, poster=lambda _text, _path: "123")
        self.assertEqual("123", tweet_id)

    def test_attention_visual_requires_and_uploads_evidence_as_second_image(self):
        artifacts = REPO_ROOT / "scripts" / "artifacts"
        with tempfile.TemporaryDirectory(dir=artifacts) as directory:
            root = Path(directory)
            main = root / "main.png"
            evidence = root / "evidence.png"
            Image.new("RGB", (800, 1000), "#112233").save(main)
            Image.new("RGB", (1000, 600), "#ffffff").save(evidence)
            main.with_suffix(".source.json").write_text(
                json.dumps(
                    {
                        "source_url": "https://example.com/news",
                        "source_name": "Example",
                        "published_at": "2026-08-05",
                        "evidence_type": "source_news_image",
                        "capture_type": "source_hero_image",
                        "source_image_url": "https://example.com/main.jpg",
                        "visual_role": "attention_visual",
                        "facts_verified": True,
                    }
                ),
                encoding="utf-8",
            )
            evidence.with_suffix(".source.json").write_text(
                json.dumps(
                    {
                        "source_url": "https://example.com/news",
                        "source_name": "Example",
                        "published_at": "2026-08-05",
                        "evidence_type": "reported_text_crop",
                        "is_primary_source": False,
                    }
                ),
                encoding="utf-8",
            )
            relative = lambda path: str(path.relative_to(REPO_ROOT))
            item = {
                "id": "two_images",
                "topic_type": "reported_breaking_news",
                "visual_route": "reported_text_crop",
                "text": "速報テスト\n\n僕は、事実の確認を続けます。\n\n#仮想通貨",
                "media_path": relative(main),
                "source_manifest": relative(main.with_suffix(".source.json")),
                "additional_media": [
                    {
                        "media_path": relative(evidence),
                        "source_manifest": relative(evidence.with_suffix(".source.json")),
                    }
                ],
            }
            _, paths = validated_media_paths(item)
            self.assertEqual([main, evidence], paths)
            published = []
            self.assertEqual(
                "456",
                publish_test_item(item, poster=lambda _text, media: published.append(media) or "456"),
            )
            self.assertEqual([main, evidence], published[0])


if __name__ == "__main__":
    unittest.main()
