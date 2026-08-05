#!/usr/bin/env python3
"""COLDCARD乱数生成問題の検証済み速報を単発公開する。"""

import logging
import os
import time

from generator import generate_featured_image, prepend_lead_heading, resolve_logo_brand
from wp_poster import WordPressAPI
from x_poster import post_tweet


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


OFFICIAL_ADVISORY = "https://blog.coinkite.com/coldcard-mk3-seed-generation-warning/"
OFFICIAL_TECHNICAL_REPORT = "https://blog.coinkite.com/entropy-technical-backgrounder/"
OFFICIAL_DOWNLOADS = "https://coldcard.com/downloads/"
BLOCK_ANALYSIS = (
    "https://engineering.block.xyz/blog/"
    "predictable-rng-fallback-and-32-bit-reseed-in-coldcard-firmware"
)
GITHUB_CHANGELOG = "https://github.com/Coldcard/firmware/blob/master/releases/ChangeLog.md"


def coldcard_article() -> dict:
    """一次情報とBlockの技術解析だけを基に編集した記事を返す。"""
    return {
        "title": "COLDCARDに重大な乱数生成不具合　資金流出報告、シード移行を勧告",
        "lead_heading": "COLDCARDのシード生成に重大な問題、対象者は更新と資金移行を",
        "content": f"""<p>ビットコイン向けハードウェアウォレット「COLDCARD」に、ウォレットのシード生成時の乱数を著しく弱める重大なファームウェア不具合が判明した。開発元Coinkiteは、影響を受けるファームウェアで作成したシードが危険にさらされるとして、修正版への更新後に新しいシードを作り、資金を移すよう利用者へ勧告している。</p>
<p>BlockのBitcoin Engineering and Securityチームは、COLDCARD利用者から資金喪失の報告があり、悪用が進行中だとの判断から技術解析を早期公開した。ただし、CoinkiteとBlockはいずれも、本稿執筆時点で被害総額や被害ウォレット数を公式には確定していない。本記事でも未確認の金額・件数は扱わない。</p>

<h3>「遠隔侵入」ではなく、予測しやすいシードが作られる問題</h3>
<p>今回の問題は、インターネット経由でCOLDCARD本体へ侵入し、端末内の秘密鍵を直接読み出すタイプの攻撃ではない。Coinkiteの<a href="{OFFICIAL_TECHNICAL_REPORT}" target="_blank" rel="noopener">技術解説</a>によると、2021年のソフトウェア統合時に乱数生成の呼び出し先を誤り、本来使うべきハードウェア乱数生成器ではなく、MicroPythonの決定論的なフォールバック処理が使われる状態になった。</p>
<p>このため、端末が生成したシードの候補範囲が本来より大幅に狭まり、攻撃者が端末固有情報や生成時の状態を推測・絞り込めれば、候補をオフラインで探索して秘密鍵へ到達できる可能性が生じた。シードや秘密鍵を復元されれば、対応するアドレスのビットコインを移動されるおそれがある。</p>
<div style="background:#fff3e0;border-left:4px solid #ff9800;padding:14px 18px;margin:20px 0;border-radius:4px;"><strong>重要：</strong>ファームウェアを更新するだけでは、過去に作られたシードの弱さは修復されない。対象シードを使用している場合は、修正版で新しいシードを生成し、確認した新アドレスへ資金を移す必要がある。</div>

<h3>影響を受けるモデルと修正版</h3>
<p>Coinkiteの<a href="{OFFICIAL_ADVISORY}" target="_blank" rel="noopener">公式セキュリティ勧告</a>では、影響の有無は端末の購入時期ではなく、シードを生成した時点のモデルとファームウェアで判断するとしている。公式が示した修正版は次のとおりだ。</p>
<ul>
<li><strong>Mk2／Mk3：</strong>4.2.0以降。4.0.1〜4.1.9で生成したシードが対象</li>
<li><strong>Mk4／Mk5（Standard）：</strong>5.6.0以降</li>
<li><strong>Q（Standard）：</strong>1.5.0Q以降</li>
<li><strong>Mk4／Mk5（Edge）：</strong>6.6.0X以降</li>
<li><strong>Q（Edge）：</strong>6.6.0QX以降</li>
</ul>
<p>Mk4、Mk5、Qでは、それぞれの修正版より前に生成したシードが影響を受ける。StandardとEdgeは別のリリース系統であり、単純な数字の大小では安全性を判断できない。利用者は<a href="{OFFICIAL_DOWNLOADS}" target="_blank" rel="noopener">COLDCARD公式ダウンロードページ</a>でモデルと系統に合うバージョンを確認する必要がある。</p>

<h3>CoinkiteとBlockの解析が示す深刻度</h3>
<p>Coinkiteは暫定評価として、Mk2／Mk3の実効的な探索空間を約40ビット、Mk4／Mk5／Qを約72ビットと見積もっている。いずれも同社が目標とする128ビットを下回る。公式ファームウェアの<a href="{GITHUB_CHANGELOG}" target="_blank" rel="noopener">変更履歴</a>でも、限定的なエントロピー不具合を直す緊急修正として案内されている。</p>
<p>一方、Blockの<a href="{BLOCK_ANALYSIS}" target="_blank" rel="noopener">独立した技術解析</a>は、Mk2／Mk3では端末IDやタイマー状態などが既知なら生成が決定論的になり得ると指摘した。Mk4／Mk5／Qについても、固定されたフォールバック状態などの条件下では、安全に区別される出力系列が最大2の32乗に限られるとの評価を示している。Blockは完全な実機検証による総当たり性能までは確認していないとも明記しており、数値を「誰でも直ちに全シードを解読できる時間」と読み替えるべきではない。</p>

<h3>対象者が行うべき対応</h3>
<p>Coinkiteは、まず対象モデルへ正しい修正版を導入し、端末上でバージョンを確認してから新しいシードを生成するよう案内している。新しいシードのバックアップ、ウォレットフィンガープリント、受取アドレスを確認し、少額のテスト送金が着金したことを確かめた後に残りの資金を移す。移行が完了し、十分な承認を確認するまでは古いバックアップを破棄しない。</p>
<p>慌てた移行は、送金先の間違いやバックアップ不備という別の事故を招く。シード、BIP-39パスフレーズ、秘密鍵をウェブサイトやネットワーク接続された端末へ入力してはならない。サポートを名乗る相手にも共有せず、更新ファイルと手順は公式サイトから確認したい。</p>

<h3>ダイスとBIP-39パスフレーズを使った場合</h3>
<p>Coinkiteによれば、シード作成時に公平な6面ダイスを独立して50回以上振り、その結果を秘密に保って追加していた場合、その入力だけで少なくとも128ビット相当のエントロピーが加わるため、今回の乱数問題だけを理由に危険とはみなしていない。回数や手順、最終的に使った単語に確信がなければ、移行を行うよう勧めている。</p>
<p>強く固有のBIP-39パスフレーズは追加の防壁になるが、弱いシード自体を修復するものではない。短い、一般的、再利用した、または外部に漏れた可能性があるパスフレーズは安全策にならない。COLDCARDのPINとBIP-39パスフレーズも別物である。公式は、強いパスフレーズを使っている場合でも、実務上可能な限り早く新しいシードへ移行するよう案内している。</p>

<h3>まとめ</h3>
<p>COLDCARDで確認されたのは、端末への一般的な遠隔侵入ではなく、シード生成に使う乱数の統合不具合だ。しかし、予測可能性が高まったシードから秘密鍵を探索されれば、結果として資金を盗まれる可能性がある。影響を受けるファームウェアでシードを作成した利用者は、公式勧告を確認し、正しい修正版への更新、新規シードの作成、少額テストを経た資金移行を落ち着いて進める必要がある。</p>
<p style="font-size:0.85em;color:#888;">出典：<a href="{OFFICIAL_ADVISORY}" target="_blank" rel="noopener">Coinkite公式セキュリティ勧告</a>、<a href="{OFFICIAL_TECHNICAL_REPORT}" target="_blank" rel="noopener">Coinkite技術解説</a>、<a href="{BLOCK_ANALYSIS}" target="_blank" rel="noopener">Block技術解析</a>、<a href="{GITHUB_CHANGELOG}" target="_blank" rel="noopener">COLDCARD公式GitHub変更履歴</a>（2026年8月4日確認）</p>
<p style="font-size:0.85em;color:#888;">※本記事は情報提供を目的としており、秘密鍵やシードの入力・送信を求めるものではありません。対応時は必ず公式情報を確認してください。</p>""",
        "excerpt": "COLDCARDのシード生成に使う乱数の重大な不具合が判明した。Coinkiteは修正版への更新だけでなく、新しいシードの作成と資金移行を勧告。影響モデル、原因、利用者が取るべき対応を一次情報に基づき整理する。",
        "tags": ["COLDCARD", "ハードウェアウォレット", "ビットコイン", "セキュリティ", "脆弱性"],
        "slug": "coldcard-seed-entropy-vulnerability-2026",
        "image_prompt": (
            "unbranded air-gapped hardware wallet silhouette beside a red warning light "
            "and metal dice on a dark desk"
        ),
        "logo_brand": "",
        "logo_domain": "",
        "tweet_bullets": [
            "シード生成の乱数に重大な不具合",
            "更新だけでは既存シードは直らない",
            "新シード作成後に少額テストと移行",
        ],
    }


def validate_article(article: dict) -> None:
    """公開前に、安全上重要な記述と一次情報リンクを検証する。"""
    content = article["content"]
    required = [
        OFFICIAL_ADVISORY,
        OFFICIAL_TECHNICAL_REPORT,
        OFFICIAL_DOWNLOADS,
        BLOCK_ANALYSIS,
        "更新するだけでは",
        "シード、BIP-39パスフレーズ、秘密鍵",
        "インターネット経由でCOLDCARD本体へ侵入",
        "タイプの攻撃ではない",
    ]
    missing = [item for item in required if item not in content]
    if missing:
        raise ValueError(f"公開必須情報が不足しています: {missing}")
    if any(term in content for term in ["被害総額は", "BTCが流出", "ウォレットが被害"]):
        raise ValueError("公式未確認の被害額・件数に見える表現があります")


def find_existing_article(wp: WordPressAPI, slug: str) -> dict | None:
    """同じスラッグの記事があれば返し、再実行時の二重公開を防ぐ。"""
    posts = wp.get_posts_by_slugs([slug], status="any")
    return posts[0] if posts else None


def main() -> None:
    article = coldcard_article()
    validate_article(article)
    article["content"] = prepend_lead_heading(
        article["content"], article["title"], article["lead_heading"]
    )

    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )

    existing = find_existing_article(wp, article["slug"])
    if existing:
        logger.warning(
            "同一スラッグの記事が存在するため公開を中止: %s",
            existing.get("link", article["slug"]),
        )
        return

    logo_brand, logo_domain = resolve_logo_brand(
        article["title"], article["tags"], article["logo_brand"], article["logo_domain"]
    )
    featured_media_id = None
    featured_image_url = None
    try:
        image_data = generate_featured_image(
            image_prompt=article["image_prompt"],
            tags=article["tags"],
            logo_brand=logo_brand,
            logo_domain=logo_domain,
        )
        featured_media_id, featured_image_url = wp.upload_media(
            image_data, filename=f"coldcard-security-{int(time.time())}.jpg"
        )
        logger.info("アイキャッチ生成・アップロード完了: %s", featured_image_url)
    except Exception as exc:
        logger.warning("画像生成またはアップロードに失敗したため本文公開を続行: %s", exc)

    category_id = wp.get_or_create_category("ニュース")
    result = wp.post_article(
        title=article["title"],
        content=article["content"],
        excerpt=article["excerpt"],
        tags=article["tags"],
        category_id=category_id,
        featured_media_id=featured_media_id,
        status="publish",
        slug=article["slug"],
        featured_image_url=featured_image_url,
        article_section="ニュース",
    )

    article_url = result.get("link", "")
    logger.info("公開完了: %s", article_url)
    if article_url:
        post_tweet(
            title=article["title"],
            article_url=article_url,
            tags=article["tags"],
            tweet_bullets=article["tweet_bullets"],
            article_section="ニュース",
            featured_image_url=featured_image_url,
            attach_featured_image_if_og_missing=True,
        )


if __name__ == "__main__":
    main()
