"""INUニュース投稿の主画像を、根拠画像とは分けて用意する。"""

from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageOps

from inu_gpt_image import generate_image


USER_AGENT = "Mozilla/5.0 (compatible; INUNewsVisual/1.0)"
MIN_IMAGE_SIZE = (640, 360)
MAX_IMAGE_PIXELS = 3_000_000
PORTRAIT_SIZE = (1200, 1500)
# 一目で媒体を特定できる編集ビジュアルは、投稿の主画像に再利用しない。
# 根拠のスクリーンショットは別添で保持し、主画像はINU独自の生成ビジュアルにする。
BLOCKED_EDITORIAL_VISUAL_DOMAINS = {"cointelegraph.com"}

# ニュース主体は、AIにロゴや人物を描かせず、検証可能な方法で識別する。
# 機関名はロゴではないINU独自のプレーンテキストラベルとして合成する。
# 実在人物は一次ソースの記事画像だけを使い、生成画像への置き換えを禁止する。
INSTITUTION_SUBJECTS = (
    {
        "key": "sec",
        "label": "SEC",
        "descriptor": "U.S. REGULATOR",
        "aliases": ("米証券取引委員会", "u.s. securities and exchange commission", " sec"),
    },
    {
        "key": "cftc",
        "label": "CFTC",
        "descriptor": "U.S. REGULATOR",
        "aliases": ("米商品先物取引委員会", "commodity futures trading commission", "cftc"),
    },
    {
        "key": "federal_reserve",
        "label": "FED",
        "descriptor": "U.S. CENTRAL BANK",
        "aliases": ("米連邦準備制度", "federal reserve", "frb", "fomc"),
    },
    {
        "key": "us_treasury",
        "label": "U.S. TREASURY",
        "descriptor": "U.S. GOVERNMENT",
        "aliases": ("米財務省", "u.s. treasury", "us treasury"),
    },
    {
        "key": "boj",
        "label": "BOJ",
        "descriptor": "CENTRAL BANK",
        "aliases": ("日本銀行", "bank of japan", "日銀"),
    },
    {
        "key": "fsa_japan",
        "label": "FSA JAPAN",
        "descriptor": "FINANCIAL REGULATOR",
        "aliases": ("金融庁", "financial services agency japan"),
    },
)

PUBLIC_FIGURE_SUBJECTS = (
    {
        "key": "donald_trump",
        "label": "Donald Trump",
        "aliases": ("トランプ", "donald trump", "president trump"),
    },
    {
        "key": "jerome_powell",
        "label": "Jerome Powell",
        "aliases": ("パウエル", "jerome powell", "chair powell"),
    },
    {
        "key": "paul_atkins",
        "label": "Paul Atkins",
        "aliases": ("ポール・アトキンス", "paul atkins"),
    },
    {
        "key": "michael_saylor",
        "label": "Michael Saylor",
        "aliases": ("マイケル・セイラー", "michael saylor"),
    },
    {
        "key": "elon_musk",
        "label": "Elon Musk",
        "aliases": ("イーロン・マスク", "elon musk"),
    },
    {
        "key": "larry_fink",
        "label": "Larry Fink",
        "aliases": ("ラリー・フィンク", "larry fink"),
    },
)


def identify_visual_subject(*, hook: str, source_name: str = "") -> dict | None:
    """見出しの主語を、人物優先で明示的な画像要件へ変換する。"""
    hook_text = f" {hook.lower()} "
    source_text = f" {source_name.lower()} "
    for subject in PUBLIC_FIGURE_SUBJECTS:
        if any(alias.lower() in hook_text for alias in subject["aliases"]):
            return {
                "kind": "public_figure",
                "key": subject["key"],
                "label": subject["label"],
                "identity_method": "verified_primary_source_photo",
            }
    combined = hook_text + source_text
    for subject in INSTITUTION_SUBJECTS:
        if any(alias.lower() in combined for alias in subject["aliases"]):
            return {
                "kind": "institution",
                "key": subject["key"],
                "label": subject["label"],
                "descriptor": subject["descriptor"],
                "identity_method": "editorial_plain_text_label",
            }
    return None


def _uses_blocked_editorial_visual_style(source_url: str) -> bool:
    host = urlparse(source_url).netloc.lower().split(":", 1)[0]
    return any(host == domain or host.endswith(f".{domain}") for domain in BLOCKED_EDITORIAL_VISUAL_DOMAINS)


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
        # Xのタイムラインで横長画像が小さく見えないよう、主画像は4:5へ統一する。
        # 人物写真は顔が置かれやすい上寄り中央を残す。
        image = ImageOps.fit(
            image,
            PORTRAIT_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.42),
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path, format="PNG", optimize=True)


def capture_source_hero_image(
    *,
    source_url: str,
    source_name: str,
    published_at: str,
    output_path: str | Path,
    is_primary_source: bool,
    visual_subject: dict | None = None,
    session: requests.Session | None = None,
) -> Path:
    """出典ページ自身が示す主画像だけを、投稿1枚目用に保存する。

    検索結果の無関係な画像は使わず、記事または公式発表のOG画像に限定する。
    人物ニュースでは同記事に掲載された本人写真、企業ニュースでは公式ロゴや
    現場写真が自然に最優先になる。
    """
    if _uses_blocked_editorial_visual_style(source_url):
        raise ValueError("Cointelegraphの編集ビジュアルはニュース主画像として使いません")
    if visual_subject and visual_subject.get("kind") == "public_figure" and not is_primary_source:
        raise ValueError("実在人物の画像は一次ソースに掲載された実写写真だけを使います")

    requester = session or requests.Session()
    headers = {"User-Agent": USER_AGENT}
    page = requester.get(source_url, headers=headers, timeout=25)
    page.raise_for_status()
    image_url = _og_image_url(page.text, source_url)
    page_path = urlparse(source_url).path.lower()
    # 金融庁の広報誌「アクセスFSA」の表紙は記事固有の主画像ではなく、
    # ニュースの意味を伝えられない。根拠スクリーンショットは残し、
    # 主画像はテキストレスの生成ビジュアルへ切り替える。
    if urlparse(source_url).netloc.lower().endswith("fsa.go.jp") and "/access/" in page_path:
        raise ValueError("金融庁広報誌の表紙はニュース主画像として使いません")
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
        "subject_identifiable": bool(visual_subject),
        "visual_subject": visual_subject,
        "entity_identity_required": bool(visual_subject),
        "official_logo_used": False,
    }
    destination.with_suffix(".source.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination


def _editorial_prompt(
    *, hook: str, facts: list[str], topic_type: str, visual_subject: dict | None = None
) -> str:
    facts_text = "\n".join(f"- {fact}" for fact in facts[:2])
    regulatory_direction = (
        "For a regulation or government-policy event, show a photorealistic U.S. financial-regulation scene: "
        "an official federal building or hearing-room atmosphere, a close-up of formal policy documents, "
        "and subtle digital-asset technology cues. Do not fabricate an SEC logo or readable legal text."
        if topic_type == "regulatory_rule_change"
        else ""
    )
    identity_direction = ""
    if visual_subject and visual_subject.get("kind") == "institution":
        identity_direction = (
            "Leave a clean high-contrast region in the upper-left for a verified editorial agency label "
            "that will be added after generation. Do not draw the label, seal, or logo yourself."
        )
    return f"""
Use case: ads-marketing
Asset type: primary image for a timely Japanese X news post, portrait 4:5
Primary request: Create an original, high-impact editorial news visual for this verified event.
Topic: {hook}
Verified context (for visual direction only, do not render words or numbers):
{facts_text}
Topic type: {topic_type}
Scene/backdrop: a specific, contemporary business or technology scene that communicates the event at a glance.
Style/medium: photorealistic premium financial-news editorial photography, natural material detail, sophisticated and credible at phone size. No illustration or 3D-rendered look.
Composition/framing: portrait 4:5, one decisive focal point, strong depth and contrast, generous clean negative space. It must feel like a news image, not a web card or infographic.
Topic-specific direction: {regulatory_direction} {identity_direction}
Text: no text at all.
Constraints: do not generate letters, words, numbers, charts, interface screens, company logos, trademarks, watermarks, signatures, or a likeness of a real person. Do not invent a claim beyond the verified context. This image is an attention visual; the source evidence will be attached separately.
Avoid: generic stock-photo office scenes, HTML dashboards, black breaking-news template, cryptocurrency coins unless directly essential, mascots, illustration, CGI, copied influencer layouts, and imitation of any reference image or a recognizable publisher illustration style (including Cointelegraph-style crypto editorial art).
""".strip()


def _font(size: int, *, bold: bool) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        if bold
        else "/System/Library/Fonts/Supplemental/Arial.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _apply_institution_label(image_path: Path, subject: dict) -> None:
    """公式ロゴを転載せず、ニュース主体を大きな文字で明示する。"""
    with Image.open(image_path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    width, height = image.size
    draw = ImageDraw.Draw(image, "RGBA")
    left = int(width * 0.055)
    top = int(height * 0.055)
    bottom = int(height * 0.235)
    label = str(subject["label"])
    descriptor = str(subject.get("descriptor", "NEWS SUBJECT"))
    label_size = max(48, int(width * (0.085 if len(label) <= 8 else 0.052)))
    label_font = _font(label_size, bold=True)
    descriptor_font = _font(max(18, int(width * 0.021)), bold=True)
    label_box = draw.textbbox((0, 0), label, font=label_font)
    descriptor_box = draw.textbbox((0, 0), descriptor, font=descriptor_font)
    text_width = max(label_box[2] - label_box[0], descriptor_box[2] - descriptor_box[0])
    right = min(int(width * 0.92), left + text_width + int(width * 0.105))
    radius = max(24, int(width * 0.028))
    draw.rounded_rectangle(
        (left, top, right, bottom),
        radius=radius,
        fill=(255, 255, 255, 242),
        outline=(23, 31, 43, 210),
        width=max(2, int(width * 0.004)),
    )
    accent_width = max(8, int(width * 0.012))
    draw.rounded_rectangle(
        (left, top, left + accent_width, bottom),
        radius=max(4, radius // 3),
        fill=(37, 99, 235, 255),
    )
    draw.text(
        (left + int(width * 0.045), top + int(height * 0.052)),
        label,
        font=label_font,
        fill=(10, 15, 24, 255),
    )
    draw.text(
        (left + int(width * 0.047), top + int(height * 0.025)),
        descriptor,
        font=descriptor_font,
        fill=(75, 85, 99, 255),
    )
    image.save(image_path, format="PNG", optimize=True)


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
    visual_subject: dict | None = None,
) -> Path:
    """主画像がない場合だけ、事実を増やさないテキストレスの生成画像を作る。"""
    destination = Path(output_path)
    subject = visual_subject or identify_visual_subject(hook=hook, source_name=source_name)
    if subject and subject.get("kind") == "public_figure":
        raise ValueError("実在人物ニュースはAI画像にせず、一次ソースの実写写真を使います")
    prompt = _editorial_prompt(
        hook=hook,
        facts=facts,
        topic_type=topic_type,
        visual_subject=subject,
    )
    generate_image(prompt, destination)
    if subject and subject.get("kind") == "institution":
        _apply_institution_label(destination, subject)
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
        "subject_identifiable": bool(subject),
        "visual_subject": subject,
        "entity_identity_required": bool(subject),
        "official_logo_used": False,
    }
    destination.with_suffix(".source.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return destination
