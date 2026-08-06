import unittest

from price_formatting import format_usd_prices


class PriceFormattingTests(unittest.TestCase):
    def test_decimal_man_price_uses_title_style(self):
        self.assertEqual(
            "ビットコイン6万4,600ドルを維持",
            format_usd_prices("ビットコイン6.46万ドルを維持", for_title=True),
        )

    def test_decimal_man_price_uses_article_style(self):
        self.assertEqual(
            "ビットコインは64,600ドルで推移した",
            format_usd_prices("ビットコインは6.46万ドルで推移した", for_title=False),
        )

    def test_existing_price_styles_are_normalized_for_their_context(self):
        self.assertEqual("6万4,600ドル", format_usd_prices("64,600ドル", for_title=True))
        self.assertEqual("64,600ドル", format_usd_prices("6万4,600ドル", for_title=False))

    def test_non_price_numbers_are_untouched(self):
        self.assertEqual("2026年8月6日", format_usd_prices("2026年8月6日", for_title=False))


if __name__ == "__main__":
    unittest.main()
