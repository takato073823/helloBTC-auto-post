"""INUのネイティブ引用投稿をオフラインで検証する。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from inu_quote_post import load_state, publish, validate_quote


TEXT = "アーサー・ヘイズ氏、ビットコインの底打ちを主張。\n\n「100万ドルへ向けて上昇が始まる」と発言しました。\n\n僕としては、強気発言のあとにBTCへ実際の買いが続くかを見ます。\n\n#ビットコイン #仮想通貨"


class INUQuotePostTests(unittest.TestCase):
    def test_quote_copy_is_valid(self):
        text = validate_quote("arthur_hayes_btc_20260805", "2084816282902466746", TEXT, {"posted": []})
        self.assertIn("僕としては", text)

    def test_duplicate_key_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "公開済み"):
            validate_quote(
                "arthur_hayes_btc_20260805",
                "2084816282902466746",
                TEXT,
                {"posted": [{"post_key": "arthur_hayes_btc_20260805"}]},
            )

    def test_success_writes_one_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            tweet_id = publish(
                "arthur_hayes_btc_20260805",
                "2084816282902466746",
                TEXT,
                state_path=path,
                poster=lambda _text, _id: "123456",
            )
            self.assertEqual("123456", tweet_id)
            self.assertEqual("123456", load_state(path)["posted"][0]["tweet_id"])
            self.assertEqual("2084816282902466746", load_state(path)["posted"][0]["source_tweet_id"])
            self.assertEqual("x_native_video_reference", load_state(path)["posted"][0]["delivery_mode"])

    def test_video_reference_url_is_added_by_poster(self):
        captured = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            publish(
                "arthur_hayes_btc_20260805",
                "2084816282902466746",
                TEXT,
                state_path=path,
                poster=lambda text, _id: captured.append(text) or "123456",
            )
        # publish passes the editorial body only; x_poster is solely responsible for
        # adding the native-video reference, so it can never be mistaken for a source URL.
        self.assertNotIn("/video/1", captured[0])

    def test_state_file_is_json(self):
        self.assertEqual({"posted": []}, json.loads('{"posted": []}'))


if __name__ == "__main__":
    unittest.main()
