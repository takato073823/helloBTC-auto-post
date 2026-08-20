#!/usr/bin/env python3
"""Xのクリエイター報酬とステーブルコイン検討報道を単発公開する。"""

import logging
import os
import time
from pathlib import Path

from generator import prepend_lead_heading
from wp_poster import WordPressAPI
from x_poster import post_tweet


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

COINDESK_REPORT = (
    "https://www.coindesk.com/ja/business/2026/08/20/"
    "elon-musk-s-x-is-exploring-stablecoins-to-pay-influencers-and-content-providers"
)
X_CREATORS_POST = "https://x.com/XCreators/status/2085835082166653393"
X_REVENUE_HELP = "https://help.x.com/en/using-x/creator-revenue-sharing"
X_MONEY_OFFICIAL = "https://money.x.com/en"
CIRCLE_USDC_OFFICIAL = "https://www.circle.com/usdc"

SLUG = "x-stablecoin-creator-payouts-2026"
FEATURED_IMAGE = (
    Path(__file__).resolve().parent.parent
    / "assets/featured/x-stablecoin-creator-payouts-2026.jpg"
)


def x_stablecoin_article() -> dict:
    """報道と公式資料を区別した、編集済みニュース記事を返す。"""
    return {
        "title": "X、クリエイター報酬にステーブルコイン検討か　USDC候補と報道",
        "lead_heading": "Xがクリエイター支払いへのステーブルコイン活用を協議中と報道",
        "content": f"""<p>イーロン・マスク氏が所有するSNS「X」が、クリエイターやコンテンツ提供者への報酬支払いにステーブルコインを活用する方法を検討していると報じられた。CoinDeskが8月20日、計画を知る関係者の話として伝えたもので、Circleが発行するUSDCなどが候補に挙がっているという。</p>
<p>一方、Xはステーブルコイン導入を公式には発表していない。CoinDeskによると協議は継続中で、同社から直ちにコメントは得られなかった。導入の可否、開始時期、対象国、利用するブロックチェーン、受取方法はいずれも現時点で未確定だ。</p>

<div style="background:#eef6ff;border-left:4px solid #1976d2;padding:14px 18px;margin:20px 0;border-radius:4px;"><strong>現時点の整理：</strong>確認できたのは、関係者情報に基づく「検討・協議中」との報道だ。X公式がUSDCの採用や提供開始を発表した事実はなく、正式決定として扱う段階ではない。</div>

<h3>USDCなどを使った報酬支払いを協議中と報道</h3>
<p><a href="{COINDESK_REPORT}" target="_blank" rel="noopener noreferrer">CoinDeskの報道</a>によると、Xは影響力のあるユーザーやコンテンツ提供者に対するロイヤリティの支払いで、USDCなどのステーブルコインを利用する方向を協議している。情報源は、他のソーシャルメディアでもクリエイター報酬へのステーブルコイン利用を試している関係者とされる。</p>
<p>ただし、具体的な提携先や決済事業者、採用通貨、対応ネットワークは明らかにされていない。「USDCが候補」と「USDC採用が決定」は意味が異なるため、今後のXまたはCircleによる正式発表を待つ必要がある。</p>

<h3>Xはクリエイター報酬制度を全面刷新</h3>
<p>Xは8月8日、<a href="{X_CREATORS_POST}" target="_blank" rel="noopener noreferrer">クリエイター向け公式アカウント</a>を通じて「Original Content Rewards Program」を発表した。独自のアイデア、専門知識、報道、創造性、コメントを提供するクリエイターを評価し、条件を満たす表示回数に基づいて報酬を支払う新制度だ。</p>
<p><a href="{X_REVENUE_HELP}" target="_blank" rel="noopener noreferrer">X公式ヘルプ</a>では、従来のCreator Revenue Sharingへの新規登録を8月7日に停止し、既存参加者の報酬算定を9月7日まで継続すると案内している。9月8日からは、既存参加者が新制度へ申請できるよう段階的にアクセスを広げる予定だ。</p>
<p>公式発表にはステーブルコインへの言及がない。今回の報道は、報酬の算定制度とは別に、実際の支払い手段を拡張する可能性を示すものと位置付けられる。</p>

<h3>現在の支払い経路はStripeまたは対象者のX Money</h3>
<p>X公式ヘルプによると、従来の収益分配を受け取る際は、Stripeの支払いアカウントまたは対象者向けのX Moneyアカウントを接続する。本人確認も必要とされている。ステーブルコインが加わる場合、既存の法定通貨経路を置き換えるのか、任意の受取方法として追加するのかが焦点になる。</p>
<p><a href="{X_MONEY_OFFICIAL}" target="_blank" rel="noopener noreferrer">X Money公式サイト</a>では、同サービスを米国の一部利用者へ段階的に提供していると説明する。X Paymentsは銀行ではなく、銀行サービスはCross River Bankなどが担う。現段階でX Money自体が世界中のクリエイターに利用可能なわけではないため、越境報酬をどう届けるかはXにとって重要な課題だ。</p>

<h3>なぜステーブルコインが候補になるのか</h3>
<p>ステーブルコインは、法定通貨などに連動する安定した価値を目指す暗号資産だ。<a href="{CIRCLE_USDC_OFFICIAL}" target="_blank" rel="noopener noreferrer">Circleの公式説明</a>によると、USDCは米ドルと1対1で償還できるよう設計され、流通額と同額以上の現金・流動性の高い現金同等資産で裏付けられている。</p>
<p>ブロックチェーン上で24時間送付できるため、多数の国にいるクリエイターへの支払いを迅速化できる可能性がある。銀行送金の対応時間や中継銀行、少額報酬の手数料といった摩擦を減らせる点は、グローバルなプラットフォームにとって利点になり得る。</p>
<p>ただし、これは一般的な仕組みから考えられる編集部の分析であり、Xが示した導入理由ではない。また、ステーブルコインにも発行体、ウォレット管理、ブロックチェーン障害、送付先の入力ミス、価格連動のずれなどのリスクがある。</p>

<h3>対象国・受取方法・税務対応が今後の焦点</h3>
<p>実用化には、国ごとの暗号資産規制、本人確認、制裁対応、税務処理、法定通貨への換金方法を整理する必要がある。複数のネットワークで同名の資産を扱う場合は、対応チェーンを誤った送金の防止策も欠かせない。</p>
<p>クリエイター側にとっては、ステーブルコイン受取が任意か必須か、手数料を誰が負担するか、受領時点の評価額をどう記録するかが重要になる。日本居住者を対象にするかどうかも発表されておらず、日本での利用可否や税務上の取扱いを現時点で断定することはできない。</p>

<h3>まとめ</h3>
<p>Xがクリエイター報酬にUSDCなどのステーブルコインを活用する方向で協議していると報じられた。Xはクリエイター報酬制度を刷新している最中だが、公式資料にはステーブルコイン採用の記載はなく、まだ検討段階とみられる。今後はXによる正式発表、採用通貨、対象国、対応ネットワーク、既存のStripe・X Moneyとの関係が注目される。</p>

<div style="background:#f7f7f7;padding:16px 18px;margin:24px 0;border-radius:4px;"><strong>参考・一次資料</strong><ul>
<li><a href="{COINDESK_REPORT}" target="_blank" rel="noopener noreferrer">CoinDesk：Xのステーブルコイン報酬検討に関する報道</a></li>
<li><a href="{X_CREATORS_POST}" target="_blank" rel="noopener noreferrer">X Creators：Original Content Rewards Programの公式発表</a></li>
<li><a href="{X_REVENUE_HELP}" target="_blank" rel="noopener noreferrer">Xヘルプ：Creator Revenue Sharing</a></li>
<li><a href="{X_MONEY_OFFICIAL}" target="_blank" rel="noopener noreferrer">X Money公式サイト</a></li>
<li><a href="{CIRCLE_USDC_OFFICIAL}" target="_blank" rel="noopener noreferrer">Circle：USDC公式情報</a></li>
</ul></div>
<p style="font-size:0.85em;color:#888;">※本記事は情報提供を目的としており、特定の暗号資産、サービス、投資行動を推奨するものではありません。サービス提供条件や税務上の取扱いは、今後の公式発表と居住地の法令を確認してください。</p>""",
        "excerpt": "Xがクリエイターやコンテンツ提供者への報酬にUSDCなどのステーブルコインを活用する方法を検討中と報じられた。Xの公式発表と報道を区別し、新報酬制度やX Moneyとの関係、今後の論点を整理する。",
        "meta_description": "Xがクリエイター報酬へのUSDCなどのステーブルコイン活用を検討中と報道。X公式の新報酬制度、現在の支払い方法、導入に向けた課題を一次資料とともに解説する。",
        "tags": ["X", "ステーブルコイン", "USDC", "クリエイターエコノミー", "暗号資産決済"],
        "slug": SLUG,
        "tweet_bullets": [
            "USDCなどの活用を協議中と報道",
            "X公式は導入をまだ発表せず",
            "新報酬制度との接続が焦点",
        ],
    }


def validate_article(article: dict) -> None:
    """検討報道を正式決定と誤認させないための公開前検証。"""
    content = article["content"]
    required = [
        COINDESK_REPORT,
        X_CREATORS_POST,
        X_REVENUE_HELP,
        X_MONEY_OFFICIAL,
        CIRCLE_USDC_OFFICIAL,
        "Xはステーブルコイン導入を公式には発表していない",
        "導入の可否、開始時期、対象国、利用するブロックチェーン、受取方法はいずれも現時点で未確定",
        "編集部の分析",
    ]
    missing = [item for item in required if item not in content]
    if missing:
        raise ValueError(f"公開必須情報が不足しています: {missing}")
    if "検討か" not in article["title"] or "と報道" not in article["title"]:
        raise ValueError("タイトルが検討段階の報道であることを示していません")
    prohibited = ["XはUSDCを採用した", "XがUSDC支払いを開始", "USDCで支払うと発表"]
    if any(term in content for term in prohibited):
        raise ValueError("未確認の正式採用を断定する表現があります")
    if not FEATURED_IMAGE.is_file():
        raise FileNotFoundError(f"承認済みアイキャッチが見つかりません: {FEATURED_IMAGE}")


def find_existing_article(wp: WordPressAPI) -> dict | None:
    """同一スラッグを失敗時も見逃さず、記事の二重公開を防ぐ。"""
    posts = wp._request(
        "GET",
        "posts",
        params={
            "slug": SLUG,
            "status": "any",
            "context": "edit",
            "_fields": "id,slug,link,title",
        },
    )
    return posts[0] if posts else None


def post_article_to_x(article: dict, article_url: str, featured_image_url: str | None = None) -> str:
    """記事URLをXへ投稿し、失敗を成功扱いにしない。"""
    tweet_id = post_tweet(
        title=article["title"],
        article_url=article_url,
        tags=article["tags"],
        tweet_bullets=article["tweet_bullets"],
        article_section="ニュース",
        featured_image_url=featured_image_url,
        attach_featured_image_if_og_missing=bool(featured_image_url),
    )
    if not tweet_id:
        raise RuntimeError("X投稿を完了できませんでした")
    return str(tweet_id)


def main() -> None:
    article = x_stablecoin_article()
    validate_article(article)
    article["content"] = prepend_lead_heading(
        article["content"], article["title"], article["lead_heading"]
    )

    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )
    existing = find_existing_article(wp)
    retry_x_only = os.getenv("RETRY_X_ONLY", "").strip().lower() in {"1", "true", "yes"}

    if retry_x_only:
        if not existing:
            raise RuntimeError("X再投稿の対象記事が見つかりません")
        tweet_id = post_article_to_x(article, existing["link"])
        logger.info("X再投稿完了: https://x.com/i/web/status/%s", tweet_id)
        return
    if existing:
        raise RuntimeError(f"同一スラッグの記事がすでに存在します: {existing['link']}")

    media_id, image_url = wp.upload_media(
        FEATURED_IMAGE.read_bytes(),
        filename=f"x-stablecoin-creator-payouts-{int(time.time())}.jpg",
    )
    category_id = wp.get_or_create_category("ニュース")
    result = wp.post_article(
        title=article["title"],
        content=article["content"],
        excerpt=article["excerpt"],
        meta_description=article["meta_description"],
        tags=article["tags"],
        category_id=category_id,
        featured_media_id=media_id,
        status="publish",
        slug=article["slug"],
        featured_image_url=image_url,
        article_section="ニュース",
    )

    article_url = result.get("link", "")
    if not article_url:
        raise RuntimeError("WordPressが公開記事URLを返しませんでした")
    logger.info("記事公開完了: %s", article_url)
    tweet_id = post_article_to_x(article, article_url, image_url)
    logger.info("記事連動X投稿完了: https://x.com/i/web/status/%s", tweet_id)


if __name__ == "__main__":
    main()
