"""X投稿でサービス名が外部リンク化しないことを確認する軽量テスト。"""
import ast
import re
import unittest
from pathlib import Path


SOURCE_PATH = Path(__file__).with_name("x_poster.py")
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")


def _load_helpers():
    tree = ast.parse(SOURCE)
    assignments = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_SERVICE_DOMAIN_RE" for target in node.targets)
    ]
    functions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_neutralize_service_domains", "_build_hashtags", "_build_tweet"}
    ]
    namespace = {"re": re}
    exec(compile(ast.Module(body=assignments + functions, type_ignores=[]), str(SOURCE_PATH), "exec"), namespace)
    return namespace


class XPosterRulesTests(unittest.TestCase):
    def setUp(self):
        self.helpers = _load_helpers()

    def test_service_domain_is_not_left_as_an_external_link(self):
        neutralize = self.helpers["_neutralize_service_domains"]
        self.assertEqual("Crypto(.)com", neutralize("Crypto.com"))
        self.assertEqual("BingX(.)com", neutralize("BingX.com"))
        self.assertEqual("Crypto(.)comの情報", neutralize("Crypto.comの情報"))
        self.assertEqual("取引所Crypto(.)com", neutralize("取引所Crypto.com"))
        self.assertEqual("Crypto(.)com-X", neutralize("Crypto.com-X"))
        self.assertEqual("Crypto(.)com.", neutralize("Crypto.com."))
        self.assertEqual("crypto.com.example", neutralize("crypto.com.example"))

    def test_real_urls_and_email_addresses_are_not_changed(self):
        neutralize = self.helpers["_neutralize_service_domains"]
        self.assertEqual("https://crypto.com/news", neutralize("https://crypto.com/news"))
        self.assertEqual("help@crypto.com", neutralize("help@crypto.com"))

    def test_article_url_remains_the_only_external_url_in_tweet(self):
        tweet = self.helpers["_build_tweet"](
            title="Crypto.comが新サービスを発表",
            article_url="https://hellobtc.jp/crypto-com-news/",
            article_section="ニュース",
            tweet_bullets=["Crypto.comの公式発表を確認"],
            tags=["Crypto.com", "ニュース"],
        )
        self.assertIn("Crypto(.)com", tweet)
        self.assertIn("https://hellobtc.jp/crypto-com-news/", tweet)
        self.assertNotIn("https://crypto.com", tweet)


if __name__ == "__main__":
    unittest.main()
