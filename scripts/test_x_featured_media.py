"""X記事投稿でアイキャッチを直接添付する処理のテスト。"""

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


# 開発環境にtweepyがない場合でも、ネットワークを使わない単体テストを実行する。
if "tweepy" not in sys.modules:
    tweepy_stub = types.ModuleType("tweepy")
    tweepy_stub.API = object
    tweepy_stub.Client = object
    tweepy_stub.OAuth1UserHandler = object
    sys.modules["tweepy"] = tweepy_stub

sys.path.insert(0, str(Path(__file__).parent))
import x_poster  # noqa: E402


class _Response:
    def __init__(self, content=b"jpeg-bytes", content_type="image/jpeg", text=""):
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.text = text

    def raise_for_status(self):
        return None


class _XApiError(Exception):
    def __init__(self, status_code):
        super().__init__(f"X API status {status_code}")
        self.response = SimpleNamespace(status_code=status_code)


class XFeaturedMediaTests(unittest.TestCase):
    def test_downloads_supported_same_host_image(self):
        with patch.object(x_poster.requests, "get", return_value=_Response()) as get:
            data, filename = x_poster._download_featured_image(
                "https://hellobtc.jp/wp-content/uploads/featured.jpg",
                "https://hellobtc.jp/article/",
            )
        self.assertEqual(b"jpeg-bytes", data)
        self.assertEqual("featured.jpg", filename)
        get.assert_called_once()

    def test_rejects_image_from_another_host(self):
        with self.assertRaisesRegex(ValueError, "同一ホスト"):
            x_poster._download_featured_image(
                "https://example.com/featured.jpg",
                "https://hellobtc.jp/article/",
            )

    def test_rejects_unsupported_content_type(self):
        with patch.object(
            x_poster.requests,
            "get",
            return_value=_Response(content_type="text/html"),
        ):
            with self.assertRaisesRegex(ValueError, "未対応"):
                x_poster._download_featured_image(
                    "https://hellobtc.jp/featured.webp",
                    "https://hellobtc.jp/article/",
                )

    def test_news_tweet_attaches_featured_image_when_og_image_is_missing(self):
        client = Mock()
        client.create_tweet.return_value = SimpleNamespace(data={"id": "tweet-123"})
        with (
            patch.object(x_poster, "_secrets_available", return_value=True),
            patch.object(x_poster, "_is_og_image_ready", return_value=False),
            patch.object(x_poster, "_get_oauth1_api", return_value=Mock()),
            patch.object(x_poster, "_upload_featured_image", return_value="media-456") as upload,
            patch.object(x_poster, "_get_client", return_value=client),
        ):
            result = x_poster.post_tweet(
                title="テスト記事",
                article_url="https://hellobtc.jp/test/",
                featured_image_url="https://hellobtc.jp/featured.jpg",
                attach_featured_image_if_og_missing=True,
            )

        self.assertEqual("tweet-123", result)
        upload.assert_called_once()
        client.create_tweet.assert_called_once()
        self.assertEqual(
            ["media-456"],
            client.create_tweet.call_args.kwargs["media_ids"],
        )

    def test_healthy_og_card_is_posted_without_media_attachment(self):
        client = Mock()
        client.create_tweet.return_value = SimpleNamespace(data={"id": "tweet-123"})
        with (
            patch.object(x_poster, "_secrets_available", return_value=True),
            patch.object(x_poster, "_is_og_image_ready", return_value=True),
            patch.object(x_poster, "_upload_featured_image") as upload,
            patch.object(x_poster, "_get_client", return_value=client),
        ):
            result = x_poster.post_tweet(
                title="OGPが健全なニュース",
                article_url="https://hellobtc.jp/ready/",
                featured_image_url="https://hellobtc.jp/featured.jpg",
                attach_featured_image_if_og_missing=True,
            )

        self.assertEqual("tweet-123", result)
        upload.assert_not_called()
        self.assertNotIn("media_ids", client.create_tweet.call_args.kwargs)

    def test_article_post_uses_featured_image_fallback_when_opted_in(self):
        client = Mock()
        client.create_tweet.return_value = SimpleNamespace(data={"id": "tweet-123"})
        with (
            patch.object(x_poster, "_secrets_available", return_value=True),
            patch.object(x_poster, "_is_og_image_ready", return_value=False),
            patch.object(x_poster, "_get_oauth1_api", return_value=Mock()),
            patch.object(x_poster, "_upload_featured_image", return_value="media-789") as upload,
            patch.object(x_poster, "_get_client", return_value=client),
        ):
            result = x_poster.post_tweet(
                title="取引所ガイド",
                article_url="https://hellobtc.jp/guide/",
                featured_image_url="https://hellobtc.jp/featured.jpg",
                article_section="取引所",
                attach_featured_image_if_og_missing=True,
            )

        self.assertEqual("tweet-123", result)
        upload.assert_called_once()
        self.assertEqual(
            ["media-789"],
            client.create_tweet.call_args.kwargs["media_ids"],
        )

    def test_skips_news_tweet_when_og_image_is_missing_and_no_fallback_exists(self):
        with (
            patch.object(x_poster, "_secrets_available", return_value=True),
            patch.object(x_poster, "_is_og_image_ready", return_value=False),
            patch.object(x_poster, "_get_client") as get_client,
        ):
            result = x_poster.post_tweet(
                title="画像なし記事",
                article_url="https://hellobtc.jp/no-image/",
                attach_featured_image_if_og_missing=True,
            )
        self.assertIsNone(result)
        get_client.assert_not_called()

    def test_og_image_check_requires_a_retrievable_og_image(self):
        article = _Response(
            text='<meta property="og:image" content="https://hellobtc.jp/featured.jpg">'
        )
        with (
            patch.object(x_poster, "_OGP_CHECK_ATTEMPTS", 1),
            patch.object(x_poster.requests, "get", return_value=article),
            patch.object(x_poster, "_download_featured_image", return_value=(b"img", "featured.jpg")),
        ):
            self.assertTrue(x_poster._is_og_image_ready("https://hellobtc.jp/article/"))

    def test_explicit_related_reply_is_posted_after_the_main_tweet(self):
        client = Mock()
        client.create_tweet.side_effect = [
            SimpleNamespace(data={"id": "tweet-123"}),
            SimpleNamespace(data={"id": "reply-456"}),
        ]
        with (
            patch.object(x_poster, "_secrets_available", return_value=True),
            patch.object(x_poster, "_get_client", return_value=client),
        ):
            result = x_poster.post_tweet(
                title="通常の記事",
                article_url="https://hellobtc.jp/bingx-guide/",
                tags=["取引所"],
                related_reply_text="【広告・PR】関連情報\nhttps://bingxdao.com/invite/FIKYOA/",
                return_post_result=True,
            )

        self.assertEqual("tweet-123", result.tweet_id)
        self.assertTrue(result.related_reply_posted)
        self.assertEqual(2, client.create_tweet.call_count)
        self.assertEqual(
            "tweet-123",
            client.create_tweet.call_args_list[1].kwargs["in_reply_to_tweet_id"],
        )
        self.assertEqual(
            "【広告・PR】関連情報\nhttps://bingxdao.com/invite/FIKYOA/",
            client.create_tweet.call_args_list[1].kwargs["text"],
        )

    def test_bingx_mention_does_not_post_an_invite_reply_without_explicit_opt_in(self):
        client = Mock()
        client.create_tweet.return_value = SimpleNamespace(data={"id": "tweet-123"})
        with (
            patch.object(x_poster, "_secrets_available", return_value=True),
            patch.object(x_poster, "_get_client", return_value=client),
        ):
            result = x_poster.post_tweet(
                title="BingXにも言及するニュース",
                article_url="https://hellobtc.jp/bitcoin-guide/",
                tags=["BingX", "ニュース"],
            )

        self.assertEqual("tweet-123", result)
        client.create_tweet.assert_called_once()

    def test_related_reply_failure_is_returned_for_persistent_recovery(self):
        client = Mock()
        client.create_tweet.side_effect = [
            SimpleNamespace(data={"id": "tweet-123"}),
            _XApiError(403),
        ]
        with (
            patch.object(x_poster, "_secrets_available", return_value=True),
            patch.object(x_poster, "_get_client", return_value=client),
        ):
            result = x_poster.post_tweet(
                title="通常記事",
                article_url="https://hellobtc.jp/article/",
                related_reply_text="関連投稿",
                return_post_result=True,
            )

        self.assertEqual("tweet-123", result.tweet_id)
        self.assertFalse(result.related_reply_posted)
        self.assertEqual(2, client.create_tweet.call_count)

    def test_rate_limited_related_reply_retries_without_reposting_the_main_tweet(self):
        client = Mock()
        client.create_tweet.side_effect = [
            SimpleNamespace(data={"id": "tweet-123"}),
            _XApiError(429),
            SimpleNamespace(data={"id": "reply-456"}),
        ]
        with (
            patch.object(x_poster, "_secrets_available", return_value=True),
            patch.object(x_poster, "_get_client", return_value=client),
            patch.object(x_poster.time, "sleep") as sleep,
        ):
            result = x_poster.post_tweet(
                title="通常記事",
                article_url="https://hellobtc.jp/article/",
                related_reply_text="関連投稿",
                return_post_result=True,
            )

        self.assertEqual("tweet-123", result.tweet_id)
        self.assertTrue(result.related_reply_posted)
        self.assertEqual(3, client.create_tweet.call_count)
        self.assertEqual(1, sleep.call_count)

    def test_every_hellobtc_article_workflow_opts_in_to_the_fallback(self):
        root = Path(__file__).parent
        for filename in (
            "main.py",
            "breaking_news.py",
            "coldcard_security_news.py",
            "bingx_seo.py",
            "exchange_guide.py",
        ):
            self.assertIn(
                "attach_featured_image_if_og_missing=True",
                (root / filename).read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
