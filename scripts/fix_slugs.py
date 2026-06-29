#!/usr/bin/env python3
"""
日本語エンコードURL（長いスラッグ）を短い英語スラッグへ一括変更する（単発・手動トリガー）。

- SLUG_MAP の {投稿ID: 新スラッグ} に従って slug を更新する。
- slug 変更で WordPress が旧スラッグを _wp_old_slug に記録し、旧URL→新URL の
  301リダイレクトを自動付与するため、既存のSEO評価・被リンク・インデックスは引き継がれる。
- 新スラッグが既存と衝突する場合、WordPress 側が自動で連番（-2 等）を付与する。
  実際に確定したスラッグはレスポンスから読み取ってログ出力する。
- 既に目的の slug になっている記事はスキップ（冪等）。
"""
import logging
import os

from wp_poster import WordPressAPI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# {投稿ID: 新しい英語スラッグ}  ※タイトルから内容を表す簡潔なスラッグを作成
SLUG_MAP = {
    # --- 基礎・解説 ---
    152: "bitcoin-how-it-works",
    142: "what-is-cryptocurrency",
    170: "how-blockchain-works",
    181: "what-is-leverage",
    129: "how-to-earn-crypto-beginners",
    197: "crypto-security-guide",
    207: "google-authenticator-2fa-setup",
    430: "crypto-tax-guide",
    410: "stablecoin-beginner-guide",
    454: "liquidity-mining-guide",
    530: "what-is-meme-coin-pumpfun",
    557: "what-is-rwa-tokenization",
    578: "what-is-ethereum",
    371: "copy-trading-mistakes",
    # --- プロジェクト・銘柄解説 ---
    236: "extended-perpdex-guide",
    484: "lighter-perpdex-guide",
    676: "what-is-zama-fhe",
    710: "what-is-codex-pbc",
    829: "edgex-price-prediction",
    606: "penguin-token-surge",
    # --- BingX ---
    289: "bingx-account-opening",
    335: "bingx-spot-trading-basics",
    761: "bingx-invite-code",
    970: "bingx-campaign-2026",
    995: "bingx-8th-anniversary",
    # --- ニュース・相場 ---
    600: "fed-rate-hold-bitcoin-stalls",
    625: "tesla-loss-holds-bitcoin",
    629: "bitcoin-drops-81000-liquidations",
    633: "binance-cz-criticism",
    657: "bitcoin-crash-77000",
    665: "bitcoin-etf-investors-losses",
    672: "michael-burry-bitcoin-warning",
    696: "bitcoin-60000-scenarios",
    699: "bitcoin-rebounds-72000",
    703: "bithumb-bitcoin-misdelivery",
    706: "standard-chartered-bearish-forecast",
    728: "x-crypto-trading-cashtags",
    735: "metaplanet-btc-impairment",
    1194: "crypto-fraud-teen-arrested",
    1197: "anthropic-ceo-ai-regulation",
    1206: "raydium-exploit-solana",
    1208: "neura-robotics-funding",
    1210: "blackrock-bita-yield-etf",
    1212: "bitmine-eth-accumulation",
    1215: "wallstreet-stablecoin-rwa",
    1217: "claude-fable5-security-breach",
    1219: "advisors-shift-stablecoin-rwa",
    1221: "spacex-ipo-bitcoin-impact",
    1223: "figure-acquires-kiavi",
    1225: "microstrategy-btc-sale-test",
    1227: "morgan-stanley-bitcoin-education",
    1229: "fold-holdings-btc-sale",
    1231: "crypto-market-ai-eu-regulation",
}


def main():
    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )

    total = len(SLUG_MAP)
    changed = skipped = failed = 0
    logger.info(f"スラッグ変更開始: {total}件")

    for post_id, new_slug in SLUG_MAP.items():
        try:
            current = wp._request(
                "GET", f"posts/{post_id}",
                params={"context": "edit", "_fields": "id,slug,link"},
            )
            old_slug = current.get("slug", "")
            old_link = current.get("link", "")

            if old_slug == new_slug:
                logger.info(f"[skip] ID {post_id}: 既に '{new_slug}'")
                skipped += 1
                continue

            result = wp.update_post(post_id, slug=new_slug)
            actual = result.get("slug", "")
            new_link = result.get("link", "")
            flag = "" if actual == new_slug else f"（衝突回避で '{actual}' に確定）"
            logger.info(f"[ok]   ID {post_id}: {old_link}  ->  {new_link} {flag}")
            changed += 1
        except Exception as e:
            logger.error(f"[fail] ID {post_id} ({new_slug}): {e}")
            failed += 1

    logger.info(f"完了: 変更 {changed} / スキップ {skipped} / 失敗 {failed}（全 {total}件）")


if __name__ == "__main__":
    main()
