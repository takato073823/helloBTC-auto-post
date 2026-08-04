"""INUが扱う時事投稿の系統と、画像・確認ルールを一元管理する。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContentTypePolicy:
    key: str
    label: str
    visual_route: str
    review_mode: str
    requires_primary_source: bool
    priority: int
    requires_source_media: bool = False


def _policy(
    key: str,
    label: str,
    visual_route: str,
    review_mode: str,
    requires_primary_source: bool,
    priority: int,
    *,
    requires_source_media: bool = False,
) -> ContentTypePolicy:
    return ContentTypePolicy(
        key=key,
        label=label,
        visual_route=visual_route,
        review_mode=review_mode,
        requires_primary_source=requires_primary_source,
        priority=priority,
        requires_source_media=requires_source_media,
    )


CONTENT_TYPES = {
    policy.key: policy
    for policy in (
        _policy("breaking_news", "公式速報", "official_text_crop", "hybrid", True, 100),
        _policy("security_incident", "セキュリティ速報", "official_text_crop", "manual", True, 100),
        _policy("crypto_market", "仮想通貨価格急変", "live_chart", "auto", False, 95),
        _policy("trending_token", "話題トークン価格", "live_chart", "hybrid", False, 88),
        _policy("us_stock", "米国株価格急変", "live_chart", "hybrid", False, 88),
        _policy("jp_stock", "日本株価格急変", "live_chart", "hybrid", False, 88),
        _policy("market_microstructure", "出来高・流動性・デリバティブ", "official_data_crop", "hybrid", True, 86),
        _policy("etf_flow", "ETF資金フロー", "official_data_crop", "hybrid", True, 86),
        _policy("institutional_flow", "機関投資家の資金フロー", "official_data_crop", "hybrid", True, 84),
        _policy("onchain", "オンチェーン", "official_data_crop", "hybrid", True, 84),
        _policy("whale_treasury", "クジラ・企業保有", "official_data_crop", "hybrid", True, 84),
        _policy("earnings", "決算・業績", "official_data_crop", "hybrid", True, 82),
        _policy("supply_event", "アンロック・需給イベント", "official_text_crop", "hybrid", True, 80),
        _policy("adoption_kpi", "利用者・決済・TVL", "official_data_crop", "hybrid", True, 78),
        _policy("policy_household", "政策・生活への影響", "official_text_crop", "hybrid", True, 78),
        _policy("macro_event", "経済イベント予告・結果", "official_data_crop", "hybrid", True, 82),
        _policy("macro_geopolitics", "地政学・マクロ", "official_text_crop", "manual", True, 84),
        _policy("developing_story", "続報・条件変更", "official_text_crop", "hybrid", True, 90),
        _policy("historical_milestone", "史上最高・歴史的水準", "live_chart", "hybrid", False, 76),
        _policy("cross_asset", "複数資産の比較・乖離", "live_chart", "hybrid", False, 76),
        _policy("timeline_explainer", "出来事の時系列", "gpt_timeline", "manual", True, 74),
        _policy(
            "translation_quote",
            "海外投稿の翻訳引用",
            "manual_quote_with_source_media",
            "manual",
            True,
            74,
            requires_source_media=True,
        ),
        _policy(
            "x_reaction",
            "Xで話題の投稿への見解",
            "manual_quote_with_source_media",
            "manual",
            True,
            72,
            requires_source_media=True,
        ),
        _policy("public_figure_statement", "著名人の発言", "official_text_crop", "manual", True, 80),
        _policy("ai_comparison", "AIを使った比較・検証", "gpt_explainer", "manual", True, 68),
        _policy("market_meme", "金融ネタ・ミーム", "gpt_creative", "manual", False, 30),
        _policy("campaign", "提携キャンペーン", "gpt_creative", "manual", False, 20),
    )
}


def get_content_policy(topic_type: str) -> ContentTypePolicy:
    try:
        return CONTENT_TYPES[topic_type]
    except KeyError as exc:
        raise ValueError(f"投稿対象外の系統です: {topic_type}") from exc

