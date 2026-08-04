"""OpenAI Image APIでINUのオリジナル投稿画像を生成する。"""

from __future__ import annotations

import base64
import os
from functools import lru_cache
from pathlib import Path

from openai import OpenAI


DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "1024x1280"
DEFAULT_QUALITY = "medium"


@lru_cache(maxsize=1)
def _client() -> OpenAI:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEYが設定されていません")
    return OpenAI(api_key=api_key, max_retries=2, timeout=180.0)


def generate_image(
    prompt: str,
    output_path: str | Path,
    *,
    model: str | None = None,
    size: str | None = None,
    quality: str | None = None,
) -> Path:
    selected_model = model or os.environ.get("INU_IMAGE_MODEL", DEFAULT_MODEL)
    selected_size = size or os.environ.get("INU_IMAGE_SIZE", DEFAULT_SIZE)
    selected_quality = quality or os.environ.get("INU_IMAGE_QUALITY", DEFAULT_QUALITY)
    result = _client().images.generate(
        model=selected_model,
        prompt=prompt,
        size=selected_size,
        quality=selected_quality,
        output_format="png",
        moderation="auto",
    )
    if not result.data or not result.data[0].b64_json:
        raise RuntimeError("GPT Imageから画像が返りませんでした")
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(result.data[0].b64_json))
    return path

