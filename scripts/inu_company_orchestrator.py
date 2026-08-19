#!/usr/bin/env python3
"""INUの各部門を、検証済み成果物で接続する統括オーケストレーター。"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import tempfile
from pathlib import Path

import inu_auto_hourly
from inu_editorial_policy import validate_auto_post_quality
from inu_hermes_research import refresh_packet
from inu_hourly_dispatcher import load_state, save_state
from inu_live_post import validate_test_item
from inu_post import validate_post
from inu_pipeline_contracts import (
    content_fingerprint,
    event_fingerprint,
    release_reservation,
)
from x_poster import XCreditsDepletedError


SCRIPT_DIR = Path(__file__).resolve().parent
COMPANY_STATE_PATH = SCRIPT_DIR / "inu_company_state.json"
MAX_AUDIT_ROWS = 300
X_CREDIT_RETRY_HOURS = 6


def _load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        value = {}
    value.setdefault("version", 1)
    value.setdefault("runs", [])
    return value


def _save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _append_audit(path: Path, row: dict) -> None:
    state = _load(path)
    state["runs"].append(row)
    state["runs"] = state["runs"][-MAX_AUDIT_ROWS:]
    _save(path, state)


def _prepared(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _quality_handoff(prepared: dict) -> dict:
    item = dict(prepared["item"])
    candidate = dict(prepared["candidate"])
    # 一次情報から作る編集候補は編集憲法を再審査する。実測相場のfallbackは
    # 既に専用の価格・画像ゲートを通っており、同じ欄を要求すると誤拒否になる。
    if candidate.get("facts"):
        validate_auto_post_quality(candidate)
    # Xネイティブ引用はローカル画像を持たない。本文と参照IDは作成段階で
    # 検証済みなので、ここでは投稿文を再審査する。通常投稿は画像manifestまで再審査する。
    delivery_mode = str(item.get("delivery_mode", ""))
    if delivery_mode in {"x_native_video_reference", "x_native_quote"}:
        validate_post(str(item.get("text", "")))
        if not str(item.get("source_tweet_id", "")).isdigit():
            raise ValueError("Xネイティブ引用の投稿IDが不正です")
    else:
        validate_test_item(item)
    return {
        "slot": str(prepared["slot"]),
        "post_id": str(item["id"]),
        "topic_type": str(candidate["topic_type"]),
        "content_fingerprint": content_fingerprint(item.get("text", "")),
        "event_fingerprint": event_fingerprint(candidate),
    }


def prepare(args: argparse.Namespace) -> int:
    started = dt.datetime.now(dt.timezone.utc)
    hourly_state = load_state(Path(args.state))
    x_block = hourly_state.get("external_blocks", {}).get("x_api", {})
    try:
        retry_after = dt.datetime.fromisoformat(
            str(x_block.get("retry_after", "")).replace("Z", "+00:00")
        )
    except ValueError:
        retry_after = None
    if (
        retry_after is not None
        and retry_after.tzinfo is not None
        and started < retry_after.astimezone(dt.timezone.utc)
        and os.environ.get("INU_FORCE_X_DELIVERY_PROBE", "false").strip().lower()
        not in {"1", "true", "yes"}
    ):
        inu_auto_hourly._emit_output("ready", "false")
        _append_audit(
            Path(args.company_state),
            {
                "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
                "started_at": started.isoformat(),
                "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "mode": "prepare",
                "departments": {
                    "research": "paused_cost_guard",
                    "editorial": "not_started",
                    "quality": "not_started",
                    "distribution": "blocked_external",
                    "reliability": "observed",
                },
                "status": "blocked_external",
                "reason": "X_API_CREDITS_DEPLETED",
                "retry_after": retry_after.isoformat(),
            },
        )
        return 0
    hermes_status = "disabled"
    if os.environ.get("INU_HERMES_RESEARCH_ENABLED", "false").strip().lower() in {"1", "true", "yes"}:
        hermes_status = str(refresh_packet().get("status", "failed"))
    xai_status = (
        "enabled"
        if os.environ.get("INU_GROK_X_SEARCH_ENABLED", "false").strip().lower()
        in {"1", "true", "yes"}
        and bool(os.environ.get("XAI_API_KEY", "").strip())
        else "disabled"
    )
    prepared_path = Path(args.prepared)
    before_mtime = prepared_path.stat().st_mtime_ns if prepared_path.exists() else None
    exit_code = inu_auto_hourly.prepare(args)
    prepared = _prepared(prepared_path)
    changed = prepared_path.exists() and prepared_path.stat().st_mtime_ns != before_mtime
    audit = {
        "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "started_at": started.isoformat(),
        "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "mode": "prepare",
        "departments": {
            "research": "completed",
            "hermes_x_discovery": hermes_status,
            "xai_x_search": xai_status,
            "editorial": "not_started",
            "quality": "not_started",
            "distribution": "not_started",
            "reliability": "observed",
        },
        "status": "no_publishable_candidate",
    }
    if exit_code == 0 and changed and prepared:
        handoff = _quality_handoff(prepared)
        audit["departments"].update(
            {"editorial": "completed", "quality": "approved", "distribution": "reserved"}
        )
        audit.update(handoff)
        audit["status"] = "approved_outbox"
    _append_audit(Path(args.company_state), audit)
    return exit_code


def publish(args: argparse.Namespace) -> int:
    started = dt.datetime.now(dt.timezone.utc)
    prepared = _prepared(Path(args.prepared))
    if not prepared:
        raise RuntimeError("配信部が受け取る承認済みOutboxがありません")
    handoff = _quality_handoff(prepared)
    audit = {
        "run_id": os.environ.get("GITHUB_RUN_ID", "local"),
        "started_at": started.isoformat(),
        "mode": "publish",
        "departments": {"quality": "reapproved", "distribution": "publishing"},
        **handoff,
    }
    try:
        exit_code = inu_auto_hourly.publish(args)
    except Exception as exc:
        failed_at = dt.datetime.now(dt.timezone.utc)
        state_path = Path(args.state)
        state = load_state(state_path)
        state = release_reservation(
            state,
            slot=handoff["slot"],
            post_id=handoff["post_id"],
            now=dt.datetime.now(dt.timezone.utc),
            reason=str(exc),
        )
        save_state(state_path, state)
        if isinstance(exc, XCreditsDepletedError):
            state = load_state(state_path)
            state.setdefault("external_blocks", {})["x_api"] = {
                "reason": "X_API_CREDITS_DEPLETED",
                "blocked_at": failed_at.isoformat(),
                "retry_after": (
                    failed_at + dt.timedelta(hours=X_CREDIT_RETRY_HOURS)
                ).isoformat(),
            }
            save_state(state_path, state)
            audit["departments"]["distribution"] = "blocked_external"
            audit["status"] = "blocked_external"
        else:
            audit["departments"]["distribution"] = "failed_released"
            audit["status"] = "retryable_delivery_failure"
        audit["reason"] = str(exc)[:300]
        audit["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        _append_audit(Path(args.company_state), audit)
        raise
    audit["departments"]["distribution"] = "published"
    audit["status"] = "published"
    state_path = Path(args.state)
    state = load_state(state_path)
    if "x_api" in state.get("external_blocks", {}):
        state["external_blocks"].pop("x_api", None)
        save_state(state_path, state)
    audit["completed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    _append_audit(Path(args.company_state), audit)
    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = inu_auto_hourly.build_parser()
    parser.description = "INU社内パイプライン統括"
    parser.add_argument("--company-state", default=str(COMPANY_STATE_PATH))
    return parser


def run(args: argparse.Namespace) -> int:
    return prepare(args) if args.prepare else publish(args)


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
