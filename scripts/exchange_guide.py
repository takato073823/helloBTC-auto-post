#!/usr/bin/env python3
"""
取引所登録ガイド記事の完全自動生成
  1. Playwright   → 公開ページのスクリーンショット
  2. Imagen       → KYC / 入金 / セキュリティの概念イメージ
  3. Claude Haiku → 記事テキスト生成（画像プレースホルダー付き）
  4. WordPress    → 画像アップロード → 記事投稿
  5. X            → 自動ツイート

使い方: python exchange_guide.py [bingx|bybit|binance|okx]
"""

import asyncio
import io
import json
import logging
import os
import re
import sys
from pathlib import Path

import anthropic
import requests
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

# ---------------------------------------------------------------------------
# 取引所設定（新しい取引所はここに追加するだけ）
# ---------------------------------------------------------------------------
EXCHANGE_CONFIGS: dict = {
    "bingx": {
        "name": "BingX",
        "invite_code": "XXCCJX",
        "invite_url": "https://bingxdao.com/invite/XXCCJX/",
        "category": "取引所",
        "slug": "bingx-registration-guide",
        "tags": ["BingX", "仮想通貨取引所", "コピートレード", "口座開設", "先物取引"],
        "tweet_bullets": [
            "招待コードXXCCJXで手数料割引特典",
            "コピートレードで初心者でも運用可能",
            "600銘柄以上・完全日本語対応",
        ],
        # Playwright でスクリーンショットを撮る公開ページ
        "public_screenshots": [
            {
                "key": "top",
                "url": "https://bingxdao.com/invite/XXCCJX/",
                "description": "BingX招待ページ・トップ画面",
                "viewport": {"width": 1280, "height": 800},
                "wait_ms": 4000,
            },
            {
                "key": "register",
                "url": "https://bingx.com/en-us/account/register/?ref=XXCCJX",
                "description": "BingX新規登録フォーム",
                "viewport": {"width": 1280, "height": 800},
                "wait_ms": 4000,
            },
            {
                "key": "trading",
                "url": "https://bingx.com/en-us/spot/BTCUSDT/",
                "description": "BTC/USDT現物取引チャート",
                "viewport": {"width": 1440, "height": 900},
                "wait_ms": 6000,
            },
            {
                "key": "copy_trade",
                "url": "https://bingx.com/en-us/copyTrade/",
                "description": "コピートレード画面",
                "viewport": {"width": 1440, "height": 900},
                "wait_ms": 6000,
            },
        ],
        # Imagen で生成する概念イメージ（ログイン必須ページの代替）
        "imagen_screenshots": [
            {
                "key": "kyc",
                "prompt": "smartphone screen showing document identity verification scan, blue beam light, dark background, clean UI",
                "description": "本人確認（KYC）イメージ",
            },
            {
                "key": "deposit",
                "prompt": "crypto wallet QR code displayed on smartphone screen, glowing interface, dark minimalist design",
                "description": "仮想通貨入金・ウォレットイメージ",
            },
            {
                "key": "security",
                "prompt": "digital padlock glowing blue on dark background, security shield icon, cyber protection aesthetic",
                "description": "セキュリティ・2FA設定イメージ",
            },
        ],
    },
    # 他取引所はここに追加
}

# ---------------------------------------------------------------------------
# Playwright スクリーンショット
# ---------------------------------------------------------------------------

_POPUP_SELECTORS = [
    "button[id*='accept']",
    "button[class*='accept']",
    "button[class*='cookie']",
    "[aria-label='Close']",
    "button[class*='close']",
    "button[class*='dismiss']",
]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


async def _take_one_screenshot(browser, sc_cfg: dict) -> bytes | None:
    page = await browser.new_page(viewport=sc_cfg["viewport"], user_agent=_UA)
    try:
        await page.goto(sc_cfg["url"], wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(sc_cfg["wait_ms"])

        # ポップアップ・クッキー同意を閉じる
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
        logger.info(f"  ✓ screenshot: {sc_cfg['key']} ({len(raw):,} bytes)")
        return raw
    except PlaywrightTimeout:
        logger.warning(f"  ✗ timeout: {sc_cfg['key']}")
        return None
    except Exception as e:
        logger.warning(f"  ✗ error: {sc_cfg['key']} — {e}")
        return None
    finally:
        await page.close()


async def capture_public_screenshots(exchange_config: dict) -> dict[str, bytes | None]:
    results: dict[str, bytes | None] = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
        )
        for sc_cfg in exchange_config["public_screenshots"]:
            results[sc_cfg["key"]] = await _take_one_screenshot(browser, sc_cfg)
        await browser.close()
    return results


# ---------------------------------------------------------------------------
# Imagen 画像生成
# ---------------------------------------------------------------------------

def generate_imagen_image(prompt: str) -> bytes | None:
    try:
        from google import genai
        from google.genai import types

        full_prompt = (
            f"{prompt}. "
            "Photojournalism Reuters style, muted cool tones, professional lighting. "
            "No text, no people, no faces, no brand logos, no watermarks."
        )
        client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
        response = client.models.generate_images(
            model="imagen-4.0-fast-generate-001",
            prompt=full_prompt,
            config=types.GenerateImagesConfig(number_of_images=1, aspect_ratio="16:9"),
        )
        raw = response.generated_images[0].image.image_bytes
        img = Image.open(io.BytesIO(raw)).resize((1200, 675), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=90)
        logger.info(f"  ✓ imagen: {len(out.getvalue()):,} bytes")
        return out.getvalue()
    except Exception as e:
        logger.warning(f"  ✗ imagen failed: {e}")
        return None


# ---------------------------------------------------------------------------
# 画像リサイズ
# ---------------------------------------------------------------------------

def resize_to_1200x675(raw: bytes) -> bytes:
    img = Image.open(io.BytesIO(raw)).resize((1200, 675), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Claude 記事生成
# ---------------------------------------------------------------------------

def generate_article_text(exchange_config: dict, available_keys: list[str]) -> dict:
    client = anthropic.Anthropic()
    name = exchange_config["name"]
    invite_code = exchange_config["invite_code"]
    invite_url = exchange_config["invite_url"]

    img_placeholder_desc = "\n".join(
        f"- {{{{IMG_{k.upper()}}}}}" for k in available_keys
    )

    affiliate_box = (
        f'<div style="background:#fff8e1;border-left:5px solid #f7931a;'
        f'padding:16px 20px;margin:24px 0;border-radius:4px;">'
        f"<strong>{name} 招待特典</strong><br>"
        f"招待コード：<strong>{invite_code}</strong><br>"
        f'▶ <a href="{invite_url}" target="_blank" rel="nofollow noopener">'
        f"こちらから登録する（特典自動適用）</a></div>"
    )

    prompt = f"""あなたはSEOに強い仮想通貨専門ライターです。helloBTC向けに{name}の口座開設・使い方完全ガイド記事を作成してください。

【取引所情報】
- 取引所名: {name}
- 招待コード: {invite_code}
- 招待URL: {invite_url}
- 特徴: コピートレード・600銘柄以上・日本語対応・現物/先物

【使用できる画像プレースホルダー（適切な位置に1つずつ配置すること）】
{img_placeholder_desc}

【記事構成（この順番で必ず書く）】
1. リード文（招待コード特典ボックスを含む） → {{{{IMG_TOP}}}}
2. {name}の特徴3点（H3見出し3つ）
3. 口座開設手順ステップ1〜3 → {{{{IMG_REGISTER}}}}
4. 本人確認（KYC）手順 → {{{{IMG_KYC}}}}
5. 入金方法（2種類） → {{{{IMG_DEPOSIT}}}}
6. 現物取引の始め方 → {{{{IMG_TRADING}}}}
7. コピートレードの始め方 → {{{{IMG_COPY_TRADE}}}}
8. セキュリティ設定（必須項目） → {{{{IMG_SECURITY}}}}
9. まとめ（招待コード特典ボックスを再掲）

【アフィリエイトボックスHTML（リード文末とまとめに使用）】
{affiliate_box}

【情報ボックスHTML（注意事項等に使用）】
<div style="background:#e8f5e9;border-left:4px solid #4caf50;padding:14px 18px;margin:20px 0;border-radius:4px;">
ここに内容
</div>

【ルール】
- 文体：「〜した」「〜だ」「〜である」（丁寧語禁止）
- 本文1500〜2000字
- プレースホルダーは <figure>{{{{IMG_xxx}}}}</figure> 形式で配置
- 使えないキーのプレースホルダーは使わない（使えるキーリストのみ使う）
- 見出しはh3タグ
- リスク免責文を最後に <p style="font-size:0.85em;color:#888;">※ ...</p> で記載

必ず以下のJSONのみ出力（前後に余計なテキスト不要）:
{{
  "title": "SEO最適化された日本語タイトル（35〜60文字、数字や年を含む）",
  "content": "<HTML記事本文（プレースホルダー含む）>",
  "excerpt": "記事の要約（100〜150文字）"
}}"""

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end <= start:
        raise ValueError(f"JSON が見つかりません: {text[:200]}")
    return json.loads(text[start:end])


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

async def main():
    exchange_name = sys.argv[1].lower() if len(sys.argv) > 1 else "bingx"
    config = EXCHANGE_CONFIGS.get(exchange_name)
    if not config:
        logger.error(f"未対応: {exchange_name}。利用可能: {list(EXCHANGE_CONFIGS.keys())}")
        sys.exit(1)

    logger.info(f"=== {config['name']} ガイド記事生成開始 ===")

    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )

    # 1. スクリーンショット取得
    logger.info("[1/5] Playwright でスクリーンショット取得...")
    screenshots = await capture_public_screenshots(config)

    # 2. Imagen 画像生成
    logger.info("[2/5] Imagen で概念イメージを生成...")
    imagen_images: dict[str, tuple[bytes | None, str]] = {}
    for img_cfg in config["imagen_screenshots"]:
        imagen_images[img_cfg["key"]] = (
            generate_imagen_image(img_cfg["prompt"]),
            img_cfg["description"],
        )

    # 3. 全画像を WordPress にアップロード
    logger.info("[3/5] 画像を WordPress にアップロード...")
    # key -> (media_id, url, alt_text)
    image_map: dict[str, tuple[int, str, str]] = {}

    for sc_cfg in config["public_screenshots"]:
        key = sc_cfg["key"]
        raw = screenshots.get(key)
        if not raw:
            continue
        try:
            resized = resize_to_1200x675(raw)
            mid, url = wp.upload_media(resized, f"{exchange_name}-{key}.jpg")
            image_map[key] = (mid, url, sc_cfg["description"])
            logger.info(f"  アップロード完了: {key} → {url}")
        except Exception as e:
            logger.warning(f"  アップロード失敗: {key} — {e}")

    for key, (img_bytes, alt) in imagen_images.items():
        if not img_bytes:
            continue
        try:
            mid, url = wp.upload_media(img_bytes, f"{exchange_name}-{key}.jpg")
            image_map[key] = (mid, url, alt)
            logger.info(f"  アップロード完了: {key} → {url}")
        except Exception as e:
            logger.warning(f"  アップロード失敗: {key} — {e}")

    available_keys = list(image_map.keys())
    logger.info(f"  利用可能な画像: {available_keys}")

    # 4. 記事テキスト生成
    logger.info("[4/5] Claude で記事テキストを生成...")
    article = generate_article_text(config, available_keys)

    # プレースホルダーを <img> タグに置換
    content = article["content"]
    for key, (_, url, alt) in image_map.items():
        img_html = (
            f'<img src="{url}" alt="{alt}" '
            f'style="width:100%;height:auto;border-radius:6px;margin:12px 0;" '
            f'loading="lazy">'
        )
        placeholder = f"{{{{IMG_{key.upper()}}}}}"
        content = content.replace(f"<figure>{placeholder}</figure>", f"<figure>{img_html}</figure>")
        content = content.replace(placeholder, img_html)

    # 未使用プレースホルダーを削除
    content = re.sub(r"<figure>\{\{IMG_[A-Z_]+\}\}</figure>", "", content)
    content = re.sub(r"\{\{IMG_[A-Z_]+\}\}", "", content)

    # アイキャッチ画像（top → trading → copy_trade の優先順）
    featured_media_id = None
    featured_image_url = None
    for pref in ["top", "trading", "copy_trade", "register"]:
        if pref in image_map:
            featured_media_id, featured_image_url, _ = image_map[pref]
            break

    # 5. WordPress に投稿
    logger.info("[5/5] WordPress に記事を投稿...")
    category_id = wp.get_or_create_category(config["category"])

    result = wp.post_article(
        title=article["title"],
        content=content,
        excerpt=article["excerpt"],
        tags=config["tags"],
        category_id=category_id,
        featured_media_id=featured_media_id,
        status="publish",
        slug=config["slug"],
        featured_image_url=featured_image_url,
        article_section="取引所",
    )

    article_url = result.get("link", "")
    logger.info(f"=== 記事公開完了: {article_url} ===")

    # X (Twitter) 投稿
    post_tweet(
        title=article["title"],
        article_url=article_url,
        tags=config["tags"],
        tweet_bullets=config["tweet_bullets"],
        article_section="取引所",
    )


if __name__ == "__main__":
    asyncio.run(main())
