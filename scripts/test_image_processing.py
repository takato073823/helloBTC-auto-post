"""縦横比を維持する画像仕上げ処理のテスト。"""

import io
import unittest

from PIL import Image, ImageDraw

from image_processing import SAFE_COMPOSITION_PROMPT, fit_image_to_jpeg


class ImageProcessingTests(unittest.TestCase):
    def test_shared_prompt_protects_subject_framing(self):
        self.assertIn("8 percent safe margin from all four edges", SAFE_COMPOSITION_PROMPT)
        self.assertIn("Never crop, obscure, or cut off the primary subject", SAFE_COMPOSITION_PROMPT)
        self.assertIn("final 1.91:1 crop", SAFE_COMPOSITION_PROMPT)
        self.assertNotIn("building dome", SAFE_COMPOSITION_PROMPT.lower())
        self.assertNotIn("architecture", SAFE_COMPOSITION_PROMPT.lower())

    def test_fit_crops_without_distorting_centered_square(self):
        source = Image.new("RGB", (800, 800), "black")
        ImageDraw.Draw(source).rectangle((200, 200, 600, 600), fill="red")
        raw = io.BytesIO()
        source.save(raw, format="PNG")

        result = Image.open(io.BytesIO(fit_image_to_jpeg(
            raw.getvalue(), width=1200, height=630, quality=95,
        ))).convert("RGB")
        self.assertEqual(result.size, (1200, 630))

        red_pixels = [
            (x, y)
            for y in range(result.height)
            for x in range(result.width)
            if result.getpixel((x, y))[0] > 180 and result.getpixel((x, y))[1] < 70
        ]
        xs = [point[0] for point in red_pixels]
        ys = [point[1] for point in red_pixels]
        red_width = max(xs) - min(xs) + 1
        red_height = max(ys) - min(ys) + 1
        self.assertLessEqual(abs(red_width - red_height), 3)


if __name__ == "__main__":
    unittest.main()
