"""重大リスク投稿用の、強いが誤認を招かないオリジナル画像を作る。"""

from __future__ import annotations

from importlib.util import find_spec
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from inu_gpt_image import generate_image


ALLOWED_THEMES = {
    "exchange_shutdown": "a digital asset exchange service going dark and users protecting access to their funds",
    "withdrawal_halt": "a crypto withdrawal gateway closing while a user urgently secures their assets",
    "hack": "a serious cyber breach threatening digital assets while defensive security activates",
    "custody_risk": "the danger of concentrating all digital assets in one custody provider",
}

DEFAULT_HEADLINES = {
    "exchange_shutdown": "取引所が止まる前に\n見直すこと。",
    "withdrawal_halt": "出金できなくなる前に\n備えること。",
    "hack": "全資産を失う前に\n見直すこと。",
    "custody_risk": "預け先をひとつにする\nその前に。",
}

BADGE_LABELS = {
    "exchange_shutdown": "SERVICE ALERT",
    "withdrawal_halt": "WITHDRAWAL ALERT",
    "hack": "SECURITY ALERT",
    "custody_risk": "ASSET SAFETY",
}

LANDSCAPE_SIZE = "1536x1024"


def build_risk_alert_prompt(theme: str) -> str:
    if theme not in ALLOWED_THEMES:
        raise ValueError(f"重大リスク画像の対象外です: {theme}")
    return f"""
Use case: editorial illustration for a Japanese financial-news post on X
Canvas: landscape 3:2, 1536x1024
Situation: {ALLOWED_THEMES[theme]}
Primary request: Create an original, emotionally powerful risk-warning scene that stops the scroll. The image should communicate urgency, loss, uncertainty, and the need to act carefully without depicting a confirmed victim or making a specific allegation.
Composition: cinematic editorial illustration; one clear human focal point on the right half; abstract exchange or custody infrastructure failing in the background; strong depth; leave a dark, uncluttered area across the left half for a large Japanese headline that will be added later by code.
Palette: vivid magenta, hot pink, deep violet, near-black shadows, a small amount of cold cyan light; high contrast; dramatic rim lighting.
Style: premium contemporary financial editorial art, polished 3D/cinematic realism, expressive but not gruesome, highly legible at phone size.
Strict constraints: no text, letters, numbers, words, logos, trademarks, exchange names, coins with brand marks, app screens, charts, price data, watermarks, signatures, mascots, or identifiable real people. Do not copy the composition, character, typography, or exact palette placement of any reference image or influencer post.
Avoid: generic corporate stock art, website-card layouts, HTML dashboards, comedy, celebration, weapons, gore, fire covering the whole scene, or a centered subject that blocks the headline area.
""".strip()


def _font_path() -> Path:
    spec = find_spec("japanize_matplotlib")
    roots = list(spec.submodule_search_locations or []) if spec else []
    if not roots:
        raise RuntimeError("日本語画像用フォントがありません")
    path = Path(roots[0]) / "fonts" / "ipaexg.ttf"
    if not path.is_file():
        raise RuntimeError("日本語画像用フォントファイルがありません")
    return path


def apply_risk_alert_overlay(
    source_path: str | Path,
    output_path: str | Path,
    *,
    headline: str,
    badge_label: str = "SECURITY ALERT",
) -> Path:
    clean = headline.strip()
    if not clean or len(clean) > 42:
        raise ValueError("画像見出しは1〜42文字にしてください")

    with Image.open(source_path) as opened:
        base = ImageOps.fit(
            opened.convert("RGB"),
            (1536, 864),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    base = ImageEnhance.Contrast(base).enhance(1.08)

    shade = Image.new("L", base.size, 0)
    shade_draw = ImageDraw.Draw(shade)
    for x in range(base.width):
        strength = max(0, int(238 * (1 - x / (base.width * 0.72))))
        shade_draw.line((x, 0, x, base.height), fill=strength)
    black = Image.new("RGB", base.size, "#08050B")
    base = Image.composite(black, base, shade.filter(ImageFilter.GaussianBlur(22)))

    draw = ImageDraw.Draw(base)
    label_font = ImageFont.truetype(str(_font_path()), 30)
    headline_font = ImageFont.truetype(str(_font_path()), 94)
    label_box = draw.textbbox((0, 0), badge_label, font=label_font)
    label_width = label_box[2] - label_box[0]
    draw.rounded_rectangle(
        (92, 92, 184 + label_width, 154),
        radius=18,
        fill="#F10070",
    )
    draw.ellipse((116, 111, 136, 131), fill="white")
    draw.text((154, 104), badge_label, font=label_font, fill="white")

    lines: list[str] = []
    for paragraph in clean.splitlines():
        current = ""
        for character in paragraph:
            candidate = current + character
            width = draw.textbbox((0, 0), candidate, font=headline_font)[2]
            if current and width > 760:
                lines.append(current)
                current = character
            else:
                current = candidate
        if current:
            lines.append(current)
    if len(lines) > 3:
        raise ValueError("画像見出しは3行以内に収まる長さにしてください")

    y = 230
    for line in lines:
        draw.text(
            (92, y),
            line,
            font=headline_font,
            fill="white",
            stroke_width=2,
            stroke_fill="#120811",
        )
        y += 118

    draw.rectangle((92, 704, 620, 712), fill="#F10070")
    sub_font = ImageFont.truetype(str(_font_path()), 34)
    draw.text((92, 738), "事実を確認し、資産を守る。", font=sub_font, fill="#F2DDE8")

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    base.save(destination, "PNG", optimize=True)
    return destination


def generate_risk_alert_visual(
    *,
    theme: str,
    headline: str | None,
    output_path: str | Path,
    prompt_path: str | Path | None = None,
) -> str:
    prompt = build_risk_alert_prompt(theme)
    if prompt_path:
        path = Path(prompt_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(prompt + "\n", encoding="utf-8")
    destination = Path(output_path)
    raw_path = destination.with_name(f"{destination.stem}.raw.png")
    try:
        generate_image(
            prompt,
            raw_path,
            size=LANDSCAPE_SIZE,
            quality="medium",
        )
        apply_risk_alert_overlay(
            raw_path,
            destination,
            headline=headline or DEFAULT_HEADLINES[theme],
            badge_label=BADGE_LABELS[theme],
        )
    finally:
        raw_path.unlink(missing_ok=True)
    return prompt
