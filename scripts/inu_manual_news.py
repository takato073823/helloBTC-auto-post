#!/usr/bin/env python3
"""チャットで指定されたニュースを、INUの画像付き個別投稿として公開する。"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
from pathlib import Path

from inu_hourly_dispatcher import load_state, save_state
from inu_live_post import publish_test_item, validate_test_item
from inu_news_visual import capture_source_hero_image, generate_editorial_news_visual
from inu_post import compose_post
from inu_source_capture import SourceCaptureSpec, capture_official_evidence


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
CATALOG_PATH = SCRIPT_DIR / "inu_manual_posts.json"
STATE_PATH = SCRIPT_DIR / "inu_manual_post_state.json"
ARTIFACT_DIR = SCRIPT_DIR / "artifacts" / "inu-manual"
PREPARED_PATH = ARTIFACT_DIR / "prepared.json"
MAX_GENERATED_EDITORIAL_VISUALS_PER_DAY = 18


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(REPO_ROOT))


def _load_post(post_id: str) -> dict:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    for item in payload.get("posts", []):
        if item.get("id") == post_id:
            return dict(item)
    raise ValueError(f"指定された個別投稿がありません: {post_id}")


def _today_generated_count(state: dict) -> int:
    today = dt.datetime.now(dt.timezone.utc).astimezone().date().isoformat()
    return sum(
        1
        for row in list(state.get("reservations", [])) + list(state.get("posted", []))
        if row.get("generated_editorial_visual") and str(row.get("prepared_at", "")).startswith(today)
    )


def _build_item(post: dict, state: dict) -> tuple[dict, bool]:
    required = {
        "id", "topic_type", "visual_route", "hook", "facts", "opinion", "source_name",
        "source_url", "published_at", "evidence_anchor", "tags", "is_primary_source",
    }
    missing = sorted(required - set(post))
    if missing:
        raise ValueError(f"個別投稿の必要情報が不足しています: {missing}")
    if not isinstance(post["facts"], list) or not post["facts"]:
        raise ValueError("個別投稿には確認済みの要点が必要です")

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    evidence_path = ARTIFACT_DIR / f"{post['id']}-evidence.png"
    spec = SourceCaptureSpec(
        source_url=post["source_url"],
        source_name=post["source_name"],
        published_at=post["published_at"],
        evidence_type=post["visual_route"],
        selector="[data-inu-manual-evidence]",
        is_primary_source=bool(post["is_primary_source"]),
    )
    asyncio.run(capture_official_evidence(spec, evidence_path, evidence_anchor=post["evidence_anchor"]))
    if post.get("evidence_as_primary"):
        item = {
            "id": post["id"],
            "topic_type": post["topic_type"],
            "visual_route": post["visual_route"],
            "text": compose_post(
                hook=post["hook"], facts=post["facts"], opinion=post["opinion"],
                tags=post["tags"],
            ),
            "media_path": _relative(evidence_path),
            "source_manifest": _relative(evidence_path.with_suffix(".source.json")),
        }
        validate_test_item(item)
        return item, False

    primary_path = ARTIFACT_DIR / f"{post['id']}-main.png"
    generated = False
    try:
        capture_source_hero_image(
            source_url=post["source_url"], source_name=post["source_name"],
            published_at=post["published_at"], output_path=primary_path,
            is_primary_source=bool(post["is_primary_source"]),
        )
    except Exception as source_image_error:
        if _today_generated_count(state) >= MAX_GENERATED_EDITORIAL_VISUALS_PER_DAY:
            raise ValueError("主画像がなく、生成画像の日次上限に達しています") from source_image_error
        logger.info("出典主画像なし。生成ビジュアルへ切替: %s", source_image_error)
        generate_editorial_news_visual(
            hook=post["hook"], facts=post["facts"], topic_type=post["topic_type"],
            source_url=post["source_url"], source_name=post["source_name"],
            published_at=post["published_at"], output_path=primary_path,
            is_primary_source=bool(post["is_primary_source"]),
        )
        generated = True

    item = {
        "id": post["id"],
        "topic_type": post["topic_type"],
        "visual_route": post["visual_route"],
        "text": compose_post(
            hook=post["hook"], facts=post["facts"], opinion=post["opinion"],
            tags=post["tags"],
        ),
        "media_path": _relative(primary_path),
        "source_manifest": _relative(primary_path.with_suffix(".source.json")),
        "additional_media": [{
            "media_path": _relative(evidence_path),
            "source_manifest": _relative(evidence_path.with_suffix(".source.json")),
        }],
    }
    validate_test_item(item)
    return item, generated


def prepare(args: argparse.Namespace) -> int:
    state = load_state(Path(args.state))
    state.setdefault("reservations", [])
    state.setdefault("posted", [])
    post = _load_post(args.post_id)
    if any(row.get("post_id") == post["id"] for row in state["posted"]):
        logger.info("この個別投稿は投稿済みです: %s", post["id"])
        return 0
    item, generated = _build_item(post, state)
    Path(args.prepared).write_text(json.dumps({"item": item, "post": post, "generated": generated}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    existing = next((row for row in state["reservations"] if row.get("post_id") == post["id"]), None)
    if existing is None:
        state["reservations"].append({
            "post_id": post["id"], "generated_editorial_visual": generated,
            "prepared_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        })
    else:
        logger.info("未公開の予約を引き継いで投稿データを再準備: %s", post["id"])
    save_state(Path(args.state), state)
    logger.info("個別投稿を準備: %s", post["id"])
    return 0


def publish(args: argparse.Namespace) -> int:
    if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        raise RuntimeError("GitHub Actionsの再実行は重複投稿防止のため禁止しています")
    state = load_state(Path(args.state))
    state.setdefault("reservations", [])
    state.setdefault("posted", [])
    prepared = json.loads(Path(args.prepared).read_text(encoding="utf-8"))
    post_id = prepared["item"]["id"]
    reservation = next((row for row in state["reservations"] if row.get("post_id") == post_id), None)
    if reservation is None or any(row.get("post_id") == post_id for row in state["posted"]):
        raise RuntimeError("個別投稿の予約が確認できないため公開しません")
    tweet_id = publish_test_item(prepared["item"])
    state["reservations"] = [row for row in state["reservations"] if row.get("post_id") != post_id]
    state["posted"].append({
        **reservation, "tweet_id": str(tweet_id), "posted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    state["posted"] = state["posted"][-1000:]
    save_state(Path(args.state), state)
    logger.info("INU個別投稿完了: https://x.com/hellobtc_jp/status/%s", tweet_id)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="INU 個別ニュース投稿")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--prepare", action="store_true")
    action.add_argument("--publish", action="store_true")
    parser.add_argument("--post-id", required=True)
    parser.add_argument("--state", default=str(STATE_PATH))
    parser.add_argument("--prepared", default=str(PREPARED_PATH))
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    raise SystemExit(prepare(args) if args.prepare else publish(args))
