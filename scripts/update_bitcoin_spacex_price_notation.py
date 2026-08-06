#!/usr/bin/env python3
"""指定記事のドル建て価格表記をサイト共通ルールへ更新する。"""

import logging
import os

from price_formatting import format_usd_prices
from wp_poster import WordPressAPI


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

TARGET_SLUG = "bitcoin-spacex-stock-unlock"


def normalized_fields(post: dict) -> dict:
    """タイトル・本文・抜粋の価格表記だけを正規化した更新フィールドを返す。"""
    current_title = post.get("title", {}).get("raw", "")
    current_content = post.get("content", {}).get("raw", "")
    current_excerpt = post.get("excerpt", {}).get("raw", "")
    fields = {}

    title = format_usd_prices(current_title, for_title=True)
    content = format_usd_prices(current_content, for_title=False)
    excerpt = format_usd_prices(current_excerpt, for_title=False)
    if title != current_title:
        fields["title"] = title
    if content != current_content:
        fields["content"] = content
    if excerpt != current_excerpt:
        fields["excerpt"] = excerpt
    return fields


def main() -> None:
    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )
    posts = wp.get_posts_by_slugs([TARGET_SLUG])
    if len(posts) != 1:
        raise RuntimeError(f"対象記事が一意に取得できませんでした: {TARGET_SLUG} ({len(posts)}件)")

    post = posts[0]
    fields = normalized_fields(post)
    if not fields:
        logger.info("価格表記はすでに統一済みです: %s", post.get("link", ""))
        return

    result = wp.update_post(post["id"], **fields)
    logger.info("価格表記を更新: %s（更新フィールド: %s）", result.get("link", ""), ", ".join(fields))


if __name__ == "__main__":
    main()
