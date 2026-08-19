"""INUの独立情報投稿を、ネットワークと秘密情報なしで検証する。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


try:
    import tweepy  # noqa: F401
except ModuleNotFoundError:
    fake_tweepy = types.ModuleType("tweepy")
    fake_tweepy.Client = type("Client", (), {})
    fake_tweepy.API = type("API", (), {})
    fake_tweepy.OAuth1UserHandler = type("OAuth1UserHandler", (), {})
    sys.modules["tweepy"] = fake_tweepy

import x_info_poster
import x_poster


class FakeMediaAPI:
    def media_upload(self, filename):
        self.filename = filename
        self.filenames = getattr(self, "filenames", []) + [filename]
        return types.SimpleNamespace(media_id_string=f"media-{len(self.filenames)}")


class FakeClient:
    def create_tweet(self, **kwargs):
        self.kwargs = kwargs
        return types.SimpleNamespace(data={"id": "tweet-456"})


class XInfoPosterTests(unittest.TestCase):
    def test_catalog_is_valid_and_has_at_least_one_day_of_hourly_content(self):
        posts = x_info_poster.load_catalog()
        self.assertGreaterEqual(len(posts), 24)
        self.assertEqual(len(posts), len({item["id"] for item in posts}))
        for item in posts:
            self.assertLessEqual(
                x_info_poster.weighted_length(x_info_poster.build_info_tweet(item)),
                x_info_poster.MAX_WEIGHTED_LENGTH,
            )

    def test_workflow_runs_every_hour(self):
        workflow_path = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "x_info_posts.yml"
        )
        if not workflow_path.exists():
            self.skipTest("毎時運用ワークフローはテスト投稿承認後に公開する")
        workflow = workflow_path.read_text(encoding="utf-8")
        self.assertIn('- cron: "0 * * * *"', workflow)
        self.assertNotIn('cron: "0 23 * * *"', workflow)

    def test_service_domain_is_neutralized_and_no_url_is_added(self):
        item = {
            "id": "crypto_com_test",
            "category": "取引所",
            "title": "Crypto.comの表示を確認",
            "bullets": ["Crypto.comはサービス名", "記事URLは投稿しない"],
            "tags": ["Crypto.com"],
            "source": "テスト",
        }
        text = x_info_poster.build_info_tweet(item)
        self.assertIn("Crypto(.)com", text)
        self.assertNotIn("https://", text)

    def test_blocking_claim_is_rejected(self):
        item = {
            "id": "blocked_claim",
            "category": "相場",
            "title": "絶対に上がる銘柄",
            "bullets": ["要点A", "要点B"],
            "tags": [],
            "source": "テスト",
        }
        with self.assertRaises(ValueError):
            x_info_poster.validate_item(item)

    def test_same_slot_is_idempotent_and_rotation_does_not_repeat(self):
        posts = x_info_poster.load_catalog()
        state = x_info_poster.load_state(Path("/missing/state.json"))
        slot = "2026-08-04-08"
        first = x_info_poster.select_item(posts, state, slot)
        updated = x_info_poster.mark_posted(state, first, slot, "1", posts)
        self.assertIsNone(x_info_poster.select_item(posts, updated, slot))
        second = x_info_poster.select_item(posts, updated, "2026-08-04-11")
        self.assertNotEqual(first["id"], second["id"])

    def test_card_is_x_image_size(self):
        item = x_info_poster.load_catalog()[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = x_info_poster.render_card(item, Path(tmp) / "card.png")
            with Image.open(path) as image:
                self.assertEqual(image.size, (1600, 900))

    def test_dry_run_does_not_update_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            args = argparse.Namespace(
                catalog=str(x_info_poster.CATALOG_PATH),
                state=str(state),
                slot="2026-08-04-08",
                post_id=None,
                live=False,
            )
            self.assertEqual(x_info_poster.run(args), 0)
            self.assertFalse(state.exists())

    def test_media_post_uses_same_oauth_secrets_and_image(self):
        media_api = FakeMediaAPI()
        client = FakeClient()
        secrets = {
            "X_API_KEY": "test",
            "X_API_KEY_SECRET": "test",
            "X_ACCESS_TOKEN": "test",
            "X_ACCESS_TOKEN_SECRET": "test",
        }
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "card.png"
            image.write_bytes(b"png")
            with patch.dict(os.environ, secrets), patch.object(
                x_poster, "_get_oauth1_api", return_value=media_api
            ), patch.object(x_poster, "_get_client", return_value=client):
                result = x_poster.post_info_tweet("Crypto.comの情報", image)
        self.assertEqual(result, "tweet-456")
        self.assertEqual(client.kwargs["text"], "Crypto(.)comの情報")
        self.assertEqual(client.kwargs["media_ids"], ["media-1"])

    def test_media_post_can_attach_primary_visual_and_evidence(self):
        media_api = FakeMediaAPI()
        client = FakeClient()
        secrets = {
            "X_API_KEY": "test",
            "X_API_KEY_SECRET": "test",
            "X_ACCESS_TOKEN": "test",
            "X_ACCESS_TOKEN_SECRET": "test",
        }
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "main.png"
            second = Path(tmp) / "evidence.png"
            first.write_bytes(b"png")
            second.write_bytes(b"png")
            with patch.dict(os.environ, secrets), patch.object(
                x_poster, "_get_oauth1_api", return_value=media_api
            ), patch.object(x_poster, "_get_client", return_value=client):
                result = x_poster.post_info_tweet("テスト", [first, second])
        self.assertEqual(result, "tweet-456")
        self.assertEqual(client.kwargs["media_ids"], ["media-1", "media-2"])

    def test_failed_media_upload_never_falls_back_to_text_only(self):
        client = FakeClient()
        failing_api = types.SimpleNamespace(
            media_upload=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("upload failed"))
        )
        secrets = {
            "X_API_KEY": "test",
            "X_API_KEY_SECRET": "test",
            "X_ACCESS_TOKEN": "test",
            "X_ACCESS_TOKEN_SECRET": "test",
        }
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "card.png"
            image.write_bytes(b"png")
            with patch.dict(os.environ, secrets), patch.object(
                x_poster, "_get_oauth1_api", return_value=failing_api
            ), patch.object(x_poster, "_get_client", return_value=client):
                result = x_poster.post_info_tweet("テスト", image)
        self.assertIsNone(result)
        self.assertFalse(hasattr(client, "kwargs"))

    def test_x_credit_depletion_is_propagated_to_cost_guard(self):
        error = RuntimeError("credits depleted")
        error.response = types.SimpleNamespace(status_code=402)
        failing_api = types.SimpleNamespace(
            media_upload=lambda **_kwargs: (_ for _ in ()).throw(error)
        )
        secrets = {
            "X_API_KEY": "test",
            "X_API_KEY_SECRET": "test",
            "X_ACCESS_TOKEN": "test",
            "X_ACCESS_TOKEN_SECRET": "test",
        }
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "card.png"
            image.write_bytes(b"png")
            with patch.dict(os.environ, secrets), patch.object(
                x_poster, "_get_oauth1_api", return_value=failing_api
            ):
                with self.assertRaises(x_poster.XCreditsDepletedError):
                    x_poster.post_info_tweet("テスト", image)

    def test_link_card_keeps_article_url_unchanged_and_uploads_no_media(self):
        client = FakeClient()
        secrets = {
            "X_API_KEY": "test",
            "X_API_KEY_SECRET": "test",
            "X_ACCESS_TOKEN": "test",
            "X_ACCESS_TOKEN_SECRET": "test",
        }
        article_url = "https://crypto.com/news/market-update"
        with patch.dict(os.environ, secrets), patch.object(
            x_poster, "_get_client", return_value=client
        ), patch.object(x_poster, "_get_oauth1_api") as media_api:
            result = x_poster.post_link_card_tweet("Crypto.comの内容", article_url)
        self.assertEqual("tweet-456", result)
        self.assertEqual(f"Crypto(.)comの内容\n\n{article_url}", client.kwargs["text"])
        media_api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
