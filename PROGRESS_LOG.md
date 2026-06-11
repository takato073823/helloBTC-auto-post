# helloBTC 自動記事投稿システム — 進捗ログ

## システム概要

仮想通貨ニュースサイト `hellobtc.jp` 向けの自動記事生成・WordPress投稿・X（Twitter）投稿パイプライン。

---

## 実装済み機能

### 1. 記事自動生成（Claude Haiku 4.5）
- 英語ニュース（CoinDesk / CoinTelegraph / Decrypt / The Block / Bitcoin Magazine）を RSS で取得
- Claude Haiku 4.5 で日本語リライト
- JSON 出力：title / content / excerpt / tags / slug / image_prompt / tweet_bullets
- ツイート埋め込み（公式 X ポストの oEmbed HTML）対応

### 2. アイキャッチ画像生成（Google Imagen 4 Fast）
- スタイル：Reuters/フォトジャーナリズム風、85mm f/2.0 bokeh、muted cool tones
- サイズ：1200×630 px（OGP 最適）
- フォールバック：imagen-4.0-generate-001 → gemini-flash-image

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
| `ANTHROPIC_API_KEY` | Claude API |
| `GOOGLE_API_KEY` | Google Imagen API |
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
│   ├── generator.py          # Claude で記事・画像プロンプト生成
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

## コスト試算（6記事/日）

| サービス | 1日 | 1ヶ月 |
|---|---|---|
| Claude Haiku 4.5（記事生成） | ~$0.06 | ~$1.80 |
| Google Imagen 4 Fast（画像生成） | ~$0.18 | ~$5.40 |
| X API Pay Per Use（ツイート） | - | ~$6（概算） |
| GitHub Actions | 無料 | 無料 |
| **合計** | **~$0.24+** | **約$13（¥1,950）** |

※ コストの大半は Imagen（画像生成）。削減したい場合は画像生成頻度を下げることで対応可能。

---

## 動作確認済み（2026-06-11）

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
