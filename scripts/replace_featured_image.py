#!/usr/bin/env python3
"""Google AI Studioで既存記事のアイキャッチだけを差し替える。"""

import logging
import os
import re
import time

from generator import generate_featured_image
from wp_poster import WordPressAPI


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def update_schema_image(content: str, image_url: str) -> str:
    """本文先頭のNewsArticleスキーマにある画像URLも同期する。"""
    updated, count = re.subn(
        r'("image":\{"@type":"ImageObject","url":")[^"]+',
        lambda match: match.group(1) + image_url,
        content,
        count=1,
    )
    if count == 0:
        logger.warning("NewsArticleスキーマに画像URLがないため、アイキャッチのみ更新します")
    return updated


def main() -> None:
    slug = os.environ["POST_SLUG"].strip()
    image_prompt = os.environ["IMAGE_PROMPT"].strip()
    tags = [tag.strip() for tag in os.getenv("IMAGE_TAGS", "").split(",") if tag.strip()]
    logo_brand = os.getenv("LOGO_BRAND", "").strip() or None
    logo_domain = os.getenv("LOGO_DOMAIN", "").strip() or None

    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )
    posts = wp.get_posts_by_slugs([slug])
    if len(posts) != 1:
        raise RuntimeError(f"対象記事が一意に取得できませんでした: {slug} ({len(posts)}件)")

    post = posts[0]
    image_data = generate_featured_image(
        image_prompt=image_prompt,
        tags=tags,
        logo_brand=logo_brand,
        logo_domain=logo_domain,
        article_title=post.get("title", {}).get("raw", ""),
        article_content=post.get("content", {}).get("raw", ""),
    )
    media_id, image_url = wp.upload_media(
        image_data,
        filename=f"featured-replaced-{slug}-{int(time.time())}.jpg",
    )
    content = update_schema_image(post["content"]["raw"], image_url)
    result = wp.update_post(post["id"], featured_media=media_id, content=content)
    logger.info("アイキャッチ更新完了: %s", result.get("link", ""))
    logger.info("新しい画像: %s", image_url)


if __name__ == "__main__":
    main()
