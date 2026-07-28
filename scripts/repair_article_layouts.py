#!/usr/bin/env python3
"""公開済み記事の未閉鎖HTMLを補正する保守スクリプト。

SWELLのcap-blockが閉じられないと、後続の本文が色付きボックス内に入って
しまう。公開済み記事をraw HTMLで取得し、実際に崩れている記事だけを更新する。
"""
import logging
import os

# このスクリプトはClaudeを呼び出さないが、generatorのクライアント初期化には
# 環境変数が必要なため、未設定時だけダミー値を使う。
os.environ.setdefault("ANTHROPIC_API_KEY", "not-used-by-layout-repair")

from generator import normalize_swell_html, prepend_lead_heading
from wp_poster import WordPressAPI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BITMART_SLUG = "bitmart-exchange-shutdown-2026"
BITMART_HEADING = "BitMartが取引所事業を段階的に終了、8月26日に全取引を停止"


def main():
    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )
    updated = 0
    checked = 0

    for post in wp.get_published_posts_with_content():
        checked += 1
        raw = post.get("content", {}).get("raw", "")
        if not raw:
            continue

        repaired = normalize_swell_html(raw)
        if post.get("slug") == BITMART_SLUG:
            repaired = prepend_lead_heading(repaired, post["title"]["raw"], BITMART_HEADING)

        if repaired == raw:
            continue

        wp.update_post_content(post["id"], repaired)
        updated += 1
        logger.info("本文レイアウトを修正: %s", post.get("slug"))

    logger.info("レイアウト保守完了: 確認=%d件、修正=%d件", checked, updated)


if __name__ == "__main__":
    main()
