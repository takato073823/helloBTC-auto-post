#!/usr/bin/env python3
"""検証済みのINU投稿を、明示的な手動実行時だけXへ送る。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from pathlib import Path
from typing import Callable

from PIL import Image

from inu_content_types import get_content_policy
from inu_post import validate_post
from x_poster import _neutralize_service_domains, post_info_tweet


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = Path(__file__).resolve().parent / "inu_test_posts.json"
MAX_MEDIA_BYTES = 5 * 1024 * 1024
BLOCKING_PATTERNS = (
    "必ず儲かる",
    "絶対に上がる",
    "絶対に下がる",
    "買うべき",
    "売るべき",
    "利益保証",
    "元本保証",
    "価格目標",
    "今すぐ買",
    "今すぐ売",
)


def load_test_item(post_id: str) -> dict:
    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    items = {item["id"]: item for item in payload.get("posts", [])}
    if post_id not in items:
        raise ValueError(f"許可されていないテスト投稿IDです: {post_id}")
    return items[post_id]


def _media_path(value: str) -> Path:
    media_path = (REPO_ROOT / value).resolve()
    if REPO_ROOT not in media_path.parents or not media_path.is_file():
        raise ValueError("投稿画像がリポジトリ内に存在しません")
    if media_path.stat().st_size > MAX_MEDIA_BYTES:
        raise ValueError("投稿画像が5MBを超えています")
    with Image.open(media_path) as image:
        image.verify()
    with Image.open(media_path) as image:
        if image.format != "PNG":
            raise ValueError("検証済みPNG以外は投稿できません")
        if image.width < 320 or image.height < 180:
            raise ValueError("投稿画像が小さすぎます")
    return media_path


def _manifest(value: str) -> dict:
    manifest_path = (REPO_ROOT / value).resolve()
    if REPO_ROOT not in manifest_path.parents or not manifest_path.is_file():
        raise ValueError("出典メタデータがありません")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _validate_evidence_manifest(manifest: dict, visual_route: str, policy) -> None:
    if manifest.get("evidence_type") != visual_route:
        raise ValueError("画像の出典形式が投稿データと一致しません")
    if visual_route.startswith("official_"):
        if not manifest.get("is_primary_source"):
            raise ValueError("一次資料として確認されていません")
    elif visual_route.startswith("gpt_"):
        if not manifest.get("generated_image") or not manifest.get("facts_verified"):
            raise ValueError("生成画像内の事実確認が完了していません")
        if policy.requires_primary_source and not manifest.get("facts_primary_source"):
            raise ValueError("図解の根拠が一次資料で確認されていません")
    elif visual_route == "market_service_screenshot":
        required_screenshot_fields = {
            "data_verified": True,
            "capture_type": "service_screenshot",
            "screenshot_provider": "TradingView",
            "attribution_visible": True,
            "white_background": True,
        }
        invalid = [
            key
            for key, expected in required_screenshot_fields.items()
            if manifest.get(key) != expected
        ]
        if invalid:
            raise ValueError(f"実サービス画面の検証が完了していません: {invalid}")
    source_url = str(manifest.get("source_url", ""))
    if policy.requires_primary_source and not source_url.startswith("https://"):
        raise ValueError("出典URLが不正です")


def validated_media_paths(item: dict) -> tuple[str, list[Path]]:
    required = {
        "id",
        "topic_type",
        "visual_route",
        "text",
        "media_path",
        "source_manifest",
    }
    missing = sorted(required - set(item))
    if missing:
        raise ValueError(f"投稿データが不足しています: {missing}")

    safe_text = _neutralize_service_domains(item["text"].strip())
    validate_post(safe_text)
    policy = get_content_policy(item["topic_type"])
    if item["visual_route"] != policy.visual_route:
        raise ValueError("投稿系統と画像形式が一致しません")
    if policy.review_mode == "manual":
        raise ValueError("手動確認専用の投稿系統は自動公開できません")
    blocked = [pattern for pattern in BLOCKING_PATTERNS if pattern in safe_text]
    if blocked:
        raise ValueError(f"禁止表現があります: {blocked}")
    if re.search(r"https?://|www\.", safe_text, flags=re.IGNORECASE):
        raise ValueError("投稿本文に外部URLを直書きできません")

    media_path = _media_path(item["media_path"])
    manifest = _manifest(item["source_manifest"])
    visual_route = item["visual_route"]
    extra_media = item.get("additional_media", [])
    if manifest.get("visual_role") == "attention_visual":
        if manifest.get("evidence_type") not in {"source_news_image", "gpt_news_visual"}:
            raise ValueError("主画像の形式が不正です")
        if not manifest.get("facts_verified"):
            raise ValueError("主画像の事実確認が完了していません")
        if manifest.get("evidence_type") == "gpt_news_visual" and not manifest.get("generated_image"):
            raise ValueError("生成主画像の記録が不正です")
        if manifest.get("evidence_type") == "source_news_image":
            if manifest.get("capture_type") != "source_hero_image" or not str(manifest.get("source_image_url", "")).startswith("https://"):
                raise ValueError("出典主画像の記録が不正です")
        if not extra_media:
            raise ValueError("主画像には根拠画像の追加が必要です")
    else:
        _validate_evidence_manifest(manifest, visual_route, policy)

    media_paths = [media_path]
    evidence_found = manifest.get("evidence_type") == visual_route
    for asset in extra_media:
        if not isinstance(asset, dict) or set(asset) != {"media_path", "source_manifest"}:
            raise ValueError("追加画像の指定が不正です")
        media_paths.append(_media_path(asset["media_path"]))
        evidence_manifest = _manifest(asset["source_manifest"])
        _validate_evidence_manifest(evidence_manifest, visual_route, policy)
        evidence_found = True
    if len(media_paths) > 4:
        raise ValueError("投稿画像は4枚までです")
    if not evidence_found:
        raise ValueError("投稿の根拠画像がありません")
    return safe_text, media_paths


def validate_test_item(item: dict) -> tuple[str, Path]:
    safe_text, media_paths = validated_media_paths(item)
    return safe_text, media_paths[0]


def publish_test_item(
    item: dict,
    *,
    poster: Callable[[str, Path], str | None] = post_info_tweet,
) -> str:
    safe_text, media_paths = validated_media_paths(item)
    media = media_paths[0] if len(media_paths) == 1 else media_paths
    tweet_id = poster(safe_text, media)
    if not tweet_id:
        raise RuntimeError("X投稿に失敗しました。文字だけの代替投稿は行っていません")
    return str(tweet_id)


def run(args: argparse.Namespace) -> int:
    item = load_test_item(args.post_id)
    safe_text, media_path = validate_test_item(item)
    logger.info("投稿前検証完了: %s / %s", item["id"], media_path)
    logger.info("投稿本文:\n%s", safe_text)

    if not args.live:
        logger.info("ドライランのためXには投稿していません")
        return 0

    if os.environ.get("GITHUB_RUN_ATTEMPT", "1") != "1":
        raise RuntimeError("GitHub Actionsの再実行は重複投稿防止のため禁止しています")

    tweet_id = publish_test_item(item)
    tweet_url = f"https://x.com/hellobtc_jp/status/{tweet_id}"
    logger.info("INUテスト投稿完了: %s", tweet_url)
    output_file = os.environ.get("GITHUB_OUTPUT")
    if output_file:
        with Path(output_file).open("a", encoding="utf-8") as handle:
            handle.write(f"tweet_url={tweet_url}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="検証済みINU投稿の手動公開")
    parser.add_argument("--post-id", required=True)
    parser.add_argument("--live", action="store_true")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
