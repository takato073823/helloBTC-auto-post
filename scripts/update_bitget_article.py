#!/usr/bin/env python3
"""公開済みのBitget記事に公式ソースと修正済みアイキャッチを反映する。"""
import logging
import os
import re
import time

from generator import generate_featured_image
from wp_poster import WordPressAPI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SLUG = "bitget-japan-service-exit-2026"
OFFICIAL_SOURCE_MARKER = "hellobtc-official-sources"
OFFICIAL_SOURCES_HTML = """<section class="hellobtc-official-sources" style="background:#f5fbfc;border-left:4px solid #08a6b5;padding:16px 20px;margin:24px 0;border-radius:4px;">
<strong>公式ソース</strong>
<ul style="margin:10px 0 0;padding-left:1.3em;">
<li><a href="https://www.bitget.com/ja/support/articles/12560603890270" target="_blank" rel="noopener noreferrer">重要なお知らせ：日本居住者の皆様への重要なご案内（Bitget公式）</a></li>
<li><a href="https://www.bitget.com/ja/support/articles/12560603890271" target="_blank" rel="noopener noreferrer">FAQ - 日本居住者の皆様への重要なお知らせ（Bitget公式）</a></li>
</ul>
</section>"""


def add_official_sources(content: str) -> str:
    """リード直後に、公式発表とFAQへのリンクを一度だけ挿入する。"""
    if OFFICIAL_SOURCE_MARKER in content:
        return content
    updated, count = re.subn(
        r"(<p>.*?</p>)",
        lambda match: match.group(1) + "\n" + OFFICIAL_SOURCES_HTML,
        content,
        count=1,
        flags=re.DOTALL,
    )
    if count != 1:
        raise ValueError("記事本文に公式ソースを差し込む位置が見つかりません")
    return updated


def update_schema_image(content: str, image_url: str) -> str:
    """本文先頭のNewsArticleスキーマに登録済みの画像URLも同期する。"""
    updated, _ = re.subn(
        r'("image":\{"@type":"ImageObject","url":")[^"]+',
        lambda match: match.group(1) + image_url,
        content,
        count=1,
    )
    return updated


def main():
    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )
    posts = wp.get_posts_by_slugs([SLUG])
    if len(posts) != 1:
        raise RuntimeError(f"対象記事が一意に取得できませんでした: {SLUG} ({len(posts)}件)")

    post = posts[0]
    content = add_official_sources(post["content"]["raw"])
    fields = {"content": content}

    try:
        image_data = generate_featured_image(
            image_prompt="dark cryptocurrency trading terminal with subtle Japanese city lights, cinematic news photography, no text, no people",
            tags=["Bitget", "仮想通貨取引所", "暗号資産", "日本居住者"],
            logo_brand="Bitget",
            logo_domain="bitget.com",
        )
        media_id, image_url = wp.upload_media(
            image_data, filename=f"bitget-featured-updated-{int(time.time())}.jpg"
        )
        fields["featured_media"] = media_id
        fields["content"] = update_schema_image(content, image_url)
        logger.info("修正済みアイキャッチをアップロード: %s", image_url)
    except Exception as err:
        logger.warning("アイキャッチ更新に失敗したため本文のみ更新: %s", err)

    result = wp.update_post(post["id"], **fields)
    logger.info("記事更新完了: %s", result.get("link", ""))


if __name__ == "__main__":
    main()
