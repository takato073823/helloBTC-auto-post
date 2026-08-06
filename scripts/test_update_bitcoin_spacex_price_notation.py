import unittest

from update_bitcoin_spacex_price_notation import normalized_fields


class BitcoinSpacexPriceNotationTests(unittest.TestCase):
    def test_updates_title_and_article_text_with_different_formats(self):
        fields = normalized_fields({
            "title": {"raw": "ビットコイン6.46万ドル維持"},
            "content": {"raw": "<p>6.46万ドルで推移。</p>"},
            "excerpt": {"raw": "価格は6万4,600ドル。"},
        })
        self.assertEqual("ビットコイン6万4,600ドル維持", fields["title"])
        self.assertEqual("<p>64,600ドルで推移。</p>", fields["content"])
        self.assertEqual("価格は64,600ドル。", fields["excerpt"])

    def test_returns_no_fields_when_already_normalized(self):
        self.assertEqual({}, normalized_fields({
            "title": {"raw": "ビットコイン6万4,600ドル維持"},
            "content": {"raw": "<p>64,600ドルで推移。</p>"},
            "excerpt": {"raw": ""},
        }))


if __name__ == "__main__":
    unittest.main()
