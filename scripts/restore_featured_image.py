#!/usr/bin/env python3
"""WordPressメディアライブラリ内の既存画像をアイキャッチへ戻す。"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from replace_featured_image import update_schema_image
from wp_poster import WordPressAPI


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def validate_restore_target(base_url: str, media_id: int, media_url: str) -> None:
    """別サイトや不正なメディア指定への復元を防ぐ。"""
    base = urlparse(base_url)
    target = urlparse(media_url)
    if media_id <= 0:
        raise ValueError("MEDIA_IDは正の整数で指定してください")
    if target.scheme not in {"http", "https"} or target.netloc != base.netloc:
        raise ValueError("MEDIA_URLは対象WordPressと同じサイトを指定してください")
    if "/wp-content/uploads/" not in target.path:
        raise ValueError("MEDIA_URLはWordPressメディアライブラリの画像を指定してください")


def main() -> None:
    base_url = os.environ["WP_URL"].rstrip("/")
    slug = os.environ["POST_SLUG"].strip()
    media_id = int(os.environ["MEDIA_ID"])
    media_url = os.environ["MEDIA_URL"].strip()
    validate_restore_target(base_url, media_id, media_url)

    wp = WordPressAPI(
        base_url,
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )
    media = wp._request("GET", f"media/{media_id}", params={"context": "edit"})
    actual_url = media.get("source_url", "")
    if actual_url != media_url:
        raise RuntimeError(
            f"メディアIDとURLが一致しません: id={media_id}, actual={actual_url}"
        )

    posts = wp.get_posts_by_slugs([slug])
    if len(posts) != 1:
        raise RuntimeError(f"対象記事が一意に取得できませんでした: {slug} ({len(posts)}件)")
    post = posts[0]
    content = update_schema_image(post.get("content", {}).get("raw", ""), media_url)
    result = wp.update_post(post["id"], featured_media=media_id, content=content)
    logger.info("アイキャッチ復元完了: %s", result.get("link", ""))
    logger.info("復元画像: %s", media_url)


if __name__ == "__main__":
    main()
