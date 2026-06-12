"""
Claude API を使って日本語 SEO 記事を生成する
"""
import anthropic
import json
import logging
import io

logger = logging.getLogger(__name__)
client = anthropic.Anthropic()

# matplotlib は SEO 記事のグラフ生成にのみ使用（未インストール時はスキップ）
_matplotlib_available = False
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    try:
        import japanize_matplotlib  # noqa: F401 — 日本語フォントを自動設定
    except ImportError:
        pass
    _matplotlib_available = True
except ImportError:
    pass

SEO_ARTICLE_TYPES = ["コラム", "DeFi", "基礎知識", "取引所"]


def generate_article(title, content, source_url, source_name, tweet_urls=None):
    """英語ニュースから SEO 最適化された日本語記事を生成"""

    # ツイートURLがある場合の追加指示
    if tweet_urls:
        numbered = "\n".join(f"  {i+1}. {u}" for i, u in enumerate(tweet_urls))
        tweet_instruction = f"""
【公式ソース（ツイート/X投稿）】
以下は元記事で引用されている公式発表ツイートのURLです:
{numbered}

「公式に発表した」「X（旧Twitter）で明らかにした」などの文脈で言及した段落の直後に、
{{TWEET_1}} というプレースホルダーを1つだけ挿入してください。
（後で実際のツイートカードHTMLに自動置換されます）
記事の流れに合わない場合は挿入しなくてよい。"""
    else:
        tweet_instruction = ""

    prompt = f"""以下の英語の仮想通貨ニュースを基に、SEO最適化された日本語のブログ記事を作成してください。

【元記事】
タイトル: {title}
出典: {source_name} ({source_url})
内容:
{content}
{tweet_instruction}
【サイト情報】
- サイト名: helloBTC
- テーマ: 仮想通貨・ビットコイン情報
- ターゲット読者: 仮想通貨に興味がある日本人（初心者〜中級者）

【記事作成ルール】
1. 元記事をそのまま翻訳せず、独自の視点・解説・背景情報を加えて完全にリライトする
2. 日本の読者向けにわかりやすい言葉で書く（専門用語には簡単な説明を添える）
3. 重要なキーワードを自然に含める
4. H3見出しは3つ設ける。全ての見出しは記事の内容を具体的に表すタイトルにする（「まとめ」「概要」などの汎用的な言葉は使わない）
5. 参照リンクや出典の記載は一切不要
6. コピペと判定されないよう、文章構成・表現・順序を元記事から大きく変える
7. 文体は「〜した」「〜だ」「〜である」の言い切り調で統一する（「〜しました」「〜です」などの丁寧語は使わない）
8. 公式ソース（ツイート）が提供されている場合は、記事の流れに合わせて適切な位置に埋め込む

必ず以下のJSON形式のみで出力してください（前後に余計なテキストを含めないこと）:
{{
  "title": "SEO最適化された日本語タイトル（30〜60文字、数字や具体的な情報を含む）",
  "content": "<h3>具体的な見出し1</h3><p>本文...</p><h3>具体的な見出し2</h3><p>本文...</p><h3>具体的な見出し3</h3><p>本文...</p>",
  "excerpt": "記事の要約（100〜150文字）",
  "meta_description": "Google検索結果に表示されるメタディスクリプション（120〜160文字）",
  "tags": ["ビットコイン", "仮想通貨", "関連タグ3", "関連タグ4", "関連タグ5"],
  "slug": "bitcoin-etf-record-inflows (英語・小文字・ハイフン区切り・3〜5単語)",
  "image_prompt": "Describe one specific photorealistic news photograph scene for this article. One concrete subject with lighting and setting. Examples: 'stacked gold coins on dark marble surface, dramatic side lighting', 'trading monitor displaying red price chart, blue screen glow', 'rows of server racks in dark data center, blue LED light', 'physical gold bar on reflective black surface, spotlight'. NO people, NO brand names, NO text. Max 15 words.",
  "tweet_bullets": ["この記事の要点1（25文字以内）", "この記事の要点2（25文字以内）", "この記事の要点3（25文字以内）"]
}}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()

    # JSON 部分を抽出
    start = response_text.find("{")
    end = response_text.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"JSON が見つかりません: {response_text[:200]}")

    try:
        return json.loads(response_text[start:end])
    except json.JSONDecodeError as e:
        logger.error(f"JSON パースエラー: {e}\nレスポンス: {response_text[start:end][:500]}")
        raise


def generate_featured_image(image_prompt, tags=None):
    """Gemini / Imagen を使ってアイキャッチ画像を生成（Google AI Studio 対応）"""
    import os
    from google import genai
    from google.genai import types

    from PIL import Image

    api_key = os.environ["GOOGLE_API_KEY"]
    base_prompt = image_prompt or "gold bitcoin coins stacked on dark surface, dramatic side lighting"
    full_prompt = (
        f"{base_prompt}. "
        "Photojournalism, Reuters news photography style. "
        "Shot on 85mm lens, f/2.0 aperture, shallow depth of field with soft bokeh background. "
        "Professional studio lighting or natural window light, realistic textures and materials. "
        "Muted color grading, slightly desaturated, cool tones. "
        "Sharp focus on subject, news magazine quality, high resolution. "
        "No text, no watermark, no people, no faces, no logos."
    )

    client = genai.Client(api_key=api_key)

    image_models = [
        ("imagen-4.0-fast-generate-001", "imagen"),
        ("imagen-4.0-generate-001", "imagen"),
        ("gemini-2.5-flash-image", "gemini"),
        ("gemini-3.1-flash-image", "gemini"),
    ]

    raw_bytes = None
    for model_name, model_type in image_models:
        try:
            logger.info(f"アイキャッチ画像を生成中（{model_name}）...")
            if model_type == "imagen":
                response = client.models.generate_images(
                    model=model_name,
                    prompt=full_prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                        aspect_ratio="4:3",
                    ),
                )
                raw_bytes = response.generated_images[0].image.image_bytes
            else:
                response = client.models.generate_content(
                    model=model_name,
                    contents=full_prompt,
                    config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
                )
                for part in response.candidates[0].content.parts:
                    if part.inline_data is not None:
                        raw_bytes = part.inline_data.data
            if raw_bytes:
                break
        except Exception as e:
            logger.warning(f"{model_name} 失敗: {e}")
            continue

    if not raw_bytes:
        raise ValueError("利用可能な画像生成モデルが見つかりません")

    # 1200×630 にリサイズ
    img = Image.open(io.BytesIO(raw_bytes))
    img = img.resize((1200, 630), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=92)
    logger.info("画像を1200×630にリサイズ完了")
    return output.getvalue()


def get_seo_article_type() -> str:
    """日付と実行回数（時間帯）でSEO記事カテゴリをローテーションする"""
    import datetime
    now = datetime.datetime.now()
    day_idx = now.timetuple().tm_yday
    slot = 0 if now.hour < 15 else 1  # 朝=0、夕=1
    return SEO_ARTICLE_TYPES[(day_idx * 2 + slot) % len(SEO_ARTICLE_TYPES)]


def generate_seo_article(article_type: str) -> dict:
    """SEO強化記事を Claude Haiku で生成する（カテゴリ: コラム/DeFi/基礎知識/取引所）"""
    if article_type == "基礎知識":
        return _generate_kiso_article()
    return _generate_rich_article(article_type)


def _generate_rich_article(article_type: str) -> dict:
    """コラム・DeFi・取引所カテゴリ向けリッチデザインHTML記事を生成する。"""

    type_configs = {
        "コラム": {
            "theme": "仮想通貨・ブロックチェーン業界の最新トレンド・時事コラム。市場動向の背景にある社会的・経済的要因を深堀りした考察記事",
            "chart_hint": "仮想通貨の市場シェア・価格推移・時価総額変動の比較グラフ",
            "table_hint": "市場データや各国の規制状況・機関投資家の参入状況などを表形式でまとめる",
            "topics": "市場サイクル・機関投資家・規制動向・ビットコインETF・AI×ブロックチェーン・RWA・ステーブルコイン政策など",
            "qa_hint": "読者が「なぜ」「どうなる」と疑問を持ちやすいトピックを3問",
        },
        "DeFi": {
            "theme": "分散型金融（DeFi）の仕組み・主要プロトコル・リスク・利回り・最新動向の解説記事",
            "chart_hint": "主要DeFiプロトコルのTVL（ロック総額）または APY（利回り）比較棒グラフ",
            "table_hint": "主要プロトコルの比較表（TVL・チェーン・利回り・リスクレベルなど）",
            "topics": "Uniswap・Aave・Compound・Curve・Lido・EigenLayer・Pendle・LST・LRT・イールドファーミング・流動性プールなど",
            "qa_hint": "DeFiの安全性・始め方・リスク管理に関する初心者向け3問",
        },
        "取引所": {
            "theme": "仮想通貨取引所の選び方・手数料比較・セキュリティ・機能・使い方の解説記事",
            "chart_hint": "国内外の主要取引所の取引量・手数料・取扱銘柄数の比較グラフ",
            "table_hint": "取引所の比較表（手数料・セキュリティ・日本語対応・取扱銘柄数・入出金方法など）",
            "topics": "取引所比較・セキュリティ・スプレッド・IEO・コピートレード・レバレッジ・税金・スマートフォンアプリなど",
            "qa_hint": "取引所の安全性・選び方・手数料に関する実用的な3問",
        },
    }

    cfg = type_configs.get(article_type, type_configs["コラム"])

    prompt = f"""あなたはSEOに強い仮想通貨専門ライターです。helloBTC向けに「{article_type}」カテゴリの長文SEO記事を作成してください。

【テーマ・方向性】
{cfg['theme']}
参考トピック例（自由に選択・組み合わせ）: {cfg['topics']}

【必須コンテンツ構成（この順番で出力）】

① リード文（150〜200文字）
  - 読者の悩みや疑問に直接応える書き出し

② 目次ボックス（下記HTMLをそのまま使う。項目数は5〜6）:
<div style='background:#f5f5f5;border:2px solid #e0e0e0;border-radius:8px;padding:20px 28px;margin:24px 0;'>
<p style='font-weight:700;font-size:1.05em;margin:0 0 12px;color:#333;'>📋 目次</p>
<ol style='margin:0;padding-left:22px;line-height:2.1;color:#555;font-size:0.95em;'>
<li>実際の記事セクションタイトル1</li>
<li>実際の記事セクションタイトル2</li>
...
</ol>
</div>

③〜⑦ 各セクション（5〜6セクション）
各セクションは以下の構成:
  - バナー見出し（ダーク背景・下記スタイル必須）
  - 本文（2〜3段落）
  - 適所にinfoボックス・テーブル・リスト・プレースホルダーを配置

⑧ Q&Aセクション（3問）
⑨ 免責文

【デザインHTMLパーツ（必ずこのスタイルを正確に使う）】

バナー見出し:
<div style='background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:18px 24px;border-radius:6px;border-left:5px solid #f7931a;margin:36px 0 20px;font-size:1.1em;font-weight:700;'>🔷 セクションタイトル</div>

グリーンボックス（ポイント・まとめ）:
<div style='background:#e8f5e9;border-left:5px solid #4caf50;padding:16px 20px;margin:20px 0;border-radius:4px;'><strong>✅ ポイント</strong><br>内容</div>

オレンジボックス（注意・重要）:
<div style='background:#fff3e0;border-left:5px solid #ff9800;padding:16px 20px;margin:20px 0;border-radius:4px;'><strong>⚠️ 注意点</strong><br>内容</div>

赤ボックス（リスク・警告）:
<div style='background:#fce4ec;border-left:5px solid #e91e63;padding:16px 20px;margin:20px 0;border-radius:4px;'><strong>🔴 リスク</strong><br>内容</div>

データ・比較テーブル（{cfg['table_hint']}）:
<div style='overflow-x:auto;margin:24px 0;'><table style='width:100%;border-collapse:collapse;font-size:0.93em;'>
<thead><tr style='background:#f7931a;color:#fff;'>
<th style='padding:12px 14px;border:1px solid #e6881a;'>項目</th>
<th style='padding:12px 14px;border:1px solid #e6881a;'>内容A</th>
<th style='padding:12px 14px;border:1px solid #e6881a;'>内容B</th>
</tr></thead>
<tbody>
<tr style='background:#fff8e1;'><td style='padding:10px 14px;border:1px solid #ddd;font-weight:600;'>項目名</td><td style='padding:10px 14px;border:1px solid #ddd;'>値</td><td style='padding:10px 14px;border:1px solid #ddd;'>値</td></tr>
<tr style='background:#fff;'><td style='padding:10px 14px;border:1px solid #ddd;font-weight:600;'>項目名</td><td style='padding:10px 14px;border:1px solid #ddd;'>値</td><td style='padding:10px 14px;border:1px solid #ddd;'>値</td></tr>
</tbody></table></div>

Q&Aアイテム:
<div style='margin:20px 0;'><div style='background:#1a1a2e;color:#fff;border-radius:8px 8px 0 0;padding:14px 20px;font-weight:600;'>Q. 質問文</div><div style='background:#fffbf0;border:1px solid #f7931a;border-top:none;border-radius:0 0 8px 8px;padding:14px 20px;color:#333;'><strong>A.</strong> 回答文</div></div>

免責文:
<p style='font-size:0.85em;color:#888;margin-top:32px;'>※本記事は情報提供を目的としており、投資助言ではありません。仮想通貨への投資はリスクを伴います。余裕資金の範囲内でご判断ください。</p>

【プレースホルダー配置（必ず含める）】
- {{IMAGE_1}}: セクション2〜3の末尾
- {{IMAGE_2}}: セクション4〜5の末尾
- {{CHART}}: 比較・データセクションの末尾

【ライティングルール】
- 文体：「〜した」「〜だ」「〜である」（丁寧語禁止）
- 本文：2000〜2800文字
- HTMLの属性はすべてシングルクォート（'）で統一（JSON破損防止）
- 参照リンク・出典記載は不要
- 2026年現在の最新状況を踏まえた内容

【グラフデータ】
- {cfg['chart_hint']}
- 記事内容に合ったリアルで説得力ある値
- ラベルは日本語

【画像プロンプト】
- 英語15語以内、人物・ブランド名・固有名詞禁止

必ず以下のJSONのみ出力（前後に余計なテキスト不要）:
{{
  "title": "SEO最適化された日本語タイトル（35〜65文字、具体的な数字・年を含む）",
  "content": "<完全なHTML記事本文（目次〜免責文、IMAGE_1/IMAGE_2/CHARTプレースホルダー含む）>",
  "excerpt": "記事の要約（100〜150文字）",
  "slug": "article-topic-keyword（英語・ハイフン区切り・3〜5単語）",
  "tags": ["タグ1", "タグ2", "タグ3", "タグ4", "タグ5"],
  "tweet_bullets": ["要点1（25文字以内）", "要点2（25文字以内）", "要点3（25文字以内）"],
  "featured_image_prompt": "Photorealistic scene on dark surface, dramatic lighting. NO people, NO brand names, NO text. Max 15 words.",
  "article_image_prompts": [
    "Photorealistic scene 1, dramatic lighting, dark background. NO people, NO brand names, NO text. Max 15 words.",
    "Photorealistic scene 2, dramatic lighting, dark background. NO people, NO brand names, NO text. Max 15 words."
  ],
  "chart": {{
    "type": "bar",
    "title": "グラフのタイトル",
    "labels": ["ラベル1", "ラベル2", "ラベル3", "ラベル4", "ラベル5"],
    "values": [10.5, 8.2, 5.1, 3.8, 2.4],
    "unit": "単位（例: 十億ドル、%）",
    "caption": "※数値は概算・参考値です（2026年時点）"
  }}
}}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()
    start = response_text.find("{")
    end = response_text.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"JSON が見つかりません: {response_text[:200]}")

    try:
        return json.loads(response_text[start:end])
    except json.JSONDecodeError as e:
        logger.error(f"SEO記事 JSON パースエラー: {e}\nレスポンス: {response_text[start:end][:500]}")
        raise


def _generate_kiso_article() -> dict:
    """アルトコイン基礎知識記事をリッチデザインHTMLで生成する。

    参考記事のデザイン要素:
    - 目次ボックス（番号付き）
    - ダーク背景バナー見出し（グラデーション＋オレンジ左ボーダー）
    - オレンジヘッダーの基本情報テーブル・比較テーブル
    - カラーinfoボックス（緑=ポイント / オレンジ=注意 / 赤=リスク）
    - Q&Aセクション（ダーク質問行＋薄黄回答行）
    """

    # ---- HTML パーツ定義（シングルクォートで属性を書きJSON破損を防ぐ） ----
    TOC_TEMPLATE = (
        "<div style='background:#f5f5f5;border:2px solid #e0e0e0;border-radius:8px;"
        "padding:20px 28px;margin:24px 0;'>"
        "<p style='font-weight:700;font-size:1.05em;margin:0 0 12px;color:#333;'>"
        "📋 目次</p>"
        "<ol style='margin:0;padding-left:22px;line-height:2.1;color:#555;font-size:0.95em;'>"
        "%%TOC_ITEMS%%"
        "</ol></div>"
    )

    BANNER_H2 = (
        "<div style='background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;"
        "padding:18px 24px;border-radius:6px;border-left:5px solid #f7931a;"
        "margin:36px 0 20px;font-size:1.1em;font-weight:700;'>"
        "%%TITLE%%</div>"
    )

    INFO_TABLE = (
        "<div style='overflow-x:auto;margin:20px 0;'>"
        "<table style='width:100%;border-collapse:collapse;font-size:0.95em;'>"
        "<thead><tr style='background:#f7931a;color:#fff;'>"
        "<th style='padding:12px 16px;text-align:left;border:1px solid #e6881a;min-width:110px;'>項目</th>"
        "<th style='padding:12px 16px;text-align:left;border:1px solid #e6881a;'>内容</th>"
        "</tr></thead>"
        "<tbody>%%ROWS%%</tbody></table></div>"
    )

    CMP_TABLE = (
        "<div style='overflow-x:auto;margin:24px 0;'>"
        "<table style='width:100%;border-collapse:collapse;font-size:0.9em;'>"
        "<thead><tr style='background:#1a1a2e;color:#fff;'>"
        "<th style='padding:12px 14px;border:1px solid #333;'>比較項目</th>"
        "<th style='padding:12px 14px;border:1px solid #333;background:#f7931a;'>%%COIN%%</th>"
        "<th style='padding:12px 14px;border:1px solid #333;'>競合A</th>"
        "<th style='padding:12px 14px;border:1px solid #333;'>競合B</th>"
        "</tr></thead>"
        "<tbody>%%CMP_ROWS%%</tbody></table></div>"
    )

    GREEN_BOX = (
        "<div style='background:#e8f5e9;border-left:5px solid #4caf50;"
        "padding:16px 20px;margin:20px 0;border-radius:4px;'>"
        "<strong>✅ ポイント</strong><br>%%BODY%%</div>"
    )

    ORANGE_BOX = (
        "<div style='background:#fff3e0;border-left:5px solid #ff9800;"
        "padding:16px 20px;margin:20px 0;border-radius:4px;'>"
        "<strong>⚠️ 注意点</strong><br>%%BODY%%</div>"
    )

    RED_BOX = (
        "<div style='background:#fce4ec;border-left:5px solid #e91e63;"
        "padding:16px 20px;margin:20px 0;border-radius:4px;'>"
        "<strong>🔴 リスク</strong><br>%%BODY%%</div>"
    )

    QA_ITEM = (
        "<div style='margin:20px 0;'>"
        "<div style='background:#1a1a2e;color:#fff;border-radius:8px 8px 0 0;"
        "padding:14px 20px;font-weight:600;'>Q. %%Q%%</div>"
        "<div style='background:#fffbf0;border:1px solid #f7931a;border-top:none;"
        "border-radius:0 0 8px 8px;padding:14px 20px;color:#333;'>"
        "<strong>A.</strong> %%A%%</div></div>"
    )

    prompt = f"""あなたはSEOに強い仮想通貨専門ライターです。helloBTC向けにアルトコイン・ブロックチェーン技術の「基礎知識」記事を作成してください。

【テーマ選定】
以下から2026年時点でSEO需要が高いトピックを1つ自由に選ぶ:
Ethereum・Solana・XRP・Cardano・Avalanche・Polkadot・Chainlink・MATIC（Polygon）・TON・SUI・NEAR・Aptos・Arbitrum・Optimism・Cosmos・Filecoin・Render・Injective・Celestia・Starknet など

【記事の必須構成（この順番で出力）】

① リード文（150〜200文字）
  - 「〜とは何か」「なぜ注目されているか」を簡潔に伝える
  - 読者の検索意図に応える冒頭

② 目次ボックス（以下のHTML構造で出力）:
{TOC_TEMPLATE.replace("%%TOC_ITEMS%%", "<li>項目1</li><li>項目2</li>...")}

③ セクション1「[コイン名]とは？基本情報まとめ」
  - バナー見出し（ダーク背景）
  - 基本情報テーブル（名称 / ティッカー / 設立年 / 発行上限 / 合意アルゴリズム / 時価総額順位 / 公式サイト）
  - グリーンポイントボックスで「一言でいうと何か」を強調
  - {{IMAGE_1}} を配置

④ セクション2「仕組みと技術的特徴」
  - バナー見出し
  - 2〜3段落の詳細説明
  - オレンジ注意ボックスで技術的な落とし穴や制約を説明

⑤ セクション3「ユースケースと実際の活用事例」
  - バナー見出し
  - 箇条書き（ul）で3〜5例
  - {{IMAGE_2}} を配置

⑥ セクション4「競合プロジェクトとの比較」
  - バナー見出し
  - 比較テーブル（4行・3社比較）
  - {{CHART}} を配置

⑦ セクション5「将来性・課題・投資リスク」
  - バナー見出し
  - 将来性のポジティブ評価
  - 赤リスクボックスで投資リスクを明示

⑧ Q&Aセクション（3問）
  - バナー見出し「よくある質問（Q&A）」
  - Q&A形式で3問（初心者が検索しそうな疑問）

⑨ 免責文
  <p style='font-size:0.85em;color:#888;margin-top:32px;'>※本記事は情報提供を目的としており、投資助言ではありません。仮想通貨への投資は価格変動リスクを伴います。余裕資金の範囲内でご判断ください。</p>

【デザインHTMLパーツ（正確にこのスタイルで使う）】

バナー見出し:
{BANNER_H2.replace("%%TITLE%%", "🔷 セクションタイトル")}

基本情報テーブル:
{INFO_TABLE.replace("%%ROWS%%", "<tr style='background:#fff8e1;'><td style='padding:10px 16px;border:1px solid #ddd;font-weight:600;'>名称</td><td style='padding:10px 16px;border:1px solid #ddd;'>〇〇〇</td></tr>")}

比較テーブル:
{CMP_TABLE.replace("%%COIN%%", "対象コイン").replace("%%CMP_ROWS%%", "<tr><td>...</td><td>...</td><td>...</td><td>...</td></tr>")}

グリーンボックス:
{GREEN_BOX.replace("%%BODY%%", "ポイント内容")}

オレンジボックス:
{ORANGE_BOX.replace("%%BODY%%", "注意内容")}

赤ボックス:
{RED_BOX.replace("%%BODY%%", "リスク内容")}

Q&A:
{QA_ITEM.replace("%%Q%%", "質問").replace("%%A%%", "回答")}

【ライティングルール】
- 文体：「〜した」「〜だ」「〜である」（丁寧語禁止）
- 本文：2000〜2800文字
- HTMLの属性はすべてシングルクォート（'）を使う（JSON破損防止）
- 参照リンク・出典記載は不要

【グラフデータルール】
- 比較コインの時価総額・取引量・TPS・TVLなどのリアルな数値で比較グラフを作成
- ラベルは日本語

【画像プロンプトルール】
- 英語15語以内、人物・ブランド名禁止、抽象的なビジュアルメタファーのみ

必ず以下のJSONのみ出力（前後に余計なテキスト不要）:
{{
  "title": "SEO最適化された日本語タイトル（35〜65文字、コイン名・数字・年を含む）",
  "content": "<完全なHTML記事本文（目次〜免責文まで、IMAGE_1/IMAGE_2/CHARTプレースホルダー含む）>",
  "excerpt": "記事の要約（100〜150文字）",
  "slug": "coin-name-beginner-guide（英語・ハイフン区切り・3〜5単語）",
  "tags": ["コイン名", "仮想通貨", "ブロックチェーン", "関連タグ4", "基礎知識"],
  "tweet_bullets": ["要点1（25文字以内）", "要点2（25文字以内）", "要点3（25文字以内）"],
  "featured_image_prompt": "Photorealistic scene: glowing crypto coin on dark surface, dramatic side lighting. NO people, NO text, NO brand. Max 15 words.",
  "article_image_prompts": [
    "Photorealistic blockchain network visualization, glowing nodes, dark background. NO people, NO text. Max 15 words.",
    "Photorealistic digital technology concept, abstract circuit light, dark aesthetic. NO people, NO text. Max 15 words."
  ],
  "chart": {{
    "type": "bar",
    "title": "主要コインの比較（例: 取引処理速度 TPS）",
    "labels": ["対象コイン", "競合A", "競合B", "競合C", "競合D"],
    "values": [65000, 45000, 15, 6000, 30000],
    "unit": "TPS",
    "caption": "※数値は概算・参考値です（2026年時点）"
  }}
}}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()
    start = response_text.find("{")
    end = response_text.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"基礎知識 JSON が見つかりません: {response_text[:200]}")

    try:
        return json.loads(response_text[start:end])
    except json.JSONDecodeError as e:
        logger.error(f"基礎知識 JSON パースエラー: {e}\nレスポンス: {response_text[start:end][:500]}")
        raise


def generate_chart_image(chart_data: dict) -> bytes:
    """matplotlib でグラフ画像（1200×675px）を生成して JPEG バイト列を返す"""
    if not _matplotlib_available:
        raise RuntimeError("matplotlib がインストールされていません")

    labels = chart_data.get("labels", [])
    values = chart_data.get("values", [])
    unit = chart_data.get("unit", "")
    chart_type = chart_data.get("type", "bar")
    colors = ["#F7931A", "#627EEA", "#26A17B", "#E84142", "#8247E5",
              "#00D4FF", "#FFB800", "#FF6B35"]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    if chart_type == "line":
        ax.plot(labels, values, marker="o", linewidth=2.5,
                color="#F7931A", markersize=8, markerfacecolor="white")
        ax.fill_between(range(len(labels)), values, alpha=0.15, color="#F7931A")
        for i, val in enumerate(values):
            ax.text(i, val + max(values) * 0.02, f"{val:,.1f}",
                    ha="center", va="bottom", color="white", fontsize=10)
    else:
        bars = ax.bar(labels, values, color=colors[:len(labels)],
                      width=0.6, edgecolor="none")
        for bar, val in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(values) * 0.02,
                f"{val:,.1f}",
                ha="center", va="bottom", color="white", fontsize=10, fontweight="bold",
            )

    ax.set_title(chart_data.get("title", ""), color="white", fontsize=13,
                 fontweight="bold", pad=15)
    ax.set_ylabel(unit, color="#aaaaaa", fontsize=10)
    ax.tick_params(colors="#aaaaaa", labelsize=9)
    for spine in ax.spines.values():
        spine.set_color("#333355")
    ax.yaxis.grid(True, linestyle="--", alpha=0.3, color="#444466")
    ax.set_axisbelow(True)

    caption = chart_data.get("caption", "")
    if caption:
        fig.text(0.5, 0.01, caption, ha="center", color="#888888", fontsize=8)

    plt.tight_layout(rect=[0, 0.04, 1, 1])

    output = io.BytesIO()
    plt.savefig(output, format="JPEG", quality=90, bbox_inches="tight",
                dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    logger.info("グラフ画像生成完了")
    return output.getvalue()
