"""xAI Responses APIを使い、X上の最新シグナルを構造化して取得する。"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

from openai import OpenAI


logger = logging.getLogger(__name__)
DEFAULT_MODEL = "grok-4.3"
XAI_BASE_URL = "https://api.x.ai/v1"


@lru_cache(maxsize=1)
def _get_client() -> OpenAI:
    """xAI用クライアントを遅延生成し、未設定時も既存経路を壊さない。"""
    api_key = os.environ.get("XAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("XAI_API_KEY が設定されていません")
    return OpenAI(
        api_key=api_key,
        base_url=XAI_BASE_URL,
        max_retries=1,
        timeout=180.0,
    )


def _extract_text(response: Any) -> str:
    status = getattr(response, "status", None)
    if status == "incomplete":
        details = getattr(response, "incomplete_details", None)
        raise RuntimeError(f"Grokの出力が途中で終了しました: {details}")
    text = (getattr(response, "output_text", "") or "").strip()
    if not text:
        raise RuntimeError("Grokから構造化出力が返りませんでした")
    return text


def _status_id(url: str) -> str:
    match = re.search(r"/(?:status|statuses)/(\d+)(?:[/?#]|$)", url)
    return match.group(1) if match else ""


def _is_x_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    return host in {"x.com", "twitter.com", "mobile.twitter.com"}


def _citation_urls(response: Any, dumped: dict[str, Any]) -> list[str]:
    """モデル本文ではなく、APIが返したcitation欄だけからURLを抽出する。"""
    raw_citations: list[Any] = []
    raw_citations.extend(dumped.get("citations", []) or [])
    raw_citations.extend(getattr(response, "citations", []) or [])
    for item in dumped.get("output", []) or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content", []) or []:
            for annotation in content.get("annotations", []) or []:
                if annotation.get("type") in {"url_citation", "citation"}:
                    raw_citations.append(annotation)

    urls: list[str] = []
    for citation in raw_citations:
        if isinstance(citation, str):
            url = citation.strip()
        elif isinstance(citation, dict):
            url = str(citation.get("url", "")).strip()
        else:
            url = str(getattr(citation, "url", "")).strip()
        if url.startswith(("https://", "http://")) and url not in urls:
            urls.append(url)
    return urls


def generate_x_json(
    prompt: str,
    *,
    schema_name: str,
    schema: dict[str, Any],
    from_date: dt.date,
    to_date: dt.date,
    max_output_tokens: int = 3000,
    model: str | None = None,
    request_timeout_seconds: float = 55.0,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """X Searchを必須実行し、引用で確認できたX投稿だけを返す。"""
    if not 10.0 <= request_timeout_seconds <= 180.0:
        raise ValueError("X Searchのタイムアウトは10〜180秒で指定してください")
    selected_model = model or os.environ.get("XAI_RESEARCH_MODEL", DEFAULT_MODEL)
    logger.info("GrokのX検索で速報シグナルを調査中（%s）...", selected_model)
    client = _get_client()
    with_options = getattr(client, "with_options", None)
    if callable(with_options):
        client = with_options(timeout=request_timeout_seconds, max_retries=0)
    response = client.responses.create(
        model=selected_model,
        input=prompt,
        tools=[
            {
                "type": "x_search",
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
            }
        ],
        tool_choice="required",
        include=["no_inline_citations"],
        max_output_tokens=max_output_tokens,
        text={
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "strict": True,
                "schema": schema,
            }
        },
    )

    dumped = response.model_dump() if hasattr(response, "model_dump") else {}
    cited_urls = [url for url in _citation_urls(response, dumped) if _is_x_url(url)]
    cited_ids = {_status_id(url) for url in cited_urls if _status_id(url)}
    if not cited_ids:
        raise RuntimeError("GrokのX Searchから投稿引用が返りませんでした")
    # xAIのResponses APIは、モデル・SDKの組み合わせによってx_search_callを
    # output配列に展開しない場合がある。今回のリクエストで使えるツールは
    # x_searchだけなので、APIが返したX status citationも検索実行の証跡になる。
    searched = any(item.get("type") == "x_search_call" for item in dumped.get("output", []))
    if not searched:
        logger.info("X Searchの実行記録はcitationから確認しました")

    payload = json.loads(_extract_text(response))
    signals = []
    for signal in payload.get("signals", []):
        if not isinstance(signal, dict):
            continue
        post_url = str(signal.get("post_url", "")).strip()
        if _is_x_url(post_url) and _status_id(post_url) in cited_ids:
            signals.append(signal)
        else:
            logger.warning("Grokの引用一覧にないX投稿を除外: %s", post_url)
    payload["signals"] = signals

    usage = dumped.get("usage", {}) or {}
    ticks = usage.get("cost_in_usd_ticks")
    if ticks is not None:
        logger.info("Grok推定API費用: %.6f USD", float(ticks) / 10_000_000_000)

    sources = [{"url": url, "title": "X post"} for url in cited_urls]
    return payload, sources
