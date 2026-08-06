"""Grok Imagineを含むINU画像生成のオフライン検証。"""

from __future__ import annotations

import base64
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from PIL import Image

import inu_gpt_image


def _jpeg_bytes() -> bytes:
    image = Image.new("RGB", (120, 80), "#d96b5f")
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


class GrokImageTests(unittest.TestCase):
    def test_portrait_and_landscape_sizes_map_to_supported_grok_ratios(self):
        self.assertEqual("3:4", inu_gpt_image._grok_aspect_ratio("1024x1280"))
        self.assertEqual("3:2", inu_gpt_image._grok_aspect_ratio("1536x1024"))

    def test_grok_image_is_normalized_to_png_with_portrait_settings(self):
        encoded = base64.b64encode(_jpeg_bytes()).decode()
        response = SimpleNamespace(
            data=[SimpleNamespace(b64_json=encoded)],
            usage=SimpleNamespace(cost_in_usd_ticks=500_000_000),
        )
        generate = MagicMock(return_value=response)
        client = SimpleNamespace(images=SimpleNamespace(generate=generate))
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"INU_IMAGE_PROVIDER": "grok", "INU_IMAGE_FALLBACK_PROVIDER": "none"},
            clear=False,
        ), patch.object(inu_gpt_image, "_grok_client", return_value=client):
            output = inu_gpt_image.generate_image("original editorial image", Path(directory) / "visual.png")
            with Image.open(output) as image:
                self.assertEqual("PNG", image.format)
                self.assertEqual((120, 80), image.size)
            self.assertEqual("3:4", generate.call_args.kwargs["extra_body"]["aspect_ratio"])
        self.assertTrue(output.name.endswith(".png"))

    def test_grok_failure_uses_explicit_openai_fallback(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {"INU_IMAGE_PROVIDER": "grok", "INU_IMAGE_FALLBACK_PROVIDER": "openai"},
            clear=False,
        ), patch.object(inu_gpt_image, "_generate_grok_image", side_effect=RuntimeError("xAI unavailable")), patch.object(
            inu_gpt_image, "_generate_openai_image", return_value=_jpeg_bytes()
        ) as fallback:
            output = inu_gpt_image.generate_image("fallback", Path(directory) / "visual.png")
            self.assertTrue(output.is_file())
            fallback.assert_called_once()

    def test_default_path_is_openai_and_never_falls_back_to_grok(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {},
            clear=True,
        ), patch.object(inu_gpt_image, "_generate_openai_image", return_value=_jpeg_bytes()) as openai, patch.object(
            inu_gpt_image, "_generate_grok_image"
        ) as grok:
            output = inu_gpt_image.generate_image("default", Path(directory) / "visual.png")
            self.assertTrue(output.is_file())
            openai.assert_called_once()
            grok.assert_not_called()


if __name__ == "__main__":
    unittest.main()
