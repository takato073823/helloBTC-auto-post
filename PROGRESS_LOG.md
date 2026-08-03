# helloBTC 自動記事投稿システム — 進捗ログ

## システム概要

仮想通貨ニュースサイト `hellobtc.jp` 向けの自動記事生成・WordPress投稿・X（Twitter）投稿パイプライン。

---

## 実装済み機能

### 1. 記事自動生成（OpenAI gpt-5.6-luna）
- 英語ニュース（CoinDesk / CoinTelegraph / Decrypt / The Block / Bitcoin Magazine）を RSS で取得
- 低コスト・大量処理向けの gpt-5.6-luna で日本語リライト
- Structured Outputs で title / content / excerpt / tags / slug / image_prompt / tweet_bullets の形式を固定
- ツイート埋め込み（公式 X ポストの oEmbed HTML）対応

### 2. アイキャッチ画像生成（Pillow・API費用なし）
- GitHub Actions 内で暗号資産向けのチャート／ネットワーク画像を生成
- サイズ：1200×630 px（OGP 最適）
- Google 画像APIを呼び出さないため、画像生成費は0円

### 3. WordPress 自動公開
- REST API + Application Password 認証
- 英語スラッグ（3〜5単語、ハイフン区切り）を自動生成
- NewsArticle JSON-LD スキーマをコンテンツ先頭に自動挿入
- カテゴリ・タグ・アイキャッチ画像を自動設定

### 4. SEO 初期設定（setup_seo.py）
- WordPress コア設定最適化（タイトル・説明文・タイムゾーン・コメント無効化）
- Organization スキーマを WP Headers And Footers に手動追加済み
- ニュースサイトマップ：`https://hellobtc.jp/sitemap-news.xml` ✅
- Google Search Console にニュースサイトマップ提出済み ✅
- Google News Publisher Center に helloBTC 登録済み ✅

### 5. X（Twitter）自動投稿
- tweepy + OAuth 1.0a（X API v2）
- 記事公開直後に自動ツイート
- ツイート形式：
  ```
  【カテゴリ】タイトル（45字以内）

  ・要点1
  ・要点2
  ・要点3

  ▶ https://hellobtc.jp/スラッグ/

  #タグ1 #タグ2 #仮想通貨
  ```

---

## スケジュール（GitHub Actions）

| 時刻（JST） | cron（UTC） |
|---|---|
| 07:00 | `0 22 * * *` |
| 12:00 | `0 3 * * *` |
| 15:00 | `0 6 * * *` |
| 18:00 | `0 9 * * *` |
| 20:00 | `0 11 * * *` |
| 22:00 | `0 13 * * *` |

1回の実行で 1記事投稿 → **6記事/日**

---

## GitHub Secrets 一覧

| Secret 名 | 用途 |
|---|---|
| `WP_URL` | WordPress サイト URL |
| `WP_USERNAME` | WordPress ユーザー名 |
| `WP_APP_PASSWORD` | WordPress アプリケーションパスワード |
| `OPENAI_API_KEY` | OpenAI API（記事生成） |
| `X_API_KEY` | X API Key（Consumer Key） |
| `X_API_KEY_SECRET` | X API Key Secret |
| `X_ACCESS_TOKEN` | X Access Token |
| `X_ACCESS_TOKEN_SECRET` | X Access Token Secret |

---

## ファイル構成

```
helloBTC_自動記事投稿/
├── requirements.txt          # Python 依存パッケージ
├── scripts/
│   ├── main.py               # メインスクリプト（ニュース記事）
│   ├── generator.py          # OpenAI で記事生成
│   ├── llm_client.py         # OpenAI Responses API 共通処理
│   ├── local_images.py       # 無料のローカル画像生成
│   ├── scraper.py            # RSS・記事本文スクレイピング
│   ├── wp_poster.py          # WordPress REST API 投稿
│   ├── x_poster.py           # X（Twitter）自動投稿
│   ├── setup_seo.py          # SEO 初期設定（手動・一回限り）
│   └── posted_urls.json      # 投稿済み URL キャッシュ
└── .github/workflows/
    ├── auto_post.yml         # 自動投稿（スケジュール実行）
    └── setup_seo.yml         # SEO 初期設定（手動・一回限り）
```

---

## コスト方針

- 記事生成: gpt-5.6-luna（入力 $0.20 / 100万トークン、出力 $1.20 / 100万トークン）
- 画像生成: $0（Pillowでローカル生成）
- GitHub Actions: GitHub側の利用枠内で実行。PCの起動は不要
- X API: X側の契約・使用量による（別請求）

記事生成の実費は入出力トークン数による。記事数と長さが現状程度であれば、月数ドル以内を目安に運用し、OpenAIの使用上限も設定する。

---

## 動作確認済み（2026-08-03 更新）

- WordPress 記事公開 ✅
- 英語スラッグ生成 ✅
- アイキャッチ画像生成・アップロード ✅
- NewsArticle JSON-LD スキーマ挿入 ✅
- X 自動投稿（箇条書き形式） ✅
- ツイート URL 例：`https://x.com/i/web/status/2065021845691122115`

---

## 今後の課題・改善候補

- [ ] X API クレジット残高の監視（枯渇時アラート）
- [ ] 記事品質チェック（重複投稿検出の強化）
- [ ] SEO 記事（コラム/DeFi/基礎知識/取引所）の公開運用開始
- [ ] IB アフィリエイトリンクの記事への自動挿入
- [ ] アクセス解析・収益トラッキングの仕組み構築
