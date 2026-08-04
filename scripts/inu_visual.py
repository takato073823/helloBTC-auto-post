"""投稿の内容から、最適な証拠画像の形式を選ぶ。"""

from __future__ import annotations

from dataclasses import dataclass


TIMELY_TOPIC_TYPES = {
    "crypto_market",
    "trending_token",
    "us_stock",
    "jp_stock",
    "macro_geopolitics",
    "etf_flow",
    "onchain",
    "x_reaction",
    "breaking_news",
    "security_incident",
    "campaign",
}

EVIDENCE_ROUTES = {
    "crypto_market": "live_chart",
    "trending_token": "live_chart",
    "us_stock": "live_chart",
    "jp_stock": "live_chart",
    "macro_geopolitics": "source_crop",
    "etf_flow": "source_data",
    "onchain": "source_data",
    "x_reaction": "native_quote",
    "breaking_news": "source_crop",
    "security_incident": "gpt_timeline",
    "campaign": "gpt_creative",
}


@dataclass(frozen=True)
class VisualDecision:
    route: str
    reason: str
    gpt_image_allowed: bool


def select_visual_route(topic_type: str, *, needs_timeline: bool = False) -> VisualDecision:
    if topic_type not in TIMELY_TOPIC_TYPES:
        return VisualDecision("reject", "基礎知識・不明な題材は投稿対象外", False)
    if needs_timeline:
        return VisualDecision("gpt_timeline", "複数の出来事を1枚で説明する", True)
    route = EVIDENCE_ROUTES[topic_type]
    return VisualDecision(
        route,
        {
            "live_chart": "価格と値動きは実データで示す",
            "source_data": "ETF・オンチェーンは出典付きデータを示す",
            "native_quote": "X上の発言はネイティブ引用で文脈を残す",
            "source_crop": "速報は一次資料の重要箇所を示す",
            "gpt_timeline": "時系列はオリジナル図解で整理する",
            "gpt_creative": "キャンペーンはGPT Imageで視認性を作る",
        }[route],
        route.startswith("gpt_"),
    )


def build_gpt_image_prompt(
    *,
    visual_type: str,
    headline: str,
    key_points: list[str],
    visual_direction: str = "",
) -> str:
    """SOUや参考画像を複製せず、INU固有の画像仕様を作る。"""
    if visual_type not in {"gpt_timeline", "gpt_creative", "gpt_explainer"}:
        raise ValueError("GPT Imageの対象外です")
    points = "\n".join(f"- {point}" for point in key_points[:6])
    return f"""
Use case: infographic-diagram
Asset type: X post image, portrait 4:5
Primary request: Create an original Japanese editorial visual for INU. Do not copy any existing influencer, layout, mascot, or brand campaign.
Topic: {headline}
Required information:\n{points}
Visual structure: {visual_type}. {visual_direction}
Style/medium: polished modern editorial infographic or story illustration; strong hierarchy; easy to understand on a phone; visually expressive, not a generic website card.
Composition/framing: portrait 4:5; one clear focal point; short headline; information grouped into large readable sections; generous spacing.
Color palette: warm off-white, charcoal, Bitcoin orange accents, one topic-specific accent color. Avoid using the same blue campaign look as the references.
Text: Japanese only. Keep wording exactly as supplied. Use large legible type and minimal text.
Constraints: original composition; factual and neutral; no invented prices, percentages, dates, quotes, charts, company logos, trademarks, QR codes, or watermarks. Do not add information that was not supplied. No dog character unless explicitly requested.
Avoid: HTML/CSS dashboard appearance, black news-card template, dense paragraphs, tiny footnotes, visual imitation of SOU, BingX, or the supplied examples.
""".strip()

