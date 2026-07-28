"""ニュース投稿の恒常ルールに対する軽量テスト。"""
import os
import unittest

# generator.py はクライアント初期化時にキーの存在を確認するため、API呼び出しを
# 行わないこのテストではダミー値を与える。
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key-not-used")

from generator import prepend_lead_heading, resolve_logo_brand


class NewsPostRuleTests(unittest.TestCase):
    def test_prepends_a_reworded_h2(self):
        title = "BitMart、取引所事業を段階的に終了へ　8月26日に全取引停止"
        content = "<p>リード文</p>"
        actual = prepend_lead_heading(
            content, title, "BitMartが取引所事業を段階的に終了、8月26日に全取引を停止"
        )
        self.assertTrue(actual.startswith("<h2>BitMartが取引所事業を段階的に終了、8月26日に全取引を停止</h2>"))
        self.assertIn(content, actual)

    def test_never_reuses_the_post_title_verbatim(self):
        title = "BitMart、取引所事業を段階的に終了へ　8月26日に全取引停止"
        actual = prepend_lead_heading("<p>リード文</p>", title, title)
        self.assertTrue(actual.startswith("<h2>BitMart、取引所事業を段階的に終了へ 8月26日に全取引停止を解説</h2>"))

    def test_known_brand_uses_its_fixed_official_domain(self):
        self.assertEqual(
            resolve_logo_brand("BitMart、取引所事業を段階的に終了へ", ["暗号資産"]),
            ("BitMart", "bitmart.com"),
        )


if __name__ == "__main__":
    unittest.main()
