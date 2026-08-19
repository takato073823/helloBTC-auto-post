#!/usr/bin/env python3
"""HermesのX検索を、読み取り専用の発見シグナルへ変換する橋渡し。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PACKET_PATH = SCRIPT_DIR / "inu_hermes_research_packet.json"
MAX_SIGNALS = 12


def _save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _extract_json(value: str) -> dict:
    text = value.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Hermesの返却値がJSONオブジェクトではありません")
    return parsed


def _is_x_post_url(value: str) -> bool:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return host in {"x.com", "twitter.com"} and bool(re.search(r"/status/\d+", parsed.path))


def validate_packet(payload: dict, *, now: dt.datetime) -> dict:
    if payload.get("degraded") is not False:
        raise ValueError("Hermes X検索がdegradedのため採用しません")
    signals: list[dict] = []
    for row in payload.get("signals", [])[:MAX_SIGNALS]:
        if not isinstance(row, dict) or not _is_x_post_url(str(row.get("post_url", ""))):
            continue
        citations = [
            str(url).strip()
            for url in row.get("citations", [])
            if str(url).startswith("https://")
        ]
        # X検索は発見専用。引用ゼロの要約や出典不明の話題は次段へ渡さない。
        if not citations:
            continue
        try:
            posted_at = dt.datetime.fromisoformat(str(row.get("posted_at", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if posted_at.tzinfo is None:
            continue
        age = now.astimezone(dt.timezone.utc) - posted_at.astimezone(dt.timezone.utc)
        if age < dt.timedelta(minutes=-15) or age > dt.timedelta(hours=24):
            continue
        signals.append(
            {
                "post_url": str(row["post_url"]),
                "handle": str(row.get("handle", "")).lstrip("@")[:40],
                "posted_at": posted_at.isoformat(),
                "headline": str(row.get("headline", ""))[:180],
                "summary": str(row.get("summary", ""))[:500],
                "why_trending": str(row.get("why_trending", ""))[:300],
                "topic": str(row.get("topic", ""))[:80],
                "citations": citations[:5],
            }
        )
    return {
        "version": 1,
        "status": "ready" if signals else "no_cited_signal",
        "generated_at": now.astimezone(dt.timezone.utc).isoformat(),
        "credential_source": str(payload.get("credential_source", "xai-oauth")),
        "degraded": False,
        "signals": signals,
    }


def build_prompt(now: dt.datetime) -> str:
    return f"""
あなたはINUの読み取り専用Xリサーチ部です。現在時刻は{now.isoformat()}です。
x_searchだけを使い、過去24時間の暗号資産・ETF・オンチェーン・規制・金融政策・
米国株・日本株・AIについて、反応が伸びている公開投稿を最大12件探してください。
この処理では投稿、返信、いいね、フォローを絶対に行いません。

返答は説明やMarkdownを付けず、次のJSONだけにしてください。
{{"degraded":false,"credential_source":"xai-oauth","signals":[{{
"post_url":"https://x.com/.../status/123","handle":"...",
"posted_at":"タイムゾーン付きISO 8601","headline":"...","summary":"...",
"why_trending":"反応数または急増理由","topic":"...",
"citations":["https://x.com/.../status/123"]}}]}}

必須条件:
- 実在するX投稿URLと引用を返す。引用ゼロ、検索劣化、日時不明はsignalsへ入れない。
- 噂・広告・価格断定・売買推奨は除外する。
- X投稿は発見専用。最終的な投稿候補は後段が公式発表・一次データへ戻って確認する。
""".strip()


def refresh_packet(
    *,
    output_path: Path = PACKET_PATH,
    now: dt.datetime | None = None,
    executable: str | None = None,
    timeout_seconds: int = 150,
) -> dict:
    moment = now or dt.datetime.now(dt.timezone.utc)
    command = executable or os.environ.get("INU_HERMES_RESEARCH_COMMAND", "inuresearch")
    resolved = shutil.which(command) if not Path(command).is_absolute() else command
    if not resolved or not Path(resolved).exists():
        payload = {
            "version": 1,
            "status": "blocked_external",
            "generated_at": moment.isoformat(),
            "reason": "Hermesのinuresearch profileが見つかりません",
            "signals": [],
        }
        _save(output_path, payload)
        return payload
    usage_path = output_path.with_suffix(".usage.json")
    process = subprocess.run(
        [
            str(resolved),
            "--in",
            str(REPO_ROOT),
            "-t",
            "x_search",
            "-z",
            build_prompt(moment),
            "--usage-file",
            str(usage_path),
        ],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if process.returncode != 0:
        combined = f"{process.stdout}\n{process.stderr}".strip()
        quota = any(marker in combined.lower() for marker in ("402", "credits", "spending-limit", "subscription"))
        payload = {
            "version": 1,
            "status": "blocked_external" if quota else "failed",
            "generated_at": moment.isoformat(),
            "reason": ("xAI/Grokの利用枠不足" if quota else "Hermes X検索が失敗")[:200],
            "signals": [],
        }
        _save(output_path, payload)
        return payload
    try:
        payload = validate_packet(_extract_json(process.stdout), now=moment)
    except (ValueError, json.JSONDecodeError) as exc:
        payload = {
            "version": 1,
            "status": "failed",
            "generated_at": moment.isoformat(),
            "reason": str(exc)[:200],
            "signals": [],
        }
    _save(output_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="HermesのX発見シグナルを更新")
    parser.add_argument("--output", default=str(PACKET_PATH))
    args = parser.parse_args()
    packet = refresh_packet(output_path=Path(args.output))
    print(json.dumps({"status": packet["status"], "signals": len(packet.get("signals", []))}, ensure_ascii=False))
    return 0 if packet["status"] in {"ready", "no_cited_signal", "blocked_external"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
