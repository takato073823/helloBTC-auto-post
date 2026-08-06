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
    """X Searchが返した結果・citation欄だけからURLを抽出する。

    モデル本文やtool callのquery/argumentsは対象にしない。プロンプト中のURLを
    モデルが繰り返したものを、検索で裏付けられた投稿として扱わないためである。
    """
    raw_citations: list[Any] = []
    raw_citations.extend(dumped.get("citations", []) or [])
    raw_citations.extend(getattr(response, "citations", []) or [])

    def add_result_entries(entries: Any) -> None:
        """APIの結果用フィールドにあるURLだけを受け付ける。"""
        if isinstance(entries, (str, dict)):
            entries = [entries]
        if not isinstance(entries, (list, tuple)):
            return
        for entry in entries:
            if isinstance(entry, str):
                raw_citations.append(entry)
            elif isinstance(entry, dict):
                raw_citations.append(entry)

    for item in dumped.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", ""))
        if item_type == "message":
            content_items = item.get("content", []) or []
            if isinstance(content_items, dict):
                content_items = [content_items]
            if not isinstance(content_items, list):
                continue
            for content in content_items:
                if not isinstance(content, dict):
                    continue
                annotations = content.get("annotations", []) or []
                if isinstance(annotations, dict):
                    annotations = [annotations]
                if not isinstance(annotations, list):
                    continue
                for annotation in annotations:
                    if isinstance(annotation, dict) and annotation.get("type") in {"url_citation", "citation"}:
                        raw_citations.append(annotation)
            continue

        if not item_type.endswith("_search_call"):
            continue
        # result-bearing fields only. query / arguments / input are model-authored
        # and therefore never prove that an X post was returned by X Search.
        for field in ("results", "sources", "citations", "output"):
            add_result_entries(item.get(field))
        action = item.get("action")
        if isinstance(action, dict):
            for field in ("results", "sources", "citations", "output"):
                add_result_entries(action.get(field))

    urls: list[str] = []
    for citation in raw_citations:
        if isinstance(citation, str):
            url = citation.strip()
        elif isinstance(citation, dict):
            url = str(citation.get("url") or citation.get("link") or "").strip()
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
    """X Searchを必須実行し、API結果で確認できたX投稿だけを返す。"""
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
                # to_dateが排他的な実装でもJST当日の投稿を取りこぼさないように
                # 翌日を渡し、下流の鮮度ゲートで再度絞り込む。
                "to_date": (to_date + dt.timedelta(days=1)).isoformat(),
            }
        ],
        tool_choice="required",
        # strict JSONを壊すinline citationを抑止し、tool-call側の結果を読む。
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
        output_types = sorted(
            {str(item.get("type", "")) for item in dumped.get("output", []) or [] if isinstance(item, dict)}
        )
        logger.warning(
            "GrokのX Search結果にstatus URLがありません: output_types=%s top_level_keys=%s",
            output_types,
            sorted(dumped.keys()),
        )
        raise RuntimeError("GrokのX Searchから投稿引用が返りませんでした")
    # xAIのResponses APIは、モデル・SDKの組み合わせによってx_search_callを
    # output配列に展開しない場合がある。今回のリクエストで使えるツールは
    # x_searchだけなので、APIが返したX status citationも検索実行の証跡になる。
    searched = any(
        isinstance(item, dict) and str(item.get("type", "")).endswith("_search_call")
        for item in dumped.get("output", []) or []
    )
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

    sources = [{"url": url, "title": "X Search result"} for url in cited_urls]
    return payload, sources


def generate_editorial_json(
    prompt: str,
    *,
    schema_name: str,
    schema: dict[str, Any],
    max_output_tokens: int = 2200,
    model: str | None = None,
    request_timeout_seconds: float = 55.0,
) -> dict[str, Any]:
    """検証済みの事実だけを材料に、Grokへ編集案を作らせる。

    これはX検索・Web検索の代替ではない。呼び出し元が固定した事実を読みやすい
    投稿文へ変換する用途だけに限定し、出典URLや新しい事実は返却形式にも含めない。
    """
    if not 10.0 <= request_timeout_seconds <= 180.0:
        raise ValueError("Grok編集のタイムアウトは10〜180秒で指定してください")
    selected_model = model or os.environ.get("XAI_EDITORIAL_MODEL", DEFAULT_MODEL)
    logger.info("Grokで検証済み事実の編集案を作成中（%s）...", selected_model)
    client = _get_client()
    with_options = getattr(client, "with_options", None)
    if callable(with_options):
        client = with_options(timeout=request_timeout_seconds, max_retries=0)
    response = client.responses.create(
        model=selected_model,
        input=prompt,
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
    payload = json.loads(_extract_text(response))
    dumped = response.model_dump() if hasattr(response, "model_dump") else {}
    usage = dumped.get("usage", {}) or {}
    ticks = usage.get("cost_in_usd_ticks")
    if ticks is not None:
        logger.info("Grok編集API費用: %.6f USD", float(ticks) / 10_000_000_000)
    return payload
