"""出典ページの主画像をニュース投稿の先頭に使う経路を検証する。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from inu_news_visual import (
    _editorial_prompt,
    capture_source_hero_image,
    generate_editorial_news_visual,
    identify_visual_subject,
)


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
    def test_regulatory_visual_prompt_requires_photorealistic_scene_without_fake_logo(self):
        prompt = _editorial_prompt(
            hook="📜 SEC、暗号資産規則案を公表",
            facts=["最大7500万ドルの資金調達免除を提案しました。"],
            topic_type="regulatory_rule_change",
        )
        self.assertIn("photorealistic", prompt)
        self.assertIn("federal building", prompt)
        self.assertIn("Do not fabricate an SEC logo", prompt)
        self.assertIn("portrait 4:5", prompt)

    def test_identifies_sec_and_public_figure_as_different_visual_requirements(self):
        sec = identify_visual_subject(hook="📜 SEC、暗号資産規則案を公表")
        trump = identify_visual_subject(hook="🇺🇸 トランプ大統領、暗号資産政策を発表")
        self.assertEqual("institution", sec["kind"])
        self.assertEqual("SEC", sec["label"])
        self.assertEqual("public_figure", trump["kind"])
        self.assertEqual("verified_primary_source_photo", trump["identity_method"])

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
            with Image.open(output) as image:
                self.assertEqual((1200, 1500), image.size)
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

    def test_public_figure_rejects_non_primary_source_photo(self):
        subject = identify_visual_subject(hook="トランプ大統領、政策を発表")
        session = FakeSession("", b"")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "一次ソース"):
                capture_source_hero_image(
                    source_url="https://example.com/news",
                    source_name="Example News",
                    published_at="2026-08-19",
                    output_path=Path(directory) / "main.png",
                    is_primary_source=False,
                    visual_subject=subject,
                    session=session,
                )
        self.assertEqual([], session.urls)

    def test_sec_visual_uses_large_plain_text_identity_not_official_logo(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "sec.png"

            def fake_generate(_prompt, destination):
                Image.new("RGB", (1024, 1280), "#cbd5e1").save(destination)

            with patch("inu_news_visual.generate_image", side_effect=fake_generate):
                generate_editorial_news_visual(
                    hook="📜 SEC、暗号資産規則案を公表",
                    facts=["規則案を公式発表しました。"],
                    topic_type="regulatory_rule_change",
                    source_url="https://www.sec.gov/newsroom/press-releases/example",
                    source_name="U.S. Securities and Exchange Commission",
                    published_at="2026-08-19",
                    output_path=output,
                    is_primary_source=True,
                )
            manifest = json.loads(output.with_suffix(".source.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["subject_identifiable"])
            self.assertEqual("SEC", manifest["visual_subject"]["label"])
            self.assertFalse(manifest["official_logo_used"])
            with Image.open(output) as image:
                self.assertNotEqual((203, 213, 225), image.getpixel((80, 100)))

    def test_public_figure_never_uses_generated_likeness(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "AI画像にせず"):
                generate_editorial_news_visual(
                    hook="トランプ大統領、暗号資産政策を発表",
                    facts=["公式発表を確認しました。"],
                    topic_type="regulatory_rule_change",
                    source_url="https://www.whitehouse.gov/example",
                    source_name="The White House",
                    published_at="2026-08-19",
                    output_path=Path(directory) / "trump.png",
                    is_primary_source=True,
                )


if __name__ == "__main__":
    unittest.main()
