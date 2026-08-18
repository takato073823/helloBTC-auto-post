"""Google Newsの透明性要件に必要な編集方針ページと投稿者情報を整える。"""

import logging
import os

from wp_poster import WordPressAPI


logger = logging.getLogger(__name__)

EDITORIAL_POLICY_SLUG = "about-hellobtc-editorial-policy"
EDITORIAL_POLICY_TITLE = "helloBTCについて・編集方針"
EDITORIAL_POLICY_EXCERPT = (
    "helloBTCの運営目的、ニュースの収集・AI利用・出典確認・訂正・広告掲載に関する編集方針です。"
)
AUTHOR_DESCRIPTION = (
    "helloBTC編集部の編集アカウントです。暗号資産・ブロックチェーンのニュースを日本の読者向けに整理し、"
    "出典、事実と編集部の解釈、投資上の注意点を明確にして掲載します。"
)

EDITORIAL_POLICY_CONTENT = """
<!-- wp:paragraph -->
<p>helloBTCは、暗号資産・ブロックチェーンに関するニュースと解説を、日本の読者が事実関係と注意点を確認しやすい形で提供する情報サイトです。</p>
<!-- /wp:paragraph -->

<!-- wp:heading -->
<h2 class="wp-block-heading">運営・発行主体</h2>
<!-- /wp:heading -->
<!-- wp:paragraph -->
<p>記事はhelloBTC編集部が発行します。記事ページには公開日時、更新日時、執筆アカウント、参照した情報源を明示します。</p>
<!-- /wp:paragraph -->

<!-- wp:heading -->
<h2 class="wp-block-heading">ニュース記事の作成方法</h2>
<!-- /wp:heading -->
<!-- wp:list {"ordered":true} -->
<ol class="wp-block-list"><li>信頼できる報道機関や公式発表から候補を収集します。</li><li>元情報で確認できる事実を抽出し、日本の読者への影響と用語解説を加えます。</li><li>下書き作成にAIを使用する場合があります。</li><li>公開前に、類似記事、出典URL、根拠のない数値、事実と編集部見解の混同を自動検査します。</li></ol>
<!-- /wp:list -->

<!-- wp:heading -->
<h2 class="wp-block-heading">出典・引用方針</h2>
<!-- /wp:heading -->
<!-- wp:paragraph -->
<p>ニュース記事には、読者が内容を確認できる出典リンクを掲載します。数値、仕様、発言は参照先で確認できる範囲に限定し、確認できない情報を推測で補いません。編集部による分析は、確認できた事実と区別して記載します。</p>
<!-- /wp:paragraph -->

<!-- wp:heading -->
<h2 class="wp-block-heading">訂正・更新方針</h2>
<!-- /wp:heading -->
<!-- wp:paragraph -->
<p>掲載後に誤りが判明した場合は内容を訂正し、重要な変更では更新日時を表示します。記事を新しく見せる目的だけで公開日時を変更することはありません。訂正のご連絡は<a href="https://hellobtc.jp/%e3%81%8a%e5%95%8f%e3%81%84%e5%90%88%e3%82%8f%e3%81%9b/">お問い合わせページ</a>から受け付けます。</p>
<!-- /wp:paragraph -->

<!-- wp:heading -->
<h2 class="wp-block-heading">広告・アフィリエイトについて</h2>
<!-- /wp:heading -->
<!-- wp:paragraph -->
<p>一部のページにはアフィリエイトリンクや広告が含まれる場合があります。広告・提携関係がある場合でも、ニュース本文では確認できる事実と編集部の評価を区別します。広告掲載の詳細は<a href="https://hellobtc.jp/%e5%ba%83%e5%91%8a%e6%8e%b2%e8%bc%89%e3%81%ab%e3%81%a4%e3%81%84%e3%81%a6/">広告掲載について</a>をご確認ください。</p>
<!-- /wp:paragraph -->

<!-- wp:heading -->
<h2 class="wp-block-heading">免責事項</h2>
<!-- /wp:heading -->
<!-- wp:paragraph -->
<p>helloBTCの記事は情報提供を目的とし、投資助言ではありません。暗号資産には価格変動、制度変更、サービス停止などのリスクがあります。最終的な判断はご自身の責任で行ってください。</p>
<!-- /wp:paragraph -->
""".strip()


def apply_visibility_repairs(wp: WordPressAPI) -> dict:
    page = wp.upsert_page(
        slug=EDITORIAL_POLICY_SLUG,
        title=EDITORIAL_POLICY_TITLE,
        content=EDITORIAL_POLICY_CONTENT,
        excerpt=EDITORIAL_POLICY_EXCERPT,
    )
    page_url = page.get("link") or f"{wp.base_url}/{EDITORIAL_POLICY_SLUG}/"
    wp.update_current_user_profile(url=page_url, description=AUTHOR_DESCRIPTION)
    return {"page_url": page_url, "user_updated": True}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )
    result = apply_visibility_repairs(wp)
    logger.info("編集方針ページと投稿者情報を更新しました: %s", result["page_url"])


if __name__ == "__main__":
    main()
