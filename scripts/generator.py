"""
Claude API を使って日本語 SEO 記事を生成する
"""
import anthropic
import json
import logging
import io


def _repair_and_parse_json(text: str) -> dict:
    """LLMが生成したJSONをパース。文字列内の生の改行・タブを修復してから試みる。"""
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"JSON が見つかりません: {text[:200]}")

    raw = text[start:end]

    # まず直接パース
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 修復: JSON文字列値の内部にある生の改行・タブをエスケープする
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

    try:
        return json.loads("".join(repaired))
    except json.JSONDecodeError as e:
        raise json.JSONDecodeError(
            f"修復後もパース失敗: {e.msg}", e.doc, e.pos
        ) from e

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

    # JSON 部分を抽出・修復してパース
    try:
        return _repair_and_parse_json(response_text)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"JSON パースエラー: {e}\nレスポンス: {response_text[:500]}")
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


def _call_haiku(prompt: str, max_tokens: int = 8192) -> str:
    """Claude Haiku 4.5 を呼び出してテキストを返す。"""
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _generate_meta_json(html_content: str, article_type: str, chart_hint: str) -> dict:
    """生成済みHTMLを元にタイトル・スラッグ等のメタデータをJSON生成（小さいのでパース安定）。"""
    prompt = f"""以下のHTML記事（{article_type}カテゴリ）を読んで、メタデータをJSONで出力してください。

記事の冒頭（参考）:
{html_content[:800]}

必ず以下のJSONのみ出力してください（前後にテキスト不要）:
{{
  "title": "SEO最適化された日本語タイトル（35〜65文字、具体的な数字・年を含む）",
  "excerpt": "記事の要約（100〜150文字）",
  "slug": "article-topic-keyword（英語・ハイフン区切り・3〜5単語）",
  "tags": ["タグ1", "タグ2", "タグ3", "タグ4", "タグ5"],
  "tweet_bullets": ["要点1（25文字以内）", "要点2（25文字以内）", "要点3（25文字以内）"],
  "featured_image_prompt": "Photorealistic scene, dramatic lighting, dark background. NO people, NO brand names, NO text. Max 15 words.",
  "article_image_prompts": [
    "Photorealistic scene 1, dramatic lighting. NO people, NO brand names, NO text. Max 15 words.",
    "Photorealistic scene 2, dramatic lighting. NO people, NO brand names, NO text. Max 15 words."
  ],
  "chart": {{
    "type": "bar",
    "title": "{chart_hint}",
    "labels": ["ラベル1", "ラベル2", "ラベル3", "ラベル4", "ラベル5"],
    "values": [10.5, 8.2, 5.1, 3.8, 2.4],
    "unit": "単位",
    "caption": "※数値は概算・参考値です（2026年時点）"
  }}
}}"""
    raw = _call_haiku(prompt, max_tokens=1024)
    return _repair_and_parse_json(raw)


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

    # ── Pass 1: HTML本文のみ生成（JSON不使用でトークン切れを回避）────────
    html_prompt = f"""あなたはSEOに強い仮想通貨専門ライターです。helloBTC向けに「{article_type}」カテゴリの記事HTML本文のみを出力してください。JSONは不要です。

【テーマ】{cfg['theme']}
参考トピック例: {cfg['topics']}

【構成（この順番でHTMLのみ出力）】
① リード文（p タグ、150〜200文字）
② 目次ボックス:
<div style='background:#f5f5f5;border:2px solid #e0e0e0;border-radius:8px;padding:20px 28px;margin:24px 0;'><p style='font-weight:700;font-size:1.05em;margin:0 0 12px;color:#333;'>📋 目次</p><ol style='margin:0;padding-left:22px;line-height:2.1;color:#555;font-size:0.95em;'><li>実際のタイトル</li>...</ol></div>
③〜⑦ 各セクション（5〜6個）:
  - バナー見出し: <div style='background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:18px 24px;border-radius:6px;border-left:5px solid #f7931a;margin:36px 0 20px;font-size:1.1em;font-weight:700;'>🔷 タイトル</div>
  - 本文 p タグ
  - 必要に応じてinfoボックス・テーブル・リストを使う
⑧ Q&Aセクション（3問、バナー見出し＋Q&Aアイテム）:
  Q&Aアイテム: <div style='margin:20px 0;'><div style='background:#1a1a2e;color:#fff;border-radius:8px 8px 0 0;padding:14px 20px;font-weight:600;'>Q. 質問</div><div style='background:#fffbf0;border:1px solid #f7931a;border-top:none;border-radius:0 0 8px 8px;padding:14px 20px;'><strong>A.</strong> 回答</div></div>
⑨ 免責文: <p style='font-size:0.85em;color:#888;margin-top:32px;'>※本記事は情報提供を目的としており、投資助言ではありません。</p>

【デザインパーツ（必要に応じて使う）】
グリーン: <div style='background:#e8f5e9;border-left:5px solid #4caf50;padding:16px 20px;margin:20px 0;border-radius:4px;'><strong>✅ ポイント</strong><br>内容</div>
オレンジ: <div style='background:#fff3e0;border-left:5px solid #ff9800;padding:16px 20px;margin:20px 0;border-radius:4px;'><strong>⚠️ 注意点</strong><br>内容</div>
赤: <div style='background:#fce4ec;border-left:5px solid #e91e63;padding:16px 20px;margin:20px 0;border-radius:4px;'><strong>🔴 リスク</strong><br>内容</div>
テーブル: <div style='overflow-x:auto;margin:24px 0;'><table style='width:100%;border-collapse:collapse;font-size:0.93em;'><thead><tr style='background:#f7931a;color:#fff;'><th style='padding:12px 14px;border:1px solid #e6881a;'>項目</th><th style='padding:12px 14px;border:1px solid #e6881a;'>A</th><th style='padding:12px 14px;border:1px solid #e6881a;'>B</th></tr></thead><tbody><tr style='background:#fff8e1;'><td style='padding:10px 14px;border:1px solid #ddd;font-weight:600;'>名前</td><td style='padding:10px 14px;border:1px solid #ddd;'>値</td><td style='padding:10px 14px;border:1px solid #ddd;'>値</td></tr></tbody></table></div>

【プレースホルダー（必ず含める）】{{IMAGE_1}} {{IMAGE_2}} {{CHART}}

【ルール】文体:言い切り調。本文1800〜2200文字。HTMLの属性はシングルクォート。JSON不要。HTMLのみ出力。"""

    logger.info(f"  Pass1: {article_type} HTML本文生成中...")
    html_content = _call_haiku(html_prompt, max_tokens=8192)

    # ── Pass 2: メタデータJSON生成（小さいのでパース安定）────────────────
    logger.info(f"  Pass2: メタデータJSON生成中...")
    meta = _generate_meta_json(html_content, article_type, cfg["chart_hint"])
    meta["content"] = html_content
    return meta


def _generate_kiso_article() -> dict:
    """アルトコイン基礎知識記事を2パスで生成（Pass1=HTML本文、Pass2=メタデータJSON）。"""

    # ── Pass 1: HTML本文のみ生成 ──────────────────────────────────────────
    html_prompt = """あなたはSEOに強い仮想通貨専門ライターです。helloBTC向けにアルトコイン・ブロックチェーン「基礎知識」記事のHTML本文のみを出力してください。JSONは不要です。

【テーマ選定】2026年時点でSEO需要が高いトピックを1つ選ぶ:
Ethereum・Solana・XRP・Cardano・Avalanche・Polkadot・Chainlink・Polygon・TON・SUI・NEAR・Aptos・Arbitrum・Optimism・Cosmos・Filecoin・Render・Injective・Celestia・Starknet

【構成（HTMLのみ。JSONなし）】
① リード文（pタグ、150〜200文字）
② 目次ボックス:
<div style='background:#f5f5f5;border:2px solid #e0e0e0;border-radius:8px;padding:20px 28px;margin:24px 0;'><p style='font-weight:700;font-size:1.05em;margin:0 0 12px;color:#333;'>📋 目次</p><ol style='margin:0;padding-left:22px;line-height:2.1;color:#555;'><li>タイトル</li>...</ol></div>
③ セクション1「[コイン名]とは？基本情報」
  - バナー見出し（下記スタイル）
  - 基本情報テーブル（名称/ティッカー/設立年/発行上限/合意アルゴリズム/時価総額順位）
  - グリーンポイントボックス
  - {IMAGE_1}
④ セクション2「仕組みと技術的特徴」
  - バナー見出し・本文・オレンジ注意ボックス
⑤ セクション3「ユースケースと活用事例」
  - バナー見出し・ul箇条書き3〜5例・{IMAGE_2}
⑥ セクション4「競合プロジェクトとの比較」
  - バナー見出し・比較テーブル（3社比較）・{CHART}
⑦ セクション5「将来性・課題・投資リスク」
  - バナー見出し・本文・赤リスクボックス
⑧ Q&Aセクション（バナー見出し「よくある質問」＋Q&Aアイテム3問）
⑨ 免責文: <p style='font-size:0.85em;color:#888;margin-top:32px;'>※本記事は情報提供を目的としており投資助言ではありません。</p>

【デザインパーツ】
バナー見出し: <div style='background:linear-gradient(135deg,#1a1a2e,#16213e);color:#fff;padding:18px 24px;border-radius:6px;border-left:5px solid #f7931a;margin:36px 0 20px;font-size:1.1em;font-weight:700;'>🔷 タイトル</div>
基本情報テーブル: <div style='overflow-x:auto;margin:20px 0;'><table style='width:100%;border-collapse:collapse;'><thead><tr style='background:#f7931a;color:#fff;'><th style='padding:12px;border:1px solid #e6881a;'>項目</th><th style='padding:12px;border:1px solid #e6881a;'>内容</th></tr></thead><tbody><tr style='background:#fff8e1;'><td style='padding:10px;border:1px solid #ddd;font-weight:600;'>名称</td><td style='padding:10px;border:1px solid #ddd;'>〇〇</td></tr></tbody></table></div>
比較テーブル: <div style='overflow-x:auto;margin:24px 0;'><table style='width:100%;border-collapse:collapse;font-size:0.93em;'><thead><tr style='background:#1a1a2e;color:#fff;'><th style='padding:12px;border:1px solid #333;'>比較項目</th><th style='padding:12px;border:1px solid #333;background:#f7931a;'>コイン名</th><th style='padding:12px;border:1px solid #333;'>競合A</th><th style='padding:12px;border:1px solid #333;'>競合B</th></tr></thead><tbody><tr style='background:#fff8e1;'><td style='padding:10px;border:1px solid #ddd;font-weight:600;'>項目</td><td style='padding:10px;border:1px solid #ddd;'>値</td><td style='padding:10px;border:1px solid #ddd;'>値</td><td style='padding:10px;border:1px solid #ddd;'>値</td></tr></tbody></table></div>
グリーン: <div style='background:#e8f5e9;border-left:5px solid #4caf50;padding:16px 20px;margin:20px 0;border-radius:4px;'><strong>✅ ポイント</strong><br>内容</div>
オレンジ: <div style='background:#fff3e0;border-left:5px solid #ff9800;padding:16px 20px;margin:20px 0;border-radius:4px;'><strong>⚠️ 注意点</strong><br>内容</div>
赤: <div style='background:#fce4ec;border-left:5px solid #e91e63;padding:16px 20px;margin:20px 0;border-radius:4px;'><strong>🔴 リスク</strong><br>内容</div>
Q&A: <div style='margin:20px 0;'><div style='background:#1a1a2e;color:#fff;border-radius:8px 8px 0 0;padding:14px 20px;font-weight:600;'>Q. 質問</div><div style='background:#fffbf0;border:1px solid #f7931a;border-top:none;border-radius:0 0 8px 8px;padding:14px 20px;'><strong>A.</strong> 回答</div></div>

【ルール】文体:言い切り調。本文1800〜2200文字。属性はシングルクォート。HTMLのみ出力。"""

    logger.info("  Pass1: 基礎知識 HTML本文生成中...")
    html_content = _call_haiku(html_prompt, max_tokens=8192)

    # ── Pass 2: メタデータJSON生成 ────────────────────────────────────────
    logger.info("  Pass2: メタデータJSON生成中...")
    meta = _generate_meta_json(html_content, "基礎知識", "主要コインのTPS・時価総額・TVL比較")
    meta["content"] = html_content
    return meta


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
