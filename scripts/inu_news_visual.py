"""INUニュース投稿の主画像を、根拠画像とは分けて用意する。"""

from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

from inu_gpt_image import generate_image


USER_AGENT = "Mozilla/5.0 (compatible; INUNewsVisual/1.0)"
MIN_IMAGE_SIZE = (640, 360)
MAX_IMAGE_PIXELS = 3_000_000


def _https_url(value: str, base_url: str) -> str:
    url = urljoin(base_url, (value or "").strip())
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("HTTPSの主画像URLが必要です")
    return url


def _og_image_url(html: str, source_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for selector in (
        'meta[property="og:image"]',
        'meta[name="twitter:image"]',
        'meta[property="twitter:image"]',
    ):
        tag = soup.select_one(selector)
        if tag and tag.get("content"):
            return _https_url(str(tag["content"]), source_url)
    raise ValueError("記事・公式発表に主画像が見つかりません")


def _save_as_source_png(image_bytes: bytes, output_path: Path) -> None:
    with Image.open(io.BytesIO(image_bytes)) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
        if image.width < MIN_IMAGE_SIZE[0] or image.height < MIN_IMAGE_SIZE[1]:
            raise ValueError("主画像が小さすぎます")
        image.thumbnail((MAX_IMAGE_PIXELS, MAX_IMAGE_PIXELS), Image.Resampling.LANCZOS)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path, format="PNG", optimize=True)


def capture_source_hero_image(
    *,
    source_url: str,
    source_name: str,
    published_at: str,
    output_path: str | Path,
    is_primary_source: bool,
    session: requests.Session | None = None,
) -> Path:
    """出典ページ自身が示す主画像だけを、投稿1枚目用に保存する。

    検索結果の無関係な画像は使わず、記事または公式発表のOG画像に限定する。
    人物ニュースでは同記事に掲載された本人写真、企業ニュースでは公式ロゴや
    現場写真が自然に最優先になる。
    """
    requester = session or requests.Session()
    headers = {"User-Agent": USER_AGENT}
    page = requester.get(source_url, headers=headers, timeout=25)
    page.raise_for_status()
    image_url = _og_image_url(page.text, source_url)
    response = requester.get(image_url, headers=headers, timeout=25)
    response.raise_for_status()

    destination = Path(output_path)
    _save_as_source_png(response.content, destination)
    manifest = {
        "source_url": source_url,
        "source_name": source_name,
        "published_at": published_at,
        "evidence_type": "source_news_image",
        "is_primary_source": bool(is_primary_source),
        "source_image_url": image_url,
        "capture_type": "source_hero_image",
        "visual_role": "attention_visual",
        "facts_verified": True,
        "generated_image": False,
    }
    destination.with_suffix(".source.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination


def _editorial_prompt(*, hook: str, facts: list[str], topic_type: str) -> str:
    facts_text = "\n".join(f"- {fact}" for fact in facts[:2])
    return f"""
Use case: ads-marketing
Asset type: primary image for a timely Japanese X news post, portrait 4:5
Primary request: Create an original, high-impact editorial news visual for this verified event.
Topic: {hook}
Verified context (for visual direction only, do not render words or numbers):
{facts_text}
Topic type: {topic_type}
Scene/backdrop: a specific, contemporary business or technology scene that communicates the event at a glance.
Style/medium: premium financial-news editorial photography or cinematic illustration, sophisticated and credible at phone size.
Composition/framing: portrait 4:5, one decisive focal point, strong depth and contrast, generous clean negative space. It must feel like a news image, not a web card or infographic.
Text: no text at all.
Constraints: do not generate letters, words, numbers, charts, interface screens, company logos, trademarks, watermarks, signatures, or a likeness of a real person. Do not invent a claim beyond the verified context. This image is an attention visual; the source evidence will be attached separately.
Avoid: generic stock-photo office scenes, HTML dashboards, black breaking-news template, cryptocurrency coins unless directly essential, mascots, copied influencer layouts, and imitation of any reference image.
""".strip()


def generate_editorial_news_visual(
    *,
    hook: str,
    facts: list[str],
    topic_type: str,
    source_url: str,
    source_name: str,
    published_at: str,
    output_path: str | Path,
    is_primary_source: bool,
) -> Path:
    """主画像がない場合だけ、事実を増やさないテキストレスの生成画像を作る。"""
    destination = Path(output_path)
    prompt = _editorial_prompt(hook=hook, facts=facts, topic_type=topic_type)
    generate_image(prompt, destination)
    manifest = {
        "source_url": source_url,
        "source_name": source_name,
        "published_at": published_at,
        "evidence_type": "gpt_news_visual",
        "is_primary_source": bool(is_primary_source),
        "facts_primary_source": bool(is_primary_source),
        "facts_verified": True,
        "generated_image": True,
        "visual_role": "attention_visual",
        "textless": True,
        "no_brand_or_logo": True,
    }
    destination.with_suffix(".source.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination
