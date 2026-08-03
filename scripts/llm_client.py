"""Low-cost OpenAI Responses API helpers for article generation."""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

from openai import OpenAI


logger = logging.getLogger(__name__)

# Luna is the cost-sensitive, high-volume member of the GPT-5.6 family.
DEFAULT_MODEL = "gpt-5.6-luna"


@lru_cache(maxsize=1)
def _get_client() -> OpenAI:
    """Create the API client lazily so imports and offline tests need no key."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY が設定されていません")
    return OpenAI(api_key=api_key, max_retries=2, timeout=180.0)


def _model_name(model: str | None = None) -> str:
    return model or os.environ.get("OPENAI_TEXT_MODEL", DEFAULT_MODEL)


def _extract_text(response: Any) -> str:
    status = getattr(response, "status", None)
    if status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        raise RuntimeError(f"OpenAIの出力が途中で終了しました: {details}")

    text = (getattr(response, "output_text", "") or "").strip()
    if not text:
        raise RuntimeError("OpenAIからテキスト出力が返りませんでした")
    return text


def generate_text(
    prompt: str,
    *,
    max_output_tokens: int = 8192,
    model: str | None = None,
) -> str:
    """Generate plain text/HTML with minimal reasoning cost."""
    selected_model = _model_name(model)
    logger.info("OpenAIで生成中（%s）...", selected_model)
    response = _get_client().responses.create(
        model=selected_model,
        input=prompt,
        max_output_tokens=max_output_tokens,
        reasoning={"effort": "none"},
    )
    return _extract_text(response)


def generate_json(
    prompt: str,
    *,
    schema_name: str,
    schema: dict[str, Any],
    max_output_tokens: int = 8192,
    model: str | None = None,
) -> dict[str, Any]:
    """Generate schema-constrained JSON through Structured Outputs."""
    selected_model = _model_name(model)
    logger.info("OpenAIで構造化データを生成中（%s）...", selected_model)
    response = _get_client().responses.create(
        model=selected_model,
        input=prompt,
        max_output_tokens=max_output_tokens,
        reasoning={"effort": "none"},
        text={
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
    )
    return json.loads(_extract_text(response))
