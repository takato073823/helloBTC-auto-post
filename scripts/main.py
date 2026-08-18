"""
自動記事投稿メインスクリプト
RUN_MODE=news（デフォルト）: ニュース記事 1本を自動公開
RUN_MODE=seo: SEO特集記事 1本を下書き保存
"""
import os
import json
import logging
import time
from html import escape
from pathlib import Path

from scraper import get_latest_articles, fetch_article_content, fetch_tweet_embed_html
from generator import (
    generate_article, generate_featured_image,
    generate_seo_article, generate_chart_image, get_seo_article_type,
    append_source_attribution, is_duplicate_seo_topic, normalize_swell_html,
    is_duplicate_news_topic, is_publishable_news, prepend_direct_answer, prepend_lead_heading,
    resolve_logo_brand,
)
from wp_poster import WordPressAPI
from x_poster import post_tweet

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

POSTED_URLS_FILE = Path(__file__).parent / "posted_urls.json"
ARTICLES_PER_RUN = 1  # 1回の実行で投稿する記事数（1日3回 × 1本 = 最大3本/日）
MIN_CONTENT_LENGTH = 200  # 最低限必要な記事本文の長さ


def load_posted_urls():
    if POSTED_URLS_FILE.exists():
        with open(POSTED_URLS_FILE, encoding="utf-8") as f:
            data = json.load(f)
            return set(data) if isinstance(data, list) else set()
    return set()


def save_posted_urls(urls):
    with open(POSTED_URLS_FILE, "w", encoding="utf-8") as f:
        # 最新 500 件のみ保持（無限に増えないよう制限）
        url_list = list(urls)[-500:]
        json.dump(url_list, f, indent=2, ensure_ascii=False)


def main():
    # 環境変数から認証情報を取得
    wp_url = os.environ["WP_URL"]
    wp_username = os.environ["WP_USERNAME"]
    wp_app_password = os.environ["WP_APP_PASSWORD"]
    # OPENAI_API_KEY は記事生成時に使用する。

    wp = WordPressAPI(wp_url, wp_username, wp_app_password)
    posted_urls = load_posted_urls()
    recent_news_titles = wp.get_recent_published_titles(days=30)

    # 「ニュース」カテゴリの ID を取得（なければ自動作成）
    news_category_id = wp.get_or_create_category("ニュース")
    logger.info(f"カテゴリ「ニュース」ID: {news_category_id}")

    logger.info("最新ニュースを取得中...")
    candidates = get_latest_articles(count=30)
    new_articles = [a for a in candidates if a["url"] not in posted_urls]
    logger.info(f"未投稿記事: {len(new_articles)} 件")

    if not new_articles:
        logger.info("投稿する新しい記事がありません")
        return

    posted_count = 0
    failure_count = 0
    for article in new_articles:
        if posted_count >= ARTICLES_PER_RUN:
            break

        url = article["url"]
        title = article["title"]
        logger.info(f"処理中: {title[:70]}")

        try:
            # 元記事の本文とツイートURLを取得
            article_data = fetch_article_content(url)
            content = article_data["text"]
            tweet_urls = article_data["tweet_urls"]

            if len(content) < MIN_CONTENT_LENGTH:
                logger.warning(f"本文が短すぎるためスキップ: {url}")
                posted_urls.add(url)  # 再試行しないようにスキップ済みとして記録
                continue

            # OpenAI で日本語記事を生成
            logger.info("記事を生成中...")
            generated = generate_article(
                title=title,
                content=content,
                source_url=url,
                source_name=article.get("source", ""),
                tweet_urls=tweet_urls,
            )

            if not is_publishable_news(generated):
                logger.warning(
                    "独自価値または一次根拠が不足しているため公開を見送ります: %s",
                    generated.get("unique_value", "未記載"),
                )
                posted_urls.add(url)
                save_posted_urls(posted_urls)
                continue

            if is_duplicate_news_topic(generated["title"], recent_news_titles):
                logger.warning("Google Newsでの重複評価を避けるため公開を見送ります")
                posted_urls.add(url)
                save_posted_urls(posted_urls)
                continue

            # ツイートプレースホルダーを埋め込みカードHTMLに置換
            article_content = generated["content"]
            for i, tweet_url in enumerate(tweet_urls, 1):
                placeholder = "{TWEET_" + str(i) + "}"
                if placeholder in article_content:
                    embed_html = fetch_tweet_embed_html(tweet_url)
                    article_content = article_content.replace(placeholder, embed_html)
            # 未置換のプレースホルダーを除去
            import re as _re
            article_content = _re.sub(r"\{TWEET_\d+\}", "", article_content)
            # 投稿タイトルのコピーではない h2 を、本文の最上部に必ず置く。
            generated["content"] = prepend_lead_heading(
                article_content, generated["title"], generated.get("lead_heading")
            )
            generated["content"] = prepend_direct_answer(
                generated["content"], generated.get("direct_answer") or generated.get("excerpt")
            )
            generated["content"] = append_source_attribution(
                generated["content"], article.get("source", ""), url
            )

            logo_brand, logo_domain = resolve_logo_brand(
                generated["title"], generated.get("tags"),
                generated.get("logo_brand"), generated.get("logo_domain"),
            )

            # アイキャッチ画像を生成してアップロード
            featured_media_id = None
            featured_image_url = None
            try:
                image_data = generate_featured_image(
                    image_prompt=generated.get("image_prompt", ""),
                    tags=generated.get("tags", []),
                    logo_brand=logo_brand,
                    logo_domain=logo_domain,
                    article_title=generated["title"],
                    article_content=generated["content"],
                )
                featured_media_id, featured_image_url = wp.upload_media(
                    image_data, filename=f"featured-{int(time.time())}.jpg"
                )
            except Exception as e:
                logger.warning(f"画像生成/アップロード失敗（記事投稿は続行）: {e}")

            # WordPress に投稿
            result = wp.post_article(
                title=generated["title"],
                content=generated["content"],
                excerpt=generated["excerpt"],
                meta_description=generated.get("meta_description") or generated.get("excerpt", ""),
                tags=generated.get("tags", []),
                category_id=news_category_id,
                featured_media_id=featured_media_id,
                slug=generated.get("slug"),
                featured_image_url=featured_image_url,
                article_section="ニュース",
            )

            # X (Twitter) に投稿
            article_url = result.get("link", "")
            if article_url:
                post_tweet(
                    title=generated["title"],
                    article_url=article_url,
                    tags=generated.get("tags", []),
                    tweet_bullets=generated.get("tweet_bullets"),
                    article_section="ニュース",
                    featured_image_url=featured_image_url,
                    attach_featured_image_if_og_missing=True,
                )

            posted_urls.add(url)
            save_posted_urls(posted_urls)
            recent_news_titles.insert(0, generated["title"])
            posted_count += 1
            logger.info(f"投稿完了 ({posted_count}/{ARTICLES_PER_RUN}): {generated['title']}")

            time.sleep(2)  # API レート制限対策

        except Exception as e:
            failure_count += 1
            logger.error(f"処理失敗 ({url}): {e}")
            error_text = str(e).lower()
            if "insufficient_quota" in error_text or "current quota" in error_text:
                raise RuntimeError(
                    "OpenAI APIの利用残高がありません。"
                    "OpenAI PlatformのBilling画面でクレジットを追加してください。"
                ) from e
            continue

    logger.info(f"完了。今回 {posted_count} 件投稿しました。")
    if posted_count == 0 and failure_count > 0:
        raise RuntimeError(
            f"未投稿のまま {failure_count} 件の処理が失敗しました。"
            "GitHub Actionsのログを確認してください。"
        )


def _make_img_html(media_url: str, alt: str = "", media_id: int = 0) -> str:
    """WordPress 向けの figure+img タグを生成する"""
    return (
        f'<figure class="wp-block-image size-full">'
        f'<img src="{media_url}" alt="{escape(alt)}" class="wp-image-{media_id}"/>'
        f"</figure>"
    )


def _make_chart_html(media_url: str, caption: str, media_id: int = 0) -> str:
    return (
        f'<figure class="wp-block-image size-full">'
        f'<img src="{media_url}" alt="{escape(caption)}" class="wp-image-{media_id}"/>'
        f'<figcaption class="wp-element-caption">{escape(caption)}</figcaption>'
        f"</figure>"
    )


def run_seo_article():
    """SEO特集記事を1本生成して WordPress に下書き保存する"""
    wp_url = os.environ["WP_URL"]
    wp_username = os.environ["WP_USERNAME"]
    wp_app_password = os.environ["WP_APP_PASSWORD"]

    wp = WordPressAPI(wp_url, wp_username, wp_app_password)

    article_type = get_seo_article_type()
    logger.info(f"SEO記事を生成中 (カテゴリ: {article_type})...")

    generated = generate_seo_article(article_type)
    logger.info(f"生成タイトル: {generated['title']}")

    existing_titles = wp.get_published_titles()
    if is_duplicate_seo_topic(
        generated.get("primary_topic", ""), generated["title"], existing_titles
    ):
        logger.warning("既存記事との競合を避けるため、今回のSEO記事は投稿しません")
        return

    content = normalize_swell_html(generated["content"])
    ts = int(time.time())

    # ── アイキャッチ画像 ─────────────────────────────────
    featured_media_id = None
    featured_image_url = None
    try:
        img_data = generate_featured_image(
            image_prompt=generated.get("featured_image_prompt", ""),
            article_title=generated["title"], article_content=generated["content"],
        )
        featured_media_id, featured_image_url = wp.upload_media(img_data, filename=f"seo-featured-{ts}.jpg")
    except Exception as e:
        logger.warning(f"アイキャッチ生成失敗（続行）: {e}")

    # ── 記事内画像 1 ──────────────────────────────────────
    img1_prompts = generated.get("article_image_prompts", [])
    img1_html = ""
    try:
        prompt1 = img1_prompts[0] if img1_prompts else "glowing cryptocurrency network digital art"
        img1_data = generate_featured_image(
            image_prompt=prompt1, article_title=generated["title"], article_content=generated["content"],
        )
        img1_id, img1_url = wp.upload_media(img1_data, filename=f"seo-img1-{ts}.jpg")
        img1_html = _make_img_html(
            img1_url, f"{generated['title']}に関する解説画像", img1_id
        )
    except Exception as e:
        logger.warning(f"記事内画像1 生成失敗（続行）: {e}")

    # ── 記事内画像 2 ──────────────────────────────────────
    img2_html = ""
    try:
        prompt2 = img1_prompts[1] if len(img1_prompts) > 1 else "blockchain technology abstract visualization"
        img2_data = generate_featured_image(
            image_prompt=prompt2, article_title=generated["title"], article_content=generated["content"],
        )
        img2_id, img2_url = wp.upload_media(img2_data, filename=f"seo-img2-{ts}.jpg")
        img2_html = _make_img_html(
            img2_url, f"{generated['title']}に関する参考画像", img2_id
        )
    except Exception as e:
        logger.warning(f"記事内画像2 生成失敗（続行）: {e}")

    # ── グラフ画像 ────────────────────────────────────────
    chart_html = ""
    try:
        chart_data = generated.get("chart", {})
        if chart_data:
            chart_bytes = generate_chart_image(chart_data)
            chart_id, chart_url = wp.upload_media(chart_bytes, filename=f"seo-chart-{ts}.jpg")
            chart_html = _make_chart_html(
                chart_url,
                chart_data.get("caption", "データグラフ"),
                chart_id,
            )
    except Exception as e:
        logger.warning(f"グラフ生成失敗（続行）: {e}")

    # ── プレースホルダーを実際の HTML に差し替え ───────────
    content = content.replace("{IMAGE_1}", img1_html)
    content = content.replace("{IMAGE_2}", img2_html)
    content = content.replace("{CHART}", chart_html)
    content = normalize_swell_html(content)

    # ── WordPress にカテゴリ取得 → 下書きで投稿 ─────────────
    category_id = wp.get_or_create_category(article_type)
    logger.info(f"カテゴリ「{article_type}」ID: {category_id}")

    result = wp.post_article(
        title=generated["title"],
        content=content,
        excerpt=generated.get("excerpt", ""),
        tags=generated.get("tags", []),
        category_id=category_id,
        featured_media_id=featured_media_id,
        # SEO特集は品質・正確性を確認してから公開する。
        status="draft",
        slug=generated.get("slug"),
        featured_image_url=featured_image_url,
        article_section=article_type,
    )
    logger.info(f"SEO記事を下書き保存しました: {result.get('link', '')}")


if __name__ == "__main__":
    run_mode = os.environ.get("RUN_MODE", "news")
    if run_mode == "seo":
        run_seo_article()
    else:
        main()
