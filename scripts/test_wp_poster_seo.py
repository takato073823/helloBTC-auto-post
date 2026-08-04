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


if __name__ == "__main__":
    unittest.main()
