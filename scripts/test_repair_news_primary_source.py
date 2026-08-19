import unittest

from repair_news_primary_source import repair_content


class RepairNewsPrimarySourceTests(unittest.TestCase):
    def test_replaces_secondary_source_and_removes_conclusion_label(self):
        original = (
            '<h2>見出し</h2><p class="hellobtc-direct-answer">'
            '<strong>結論：</strong>SECが規則案を公表した。</p>'
            '<!-- wp:paragraph {"className":"hellobtc-source"} -->\n'
            '<p class="hellobtc-source">出典：<a href="https://cointelegraph.com/x">'
            'CoinTelegraph</a></p>\n<!-- /wp:paragraph -->'
        )
        actual = repair_content(
            original,
            "米国証券取引委員会（SEC）",
            "https://www.sec.gov/newsroom/press-releases/2026-76",
            "CoinTelegraph",
            "https://cointelegraph.com/x",
        )
        self.assertNotIn("<strong>結論：</strong>", actual)
        self.assertIn("一次資料：", actual)
        self.assertIn("https://www.sec.gov/newsroom/press-releases/2026-76", actual)
        self.assertIn("参考報道：", actual)
        self.assertEqual(1, actual.count('class="hellobtc-source"'))

    def test_fails_closed_if_expected_blocks_are_missing(self):
        with self.assertRaises(RuntimeError):
            repair_content(
                "<p>本文のみ</p>", "SEC", "https://www.sec.gov/example"
            )


if __name__ == "__main__":
    unittest.main()
