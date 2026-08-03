#!/usr/bin/env python3
"""既存公開記事に残るBingX招待コード・URLを安全に統一する。"""

import argparse
import logging
import os

from wp_poster import WordPressAPI


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

OLD_CODE = "XXCCJX"
NEW_CODE = "FIKYOA"
NEW_URL = "https://bingxdao.com/invite/FIKYOA/"

# 長いURLを先に置換し、その内部に含まれる旧コードを二重計上しない。
REPLACEMENTS = (
    ("https://bingx.com/en-us/account/register/?ref=XXCCJX", NEW_URL),
    ("https://bingxdao.com/invite/XXCCJX/", NEW_URL),
    ("https://bingxdao.com/invite/XXCCJX", NEW_URL),
    (OLD_CODE, NEW_CODE),
)


def replace_legacy_invite_references(content: str) -> tuple[str, int]:
    """本文内の旧BingX招待参照だけを置換し、置換件数を返す。冪等。"""
    updated = content
    replacements = 0
    for old, new in REPLACEMENTS:
        count = updated.count(old)
        if count:
            updated = updated.replace(old, new)
            replacements += count
    return updated, replacements


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="実際にWordPressへ更新する。指定しない場合は対象件数の確認のみ。",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )

    posts = wp.get_published_posts_with_content()
    affected_posts = 0
    replacements = 0

    for post in posts:
        content = post.get("content", {}).get("raw", "")
        updated, count = replace_legacy_invite_references(content)
        if not count:
            continue

        affected_posts += 1
        replacements += count
        logger.info(
            "%s post=%s slug=%s replacements=%s",
            "更新" if args.apply else "確認",
            post["id"],
            post.get("slug", ""),
            count,
        )
        if args.apply:
            wp.update_post_content(post["id"], updated)

    logger.info(
        "%s完了: 対象記事=%s件、置換=%s件、全公開記事=%s件",
        "更新" if args.apply else "確認",
        affected_posts,
        replacements,
        len(posts),
    )


if __name__ == "__main__":
    main()
