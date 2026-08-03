"""追加API費用のかからない画像生成のテスト。"""

import io
import unittest

from PIL import Image

from local_images import create_editorial_image


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


if __name__ == "__main__":
    unittest.main()
