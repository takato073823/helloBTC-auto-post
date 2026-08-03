#!/usr/bin/env python3
"""BingX関連の解説記事を、重複を避けて順番に生成・公開する。

記事は広告・PRであることを明示し、日本居住者の利用可否・登録状況・
居住地制限を公式情報で確認するよう案内する。全テーマの公開後に同じURLを
再投稿することはせず、新しいテーマの追加または既存記事の更新を待つ。
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).parent))
from wp_poster import WordPressAPI
from x_poster import post_tweet
from llm_client import generate_json
from image_processing import SAFE_COMPOSITION_PROMPT, fit_image_to_jpeg

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
    '<small>広告・PR</small><br>'
    "<strong>BingX 招待プログラム</strong><br>"
    f"招待コード：<strong>{INVITE_CODE}</strong><br>"
    f'▶ <a href="{INVITE_URL}" target="_blank" rel="nofollow noopener">'
    "登録ページで現在の対象地域・特典条件を確認する</a></div>"
)

INFO_BOX_OPEN  = '<div style="background:#e8f5e9;border-left:4px solid #4caf50;padding:14px 18px;margin:20px 0;border-radius:4px;">'
WARN_BOX_OPEN  = '<div style="background:#fff3e0;border-left:4px solid #ff9800;padding:14px 18px;margin:20px 0;border-radius:4px;">'
BOX_CLOSE      = "</div>"

# 出典リンクボックス（記事末尾に挿入。LLMにURLを作らせず確実な公式ソースのみ）
SOURCE_BOX = (
    '<div style="background:#f5f7fa;border:1px solid #e0e0e0;'
    'padding:14px 18px;margin:24px 0;border-radius:6px;font-size:0.9em;">'
    "<strong>参考・出典</strong>"
    '<ul style="margin:8px 0 0;padding-left:1.2em;">'
    '<li><a href="https://bingx.com/" target="_blank" rel="nofollow noopener">BingX公式サイト</a></li>'
    '<li><a href="https://bingxservice.zendesk.com/hc/ja" target="_blank" rel="nofollow noopener">'
    "BingX公式ヘルプセンター（日本語）</a></li>"
    "</ul></div>"
)

BITGET_MIGRATION_SOURCE_BOX = (
    '<div style="background:#f5f7fa;border:1px solid #e0e0e0;'
    'padding:14px 18px;margin:24px 0;border-radius:6px;font-size:0.9em;">'
    "<strong>参考・出典</strong>"
    '<ul style="margin:8px 0 0;padding-left:1.2em;">'
    '<li><a href="https://www.bitget.com/ja/support/articles/12560603890270" target="_blank" rel="nofollow noopener">'
    "Bitget公式：日本居住者への重要なお知らせ</a></li>"
    '<li><a href="https://www.bitget.com/ja/support/articles/12560603890271" target="_blank" rel="nofollow noopener">'
    "Bitget公式：日本居住者向けFAQ</a></li>"
    '<li><a href="https://www.fsa.go.jp/menkyo/menkyoj/kasoutuka.pdf" target="_blank" rel="noopener">'
    "金融庁：暗号資産交換業者登録一覧</a></li>"
    "</ul></div>"
)

JAPAN_RESIDENT_NOTICE = (
    '<p class="hellobtc-japan-resident-notice"><strong>日本居住者の方へ：</strong>'
    '本記事は広告・PRを含む情報提供であり、特定の取引所の利用を勧めるものではない。'
    '海外取引所の利用可否・居住地制限・登録状況・手数料・出金条件は変わり得るため、'
    '<a href="https://www.fsa.go.jp/menkyo/menkyoj/kasoutuka.pdf" target="_blank" rel="noopener">金融庁の登録一覧</a>'
    'と各事業者の公式規約を確認したうえで判断すること。</p>'
)

# 地域制限(米国IP)・URL変更により bingx.com のライブ撮影は失敗し、
# 「Access prohibited」や404画面を撮ってしまうため無効化。Imagen概念画像のみ使う。
USE_LIVE_SCREENSHOTS = False

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
            "最後に、利用目的ごとの確認項目を整理する。BingX・Bybitのいずれかを一律に推奨しない。"
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
            "各項目の確認方法と利用目的ごとの留意点を整理する。BingX・Binanceのいずれかを一律に推奨しない。"
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
            "初めて取引する場合の少額テスト・リスク管理・公式規約の確認を解説する。"
            "よくある疑問（利用条件・出金・本人確認）は、断定せず公式情報の確認方法を示す。"
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
    # -----------------------------------------------------------------------
    # ロングテール（競合が薄く検索意図が深い・新規ドメインでも上位を取りやすい）
    # -----------------------------------------------------------------------
    {
        "id": "japan-legal",
        "keyword": "BingX 日本人 使える 違法 安全",
        "slug": "bingx-japan-legal-safe",
        "tags": ["BingX", "日本人", "違法", "安全性", "海外取引所"],
        "type": "guide",
        "article_guide": (
            "「BingXは日本人が使っても違法ではないのか・安全なのか」という不安に答える記事。"
            "法的評価や利用可否を本文だけで断定せず、金融庁の登録一覧、利用規約、居住地制限を確認する手順を示す。"
            "利用上の自己責任・税務申告・カストディや出金に伴うリスクを解説し、必要に応じて専門家へ相談するよう案内する。"
            "結論は、個別の取引所を勧めず、公式情報を確認したうえで判断することとする。"
        ),
        "tweet_bullets": ["海外取引所の利用自体は違法ではない", "日本語対応で初心者でも安心", "税務申告の義務だけは要注意"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "japanese flag and digital scales of justice concept, legal compliance, dark background",
            "secure digital shield over world map, global access concept, dark interface",
        ],
    },
    {
        "id": "withdraw-trouble",
        "keyword": "BingX 出金できない 原因 対処法",
        "slug": "bingx-withdrawal-troubleshooting",
        "tags": ["BingX", "出金できない", "対処法", "トラブル", "海外取引所"],
        "type": "tutorial",
        "article_guide": (
            "「BingXで出金できない」ときの原因と対処法を網羅する記事。"
            "①KYС未完了、②出金アドレス/ネットワークの誤り、③出金上限・最低額未達、"
            "④セキュリティ審査（24時間出金制限・新規アドレス）、⑤メンテナンス、の原因別に"
            "チェックリストと解決手順を示す。問い合わせ先（サポート）への連絡方法も含める。"
        ),
        "tweet_bullets": ["まずKYC完了とネットワーク選択を確認", "新規アドレスは24時間出金制限あり", "原因別チェックリストで即解決"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "error warning triangle on cryptocurrency wallet screen, troubleshooting concept, dark UI",
            "checklist with red and green marks on dark screen, problem solving concept",
        ],
    },
    {
        "id": "deposit-trouble",
        "keyword": "BingX 入金 反映されない 対処法",
        "slug": "bingx-deposit-not-credited",
        "tags": ["BingX", "入金反映されない", "対処法", "USDT", "トラブル"],
        "type": "tutorial",
        "article_guide": (
            "「BingXに入金したのに反映されない」ときの原因と対処法を解説する記事。"
            "①ネットワーク選択ミス（TRC20/ERC20/BEP20の取り違え）、②必要承認数の待機、"
            "③タグ/メモの記入漏れ、④最低入金額未達、⑤送金先アドレス誤り、を原因別に解説。"
            "ブロックチェーンエクスプローラーでの着金確認方法と、サポートへの問い合わせ手順も含める。"
        ),
        "tweet_bullets": ["原因の多くはネットワーク選択ミス", "エクスプローラーで着金状況を確認", "承認数待ちなら時間で反映される"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "pending transaction hourglass on blockchain network, waiting concept, dark background",
            "cryptocurrency network selection menu on phone screen, glowing options, dark UI",
        ],
    },
    {
        "id": "login-trouble",
        "keyword": "BingX ログインできない 対処法 パスワード",
        "slug": "bingx-login-troubleshooting",
        "tags": ["BingX", "ログインできない", "パスワード", "2段階認証", "対処法"],
        "type": "tutorial",
        "article_guide": (
            "「BingXにログインできない」ときの原因と対処法を解説する記事。"
            "①パスワード忘れ（リセット手順）、②2段階認証（2FA）コードが通らない、"
            "③メール/SMS認証コードが届かない、④アカウントロック、⑤端末変更時の確認、"
            "を原因別に解決手順で示す。2FA端末を紛失した場合の復旧申請方法も含める。"
        ),
        "tweet_bullets": ["パスワードはメールから即リセット可能", "2FAコードは端末の時刻ズレが原因のことも", "認証コード未着はスパムフォルダを確認"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "locked padlock on login screen, access denied concept, dark interface",
            "two factor authentication code on smartphone, security verification, dark background",
        ],
    },
    {
        "id": "min-deposit",
        "keyword": "BingX 最低入金額 少額 いくらから",
        "slug": "bingx-minimum-deposit",
        "tags": ["BingX", "最低入金額", "少額", "初心者", "入金"],
        "type": "guide",
        "article_guide": (
            "「BingXは最低いくらから始められるのか」に答える記事。"
            "仮想通貨入金・クレジットカード購入・P2Pそれぞれの最低額の目安、"
            "コピートレード（10 USDT〜）や現物・先物の最小注文額、"
            "少額から始める初心者向けのおすすめ手順（まず少額で操作に慣れる）を解説する。"
        ),
        "tweet_bullets": ["コピートレードは10 USDTから可能", "少額で操作に慣れるのが初心者の鉄則", "入金方法ごとに最低額が異なる"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "small stack of coins growing into larger stack, small start concept, dark background",
            "minimum amount input field on trading app, glowing numbers, dark interface",
        ],
    },
    {
        "id": "invite-code-where",
        "keyword": "BingX 招待コード 入力 どこ 後から",
        "slug": "bingx-invite-code-where-to-enter",
        "tags": ["BingX", "招待コード", "入力方法", "登録", "特典"],
        "type": "tutorial",
        "article_guide": (
            "「BingXの招待コードはどこに入力するのか・後から入力できるのか」に答える記事。"
            f"登録画面での招待コード{INVITE_CODE}の入力位置を画像付きで示し、"
            "入力し忘れた場合に後から適用できるか（基本は登録時のみ）、"
            "招待コードを入れるメリット（手数料割引・ボーナス）を具体的に解説する。"
            "招待コードが反映されているかの確認方法も含める。"
        ),
        "tweet_bullets": [f"招待コードは登録画面で{INVITE_CODE}を入力", "後からの追加は原則不可・登録時に必ず入力", "入力で手数料割引＆ボーナスが適用"],
        "screenshot_pages": [
            {"key": "invite", "url": "https://bingxdao.com/invite/XXCCJX/", "description": "BingX招待コード入力ページ", "viewport": {"width": 1280, "height": 800}, "wait_ms": 4000},
        ],
        "imagen_prompts": [
            "referral code input box highlighted on registration screen, glowing field, dark UI",
        ],
    },
    {
        "id": "withdraw-time",
        "keyword": "BingX 出金 時間 反映 どのくらい かかる",
        "slug": "bingx-withdrawal-time",
        "tags": ["BingX", "出金時間", "反映時間", "送金", "手数料"],
        "type": "guide",
        "article_guide": (
            "「BingXの出金はどのくらい時間がかかるのか」に答える記事。"
            "出金申請から着金までの一般的な所要時間（通常数分〜数十分）、"
            "ネットワーク別の速度・混雑時の遅延、初回出金やセキュリティ審査による遅延、"
            "出金が遅いときに確認すべきポイントを解説する。早く着金させるコツも含める。"
        ),
        "tweet_bullets": ["通常は数分〜数十分で着金", "ネットワーク混雑時は遅延することも", "初回や新規アドレスは審査で遅くなる"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "stopwatch over cryptocurrency transfer, speed concept, dark background",
            "fast moving digital coins through network tunnel, transfer speed, dark interface",
        ],
    },
    {
        "id": "demo-trade",
        "keyword": "BingX デモトレード やり方 練習",
        "slug": "bingx-demo-trading-guide",
        "tags": ["BingX", "デモトレード", "練習", "初心者", "先物取引"],
        "type": "tutorial",
        "article_guide": (
            "BingXのデモトレード（仮想資金での練習取引）のやり方を解説する記事。"
            "デモ口座への切り替え手順、仮想USDTの取得・リセット方法、"
            "デモで練習すべきこと（注文方法・レバレッジ・損切り設定）、"
            "本番に移行するタイミングの目安を初心者向けに解説する。"
        ),
        "tweet_bullets": ["仮想資金でノーリスク練習が可能", "注文・レバレッジ操作に慣れてから本番へ", "デモ資金はリセットして何度でも練習"],
        "screenshot_pages": [
            {"key": "futures", "url": "https://bingx.com/en-us/perpetual/BTCUSDT/", "description": "先物取引画面（デモ切替）", "viewport": {"width": 1440, "height": 900}, "wait_ms": 7000},
        ],
        "imagen_prompts": [
            "practice mode simulation on trading screen, training concept, dark interface",
        ],
    },
    {
        "id": "card-deposit-trouble",
        "keyword": "BingX クレジットカード 入金できない 対処",
        "slug": "bingx-card-deposit-failed",
        "tags": ["BingX", "クレジットカード", "入金できない", "対処法", "決済"],
        "type": "tutorial",
        "article_guide": (
            "「BingXでクレジットカード入金（仮想通貨購入）ができない」ときの原因と対処法を解説する記事。"
            "①カード会社による海外/暗号資産決済のブロック、②3Dセキュア未対応、"
            "③限度額・利用可能枠不足、④対応ブランドの確認、⑤本人確認(KYC)未完了、を原因別に解説。"
            "代替手段（仮想通貨送金・P2P）への切り替えも案内する。"
        ),
        "tweet_bullets": ["カード会社が暗号資産決済をブロックしがち", "3Dセキュア対応カードを使う", "ダメなら仮想通貨送金/P2Pが確実"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "declined credit card with red cross on payment screen, error concept, dark background",
            "alternative payment methods icons glowing on dark interface, options concept",
        ],
    },
    {
        "id": "2fa-guide",
        "keyword": "BingX 2段階認証 設定 解除 やり方",
        "slug": "bingx-2fa-setup-guide",
        "tags": ["BingX", "2段階認証", "2FA", "セキュリティ", "設定"],
        "type": "tutorial",
        "article_guide": (
            "BingXの2段階認証（2FA）の設定・解除・変更方法を解説する記事。"
            "Google Authenticatorを使った2FA設定手順、"
            "バックアップキー（復旧コード）の保管の重要性、"
            "端末を機種変更/紛失したときの2FA再設定・復旧手順、解除方法を画像付きで解説する。"
            "2FA設定がなぜ重要か（不正出金防止）も触れる。"
        ),
        "tweet_bullets": ["Google Authenticatorで2FAを設定", "バックアップキーは必ず保管", "機種変更前に2FA解除or移行を忘れずに"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "authenticator app generating security code, two factor concept, dark background",
            "backup key written on paper next to smartphone, recovery concept, dark aesthetic",
        ],
    },
    # -----------------------------------------------------------------------
    # キーワードギャップ（競合大手が専用記事を持たない＝穴。SERP調査で検証済み）
    # -----------------------------------------------------------------------
    {
        "id": "account-delete",
        "keyword": "BingX 退会 アカウント削除 方法",
        "slug": "bingx-account-deletion-guide",
        "tags": ["BingX", "退会", "アカウント削除", "解約", "海外取引所"],
        "type": "tutorial",
        "article_guide": (
            "BingXの退会・アカウント削除の方法を解説する記事。"
            "削除前にやるべきこと（全資産の出金・未決済注文の決済）、"
            "カスタマーサービス経由での削除申請手順、必要書類（本人確認書類＋手書きメモの自撮り）、"
            "処理にかかる期間（1〜5営業日）、削除は取り消し不可という注意点を解説する。"
            "「使わないなら放置でよいのか／削除すべきか」の判断も添える。"
            "※競合の仮想通貨メディアが専用記事を持たない穴キーワード。網羅性で上位を狙う。"
        ),
        "tweet_bullets": ["削除前に全資産の出金を忘れずに", "削除はサポート経由で申請（1〜5営業日）", "一度削除すると復旧不可・要注意"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "account deletion trash bin icon on dark screen, closing account concept",
            "step by step removal process flow on dark interface, formal concept",
        ],
    },
    {
        "id": "jpy-withdraw",
        "keyword": "BingX 日本円 出金 方法 国内取引所 送金",
        "slug": "bingx-jpy-withdrawal-guide",
        "tags": ["BingX", "日本円出金", "国内取引所", "送金", "ビットフライヤー"],
        "type": "tutorial",
        "article_guide": (
            "「BingXから日本円を出金する方法」を解説する記事。"
            "BingXは銀行への直接の日本円出金に原則対応していないため、"
            "①BingXでUSDT等をビットコイン/XRP等に替える→②国内取引所（bitFlyer/bitbank/Coincheck等）へ送金→"
            "③国内取引所で日本円に換金して銀行出金、という現実的な手順をステップで解説する。"
            "送金時のネットワーク選択・手数料・反映時間・宛先アドレス登録の注意点を含める。"
            "※「海外取引所→国内→日本円」の導線は競合がBingX向けに作れていない穴。"
        ),
        "tweet_bullets": ["BingXは直接の日本円出金に非対応", "国内取引所へ送金→換金が王道ルート", "送金ネットワークの選択ミスに注意"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "cryptocurrency converting to japanese yen banknotes, exchange concept, dark background",
            "money transfer route diagram between two exchanges, glowing path, dark interface",
        ],
    },
    {
        "id": "api-guide",
        "keyword": "BingX API キー 作成 自動売買 bot 連携",
        "slug": "bingx-api-key-guide",
        "tags": ["BingX", "API", "自動売買", "bot", "連携"],
        "type": "tutorial",
        "article_guide": (
            "BingXのAPIキーの作成方法と、外部の自動売買ツール（bot）との連携手順を日本語で解説する記事。"
            "API管理画面でのキー作成手順、権限設定（読み取り/取引は許可・出金権限はオフが鉄則）、"
            "IPホワイトリスト設定によるセキュリティ強化、"
            "代表的な連携先（TradingView・各種botサービス）の概要、APIキー流出時のリスクと対策を解説する。"
            "※日本語の解説がほぼ無く英語ソースばかりの穴キーワード。日本語で網羅し独占を狙う。"
        ),
        "tweet_bullets": ["APIキーは出金権限を必ずオフに", "IPホワイトリストでセキュリティ強化", "日本語で分かるBingX API連携ガイド"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "API key generation screen with code strings, developer concept, dark interface",
            "connected nodes between trading platforms, integration concept, glowing dark background",
        ],
    },
    {
        "id": "tradingview-guide",
        "keyword": "BingX TradingView 連携 自動売買 やり方",
        "slug": "bingx-tradingview-integration",
        "tags": ["BingX", "TradingView", "自動売買", "シグナル取引", "連携"],
        "type": "tutorial",
        "article_guide": (
            "BingXとTradingViewを連携して自動売買する方法を日本語で解説する記事。"
            "①TradingViewから直接BingXで取引する簡易連携（口座リンク手順・対応はUSDT建て先物）、"
            "②Signal Trading機能でTradingViewのアラートから自動売買する方法（ストラテジー設定の流れ）、"
            "の2パターンをステップで解説する。メリット・注意点・リスク管理も含める。"
            "※英語ソースと公式ブログしか無い穴。日本語の実践ガイドで上位を狙う。"
        ),
        "tweet_bullets": ["TradingViewから直接BingXで発注できる", "シグナル連携でアラート自動売買も可能", "対応はUSDT建て先物が中心"],
        "screenshot_pages": [
            {"key": "tv", "url": "https://bingx.com/en/tradingView/", "description": "BingX TradingView連携ページ", "viewport": {"width": 1440, "height": 900}, "wait_ms": 6000},
        ],
        "imagen_prompts": [
            "trading chart with automated signal arrows, algorithmic concept, dark interface",
        ],
    },
    {
        "id": "wealth-guide",
        "keyword": "BingX Wealth Earn デュアル投資 資産運用 利息",
        "slug": "bingx-wealth-earn-guide",
        "tags": ["BingX", "Wealth", "Earn", "デュアル投資", "資産運用"],
        "type": "guide",
        "article_guide": (
            "BingXの資産運用機能（Wealth / Earn / デュアル投資）で利息・不労所得を得る方法を解説する記事。"
            "①Wealth/Earn（柔軟・固定の利息運用、APRの目安、利息の計算例）、"
            "②デュアル投資（Buy Low / Sell High の仕組み、どんな相場で使うか）、"
            "を初心者向けに整理する。元本リスク・注意点・他の運用（コピー/グリッド）との違いも触れる。"
            "※公式と海外サイトしか無い穴。日本語で網羅し『BingXで増やす』需要を取る。"
        ),
        "tweet_bullets": ["余剰USDTを利息運用できるBingX Earn", "デュアル投資はBuy Low/Sell Highで利息獲得", "固定・柔軟から選べる資産運用"],
        "screenshot_pages": [
            {"key": "earn", "url": "https://bingx.com/en/wealth/earn/", "description": "BingX Earn 資産運用ページ", "viewport": {"width": 1440, "height": 900}, "wait_ms": 6000},
        ],
        "imagen_prompts": [
            "passive income growing coins with upward arrow, savings concept, dark background",
            "percentage yield APR glowing on financial screen, interest concept, dark interface",
        ],
    },
    {
        "id": "tax-tool",
        "keyword": "BingX 取引履歴 ダウンロード 損益計算 やり方",
        "slug": "bingx-trade-history-export",
        "tags": ["BingX", "取引履歴", "損益計算", "Gtax", "確定申告"],
        "type": "tutorial",
        "article_guide": (
            "BingXの取引履歴をダウンロードし、損益計算ツールで集計するまでの手順を解説する記事。"
            "資産履歴/取引履歴のエクスポート手順（期間指定・Excel/CSV出力）、"
            "損益計算ツール（Gtax・クリプタクト等）へのインポート方法、"
            "計算結果を確定申告にどう使うかの流れを解説する。"
            "（税制の詳細は別記事「BingXの税金・確定申告」へ内部リンクで誘導し、本記事は“手順”に特化）"
            "※ツールベンダーの断片的ヘルプしか無く、まとめた解説が無い穴キーワード。"
        ),
        "tweet_bullets": ["資産履歴からCSV/Excelで履歴を出力", "Gtaxやクリプタクトに取込んで自動集計", "確定申告前の損益計算をこれ1本で"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "spreadsheet with transaction history rows on screen, data export concept, dark interface",
            "calculator and tax documents with crypto coins, accounting concept, dark desk",
        ],
    },
    # 日本居住者向けの確認・比較ニーズ。Bitgetの利用条件変更を機に検索され得る
    # テーマだが、BingXへの移行を事実として扱ったり、利用を推奨したりしない。
    {
        "id": "japan-terms-check",
        "keyword": "BingX 日本居住者 利用条件 確認",
        "slug": "bingx-japan-resident-terms-check",
        "tags": ["BingX", "日本居住者", "利用条件", "暗号資産取引所", "注意点"],
        "type": "guide",
        "priority": True,
        "article_guide": (
            "日本居住者がBingXを検討する前に確認すべき項目を、公式規約・本人確認・"
            "居住地制限・入出金条件・金融庁の登録一覧という観点で整理する。"
            "利用できる、合法、安全と断定しない。BingXの登録状況は閲覧時点の金融庁資料と"
            "公式情報で必ず確認するよう案内し、国内の登録業者も比較対象になり得ることを説明する。"
        ),
        "tweet_bullets": ["利用可否は居住地・公式規約で確認", "登録状況と出金条件を事前に確認", "送金前は少額テストを行う"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "cryptocurrency exchange terms checklist on a laptop screen, Japanese city at night, calm editorial style, no logos, no text",
            "secure crypto wallet transfer preparation, checklist and shield icon, dark blue background, no logos, no text",
        ],
    },
    {
        "id": "bitget-asset-checklist",
        "keyword": "Bitget 出金 資産移動 確認項目 BingX",
        "slug": "bitget-asset-transfer-checklist-bingx",
        "tags": ["Bitget", "BingX", "出金", "資産移動", "仮想通貨取引所"],
        "type": "guide",
        "priority": True,
        "include_affiliate_cta": False,
        "source_box": BITGET_MIGRATION_SOURCE_BOX,
        "article_guide": (
            "Bitgetの日本居住者向け公式案内を確認するための資産移動チェックリストを作る。"
            "出金期限・対象サービス・本人確認・送金先アドレス・ネットワーク・少額テスト・"
            "取引履歴の保存を、公式告知への確認導線とともに説明する。"
            "BingXは送金先候補の一例として触れるに留め、公式な移行先・推奨先と表現しない。"
            "出金期限や利用可否など変動する事実は本文で断定せず、公式告知を確認するよう案内する。"
        ),
        "tweet_bullets": ["まずBitget公式告知で対象・期限を確認", "送金先とネットワークを二重確認", "初回は少額テストから進める"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "cryptocurrency asset transfer checklist with secure wallet and verification steps, professional editorial style, no logos, no text",
            "blockchain transfer route between wallets with shield icon, dark blue financial technology concept, no logos, no text",
        ],
    },
    {
        "id": "bitget-alternative-check",
        "keyword": "Bitget 代替 取引所 比較 BingX 日本居住者",
        "slug": "bitget-alternatives-bingx-checklist",
        "tags": ["Bitget", "BingX", "取引所比較", "日本居住者", "暗号資産"],
        "type": "comparison",
        "priority": True,
        "include_affiliate_cta": False,
        "source_box": BITGET_MIGRATION_SOURCE_BOX,
        "article_guide": (
            "Bitgetの利用条件変更を受けて取引所を比較する際の確認項目を解説する。"
            "国内の登録業者、海外取引所、自己管理ウォレットという選択肢を分け、"
            "登録状況・居住地制限・取扱資産・送金・手数料・カストディリスクを中立に比較する。"
            "BingXは海外取引所の一例として扱うだけで、Bitget利用者が移行している、"
            "またはBingXが安全な後継先であるとは書かない。"
        ),
        "tweet_bullets": ["比較前に金融庁の登録一覧を確認", "利用条件と出金先を個別に確認", "特定の取引所を一律に推奨しない"],
        "screenshot_pages": [],
        "imagen_prompts": [
            "neutral cryptocurrency exchange comparison checklist, multiple generic exchange panels, Japanese editorial style, no brand logos, no text",
            "digital asset custody choices with wallet and exchange icons, balanced neutral infographic style, no logos, no text",
        ],
    },
]

# ---------------------------------------------------------------------------
# 内部リンク・トピッククラスター
# ---------------------------------------------------------------------------
# 公開済みのBingX記事同士を相互リンクし、「BingXといえばhelloBTC」という
# トピカルオーソリティをGoogleに認識させる。新記事の公開ごとに既存記事の
# クラスターも作り直し、双方向リンクを維持する（ハブ&スポーク構造）。
# ---------------------------------------------------------------------------

CLUSTER_START = "<!--BINGX_CLUSTER_START-->"
CLUSTER_END = "<!--BINGX_CLUSTER_END-->"

# ピラー（ハブ）記事 — 全記事がここへ集約しリンクする
PILLAR_ID = "beginner-guide"

# クラスター内のリンク文言（descriptive anchor — SEO上、説明的なアンカーが有効）
CLUSTER_ANCHORS = {
    "beginner-guide":    "BingXの始め方・完全ガイド【初心者向け】",
    "kyc-guide":         "BingXの本人確認（KYC）のやり方",
    "deposit-guide":     "BingXの入金方法（仮想通貨・クレカ・P2P）",
    "withdraw-guide":    "BingXの出金・送金方法と手数料",
    "spot-trade-guide":  "BingXの現物取引のやり方・注文方法",
    "futures-guide":     "BingXの先物取引・レバレッジの使い方",
    "leverage-guide":    "BingXのレバレッジ設定と証拠金の計算",
    "copy-trade-guide":  "BingXのコピートレードの始め方",
    "grid-trade-guide":  "BingXのグリッドトレード（自動売買）設定",
    "app-guide":         "BingXスマホアプリの使い方",
    "fees-guide":        "BingXの手数料を徹底解説",
    "bonus-guide":       "BingXの招待コード・ボーナス特典",
    "review":            "BingXの評判・口コミとメリット/デメリット",
    "security-guide":    "BingXの安全性・セキュリティ",
    "vs-bybit":          "BingXとBybitを比較",
    "vs-binance":        "BingXとBinanceを比較",
    "earn-guide":        "BingXで稼ぐ方法・投資戦略",
    "tax-guide":         "BingXの税金・確定申告の方法",
    # ロングテール
    "japan-legal":       "BingXは日本人が使っても違法・危険ではない？",
    "withdraw-trouble":  "BingXで出金できないときの原因と対処法",
    "deposit-trouble":   "BingXの入金が反映されないときの対処法",
    "login-trouble":     "BingXにログインできないときの対処法",
    "min-deposit":       "BingXは最低いくらから？最低入金額まとめ",
    "invite-code-where": "BingXの招待コードはどこに入力する？",
    "withdraw-time":     "BingXの出金にかかる時間の目安",
    "demo-trade":        "BingXのデモトレードのやり方",
    "card-deposit-trouble": "BingXでクレカ入金できないときの対処法",
    "2fa-guide":         "BingXの2段階認証（2FA）の設定方法",
    # キーワードギャップ
    "account-delete":    "BingXの退会・アカウント削除の方法",
    "jpy-withdraw":      "BingXから日本円に出金する方法（国内取引所経由）",
    "api-guide":         "BingXのAPIキー作成・bot連携のやり方",
    "tradingview-guide": "BingX×TradingViewで自動売買するやり方",
    "wealth-guide":      "BingXのWealth/Earn・デュアル投資で利息を得る方法",
    "tax-tool":          "BingXの取引履歴ダウンロードと損益計算の手順",
    # 日本居住者向け確認・Bitget関連
    "japan-terms-check": "BingXを検討する前の日本居住者向け確認項目",
    "bitget-asset-checklist": "Bitgetからの資産移動で確認するチェックリスト",
    "bitget-alternative-check": "Bitgetの代替候補を比較する際の確認項目",
}

# スラッグ → トピックid の逆引き
_SLUG_TO_ID = {t["slug"]: t["id"] for t in TOPICS}


def _build_cluster_html(published: list[dict], current_slug: str) -> str:
    """current 以外の公開済みBingX記事へのリンク集（クラスターボックス）を生成。
    ピラー（完全ガイド）を先頭に配置する。published は {slug, link} のリスト。"""
    others = [p for p in published if p["slug"] != current_slug]
    if not others:
        return ""

    def sort_key(p):
        tid = _SLUG_TO_ID.get(p["slug"], "")
        return (0 if tid == PILLAR_ID else 1, tid)

    others.sort(key=sort_key)

    items = []
    for p in others:
        tid = _SLUG_TO_ID.get(p["slug"], "")
        anchor = CLUSTER_ANCHORS.get(tid, p.get("title", "BingX関連記事"))
        items.append(
            f'<li style="margin:6px 0;"><a href="{p["link"]}">{anchor}</a></li>'
        )

    box = (
        '<div style="background:#f5f7fa;border:1px solid #e0e0e0;'
        'padding:18px 22px;margin:32px 0;border-radius:8px;">'
        '<strong style="display:block;margin-bottom:10px;font-size:1.05em;">'
        '📚 BingXをもっと知る（関連記事）</strong>'
        f'<ul style="margin:0;padding-left:1.2em;line-height:1.7;">{"".join(items)}</ul>'
        "</div>"
    )
    return f"{CLUSTER_START}{box}{CLUSTER_END}"


def _upsert_cluster(content: str, cluster_html: str) -> str:
    """記事本文にクラスターを差し込む。既存ブロックがあれば置換、なければ
    末尾の免責文の直前（無ければ最後）に挿入する。冪等。"""
    if not cluster_html:
        return content

    # 既存クラスターを置換
    pattern = re.compile(
        re.escape(CLUSTER_START) + ".*?" + re.escape(CLUSTER_END),
        re.DOTALL,
    )
    if pattern.search(content):
        return pattern.sub(cluster_html, content)

    # 免責文の直前に挿入
    disclaimer = re.search(r'<p style="font-size:0\.85em', content)
    if disclaimer:
        idx = disclaimer.start()
        return content[:idx] + cluster_html + content[idx:]

    return content + cluster_html


def sync_clusters(wp) -> None:
    """公開済みの全BingX記事を取得し、各記事の内部リンククラスターを
    最新状態に作り直す。これにより新記事↔既存記事の双方向リンクが張られる。"""
    slugs = [t["slug"] for t in TOPICS]
    posts = wp.get_posts_by_slugs(slugs)
    if not posts:
        logger.info("  クラスター同期: 公開済みBingX記事が見つかりません")
        return

    published = [
        {"slug": p["slug"], "link": p["link"], "id": p["id"],
         "raw": p.get("content", {}).get("raw", ""),
         "title": p.get("title", {}).get("raw", "")}
        for p in posts
    ]
    logger.info(f"  クラスター同期: {len(published)}記事を相互リンク")

    updated = 0
    for p in published:
        cluster = _build_cluster_html(published, p["slug"])
        new_content = _upsert_cluster(p["raw"], cluster)
        if new_content != p["raw"]:
            try:
                wp.update_post_content(p["id"], new_content)
                updated += 1
            except Exception as e:
                logger.warning(f"  ✗ クラスター更新失敗 (post {p['id']}): {e}")
    logger.info(f"  ✓ クラスター更新: {updated}/{len(published)}記事")


# ---------------------------------------------------------------------------
# 重複タイトルチェック
# ---------------------------------------------------------------------------

def fetch_existing_titles(wp, query: str = "BingX") -> list[str]:
    """公開済み記事のタイトル一覧を取得（重複回避のため）。"""
    try:
        posts = wp._request(
            "GET", "posts",
            params={"search": query, "per_page": 100, "status": "publish", "_fields": "title"},
        )
        import html as _html
        return [_html.unescape(p.get("title", {}).get("rendered", "")) for p in posts]
    except Exception as e:
        logger.warning(f"  既存タイトル取得に失敗: {e}")
        return []


def _normalize_title(t: str) -> str:
    """比較用にタイトルを正規化（記号・空白・年号などを除去）。"""
    t = re.sub(r"[【】\[\]｜|（）()・,、。\s〜~\-—_:：!！?？]", "", t)
    t = re.sub(r"20\d{2}年?", "", t)
    return t.lower()


def is_duplicate_title(title: str, existing: list[str]) -> bool:
    """既存タイトルと「ほぼ同一」かを判定する最終セーフティネット。
    意味的な類似（言い換え）は誤検出を避けるためここでは弾かず、LLMへの
    既存タイトル提示（avoid_titles）側で防ぐ。ここは正規化後の一致率0.95以上のみ。
    ※「入金方法ガイド」と「出金方法ガイド」のような別記事は弾かない閾値。"""
    import difflib
    n = _normalize_title(title)
    if len(n) < 6:
        return False
    for e in existing:
        ne = _normalize_title(e)
        if not ne:
            continue
        if n == ne or difflib.SequenceMatcher(None, n, ne).ratio() >= 0.95:
            return True
    return False


# ---------------------------------------------------------------------------
# FAQ 構造化データ（FAQPage JSON-LD）
# ---------------------------------------------------------------------------

def build_faq_schema_html(faq: list[dict]) -> str:
    """記事の faq 配列から FAQPage JSON-LD を生成。リッチリザルト獲得を狙う。"""
    entries = []
    for item in faq:
        q = (item.get("q") or "").strip()
        a = (item.get("a") or "").strip()
        if not q or not a:
            continue
        entries.append({
            "@type": "Question",
            "name": q,
            "acceptedAnswer": {"@type": "Answer", "text": a},
        })
    if not entries:
        return ""
    schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": entries,
    }
    schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    return f'<script type="application/ld+json">{schema_json}</script>\n'


def build_faq_section_html(faq: list[dict]) -> str:
    """記事末尾に表示するFAQセクション（可視）を生成。"""
    rows = []
    for item in faq:
        q = (item.get("q") or "").strip()
        a = (item.get("a") or "").strip()
        if not q or not a:
            continue
        rows.append(
            f'<dt style="font-weight:bold;margin-top:14px;">Q. {q}</dt>'
            f'<dd style="margin:6px 0 0;padding-left:1em;">A. {a}</dd>'
        )
    if not rows:
        return ""
    return (
        '<h3>よくある質問（FAQ）</h3>'
        f'<dl style="margin:16px 0;">{"".join(rows)}</dl>'
    )


def insert_required_notice(content: str) -> str:
    """広告・PRと日本居住者向け確認事項を、本文上部に一度だけ表示する。"""
    if "hellobtc-japan-resident-notice" in content:
        return content
    first_paragraph = re.search(r"</p>", content, flags=re.IGNORECASE)
    if first_paragraph:
        return content[:first_paragraph.end()] + JAPAN_RESIDENT_NOTICE + content[first_paragraph.end():]
    return JAPAN_RESIDENT_NOTICE + content


def safe_tweet_bullets(topic: dict) -> list[str]:
    """数値や優位性を断定しない、記事公開告知用の要点だけを返す。"""
    if topic["id"].startswith("bitget-"):
        return topic["tweet_bullets"]
    return [
        "利用条件・対象地域は公式情報で確認",
        "送金・出金前に手数料とネットワークを確認",
        "暗号資産取引には価格変動などのリスクがある",
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


def pick_next_topic(existing_slugs: set[str] | None = None) -> dict | None:
    """未公開のテーマを順番に返す。

    既に同じスラッグの記事があるテーマは投稿済みとして記録し、全テーマを
    消化した後は ``None`` を返す。同じURLでの記事再投稿を防ぐため、リセットは
    行わない。
    """
    existing_slugs = existing_slugs or set()
    posted = load_posted_ids()
    posted_set = set(posted)
    changed = False

    # 時事性の高い確認・比較テーマを先に出し、その後は従来の順番を保つ。
    ordered_topics = sorted(TOPICS, key=lambda topic: not topic.get("priority", False))
    for topic in ordered_topics:
        if topic["id"] in posted_set:
            continue
        if topic["slug"] in existing_slugs:
            logger.info("既存スラッグのためスキップ: %s", topic["slug"])
            posted.append(topic["id"])
            posted_set.add(topic["id"])
            changed = True
            continue
        if changed:
            save_posted_ids(posted)
        return topic

    if changed:
        save_posted_ids(posted)
    logger.info("未公開のBingXテーマはありません。新しいテーマの追加または既存記事の更新を待ちます。")
    return None


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
    if not USE_LIVE_SCREENSHOTS:
        logger.info("  ライブ撮影は無効（地域制限対策）。Imagen概念画像のみ使用します。")
        return {}
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
            f"{SAFE_COMPOSITION_PROMPT}"
            "No text, no people, no faces, no brand logos, no watermarks."
        )
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        response = client.models.generate_images(
            model="imagen-4.0-fast-generate-001",
            prompt=full,
            config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio="16:9"),
        )
        raw = response.generated_images[0].image.image_bytes
        output = fit_image_to_jpeg(raw, width=1200, height=675, quality=90)
        logger.info("  ✓ imagen generated")
        return output
    except Exception as e:
        logger.warning(f"  ✗ imagen failed: {e}")
        return None


def resize_jpeg(raw: bytes) -> bytes:
    return fit_image_to_jpeg(raw, width=1200, height=675, quality=88)


# ---------------------------------------------------------------------------
# OpenAI 記事生成
# ---------------------------------------------------------------------------

_TYPE_INSTRUCTIONS = {
    "tutorial": "手順解説記事。ステップ番号付きのol/ulリストを多用し、注意事項はinfoボックスで強調する。",
    "review":   "評判・レビュー記事。メリット・デメリットをリストアップし、総合評価で締める。客観的・公平な視点で書く。",
    "comparison": "比較記事。HTMLのtableタグで比較表を作成し、用途・確認項目ごとの違いを中立に示す。特定の取引所を推奨する結論にしない。",
    "guide":    "解説ガイド記事。見出しごとに情報を整理し、初心者でも理解できるよう専門用語には説明を添える。",
}

BINGX_ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "content": {"type": "string"},
        "excerpt": {"type": "string"},
        "faq": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "q": {"type": "string"},
                    "a": {"type": "string"},
                },
                "required": ["q", "a"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "content", "excerpt", "faq"],
    "additionalProperties": False,
}


def generate_article(topic: dict, image_keys: list[str], avoid_titles: list[str] | None = None) -> dict:
    img_placeholders = "\n".join(
        f"- {{{{IMG_{k.upper()}}}}}: 対応する画像を配置" for k in image_keys
    )
    type_guide = _TYPE_INSTRUCTIONS.get(topic["type"], "")

    # 重複タイトル回避: 既存記事のタイトルをプロンプトに渡す
    if avoid_titles:
        listed = "\n".join(f"  - {t}" for t in avoid_titles[:40])
        avoid_block = (
            "\n【重複禁止】helloBTCには既に以下のタイトルの記事が存在する。"
            "これらと同一・酷似しないタイトルにすること:\n" + listed + "\n"
        )
    else:
        avoid_block = ""

    if topic.get("include_affiliate_cta", True):
        affiliate_block = (
            "【広告・PR表記と招待プログラムHTML（リード文の末尾と記事末の2箇所に必ず挿入）】\n"
            + AFFILIATE_BOX
        )
    else:
        affiliate_block = (
            "【広告・PR表記】\n"
            "本文の冒頭に「本記事は広告・PRを含む」と明記する。"
            "招待コード・登録リンク・登録を促すCTAは挿入しない。"
        )

    prompt = f"""あなたはSEOに強い仮想通貨専門ライターです。helloBTC向けにBingXについての記事を作成してください。

【記事テーマ】
キーワード: {topic['keyword']}
記事の方針: {topic['article_guide']}
記事タイプ: {topic['type']} — {type_guide}

【BingX 基本情報（記事中に必要に応じて活用）】
- 招待コード: {INVITE_CODE} / 招待URL: {INVITE_URL}
- 機能・対応資産・手数料・対象地域・利用可否は変更され得る。断定せず、公式情報での確認を促す。

【使用できる画像プレースホルダー（適切な位置に配置）】
{img_placeholders if img_placeholders else "（画像なし）"}

{affiliate_block}

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
- 見出し: 最初の見出しだけ<h2>タグ、それ以降の見出しはすべて<h3>タグにする（h2は記事内で1個だけ。残り3〜5個はh3）
- 画像プレースホルダー: <figure>{{{{IMG_xxx}}}}</figure> 形式で配置
- 末尾: <p style="font-size:0.85em;color:#888;">※仮想通貨への投資はリスクを伴います。余裕資金の範囲内で行ってください。</p>
- 外部URLは生成・推測しない。公式URLは投稿処理で別途追加される。
- 数値（手数料、レバレッジ、対応銘柄数、出金時間、特典額など）は、公式出典を本文内で確認できない限り、具体値で断定しない。

【禁止表現】
- 「Bitget利用者がBingXへ移行・乗り換えしている／増えている」といった、根拠のない利用動向の断定。
- BingXをBitgetの公式移行先・推奨先・安全な後継先と表現すること。
- BingXが金融庁登録済み、日本で認可済み、日本居住者が安全に使い続けられる、規制の影響を受けない、と断定すること。
- 「必ず稼げる」「確実」「最適」「安全」といった保証・優位性の表現。
- Bitgetの利用条件変更を利用した登録の煽り、期限を利用した登録の誘導。
- 特典額、手数料割引、対応機能を根拠なしに保証すること。
- 日本居住者向け記事では、金融庁の登録一覧と各事業者の公式規約・居住地制限を確認するよう必ず案内する。
{avoid_block}
必ず以下のJSONのみ出力（前後に余計なテキスト不要）:
{{
  "title": "SEO最適化された日本語タイトル（30文字以内・厳守。具体的で数字を含む。helloBTCの他記事と重複しない独自の表現にする）",
  "content": "<HTML記事本文>",
  "excerpt": "記事の要約（100〜150文字。絵文字や記号は使わない）",
  "faq": [
    {{"q": "この記事のテーマに関する具体的な検索質問", "a": "簡潔で的確な回答（80〜120文字）"}},
    {{"q": "...", "a": "..."}},
    {{"q": "...", "a": "..."}}
  ]
}}
※ faq は実際にユーザーが検索する自然な疑問文を3〜5個。回答は事実ベースで具体的に。"""

    return generate_json(
        prompt,
        schema_name="bingx_seo_article",
        schema=BINGX_ARTICLE_SCHEMA,
        max_output_tokens=8192,
    )


def _parse_article_json(text: str) -> dict:
    """LLM出力からJSONを取り出してパース。文字列内の生の改行・タブを修復してから再試行する。"""
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"JSON が見つかりません: {text[:200]}")

    raw = text[start:end]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # JSON文字列値の内部にある生の改行・タブをエスケープして再パース
    repaired = []
    in_string = False
    skip_next = False
    for ch in raw:
        if skip_next:
            repaired.append(ch)
            skip_next = False
        elif ch == "\\" and in_string:
            repaired.append(ch)
            skip_next = True
        elif ch == '"':
            in_string = not in_string
            repaired.append(ch)
        elif in_string and ch == "\n":
            repaired.append("\\n")
        elif in_string and ch == "\r":
            repaired.append("\\r")
        elif in_string and ch == "\t":
            repaired.append("\\t")
        else:
            repaired.append(ch)

    return json.loads("".join(repaired))


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

async def main():
    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )

    # WordPress上に同一スラッグ（下書きを含む）がないテーマだけを選ぶ。
    # 画像生成より前に確認することで、重複投稿と不要なAPI利用を防ぐ。
    existing_posts = wp.get_posts_by_slugs(
        [topic["slug"] for topic in TOPICS], status="any"
    )
    existing_slugs = {post.get("slug", "") for post in existing_posts}
    topic = pick_next_topic(existing_slugs)
    if topic is None:
        return
    logger.info(f"=== BingX SEO記事生成: [{topic['id']}] {topic['keyword']} ===")

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

    # 4. 記事生成（重複タイトル回避）
    logger.info("[4/5] OpenAI 記事生成...")
    existing_titles = fetch_existing_titles(wp)
    article = generate_article(topic, available_keys, avoid_titles=existing_titles)

    # タイトル重複チェック → 重複なら再生成（最大2回）
    for _ in range(2):
        if not is_duplicate_title(article["title"], existing_titles):
            break
        logger.warning(f"  タイトル重複検出: 「{article['title']}」→ 再生成")
        article = generate_article(topic, available_keys, avoid_titles=existing_titles)

    # タイトル30字以内を保証（超過時は安全に切り詰め）
    if len(article["title"]) > 30:
        logger.info(f"  タイトル30字超過のため切り詰め: {article['title']}")
        article["title"] = article["title"][:30]

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

    # すべてのBingX記事で広告・PRと日本居住者向けの確認事項を明記
    content = insert_required_notice(content)

    # FAQ（可視セクション + FAQPage構造化データ）を挿入
    faq = article.get("faq") or []
    if faq:
        faq_section = build_faq_section_html(faq)
        faq_schema = build_faq_schema_html(faq)
        # 可視FAQは免責文の直前へ、schemaは先頭へ
        disclaimer = re.search(r'<p style="font-size:0\.85em', content)
        if faq_section:
            if disclaimer:
                idx = disclaimer.start()
                content = content[:idx] + faq_section + content[idx:]
            else:
                content = content + faq_section
        content = faq_schema + content
        logger.info(f"  ✓ FAQ {len(faq)}件を挿入")

    # 出典リンクボックスを免責文の直前に挿入。Bitget関連テーマは公式告知を使う。
    source_box = topic.get("source_box", SOURCE_BOX)
    disclaimer = re.search(r'<p style="font-size:0\.85em', content)
    if disclaimer:
        idx = disclaimer.start()
        content = content[:idx] + source_box + content[idx:]
    else:
        content = content + source_box
    logger.info("  ✓ 出典リンクを挿入")

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

    # 5.5 内部リンククラスターを全BingX記事で同期（新記事↔既存記事を相互リンク）
    logger.info("[6/6] 内部リンククラスター同期...")
    try:
        sync_clusters(wp)
    except Exception as e:
        logger.warning(f"  クラスター同期でエラー: {e}")

    # X 投稿
    post_tweet(
        title=article["title"],
        article_url=article_url,
        tags=topic["tags"],
        tweet_bullets=safe_tweet_bullets(topic),
        article_section="取引所",
    )

    # 投稿済みに記録
    posted = load_posted_ids()
    posted.append(topic["id"])
    save_posted_ids(posted)
    logger.info(f"投稿済みトピック記録: {topic['id']} ({len(posted)}/{len(TOPICS)}件完了)")


if __name__ == "__main__":
    asyncio.run(main())
