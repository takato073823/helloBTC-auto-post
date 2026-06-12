#!/usr/bin/env python3
"""
BingX IB特化 SEO記事 完全自動生成
18トピックを順番に1日1記事公開し、IB報酬の流入を最大化する。

- 公開ページ → Playwright スクリーンショット
- ログイン必須ページ → Imagen 概念イメージ
- 全記事に招待コード XXCCJX の CTAボックスを挿入
- bingx_posted_topics.json で投稿済みを管理（全完了後は最初に戻る）
"""

import asyncio
import io
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import anthropic
from PIL import Image
from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent))
from wp_poster import WordPressAPI
from x_poster import post_tweet

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

POSTED_FILE = Path(__file__).parent / "bingx_posted_topics.json"

INVITE_CODE = "XXCCJX"
INVITE_URL  = "https://bingxdao.com/invite/XXCCJX/"

AFFILIATE_BOX = (
    '<div style="background:#fff8e1;border-left:5px solid #f7931a;'
    'padding:18px 22px;margin:28px 0;border-radius:6px;">'
    "<strong>🎁 BingX 招待特典</strong><br>"
    f"招待コード：<strong>{INVITE_CODE}</strong><br>"
    f'▶ <a href="{INVITE_URL}" target="_blank" rel="nofollow noopener">'
    "こちらから登録する（特典・手数料割引が自動適用）</a></div>"
)

INFO_BOX_OPEN  = '<div style="background:#e8f5e9;border-left:4px solid #4caf50;padding:14px 18px;margin:20px 0;border-radius:4px;">'
WARN_BOX_OPEN  = '<div style="background:#fff3e0;border-left:4px solid #ff9800;padding:14px 18px;margin:20px 0;border-radius:4px;">'
BOX_CLOSE      = "</div>"

# ---------------------------------------------------------------------------
# 18 トピック定義
# ---------------------------------------------------------------------------
# screenshot_pages: 公開URLがあればPlaywrightで撮影
# imagen_prompts  : Imagenで生成する概念イメージ（featured含む）
# type            : tutorial | review | comparison | guide
# ---------------------------------------------------------------------------

TOPICS = [
    {
        "id": "kyc-guide",
        "keyword": "BingX 本人確認 KYC やり方",
        "slug": "bingx-kyc-verification-guide",
        "tags": ["BingX", "本人確認", "KYC", "口座開設", "仮想通貨取引所"],
        "type": "tutorial",
        "article_guide": (
            "BingXで本人確認（KYC）を行う手順を画像付きで解説する。"
            "用意する書類・所要時間・審査期間・よくあるエラーと対処法を含める。"
        ),
        "tweet_bullets": ["本人確認は5〜10分で完了", "パスポートor免許証で対応", "KYC完了で出金上限が大幅アップ"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "smartphone scanning passport document, blue verification beam, dark mobile UI",
            "facial recognition dots on phone screen, biometric scan animation, dark background",
        ],
    },
    {
        "id": "copy-trade-guide",
        "keyword": "BingX コピートレード 始め方 トレーダー 選び方",
        "slug": "bingx-copy-trade-guide",
        "tags": ["BingX", "コピートレード", "自動売買", "仮想通貨", "初心者"],
        "type": "tutorial",
        "article_guide": (
            "BingXのコピートレード機能の始め方を解説する。"
            "トレーダーの選び方の基準（勝率・最大ドローダウン・フォロワー数）、"
            "コピー金額の設定、損切り設定、コピー停止方法まで含める。"
        ),
        "tweet_bullets": ["10 USDTから始められるコピートレード", "勝率・ドローダウンでトレーダーを選ぶ", "自動売買で24時間運用可能"],
        "screenshot_pages": [
            {"key": "copy_top", "url": "https://bingx.com/en-us/copyTrade/", "description": "コピートレードTOP画面", "viewport": {"width": 1440, "height": 900}, "wait_ms": 6000},
            {"key": "traders", "url": "https://bingx.com/en-us/copyTrade/leadTrader/", "description": "トレーダー一覧・ランキング", "viewport": {"width": 1440, "height": 900}, "wait_ms": 6000},
        ],
        "imagen_prompts": [
            "copy trading dashboard showing green profit chart, multiple screens, dark interface",
        ],
    },
    {
        "id": "futures-guide",
        "keyword": "BingX 先物取引 使い方 レバレッジ 設定",
        "slug": "bingx-futures-trading-guide",
        "tags": ["BingX", "先物取引", "レバレッジ", "仮想通貨FX", "取引方法"],
        "type": "tutorial",
        "article_guide": (
            "BingX先物取引の使い方を初心者向けに解説する。"
            "レバレッジの設定方法、ロング・ショートの違い、証拠金計算、"
            "損切り（ストップロス）・利確（テイクプロフィット）の設定方法を含める。"
            "リスク管理の重要性も必ず触れる。"
        ),
        "tweet_bullets": ["最大150倍レバレッジに対応", "ロング・ショート両方向で稼げる", "TP/SL設定でリスク管理が重要"],
        "screenshot_pages": [
            {"key": "futures", "url": "https://bingx.com/en-us/perpetual/BTCUSDT/", "description": "BTC/USDT先物取引チャート", "viewport": {"width": 1440, "height": 900}, "wait_ms": 7000},
        ],
        "imagen_prompts": [
            "leveraged trading chart with rising and falling candlesticks, dramatic red and green, dark screen",
            "trading risk management calculator on screen, numbers glowing, dark interface",
        ],
    },
    {
        "id": "deposit-guide",
        "keyword": "BingX 入金方法 仮想通貨 クレジットカード",
        "slug": "bingx-deposit-guide",
        "tags": ["BingX", "入金方法", "仮想通貨送金", "USDT", "口座開設"],
        "type": "tutorial",
        "article_guide": (
            "BingXへの入金方法を全種類解説する。"
            "①仮想通貨送金（USDT TRC20推奨・手順・ネットワーク選択の注意点）、"
            "②クレジットカード購入（手数料・メリット・デメリット）、"
            "③P2P取引の3パターンをステップ別に説明する。"
        ),
        "tweet_bullets": ["USDT TRC20送金が最も手数料安い", "クレカは少額・急ぎ向け（手数料高め）", "P2Pで日本円から直接入金も可能"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "crypto wallet QR code on smartphone screen, soft neon glow, dark minimalist design",
            "digital money transfer between two smartphones, glowing arrows, dark background",
        ],
    },
    {
        "id": "withdraw-guide",
        "keyword": "BingX 出金方法 送金 手数料 時間",
        "slug": "bingx-withdrawal-guide",
        "tags": ["BingX", "出金方法", "送金", "仮想通貨", "取引所"],
        "type": "tutorial",
        "article_guide": (
            "BingXからの出金・送金手順を解説する。"
            "出金申請の手順、ネットワーク選択（手数料比較）、"
            "出金上限・最低出金額、出金にかかる時間（目安）、"
            "よくあるトラブルと対処法（出金保留・遅延）を含める。"
        ),
        "tweet_bullets": ["ネットワーク選択ミスに注意", "KYC完了で出金上限が大幅アップ", "通常24時間以内に着金"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "cryptocurrency transfer confirmation screen on phone, transaction pending, dark UI",
            "blockchain transaction network visualization, glowing nodes, dark background",
        ],
    },
    {
        "id": "spot-trade-guide",
        "keyword": "BingX 現物取引 買い方 注文方法",
        "slug": "bingx-spot-trading-guide",
        "tags": ["BingX", "現物取引", "ビットコイン購入", "仮想通貨", "初心者"],
        "type": "tutorial",
        "article_guide": (
            "BingX現物取引の始め方を初心者向けに解説する。"
            "成行注文・指値注文・逆指値注文の違い、"
            "取引ペアの選び方、注文画面の見方、"
            "手数料（Maker/Taker）の計算方法を含める。"
        ),
        "tweet_bullets": ["成行・指値・逆指値の3種類に対応", "現物Maker手数料0.1%と低水準", "600銘柄以上から選べる"],
        "screenshot_pages": [
            {"key": "spot", "url": "https://bingx.com/en-us/spot/BTCUSDT/", "description": "BTC/USDT現物取引画面", "viewport": {"width": 1440, "height": 900}, "wait_ms": 7000},
        ],
        "imagen_prompts": [
            "cryptocurrency trading order book on screen, green and red prices, dark interface",
        ],
    },
    {
        "id": "app-guide",
        "keyword": "BingX アプリ ダウンロード 使い方 スマホ",
        "slug": "bingx-app-guide",
        "tags": ["BingX", "スマホアプリ", "iOS", "Android", "取引所アプリ"],
        "type": "tutorial",
        "article_guide": (
            "BingXスマホアプリ（iOS・Android）の使い方を解説する。"
            "ダウンロード手順、初期設定（通知・言語）、"
            "アプリでできること（取引・コピートレード・チャート確認）、"
            "便利な機能（価格アラート・ウォッチリスト）を紹介する。"
        ),
        "tweet_bullets": ["iOS・Android両対応の公式アプリ", "価格アラートで相場急変を即通知", "コピートレードもアプリで管理できる"],
        "screenshot_pages": [
            {"key": "main", "url": "https://bingx.com/en-us/", "description": "BingXメインサイト・アプリDLバナー", "viewport": {"width": 1280, "height": 800}, "wait_ms": 4000},
        ],
        "imagen_prompts": [
            "smartphone displaying cryptocurrency trading app with charts, dark sleek design",
            "mobile app notification alert for price change, glowing screen, dark background",
        ],
    },
    {
        "id": "review",
        "keyword": "BingX 評判 口コミ メリット デメリット",
        "slug": "bingx-review-reputation",
        "tags": ["BingX", "評判", "口コミ", "メリット", "デメリット"],
        "type": "review",
        "article_guide": (
            "BingXの評判・口コミを元に、メリット・デメリットをバランスよく解説する。"
            "良い評判：コピートレード・日本語対応・低手数料。"
            "悪い評判：出金トラブル事例・サポート対応の遅さ。"
            "総合評価と「どんな人に向いているか」を最後にまとめる。"
        ),
        "tweet_bullets": ["コピートレードの評判が特に高い", "手数料の安さは国内外トップクラス", "日本語サポートあり・初心者向け"],
        "screenshot_pages": [
            {"key": "top", "url": "https://bingxdao.com/invite/XXCCJX/", "description": "BingXトップページ", "viewport": {"width": 1280, "height": 800}, "wait_ms": 4000},
        ],
        "imagen_prompts": [
            "five star rating review concept on dark background, golden stars glowing",
            "cryptocurrency exchange comparison chart on screen, professional dark UI",
        ],
    },
    {
        "id": "fees-guide",
        "keyword": "BingX 手数料 スプレッド 取引コスト 比較",
        "slug": "bingx-fees-guide",
        "tags": ["BingX", "手数料", "取引コスト", "スプレッド", "仮想通貨取引所"],
        "type": "guide",
        "article_guide": (
            "BingXの手数料体系を徹底解説する。"
            "現物取引（Maker/Taker）・先物取引・出金手数料の一覧表を作る。"
            "VIPレベルによる手数料割引、招待コードによる追加割引、"
            "他の主要取引所（Bybit・Binance）との比較も含める。"
        ),
        "tweet_bullets": ["現物Maker手数料0.1%（招待コードで割引）", "先物Maker手数料0.02%の低水準", "VIPレベルでさらに割引可能"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "fee percentage calculation on financial screen, numbers and charts, dark background",
            "cost comparison bar chart on screen, multiple colored bars, dark interface",
        ],
    },
    {
        "id": "security-guide",
        "keyword": "BingX 安全性 セキュリティ 危険 信頼性",
        "slug": "bingx-security-safety",
        "tags": ["BingX", "安全性", "セキュリティ", "信頼性", "仮想通貨取引所"],
        "type": "review",
        "article_guide": (
            "BingXの安全性・信頼性を解説する。"
            "運営会社の情報・設立年・ライセンス、"
            "コールドウォレット比率・保険基金・過去のセキュリティインシデントの有無、"
            "ユーザーが自分でできるセキュリティ設定（2FA・出金ホワイトリスト・ログイン通知）を含める。"
        ),
        "tweet_bullets": ["コールドウォレットで資産の大半を管理", "2FA設定で不正ログインを防止", "出金ホワイトリストで盗難リスクを最小化"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "digital padlock and shield glowing blue on dark background, cybersecurity concept",
            "cold storage hardware wallet on dark reflective surface, secure cryptocurrency",
        ],
    },
    {
        "id": "vs-bybit",
        "keyword": "BingX Bybit 比較 どっちがいい",
        "slug": "bingx-vs-bybit-comparison",
        "tags": ["BingX", "Bybit", "取引所比較", "コピートレード", "仮想通貨"],
        "type": "comparison",
        "article_guide": (
            "BingX vs Bybit を徹底比較する記事。"
            "比較項目：手数料・取扱銘柄数・コピートレード機能・セキュリティ・日本語対応・初心者のしやすさ。"
            "比較表（HTMLのtableタグ）を必ず入れる。"
            "最後に「どんな人にBingXが向いているか」でBingXを推奨する結論にする。"
        ),
        "tweet_bullets": ["コピートレードはBingXが使いやすい", "手数料はBingXがわずかに有利", "日本語対応は両取引所ともに充実"],
        "screenshot_pages": [
            {"key": "copy_top", "url": "https://bingx.com/en-us/copyTrade/", "description": "BingXコピートレード画面", "viewport": {"width": 1440, "height": 900}, "wait_ms": 6000},
        ],
        "imagen_prompts": [
            "two trading platforms side by side on dark screens, comparison concept",
        ],
    },
    {
        "id": "vs-binance",
        "keyword": "BingX Binance 比較 どっちがいい 初心者",
        "slug": "bingx-vs-binance-comparison",
        "tags": ["BingX", "Binance", "取引所比較", "初心者", "仮想通貨"],
        "type": "comparison",
        "article_guide": (
            "BingX vs Binance を比較する記事。"
            "比較項目：手数料・取扱銘柄数・コピートレード有無・UI/UXのわかりやすさ・日本語対応・最低入金額。"
            "比較表（HTMLのtableタグ）を必ず入れる。"
            "Binanceは規模で大きいが、コピートレードと使いやすさでBingXを推奨する結論にする。"
        ),
        "tweet_bullets": ["Binanceは銘柄数最多だが複雑", "BingXのコピートレードは初心者に最適", "手数料はほぼ同水準で拮抗"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "two cryptocurrency exchange platforms displayed on screens, dark comparison layout",
            "exchange trading volume chart comparison, multiple colored bars, dark background",
        ],
    },
    {
        "id": "bonus-guide",
        "keyword": "BingX ボーナス キャンペーン 招待コード 特典",
        "slug": "bingx-bonus-campaign-guide",
        "tags": ["BingX", "ボーナス", "キャンペーン", "招待コード", "特典"],
        "type": "guide",
        "article_guide": (
            "BingXで受け取れるボーナス・キャンペーン情報を解説する。"
            f"招待コード{INVITE_CODE}で受け取れるウェルカムボーナス・手数料割引の詳細、"
            "新規登録ボーナス、入金ボーナス、取引ボーナス（タスク達成型）の種類と条件、"
            "ボーナスの注意点（出金不可・有効期限など）を含める。"
        ),
        "tweet_bullets": [f"招待コード{INVITE_CODE}で特典GET", "新規登録で最大○○ドルのボーナス", "入金額に応じた追加ボーナスあり"],
        "screenshot_pages": [
            {"key": "invite", "url": "https://bingxdao.com/invite/XXCCJX/", "description": "BingX招待・特典ページ", "viewport": {"width": 1280, "height": 800}, "wait_ms": 4000},
        ],
        "imagen_prompts": [
            "gift box with glowing golden coins, bonus reward concept, dark background",
            "percentage discount label on cryptocurrency coins, promotional concept, dark aesthetic",
        ],
    },
    {
        "id": "grid-trade-guide",
        "keyword": "BingX グリッドトレード 設定 自動売買",
        "slug": "bingx-grid-trading-guide",
        "tags": ["BingX", "グリッドトレード", "自動売買", "ボット取引", "仮想通貨"],
        "type": "tutorial",
        "article_guide": (
            "BingXのグリッドトレード（自動売買ボット）の設定方法を解説する。"
            "グリッドトレードの仕組み・メリット・適した相場環境（レンジ相場）、"
            "設定パラメータ（価格範囲・グリッド数・投資額）の決め方、"
            "運用中の管理方法と注意点を含める。"
        ),
        "tweet_bullets": ["レンジ相場で利益を積み上げるボット", "設定後は24時間自動売買", "少額から始められる自動運用"],
        "screenshot_pages": [
            {"key": "grid", "url": "https://bingx.com/en-us/grid/", "description": "BingXグリッドトレード画面", "viewport": {"width": 1440, "height": 900}, "wait_ms": 6000},
        ],
        "imagen_prompts": [
            "automated trading bot concept, grid pattern on price chart, dark interface",
            "algorithmic trading multiple buy sell points on chart, glowing lines, dark screen",
        ],
    },
    {
        "id": "leverage-guide",
        "keyword": "BingX レバレッジ 倍率 設定方法 証拠金",
        "slug": "bingx-leverage-settings-guide",
        "tags": ["BingX", "レバレッジ", "証拠金", "先物取引", "リスク管理"],
        "type": "tutorial",
        "article_guide": (
            "BingXでのレバレッジ設定方法を詳しく解説する。"
            "レバレッジ倍率の選び方（初心者は2〜5倍推奨）、"
            "必要証拠金の計算方法（計算例付き）、"
            "強制ロスカット価格の計算方法、"
            "ポジションサイズとリスク管理の考え方を含める。"
        ),
        "tweet_bullets": ["初心者はレバレッジ2〜5倍を推奨", "証拠金不足でロスカットに注意", "損失は投入証拠金を超えない設計"],
        "screenshot_pages": [
            {"key": "futures", "url": "https://bingx.com/en-us/perpetual/BTCUSDT/", "description": "先物取引・レバレッジ設定画面", "viewport": {"width": 1440, "height": 900}, "wait_ms": 7000},
        ],
        "imagen_prompts": [
            "leverage slider control on trading app, risk meter, dark interface",
            "margin calculation numbers on financial screen, magnified percentage, dark background",
        ],
    },
    {
        "id": "earn-guide",
        "keyword": "BingX 稼ぐ方法 利益 戦略 コツ",
        "slug": "bingx-how-to-earn-strategy",
        "tags": ["BingX", "稼ぐ方法", "投資戦略", "コピートレード", "グリッドトレード"],
        "type": "guide",
        "article_guide": (
            "BingXで実際に稼ぐための戦略・方法を解説する記事。"
            "①コピートレード（優良トレーダー選びのコツ）、"
            "②グリッドトレード（レンジ相場での運用）、"
            "③現物の積立（DCA戦略）、"
            "④先物スキャルピング（上級者向け）の4つの戦略を初心者視点で解説する。"
            "リスク管理の重要性とおすすめの資金配分も触れる。"
        ),
        "tweet_bullets": ["コピートレードは初心者が最もリスク低め", "グリッドボットはレンジ相場で安定", "DCA積立で長期リターンを狙う"],
        "screenshot_pages": [
            {"key": "copy_top", "url": "https://bingx.com/en-us/copyTrade/", "description": "コピートレード戦略画面", "viewport": {"width": 1440, "height": 900}, "wait_ms": 6000},
        ],
        "imagen_prompts": [
            "investment strategy concept with rising profit graph, golden coins, dark background",
            "diversified portfolio allocation chart glowing on screen, dark interface",
        ],
    },
    {
        "id": "tax-guide",
        "keyword": "BingX 税金 確定申告 計算方法 仮想通貨FX",
        "slug": "bingx-tax-guide-japan",
        "tags": ["BingX", "税金", "確定申告", "仮想通貨", "雑所得"],
        "type": "guide",
        "article_guide": (
            "BingXで得た利益の税金・確定申告を解説する記事（日本向け）。"
            "仮想通貨FXの利益は雑所得として課税される点、"
            "取引履歴のダウンロード方法、損益計算ツールの使い方、"
            "確定申告が必要なケース（20万円超）、税率の目安を含める。"
        ),
        "tweet_bullets": ["利益20万円超で確定申告が必要", "雑所得として総合課税される", "取引履歴はBingXからCSVで取得可能"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "tax document and calculator on dark desk, Japanese yen coins, formal concept",
            "financial statement spreadsheet on laptop screen, tax calculation, dark office",
        ],
    },
    {
        "id": "beginner-guide",
        "keyword": "BingX 初心者 完全ガイド 登録から取引まで",
        "slug": "bingx-complete-beginner-guide",
        "tags": ["BingX", "初心者", "仮想通貨", "口座開設", "コピートレード"],
        "type": "guide",
        "article_guide": (
            "BingX初心者向け完全ガイド。"
            "「BingXとは何か」から「口座開設→本人確認→入金→最初の取引」まで一気通貫で解説する。"
            "初心者には現物取引かコピートレードから始めることを推奨する。"
            "よくある疑問（日本から使える？安全？出金できる？）もQ&A形式で答える。"
        ),
        "tweet_bullets": ["登録〜初取引まで最短30分", "初心者はコピートレードがおすすめ", "日本語完全対応で安心して使える"],
        "screenshot_pages": [
            {"key": "top", "url": "https://bingxdao.com/invite/XXCCJX/", "description": "BingX招待・登録トップ", "viewport": {"width": 1280, "height": 800}, "wait_ms": 4000},
            {"key": "copy_top", "url": "https://bingx.com/en-us/copyTrade/", "description": "コピートレード画面", "viewport": {"width": 1440, "height": 900}, "wait_ms": 6000},
        ],
        "imagen_prompts": [
            "beginner holding smartphone with crypto trading app, learning concept, dark aesthetic",
        ],
    },
]

# ---------------------------------------------------------------------------
# 投稿済みトピック管理
# ---------------------------------------------------------------------------

def load_posted_ids() -> list:
    if POSTED_FILE.exists():
        return json.loads(POSTED_FILE.read_text(encoding="utf-8"))
    return []


def save_posted_ids(ids: list):
    POSTED_FILE.write_text(json.dumps(ids, ensure_ascii=False, indent=2), encoding="utf-8")


def pick_next_topic() -> dict:
    """未投稿トピックを順番に返す。全完了なら最初に戻す。"""
    posted = load_posted_ids()
    for topic in TOPICS:
        if topic["id"] not in posted:
            return topic
    # 全部完了 → リセットして最初から
    logger.info("全トピック投稿済み。リセットして再開します。")
    save_posted_ids([])
    return TOPICS[0]


# ---------------------------------------------------------------------------
# Playwright スクリーンショット
# ---------------------------------------------------------------------------

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
_POPUP_SELECTORS = [
    "button[id*='accept']", "button[class*='accept']",
    "button[class*='cookie']", "[aria-label='Close']",
    "button[class*='close']", "button[class*='dismiss']",
]


async def _take_screenshot(browser, sc_cfg: dict) -> bytes | None:
    page = await browser.new_page(viewport=sc_cfg["viewport"], user_agent=_UA)
    try:
        await page.goto(sc_cfg["url"], wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(sc_cfg["wait_ms"])
        for sel in _POPUP_SELECTORS:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=800):
                    await el.click()
                    await page.wait_for_timeout(400)
                    break
            except Exception:
                pass
        await page.wait_for_timeout(800)
        raw = await page.screenshot(type="jpeg", quality=90, full_page=False)
        logger.info(f"  ✓ screenshot: {sc_cfg['key']}")
        return raw
    except PlaywrightTimeout:
        logger.warning(f"  ✗ timeout: {sc_cfg['key']}")
        return None
    except Exception as e:
        logger.warning(f"  ✗ error: {sc_cfg['key']} — {e}")
        return None
    finally:
        await page.close()


async def capture_screenshots(pages: list) -> dict[str, bytes | None]:
    if not pages:
        return {}
    results = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        for sc_cfg in pages:
            results[sc_cfg["key"]] = await _take_screenshot(browser, sc_cfg)
        await browser.close()
    return results


# ---------------------------------------------------------------------------
# Imagen 画像生成
# ---------------------------------------------------------------------------

def generate_imagen(prompt: str) -> bytes | None:
    try:
        from google import genai
        from google.genai import types

        full = (
            f"{prompt}. "
            "Photojournalism Reuters style, muted cool tones, professional lighting. "
            "No text, no people, no faces, no brand logos, no watermarks."
        )
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        resp = client.models.generate_images(
            model="imagen-4.0-fast-generate-001",
            prompt=full,
            config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio="16:9"),
        )
        raw = resp.generated_images[0].image.image_bytes
        img = Image.open(io.BytesIO(raw)).resize((1200, 675), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=90)
        logger.info("  ✓ imagen generated")
        return out.getvalue()
    except Exception as e:
        logger.warning(f"  ✗ imagen failed: {e}")
        return None


def resize_jpeg(raw: bytes) -> bytes:
    img = Image.open(io.BytesIO(raw)).resize((1200, 675), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Claude 記事生成
# ---------------------------------------------------------------------------

_TYPE_INSTRUCTIONS = {
    "tutorial": "手順解説記事。ステップ番号付きのol/ulリストを多用し、注意事項はinfoボックスで強調する。",
    "review":   "評判・レビュー記事。メリット・デメリットをリストアップし、総合評価で締める。客観的・公平な視点で書く。",
    "comparison": "比較記事。HTMLのtableタグで比較表を作成し、最終的にBingXを推奨する結論にする。",
    "guide":    "解説ガイド記事。見出しごとに情報を整理し、初心者でも理解できるよう専門用語には説明を添える。",
}


def generate_article(topic: dict, image_keys: list[str]) -> dict:
    client = anthropic.Anthropic()

    img_placeholders = "\n".join(
        f"- {{{{IMG_{k.upper()}}}}}: 対応する画像を配置" for k in image_keys
    )
    type_guide = _TYPE_INSTRUCTIONS.get(topic["type"], "")

    prompt = f"""あなたはSEOに強い仮想通貨専門ライターです。helloBTC向けにBingXについての記事を作成してください。

【記事テーマ】
キーワード: {topic['keyword']}
記事の方針: {topic['article_guide']}
記事タイプ: {topic['type']} — {type_guide}

【BingX 基本情報（記事中に必要に応じて活用）】
- 招待コード: {INVITE_CODE} / 招待URL: {INVITE_URL}
- 特徴: コピートレード・600銘柄以上・先物/現物・日本語対応・最大150倍レバレッジ

【使用できる画像プレースホルダー（適切な位置に配置）】
{img_placeholders if img_placeholders else "（画像なし）"}

【アフィリエイトボックスHTML（リード文の末尾と記事末の2箇所に必ず挿入）】
{AFFILIATE_BOX}

【情報ボックスHTML（ポイント・注意事項に使用）】
{INFO_BOX_OPEN}ここに内容{BOX_CLOSE}

【比較表が必要な場合のHTMLスタイル】
<table style="width:100%;border-collapse:collapse;margin:20px 0;">
<thead><tr style="background:#f7931a;color:#fff;">
<th style="padding:10px;">項目</th><th>BingX</th><th>比較対象</th></tr></thead>
<tbody><tr style="background:#fff8e1;"><td style="padding:10px;border:1px solid #ddd;">...</td>...</tr></tbody>
</table>

【ライティングルール】
- 文体: 「〜した」「〜だ」「〜である」（丁寧語禁止）
- ターゲット: 仮想通貨初心者〜中級者の日本人
- 本文: 1800〜2500文字
- 見出し: h3タグ（4〜6個）
- 画像プレースホルダー: <figure>{{{{IMG_xxx}}}}</figure> 形式で配置
- 末尾: <p style="font-size:0.85em;color:#888;">※仮想通貨への投資はリスクを伴います。余裕資金の範囲内で行ってください。</p>

必ず以下のJSONのみ出力（前後に余計なテキスト不要）:
{{
  "title": "SEO最適化された日本語タイトル（35〜65文字、具体的・数字・年を含む）",
  "content": "<HTML記事本文>",
  "excerpt": "記事の要約（100〜150文字）"
}}"""

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"JSON が見つかりません: {text[:200]}")
    return json.loads(text[start:end])


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

async def main():
    topic = pick_next_topic()
    logger.info(f"=== BingX SEO記事生成: [{topic['id']}] {topic['keyword']} ===")

    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )

    # 1. スクリーンショット
    logger.info("[1/5] Playwright スクリーンショット...")
    screenshots = await capture_screenshots(topic["screenshot_pages"])

    # 2. Imagen 画像
    logger.info("[2/5] Imagen 画像生成...")
    imagen_images = {}
    for i, prompt in enumerate(topic["imagen_prompts"]):
        key = f"img{i+1}"
        imagen_images[key] = generate_imagen(prompt)

    # 3. WordPress へ画像アップロード
    logger.info("[3/5] 画像アップロード...")
    image_map: dict[str, tuple[int, str]] = {}  # key -> (media_id, url)

    for sc_cfg in topic["screenshot_pages"]:
        key = sc_cfg["key"]
        raw = screenshots.get(key)
        if not raw:
            continue
        try:
            resized = resize_jpeg(raw)
            mid, url = wp.upload_media(resized, f"bingx-{topic['id']}-{key}.jpg")
            image_map[key] = (mid, url)
            logger.info(f"  ✓ {key} → {url}")
        except Exception as e:
            logger.warning(f"  ✗ upload failed {key}: {e}")

    featured_media_id = None
    featured_image_url = None
    for i, (key, img_bytes) in enumerate(imagen_images.items()):
        if not img_bytes:
            continue
        try:
            mid, url = wp.upload_media(img_bytes, f"bingx-{topic['id']}-{key}.jpg")
            image_map[key] = (mid, url)
            if i == 0 and not featured_media_id:
                featured_media_id = mid
                featured_image_url = url
            logger.info(f"  ✓ {key} → {url}")
        except Exception as e:
            logger.warning(f"  ✗ upload failed {key}: {e}")

    # スクリーンショットがあればそちらをアイキャッチに優先
    for sc_cfg in topic["screenshot_pages"]:
        if sc_cfg["key"] in image_map:
            featured_media_id, featured_image_url = image_map[sc_cfg["key"]]
            break

    available_keys = list(image_map.keys())
    logger.info(f"  利用可能: {available_keys}")

    # 4. 記事生成
    logger.info("[4/5] Claude 記事生成...")
    article = generate_article(topic, available_keys)

    # プレースホルダーを img タグに置換
    content = article["content"]
    for key, (_, url) in image_map.items():
        img_html = (
            f'<img src="{url}" alt="BingX {key}" '
            f'style="width:100%;height:auto;border-radius:6px;margin:12px 0;" loading="lazy">'
        )
        placeholder = f"{{{{IMG_{key.upper()}}}}}"
        content = content.replace(f"<figure>{placeholder}</figure>", f"<figure>{img_html}</figure>")
        content = content.replace(placeholder, img_html)

    # 残ったプレースホルダーを削除
    content = re.sub(r"<figure>\{\{IMG_[A-Z0-9_]+\}\}</figure>", "", content)
    content = re.sub(r"\{\{IMG_[A-Z0-9_]+\}\}", "", content)

    # 5. WordPress 投稿
    logger.info("[5/5] WordPress 投稿...")
    category_id = wp.get_or_create_category("取引所")

    result = wp.post_article(
        title=article["title"],
        content=content,
        excerpt=article["excerpt"],
        tags=topic["tags"],
        category_id=category_id,
        featured_media_id=featured_media_id,
        status="publish",
        slug=topic["slug"],
        featured_image_url=featured_image_url,
        article_section="取引所",
    )

    article_url = result.get("link", "")
    logger.info(f"=== 公開完了: {article_url} ===")

    # X 投稿
    post_tweet(
        title=article["title"],
        article_url=article_url,
        tags=topic["tags"],
        tweet_bullets=topic["tweet_bullets"],
        article_section="取引所",
    )

    # 投稿済みに記録
    posted = load_posted_ids()
    posted.append(topic["id"])
    save_posted_ids(posted)
    logger.info(f"投稿済みトピック記録: {topic['id']} ({len(posted)}/{len(TOPICS)}件完了)")


if __name__ == "__main__":
    asyncio.run(main())
