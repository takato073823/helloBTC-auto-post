#!/usr/bin/env python3
"""文字化けしたアイキャッチを写真報道風のまま差し替える。"""

from __future__ import annotations

import html
import logging
import os
import re
import time

from audit_featured_images import fetch_posts, featured_image_url, is_legacy_generated_image
from generator import generate_featured_image
from llm_client import generate_text
from replace_featured_image import update_schema_image
from wp_poster import WordPressAPI


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PHOTO_REPAIR_SUFFIX = (
    "photorealistic Reuters-style editorial news photography with realistic materials, natural depth of field, "
    "and an article-specific real-world scene; prefer subjects without typographic surfaces and turn any unavoidable "
    "document, display, label, or control panel away from the camera or fully outside the focal plane; exclude headline "
    "overlays, captions, watermarks, publisher marks, unrelated logos, pseudo-text, malformed characters, invented "
    "paragraph copy, charts, and decorative numbers"
)
REPAIRED_FEATURED_RE = re.compile(
    r"/featured-repaired-.+-\d+\.(?:jpe?g|png)$", re.IGNORECASE
)


def normalize_repair_prompt(raw_prompt: str) -> str:
    """LLM出力を1行へ整え、写真報道風と文字品質条件を必ず付ける。"""
    clean = re.sub(r"\s+", " ", (raw_prompt or "")).strip()
    clean = re.sub(r"^(?:prompt|image prompt)\s*:\s*", "", clean, flags=re.IGNORECASE)
    clean = clean.strip('"` ').rstrip(". ,;:").strip('"` ')
    if not clean:
        clean = "A realistic editorial still life showing the article's central event through relevant objects"
    return f"{clean}, {PHOTO_REPAIR_SUFFIX}"


def build_repair_prompt(title: str) -> str:
    """記事タイトルから、元の写真報道スタイルを維持した英語構図を作る。"""
    raw_prompt = generate_text(
        f"""
Create one concise English image-generation prompt for a Japanese crypto-news featured image.
Article title: {title}

Return only the prompt, with no explanation. Depict the article's central event as a realistic photojournalistic
news photograph using concrete, article-relevant locations and physical objects. Preserve believable scale,
materials, lighting, perspective, and depth of field. Do not turn the scene into abstract geometry, a symbolic
illustration, a 3D infographic, or a minimalist shape composition. Do not request people or faces. Avoid dense
writing; when a relevant document, screen, or sign is essential, request at most one to three exact short Japanese
or English terms and keep all other copy out of readable focus. Never request publisher branding. Use at most 45 words.
""".strip(),
        max_output_tokens=120,
    )
    return normalize_repair_prompt(raw_prompt)


def select_shard(items: list, shard_index: int, shard_count: int) -> list:
    if shard_count < 1 or not 0 <= shard_index < shard_count:
        raise ValueError("シャード指定が不正です")
    return [item for index, item in enumerate(items) if index % shard_count == shard_index]


def load_slug_allowlist(path: str | None) -> set[str] | None:
    """TSVの先頭列を、今回だけ修復する既存記事の許可リストとして読む。"""
    if not path:
        return None
    allowlist_path = os.path.join(os.path.dirname(__file__), path)
    with open(allowlist_path, encoding="utf-8") as handle:
        return {
            line.split("\t", 1)[0].strip()
            for line in handle
            if line.strip() and not line.lstrip().startswith("#")
        }


def is_repair_candidate(image_url: str, *, include_repaired: bool) -> bool:
    """通常は旧画像、やり直し時だけ本日の抽象化された修復画像を選ぶ。"""
    clean_url = (image_url or "").split("?", 1)[0]
    return is_legacy_generated_image(clean_url) or (
        include_repaired and bool(REPAIRED_FEATURED_RE.search(clean_url))
    )


def main() -> None:
    base_url = os.getenv("WP_URL", "https://hellobtc.jp")
    since = os.getenv("REPAIR_SINCE", "2026-08-01")
    passed_slugs = {
        slug.strip()
        for slug in os.getenv("AUDIT_PASS_SLUGS", "").split(",")
        if slug.strip()
    }
    include_repaired = os.getenv("REPAIR_INCLUDE_REPAIRED", "false").lower() in {
        "1",
        "true",
        "yes",
    }
    only_slugs = load_slug_allowlist(os.getenv("REPAIR_ONLY_SLUGS_FILE", "").strip() or None)
    max_items = max(0, int(os.getenv("REPAIR_MAX_ITEMS", "0")))
    shard_index = int(os.getenv("REPAIR_SHARD_INDEX", "0"))
    shard_count = int(os.getenv("REPAIR_SHARD_COUNT", "1"))

    targets = []
    for post in fetch_posts(base_url, since):
        image_url = featured_image_url(post)
        slug = post.get("slug", "")
        if (
            is_repair_candidate(image_url, include_repaired=include_repaired)
            and slug not in passed_slugs
            and (only_slugs is None or slug in only_slugs)
        ):
            targets.append(post)
    targets = select_shard(targets, shard_index, shard_count)
    if max_items:
        targets = targets[:max_items]

    wp = WordPressAPI(
        base_url,
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )
    repaired = 0
    failures = []
    for index, public_post in enumerate(targets, start=1):
        slug = public_post.get("slug", "")
        title = html.unescape(public_post.get("title", {}).get("rendered", ""))
        try:
            prompt = build_repair_prompt(title)
            logger.info("修復 %d/%d: %s | prompt=%s", index, len(targets), slug, prompt)
            editable_posts = wp.get_posts_by_slugs([slug])
            if len(editable_posts) != 1:
                raise RuntimeError(f"対象記事が一意に取得できません: {slug}")
            post = editable_posts[0]
            image_data = generate_featured_image(
                image_prompt=prompt,
                tags=[],
                logo_brand=None,
                logo_domain=None,
                article_title=title,
                article_content=post.get("content", {}).get("raw", ""),
            )
            media_id, image_url = wp.upload_media(
                image_data,
                filename=f"featured-replaced-{slug}-{int(time.time())}.jpg",
            )
            content = update_schema_image(post.get("content", {}).get("raw", ""), image_url)
            wp.update_post(post["id"], featured_media=media_id, content=content)
            repaired += 1
            logger.info("REPAIR_RESULT\tPASS\t%s\t%s", slug, image_url)
        except Exception as exc:
            failures.append((slug, str(exc)))
            logger.exception("REPAIR_RESULT\tFAIL\t%s\t%s", slug, exc)

    logger.info(
        "REPAIR_SUMMARY\tshard=%d/%d\ttargets=%d\trepaired=%d\tfailed=%d",
        shard_index + 1,
        shard_count,
        len(targets),
        repaired,
        len(failures),
    )
    if failures:
        failed_slugs = ", ".join(slug for slug, _ in failures)
        raise RuntimeError(f"修復できなかった記事: {failed_slugs}")


if __name__ == "__main__":
    main()
