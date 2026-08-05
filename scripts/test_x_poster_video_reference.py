"""Xネイティブ動画参照投稿の送信内容を検証する。"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import x_poster


class XPosterVideoReferenceTests(unittest.TestCase):
    def test_native_video_reference_is_sent_without_reupload(self):
        client = SimpleNamespace(
            create_tweet=lambda **kwargs: SimpleNamespace(data={"id": "456"}, kwargs=kwargs)
        )
        calls = []

        def create_tweet(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(data={"id": "456"})

        client.create_tweet = create_tweet
        required = {
            "X_API_KEY": "key",
            "X_API_KEY_SECRET": "secret",
            "X_ACCESS_TOKEN": "token",
            "X_ACCESS_TOKEN_SECRET": "token-secret",
        }
        with patch.dict(os.environ, required, clear=False), patch.object(x_poster, "_get_client", return_value=client):
            self.assertEqual("456", x_poster.post_video_reference_tweet("動画を確認", "2084816282902466746"))
        self.assertEqual(
            "動画を確認\n\nhttps://x.com/i/status/2084816282902466746/video/1",
            calls[0]["text"],
        )
        self.assertNotIn("media_ids", calls[0])

    def test_invalid_source_id_is_not_posted(self):
        self.assertIsNone(x_poster.post_video_reference_tweet("動画を確認", "bad-id"))


if __name__ == "__main__":
    unittest.main()
