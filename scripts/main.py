"""
自動記事投稿メインスクリプト
NewsNow から最新ニュースを取得 → Claude で日本語記事生成 → WordPress に投稿
"""
import os
import json
import logging
import time
from pathlib import Path

from scraper import get_latest_articles, fetch_article_content
from generator import generate_article, generate_featured_image
from wp_poster import WordPressAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

POSTED_URLS_FILE = Path(__file__).parent / "posted_urls.json"
ARTICLES_PER_RUN = 4  # 一時的に4本（通常は1）
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
    # ANTHROPIC_API_KEY は anthropic ライブラリが自動で読み込む

    wp = WordPressAPI(wp_url, wp_username, wp_app_password)
    posted_urls = load_posted_urls()

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
    for article in new_articles:
        if posted_count >= ARTICLES_PER_RUN:
            break

        url = article["url"]
        title = article["title"]
        logger.info(f"処理中: {title[:70]}")

        try:
            # 元記事の本文を取得
            content = fetch_article_content(url)
            if len(content) < MIN_CONTENT_LENGTH:
                logger.warning(f"本文が短すぎるためスキップ: {url}")
                posted_urls.add(url)  # 再試行しないようにスキップ済みとして記録
                continue

            # Claude で日本語記事を生成
            logger.info("記事を生成中...")
            generated = generate_article(
                title=title,
                content=content,
                source_url=url,
                source_name=article.get("source", ""),
            )

            # アイキャッチ画像を生成してアップロード
            featured_media_id = None
            try:
                image_data = generate_featured_image(
                    image_prompt=generated.get("image_prompt", ""),
                    tags=generated.get("tags", []),
                )
                featured_media_id = wp.upload_media(image_data, filename=f"featured-{int(time.time())}.jpg")
            except Exception as e:
                logger.warning(f"画像生成/アップロード失敗（記事投稿は続行）: {e}")

            # WordPress に投稿
            wp.post_article(
                title=generated["title"],
                content=generated["content"],
                excerpt=generated["excerpt"],
                tags=generated.get("tags", []),
                category_id=news_category_id,
                featured_media_id=featured_media_id,
            )

            posted_urls.add(url)
            save_posted_urls(posted_urls)
            posted_count += 1
            logger.info(f"投稿完了 ({posted_count}/{ARTICLES_PER_RUN}): {generated['title']}")

            time.sleep(2)  # API レート制限対策

        except Exception as e:
            logger.error(f"処理失敗 ({url}): {e}")
            continue

    logger.info(f"完了。今回 {posted_count} 件投稿しました。")


if __name__ == "__main__":
    main()
