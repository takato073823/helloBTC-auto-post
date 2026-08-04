#!/usr/bin/env python3
"""INU投稿の本文と証拠画像を手動プレビューする。"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from inu_budget import assert_within_budget
from inu_gpt_image import generate_image
from inu_post import compose_post, validate_post
from inu_source_capture import SourceCaptureSpec, capture_official_element
from inu_visual import build_gpt_image_prompt


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

    if args.visual_type.startswith("official_"):
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
            evidence_type=args.visual_type,
            selector=args.source_selector,
        )
        asyncio.run(capture_official_element(spec, image_path))
    else:
        prompt = build_gpt_image_prompt(
            visual_type=args.visual_type,
            headline=args.image_headline or args.hook,
            key_points=args.fact,
            visual_direction=args.visual_direction,
        )
        prompt_path.write_text(prompt + "\n", encoding="utf-8")
        generate_image(prompt, image_path)
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
    parser.add_argument(
        "--visual-type",
        choices=(
            "official_text_crop",
            "official_data_crop",
            "gpt_timeline",
            "gpt_creative",
            "gpt_explainer",
        ),
        default="gpt_explainer",
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
