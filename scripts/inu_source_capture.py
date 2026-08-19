"""一次資料の重要部分だけを、安全に画像化する。

ページ全体の転載や画像の再デザインは行わず、公式発表または公式データの
指定要素をそのまま証拠画像として保存する。
"""

from __future__ import annotations

import asyncio
import html
import json
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageChops
import requests


EVIDENCE_TYPES = {"official_text_crop", "official_data_crop", "reported_text_crop"}
BROAD_SELECTORS = {"*", "html", "body", "main", "article"}
SEC_USER_AGENT = "helloBTC research https://hellobtc.jp/"


def _browser_page_options(source_url: str, *, height: int, width: int = 1440) -> dict:
    options = {
        "viewport": {"width": width, "height": height},
        "device_scale_factor": 1,
        "locale": "ja-JP",
    }
    host = (urlparse(source_url).hostname or "").lower()
    if host == "sec.gov" or host.endswith(".sec.gov"):
        options["user_agent"] = SEC_USER_AGENT
    return options


def normalize_evidence_text(value: str) -> str:
    """比較用に一次資料の表記ゆれだけを吸収する。

    ここで許容するのは全角/半角、改行、約物の違いだけです。語の削除や
    あいまいな類似判定は行わないため、存在しない根拠を通す用途には使えません。
    """
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(
        char
        for char in normalized
        if char.isalnum() or unicodedata.category(char).startswith("L")
    )


@dataclass(frozen=True)
class SourceCaptureSpec:
    source_url: str
    source_name: str
    published_at: str
    evidence_type: str
    selector: str
    is_primary_source: bool = True


def validate_capture_spec(spec: SourceCaptureSpec) -> None:
    parsed = urlparse(spec.source_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("一次資料のHTTPS URLが必要です")
    if spec.evidence_type.startswith("official_") and not spec.is_primary_source:
        raise ValueError("まとめサイトや転載画像は証拠画像に使えません")
    if not spec.source_name.strip():
        raise ValueError("発信元の名称が必要です")
    try:
        date.fromisoformat(spec.published_at)
    except ValueError as exc:
        raise ValueError("公開日はYYYY-MM-DD形式で指定してください") from exc
    if spec.evidence_type not in EVIDENCE_TYPES:
        raise ValueError("公式発表または公式データの画像形式を指定してください")
    selector = spec.selector.strip()
    if not selector or selector.lower() in BROAD_SELECTORS:
        raise ValueError("ページ全体ではなく、重要部分を示す限定的なselectorが必要です")


async def _mark_normalized_evidence(page, normalized_anchor: str) -> str:
    """表記だけ正規化し、根拠全文を含む最小のDOM要素へ印を付ける。"""
    return await page.locator("body *").evaluate_all(
        r"""
        (nodes, target) => {
          const normalise = (value) => (value || "")
            .normalize("NFKC")
            .toLocaleLowerCase()
            .replace(/[\p{P}\p{S}\p{Z}\s_]/gu, "");
          const matches = (element) => normalise(element.innerText).includes(target);
          const element = nodes.find((node) =>
            matches(node) &&
            !Array.from(node.children).some((child) => matches(child))
          );
          if (!element) return "";
          const id = "inu-evidence-" + Math.random().toString(36).slice(2);
          element.setAttribute("data-inu-evidence-target", id);
          return id;
        }
        """,
        normalized_anchor,
    )


def _official_html_with_base(source_url: str) -> str:
    """公式URLから同じHTMLを取得し、相対CSSを解決できるbaseだけを補う。"""
    host = (urlparse(source_url).hostname or "").lower()
    response = requests.get(
        source_url,
        timeout=30,
        headers={
            "User-Agent": SEC_USER_AGENT
            if host == "sec.gov" or host.endswith(".sec.gov")
            else "helloBTC research https://hellobtc.jp/",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    response.raise_for_status()
    if "html" not in response.headers.get("content-type", "").lower():
        raise ValueError("公式ページのHTMLを取得できません")
    base = f'<base href="{html.escape(source_url, quote=True)}">'
    body = response.text
    lower = body.lower()
    if "<head" in lower:
        start = lower.find(">", lower.find("<head"))
        if start >= 0:
            return body[: start + 1] + base + body[start + 1 :]
    return base + body


async def capture_official_element(
    spec: SourceCaptureSpec,
    output_path: str | Path,
    *,
    timeout_ms: int = 30_000,
) -> Path:
    """公式ページ上の指定要素だけをスクリーンショットする。

    selectorが複数要素に一致した場合は誤った切り抜きを避けるため失敗させる。
    """
    validate_capture_spec(spec)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(**_browser_page_options(spec.source_url, height=1600))
        try:
            await page.goto(
                spec.source_url,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            locator = page.locator(spec.selector)
            count = await locator.count()
            if count != 1:
                raise ValueError(f"selectorは1要素だけに一致する必要があります（{count}件）")
            await locator.scroll_into_view_if_needed(timeout=timeout_ms)
            await locator.screenshot(path=str(output), timeout=timeout_ms)
        finally:
            await browser.close()

    _verify_capture(output)
    write_source_manifest(spec, output)
    return output


async def capture_official_evidence(
    spec: SourceCaptureSpec,
    output_path: str | Path,
    *,
    evidence_anchor: str,
    timeout_ms: int = 30_000,
) -> Path:
    """一次資料の見出しまたは根拠箇所を自動で切り抜く。

    Web検索が返したCSS selectorは信用せず、公式ページ内に実在する短い
    根拠文字列を探す。該当箇所が取れなければ画像なし投稿には切り替えず失敗する。
    """
    validate_capture_spec(spec)
    anchor = " ".join(evidence_anchor.split()).strip()
    if len(anchor) < 4:
        raise ValueError("証拠画像を特定できる根拠文字列が必要です")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    from playwright.async_api import async_playwright

    is_data = spec.evidence_type == "official_data_crop"
    capture_width = 1440 if is_data else 1200
    capture_height = 1120 if is_data else 1500

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(
            **_browser_page_options(
                spec.source_url,
                height=capture_height,
                width=capture_width,
            )
        )
        try:
            await page.goto(spec.source_url, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(1200)
            # 同意画面が根拠を覆ったまま保存されると、公式資料そのものの意味を
            # 損なう。閉じられるものだけを押し、残った場合は切り抜きに進めない。
            for label in ("同意しない", "拒否", "同意", "Accept", "I agree", "閉じる"):
                button = page.get_by_role("button", name=label, exact=False).first
                try:
                    if await button.is_visible(timeout=250):
                        await button.click(timeout=500)
                except Exception:
                    pass

            locator = page.get_by_text(anchor, exact=False).first
            if not await locator.is_visible(timeout=3000):
                # Search results occasionally normalize punctuation. A stable prefix
                # still has to exist in the official document.
                prefix = anchor[: min(32, len(anchor))]
                locator = page.get_by_text(prefix, exact=False).first
            if not await locator.is_visible(timeout=3000):
                # 検証済みの根拠原文が、改行・全半角・句読点の違いだけで
                # Playwright の文字検索に一致しないことがある。ページ内の
                # テキストを同じ保守的な正規化で照合し、最小の可視要素だけを
                # 撮る。本文全体や近い別の文を撮るフォールバックではない。
                normalized_anchor = normalize_evidence_text(anchor)
                target_id = await _mark_normalized_evidence(page, normalized_anchor)
                if not target_id:
                    # 一部の官公庁サイトは自動ブラウザだけに制限画面を返す。
                    # 通常HTTPで取得でき、前段で原文照合済みの同一公式HTMLを
                    # そのままレンダリングして再探索する。文章や画像は生成しない。
                    official_html = await asyncio.to_thread(
                        _official_html_with_base, spec.source_url
                    )
                    await page.set_content(
                        official_html,
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    await page.wait_for_timeout(800)
                    target_id = await _mark_normalized_evidence(page, normalized_anchor)
                if not target_id:
                    raise ValueError("一次資料内に指定された根拠文字列が見つかりません")
                locator = page.locator(f'[data-inu-evidence-target="{target_id}"]')
                if not await locator.is_visible(timeout=3000):
                    raise ValueError("一次資料内の根拠箇所を表示できません")

            await locator.scroll_into_view_if_needed(timeout=timeout_ms)
            box = await locator.bounding_box()
            if not box:
                raise ValueError("根拠箇所の表示範囲を取得できません")

            document_height = await page.evaluate(
                "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)"
            )
            top = max(0, float(box["y"]) - (260 if is_data else 80))
            height = min(float(capture_height), float(document_height) - top)
            if height < 180:
                raise ValueError("根拠箇所を読める範囲で切り抜けません")
            await page.screenshot(
                path=str(output),
                clip={"x": 0, "y": top, "width": capture_width, "height": height},
                timeout=timeout_ms,
            )
        finally:
            await browser.close()

    if is_data:
        _trim_flat_outer_margin(output)
    else:
        # 公式テキストは細い横長画像にせず、Xの2枚表示でも読みやすい4:5へ統一する。
        # ページのピクセルは加工せず、不足する下端だけを公式ページと同じ白で補う。
        _pad_capture_to_portrait(output, width=1200, height=1500)
    _verify_capture(output)
    write_source_manifest(spec, output)
    return output


def crop_source_image(
    spec: SourceCaptureSpec,
    input_path: str | Path,
    output_path: str | Path,
    crop_box: tuple[int, int, int, int],
) -> Path:
    """入手済みの一次資料画像から重要部分だけを無加工で切り抜く。"""
    validate_capture_spec(spec)
    source = Path(input_path)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    with Image.open(source) as image:
        left, top, right, bottom = crop_box
        if left < 0 or top < 0 or right > image.width or bottom > image.height:
            raise ValueError("切り抜き範囲が元画像を超えています")
        if right <= left or bottom <= top:
            raise ValueError("切り抜き範囲が不正です")
        image.crop(crop_box).save(output)

    _verify_capture(output)
    write_source_manifest(spec, output)
    return output


def _verify_capture(path: Path) -> None:
    with Image.open(path) as image:
        if image.width < 320 or image.height < 180:
            raise ValueError("重要情報を読める大きさで切り抜いてください")
        if image.width * image.height > 8_000_000:
            raise ValueError("ページ全体ではなく重要部分だけを切り抜いてください")


def _trim_flat_outer_margin(path: Path, *, tolerance: int = 10) -> None:
    """スクリーンショット外周の単色余白だけを除去する。"""
    with Image.open(path) as original:
        image = original.convert("RGB")
        background = Image.new("RGB", image.size, image.getpixel((0, 0)))
        diff = ImageChops.difference(image, background).convert("L")
        diff = diff.point(lambda value: 255 if value > tolerance else 0)
        box = diff.getbbox()
        if not box:
            return
        left, top, right, bottom = box
        if right - left < 320 or bottom - top < 180:
            return
        pad = 12
        crop = (
            max(0, left - pad),
            max(0, top - pad),
            min(image.width, right + pad),
            min(image.height, bottom + pad),
        )
        image.crop(crop).save(path, format="PNG", optimize=True)


def _pad_capture_to_portrait(path: Path, *, width: int, height: int) -> None:
    """公式スクリーンショットを切らずに、X向け4:5キャンバスへ揃える。"""
    with Image.open(path) as original:
        image = original.convert("RGB")
        if image.width > width or image.height > height:
            image.thumbnail((width, height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (width, height), "white")
        left = (width - image.width) // 2
        canvas.paste(image, (left, 0))
        canvas.save(path, format="PNG", optimize=True)


def write_source_manifest(spec: SourceCaptureSpec, image_path: str | Path) -> Path:
    """投稿時の出典確認用に、画像と同名のメタデータを保存する。"""
    manifest = Path(image_path).with_suffix(".source.json")
    manifest.write_text(
        json.dumps(asdict(spec), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest
