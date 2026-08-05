"""出典ページの主画像をニュース投稿の先頭に使う経路を検証する。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from inu_news_visual import capture_source_hero_image


class FakeResponse:
    def __init__(self, *, text: str = "", content: bytes = b""):
        self.text = text
        self.content = content

    def raise_for_status(self):
        return None


class FakeSession:
    def __init__(self, html: str, image: bytes):
        self.html = html
        self.image = image
        self.urls: list[str] = []

    def get(self, url, **_kwargs):
        self.urls.append(url)
        if url == "https://example.com/news":
            return FakeResponse(text=self.html)
        return FakeResponse(content=self.image)


class INUNewsVisualTests(unittest.TestCase):
    def test_uses_the_source_pages_og_image_not_an_unrelated_search_result(self):
        buffer = io.BytesIO()
        Image.new("RGB", (1200, 675), "#e85d04").save(buffer, format="JPEG")
        session = FakeSession(
            '<meta property="og:image" content="/images/alibaba-news.jpg">', buffer.getvalue()
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "main.png"
            capture_source_hero_image(
                source_url="https://example.com/news",
                source_name="Example News",
                published_at="2026-08-05",
                output_path=output,
                is_primary_source=False,
                session=session,
            )
            manifest = json.loads(output.with_suffix(".source.json").read_text(encoding="utf-8"))
        self.assertEqual(
            ["https://example.com/news", "https://example.com/images/alibaba-news.jpg"],
            session.urls,
        )
        self.assertEqual("source_news_image", manifest["evidence_type"])
        self.assertEqual("attention_visual", manifest["visual_role"])

    def test_does_not_fetch_cointelegraph_editorial_visuals(self):
        session = FakeSession("", b"")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Cointelegraph"):
                capture_source_hero_image(
                    source_url="https://cointelegraph.com/news/bitcoin-market-update",
                    source_name="Cointelegraph",
                    published_at="2026-08-05",
                    output_path=Path(directory) / "main.png",
                    is_primary_source=False,
                    session=session,
                )
        self.assertEqual([], session.urls)


if __name__ == "__main__":
    unittest.main()
