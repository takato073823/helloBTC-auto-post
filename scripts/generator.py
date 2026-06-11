"""
Claude API を使って日本語 SEO 記事を生成する
"""
import anthropic
import json
import logging

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
1. わかりやすい日本語で書く（専門用語には簡単な説明を添える）
2. 重要なキーワードを自然に含める
3. 元記事を参照・引用し、出典を明記する
4. H3見出しで構造化する（2〜3個）
5. 読者の行動を促す締めくくりを入れる

必ず以下のJSON形式のみで出力してください（前後に余計なテキストを含めないこと）:
{{
  "title": "SEO最適化された日本語タイトル（30〜60文字、数字や具体的な情報を含む）",
  "content": "<h3>見出し1</h3><p>本文...</p><h3>見出し2</h3><p>本文...</p><h3>まとめ</h3><p>本文...</p><p>参照: <a href=\\"{source_url}\\" target=\\"_blank\\" rel=\\"noopener\\">{source_name}</a></p>",
  "excerpt": "記事の要約（100〜150文字）",
  "meta_description": "Google検索結果に表示されるメタディスクリプション（120〜160文字）",
  "tags": ["ビットコイン", "仮想通貨", "関連タグ3", "関連タグ4", "関連タグ5"],
  "image_prompt": "cryptocurrency news illustration in English describing the article topic (for AI image generation, 20 words max)"
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
    """Google Imagen 3 を使ってアイキャッチ画像を生成"""
    import os
    from google import genai
    from google.genai import types

    api_key = os.environ["GOOGLE_API_KEY"]
    base_prompt = image_prompt or "cryptocurrency bitcoin blockchain technology news illustration"
    if tags:
        base_prompt += f", {', '.join(tags[:2])}"
    full_prompt = f"{base_prompt}, professional digital art, clean modern design, high quality"

    client = genai.Client(api_key=api_key)
    logger.info("アイキャッチ画像を生成中（Imagen 3）...")
    response = client.models.generate_images(
        model="imagen-3.0-generate-002",
        prompt=full_prompt,
        config=types.GenerateImagesConfig(
            number_of_images=1,
            aspect_ratio="16:9",
        ),
    )
    return response.generated_images[0].image.image_bytes
