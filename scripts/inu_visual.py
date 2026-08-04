"""投稿の内容から、最適な証拠画像の形式を選ぶ。"""

from __future__ import annotations

from dataclasses import dataclass

from inu_content_types import CONTENT_TYPES, get_content_policy

TIMELY_TOPIC_TYPES = set(CONTENT_TYPES)
EVIDENCE_ROUTES = {key: policy.visual_route for key, policy in CONTENT_TYPES.items()}


@dataclass(frozen=True)
class VisualDecision:
    route: str
    reason: str
    gpt_image_allowed: bool
    review_mode: str = "manual"


def select_visual_route(topic_type: str, *, needs_timeline: bool = False) -> VisualDecision:
    if topic_type not in TIMELY_TOPIC_TYPES:
        return VisualDecision("reject", "基礎知識・不明な題材は投稿対象外", False)
    policy = get_content_policy(topic_type)
    if needs_timeline:
        return VisualDecision("gpt_timeline", "複数の出来事を1枚で説明する", True, "manual")
    route = policy.visual_route
    return VisualDecision(
        route,
        {
            "market_service_screenshot": "価格は出典表示のある実サービス画面で示す",
            "official_data_crop": "見出し・軸・単位・出典を残した公式データを示す",
            "manual_quote_with_source_media": "元画像を含むX投稿を手動引用して文脈を残す",
            "official_text_crop": "発信元と日付を確認できる一次資料の重要部分を示す",
            "gpt_timeline": "時系列はオリジナル図解で整理する",
            "gpt_creative": "キャンペーンはGPT Imageで視認性を作る",
            "gpt_explainer": "確認済みの情報だけをオリジナル図解で整理する",
            "gpt_risk_alert": "重大リスクは、確認済み事実と強い警戒ビジュアルを分離して伝える",
        }[route],
        route.startswith("gpt_"),
        policy.review_mode,
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
