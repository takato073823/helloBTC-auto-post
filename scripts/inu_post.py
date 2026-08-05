"""INU用の自然なX投稿を検証・組み立てる。"""

from __future__ import annotations

import re

from inu_persona import lint_voice
from x_poster import _build_hashtags, _neutralize_service_domains


MAX_WEIGHTED_LENGTH = 280


def weighted_length(text: str) -> int:
    return sum(1 if ord(char) < 128 else 2 for char in text)


def compose_post(
    *,
    hook: str,
    facts: list[str],
    opinion: str,
    source_label: str,
    tags: list[str] | None = None,
) -> str:
    """見出し+三点箇条書きの固定カード形式を使わず、自然文で作る。"""
    clean_hook = _neutralize_service_domains(hook.strip())
    paragraphs = [
        _neutralize_service_domains(value.strip())
        for value in facts
        if value.strip()
    ]
    clean_opinion = _neutralize_service_domains(opinion.strip())
    clean_source = _neutralize_service_domains(source_label.strip())
    # 事実を一つのブロックにまとめる。1文ごとに改行を空けると、
    # 公式資料を機械的に抜き出しただけの印象になりやすいため。
    body = f"{clean_hook}\n\n" + "\n".join(paragraphs)
    body += f"\n\n{clean_opinion}\n\n出典：{clean_source}"
    hashtags = _build_hashtags((tags or [])[:2])
    if hashtags:
        body += f"\n\n{hashtags}"
    return body


def validate_post(text: str, *, require_opinion: bool = True) -> None:
    errors = lint_voice(text, require_opinion=require_opinion)
    if weighted_length(text) > MAX_WEIGHTED_LENGTH:
        errors.append(f"Xの文字数上限を超える: {weighted_length(text)}")
    if re.search(r"https?://|www\.", text, flags=re.IGNORECASE):
        errors.append("本文に外部URLを直書きしない")
    if len(re.findall(r"(?<!\S)#[^\s#]+", text)) > 2:
        errors.append("ハッシュタグは2件まで")
    if errors:
        raise ValueError(" / ".join(errors))
