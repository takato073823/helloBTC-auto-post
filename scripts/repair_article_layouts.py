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
SOLANA_BROKEN_SLUG = "solana-sol-blockchain-guide-2026-3"


def replace_broken_solana_summary(content: str) -> str:
    """二重化して本文を囲むSolana記事末尾のcap-blockを安全な通常HTMLへ置換する。"""
    marker = '<div class="swell-block-capbox cap_box is-style-onborder_ttl"'
    start = content.rfind(marker)
    if start == -1:
        return content

    tail = content[start:]
    if "📌 本記事のまとめ" not in tail:
        return content

    clean_summary = """<!-- wp:separator -->
<hr class="wp-block-separator has-alpha-channel-opacity" />
<!-- /wp:separator -->
<!-- wp:heading -->
<h2 class="wp-block-heading">まとめ：Solanaの基本と注意点</h2>
<!-- /wp:heading -->
<!-- wp:paragraph -->
<p>Solanaは高速な処理と比較的低い手数料を特徴とするブロックチェーンである。一方で、暗号資産の利用や保有には価格変動、サービス条件、セキュリティなどの確認が欠かせない。</p>
<!-- /wp:paragraph -->
<!-- wp:paragraph -->
<p style="font-size:0.85em;color:#888;">※本記事は一般的な情報提供を目的としており、投資助言ではありません。利用する取引所・ウォレットの公式情報と最新の条件を確認したうえで、ご自身の判断で行動してください。</p>
<!-- /wp:paragraph -->"""
    return content[:start] + clean_summary


def main():
    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )
    updated = 0
    checked = 0
    failed = 0

    for post in wp.get_published_posts_with_content():
        checked += 1
        raw = post.get("content", {}).get("raw", "")
        if not raw:
            continue

        repaired = normalize_swell_html(raw)
        if post.get("slug") == BITMART_SLUG:
            repaired = prepend_lead_heading(repaired, post["title"]["raw"], BITMART_HEADING)
        if post.get("slug") == SOLANA_BROKEN_SLUG:
            repaired = replace_broken_solana_summary(repaired)
            repaired = normalize_swell_html(repaired)

        if repaired == raw:
            continue

        try:
            wp.update_post_content(post["id"], repaired)
            updated += 1
            logger.info("本文レイアウトを修正: %s", post.get("slug"))
        except Exception as e:
            # 一部の旧記事が壊れていても、他の記事の修正を中断させない。
            failed += 1
            logger.error("本文修正をスキップ: %s (%s)", post.get("slug"), e)

    logger.info("レイアウト保守完了: 確認=%d件、修正=%d件、保留=%d件", checked, updated, failed)


if __name__ == "__main__":
    main()
