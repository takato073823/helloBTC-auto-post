"""一次資料の重要部分だけを、安全に画像化する。

ページ全体の転載や画像の再デザインは行わず、公式発表または公式データの
指定要素をそのまま証拠画像として保存する。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image


EVIDENCE_TYPES = {"official_text_crop", "official_data_crop"}
BROAD_SELECTORS = {"*", "html", "body", "main", "article"}


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
    if not spec.is_primary_source:
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
        page = await browser.new_page(
            viewport={"width": 1440, "height": 1600},
            device_scale_factor=1,
            locale="ja-JP",
        )
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


def write_source_manifest(spec: SourceCaptureSpec, image_path: str | Path) -> Path:
    """投稿時の出典確認用に、画像と同名のメタデータを保存する。"""
    manifest = Path(image_path).with_suffix(".source.json")
    manifest.write_text(
        json.dumps(asdict(spec), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest
