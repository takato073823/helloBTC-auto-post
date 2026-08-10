#!/usr/bin/env python3
"""
速報記事の単発公開スクリプト（手動トリガー専用）

検証済みのファクトだけを OpenAI に渡して日本語ニュース記事を生成し、
ニュースと同じ導線（アイキャッチ生成 → WordPress 公開 → X 投稿）で公開する。
数値・固有名詞のハルシネーションを防ぐため、本文の事実は FACTS のみを根拠にする。

使い方:
  RUN_MODE 不要。環境変数（WP_*, OPENAI_API_KEY, X_*）を
  設定した上で `python breaking_news.py` を実行（GitHub Actions の workflow_dispatch 経由）。
"""
import logging
import os
import re
import time

from generator import generate_featured_image, prepend_lead_heading, resolve_logo_brand
from llm_client import generate_json
from wp_poster import WordPressAPI
from x_poster import post_tweet

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 検証済みファクト（2026-08-03 時点）
#   一次情報: Bitget公式「重要なお知らせ：日本居住者の皆様への重要なご案内」およびFAQ
# 本文の数値・固有名詞はこの範囲のみを根拠とする（推測の数値を足さない）。
# ---------------------------------------------------------------------------
HEADLINE = "Bitget、日本居住者向けサービス終了へ　11月から段階的に制限"

BREAKING_ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "lead_heading": {"type": "string"},
        "content": {"type": "string"},
        "excerpt": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "slug": {"type": "string"},
        "image_prompt": {"type": "string"},
        "logo_brand": {"type": "string"},
        "logo_domain": {"type": "string"},
        "tweet_bullets": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "title", "lead_heading", "content", "excerpt", "tags", "slug",
        "image_prompt", "logo_brand", "logo_domain", "tweet_bullets",
    ],
    "additionalProperties": False,
}

FACTS = """
- Bitgetは2026年8月3日、日本の規制遵守に向けた継続的な取り組みの一環として、日本居住者へのサービス提供を終了すると発表した。
- 日本居住者の新規登録は2026年8月3日以降、受け付けない。
- 日本居住者とみなされるアカウントは、2026年11月1日11時（日本時間）から決済専用（Close-Only）モードとなる。新規ポジションの建玉・既存ポジションへの追加はできず、制限付きの入金および暗号資産・法定通貨の出金を除き、全てのBitget商品が利用できなくなる。
- Close-Onlyの対象には、現物、先物、P2P、Convert、Earn、カード、コピートレード、取引ボットなどが含まれる。
- 2026年12月31日11時（日本時間）から、全商品に残る未決済ポジションは強制決済され、カードサービスは停止される。暗号資産の出金機能は継続して利用できるとしている。
- 住所判定に誤りがある利用者は、継続的なアクセス確保のため、2026年11月1日11時（日本時間）までにレベル2本人確認（住所証明／KYC2）を完了するよう案内されている。
- 日本居住者として扱われる通知は、2026年9月17日以降に該当する利用者へ送られる予定。詳細な対応・資産管理方法はメールで順次案内される。
"""

SOURCES_NOTE = "（出典: Bitget公式発表「重要なお知らせ：日本居住者の皆様への重要なご案内」およびFAQを基に編集部が作成）"


def _bitget_breaking_article() -> dict:
    """Bitget公式発表だけを基に編集した速報記事を返す。"""
    return {
        "title": "Bitget、日本居住者向けサービス終了へ　11月から段階的に制限",
        "lead_heading": "Bitgetが日本居住者向けサービスを終了、11月から決済専用へ",
        "content": """<p>暗号資産取引所Bitget（ビットゲット）は8月3日、日本居住者へのサービス提供を終了し、対象アカウントへ段階的な制限を適用すると発表した。新規登録は同日から停止され、11月1日には対象アカウントが決済専用（Close-Only）モードへ移行する予定だ。12月31日には、残存する未決済ポジションが強制決済の対象となる。</p>
<p>Bitgetは、日本の規制遵守に向けた継続的な取り組みの一環だと説明している。利用者は自分の居住地判定、保有ポジション、利用中のサービスを確認し、今後メールで案内される手続きに備える必要がある。</p>

<h3>8月3日から新規登録を停止</h3>
<p>Bitgetは2026年8月3日以降、日本居住者の新規登録を受け付けない。既存利用者に対しては直ちに全機能を止めるのではなく、段階的な制限を実施する方針を示した。</p>
<p>日本居住者として扱われる通知は、9月17日以降に該当する利用者へ送られる予定となっている。通知を受け取った人で、居住国の登録内容が誤っている、または不正確だと考える場合は、できるだけ早くレベル2本人確認（住所証明／KYC2）を行うよう案内されている。</p>

<h3>11月1日から決済専用（Close-Only）モードへ</h3>
<p>日本居住者とみなされるアカウントには、2026年11月1日11時（日本時間）から決済専用モードが適用される。この状態では新規ポジションを建てたり、既存ポジションへ追加したりできない。制限付きの入金、暗号資産・法定通貨の出金を除き、Bitgetの商品を利用できなくなるとしている。</p>
<p>対象には現物取引、先物取引、P2P取引、Convert、Earn（資産運用）、カードサービス、コピートレード、取引ボットなどが含まれる。保有中の注文やポジション、運用中の商品がある場合は、制限開始前に取扱状況を確認しておきたい。</p>

<h3>12月31日以降は残存ポジションが強制決済の対象</h3>
<p>12月31日11時（日本時間）以降、全商品に残る未決済ポジションは強制決済される。カードサービスも同時に停止される予定だ。Bitgetは、予期せぬ影響を避けるため、この日までに未決済ポジションを解消し、アカウント内の資金・資産の出金を始めるよう呼びかけている。</p>
<div style="background:#fff3e0;border-left:4px solid #ff9800;padding:14px 18px;margin:20px 0;border-radius:4px;"><strong>主な日程：</strong>8月3日に日本居住者の新規登録を停止。11月1日11時（日本時間）から決済専用モードへ移行し、12月31日11時（日本時間）以降は残存ポジションが強制決済の対象となる。</div>
<p>強制決済の対象や手続きは、保有する商品によって影響が異なる可能性がある。現物、先物、資産運用、コピー取引などを利用している場合は、各商品の残高や注文を個別に確認する必要がある。</p>

<h3>出金機能は継続、メールでの案内を確認</h3>
<p>BitgetのFAQによると、暗号資産の出金機能は12月31日以降も引き続き利用できる。公式案内では、資金は出金されるまでアカウント内で保管され、利用者自身が管理するウォレットまたは他の取引所アカウントへ出金できるとしている。</p>
<p>ただし、サービス制限や個別の手続きに関する詳細は、今後メールで順次案内される予定だ。出金先のアドレスやネットワークの選択を誤ると資産を失うおそれがあるため、実際に出金する際は公式ガイドと受取先の条件を確認したい。なりすましの案内を避けるためにも、メール内のリンクを不用意に開かず、Bitgetの公式サイト・公式アプリから通知内容を確認することが重要である。</p>

<h3>居住地判定に誤りがある場合はKYC2を確認</h3>
<p>現在は日本に居住していないにもかかわらず日本居住者と判定されている場合、Bitgetは2026年11月1日11時（日本時間）までのレベル2本人確認を推奨している。住所証明として、銀行取引明細書、公共料金の請求書、税務証明書などが必要となる場合がある。</p>
<p>なお、11月1日以降も本人確認を完了することは可能だが、継続的なアクセスを確保するためには早めの対応が勧められている。対象者は公式の通知、FAQ、カスタマーサポートを通じて、自身のアカウントに適用される手順を確認したい。</p>
<p style="font-size:0.85em;color:#888;">（出典: Bitget公式発表「重要なお知らせ：日本居住者の皆様への重要なご案内」およびFAQを基に編集部が作成）</p>
<p style="font-size:0.85em;color:#888;">※本記事は情報提供を目的としたものであり、特定の暗号資産の売買や資産移転先を推奨するものではありません。最新の公式案内と利用条件を確認してください。</p>""",
        "excerpt": "Bitgetは日本居住者向けサービスの終了を発表した。新規登録は8月3日に停止され、11月1日から対象アカウントを決済専用へ制限。12月31日以降は残存ポジションが強制決済の対象となる。利用者が確認すべき日程と出金対応を整理する。",
        "tags": ["Bitget", "仮想通貨取引所", "暗号資産", "日本居住者", "出金"],
        "slug": "bitget-japan-service-exit-2026",
        "image_prompt": "dark cryptocurrency trading terminal with subtle Japanese city lights, cinematic news photography, no text, no people",
        "logo_brand": "Bitget",
        "logo_domain": "bitget.com",
        "tweet_bullets": ["8月3日から新規登録を停止", "11月1日から決済専用へ制限", "12月31日から残存ポジションを強制決済"],
    }


def generate_breaking_article() -> dict:
    """一次情報に基づく編集済みの速報記事を返す。

    速報時に生成APIの利用枠がない場合でも、検証済みファクトだけを用いて
    正確な記事を公開できるようにする。
    """
    return _bitget_breaking_article()

    return {
        "title": "BitMart、取引所事業を段階的に終了へ　8月26日に全取引停止",
        "lead_heading": "BitMartが取引所事業を段階的に終了、8月26日に全取引を停止",
        "content": """<p>暗号資産取引所BitMart（ビットマート）は7月26日、取引プラットフォームの運営を段階的に終了すると発表した。すでに新規登録、入金、新規注文などの受付を順次停止しており、8月26日には現物・先物を含むすべての取引サービスを止める予定だ。プラットフォーム運営の正式終了は2027年1月31日15時59分（UTC、日本時間2月1日0時59分）とされている。</p>
<p>同社は決定の背景について、事業運営の状況、市場環境、今後の戦略を総合的に検討した結果だと説明した。利用者にとっては、保有資産、未約定注文、先物ポジション、利用中の関連サービスを早めに確認する局面となる。</p>

<h3>BitMartの終了スケジュール</h3>
<p>BitMartは、取引プラットフォームを一度に閉鎖するのではなく、段階的にサービスを終了する方針を示した。7月26日1時30分（UTC、日本時間10時30分）以降、新規ユーザー登録と暗号資産・法定通貨の入金受付を順次停止している。入金停止後に送付した資産は口座に自動反映されない可能性があるとしており、同社は入金を行わないよう注意を促している。</p>
<p>次の大きな節目は8月26日1時（UTC、日本時間10時）だ。この時点で、現物取引、先物取引、その他の取引サービスが停止される予定となっている。さらに、2027年1月31日15時59分（UTC）に取引プラットフォームの運営を正式に終了する計画である。</p>

<h3>新規注文や自動取引サービスは段階的に停止</h3>
<p>7月26日以降、先物口座は決済のみが可能なReduce-Onlyモードへ移行し、新規ポジションを建てられなくなる。現物取引も新規注文の受付を停止する。コピートレード、グリッド取引、API取引などの自動取引サービスについても、順次終了する予定だ。</p>
<p>未約定の注文は、利用者が取り消すか、システム側で取り消されるとしている。特に自動売買を設定している場合は、注文やボットの稼働状況を自ら確認しておく必要がある。取引画面に残っている注文があるからといって、従来どおり執行されるとは限らない。</p>

<h3>8月26日の全取引停止と先物ポジション</h3>
<p>8月26日1時（UTC）を過ぎても先物ポジションが残っている場合、BitMartは当時適用されるマーク価格、指数価格、または決済ルールに基づき、プラットフォーム側で処理する可能性があるとしている。具体的な決済方法は別途案内される予定であり、先物を利用している人は公式の続報を確認する必要がある。</p>
<div style="background:#fff3e0;border-left:4px solid #ff9800;padding:14px 18px;margin:20px 0;border-radius:4px;"><strong>確認ポイント：</strong>取引停止予定時刻は8月26日1時（UTC）、日本時間では同日10時である。先物ポジションや未約定注文を保有している場合は、停止時刻より前に状況を確認し、公式案内に沿って対応したい。</div>
<p>BitMart Earn、ステーキング、レンディング、Launchpadなども、各商品の取り扱いに応じて段階的に終了する。償還、決済、今後の手続きは、対象となる利用者へ個別の告知およびプラットフォーム内通知で案内される。</p>

<h3>出金は利用可能、ただし早めの確認を推奨</h3>
<p>出金サービスは今回の発表後も利用可能とされている。一方でBitMartは、8月26日1時（UTC）までに本人確認と保有ポジションの解消を済ませ、同日5時（UTC、日本時間14時）より前に出金申請を行うよう強く推奨している。</p>
<p>この時刻は、公式発表が利用者に早めの手続きを勧めている時刻であり、出金サービスの終了時刻として断定されたものではない。プラットフォーム運営の正式終了後も、一定期間はログインして残高や履歴を確認し、定められた手続きに従って出金申請できるとしている。ただし、手続きには本人確認や個別の条件が関わる可能性があるため、余裕を持って情報を確認することが重要だ。</p>

<h3>公式サイト・アプリの案内を確認する</h3>
<p>サービス終了に関する局面では、偽の出金案内や手数料を求める詐欺にも注意が必要となる。BitMartは、Telegram、WhatsApp、WeChat、SNSの個人アカウントなどを通じて「優先出金手数料」「口座凍結解除費用」「保証金」を請求することはないと明記している。パスワード、SMS認証コード、Google Authenticatorのコード、秘密鍵、リカバリーフレーズを要求することもない。</p>
<p>利用者は公式サイト、公式アプリ、登録メールアドレス、公式サポート窓口からの情報を確認し、保有資産と取引状況を整理したい。今後は各サービスの償還・決済手続きや先物の詳細など、BitMartによる追加発表が焦点となる。</p>
<p style="font-size:0.85em;color:#888;">（出典: BitMart公式発表「Important Notice Regarding the Orderly Cessation of BitMart Operations」を基に編集部が作成）</p>
<p style="font-size:0.85em;color:#888;">※本記事は情報提供を目的としたものであり、特定の暗号資産の売買を推奨するものではありません。利用するサービスの条件や最新の公式案内を確認してください。</p>""",
        "excerpt": "BitMartは取引プラットフォームの段階的な終了を発表した。新規登録・入金・新規注文を順次停止し、8月26日には現物・先物を含む全取引サービスを停止する予定。出金、未決済ポジション、関連サービスで確認すべき点を整理する。",
        "tags": ["BitMart", "仮想通貨取引所", "暗号資産", "取引停止", "出金"],
        "slug": "bitmart-exchange-shutdown-2026",
        "image_prompt": "dark cryptocurrency exchange server room, muted blue monitors, cinematic news photography, no text, no people",
        "logo_brand": "BitMart",
        "logo_domain": "bitmart.com",
        "tweet_bullets": ["8月26日10時に全取引停止予定", "新規登録・入金・注文は順次停止", "出金・未決済ポジションを早めに確認"],
    }

    prompt = f"""あなたはSEOに強い仮想通貨専門の日本人ニュースライターです。helloBTC向けに「BitMartの取引プラットフォーム終了」の速報記事を作成してください。

【記事の骨子（見出し相当）】
{HEADLINE}

【記事に使ってよい事実（数値・固有名詞はこの範囲のみを根拠にする。ここに無い数値を創作しない）】
{FACTS}

【ライティングルール】
- 文体: 「〜した」「〜だ」「〜である」の常体（丁寧語禁止）。冷静で客観的なニュース調。
- ターゲット: 仮想通貨初心者〜中級者の日本人。
- 本文: 1500〜2200文字。
- 構成: リード文（何が起きたか3〜4行で要約）→ h3見出し4〜5個（終了スケジュール / すでに停止・制限されるサービス / 8月26日の取引停止と先物ポジション / 出金と資産確認で注意すべきこと / 正式終了までの流れ）。
- パニックを煽らない。利用者に必要な確認事項を整理し、特定の売買や資産移転先を推奨しない（YMYL配慮）。
- 「出金可能」と「出金申請を早めるよう推奨」は区別して記述する。公式発表にない理由、資産の安全性、具体的な出金期限を断定しない。
- 見出しはh3タグ。本文中の重要ポイントは <div style="background:#fff3e0;border-left:4px solid #ff9800;padding:14px 18px;margin:20px 0;border-radius:4px;">…</div> の注意ボックスで強調してよい。
- 本文の最上部には、投稿タイトルと意味は同じだが表現を少し変えた h2 見出しを置く。投稿タイトルをそのままコピーしない。
- 記事の最後に出典注記を入れる: <p style="font-size:0.85em;color:#888;">{SOURCES_NOTE}</p>
- さらに末尾に: <p style="font-size:0.85em;color:#888;">※本記事は情報提供を目的としたものであり、特定の暗号資産の売買を推奨するものではありません。投資は自己責任で、余裕資金の範囲内で行ってください。</p>

必ず以下のJSONのみ出力（前後に余計なテキスト不要）:
{{
  "title": "SEO最適化された日本語タイトル（35〜60文字、具体的・数字を含む。例: ビットコイン6万ドル割れ…）",
  "lead_heading": "投稿タイトルと表現を少し変えた、本文最上部用の日本語h2見出し",
  "content": "<HTML記事本文>",
  "excerpt": "記事の要約（100〜150文字、絵文字や記号は使わない）",
  "tags": ["BitMart","仮想通貨取引所","暗号資産","取引停止","出金"],
  "slug": "bitmart-exchange-shutdown-2026",
  "image_prompt": "英語の画像生成プロンプト（暗い取引所サーバールームと暗号資産取引画面を連想させる抽象的・報道写真風。テキストや人物は含めない）",
  "logo_brand": "BitMart",
  "logo_domain": "bitmart.com",
  "tweet_bullets": ["要点1（20字前後）","要点2","要点3"]
}}"""

    return generate_json(
        prompt,
        schema_name="breaking_news_article",
        schema=BREAKING_ARTICLE_SCHEMA,
        max_output_tokens=8192,
    )


def main():
    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )

    logger.info("速報記事を生成中...")
    article = generate_breaking_article()
    logger.info(f"生成タイトル: {article['title']}")
    article["content"] = prepend_lead_heading(
        article["content"], article["title"], article.get("lead_heading")
    )
    logo_brand, logo_domain = resolve_logo_brand(
        article["title"], article.get("tags"),
        article.get("logo_brand"), article.get("logo_domain"),
    )

    # アイキャッチ画像（失敗しても公開は続行）
    featured_media_id = None
    featured_image_url = None
    try:
        img = generate_featured_image(
            image_prompt=article.get("image_prompt", "falling red cryptocurrency price chart, dramatic market crash, dark"),
            tags=article.get("tags", []),
            logo_brand=logo_brand,
            logo_domain=logo_domain,
            article_title=article["title"],
            article_content=article["content"],
        )
        featured_media_id, featured_image_url = wp.upload_media(
            img, filename=f"breaking-{int(time.time())}.jpg"
        )
        logger.info(f"アイキャッチ: {featured_image_url}")
    except Exception as e:
        logger.warning(f"画像生成/アップロード失敗（続行）: {e}")

    category_id = wp.get_or_create_category("ニュース")

    result = wp.post_article(
        title=article["title"],
        content=article["content"],
        excerpt=article["excerpt"],
        tags=article.get("tags", []),
        category_id=category_id,
        featured_media_id=featured_media_id,
        status="publish",
        slug=article.get("slug"),
        featured_image_url=featured_image_url,
        article_section="ニュース",
    )

    article_url = result.get("link", "")
    logger.info(f"=== 公開完了: {article_url} ===")

    if article_url:
        post_tweet(
            title=article["title"],
            article_url=article_url,
            tags=article.get("tags", []),
            tweet_bullets=article.get("tweet_bullets"),
            article_section="ニュース",
            featured_image_url=featured_image_url,
            attach_featured_image_if_og_missing=True,
        )


if __name__ == "__main__":
    main()
