"""INUの引用投稿を、秘密情報とネットワークなしで検証する。"""

from __future__ import annotations

import argparse
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import x_quote_post


class FakeClient:
    def create_tweet(self, **kwargs):
        self.kwargs = kwargs
        return types.SimpleNamespace(data={"id": "quote-123"})


class FailingClient:
    def __init__(self):
        self.call_count = 0

    def create_tweet(self, **kwargs):
        self.call_count += 1
        raise RuntimeError("quote not permitted")


class XQuotePostTests(unittest.TestCase):
    def test_default_commentary_uses_boku_and_passes_validation(self):
        text = x_quote_post.validate_quote(
            x_quote_post.COMMENTARY,
            x_quote_post.DEFAULT_SOURCE_ID,
            x_quote_post.DEFAULT_KEY,
        )
        self.assertIn("僕", text)
        self.assertNotIn("INUは", text)
        self.assertNotIn("http", text)

    def test_inu_third_person_is_rejected(self):
        with self.assertRaises(ValueError):
            x_quote_post.validate_quote(
                "INUはこの数字に注目しています。僕も確認します。",
                x_quote_post.DEFAULT_SOURCE_ID,
                x_quote_post.DEFAULT_KEY,
            )

    def test_dry_run_never_calls_x_or_writes_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            args = argparse.Namespace(
                live=False,
                source_id=x_quote_post.DEFAULT_SOURCE_ID,
                key=x_quote_post.DEFAULT_KEY,
                state=str(state),
                text=x_quote_post.COMMENTARY,
            )
            with patch.object(x_quote_post, "post_quote") as post:
                self.assertEqual(x_quote_post.run(args), 0)
            post.assert_not_called()
            self.assertFalse(state.exists())

    def test_live_post_uses_native_quote_id_and_records_once(self):
        client = FakeClient()
        secrets = {
            "X_API_KEY": "test",
            "X_API_KEY_SECRET": "test",
            "X_ACCESS_TOKEN": "test",
            "X_ACCESS_TOKEN_SECRET": "test",
        }
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            args = argparse.Namespace(
                live=True,
                source_id=x_quote_post.DEFAULT_SOURCE_ID,
                key=x_quote_post.DEFAULT_KEY,
                state=str(state),
                text=x_quote_post.COMMENTARY,
            )
            with patch.dict(os.environ, secrets), patch.object(
                x_quote_post, "_get_client", return_value=client
            ):
                self.assertEqual(x_quote_post.run(args), 0)
                self.assertEqual(x_quote_post.run(args), 0)

            self.assertEqual(client.kwargs["quote_tweet_id"], x_quote_post.DEFAULT_SOURCE_ID)
            self.assertEqual(client.kwargs["text"], x_quote_post.COMMENTARY)
            self.assertIn('"tweet_id": "quote-123"', state.read_text(encoding="utf-8"))

    def test_api_failure_does_not_write_state_or_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state.json"
            args = argparse.Namespace(
                live=True,
                source_id=x_quote_post.DEFAULT_SOURCE_ID,
                key=x_quote_post.DEFAULT_KEY,
                state=str(state),
                text=x_quote_post.COMMENTARY,
            )
            with patch.object(x_quote_post, "post_quote", return_value=None) as post:
                self.assertEqual(x_quote_post.run(args), 1)
            self.assertEqual(post.call_count, 1)
            self.assertFalse(state.exists())

    def test_native_quote_api_exception_is_not_retried_or_replaced(self):
        client = FailingClient()
        secrets = {
            "X_API_KEY": "test",
            "X_API_KEY_SECRET": "test",
            "X_ACCESS_TOKEN": "test",
            "X_ACCESS_TOKEN_SECRET": "test",
        }
        with patch.dict(os.environ, secrets), patch.object(
            x_quote_post, "_get_client", return_value=client
        ):
            self.assertIsNone(
                x_quote_post.post_quote(
                    x_quote_post.COMMENTARY,
                    x_quote_post.DEFAULT_SOURCE_ID,
                )
            )
        self.assertEqual(client.call_count, 1)


if __name__ == "__main__":
    unittest.main()
