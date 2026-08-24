"""追加API費用のかからない画像生成のテスト。"""

import io
import unittest

from PIL import Image

from local_images import _is_explicit_market_chart_request, create_editorial_image


class LocalImageTests(unittest.TestCase):
    def test_generates_expected_jpeg_size(self):
        raw = create_editorial_image("bitcoin market chart", width=1200, height=630)
        image = Image.open(io.BytesIO(raw))

        self.assertEqual(image.format, "JPEG")
        self.assertEqual(image.size, (1200, 630))
        self.assertGreater(len(raw), 20_000)

    def test_same_prompt_is_deterministic(self):
        first = create_editorial_image("blockchain network", width=640, height=360)
        second = create_editorial_image("blockchain network", width=640, height=360)
        different = create_editorial_image("defi protocol", width=640, height=360)

        self.assertEqual(first, second)
        self.assertNotEqual(first, different)

    def test_fallback_image_contains_no_site_text(self):
        raw = create_editorial_image("bitcoin market chart", width=1200, height=630)
        # 代替画像は画像生成の文字品質検査を回避するため、文字列を埋め込まない。
        self.assertNotIn(b"helloBTC", raw)

    def test_chart_is_used_only_for_an_explicit_price_chart_request(self):
        self.assertTrue(_is_explicit_market_chart_request("BTC candlestick chart after a price breakout"))
        self.assertFalse(_is_explicit_market_chart_request("Bitcoin and Ether ETF inflows"))
        self.assertFalse(_is_explicit_market_chart_request("Term Finance governance exploit"))
        self.assertFalse(_is_explicit_market_chart_request("Grayscale Zcash ETF SEC filing amendment"))


if __name__ == "__main__":
    unittest.main()
