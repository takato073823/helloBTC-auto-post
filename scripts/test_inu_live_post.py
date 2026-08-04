"""INUの実投稿ゲートをオフラインで検証する。"""

from __future__ import annotations

import unittest

from inu_live_post import load_test_item, publish_test_item, validate_test_item


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


if __name__ == "__main__":
    unittest.main()
