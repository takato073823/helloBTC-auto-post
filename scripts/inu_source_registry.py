"""INUが毎時投稿で優先的に探索する固定の発見・一次情報リスト。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = SCRIPT_DIR / "inu_fixed_sources.json"
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


@dataclass(frozen=True)
class FixedSource:
    name: str
    url: str
    kind: str


def _https_url(value: object) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"固定情報源URLが不正です: {url}")
    return url


@lru_cache(maxsize=1)
def load_registry(path: Path = REGISTRY_PATH) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"固定情報源リストを読み込めません: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("topics"), dict):
        raise ValueError("固定情報源リストのtopicsが不正です")
    for topic, sources in raw["topics"].items():
        if not isinstance(topic, str) or not isinstance(sources, list) or not sources:
            raise ValueError(f"固定情報源リストのカテゴリーが不正です: {topic}")
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError(f"固定情報源の形式が不正です: {topic}")
            if not str(source.get("name", "")).strip() or not str(source.get("kind", "")).strip():
                raise ValueError(f"固定情報源の名称または種別が不正です: {topic}")
            _https_url(source.get("url"))
    discovery = raw.get("discovery_only", {})
    if not isinstance(discovery, dict):
        raise ValueError("discovery_onlyの形式が不正です")
    for source in discovery.get("media", []):
        if not isinstance(source, dict) or not str(source.get("name", "")).strip():
            raise ValueError("発見専用メディアの形式が不正です")
        _https_url(source.get("url"))
    for account in discovery.get("x_accounts", []):
        if not isinstance(account, dict) or not _HANDLE_RE.fullmatch(str(account.get("handle", ""))):
            raise ValueError("発見専用Xアカウントの形式が不正です")
    return raw


def topic_sources(topic_type: str) -> tuple[FixedSource, ...]:
    registry = load_registry()
    raw_sources = registry["topics"].get(topic_type, [])
    return tuple(
        FixedSource(
            name=str(source["name"]).strip(),
            url=_https_url(source["url"]),
            kind=str(source["kind"]).strip(),
        )
        for source in raw_sources
    )


def discovery_sources() -> tuple[tuple[str, str], ...]:
    registry = load_registry()
    media = registry.get("discovery_only", {}).get("media", [])
    return tuple((str(row["name"]).strip(), _https_url(row["url"])) for row in media)


def discovery_x_handles() -> tuple[str, ...]:
    registry = load_registry()
    rows = registry.get("discovery_only", {}).get("x_accounts", [])
    return tuple(str(row["handle"]).strip() for row in rows)


def topic_source_context(topic_type: str | None = None) -> str:
    """検索プロンプトに渡す、固定一次情報源の簡潔な一覧。"""
    registry = load_registry()
    topics = [topic_type] if topic_type else list(registry["topics"])
    lines: list[str] = []
    for topic in topics:
        sources = topic_sources(topic)
        if not sources:
            continue
        lines.append(f"[{topic}]")
        lines.extend(f"- {source.name}: {source.url} ({source.kind})" for source in sources)
    return "\n".join(lines)
