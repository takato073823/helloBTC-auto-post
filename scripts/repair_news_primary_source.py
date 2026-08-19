"""既存ニュースの報道元リンクを、検証可能な一次資料リンクへ修復する。"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
from html import escape
from pathlib import Path

from wp_poster import WordPressAPI


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def build_primary_source_block(
    primary_name: str,
    primary_url: str,
    reference_name: str = "",
    reference_url: str = "",
) -> str:
    block = (
        '<!-- wp:paragraph {"className":"hellobtc-source"} -->\n'
        '<p class="hellobtc-source">一次資料：'
        f'<a href="{escape(primary_url, quote=True)}" target="_blank" '
        f'rel="noopener noreferrer">{escape(primary_name)}</a></p>\n'
        '<!-- /wp:paragraph -->'
    )
    if reference_name and reference_url:
        block += (
            '\n<!-- wp:paragraph {"className":"hellobtc-reference-source"} -->\n'
            '<p class="hellobtc-reference-source">参考報道：'
            f'<a href="{escape(reference_url, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">{escape(reference_name)}</a></p>\n'
            '<!-- /wp:paragraph -->'
        )
    return block


def repair_content(
    content: str,
    primary_name: str,
    primary_url: str,
    reference_name: str = "",
    reference_url: str = "",
) -> str:
    # 読者に不要な固定ラベルだけを外し、直接回答そのものは残す。
    repaired, label_count = re.subn(
        r"<strong>\s*結論[：:]\s*</strong>\s*", "", content, count=1,
        flags=re.IGNORECASE,
    )
    if label_count != 1:
        raise RuntimeError(f"結論ラベルの置換対象が1件ではありません: {label_count}")

    new_source = build_primary_source_block(
        primary_name, primary_url, reference_name, reference_url
    )
    source_pattern = re.compile(
        r'(?:<!--\s*wp:paragraph\s+\{"className":"hellobtc-source"\}\s*-->\s*)?'
        r'<p\b[^>]*class=["\'][^"\']*\bhellobtc-source\b[^"\']*["\'][^>]*>.*?</p>'
        r'(?:\s*<!--\s*/wp:paragraph\s*-->)?',
        re.DOTALL | re.IGNORECASE,
    )
    repaired, source_count = source_pattern.subn(new_source, repaired, count=1)
    if source_count != 1:
        raise RuntimeError(f"既存出典ブロックの置換対象が1件ではありません: {source_count}")
    return repaired


def repair_post(
    wp: WordPressAPI,
    slug: str,
    primary_name: str,
    primary_url: str,
    backup_path: Path,
    reference_name: str = "",
    reference_url: str = "",
) -> dict:
    posts = wp.get_posts_by_slugs([slug])
    if len(posts) != 1:
        raise RuntimeError(f"対象記事が1件に定まりません: {slug} ({len(posts)}件)")
    post = posts[0]
    raw = post.get("content", {}).get("raw", "")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path.write_text(json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")

    updated_content = repair_content(
        raw, primary_name, primary_url, reference_name, reference_url
    )
    result = wp.update_post_content(post["id"], updated_content)
    logger.info("一次資料へ修復完了: %s", result.get("link", slug))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slug", required=True)
    parser.add_argument("--primary-name", required=True)
    parser.add_argument("--primary-url", required=True)
    parser.add_argument("--reference-name", default="")
    parser.add_argument("--reference-url", default="")
    parser.add_argument("--backup", type=Path, default=Path("news-primary-source-backup.json"))
    args = parser.parse_args()
    wp = WordPressAPI(
        os.environ["WP_URL"], os.environ["WP_USERNAME"], os.environ["WP_APP_PASSWORD"]
    )
    repair_post(
        wp, args.slug, args.primary_name, args.primary_url, args.backup,
        args.reference_name, args.reference_url,
    )


if __name__ == "__main__":
    main()
