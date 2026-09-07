#!/usr/bin/env python3
"""承認済みアイキャッチを一括で既存記事へ反映する。"""

import json
import logging
import os
from pathlib import Path

from image_processing import fit_image_to_jpeg
from replace_featured_image import update_schema_image
from wp_poster import WordPressAPI


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "assets" / "featured" / "2026-08-25-onward" / "manifest.json"


def load_manifest(path: Path) -> list[dict[str, str]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not rows:
        raise ValueError("アイキャッチ差し替えマニフェストが空です")
    for row in rows:
        if not isinstance(row, dict) or not row.get("slug") or not row.get("image_file"):
            raise ValueError(f"不正なマニフェスト項目: {row}")
        if not (REPO_ROOT / row["image_file"]).is_file():
            raise FileNotFoundError(f"アイキャッチ画像が見つかりません: {row['image_file']}")
    return rows


def main() -> None:
    manifest_path = Path(os.getenv("FEATURED_IMAGE_MANIFEST", DEFAULT_MANIFEST))
    rows = load_manifest(manifest_path)
    wp = WordPressAPI(
        os.environ["WP_URL"],
        os.environ["WP_USERNAME"],
        os.environ["WP_APP_PASSWORD"],
    )
    posts = {post["slug"]: post for post in wp.get_posts_by_slugs([row["slug"] for row in rows])}
    missing = [row["slug"] for row in rows if row["slug"] not in posts]
    if missing:
        raise RuntimeError(f"対象記事が見つかりません: {', '.join(missing)}")

    for row in rows:
        slug = row["slug"]
        post = posts[slug]
        source = (REPO_ROOT / row["image_file"]).read_bytes()
        image_data = fit_image_to_jpeg(source, width=1200, height=630, quality=92)
        media_id, image_url = wp.upload_media(image_data, filename=f"featured-approved-{slug}.jpg")
        content = update_schema_image(post["content"]["raw"], image_url)
        wp.update_post(post["id"], featured_media=media_id, content=content)
        logger.info("アイキャッチ更新完了: %s -> %s", slug, image_url)


if __name__ == "__main__":
    main()
