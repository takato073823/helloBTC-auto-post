#!/usr/bin/env python3
"""
速報記事の単発公開スクリプト（手動トリガー専用）

検証済みのファクトだけを Claude に渡して日本語ニュース記事を生成し、
ニュースと同じ導線（アイキャッチ生成 → WordPress 公開 → X 投稿）で公開する。
数値・固有名詞のハルシネーションを防ぐため、本文の事実は FACTS のみを根拠にする。

使い方:
  RUN_MODE 不要。環境変数（WP_*, ANTHROPIC_API_KEY, GOOGLE_API_KEY, X_*）を
  設定した上で `python breaking_news.py` を実行（GitHub Actions の workflow_dispatch 経由）。
"""
import json
import logging
import os
import re
import time

import anthropic

from generator import generate_featured_image
from wp_poster import WordPressAPI
from x_poster import post_tweet

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 検証済みファクト（2026-06-25 時点・複数ソースで確認）
#   出典: Yahoo Finance / CoinStats / Intellectia / TradingKey
# 本文の数値・固有名詞はこの範囲のみを根拠とする（推測の数値を足さない）。
# ---------------------------------------------------------------------------
HEADLINE = "ビットコインが6万ドル割れ、仮想通貨が全面急落"

FACTS = """
- 2026年6月25日、ビットコイン(BTC)は1BTC=6万ドルを割り込み、一時59,334ドルまで下落した（始値は約60,983ドル、前日比約2.7%安）。
- これは2025年10月に記録した史上最高値・約126,200ドルから約51%下落した水準。
- イーサリアム(ETH)も1,700ドル付近まで下落し、主要アルトコインも軒並み下落。
- BTCは24時間で約1.9%安、週間で約4.5%安。米ナスダックの約2.2%下落と連動した。
- 直近30日間の資金流出は約63.5億ドル規模で、現物ビットコインETF登場以降で最大級の機関投資家の解約(資金引き揚げ)の波と報じられている。
- 下落の主な要因として指摘されているもの:
  (1) 米国とイランの地政学的緊張の高まりによるインフレ懸念と、米FRBの利下げ先送り観測。
  (2) 大口保有企業によるビットコイン売却の市場噂。
  (3) 現物ビットコインETFからの資金流出。
  (4) 米国の暗号資産規制法案（CLARITY法）の審議遅延観測。
  (5) 資金がAI関連株などへ移動した後、そのAI関連株も急落（約10%安）し、リスク資産全体が売られた。
"""

SOURCES_NOTE = "（出典: Yahoo Finance、CoinStats、Intellectia、TradingKey などの報道を基に編集部が作成）"


def generate_breaking_article() -> dict:
    client = anthropic.Anthropic()

    prompt = f"""あなたはSEOに強い仮想通貨専門の日本人ニュースライターです。helloBTC向けに「仮想通貨急落」の速報記事を作成してください。

【記事の骨子（見出し相当）】
{HEADLINE}

【記事に使ってよい事実（数値・固有名詞はこの範囲のみを根拠にする。ここに無い数値を創作しない）】
{FACTS}

【ライティングルール】
- 文体: 「〜した」「〜だ」「〜である」の常体（丁寧語禁止）。冷静で客観的なニュース調。
- ターゲット: 仮想通貨初心者〜中級者の日本人。
- 本文: 1500〜2200文字。
- 構成: リード文（何が起きたか3〜4行で要約）→ h3見出し4〜5個（値動きの詳細 / 急落の背景・要因 / 市場への影響 / 今後の注目点 / 投資家が今意識すべきこと）。
- パニックを煽らない。投資判断は自己責任であることを促し、特定の売買を推奨しない（YMYL配慮）。
- 急落時こそ重要な「リスク管理」「余裕資金」「長期視点」「ドルコスト平均法(DCA)」などに簡潔に触れてよい。
- 見出しはh3タグ。本文中の重要ポイントは <div style="background:#fff3e0;border-left:4px solid #ff9800;padding:14px 18px;margin:20px 0;border-radius:4px;">…</div> の注意ボックスで強調してよい。
- 記事の最後に出典注記を入れる: <p style="font-size:0.85em;color:#888;">{SOURCES_NOTE}</p>
- さらに末尾に: <p style="font-size:0.85em;color:#888;">※本記事は情報提供を目的としたものであり、特定の暗号資産の売買を推奨するものではありません。投資は自己責任で、余裕資金の範囲内で行ってください。</p>

必ず以下のJSONのみ出力（前後に余計なテキスト不要）:
{{
  "title": "SEO最適化された日本語タイトル（35〜60文字、具体的・数字を含む。例: ビットコイン6万ドル割れ…）",
  "content": "<HTML記事本文>",
  "excerpt": "記事の要約（100〜150文字、絵文字や記号は使わない）",
  "tags": ["ビットコイン","暗号資産","相場","急落","BTC"],
  "slug": "btc-crypto-crash-june-2026",
  "image_prompt": "英語の画像生成プロンプト（下落チャートを連想させる抽象的・報道写真風。テキストや人物は含めない）",
  "tweet_bullets": ["要点1（20字前後）","要点2","要点3"]
}}"""

    last_err = None
    for attempt in range(1, 4):
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        if resp.stop_reason == "max_tokens":
            last_err = "max_tokensで打ち切り"
            logger.warning(f"  リトライ {attempt}/3: {last_err}")
            continue
        text = resp.content[0].text.strip()
        start, end = text.find("{"), text.rfind("}") + 1
        if start == -1 or end <= start:
            last_err = "JSONが見つからない"
            logger.warning(f"  リトライ {attempt}/3: {last_err}")
            continue
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError as e:
            last_err = e
            logger.warning(f"  リトライ {attempt}/3: JSONパース失敗 ({e})")
    raise RuntimeError(f"記事生成に3回失敗: {last_err}")


def main():
    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )

    logger.info("速報記事を生成中...")
    article = generate_breaking_article()
    logger.info(f"生成タイトル: {article['title']}")

    # アイキャッチ画像（失敗しても公開は続行）
    featured_media_id = None
    featured_image_url = None
    try:
        img = generate_featured_image(
            image_prompt=article.get("image_prompt", "falling red cryptocurrency price chart, dramatic market crash, dark"),
            tags=article.get("tags", []),
        )
        featured_media_id, featured_image_url = wp.upload_media(
            img, filename=f"breaking-{int(time.time())}.jpg"
        )
        logger.info(f"アイキャッチ: {featured_image_url}")
    except Exception as e:
        logger.warning(f"画像生成/アップロード失敗（続行）: {e}")

    category_id = wp.get_or_create_category("ニュース")

    result = wp.post_article(
        title=article["title"],
        content=article["content"],
        excerpt=article["excerpt"],
        tags=article.get("tags", []),
        category_id=category_id,
        featured_media_id=featured_media_id,
        status="publish",
        slug=article.get("slug"),
        featured_image_url=featured_image_url,
        article_section="ニュース",
    )

    article_url = result.get("link", "")
    logger.info(f"=== 公開完了: {article_url} ===")

    if article_url:
        post_tweet(
            title=article["title"],
            article_url=article_url,
            tags=article.get("tags", []),
            tweet_bullets=article.get("tweet_bullets"),
            article_section="ニュース",
        )


if __name__ == "__main__":
    main()
