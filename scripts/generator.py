"""
Claude API を使って日本語 SEO 記事を生成する
"""
import anthropic
import json
import logging
import io

logger = logging.getLogger(__name__)
client = anthropic.Anthropic()


def generate_article(title, content, source_url, source_name):
    """英語ニュースから SEO 最適化された日本語記事を生成"""

    prompt = f"""以下の英語の仮想通貨ニュースを基に、SEO最適化された日本語のブログ記事を作成してください。

【元記事】
タイトル: {title}
出典: {source_name} ({source_url})
内容:
{content}

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

必ず以下のJSON形式のみで出力してください（前後に余計なテキストを含めないこと）:
{{
  "title": "SEO最適化された日本語タイトル（30〜60文字、数字や具体的な情報を含む）",
  "content": "<h3>具体的な見出し1</h3><p>本文...</p><h3>具体的な見出し2</h3><p>本文...</p><h3>具体的な見出し3</h3><p>本文...</p>",
  "excerpt": "記事の要約（100〜150文字）",
  "meta_description": "Google検索結果に表示されるメタディスクリプション（120〜160文字）",
  "tags": ["ビットコイン", "仮想通貨", "関連タグ3", "関連タグ4", "関連タグ5"],
  "image_prompt": "visual concept in English for this article (NO brand names, NO model names, NO person names, NO text — use visual metaphors like glowing coins, blockchain network, price charts, golden bitcoin, digital finance, etc. Max 15 words)"
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
    base_prompt = image_prompt or "golden bitcoin coin glowing on dark background, financial technology"
    full_prompt = (
        f"{base_prompt}, "
        "photorealistic digital art, no text, no letters, no watermark, "
        "professional finance illustration, high quality, 4:3 aspect ratio"
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

    # 1100×800 にリサイズ
    img = Image.open(io.BytesIO(raw_bytes))
    img = img.resize((1100, 800), Image.LANCZOS)
    output = io.BytesIO()
    img.save(output, format="JPEG", quality=92)
    logger.info("画像を1100×800にリサイズ完了")
    return output.getvalue()
