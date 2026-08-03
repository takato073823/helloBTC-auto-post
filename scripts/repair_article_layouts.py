#!/usr/bin/env python3
"""公開済み記事の未閉鎖HTMLを補正する保守スクリプト。

SWELLのcap-blockが閉じられないと、後続の本文が色付きボックス内に入って
しまう。公開済み記事をraw HTMLで取得し、実際に崩れている記事だけを更新する。
"""
import logging
import os

from generator import normalize_swell_html, prepend_lead_heading
from wp_poster import WordPressAPI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BITMART_SLUG = "bitmart-exchange-shutdown-2026"
BITMART_HEADING = "BitMartが取引所事業を段階的に終了、8月26日に全取引を停止"
SOLANA_BROKEN_SLUG = "solana-sol-blockchain-guide-2026-3"


def clean_solana_article() -> str:
    """崩れたSolana記事を、SWELL依存のボックスを使わない有効な本文へ置換する。"""
    return """<!-- wp:paragraph -->
<p>Solana（SOL）は、高い処理性能と比較的低い取引コストを目指して設計されたブロックチェーンである。暗号資産そのものだけでなく、分散型金融（DeFi）やNFT、アプリケーションの基盤としても利用されている。本記事では、仕組みの基本、利用時に確認したい点、リスクを初心者向けに整理する。</p>
<!-- /wp:paragraph -->
<!-- wp:heading -->
<h2 class="wp-block-heading">Solanaとは何か</h2>
<!-- /wp:heading -->
<!-- wp:paragraph -->
<p>Solanaは、スマートコントラクトを実行できるレイヤー1ブロックチェーンである。レイヤー1とは、他のチェーンに依存せず、取引の記録や検証を自ら行うネットワークを指す。SOLはネットワークの取引手数料やステーキングなどに使われる暗号資産だ。</p>
<!-- /wp:paragraph -->
<!-- wp:heading -->
<h2 class="wp-block-heading">高速処理を支える仕組み</h2>
<!-- /wp:heading -->
<!-- wp:paragraph -->
<p>Solanaでは、取引の順序や時間の記録を効率化するProof of History（PoH）と、保有量に応じて検証を担うProof of Stake（PoS）を組み合わせている。複数の処理を並行して扱える設計も特徴であり、アプリケーションの利用者が増えた場面でも処理を進めやすくすることを目指している。</p>
<!-- /wp:paragraph -->
<!-- wp:heading -->
<h2 class="wp-block-heading">利用・購入前に確認したいこと</h2>
<!-- /wp:heading -->
<!-- wp:list -->
<ul class="wp-block-list"><li>利用する国内取引所やウォレットがSOLと送金ネットワークに対応しているか</li><li>送金先アドレスとネットワークを入力前に再確認できているか</li><li>手数料、出金条件、本人確認の要件が最新の公式案内と一致しているか</li></ul>
<!-- /wp:list -->
<!-- wp:paragraph -->
<p>暗号資産の送金は、誤ったアドレスや異なるネットワークを指定すると取り戻せない場合がある。操作前には取引所・ウォレットの公式ヘルプを確認し、少額での確認を含めて慎重に進めたい。</p>
<!-- /wp:paragraph -->
<!-- wp:heading -->
<h2 class="wp-block-heading">Solanaに関する主なリスク</h2>
<!-- /wp:heading -->
<!-- wp:paragraph -->
<p>SOLの価格は大きく変動する可能性があり、ネットワーク障害、規制の変更、アプリケーションや取引所の安全性なども利用時のリスクとなる。将来の価格や収益を前提に判断せず、失っても生活に影響しない範囲で扱うことが重要である。</p>
<!-- /wp:paragraph -->
<!-- wp:heading -->
<h2 class="wp-block-heading">まとめ：Solanaの基本と注意点</h2>
<!-- /wp:heading -->
<!-- wp:paragraph -->
<p>Solanaは、処理性能を重視したブロックチェーンの一つである。技術的な特徴だけでなく、利用するサービスの条件、送金先、セキュリティ、価格変動リスクを確認したうえで、自分に合う利用方法を判断したい。</p>
<!-- /wp:paragraph -->
<!-- wp:paragraph -->
<p style="font-size:0.85em;color:#888;">※本記事は一般的な情報提供を目的としており、投資助言ではありません。利用する取引所・ウォレットの公式情報と最新の条件を確認したうえで、ご自身の判断で行動してください。</p>
<!-- /wp:paragraph -->"""


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
            repaired = clean_solana_article()

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
