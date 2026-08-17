"""OpenAI API を使って日本語 SEO 記事を生成する。"""
import json
import logging
import io
import os
import re
from difflib import SequenceMatcher
from html import escape, unescape
from urllib.parse import urlparse

from llm_client import generate_json, generate_text
from image_processing import SAFE_COMPOSITION_PROMPT, fit_image_to_jpeg


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

NEWS_ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "lead_heading": {"type": "string"},
        "content": {"type": "string"},
        "excerpt": {"type": "string"},
        "meta_description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "slug": {"type": "string"},
        "image_prompt": {"type": "string"},
        "logo_brand": {"type": "string"},
        "logo_domain": {"type": "string"},
        "tweet_bullets": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "title", "lead_heading", "content", "excerpt", "meta_description",
        "tags", "slug", "image_prompt", "logo_brand", "logo_domain",
        "tweet_bullets",
    ],
    "additionalProperties": False,
}

SEO_METADATA_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "primary_topic": {"type": "string"},
        "excerpt": {"type": "string"},
        "slug": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "tweet_bullets": {"type": "array", "items": {"type": "string"}},
        "featured_image_prompt": {"type": "string"},
        "article_image_prompts": {
            "type": "array",
            "items": {"type": "string"},
        },
        "chart": {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "title": {"type": "string"},
                "labels": {"type": "array", "items": {"type": "string"}},
                "values": {"type": "array", "items": {"type": "number"}},
                "unit": {"type": "string"},
                "caption": {"type": "string"},
            },
            "required": ["type", "title", "labels", "values", "unit", "caption"],
            "additionalProperties": False,
        },
    },
    "required": [
        "title", "primary_topic", "excerpt", "slug", "tags", "tweet_bullets",
        "featured_image_prompt", "article_image_prompts", "chart",
    ],
    "additionalProperties": False,
}

# ロゴ利用を許可する当事者プロジェクトと公式ドメイン。
# 出典メディアや未確認ブランドを画像へ混入させないための許可リストとして使う。
KNOWN_BRAND_DOMAINS = {
    "BitMart": "bitmart.com",
    "Binance": "binance.com",
    "Coinbase": "coinbase.com",
    "Kraken": "kraken.com",
    "OKX": "okx.com",
    "Bybit": "bybit.com",
    "Bitget": "bitget.com",
    "BingX": "bingx.com",
    "Robinhood": "robinhood.com",
    "Hyperliquid": "hyperliquid.xyz",
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


def append_source_attribution(content: str, source_name: str, source_url: str) -> str:
    """ニュース本文の末尾に、読者が確認できる出典リンクを追加する。"""
    clean_url = (source_url or "").strip()
    parsed = urlparse(clean_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        logger.warning("無効な出典URLのため出典表記を追加しません: %s", source_url)
        return content

    label = re.sub(r"\s+", " ", unescape(source_name or "")).strip() or parsed.netloc
    source_block = (
        '<!-- wp:paragraph {"className":"hellobtc-source"} -->\n'
        '<p class="hellobtc-source">出典：'
        f'<a href="{escape(clean_url, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer">{escape(label)}</a></p>\n'
        '<!-- /wp:paragraph -->'
    )
    return content.rstrip() + "\n" + source_block


def _valid_logo_domain(domain: str | None) -> str | None:
    """外部URL取得に使える、スキームを含まない正規ドメインだけを受け入れる。"""
    if not domain:
        return None
    candidate = domain.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    return candidate if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9-]+)+", candidate) else None


def resolve_logo_brand(title: str, tags: list[str] | None = None,
                       logo_brand: str | None = None, logo_domain: str | None = None) -> tuple[str | None, str | None]:
    """記事タイトル・タグに明記された当事者プロジェクトだけを許可する。

    出典メディアやLLMの推測によるロゴ採用を防ぐため、任意のブランド名・
    ドメインは採用せず、管理済みリストと記事タイトル／タグだけで判定する。
    """
    searchable = " ".join([title, *(tags or [])])
    searchable_lower = searchable.lower()
    for brand, domain in KNOWN_BRAND_DOMAINS.items():
        if brand.lower() in searchable_lower:
            return brand, domain
    return None, None


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
1. 元記事の事実関係と意味を正確に保ち、確認できない数値・発言・背景を追加しない
2. 日本の読者向けにわかりやすい言葉で書く（専門用語には簡単な説明を添える）
3. 重要なキーワードを自然に含める
4. H3見出しは3つ設ける。全ての見出しは記事の内容を具体的に表すタイトルにする（「まとめ」「概要」などの汎用的な言葉は使わない）
5. 元記事の要約だけで終わらせず、元記事内で確認できる事実を使って、日本の投資家への影響や用語の解説などの独自価値を加える
6. 読者が事実と解説を区別できる独立した構成で書く。出典リンクは投稿時にシステムが本文末尾へ追加するため、本文中に偽の出典やURLを作らない
7. 文体は「〜した」「〜だ」「〜である」の言い切り調で統一する（「〜しました」「〜です」などの丁寧語は使わない）
8. 公式ソース（ツイート）が提供されている場合は、記事の流れに合わせて適切な位置に埋め込む
9. 本文の先頭には、投稿タイトルと同じ意味を保ちながら表現を少し変えた h2 見出しを置く。この見出しは投稿タイトルと一字一句同じにしない
10. 取引所・企業・財団など、ニュースを発表した当事者プロジェクトが記事の中心にある場合だけ、その組織名と公式サイトのドメインを指定する。CoinDeskなど出典メディア、報道機関、記者、競合メディアは絶対に指定しない。当事者がいない場合や公式ドメインを確信できない場合は両方を空文字にする
11. アイキャッチは記事内容との一致を最優先にする。image_prompt には、元記事で確認できる中心的な出来事・対象物だけを具体的に描写する。汎用的な暗号資産ニュース画像や、記事に明記されない議事堂・政府建築・ランドマーク・国旗・都市景観を加えてはならない。建物、モニター、コイン、チャートも、元記事の中心的な対象である場合だけ指定する。書類・画面・看板が中心的対象なら使用してよいが、表示文字は正確な短い固有語・略称・数字だけを英語で引用符に入れて指定し、本文段落や架空の文字列を要求しない

必ず以下のJSON形式のみで出力してください（前後に余計なテキストを含めないこと）:
{{
  "title": "SEO最適化された日本語タイトル（30〜60文字、数字や具体的な情報を含む）",
  "lead_heading": "投稿タイトルとは少し表現を変えた、本文冒頭用の日本語h2見出し",
  "content": "<h3>具体的な見出し1</h3><p>本文...</p><h3>具体的な見出し2</h3><p>本文...</p><h3>具体的な見出し3</h3><p>本文...</p>",
  "excerpt": "記事の要約（100〜150文字）",
  "meta_description": "Google検索結果に表示されるメタディスクリプション（120〜160文字）",
  "tags": ["ビットコイン", "仮想通貨", "関連タグ3", "関連タグ4", "関連タグ5"],
  "slug": "bitcoin-etf-record-inflows (英語・小文字・ハイフン区切り・3〜5単語)",
  "image_prompt": "Describe one full-bleed photorealistic scene that directly depicts the verified central subject. A physical document, screen, or sign is allowed only when central to the verified event. If visible copy is essential, include at most three exact short English terms, initials, dates, or numbers in quotation marks; never request paragraph copy, fake words, pseudo-text, garbled text, or a webpage/headline layout. Do not add generic crypto decoration or unrelated objects. NO people. Max 25 words.",
  "logo_brand": "ニュースを発表した当事者プロジェクト名。出典メディアは禁止。該当しなければ空文字",
  "logo_domain": "当事者プロジェクトの公式サイトドメイン。出典メディアは禁止。確信できなければ空文字。https://やパスは含めない",
  "tweet_bullets": ["この記事の要点1（25文字以内）", "この記事の要点2（25文字以内）", "この記事の要点3（25文字以内）"]
}}"""

    return generate_json(
        prompt,
        schema_name="news_article",
        schema=NEWS_ARTICLE_SCHEMA,
        max_output_tokens=2048,
    )


def _trusted_project_logo(logo_brand: str | None, logo_domain: str | None) -> str | None:
    """管理済みの当事者プロジェクトと公式ドメインが一致する場合だけ返す。"""
    if not logo_brand:
        return None
    clean_domain = _valid_logo_domain(logo_domain)
    for brand, official_domain in KNOWN_BRAND_DOMAINS.items():
        if brand.lower() == logo_brand.strip().lower() and clean_domain == official_domain:
            return brand
    return None


def _image_article_context(article_title: str | None, article_content: str | None) -> str:
    """記事本文を画像モデルへ渡さず、文字の描画を誘発しない。"""
    if not (article_title or article_content):
        return ""
    # 日本語の見出し・本文をImagenへ渡すと、ニュースページ風の文字列として
    # 画像内に模写される。記事内容から抽出済みの英語image_promptだけを視覚指示に使う。
    return (
        "The opening visual brief was derived from a verified Japanese article. Treat it only as semantic "
        "scene guidance. Never recreate, quote, typeset, translate, or imitate the article title or body. "
    )


def _image_text_review_prompt(
    trusted_brand: str | None,
    visual_brief: str | None = None,
) -> str:
    logo_rule = (
        f"The single authentic {trusted_brand} brand mark is allowed only as an integrated environmental logo. "
        "Reject every other logo, publisher mark, or media brand."
        if trusted_brand
        else "Reject every logo, publisher mark, media brand, and watermark."
    )
    brief = (visual_brief or "").strip()
    brief_rule = f"The intended visual brief is: {brief}." if brief else "No visual brief was supplied."
    return f"""
Inspect this proposed Japanese news-site featured image at full resolution.
{brief_rule}
Short text naturally printed on an article-specific physical item such as a document, screen, sign, or device is
allowed only when every visible word is clearly legible, correctly spelled, contextually relevant to the visual
brief, and written in coherent Japanese or English. Correct initials, dates, numbers, and short labels are allowed.
Reject the image for any pseudo-text, invented glyph, malformed CJK character, fake or misspelled word, broken
number, mixed-script gibberish, duplicated fragment, corrupted text, garbled text, or unreadable character cluster.
Reject unexpected Chinese or other-language text unless the visual brief explicitly requires that language.
Reject dense generated paragraph copy when its wording cannot be verified. Natural depth-of-field blur is acceptable
only when it forms neutral continuous print texture without visible fake glyphs or character-like corruption.
Do not reject an image merely because it contains accurate, readable, contextually appropriate text on an object.
{logo_rule}
Return exactly one line: PASS if all visible text satisfies these rules, otherwise REJECT followed by a short reason.
""".strip()


def _image_review_passed(review_text: str) -> bool:
    return review_text.strip().upper() == "PASS"


class ImageReviewUnavailableError(RuntimeError):
    """画像の公開前検査を実行できない場合に、生成を安全側で停止する。"""


def _review_generated_image(
    client,
    raw_bytes: bytes,
    trusted_brand: str | None,
    visual_brief: str | None = None,
) -> tuple[bool, str]:
    """Gemini Visionで文字の可読性と正確性を検査し、文字化け画像を拒否する。"""
    from google.genai import types

    mime_type = "image/png" if raw_bytes.startswith(b"\x89PNG") else "image/jpeg"
    configured_model = os.environ.get("GOOGLE_IMAGE_REVIEW_MODEL", "").strip()
    review_models = [configured_model] if configured_model else ["gemini-3.6-flash", "gemini-3.5-flash"]
    errors = []
    for model_name in review_models:
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[
                    types.Part.from_bytes(data=raw_bytes, mime_type=mime_type),
                    _image_text_review_prompt(trusted_brand, visual_brief),
                ],
            )
            review = (response.text or "").strip()
            return _image_review_passed(review), review or "画像検査の応答が空でした"
        except Exception as exc:
            errors.append(f"{model_name}: {exc}")
            logger.warning("画像検査モデル%sを利用できません: %s", model_name, exc)
    raise ImageReviewUnavailableError(" / ".join(errors))


def _build_imagen_prompt(
    base_prompt: str,
    logo_brand: str | None,
    logo_domain: str | None,
    article_title: str | None = None,
    article_content: str | None = None,
) -> str:
    """記事内容を確認したうえで、報道写真と許可済みロゴの条件を組み立てる。"""
    trusted_brand = _trusted_project_logo(logo_brand, logo_domain)
    strict_text_free = bool(
        re.search(r"\b(?:no text|no writing|no print|text-free)\b", base_prompt, re.IGNORECASE)
    )
    if trusted_brand:
        logo_instruction = (
            f"The official {trusted_brand} brand mark must be clearly recognizable and visible as an integrated "
            "element of the article-specific primary subject, not omitted. Place it within an operational "
            "interface or physical object explicitly relevant to the article, matching the perspective, ambient lighting, reflections, texture, "
            "and depth of field. Keep it between 8 and 15 percent of the frame. It must feel "
            "photographed as part of the environment, never on a standalone card, sign, plaque, placard, paper, "
            "foreground panel, floating corner badge, sticker, white box, watermark, or separate overlay. "
            "Preserve the authentic logo proportions; never stretch, compress, skew, warp, or redraw it. "
            "Do not include any other logo or media branding. "
        )
    else:
        logo_instruction = (
            "No logos, media branding, watermarks, publisher marks, trademarks, or organization emblems on any "
            "object. Short accurate contextual copy is governed by the text-quality rule below and is not a logo. "
            "Any coin must be completely unbranded. "
        )
    if strict_text_free:
        style_instruction = (
            "Minimalist studio product photography on a seamless background. Show only the objects explicitly named "
            "in the opening brief. Do not infer or add contextual props, furniture, paperwork, signage, or scenery. "
        )
        text_instruction = (
            "The opening visual brief explicitly requires a text-free image. Absolutely no writing or writing-like "
            "marks may appear: no letters, words, numbers, labels, signs, UI copy, paragraph lines, pseudo-text, "
            "invented glyphs, malformed CJK, or garbled characters. Keep every visible surface fully blank. "
        )
        object_text_instruction = (
            "Do not add documents, labels, screens, nameplates, printed cards, or control panels. "
        )
    else:
        style_instruction = (
            "Photojournalism, Reuters news photography style. Shot on 85mm lens, f/2.0 aperture, shallow depth of "
            "field with soft bokeh background. Professional studio lighting or natural window light, realistic "
            "textures and materials. "
        )
        text_instruction = (
            "Natural writing on an article-specific physical item is allowed only when essential to the scene. "
            "Keep visible copy to one to three short, fully legible Japanese or English terms, initials, dates, or "
            "numbers explicitly supported by the opening visual brief. Spell every word correctly and render every "
            "character completely. Never invent paragraph body copy, fake words, pseudo-text, malformed CJK, mixed-script "
            "gibberish, duplicated fragments, garbled text, corrupted characters, or unreadable character clusters. "
            "Do not add Chinese or another language unless the opening visual brief explicitly requires it. "
        )
        object_text_instruction = (
            "Physical documents, screens, and signs may appear only when explicitly relevant to the opening visual "
            "brief; they must remain photographed objects rather than a page layout. Keep unrelated surfaces plain and "
            "unmarked. Avoid dense paragraph copy, generic paperwork, packaging text, serial plates, and crowded control "
            "layouts. "
        )

    return (
        f"{base_prompt}. "
        f"{_image_article_context(article_title, article_content)}"
        "Editorial relevance is mandatory: depict only the concrete primary subject or event specified in the "
        "opening brief. Do not add generic crypto-news decoration. Do not show a government building, capitol, "
        "parliament, White House, landmark, monument, flag, skyline, data center, trading monitor, coin, or chart "
        "unless it is explicitly named in the opening brief. The image must be visually specific to this article, "
        "not a reusable generic news scene. "
        f"{style_instruction}"
        "Muted color grading, slightly desaturated, cool tones. "
        "Sharp focus on subject, news magazine quality, high resolution. "
        "Create a full-bleed photographic scene only. Never create a webpage, news article screenshot, report-page "
        "layout, presentation, infographic, poster, headline layout, caption bar, white text panel, or floating text "
        "area. "
        f"{object_text_instruction}"
        f"{text_instruction}"
        f"{SAFE_COMPOSITION_PROMPT}"
        f"{logo_instruction}"
        "No people and no faces."
    )


def generate_featured_image(
    image_prompt,
    tags=None,
    logo_brand=None,
    logo_domain=None,
    article_title=None,
    article_content=None,
):
    """タイトルと本文を確認してから、Gemini / Imagen でアイキャッチを生成する。"""
    import os
    from google import genai
    from google.genai import types

    api_key = os.environ["GOOGLE_API_KEY"]
    base_prompt = image_prompt or "gold bitcoin coins stacked on dark surface, dramatic side lighting"
    full_prompt = _build_imagen_prompt(
        base_prompt, logo_brand, logo_domain, article_title, article_content
    )

    client = genai.Client(api_key=api_key)
    image_models = [
        ("imagen-4.0-fast-generate-001", "imagen"),
        ("imagen-4.0-generate-001", "imagen"),
        ("gemini-2.5-flash-image", "gemini"),
        ("gemini-3.1-flash-image", "gemini"),
    ]

    raw_bytes = None
    trusted_brand = _trusted_project_logo(logo_brand, logo_domain)
    text_rejections = 0
    max_text_rejections = 3
    for model_name, model_type in image_models:
        while text_rejections < max_text_rejections:
            candidate_bytes = None
            try:
                logger.info("アイキャッチ画像を生成中（%s）...", model_name)
                if model_type == "imagen":
                    response = client.models.generate_images(
                        model=model_name,
                        prompt=full_prompt,
                        config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio="16:9"),
                    )
                    candidate_bytes = response.generated_images[0].image.image_bytes
                else:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=full_prompt,
                        config=types.GenerateContentConfig(
                            response_modalities=["IMAGE"],
                            image_config=types.ImageConfig(aspect_ratio="16:9"),
                        ),
                    )
                    for part in response.candidates[0].content.parts:
                        if part.inline_data is not None:
                            candidate_bytes = part.inline_data.data
                if not candidate_bytes:
                    raise ValueError("画像データが返りませんでした")
                passed, review = _review_generated_image(
                    client, candidate_bytes, trusted_brand, base_prompt
                )
                if passed:
                    logger.info("画像内文字品質検査に合格（文字化け・疑似文字なし）")
                    raw_bytes = candidate_bytes
                    break
                text_rejections += 1
                logger.warning(
                    "画像内に文字化け・不正文字を検出したため再生成（%d/%d）: %s",
                    text_rejections,
                    max_text_rejections,
                    review,
                )
                full_prompt += (
                    " A previous attempt was rejected for corrupted or unverifiable writing. Remove all nonessential "
                    "copy. If the visual brief requires text on an object, keep only one to three exact, short, "
                    "correctly spelled Japanese or English terms; never invent paragraph copy or pseudo-text."
                )
            except ImageReviewUnavailableError:
                raise
            except Exception as e:
                logger.warning("%s 失敗: %s", model_name, e)
                break
        if raw_bytes or text_rejections >= max_text_rejections:
            break

    if not raw_bytes:
        if text_rejections >= max_text_rejections:
            raise ValueError("文字化け・疑似文字のないアイキャッチを生成できませんでした")
        raise ValueError("利用可能な画像生成モデルが見つかりません")

    image_data = fit_image_to_jpeg(raw_bytes, width=1200, height=630, quality=92)
    logger.info("縦横比を維持して1200×630に中央トリミング完了")

    return image_data


def get_seo_article_type() -> str:
    """日付と実行回数（時間帯）でSEO記事カテゴリをローテーションする"""
    import datetime
    now = datetime.datetime.now()
    day_idx = now.timetuple().tm_yday
    slot = 0 if now.hour < 15 else 1  # 朝=0、夕=1
    return SEO_ARTICLE_TYPES[(day_idx * 2 + slot) % len(SEO_ARTICLE_TYPES)]


def generate_seo_article(article_type: str) -> dict:
    """SEO強化記事を低コストOpenAIモデルで生成する。"""
    if article_type == "基礎知識":
        return _generate_kiso_article()
    return _generate_rich_article(article_type)


def _call_llm(prompt: str, max_tokens: int = 8192) -> str:
    """OpenAIを呼び出してテキストを返し、コードブロックマーカーを除去する。"""
    text = generate_text(prompt, max_output_tokens=max_tokens)
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
    return generate_json(
        prompt,
        schema_name="seo_metadata",
        schema=SEO_METADATA_SCHEMA,
        max_output_tokens=1024,
    )


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
    html_content = _call_llm(html_prompt, max_tokens=8192)

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
    html_content = _call_llm(html_prompt, max_tokens=8192)

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
