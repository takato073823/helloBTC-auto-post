"""WordPress投稿時のSEOデータに対する軽量テスト。"""
import unittest

from wp_poster import WordPressAPI


class RecordingWordPressAPI(WordPressAPI):
    def __init__(self):
        super().__init__("https://hellobtc.jp", "user", "password")
        self.calls = []

    def _request(self, method, endpoint, **kwargs):
        self.calls.append((method, endpoint, kwargs))
        if method == "POST" and endpoint == "posts":
            return {"link": "https://hellobtc.jp/test-article/"}
        return []


class WordPressPosterSEOTests(unittest.TestCase):
    def test_posts_meta_description_as_excerpt_without_inline_schema(self):
        wp = RecordingWordPressAPI()
        wp.post_article(
            title="テスト記事",
            content="<h2>見出し</h2><p>本文</p>",
            excerpt="短い要約",
            meta_description="Google検索に使う説明文",
            slug="test-article",
        )

        payload = wp.calls[-1][2]["json"]
        self.assertEqual(payload["excerpt"], "Google検索に使う説明文")
        self.assertEqual(payload["content"], "<h2>見出し</h2><p>本文</p>")
        self.assertNotIn("application/ld+json", payload["content"])

    def test_uses_summary_when_meta_description_is_missing(self):
        wp = RecordingWordPressAPI()
        wp.post_article(
            title="テスト記事",
            content="<p>本文</p>",
            excerpt="短い要約",
        )
        self.assertEqual(wp.calls[-1][2]["json"]["excerpt"], "短い要約")

    def test_normalizes_usd_price_notation_by_field(self):
        wp = RecordingWordPressAPI()
        wp.post_article(
            title="ビットコイン6.46万ドルを維持",
            content="<p>価格は6.46万ドルだった。</p>",
            excerpt="6.46万ドルで推移した。",
        )

        payload = wp.calls[-1][2]["json"]
        self.assertEqual("ビットコイン6万4,600ドルを維持", payload["title"])
        self.assertEqual("<p>価格は64,600ドルだった。</p>", payload["content"])
        self.assertEqual("64,600ドルで推移した。", payload["excerpt"])

    def test_fetches_all_published_title_pages(self):
        wp = RecordingWordPressAPI()
        pages = {
            1: [{"title": {"rendered": f"記事{i}"}} for i in range(100)],
            2: [{"title": {"rendered": "記事100"}}],
        }

        def request(method, endpoint, **kwargs):
            return pages[kwargs["params"]["page"]]

        wp._request = request
        titles = wp.get_published_titles()
        self.assertEqual(len(titles), 101)
        self.assertEqual(titles[-1], "記事100")

    def test_fetches_recent_titles_for_news_deduplication(self):
        wp = RecordingWordPressAPI()

        def request(method, endpoint, **kwargs):
            self.assertEqual("GET", method)
            self.assertEqual("posts", endpoint)
            self.assertEqual("publish", kwargs["params"]["status"])
            self.assertEqual("desc", kwargs["params"]["order"])
            self.assertIn("after", kwargs["params"])
            return [{"title": {"rendered": "直近ニュース"}}]

        wp._request = request
        self.assertEqual(["直近ニュース"], wp.get_recent_published_titles(days=30))

    def test_upserts_an_existing_editorial_policy_page(self):
        wp = RecordingWordPressAPI()
        responses = [
            [{"id": 900, "slug": "about-hellobtc-editorial-policy"}],
            {"id": 900, "link": "https://hellobtc.jp/about-hellobtc-editorial-policy/"},
        ]

        def request(method, endpoint, **kwargs):
            wp.calls.append((method, endpoint, kwargs))
            return responses.pop(0)

        wp._request = request
        result = wp.upsert_page(
            "about-hellobtc-editorial-policy", "編集方針", "<p>本文</p>", "要約"
        )
        self.assertEqual("pages/900", wp.calls[-1][1])
        self.assertEqual("publish", wp.calls[-1][2]["json"]["status"])
        self.assertEqual(900, result["id"])


if __name__ == "__main__":
    unittest.main()
