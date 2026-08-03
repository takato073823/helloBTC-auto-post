"""
Claude API を使って日本語 SEO 記事を生成する
"""
import anthropic
import json
import logging
import io
import re
from difflib import SequenceMatcher
from html import escape, unescape

import requests


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

# 主要サービスは、記事生成AIの回答に依存せず公式ドメインを固定する。
# ファビコンをロゴマークとして画像に合成するため、商標名を画像生成モデルに
# 描かせず、文字化け・誤ったロゴを避けられる。
KNOWN_BRAND_DOMAINS = {
    "BitMart": "bitmart.com",
    "Binance": "binance.com",
    "Coinbase": "coinbase.com",
    "Kraken": "kraken.com",
    "OKX": "okx.com",
    "Bybit": "bybit.com",
    "Bitget": "bitget.com",
    "BingX": "bingx.com",
    "MEXC": "mexc.com",
    "KuCoin": "kucoin.com",
    "Gate.io": "gate.io",
    "bitFlyer": "bitflyer.com",
    "Coincheck": "coincheck.com",
    "GMOコイン": "coin.z.com",
    "SBI VCトレード": "sbivc.co.jp",
    "楽天ウォレット": "wallet.rakuten.co.jp",
    "MetaMask": "metamask.io",
    "Ethereum": "ethereum.org",
    "Solana": "solana.com",
    "Ripple": "ripple.com",
}

_VOID_HTML_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
_BALANCED_HTML_TAGS = {"div", "p", "ul", "ol", "li", "dl", "dt", "dd", "figure", "table", "thead", "tbody", "tr", "th", "td", "span", "strong", "h2", "h3"}
_HTML_TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9-]*)\b[^>]*>")


def normalize_swell_html(content: str) -> str:
    """生成HTMLの未閉鎖タグを補い、SWELLのボックス崩れを防ぐ。

    LLMの出力が途中で終わると cap-block の ``div`` が閉じず、以降の本文全体が
    色付きボックスとして描画される。投稿前に不足分を末尾へ補完する最後の安全網。
    """
    stack: list[str] = []
    for match in _HTML_TAG_RE.finditer(content):
        tag = match.group(0)
        name = match.group(1).lower()
        if name not in _BALANCED_HTML_TAGS:
            continue
        if tag.startswith("</"):
            if name in stack:
                # HTMLの慣用的な自動閉鎖と同じく、内側の未閉鎖要素を先に閉じる。
                while stack and stack[-1] != name:
                    stack.pop()
                stack.pop()
        elif not tag.rstrip().endswith("/>") and name not in _VOID_HTML_TAGS:
            stack.append(name)

    if not stack:
        return content

    logger.warning("未閉鎖HTMLタグを補完して投稿: %s", ", ".join(stack))
    return content + "".join(f"</{name}>" for name in reversed(stack))


def validate_swell_html(content: str) -> bool:
    """SWELLボックスを含む記事に、未閉鎖の主要タグがないか判定する。"""
    return normalize_swell_html(content) == content


def prepend_lead_heading(content: str, article_title: str, lead_heading: str | None = None) -> str:
    """本文の先頭に、投稿タイトルと同一ではない h2 を必ず置く。"""
    proposed = re.sub(r"<[^>]+>", "", lead_heading or "")
    proposed = re.sub(r"\s+", " ", unescape(proposed)).strip()
    title_text = re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", article_title))).strip()

    # AIが投稿タイトルをそのまま返した場合でも、同じテキストを使わない。
    if not proposed or proposed == title_text:
        proposed = f"{title_text}を解説"

    heading = f"<h2>{escape(proposed)}</h2>"
    # AIが先頭に h2 を出力しても、投稿前に必ず上記の見出しへ統一する。
    return re.sub(r"^\s*<h2\b[^>]*>.*?</h2>\s*", heading, content, count=1,
                  flags=re.IGNORECASE | re.DOTALL) if re.match(
                      r"^\s*<h2\b", content, flags=re.IGNORECASE
                  ) else heading + content


def _valid_logo_domain(domain: str | None) -> str | None:
    """外部URL取得に使える、スキームを含まない正規ドメインだけを受け入れる。"""
    if not domain:
        return None
    candidate = domain.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    return candidate if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]+)+", candidate) else None


def resolve_logo_brand(title: str, tags: list[str] | None = None,
                       logo_brand: str | None = None, logo_domain: str | None = None) -> tuple[str | None, str | None]:
    """記事の中心となる組織のロゴ名・公式ドメインを決定する。"""
    searchable = " ".join([title, *(tags or []), logo_brand or ""])
    searchable_lower = searchable.lower()
    for brand, domain in KNOWN_BRAND_DOMAINS.items():
        if brand.lower() in searchable_lower:
            return brand, domain

    # 未登録の組織は、AIが明示した有効な公式ドメインがある場合だけ採用する。
    # 推測によるロゴの取り違えを避けるため、ドメインが空ならロゴを載せない。
    clean_domain = _valid_logo_domain(logo_domain)
    clean_brand = re.sub(r"\s+", " ", (logo_brand or "")).strip()[:60] or None
    return (clean_brand, clean_domain) if clean_brand and clean_domain else (None, None)


def is_duplicate_seo_topic(primary_topic: str, title: str, existing_titles: list[str]) -> bool:
    """同じ検索意図の記事を増やさないための公開前チェック。"""
    def normalise(value: str) -> str:
        plain = unescape(re.sub(r"<[^>]+>", "", value)).lower()
        plain = re.sub(r"20\d{2}年?(最新|最新版)?", "", plain)
        return re.sub(r"[\s　｜|・、】【（）()!?！？，、:：\-ー]", "", plain)

    topic = primary_topic.strip().lower()
    normalized_title = normalise(title)
    for existing in existing_titles:
        normalized_existing = normalise(existing)
        similar = SequenceMatcher(None, normalized_title, normalized_existing).ratio()
        same_topic = len(topic) >= 3 and topic in existing.lower()
        if similar >= 0.62 or (same_topic and similar >= 0.42):
            logger.warning("類似SEO記事を検出: %s", unescape(existing))
            return True
    return False


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
9. 本文の先頭には、投稿タイトルと同じ意味を保ちながら表現を少し変えた h2 見出しを置く。この見出しは投稿タイトルと一字一句同じにしない
10. 取引所・企業・財団など、記事の中心となる組織がある場合は、その組織名と公式サイトのドメインを指定する。公式サイトのドメインを確信できない場合は空文字にする

必ず以下のJSON形式のみで出力してください（前後に余計なテキストを含めないこと）:
{{
  "title": "SEO最適化された日本語タイトル（30〜60文字、数字や具体的な情報を含む）",
  "lead_heading": "投稿タイトルとは少し表現を変えた、本文冒頭用の日本語h2見出し",
  "content": "<h3>具体的な見出し1</h3><p>本文...</p><h3>具体的な見出し2</h3><p>本文...</p><h3>具体的な見出し3</h3><p>本文...</p>",
  "excerpt": "記事の要約（100〜150文字）",
  "meta_description": "Google検索結果に表示されるメタディスクリプション（120〜160文字）",
  "tags": ["ビットコイン", "仮想通貨", "関連タグ3", "関連タグ4", "関連タグ5"],
  "slug": "bitcoin-etf-record-inflows (英語・小文字・ハイフン区切り・3〜5単語)",
  "image_prompt": "Describe one specific photorealistic news photograph scene for this article. One concrete subject with lighting and setting. Examples: 'stacked gold coins on dark marble surface, dramatic side lighting', 'trading monitor displaying red price chart, blue screen glow', 'rows of server racks in dark data center, blue LED light', 'physical gold bar on reflective black surface, spotlight'. NO people, NO brand names, NO text. Max 15 words.",
  "logo_brand": "記事の中心となる組織名。該当しなければ空文字",
  "logo_domain": "logo_brandの公式サイトドメイン。確信できなければ空文字。https://やパスは含めない",
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


def _fetch_brand_logo(domain: str):
    """Google favicon service から組織のロゴマークを取得する。"""
    response = requests.get(
        "https://www.google.com/s2/favicons",
        params={"domain": domain, "sz": 256},
        headers={"User-Agent": "helloBTC image publisher/1.0"},
        timeout=12,
    )
    response.raise_for_status()
    from PIL import Image
    return Image.open(io.BytesIO(response.content)).convert("RGBA")


def _remove_white_logo_canvas(logo):
    """外周につながる白い台紙だけを透過化し、ロゴ本体は残す。"""
    from collections import deque

    logo = logo.copy().convert("RGBA")
    width, height = logo.size
    pixels = logo.load()
    queue = deque()
    seen = set()

    def is_light_canvas(x, y):
        red, green, blue, alpha = pixels[x, y]
        return alpha > 0 and red >= 242 and green >= 242 and blue >= 242

    for x in range(width):
        queue.extend(((x, 0), (x, height - 1)))
    for y in range(height):
        queue.extend(((0, y), (width - 1, y)))

    while queue:
        x, y = queue.popleft()
        if (x, y) in seen or not (0 <= x < width and 0 <= y < height):
            continue
        seen.add((x, y))
        if not is_light_canvas(x, y):
            continue
        red, green, blue, _ = pixels[x, y]
        pixels[x, y] = (red, green, blue, 0)
        queue.extend(((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))

    bbox = logo.getchannel("A").getbbox()
    return logo.crop(bbox) if bbox else logo


def _overlay_brand_logo(image_data: bytes, brand_name: str, brand_domain: str) -> bytes:
    """生成済み画像に、白い台紙を付けず公式ロゴマークだけを合成する。"""
    from PIL import Image, ImageFilter

    image = Image.open(io.BytesIO(image_data)).convert("RGBA")
    logo = _remove_white_logo_canvas(_fetch_brand_logo(brand_domain))
    logo.thumbnail((170, 170), Image.LANCZOS)
    if logo.width < 12 or logo.height < 12:
        raise ValueError("取得したロゴ画像が小さすぎます")

    # 背景との視認性を確保するため、カードではなく薄い影だけを敷く。
    shadow = Image.new("RGBA", logo.size, (0, 0, 0, 0))
    shadow.putalpha(logo.getchannel("A").point(lambda value: int(value * 0.42)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    margin = 36
    x = image.width - logo.width - margin
    y = margin
    image.alpha_composite(shadow, (x + 4, y + 5))
    image.alpha_composite(logo, (x, y))

    output = io.BytesIO()
    image.convert("RGB").save(output, format="JPEG", quality=92)
    logger.info("アイキャッチに%sのロゴを追加", brand_name)
    return output.getvalue()


def generate_featured_image(image_prompt, tags=None, logo_brand=None, logo_domain=None):
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
    image_data = output.getvalue()

    domain = _valid_logo_domain(logo_domain)
    if logo_brand and domain:
        try:
            return _overlay_brand_logo(image_data, str(logo_brand), domain)
        except Exception as e:
            # ロゴ取得の一時失敗で、記事全体の公開を止めない。
            logger.warning("%sのロゴ追加に失敗: %s", logo_brand, e)
    return image_data


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
    """Claude Haiku 4.5 を呼び出してテキストを返す。コードブロックマーカーを除去する。"""
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if msg.stop_reason == "max_tokens":
        # 途中で途切れたHTMLは、未閉鎖のSWELLボックスを生み表示を壊す。
        raise RuntimeError("記事HTMLが出力上限で途中終了しました")
    text = msg.content[0].text.strip()
    # ```html や ``` などのコードブロックマーカーを除去
    import re as _re
    text = _re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = _re.sub(r"\n?```$", "", text)
    return text.strip()


def _generate_meta_json(html_content: str, article_type: str, chart_hint: str) -> dict:
    """生成済みHTMLを元にタイトル・スラッグ等のメタデータをJSON生成（小さいのでパース安定）。"""
    prompt = f"""以下のHTML記事（{article_type}カテゴリ）を読んで、メタデータをJSONで出力してください。

記事の冒頭（参考）:
{html_content[:800]}

必ず以下のJSONのみ出力してください（前後にテキスト不要）:
{{
  "title": "SEO最適化された日本語タイトル（35〜65文字、具体的な数字・年を含む）",
  "primary_topic": "この記事が答える中心テーマ（例: Solana、暗号資産の税金）",
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
    """コラム・DeFi・取引所カテゴリ向けSWELLブロック記法記事を生成する。"""

    type_configs = {
        "コラム": {
            "theme": "仮想通貨・ブロックチェーン業界の最新トレンド・時事コラム。市場動向の背景にある社会的・経済的要因を深堀りした考察記事",
            "chart_hint": "仮想通貨の市場シェア・価格推移・時価総額変動の比較グラフ",
            "topics": "市場サイクル・機関投資家・規制動向・ビットコインETF・AI×ブロックチェーン・RWA・ステーブルコイン政策など",
        },
        "DeFi": {
            "theme": "分散型金融（DeFi）の仕組み・主要プロトコル・リスク・利回り・最新動向の解説記事",
            "chart_hint": "主要DeFiプロトコルのTVL（ロック総額）または APY（利回り）比較棒グラフ",
            "topics": "Uniswap・Aave・Compound・Curve・Lido・EigenLayer・Pendle・LST・LRT・イールドファーミング・流動性プールなど",
        },
        "取引所": {
            "theme": "仮想通貨取引所の選び方・手数料比較・セキュリティ・機能・使い方の解説記事",
            "chart_hint": "国内外の主要取引所の取引量・手数料・取扱銘柄数の比較グラフ",
            "topics": "取引所比較・セキュリティ・スプレッド・IEO・コピートレード・レバレッジ・税金・スマートフォンアプリなど",
        },
    }

    cfg = type_configs.get(article_type, type_configs["コラム"])

    # ── Pass 1: SWELLブロック本文のみ生成 ────────────────────────────────────
    html_prompt = f"""あなたは仮想通貨専門の上級ライターです。helloBTC（WordPress SWELLテーマ）向けに「{article_type}」カテゴリの記事を書いてください。
出力はWordPress Gutenbergブロック記法のみ。JSONもコードブロック（```）も不要。

【テーマ】{cfg['theme']}
参考トピック: {cfg['topics']}

【品質目標：エックスサーバービジネスの記事レベル】
・各H2セクションは「読者の疑問1つ」に完全に答えて完結する
・300〜400文字ごとに必ず視覚要素（テーブル・ステップ・ボックスのいずれか1つ）を挟む
・専門用語は初出時に必ず括弧で簡潔に説明する
・数値は、変動しない仕様・公式に確認できるデータだけを使う。価格、時価総額、手数料、順位など変動する数値は根拠なしに書かない
・段落は3〜5文で完結、1段落あたり100〜150文字を目安にする

━━━━━━━━━━━━━━━━━━━━━━
【使用するSWELLブロック一覧】
━━━━━━━━━━━━━━━━━━━━━━

▼ 段落（重要語句に <span class="swl-marker mark_orange">マーカー</span> を積極使用）
<!-- wp:paragraph -->
<p>本文テキスト。<span class="swl-marker mark_orange">重要語句</span>は必ずマーカーで強調。</p>
<!-- /wp:paragraph -->

▼ セクション区切り＋H2（セクション冒頭に必ず使う）
<!-- wp:separator --><hr class="wp-block-separator has-alpha-channel-opacity" /><!-- /wp:separator -->
<!-- wp:heading -->
<h2 class="wp-block-heading">〇〇とは？／〇〇の方法／〇〇を比較</h2>
<!-- /wp:heading -->

▼ H3見出し
<!-- wp:heading {{"level":3}} -->
<h3 class="wp-block-heading">小見出し</h3>
<!-- /wp:heading -->

▼ アイコン付き段落（用途別に使い分ける）
<!-- wp:paragraph {{"className":"is-style-big_icon_point"}} -->
<p class="is-style-big_icon_point"><strong>そのセクションの結論を1文で。読者が得た情報の要点。</strong></p>
<!-- /wp:paragraph -->
<!-- wp:paragraph {{"className":"is-style-big_icon_check"}} -->
<p class="is-style-big_icon_check"><strong>メリット名：</strong>具体的な説明</p>
<!-- /wp:paragraph -->
<!-- wp:paragraph {{"className":"is-style-big_icon_caution"}} -->
<p class="is-style-big_icon_caution"><strong>注意点名：</strong>具体的なリスク・注意内容</p>
<!-- /wp:paragraph -->
<!-- wp:paragraph {{"className":"is-style-big_icon_memo"}} -->
<p class="is-style-big_icon_memo"><strong>補足：</strong>知っておくと役立つ情報</p>
<!-- /wp:paragraph -->

▼ ポイントボックス（重要情報をまとめる）
<!-- wp:loos/cap-block {{"dataColSet":"col1","className":"is-style-onborder_ttl"}} -->
<div class="swell-block-capbox cap_box is-style-onborder_ttl" data-colset="col1">
<div class="cap_box_ttl"><span>✅ この記事でわかること</span></div>
<div class="cap_box_content"><!-- wp:list -->
<ul class="wp-block-list"><!-- wp:list-item --><li><strong>ポイント1：</strong>具体的な内容</li><!-- /wp:list-item --><!-- wp:list-item --><li><strong>ポイント2：</strong>具体的な内容</li><!-- /wp:list-item --><!-- wp:list-item --><li><strong>ポイント3：</strong>具体的な内容</li><!-- /wp:list-item --></ul>
<!-- /wp:list --></div>
</div>
<!-- /wp:loos/cap-block -->

▼ 注意・警告ボックス
<!-- wp:loos/cap-block {{"dataColSet":"col1","className":"is-style-onborder_ttl2"}} -->
<div class="swell-block-capbox cap_box is-style-onborder_ttl2" data-colset="col1">
<div class="cap_box_ttl"><span>⚠️ 注意点</span></div>
<div class="cap_box_content"><!-- wp:list -->
<ul class="wp-block-list"><!-- wp:list-item --><li>注意項目1</li><!-- /wp:list-item --><!-- wp:list-item --><li>注意項目2</li><!-- /wp:list-item --></ul>
<!-- /wp:list --></div>
</div>
<!-- /wp:loos/cap-block -->

▼ ステップブロック（手順説明に使う。各ステップの title と body を充実させる）
※ stepLabel は必ず "STEP" 固定。__num は空のまま（SWELLが自動で番号を振る）。
<!-- wp:loos/step -->
<div class="swell-block-step" data-num-style="circle">
<!-- wp:loos/step-item {{"stepLabel":"STEP"}} -->
<div class="swell-block-step__item"><div class="swell-block-step__number u-bg-main"><span class="__label">STEP</span><span class="__num"></span></div><div class="swell-block-step__title">ステップタイトル（動詞で）</div><div class="swell-block-step__body"><!-- wp:paragraph --><p>具体的な手順の説明。所要時間や注意点も添える。</p><!-- /wp:paragraph --></div></div>
<!-- /wp:loos/step-item -->
<!-- wp:loos/step-item {{"stepLabel":"STEP"}} -->
<div class="swell-block-step__item"><div class="swell-block-step__number u-bg-main"><span class="__label">STEP</span><span class="__num"></span></div><div class="swell-block-step__title">ステップ2タイトル</div><div class="swell-block-step__body"><!-- wp:paragraph --><p>説明テキスト</p><!-- /wp:paragraph --></div></div>
<!-- /wp:loos/step-item -->
</div>
<!-- /wp:loos/step -->

▼ FAQブロック（よくある質問専用・構造化データ付き）
<!-- wp:loos/faq {{"outputJsonLd":true,"className":"is-style-faq-box"}} -->
<dl class="swell-block-faq is-style-faq-box" data-q="col-text" data-a="col-text">
<!-- wp:loos/faq-item -->
<div class="swell-block-faq__item"><dt class="faq_q">読者が実際に検索しそうな質問文（？で終わる）</dt><dd class="faq_a"><!-- wp:paragraph --><p>具体的で役立つ回答。数値・事例を含める。</p><!-- /wp:paragraph --></dd></div>
<!-- /wp:loos/faq-item -->
</dl>
<!-- /wp:loos/faq -->

▼ 用語定義リスト（専門用語の解説に使う）
<!-- wp:loos/dl {{"className":"is-style-border_left"}} -->
<dl class="swell-block-dl is-style-border_left">
<!-- wp:loos/dl-item --><div class="swell-block-dl__item"><dt class="swell-block-dl__dt">用語名</dt><dd class="swell-block-dl__dd">わかりやすい説明（初心者でも理解できる言葉で）</dd></div><!-- /wp:loos/dl-item -->
</dl>
<!-- /wp:loos/dl -->

▼ 比較テーブル
<!-- wp:table {{"swlScrollable":"sp","swlTableWidth":"800px","swlFz":"13px"}} -->
<figure class="wp-block-table"><table class="has-fixed-layout"><thead><tr><th class="has-text-align-center" data-align="center">比較項目</th><th class="has-text-align-center" data-align="center">A</th><th class="has-text-align-center" data-align="center">B</th></tr></thead><tbody><tr><td class="has-text-align-center" data-align="center"><strong>項目名</strong></td><td class="has-text-align-center" data-align="center">値</td><td class="has-text-align-center" data-align="center">値</td></tr></tbody></table></figure>
<!-- /wp:table -->

▼ 画像プレースホルダー
<!-- wp:html -->
{{IMAGE_1}}
<!-- /wp:html -->

━━━━━━━━━━━━━━━━━━━━━━
【記事構成（必ずこの順番・この設計で）】
━━━━━━━━━━━━━━━━━━━━━━

【冒頭】
① 「この記事でわかること」cap-block（is-style-onborder_ttl + リスト3〜4項目、各項目は「太字キーワード：説明」形式）
② リード文（段落 × 2〜3）
   ・1段落目：読者の悩み・疑問を具体的に提示
   ・2段落目：この記事を読むと何が解決するかを明示
   ・3段落目（任意）：記事の信頼性・根拠への言及
③ テーマが自動表示する目次を使うため、手作業の目次ボックスは作らない

【本文：H2セクション × 4〜5個】
各セクションのルール：
  A. [separator + H2]（見出しは「〇〇とは？」「〇〇の方法」「〇〇を比較」など疑問・行動形式）
  B. 導入段落 × 1（そのH2が答える問いを1文で示す）
  C. 本文段落 × 1〜2（具体的な説明・データ・事例）
  D. 視覚要素 × 1（以下から1種類だけ選ぶ）：
     ・テーブル（数値データ・比較がある場合）
     ・ステップブロック（手順がある場合）
     ・DLリスト（専門用語が複数ある場合）
     ・big_icon_check × 2〜3（メリット列挙の場合）
     ・注意cap-block（リスク・警告の場合）
  E. セクション結論：big_icon_point × 1（「つまり〇〇だ」という1文結論）
  F. 画像プレースホルダー（{{IMAGE_1}} または {{IMAGE_2}} を各1回）

推奨セクション構成例：
  S1「〇〇とは？定義と概要」→ DLリスト or テーブル → big_icon_point
  S2「〇〇の仕組み・特徴」   → H3 × 2 + 段落 → big_icon_check × 2 → {{IMAGE_1}}
  S3「〇〇の始め方・使い方」 → ステップブロック × 3〜4 → big_icon_memo
  S4「〇〇の比較・データ」   → 比較テーブル → big_icon_caution × 1 → {{IMAGE_2}}
  S5「〇〇のリスク・注意点」 → 注意cap-block → big_icon_caution × 1〜2

【FAQ】
⑨ separator + H2「よくある質問」+ loos/faq（3問。実際に検索されそうな疑問形の質問）

【まとめ】— ここが最重要。xserverレベルの構造化まとめを書く。
⑩ separator + H2「まとめ：〇〇について」
<!-- wp:paragraph -->
<p>本記事では〇〇について解説した。（記事の要旨を1〜2文で振り返る）</p>
<!-- /wp:paragraph -->
cap-block（is-style-onborder_ttl、タイトル「📌 本記事のまとめ」）内に big_icon_check × 3個：
  各項目：<p class="is-style-big_icon_check"><strong>キーワード：</strong>1文の要点</p>
<!-- wp:paragraph -->
<p>（読者への次のアクション・締めの一文）</p>
<!-- /wp:paragraph -->

【末尾】
⑪ {{CHART}}（グラフ）
⑫ 免責文:
<!-- wp:paragraph -->
<p style="font-size:0.85em;color:#888;">※本記事は情報提供を目的としており、投資助言ではありません。投資はご自身の判断と責任で行ってください。</p>
<!-- /wp:paragraph -->

━━━━━━━━━━━━━━━━━━━━━━
【ライティングルール（厳守）】
━━━━━━━━━━━━━━━━━━━━━━
・文体：ですます調（〜です・〜ます・〜ています）で統一する
・1段落：3〜5文・100〜150文字を目安
・専門用語：初出時に必ず（）内で説明
・数値は、変動しない仕様・公式に確認できるデータだけを使う。価格、時価総額、手数料、順位など変動する数値は根拠なしに書かない
・マーカー：各段落に1〜2箇所、重要キーワードに使う
・テキスト総量：1800〜2200文字（まとめを含む）。出力の途中終了を防ぐため、冗長な繰り返しは書かない
・赤・オレンジ系の注意cap-block（is-style-onborder_ttl2）は、実際の安全上・投資上の注意が必要な箇所で最大1個だけ使用する。目次や通常説明には使わない
・cap-blockは記事全体で最大3個まで。各cap-blockの最後に必ず </div></div> と <!-- /wp:loos/cap-block --> を記述する
・JSONなし。Gutenbergブロック記法のみ出力。"""

    logger.info(f"  Pass1: {article_type} SWELLブロック本文生成中...")
    html_content = _call_haiku(html_prompt, max_tokens=8192)

    # ── Pass 2: メタデータJSON生成（小さいのでパース安定）────────────────
    logger.info(f"  Pass2: メタデータJSON生成中...")
    meta = _generate_meta_json(html_content, article_type, cfg["chart_hint"])
    meta["content"] = normalize_swell_html(html_content)
    return meta


def _generate_kiso_article() -> dict:
    """アルトコイン基礎知識記事を2パスで生成（Pass1=SWELLブロック本文、Pass2=メタデータJSON）。"""

    # ── Pass 1: SWELLブロック本文のみ生成 ────────────────────────────────
    html_prompt = """あなたは仮想通貨専門の上級ライターです。helloBTC（WordPress SWELLテーマ）向けにアルトコイン基礎知識記事を書いてください。
出力はWordPress Gutenbergブロック記法のみ。JSONもコードブロック（```）も不要。

【テーマ選定】2026年時点でSEO需要が高いコインを1つ選ぶ:
Ethereum・Solana・XRP・Cardano・Avalanche・Polkadot・Chainlink・Polygon・TON・SUI・NEAR・Aptos・Arbitrum・Optimism・Cosmos・Filecoin・Render・Injective・Celestia・Starknet

【品質目標：エックスサーバービジネスの記事レベル】
・各H2セクションは「読者の疑問1つ」に完全に答えて完結する
・専門用語は初出時に必ず（）内で説明する
・具体的な数値・データを必ず含める（「高速」→「○○TPS」など）
・段落は3〜5文・100〜150文字を目安にする

━━━━━━━━━━━━━━━━━━━━━━
【使用するSWELLブロック一覧】
━━━━━━━━━━━━━━━━━━━━━━

▼ 段落（重要語句に <span class="swl-marker mark_orange">マーカー</span> を積極使用）
<!-- wp:paragraph -->
<p>本文テキスト。<span class="swl-marker mark_orange">重要語句</span>は必ずマーカーで強調。</p>
<!-- /wp:paragraph -->

▼ セクション区切り＋H2
<!-- wp:separator --><hr class="wp-block-separator has-alpha-channel-opacity" /><!-- /wp:separator -->
<!-- wp:heading -->
<h2 class="wp-block-heading">○○とは？／○○の仕組み／○○を比較</h2>
<!-- /wp:heading -->

▼ H3見出し
<!-- wp:heading {"level":3} -->
<h3 class="wp-block-heading">小見出し</h3>
<!-- /wp:heading -->

▼ アイコン付き段落（用途別）
<!-- wp:paragraph {"className":"is-style-big_icon_point"} -->
<p class="is-style-big_icon_point"><strong>そのセクションの結論を1文で。</strong></p>
<!-- /wp:paragraph -->
<!-- wp:paragraph {"className":"is-style-big_icon_check"} -->
<p class="is-style-big_icon_check"><strong>強み・メリット名：</strong>具体的な説明</p>
<!-- /wp:paragraph -->
<!-- wp:paragraph {"className":"is-style-big_icon_caution"} -->
<p class="is-style-big_icon_caution"><strong>リスク・注意点名：</strong>具体的な内容</p>
<!-- /wp:paragraph -->
<!-- wp:paragraph {"className":"is-style-big_icon_memo"} -->
<p class="is-style-big_icon_memo"><strong>補足：</strong>知っておくと役立つ情報</p>
<!-- /wp:paragraph -->

▼ ポイントボックス（cap-block）
<!-- wp:loos/cap-block {"dataColSet":"col1","className":"is-style-onborder_ttl"} -->
<div class="swell-block-capbox cap_box is-style-onborder_ttl" data-colset="col1">
<div class="cap_box_ttl"><span>✅ この記事でわかること</span></div>
<div class="cap_box_content"><!-- wp:list -->
<ul class="wp-block-list"><!-- wp:list-item --><li><strong>ポイント1：</strong>内容</li><!-- /wp:list-item --><!-- wp:list-item --><li><strong>ポイント2：</strong>内容</li><!-- /wp:list-item --><!-- wp:list-item --><li><strong>ポイント3：</strong>内容</li><!-- /wp:list-item --></ul>
<!-- /wp:list --></div>
</div>
<!-- /wp:loos/cap-block -->

▼ 注意ボックス
<!-- wp:loos/cap-block {"dataColSet":"col1","className":"is-style-onborder_ttl2"} -->
<div class="swell-block-capbox cap_box is-style-onborder_ttl2" data-colset="col1">
<div class="cap_box_ttl"><span>⚠️ 投資リスク</span></div>
<div class="cap_box_content"><!-- wp:list -->
<ul class="wp-block-list"><!-- wp:list-item --><li>リスク項目1</li><!-- /wp:list-item --><!-- wp:list-item --><li>リスク項目2</li><!-- /wp:list-item --></ul>
<!-- /wp:list --></div>
</div>
<!-- /wp:loos/cap-block -->

▼ ステップブロック（stepLabel は "STEP" 固定・__num は空のままにする）
<!-- wp:loos/step -->
<div class="swell-block-step" data-num-style="circle">
<!-- wp:loos/step-item {"stepLabel":"STEP"} -->
<div class="swell-block-step__item"><div class="swell-block-step__number u-bg-main"><span class="__label">STEP</span><span class="__num"></span></div><div class="swell-block-step__title">ステップタイトル（動詞形）</div><div class="swell-block-step__body"><!-- wp:paragraph --><p>具体的な手順。注意点も含める。</p><!-- /wp:paragraph --></div></div>
<!-- /wp:loos/step-item -->
<!-- wp:loos/step-item {"stepLabel":"STEP"} -->
<div class="swell-block-step__item"><div class="swell-block-step__number u-bg-main"><span class="__label">STEP</span><span class="__num"></span></div><div class="swell-block-step__title">ステップ2</div><div class="swell-block-step__body"><!-- wp:paragraph --><p>説明テキスト</p><!-- /wp:paragraph --></div></div>
<!-- /wp:loos/step-item -->
</div>
<!-- /wp:loos/step -->

▼ FAQブロック
<!-- wp:loos/faq {"outputJsonLd":true,"className":"is-style-faq-box"} -->
<dl class="swell-block-faq is-style-faq-box" data-q="col-text" data-a="col-text">
<!-- wp:loos/faq-item -->
<div class="swell-block-faq__item"><dt class="faq_q">実際に検索されそうな疑問形の質問？</dt><dd class="faq_a"><!-- wp:paragraph --><p>具体的・役立つ回答。数値や事例を含める。</p><!-- /wp:paragraph --></dd></div>
<!-- /wp:loos/faq-item -->
</dl>
<!-- /wp:loos/faq -->

▼ 用語定義リスト
<!-- wp:loos/dl {"className":"is-style-border_left"} -->
<dl class="swell-block-dl is-style-border_left">
<!-- wp:loos/dl-item --><div class="swell-block-dl__item"><dt class="swell-block-dl__dt">用語</dt><dd class="swell-block-dl__dd">初心者向けわかりやすい説明</dd></div><!-- /wp:loos/dl-item -->
</dl>
<!-- /wp:loos/dl -->

▼ 基本情報テーブル（2列）
<!-- wp:table {"swlScrollable":"sp","swlTableWidth":"700px","swlFz":"13px"} -->
<figure class="wp-block-table"><table class="has-fixed-layout"><thead><tr><th class="has-text-align-center" data-align="center">項目</th><th class="has-text-align-center" data-align="center">内容</th></tr></thead><tbody><tr><td class="has-text-align-center" data-align="center"><strong>名称</strong></td><td class="has-text-align-center" data-align="center">○○</td></tr></tbody></table></figure>
<!-- /wp:table -->

▼ 比較テーブル（4列）
<!-- wp:table {"swlScrollable":"sp","swlTableWidth":"800px","swlFz":"12px"} -->
<figure class="wp-block-table"><table class="has-fixed-layout"><thead><tr><th class="has-text-align-center" data-align="center">比較項目</th><th class="has-text-align-center" data-align="center">このコイン</th><th class="has-text-align-center" data-align="center">競合A</th><th class="has-text-align-center" data-align="center">競合B</th></tr></thead><tbody><tr><td class="has-text-align-center" data-align="center">項目</td><td class="has-text-align-center" data-align="center">値</td><td class="has-text-align-center" data-align="center">値</td><td class="has-text-align-center" data-align="center">値</td></tr></tbody></table></figure>
<!-- /wp:table -->

▼ 画像プレースホルダー
<!-- wp:html -->
{IMAGE_1}
<!-- /wp:html -->

━━━━━━━━━━━━━━━━━━━━━━
【記事構成（必ずこの順番・この設計で）】
━━━━━━━━━━━━━━━━━━━━━━

【冒頭】
① 「この記事でわかること」cap-block（is-style-onborder_ttl + リスト3〜4項目、各「太字キーワード：説明」形式）
② リード文（段落 × 2〜3）
   ・1段落目：このコインへの関心・疑問を読者目線で提示
   ・2段落目：この記事で解決できることを明示
③ テーマが自動表示する目次を使うため、手作業の目次ボックスは作らない

【本文：H2セクション × 5個（各セクションのルールを必ず守る）】
セクションルール：
  A. separator + H2（疑問・行動形の見出し）
  B. 導入段落 × 1（問いへの前置き）
  C. 本文段落 × 1〜2（説明・データ・事例）
  D. 視覚要素 × 1（テーブル or ステップ or DLリスト or big_icon × 複数）
  E. big_icon_point × 1（セクションの結論1文）

S1「[コイン名]とは？概要と基本情報」
  → DLリスト（主要用語3〜4個） → 基本情報テーブル → {IMAGE_1} → big_icon_point

S2「[コイン名]の仕組みと技術的特徴」
  → H3 × 2（特徴ごとに分けて説明） → big_icon_check × 2 → big_icon_point

S3「[コイン名]の始め方・購入方法」
  → ステップブロック × 3〜4 → big_icon_memo → {IMAGE_2}

S4「[コイン名] vs 競合：比較で見る強みと弱み」
  → 比較テーブル（4列・4〜6行） → big_icon_check × 1 → big_icon_caution × 1 → big_icon_point

S5「[コイン名]の将来性とリスク」
  → 段落 × 2 → 注意cap-block（リスク3〜4項目） → big_icon_caution × 1 → {CHART}

【FAQ】
⑨ separator + H2「よくある質問」+ loos/faq（3問。初心者が実際に検索しそうな疑問）

【まとめ — 最重要、必ず構造化する】
⑩ separator + H2「まとめ：[コイン名]について」
<!-- wp:paragraph -->
<p>本記事では[コイン名]の基本情報・仕組み・始め方・リスクについて解説した。</p>
<!-- /wp:paragraph -->
cap-block（is-style-onborder_ttl、タイトル「📌 本記事のまとめ」）内に big_icon_check × 3個：
  各項目：<p class="is-style-big_icon_check"><strong>キーワード：</strong>要点1文</p>
<!-- wp:paragraph -->
<p>（読者への次のアクション・投資判断への一言）</p>
<!-- /wp:paragraph -->

【末尾】
⑪ 免責文:
<!-- wp:paragraph -->
<p style="font-size:0.85em;color:#888;">※本記事は情報提供を目的としており投資助言ではありません。投資はご自身の判断と責任で行ってください。</p>
<!-- /wp:paragraph -->

【必ず含める】{IMAGE_1}・{IMAGE_2}・{CHART} を <!-- wp:html --> ブロック内に1つずつ配置。

━━━━━━━━━━━━━━━━━━━━━━
【ライティングルール（厳守）】
━━━━━━━━━━━━━━━━━━━━━━
・文体：ですます調（〜です・〜ます・〜ています）で統一する
・専門用語：初出時に（）内で説明（例：TPS（1秒あたりの処理件数））
・数値は、変動しない仕様・公式に確認できるデータだけを使う。価格、時価総額、手数料、順位など変動する数値は根拠なしに書かない
・マーカー：各段落に1〜2箇所
・テキスト総量：1800〜2200文字。出力の途中終了を防ぐため、同じ内容を繰り返さない
・赤・オレンジ系の注意cap-block（is-style-onborder_ttl2）は、実際の安全上・投資上の注意が必要な箇所で最大1個だけ使用する。目次や通常説明には使わない
・cap-blockは記事全体で最大3個まで。各cap-blockの最後に必ず </div></div> と <!-- /wp:loos/cap-block --> を記述する
・JSONなし。Gutenbergブロック記法のみ出力。"""

    logger.info("  Pass1: 基礎知識 SWELLブロック本文生成中...")
    html_content = _call_haiku(html_prompt, max_tokens=8192)

    # ── Pass 2: メタデータJSON生成 ────────────────────────────────────────
    logger.info("  Pass2: メタデータJSON生成中...")
    meta = _generate_meta_json(html_content, "基礎知識", "主要コインのTPS・時価総額・TVL比較")
    meta["content"] = normalize_swell_html(html_content)
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
