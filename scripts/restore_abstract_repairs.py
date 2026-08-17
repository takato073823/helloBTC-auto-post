#!/usr/bin/env python3
"""抽象化された一括修復画像だけを、変更前の写真へ戻す。"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from audit_featured_images import fetch_posts, featured_image_url
from repair_garbled_featured_images import REPAIRED_FEATURED_RE
from replace_featured_image import update_schema_image
from wp_poster import WordPressAPI


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def load_restore_map(path: Path) -> dict[str, str]:
    mapping = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        slug, image_url = line.split("\t", 1)
        mapping[slug] = image_url
    return mapping


def media_slug_from_url(image_url: str) -> str:
    return Path(urlparse(image_url).path).stem


def main() -> None:
    import os

    base_url = os.getenv("WP_URL", "https://hellobtc.jp")
    since = os.getenv("RESTORE_SINCE", "2026-08-01")
    restore_map = load_restore_map(Path(__file__).with_name("featured_image_pre_repair_map.tsv"))
    wp = WordPressAPI(
        base_url,
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )

    restored = 0
    failures = []
    for public_post in fetch_posts(base_url, since):
        slug = public_post.get("slug", "")
        current_url = featured_image_url(public_post)
        if not REPAIRED_FEATURED_RE.search(current_url.split("?", 1)[0]):
            continue
        original_url = restore_map.get(slug)
        if not original_url:
            failures.append((slug, "変更前画像の記録なし"))
            continue
        try:
            media_slug = media_slug_from_url(original_url)
            media_items = wp._request(
                "GET",
                "media",
                params={"slug": media_slug, "per_page": 10, "context": "edit"},
            )
            matches = [item for item in media_items if item.get("source_url") == original_url]
            if len(matches) != 1:
                raise RuntimeError(f"変更前メディアを一意に取得できません: {original_url}")
            editable_posts = wp.get_posts_by_slugs([slug])
            if len(editable_posts) != 1:
                raise RuntimeError(f"対象記事を一意に取得できません: {slug}")
            post = editable_posts[0]
            content = update_schema_image(post.get("content", {}).get("raw", ""), original_url)
            wp.update_post(post["id"], featured_media=matches[0]["id"], content=content)
            restored += 1
            logger.info("RESTORE_RESULT\tPASS\t%s\t%s", slug, original_url)
        except Exception as exc:
            failures.append((slug, str(exc)))
            logger.exception("RESTORE_RESULT\tFAIL\t%s\t%s", slug, exc)

    logger.info("RESTORE_SUMMARY\trestored=%d\tfailed=%d", restored, len(failures))
    if failures:
        raise RuntimeError("復元できなかった記事: " + ", ".join(slug for slug, _ in failures))


if __name__ == "__main__":
    main()
