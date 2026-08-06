"""INUの実投稿ゲートをオフラインで検証する。"""

from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

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

    def test_reported_news_uses_native_article_card_without_uploaded_images(self):
        item = {
            "id": "reported_card",
            "topic_type": "reported_breaking_news",
            "visual_route": "reported_text_crop",
            "text": "米国の暗号資産規制に新しい動き。\n\n僕としては、実務ルールがいつ示されるかを確認したいです。\n\n#仮想通貨",
            "link_card_url": "https://www.nikkei.com/article/DGXZQOUB037270T00C26A8000000/",
        }
        text, media_path = validate_test_item(item)
        self.assertIsNone(media_path)
        self.assertNotIn("https://", text)
        published = []
        self.assertEqual(
            "789",
            publish_test_item(
                item,
                poster=lambda post_text, url: published.append((post_text, url)) or "789",
            ),
        )
        self.assertEqual([(text, item["link_card_url"])], published)

    def test_link_card_uses_link_card_poster_by_default(self):
        item = {
            "id": "reported_card_default",
            "topic_type": "reported_breaking_news",
            "visual_route": "reported_text_crop",
            "text": "暗号資産規制に新しい動き。\n\n僕としては、実務ルールがいつ示されるかを確認したいです。\n\n#仮想通貨",
            "link_card_url": "https://www.nikkei.com/article/DGXZQOUB037270T00C26A8000000/",
        }
        with patch("inu_live_post.post_link_card_tweet", return_value="790") as poster:
            self.assertEqual("790", publish_test_item(item))
        poster.assert_called_once_with(item["text"], item["link_card_url"])

    def test_link_card_rejects_non_reported_news_routes(self):
        item = {
            "id": "invalid_card",
            "topic_type": "etf_flow",
            "visual_route": "official_data_crop",
            "text": "ETFの資金流入を確認。\n\n僕としては、流入先の広がりを確認したいです。",
            "link_card_url": "https://example.com/official",
        }
        with self.assertRaisesRegex(ValueError, "主要メディア速報"):
            validate_test_item(item)

    def test_attention_visual_uses_one_verified_primary_image(self):
        artifacts = REPO_ROOT / "scripts" / "artifacts"
        # GitHub Actions のクリーンな実行環境には成果物ディレクトリがない。
        artifacts.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=artifacts) as directory:
            root = Path(directory)
            main = root / "main.png"
            Image.new("RGB", (800, 1000), "#112233").save(main)
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
            relative = lambda path: str(path.relative_to(REPO_ROOT))
            item = {
                "id": "two_images",
                "topic_type": "reported_breaking_news",
                "visual_route": "reported_text_crop",
                "text": "速報テスト\n\n僕は、事実の確認を続けます。\n\n#仮想通貨",
                "media_path": relative(main),
                "source_manifest": relative(main.with_suffix(".source.json")),
            }
            _, paths = validated_media_paths(item)
            self.assertEqual([main], paths)
            published = []
            self.assertEqual(
                "456",
                publish_test_item(item, poster=lambda _text, media: published.append(media) or "456"),
            )
            self.assertEqual(main, published[0])

    def test_market_chart_accepts_facts_but_rejects_personal_opinion(self):
        artifacts = REPO_ROOT / "scripts" / "artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=artifacts) as directory:
            root = Path(directory)
            chart = root / "btc.png"
            Image.new("RGB", (900, 1200), "white").save(chart)
            chart.with_suffix(".source.json").write_text(
                json.dumps(
                    {
                        "evidence_type": "market_service_screenshot",
                        "source_url": "https://www.tradingview.com/symbols/COINBASE-BTCUSD/",
                        "data_verified": True,
                        "capture_type": "service_screenshot",
                        "screenshot_provider": "TradingView",
                        "attribution_visible": True,
                        "white_background": True,
                    }
                ),
                encoding="utf-8",
            )
            relative = lambda path: str(path.relative_to(REPO_ROOT))
            item = {
                "id": "factual_market_chart",
                "topic_type": "crypto_market",
                "visual_route": "market_service_screenshot",
                "text": "BTCは直近24時間で上昇。\n\n現在値は直近24時間レンジの上限付近です。\n\n#ビットコイン",
                "media_path": relative(chart),
                "source_manifest": relative(chart.with_suffix(".source.json")),
            }
            text, paths = validated_media_paths(item)
            self.assertNotIn("僕", text)
            self.assertEqual([chart], paths)

            item["text"] = "BTCは直近24時間で上昇。\n\n僕は、ここからの上値を見ます。\n\n#ビットコイン"
            with self.assertRaisesRegex(ValueError, "個人の意見"):
                validated_media_paths(item)


if __name__ == "__main__":
    unittest.main()
