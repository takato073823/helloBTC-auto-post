"""OpenAI Image APIでINUのオリジナル投稿画像を生成する。"""

from __future__ import annotations

import base64
import io
import logging
import os
from functools import lru_cache
from pathlib import Path

from openai import OpenAI
from PIL import Image


DEFAULT_PROVIDER = "grok"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_GROK_MODEL = "grok-imagine-image-quality"
DEFAULT_SIZE = "1024x1280"
DEFAULT_QUALITY = "medium"
GROK_ASPECT_RATIO = "3:4"
GROK_RESOLUTION = "1k"
XAI_BASE_URL = "https://api.x.ai/v1"


logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEYが設定されていません")
    return OpenAI(api_key=api_key, max_retries=2, timeout=180.0)


@lru_cache(maxsize=1)
def _grok_client() -> OpenAI:
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("XAI_API_KEYが設定されていません")
    return OpenAI(api_key=api_key, base_url=XAI_BASE_URL, max_retries=1, timeout=180.0)


def _write_png(image_bytes: bytes, output_path: str | Path) -> Path:
    """生成元のJPEG/PNGを、X投稿の検証条件に合わせてPNGへ正規化する。"""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(io.BytesIO(image_bytes)) as image:
        image.convert("RGB").save(path, format="PNG", optimize=True)
    return path


def _generate_openai_image(
    prompt: str,
    *,
    model: str | None,
    size: str | None,
    quality: str | None,
) -> bytes:
    result = _client().images.generate(
        model=model or os.environ.get("INU_OPENAI_IMAGE_MODEL", DEFAULT_MODEL),
        prompt=prompt,
        size=size or os.environ.get("INU_IMAGE_SIZE", DEFAULT_SIZE),
        quality=quality or os.environ.get("INU_IMAGE_QUALITY", DEFAULT_QUALITY),
        output_format="png",
        moderation="auto",
    )
    if not result.data or not result.data[0].b64_json:
        raise RuntimeError("GPT Imageから画像が返りませんでした")
    return base64.b64decode(result.data[0].b64_json)


def _grok_aspect_ratio(size: str | None) -> str:
    """既存のサイズ指定を、Grok Imagineで受け付ける近い比率へ変換する。"""
    value = (size or os.environ.get("INU_IMAGE_SIZE", DEFAULT_SIZE)).lower()
    if "x" not in value:
        return os.environ.get("INU_GROK_IMAGE_ASPECT_RATIO", GROK_ASPECT_RATIO)
    try:
        width, height = (int(part) for part in value.split("x", 1))
    except ValueError:
        return os.environ.get("INU_GROK_IMAGE_ASPECT_RATIO", GROK_ASPECT_RATIO)
    if width > height:
        return "3:2" if width / height < 1.7 else "16:9"
    if height > width:
        return "3:4" if height / width < 1.7 else "9:16"
    return "1:1"


def _generate_grok_image(prompt: str, *, model: str | None, size: str | None) -> bytes:
    """Grok Imagineの高品質画像を、縦長のX投稿用に取得する。"""
    result = _grok_client().images.generate(
        model=model or os.environ.get("INU_GROK_IMAGE_MODEL", DEFAULT_GROK_MODEL),
        prompt=prompt,
        response_format="b64_json",
        extra_body={
            "aspect_ratio": _grok_aspect_ratio(size),
            "resolution": os.environ.get("INU_GROK_IMAGE_RESOLUTION", GROK_RESOLUTION),
        },
    )
    if not result.data or not result.data[0].b64_json:
        raise RuntimeError("Grok Imagineから画像が返りませんでした")
    usage = getattr(result, "usage", None)
    ticks = getattr(usage, "cost_in_usd_ticks", None) if usage else None
    if ticks is not None:
        logger.info("Grok Imagine API費用: %.6f USD", float(ticks) / 10_000_000_000)
    return base64.b64decode(result.data[0].b64_json)


def generate_image(
    prompt: str,
    output_path: str | Path,
    *,
    model: str | None = None,
    size: str | None = None,
    quality: str | None = None,
) -> Path:
    provider = os.environ.get("INU_IMAGE_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    fallback = os.environ.get("INU_IMAGE_FALLBACK_PROVIDER", "openai").strip().lower()
    if provider not in {"grok", "openai"} or fallback not in {"grok", "openai", "none"}:
        raise ValueError("INU_IMAGE_PROVIDERはgrokまたはopenaiで指定してください")
    try:
        if provider == "grok":
            raw = _generate_grok_image(prompt, model=model, size=size)
        else:
            raw = _generate_openai_image(prompt, model=model, size=size, quality=quality)
    except Exception:
        if fallback == "none" or fallback == provider:
            raise
        logger.warning("%s画像生成に失敗したため%sへ切り替えます", provider, fallback)
        if fallback == "grok":
            raw = _generate_grok_image(prompt, model=model, size=size)
        else:
            raw = _generate_openai_image(prompt, model=model, size=size, quality=quality)
    return _write_png(raw, output_path)
