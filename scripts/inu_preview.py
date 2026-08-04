#!/usr/bin/env python3
"""INU投稿の本文と証拠画像を手動プレビューする。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from inu_budget import assert_within_budget
from inu_content_types import CONTENT_TYPES, get_content_policy
from inu_gpt_image import generate_image
from inu_post import compose_post, validate_post
from inu_source_capture import SourceCaptureSpec, capture_official_element
from inu_visual import build_gpt_image_prompt
from inu_visual import select_visual_route


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def run(args: argparse.Namespace) -> int:
    assert_within_budget(24)
    text = compose_post(
        hook=args.hook,
        facts=args.fact,
        opinion=args.opinion,
        source_label=args.source,
        tags=args.tag,
    )
    validate_post(text)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    text_path = output_dir / "inu_post.txt"
    prompt_path = output_dir / "inu_image_prompt.txt"
    image_path = output_dir / "inu_post.png"
    text_path.write_text(text + "\n", encoding="utf-8")

    decision = select_visual_route(args.topic_type, needs_timeline=args.needs_timeline)
    policy = get_content_policy(args.topic_type)
    if decision.route in {"live_chart", "manual_quote_with_source_media"}:
        raise ValueError(
            f"{args.topic_type}は専用経路で画像を用意してください: {decision.route}"
        )
    if args.visual_type and args.visual_type != decision.route:
        raise ValueError(
            f"投稿系統と画像形式が一致しません: {decision.route} が必要です"
        )
    visual_type = decision.route

    if visual_type.startswith("official_"):
        missing = [
            name
            for name in ("source_url", "source_name", "published_at", "source_selector")
            if not getattr(args, name)
        ]
        if missing:
            raise ValueError(f"公式スクリーンショットに必要な入力がありません: {', '.join(missing)}")
        spec = SourceCaptureSpec(
            source_url=args.source_url,
            source_name=args.source_name,
            published_at=args.published_at,
            evidence_type=visual_type,
            selector=args.source_selector,
        )
        asyncio.run(capture_official_element(spec, image_path))
    else:
        if policy.requires_primary_source:
            missing = [
                name
                for name in ("source_url", "source_name", "published_at")
                if not getattr(args, name)
            ]
            if missing:
                raise ValueError(
                    f"図解の事実確認に必要な一次資料がありません: {', '.join(missing)}"
                )
        prompt = build_gpt_image_prompt(
            visual_type=visual_type,
            headline=args.image_headline or args.hook,
            key_points=args.fact,
            visual_direction=args.visual_direction,
        )
        prompt_path.write_text(prompt + "\n", encoding="utf-8")
        generate_image(prompt, image_path)
        manifest = {
            "source_url": args.source_url or "",
            "source_name": args.source_name or args.source,
            "published_at": args.published_at or "",
            "evidence_type": visual_type,
            "is_primary_source": False,
            "facts_primary_source": bool(args.source_url),
            "facts_verified": False,
            "generated_image": True,
            "topic_type": args.topic_type,
        }
        image_path.with_suffix(".source.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    logger.info("INU投稿プレビューを生成: %s", output_dir)
    logger.info("\n%s", text)

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="INUの新しいX投稿プレビュー")
    parser.add_argument("--hook", required=True)
    parser.add_argument("--fact", action="append", required=True)
    parser.add_argument("--opinion", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--topic-type", required=True, choices=sorted(CONTENT_TYPES))
    parser.add_argument("--needs-timeline", action="store_true")
    parser.add_argument(
        "--visual-type",
        choices=(
            "official_text_crop",
            "official_data_crop",
            "gpt_timeline",
            "gpt_creative",
            "gpt_explainer",
        ),
        default=None,
    )
    parser.add_argument("--image-headline")
    parser.add_argument("--visual-direction", default="")
    parser.add_argument("--source-url")
    parser.add_argument("--source-name")
    parser.add_argument("--published-at")
    parser.add_argument("--source-selector")
    parser.add_argument("--output-dir", default="artifacts/inu-preview")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
