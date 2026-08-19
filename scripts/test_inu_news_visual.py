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
    def __init__(self, *, text: str = "", content: bytes = b"", payload=None):
        self.text = text
        self.content = content
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


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


class FakeLogoSession:
    def __init__(self, logo: bytes):
        self.logo = logo

    def get(self, url, **_kwargs):
        if "api.coingecko.com" in url:
            return FakeResponse(payload={
                "id": "ethereum",
                "links": {"homepage": ["https://ethereum.org/"]},
                "image": {"large": "https://coin-images.coingecko.com/coins/images/279/large/ethereum.png"},
            })
        return FakeResponse(content=self.logo)


class INUNewsVisualTests(unittest.TestCase):
    def test_regulatory_visual_prompt_requires_physical_sec_seal_not_text_label(self):
        prompt = _editorial_prompt(
            hook="📜 SEC、暗号資産規則案を公表",
            facts=["最大7500万ドルの資金調達免除を提案しました。"],
            topic_type="regulatory_rule_change",
        )
        self.assertIn("photorealistic", prompt)
        self.assertIn("federal building", prompt)
        self.assertIn("physically mounted cast-metal", prompt)
        self.assertIn("American flag", prompt)
        self.assertIn("not from an added text box", prompt)
        self.assertIn("portrait 4:5", prompt)

    def test_identifies_sec_and_public_figure_as_different_visual_requirements(self):
        sec = identify_visual_subject(hook="📜 SEC、暗号資産規則案を公表")
        trump = identify_visual_subject(hook="🇺🇸 トランプ大統領、暗号資産政策を発表")
        self.assertEqual("institution", sec["kind"])
        self.assertEqual("SEC", sec["label"])
        self.assertEqual("public_figure", trump["kind"])
        self.assertEqual("verified_primary_source_photo", trump["identity_method"])
        self.assertEqual("generated_editorial_portrait", trump["fallback_identity_method"])

    def test_identifies_crypto_project_by_name_or_ticker(self):
        ethereum = identify_visual_subject(hook="Ethereum、ネットワーク更新を発表")
        bitcoin = identify_visual_subject(hook="$BTCのETFフローが増加")
        self.assertEqual("crypto_project", ethereum["kind"])
        self.assertEqual("ETH", ethereum["symbol"])
        self.assertEqual("ethereum.org", ethereum["official_domain"])
        self.assertEqual("BTC", bitcoin["symbol"])

    def test_common_english_word_does_not_become_short_ticker(self):
        self.assertIsNone(identify_visual_subject(hook="公式ページへのlinkを更新"))
        self.assertEqual(
            "LINK",
            identify_visual_subject(hook="$LINK、ステーキング仕様を更新")["symbol"],
        )

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

    def test_public_figure_rejects_generic_primary_source_og_image(self):
        buffer = io.BytesIO()
        Image.new("RGB", (1200, 675), "#1e3a8a").save(buffer, format="JPEG")
        session = FakeSession(
            '<meta property="og:image" content="/images/white-house-building.jpg">',
            buffer.getvalue(),
        )
        subject = identify_visual_subject(hook="トランプ大統領、政策を発表")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "人物本人"):
                capture_source_hero_image(
                    source_url="https://example.com/news",
                    source_name="The White House",
                    published_at="2026-08-19",
                    output_path=Path(directory) / "main.png",
                    is_primary_source=True,
                    visual_subject=subject,
                    session=session,
                )

    def test_sec_visual_records_physical_seal_scene_not_plain_text_overlay(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "sec.png"

            def fake_generate(_prompt, destination):
                Image.new("RGB", (1024, 1280), "#cbd5e1").save(destination)

            with patch("inu_news_visual.generate_image", side_effect=fake_generate) as generated:
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
            self.assertTrue(manifest["official_mark_depicted"])
            self.assertEqual("photorealistic_physical_object", manifest["mark_depiction_mode"])
            prompt = generated.call_args.args[0]
            self.assertIn("physically mounted cast-metal", prompt)
            self.assertNotIn("editorial agency label", prompt)

    def test_public_figure_generated_fallback_is_neutral_and_not_event_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "trump.png"

            def fake_generate(prompt, destination):
                self.assertIn("neutral, close editorial portrait", prompt)
                self.assertIn("must not imply documentary proof", prompt)
                Image.new("RGB", (1024, 1280), "#243447").save(destination)

            with patch("inu_news_visual.generate_image", side_effect=fake_generate):
                generate_editorial_news_visual(
                    hook="トランプ大統領、暗号資産政策を発表",
                    facts=["公式発表を確認しました。"],
                    topic_type="regulatory_rule_change",
                    source_url="https://www.whitehouse.gov/example",
                    source_name="The White House",
                    published_at="2026-08-19",
                    output_path=output,
                    is_primary_source=True,
                )
            manifest = json.loads(output.with_suffix(".source.json").read_text(encoding="utf-8"))
            self.assertEqual("generated_editorial_portrait", manifest["identity_method_used"])
            self.assertTrue(manifest["generated_public_figure_portrait"])
            self.assertTrue(manifest["not_event_evidence"])

    def test_crypto_project_uses_verified_exact_logo_asset(self):
        logo_buffer = io.BytesIO()
        Image.new("RGBA", (512, 512), (98, 126, 234, 255)).save(logo_buffer, format="PNG")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "ethereum.png"

            def fake_generate(prompt, destination):
                self.assertIn("blank and unobstructed", prompt)
                self.assertIn("Do not draw any logo", prompt)
                Image.new("RGB", (1024, 1280), "#0f172a").save(destination)

            with patch("inu_news_visual.generate_image", side_effect=fake_generate):
                generate_editorial_news_visual(
                    hook="Ethereum、ネットワーク更新を発表",
                    facts=["公式ブログで更新内容を公表しました。"],
                    topic_type="protocol_update",
                    source_url="https://ethereum.org/example",
                    source_name="Ethereum Foundation",
                    published_at="2026-08-19",
                    output_path=output,
                    is_primary_source=True,
                    session=FakeLogoSession(logo_buffer.getvalue()),
                )
            manifest = json.loads(output.with_suffix(".source.json").read_text(encoding="utf-8"))
            with Image.open(output) as image:
                self.assertEqual((1200, 1500), image.size)
            self.assertTrue(manifest["official_logo_used"])
            self.assertTrue(manifest["logo_verified_against_official_domain"])
            self.assertIn("coin-images.coingecko.com", manifest["logo_source_url"])


if __name__ == "__main__":
    unittest.main()
